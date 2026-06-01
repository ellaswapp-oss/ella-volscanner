"""
scanners/sp500_vol_scanner.py
------------------------------
S&P 500 Live Volatility Scanner.

Scans the S&P 500 universe and returns tickers where:
  - IV30 is available from real options data (not synthetic)
  - IV30 / RV30 > min_ratio   (default 1.30)
  - IV surface status is healthy or suspicious (not invalid)
  - Macro regime is not dangerous (total penalty < 20)
  - Trade signal is SELL_AGGRESSIVE or SELL_SELECTIVE

Architecture
------------
Universe file : app/data/universes/sp500.json  (ordered by market cap)
Options chain : RealDataProvider (Polygon live; disk-cached per day)
Price history : RealDataProvider (Polygon; disk-cached)
IV surface    : _calculate_iv_with_liquidity() — liquidity-filtered ATM selection
Results cache : in-memory dict keyed by scan_date (reset on force_refresh)

Batch controls (passed via params dict)
---------------------------------------
  max_tickers    default 50   — how many tickers to scan (first N by market cap)
  batch_size     default 10   — tickers processed between sleep intervals
  delay_seconds  default 0.2  — seconds to sleep between batches
  max_results    default 50   — cap on results returned (post-filter)

Liquidity quality thresholds
-----------------------------
  min_open_interest default 100
  min_volume        default 10
  max_spread_pct    default 0.25  (ask-bid)/mid ≤ 25 %
  min_valid_tenors  default 2     — tenors that pass all quality checks

Exclusion criteria
------------------
A ticker is excluded (not errored) when:
  - IV30 unavailable from chain
  - No valid ATM quote (spread_quality = VERY_WIDE or no call+put pair)
  - valid_tenors < min_valid_tenors
  - IV/RV ratio < min_ratio
  - Trade signal is not SELL_SELECTIVE or SELL_AGGRESSIVE

Signal not reported back if macro_penalty ≥ _DANGEROUS_MACRO (20 pts)
but tickers are still scanned; the dangerous flag is surfaced in the result.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import pathlib
import threading
import time
from typing import Any

import numpy as np
import pandas as pd

from app.calculations.engine import (
    iv_rank as _iv_rank_fn,
    iv_rv_ratio as _calc_ratio,
    realized_volatility,
    regime_score,
    trade_recommendation,
    volatility_risk_premium,
)
from app.data.earnings_calendar import get_earnings_info
from app.data.iv_surface_health import validate_iv_surface
from app.data.options_utils import TENOR_TOLERANCE, _TENORS, _flt
from app.services.live_vol_service import _compute_macro_penalties, _rolling_rv_series

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BACKEND_ROOT  = pathlib.Path(__file__).resolve().parents[2]
_UNIVERSE_FILE = _BACKEND_ROOT / "app" / "data" / "universes" / "sp500.json"

# ---------------------------------------------------------------------------
# Default thresholds
# ---------------------------------------------------------------------------

_MIN_OI            = 100     # contracts — minimum open interest
_MIN_VOL           = 10      # contracts — minimum daily volume
_MAX_SPREAD_PCT    = 0.25    # fraction — (ask - bid) / mid ≤ 25 %
_MIN_VALID_TENORS  = 2       # tenors that pass all quality checks
_DANGEROUS_MACRO   = 20.0    # macro penalty points — above this = dangerous
_PRICE_HISTORY_DAYS = 75     # ~35 trading days (+buffer) — enough for RV30

# ---------------------------------------------------------------------------
# In-memory scan-result cache   {date_str → full scan result dict}
# ---------------------------------------------------------------------------

_SCAN_CACHE: dict[str, dict] = {}
_CACHE_LOCK = threading.Lock()


# ===========================================================================
# Universe loader
# ===========================================================================

def load_universe(path: pathlib.Path = _UNIVERSE_FILE) -> list[str]:
    """Load and return the S&P 500 ticker list from the universe JSON file."""
    data = json.loads(path.read_text())
    return [str(t).upper() for t in data.get("tickers", [])]


# ===========================================================================
# Liquidity pre-filter
# ===========================================================================

def _liquidity_prefilter(
    chain: pd.DataFrame,
    min_oi: int,
    min_vol: int,
    max_spread_pct: float,
) -> pd.DataFrame:
    """
    Remove contracts that fail any of the hard liquidity criteria:

      - bid or ask is None / ≤ 0
      - implied_volatility is None
      - volume < min_vol   (if the column exists)
      - open_interest < min_oi  (if the column exists)
      - (ask - bid) / mid > max_spread_pct
    """
    df = chain.copy()

    # Drop contracts with no quote or no IV
    df = df.dropna(subset=["bid", "ask", "implied_volatility"])
    df = df[(df["bid"] > 0) & (df["ask"] > 0)]

    if df.empty:
        return df

    # Spread pct filter
    mid        = (df["bid"] + df["ask"]) / 2.0
    mid        = mid.where(mid > 0)
    spread_pct = (df["ask"] - df["bid"]) / mid
    df         = df[spread_pct <= max_spread_pct]

    if df.empty:
        return df

    # Volume / OI
    if "volume" in df.columns:
        df = df[df["volume"].fillna(0) >= min_vol]
    if "open_interest" in df.columns:
        df = df[df["open_interest"].fillna(0) >= min_oi]

    return df.reset_index(drop=True)


# ===========================================================================
# Spread quality helper
# ===========================================================================

def _spread_quality_from_pct(pct: float | None) -> str:
    if pct is None:
        return "NO_QUOTE"
    if pct < 0.05:
        return "TIGHT"
    if pct < 0.15:
        return "FAIR"
    if pct < 0.30:
        return "WIDE"
    return "VERY_WIDE"


# ===========================================================================
# Liquidity-aware ATM IV calculator
# ===========================================================================

def _calculate_iv_with_liquidity(
    chain: pd.DataFrame,
    underlying_price: float,
    pricing_date: datetime.date,
    min_oi: int   = _MIN_OI,
    min_vol: int  = _MIN_VOL,
    max_spread_pct: float = _MAX_SPREAD_PCT,
) -> tuple[dict[str, float | None], dict[str, dict], dict[str, Any]]:
    """
    Compute ATM IV surface with liquidity-filtered contract selection.

    Enhancements over the base ``calculate_atm_iv_by_tenor``:
      1. Pre-filter chain by bid/ask, IV, volume, OI, spread pct.
      2. Per-tenor ATM selection prefers: nearest strike → best OI → tightest spread.
      3. Requires ≥ 1 valid call AND ≥ 1 valid put per tenor.
      4. Tenors with spread_quality = VERY_WIDE after averaging are skipped.
      5. Returns per-tenor liquidity metrics and an aggregated dict.

    Returns
    -------
    iv_surface : {iv7, iv14, iv30, iv60, iv90} in percent (or None)
    tenor_meta : per-tenor DTE / expiration metadata
    liq_metrics: {atm_open_interest, atm_volume, spread_pct, spread_quality,
                  contract_count, valid_tenors}
    """
    _empty_iv  = {k: None for k, _ in _TENORS}
    _empty_meta = {
        k: {
            "target_dte":       d,
            "actual_dte":       None,
            "dte_diff":         None,
            "expiration":       None,
            "within_tolerance": False,
        }
        for k, d in _TENORS
    }
    _empty_liq = {
        "atm_open_interest": None,
        "atm_volume":        None,
        "spread_pct":        None,
        "spread_quality":    "NO_QUOTE",
        "contract_count":    0,
        "valid_tenors":      0,
        "iv_source_quality": "invalid",
    }

    if chain.empty or underlying_price <= 0:
        return _empty_iv, _empty_meta, _empty_liq

    # ── Apply liquidity pre-filter ─────────────────────────────────────────
    filtered = _liquidity_prefilter(chain, min_oi, min_vol, max_spread_pct)
    total_contracts = len(filtered)
    iv_source_quality: str

    if not filtered.empty:
        # Real bid/ask quotes survived the liquidity filter — market-quoted IV.
        iv_source_quality = "quoted_market_iv"
    else:
        # First fallback: relax to just bid/ask + IV validity
        df = chain.copy().dropna(subset=["bid", "ask", "implied_volatility"])
        df = df[(df["bid"] > 0) & (df["ask"] > 0)]
        if not df.empty:
            filtered = df
            total_contracts = 0   # signal: no contracts passed full liquidity check
            iv_source_quality = "quoted_market_iv"
        else:
            # Second fallback: Polygon returned no quotes at all (bid/ask = null
            # for every contract).  Use model IV directly — no market quote.
            df2 = chain.copy().dropna(subset=["implied_volatility"])
            df2 = df2[df2["implied_volatility"] > 0]
            filtered = df2
            total_contracts = 0
            iv_source_quality = "polygon_model_iv_no_quote"

    # ── Parse expiration dates ─────────────────────────────────────────────
    try:
        filtered = filtered.copy()
        filtered["_exp_date"] = pd.to_datetime(filtered["expiration"]).dt.date
    except Exception:
        return _empty_iv, _empty_meta, _empty_liq

    expirations: list[datetime.date] = sorted(
        {d for d in filtered["_exp_date"].dropna() if d > pricing_date}
    )
    if not expirations:
        return _empty_iv, _empty_meta, _empty_liq

    iv_surface:  dict[str, float | None] = {k: None for k, _ in _TENORS}
    tenor_meta:  dict[str, dict]         = {}
    tenor_liq:   dict[str, dict]         = {}
    valid_count  = 0

    for tenor_key, target_dte in _TENORS:
        target_date = pricing_date + datetime.timedelta(days=target_dte)
        closest_exp = min(expirations, key=lambda d: abs((d - target_date).days))
        actual_dte  = (closest_exp - pricing_date).days
        dte_diff    = abs(actual_dte - target_dte)
        tolerance   = TENOR_TOLERANCE[tenor_key]
        within      = dte_diff <= tolerance

        tenor_meta[tenor_key] = {
            "target_dte":       target_dte,
            "actual_dte":       actual_dte,
            "dte_diff":         dte_diff,
            "expiration":       closest_exp.isoformat(),
            "within_tolerance": within,
        }

        if not within:
            continue

        exp_slice = filtered[filtered["_exp_date"] == closest_exp]
        if exp_slice.empty:
            continue

        iv_vals:        list[float] = []
        oi_vals:        list[float] = []
        vol_vals:       list[float] = []
        spread_pct_vals: list[float] = []
        has_call = False
        has_put  = False

        for opt_type in ("call", "put"):
            leg = exp_slice[exp_slice["option_type"] == opt_type].dropna(
                subset=["implied_volatility", "strike"]
            )
            if leg.empty:
                continue

            # ATM candidate window: nearest strike + up to 5 % of spot
            leg       = leg.copy()
            leg["_dist"] = (leg["strike"] - underlying_price).abs()
            nearest   = leg["_dist"].min()
            atm_band  = max(nearest + 0.01, underlying_price * 0.05)
            atm_cands = leg[leg["_dist"] <= atm_band].copy()
            if atm_cands.empty:
                atm_cands = leg.nsmallest(1, "_dist").copy()

            # Spread pct for ranking
            mid_c = (atm_cands["bid"] + atm_cands["ask"]) / 2.0
            atm_cands["_spct"] = (
                (atm_cands["ask"] - atm_cands["bid"])
                / mid_c.where(mid_c > 0, np.nan)
            )

            # Sort: closest ATM > tightest spread > highest OI
            sort_cols = ["_dist", "_spct"]
            sort_asc  = [True,    True   ]
            if "open_interest" in atm_cands.columns:
                atm_cands["_oi"] = atm_cands["open_interest"].fillna(0)
                sort_cols.append("_oi"); sort_asc.append(False)
            atm_cands = atm_cands.sort_values(sort_cols, ascending=sort_asc)

            best = atm_cands.iloc[0]
            iv   = _flt(best["implied_volatility"])
            if iv is None:
                continue

            # Normalise: Polygon stores IV as fraction (0.25 = 25 %)
            iv_pct = iv * 100.0 if iv <= 5.0 else iv

            iv_vals.append(iv_pct)

            oi_raw = _flt(best.get("open_interest"))
            vl_raw = _flt(best.get("volume"))
            sp_raw = float(best["_spct"]) if not math.isnan(float(best.get("_spct", float("nan")))) else None

            if oi_raw is not None: oi_vals.append(oi_raw)
            if vl_raw is not None: vol_vals.append(vl_raw)
            if sp_raw is not None: spread_pct_vals.append(sp_raw)

            if opt_type == "call": has_call = True
            else:                  has_put  = True

        # Require ≥ 1 call AND ≥ 1 put
        if not (has_call and has_put):
            continue

        if not iv_vals:
            continue

        avg_iv       = sum(iv_vals) / len(iv_vals)
        avg_sp       = sum(spread_pct_vals) / len(spread_pct_vals) if spread_pct_vals else None
        sq           = _spread_quality_from_pct(avg_sp)

        # Reject VERY_WIDE tenors
        if sq == "VERY_WIDE":
            continue

        iv_surface[tenor_key] = round(avg_iv, 2)
        valid_count += 1

        tenor_liq[tenor_key] = {
            "atm_open_interest": int(max(oi_vals)) if oi_vals else None,
            "atm_volume":        int(max(vol_vals)) if vol_vals else None,
            "spread_pct":        round(avg_sp, 4)  if avg_sp is not None else None,
            "spread_quality":    sq,
            "contract_count":    len(exp_slice),
        }

    # ── Aggregate liquidity metrics (use iv30 as primary, iv7 as fallback) ─
    primary = tenor_liq.get("iv30") or tenor_liq.get("iv7") or {}
    # If no tenors computed IV at all, downgrade source quality to invalid
    if valid_count == 0:
        iv_source_quality = "invalid"
    liq_metrics: dict[str, Any] = {
        "atm_open_interest": primary.get("atm_open_interest"),
        "atm_volume":        primary.get("atm_volume"),
        "spread_pct":        primary.get("spread_pct"),
        "spread_quality":    primary.get("spread_quality", "NO_QUOTE"),
        "contract_count":    total_contracts,
        "valid_tenors":      valid_count,
        "iv_source_quality": iv_source_quality,
    }

    return iv_surface, tenor_meta, liq_metrics


# ===========================================================================
# Scoring helpers
# ===========================================================================

def _liquidity_score(sq: str, oi: int | None) -> float:
    """
    0 – 100 composite liquidity score.
    70 pts from spread quality, 30 pts from open-interest bucket.
    """
    sq_pts = {"TIGHT": 70, "FAIR": 55, "WIDE": 35, "VERY_WIDE": 0, "NO_QUOTE": 0}
    base   = sq_pts.get(sq, 0)

    if not oi or oi <= 0:
        oi_pts = 0
    elif oi >= 10_000:
        oi_pts = 30
    elif oi >= 1_000:
        oi_pts = 20
    elif oi >= 500:
        oi_pts = 12
    else:
        oi_pts = 5

    return float(base + oi_pts)


def _scanner_score(
    iv_rv:       float,
    reg_score:   float,
    valid_tenors: int,
    liq_score:   float,
) -> float:
    """
    Composite ranking score 0 – 100.

    Weights:
      45 % IV/RV component   — normalized: 1.0 → 0, 2.0 → 100
      35 % regime score      — already 0 – 100
      10 % tenor coverage    — 0 tenors → 0, 5 tenors → 100
      10 % liquidity         — already 0 – 100
    """
    ivrv_norm  = float(np.clip((iv_rv - 1.0) / 1.0 * 100, 0, 100))
    tenor_norm = float(np.clip(valid_tenors / 5.0 * 100, 0, 100))
    score = (
        0.45 * ivrv_norm
        + 0.35 * reg_score
        + 0.10 * tenor_norm
        + 0.10 * liq_score
    )
    return round(float(np.clip(score, 0, 100)), 2)


# ===========================================================================
# Single-ticker scan
# ===========================================================================

def _scan_ticker(
    ticker:     str,
    today:      datetime.date,
    provider,
    macro_info: dict,
    params:     dict,
) -> dict:
    """
    Scan one ticker.

    Returns a dict with ``ok=True`` on success or ``ok=False`` on
    exclusion / skip / error, with a ``reason`` or ``error`` field.
    """
    min_ratio      = float(params.get("min_ratio",                1.30))
    require_iv30   = bool(params.get("require_iv30",               True))
    include_sus    = bool(params.get("include_suspicious",          True))
    exclude_inv    = bool(params.get("exclude_invalid",             True))
    min_oi         = int(params.get("min_open_interest",           _MIN_OI))
    min_vol        = int(params.get("min_volume",                  _MIN_VOL))
    max_spread     = float(params.get("max_spread_pct",            _MAX_SPREAD_PCT))
    min_tenors     = int(params.get("min_valid_tenors",            _MIN_VALID_TENORS))
    excl_earn_days = int(params.get("exclude_earnings_within_days", 0))

    try:
        # ── 1. Price history → RV30 ───────────────────────────────────────
        prices = provider.get_prices_days(ticker, _PRICE_HISTORY_DAYS)
        if len(prices) < 32:
            return {
                "ok": False, "ticker": ticker, "skipped": True,
                "reason": f"insufficient_price_history ({len(prices)} bars)",
            }

        current_price = float(prices.iloc[-1])
        rv30 = realized_volatility(prices, 30)
        if not math.isfinite(rv30) or rv30 <= 0:
            return {"ok": False, "ticker": ticker, "skipped": True,
                    "reason": "rv30_not_finite"}

        # ── 2. Options chain → IV surface (liquidity-filtered) ────────────
        chain = provider.get_options_chain(ticker, today)
        iv_surface, tenor_meta, liq_metrics = _calculate_iv_with_liquidity(
            chain, current_price, today, min_oi, min_vol, max_spread
        )

        # ── 3. Surface health validation ──────────────────────────────────
        status, warnings = validate_iv_surface(iv_surface, tenor_meta)

        available_tenors = [k for k, v in iv_surface.items() if v is not None]
        missing_tenors   = [k for k, v in iv_surface.items() if v is None]
        iv30             = iv_surface.get("iv30")

        # ── 4. Earnings / event awareness ─────────────────────────────────
        earn_info = get_earnings_info(ticker, today)

        # ── 5. Apply exclusion filters ────────────────────────────────────

        if require_iv30 and iv30 is None:
            return {"ok": False, "ticker": ticker, "excluded": True,
                    "reason": "iv30_unavailable"}

        if exclude_inv and status == "invalid":
            return {"ok": False, "ticker": ticker, "excluded": True,
                    "reason": "surface_invalid"}

        if not include_sus and status == "suspicious":
            return {"ok": False, "ticker": ticker, "excluded": True,
                    "reason": "surface_suspicious"}

        if liq_metrics["spread_quality"] == "VERY_WIDE":
            return {"ok": False, "ticker": ticker, "excluded": True,
                    "reason": "spread_very_wide"}

        if liq_metrics["valid_tenors"] < min_tenors:
            return {
                "ok": False, "ticker": ticker, "excluded": True,
                "reason": (
                    f"valid_tenors={liq_metrics['valid_tenors']}"
                    f"<{min_tenors}"
                ),
            }

        # Earnings proximity exclusion (opt-in, default 0 = off)
        if excl_earn_days > 0:
            dte = earn_info.get("days_to_earnings")
            if dte is not None and dte <= excl_earn_days:
                return {
                    "ok": False, "ticker": ticker, "excluded": True,
                    "reason": (
                        f"earnings_in_{dte}d"
                        f"<=exclude_threshold_{excl_earn_days}d"
                    ),
                }

        # ── 5. IV / RV metrics ────────────────────────────────────────────
        iv30_calc = float(iv30) if iv30 is not None else rv30 * 1.05
        ratio     = _calc_ratio(iv30_calc, rv30)

        if not math.isfinite(ratio) or ratio < min_ratio:
            return {
                "ok": False, "ticker": ticker, "excluded": True,
                "reason": f"iv_rv_ratio={ratio:.3f}<{min_ratio}",
            }

        vrp_abs = round(iv30_calc - rv30, 2)
        vrp_pct = round(volatility_risk_premium(iv30_calc, rv30), 4)

        # ── 6. Regime score (with macro penalty) ──────────────────────────
        rv30_series = _rolling_rv_series(prices, 30)
        rv30_finite = rv30_series.dropna()

        # IV Rank proxy vs rolling RV30 history
        if len(rv30_finite) >= 2:
            hist = (
                rv30_finite.iloc[-252:].values
                if len(rv30_finite) > 252
                else rv30_finite.values
            )
            ivr = float(_iv_rank_fn(iv30_calc, hist))
        else:
            ivr = 50.0
        if not math.isfinite(ivr):
            ivr = 50.0

        # Z-score of IV30 vs RV30 distribution
        if len(rv30_finite) >= 2:
            anchor = (
                rv30_finite.iloc[-252:].values
                if len(rv30_finite) > 252
                else rv30_finite.values
            )
            mu_rv  = float(anchor.mean())
            std_rv = float(anchor.std(ddof=1)) if len(anchor) > 1 else 1.0
            std_rv = std_rv if std_rv > 0 else 1.0
            z_score = float(np.clip((iv30_calc - mu_rv) / std_rv, -3.0, 3.0))
        else:
            z_score = 0.0

        # total_penalty is None when macro data is unavailable — treat as 0
        # (optimistic, but this is flagged at the scan level via macro_status)
        macro_penalty  = float(macro_info.get("total_penalty") or 0.0)
        macro_reasons  = list(macro_info.get("reasons", []))
        earn_penalty   = float(earn_info.get("score_penalty", 0.0))

        raw_score = regime_score({
            "iv_rank":     ivr,
            "vrp":         vrp_pct,
            "iv_rv_ratio": ratio,
            "z_score":     z_score,
        })
        adj_score = round(
            float(np.clip(raw_score - macro_penalty - earn_penalty, 0.0, 100.0)),
            2,
        )
        rec        = trade_recommendation(adj_score)
        signal_str = rec["signal"]

        # ── 7. Signal filter ──────────────────────────────────────────────
        valid_signals = {"SELL_AGGRESSIVE", "SELL_SELECTIVE"}
        if signal_str not in valid_signals:
            return {
                "ok": False, "ticker": ticker, "excluded": True,
                "reason": f"signal={signal_str}",
            }

        # ── 8. Liquidity score + scanner score ────────────────────────────
        liq_s  = _liquidity_score(
            liq_metrics["spread_quality"],
            liq_metrics["atm_open_interest"],
        )
        score = _scanner_score(ratio, adj_score, liq_metrics["valid_tenors"], liq_s)

        # ── 9. Build result ───────────────────────────────────────────────
        return {
            "ok":             True,
            "ticker":         ticker,
            "price":          round(current_price, 2),
            "rv30":           round(rv30, 2),
            "iv30":           round(iv30_calc, 2),
            "iv_rv_ratio":    round(ratio, 3),
            "vrp_abs":        vrp_abs,
            "vrp_pct":        round(vrp_pct * 100, 2),   # display as %
            "iv_rank":        round(ivr, 1),
            "iv_surface_status": status,
            "available_tenors":  available_tenors,
            "missing_tenors":    missing_tenors,
            "macro_penalty":     macro_penalty,
            "macro_reasons":     macro_reasons,
            "regime_score":      adj_score,
            "signal":            rec,
            "score":             score,
            # liquidity
            "atm_open_interest":  liq_metrics["atm_open_interest"],
            "atm_volume":         liq_metrics["atm_volume"],
            "spread_pct":         liq_metrics["spread_pct"],
            "spread_quality":     liq_metrics["spread_quality"],
            "contract_count":     liq_metrics["contract_count"],
            "valid_tenors":       liq_metrics["valid_tenors"],
            "liquidity_score":    round(liq_s, 1),
            # IV provenance
            "iv_source_quality":  liq_metrics["iv_source_quality"],
            "broker_verify_required": (
                liq_metrics["iv_source_quality"] == "polygon_model_iv_no_quote"
            ),
            # Earnings / event awareness
            "next_earnings_date":  earn_info["next_earnings_date"],
            "days_to_earnings":    earn_info["days_to_earnings"],
            "earnings_within_14d": earn_info["earnings_within_14d"],
            "earnings_within_30d": earn_info["earnings_within_30d"],
            "event_risk_flag":     earn_info["event_risk_flag"],
            "event_risk_reason":   earn_info["event_risk_reason"],
            "earnings_score_penalty": earn_penalty,
        }

    except Exception as exc:
        logger.warning("Scanner error — %s: %s", ticker, exc, exc_info=False)
        return {"ok": False, "ticker": ticker, "error": str(exc)}


# ===========================================================================
# Main scan orchestrator
# ===========================================================================

def run_scan(params: dict, provider) -> dict:
    """
    Run a full S&P 500 volatility scan.

    Results are cached in memory for the current trading day.
    Pass ``force_refresh=True`` in params to bypass the cache.

    Parameters
    ----------
    params : dict
        Scanner configuration (see module docstring for defaults).
    provider : RealDataProvider
        Live data provider instance.

    Returns
    -------
    dict
        Full scan result with scan_timestamp, universe counts, results list,
        error list, and macro environment metadata.
    """
    today     = datetime.date.today()
    cache_key = today.isoformat()

    # ── Cache read ─────────────────────────────────────────────────────────
    if not params.get("force_refresh", False):
        with _CACHE_LOCK:
            cached = _SCAN_CACHE.get(cache_key)
            if cached is not None:
                return {**cached, "from_cache": True}

    t0 = time.monotonic()
    logger.info("SP500 scanner starting (max_tickers=%s)", params.get("max_tickers", 50))

    # ── Load universe ──────────────────────────────────────────────────────
    tickers        = load_universe()
    max_t          = int(params.get("max_tickers", 50))
    tickers        = tickers[:max_t]
    total_universe = len(tickers)

    # ── Macro penalties (market-wide, computed once) ───────────────────────
    macro_info      = _compute_macro_penalties(provider)
    macro_status    = macro_info.get("macro_status", "unavailable")
    macro_available = macro_info.get("available", False)
    # total_penalty is None when macro is unavailable; float when available/partial
    macro_penalty_raw: float | None = macro_info.get("total_penalty")
    macro_dangerous = (
        macro_penalty_raw is not None and macro_penalty_raw >= _DANGEROUS_MACRO
    )

    # Human-readable warning shown in the UI when data is not fully available
    if macro_status == "unavailable":
        macro_warning: str | None = (
            "Macro data unavailable — regime filter not applied. "
            "Scores may be understated in stressed markets."
        )
    elif macro_status == "partial":
        _missing = macro_info.get("missing_fields", [])
        macro_warning = f"Macro data partial — missing: {', '.join(_missing)}"
    else:
        macro_warning = None

    # ── Batch scan ─────────────────────────────────────────────────────────
    batch_size  = int(params.get("batch_size",    10))
    delay_secs  = float(params.get("delay_seconds", 0.2))

    results:       list[dict] = []
    errors:        list[dict] = []
    excluded_count = 0
    skipped_count  = 0
    scanned        = 0

    for i in range(0, total_universe, batch_size):
        batch = tickers[i: i + batch_size]

        for ticker in batch:
            result = _scan_ticker(ticker, today, provider, macro_info, params)
            scanned += 1

            if result.get("error"):
                errors.append({"ticker": ticker, "error": result["error"]})
            elif result.get("skipped"):
                skipped_count += 1
            elif not result.get("ok"):
                excluded_count += 1
            else:
                results.append(result)

        if i + batch_size < total_universe:
            time.sleep(delay_secs)

    # ── Sort by composite score desc ───────────────────────────────────────
    results.sort(key=lambda r: r.get("score", 0.0), reverse=True)

    max_results = int(params.get("max_results", 50))
    results     = results[:max_results]

    elapsed = round(time.monotonic() - t0, 1)
    logger.info(
        "SP500 scanner done: scanned=%d passed=%d excluded=%d skipped=%d errors=%d (%.1fs)",
        scanned, len(results), excluded_count, skipped_count, len(errors), elapsed,
    )

    output: dict = {
        "scan_timestamp":    datetime.datetime.now().isoformat(),
        "scan_date":         today.isoformat(),
        "total_universe":    total_universe,
        "scanned":           scanned,
        "passed":            len(results),
        "failed":            excluded_count,
        "skipped":           skipped_count,
        "error_count":       len(errors),
        "elapsed_seconds":   elapsed,
        "from_cache":        False,
        # Macro environment
        "macro_dangerous":      macro_dangerous,
        "macro_penalty":        macro_penalty_raw,        # float | None
        "macro_reasons":        macro_info.get("reasons", []),
        "macro_available":      macro_available,
        "macro_status":         macro_status,             # available | partial | unavailable
        "macro_warning":        macro_warning,            # str | None
        "macro_missing_fields": macro_info.get("missing_fields", []),
        # Results
        "results":           results,
        "errors":            errors[:20],   # cap at 20 to keep response lean
        # Echo back applied params (sans force_refresh)
        "params_used": {
            k: v for k, v in params.items() if k != "force_refresh"
        },
    }

    # ── Cache write ────────────────────────────────────────────────────────
    with _CACHE_LOCK:
        _SCAN_CACHE[cache_key] = output

    return output
