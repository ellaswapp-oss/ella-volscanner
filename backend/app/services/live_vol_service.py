"""
services/live_vol_service.py
-----------------------------
Build volatility snapshots from live Polygon prices + options chain.

Called by snapshot_builder when DATA_PROVIDER=real.

Flow
----
1. Fetch 400 days of prices → RV windows, sparklines, IV rank proxy base
2. Fetch today's options chain → iv7/14/30/60/90 via calculate_atm_iv_by_tenor
3. Fallback: if options chain fails → IV = RV30 × 1.05 (synthetic_fallback)
4. Compute macro penalties (non-fatal; zero-penalty if FRED unavailable)
5. Adjust regime score by deducting macro penalties (capped at 25 pts)
"""

from __future__ import annotations

import datetime
import logging
import math

import numpy as np
import pandas as pd

from app.calculations.engine import (
    iv_rank,
    iv_rv_ratio,
    realized_volatility,
    regime_score,
    term_structure,
    trade_recommendation,
    volatility_risk_premium,
)
from app.core.config import settings
from app.data.options_utils import calculate_atm_iv_by_tenor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe(val: float, fallback: float) -> float:
    """Return val if finite, else fallback."""
    return fallback if (val is None or not math.isfinite(float(val))) else float(val)


def _rolling_rv_series(prices: pd.Series, window: int = 30) -> pd.Series:
    """Annualised rolling realised vol (%)."""
    log_ret = np.log(prices / prices.shift(1))
    return log_ret.rolling(window).std() * np.sqrt(252) * 100


def _iv_rank_proxy(current_iv: float, rv30_series: pd.Series, lookback: int = 252) -> float:
    """
    Proxy IV Rank: percentile rank of *current_iv* vs the historical
    distribution of RV30.  Used when true IV history is unavailable.

    Returns 50.0 when the series is too short to rank.
    """
    finite_rv = rv30_series.dropna()
    if len(finite_rv) < 2:
        return 50.0
    history = finite_rv.iloc[-lookback:].values
    return float(iv_rank(current_iv, history))


# ---------------------------------------------------------------------------
# Macro penalties
# ---------------------------------------------------------------------------

def _compute_macro_penalties(provider) -> dict:
    """
    Compute macro risk penalties from live macro series.

    Penalties
    ---------
    VIX curve inverted (VIX > VIX3M)   : −8 pts
    VIX ≥ 32                            : −10 pts
    10Y yield +40 bps in 20 trading days: −5 pts
    Crude oil +10% in 20 trading days   : −5 pts
    Total capped at 25 pts.

    Returns
    -------
    dict with keys:

    available      bool       True unless the macro series fetch itself failed.
    macro_status   str        "available" | "partial" | "unavailable"
    total_penalty  float      Penalty points; 0.0 when status is "unavailable"
                               (no penalty applied — never None, so callers can
                               always subtract it from the raw regime score).
    reasons        list[str]  Human-readable penalty reasons (empty = regime clear).
    missing_fields list[str]  Fields that could not be computed.

    Fails silently — each sub-signal is computed in its own try/except so a
    single failed feed (e.g. FRED rate-limit) does not zero-out the others.
    Status is "partial" when at least one field computed; "unavailable" when
    none could be computed even though the macro series was fetched.
    """
    try:
        end   = datetime.date.today()
        # 65 calendar days ≈ 45 trading days — enough for the 20-day lookback checks
        start = end - datetime.timedelta(days=65)
        macro_df = provider.get_macro_series(start, end)
    except Exception as exc:
        logger.warning("Macro data unavailable for penalty calc: %s", exc)
        return {
            "total_penalty":  0.0,
            "reasons":        [],
            "available":      False,
            "macro_status":   "unavailable",
            "missing_fields": ["vix1m", "vix3m", "us10y", "crude_oil"],
        }

    penalty  = 0.0
    reasons: list[str] = []
    missing: list[str] = []

    # ── VIX curve inversion + stress level ────────────────────────────────
    try:
        vix1m = float(macro_df["vix1m"].dropna().iloc[-1])
        vix3m = float(macro_df["vix3m"].dropna().iloc[-1])

        if vix1m > vix3m:
            penalty += 8.0
            reasons.append(
                f"VIX curve inverted: VIX {vix1m:.1f} > VIX3M {vix3m:.1f}"
            )
        if vix1m >= 32.0:
            penalty += 10.0
            reasons.append(f"VIX ≥ 32 (current: {vix1m:.1f})")

    except Exception as exc:
        logger.warning("Macro VIX data unavailable: %s", exc)
        missing.extend(["vix1m", "vix3m"])

    # ── 10Y yield spike ───────────────────────────────────────────────────
    try:
        us10y = macro_df["us10y"].dropna()
        if len(us10y) >= 21:
            yield_chg = float(us10y.iloc[-1]) - float(us10y.iloc[-21])
            if yield_chg > 0.40:
                penalty += 5.0
                reasons.append(
                    f"10Y yield +{yield_chg * 100:.0f} bps in 20 days "
                    f"({float(us10y.iloc[-21]):.2f}% → {float(us10y.iloc[-1]):.2f}%)"
                )
    except Exception as exc:
        logger.warning("Macro 10Y yield data unavailable: %s", exc)
        missing.append("us10y")

    # ── Crude oil shock ───────────────────────────────────────────────────
    try:
        crude = macro_df["crude_oil"].dropna()
        if len(crude) >= 21:
            crude_chg_pct = (float(crude.iloc[-1]) / float(crude.iloc[-21]) - 1.0) * 100.0
            if crude_chg_pct > 10.0:
                penalty += 5.0
                reasons.append(
                    f"Crude oil +{crude_chg_pct:.1f}% in 20 days "
                    f"(${float(crude.iloc[-21]):.1f} → ${float(crude.iloc[-1]):.1f})"
                )
    except Exception as exc:
        logger.warning("Macro crude oil data unavailable: %s", exc)
        missing.append("crude_oil")

    # ── Determine status ──────────────────────────────────────────────────
    _all_fields = {"vix1m", "vix3m", "us10y", "crude_oil"}
    if set(missing) >= _all_fields:
        # Every field failed — treat as fully unavailable
        macro_status  = "unavailable"
        available     = False
        total_penalty: float = 0.0
    elif missing:
        macro_status  = "partial"
        available     = True
        total_penalty = round(min(penalty, 25.0), 1)
    else:
        macro_status  = "available"
        available     = True
        total_penalty = round(min(penalty, 25.0), 1)

    return {
        "total_penalty":  total_penalty,
        "reasons":        reasons,
        "available":      available,
        "macro_status":   macro_status,
        "missing_fields": missing,
    }


# ---------------------------------------------------------------------------
# Main snapshot builder
# ---------------------------------------------------------------------------

def build_live_snapshot(ticker: str, provider) -> dict:
    """
    Build a full ticker snapshot from live Polygon prices + options chain.

    Parameters
    ----------
    ticker   : uppercase ticker symbol (already validated by the API layer)
    provider : RealDataProvider instance (passed from snapshot_builder)

    Returns
    -------
    dict matching the shape expected by market.py handlers (same keys as
    build_ticker_snapshot in vol_service.py, plus metadata keys).
    """
    today = datetime.date.today()
    days  = settings.price_history_days  # 400

    # ── 1. Price history ──────────────────────────────────────────────────────
    prices = provider.get_prices_days(ticker, days)

    rv10 = realized_volatility(prices, 10)
    rv20 = realized_volatility(prices, 20)
    rv30 = realized_volatility(prices, 30)
    rv60 = realized_volatility(prices, 60)

    rv30_series   = _rolling_rv_series(prices, 30)
    current_price = float(prices.iloc[-1])
    safe_rv30     = _safe(rv30, 20.0)

    # ── 2. Options chain → IV surface ─────────────────────────────────────────
    iv_source = "live_options_chain"

    # Core tenors that define term structure slopes (iv14 is a curve point only)
    _CORE_TS_TENORS = {"iv7", "iv30", "iv60", "iv90"}
    _ALL_TENORS     = ["iv7", "iv14", "iv30", "iv60", "iv90"]

    iv7_raw:  float | None = None
    iv14_raw: float | None = None
    iv30_raw: float | None = None
    iv60_raw: float | None = None
    iv90_raw: float | None = None

    iv_surface_metadata: dict

    try:
        chain   = provider.get_options_chain(ticker, today)
        iv_surf = calculate_atm_iv_by_tenor(chain, current_price, pricing_date=today)

        # calculate_atm_iv_by_tenor returns {iv7, iv14, iv30, iv60, iv90} as
        # percentages (or None when the nearest expiration is outside tolerance)
        def _valid(v) -> float | None:
            """Return v as float if finite, else None."""
            if v is None:
                return None
            try:
                f = float(v)
                return f if math.isfinite(f) else None
            except (TypeError, ValueError):
                return None

        iv7_raw  = _valid(iv_surf.get("iv7"))
        iv14_raw = _valid(iv_surf.get("iv14"))
        iv30_raw = _valid(iv_surf.get("iv30"))
        iv60_raw = _valid(iv_surf.get("iv60"))
        iv90_raw = _valid(iv_surf.get("iv90"))

        # ── IV surface quality metadata ───────────────────────────────────────
        iv_raw_map = {
            "iv7": iv7_raw, "iv14": iv14_raw, "iv30": iv30_raw,
            "iv60": iv60_raw, "iv90": iv90_raw,
        }
        available_tenors = [t for t in _ALL_TENORS if iv_raw_map[t] is not None]
        missing_tenors   = [t for t in _ALL_TENORS if iv_raw_map[t] is None]
        required_tenor_available = iv30_raw is not None
        missing_core = set(missing_tenors) & _CORE_TS_TENORS

        if not required_tenor_available:
            iv_surface_status      = "unavailable"
            term_structure_quality = "unavailable"
        elif missing_core:
            iv_surface_status      = "partial"
            term_structure_quality = "partial"
        else:
            iv_surface_status      = "healthy"
            term_structure_quality = "full"

        iv_surface_metadata = {
            "iv_surface_status":        iv_surface_status,
            "required_tenor_available": required_tenor_available,
            "available_tenors":         available_tenors,
            "missing_tenors":           missing_tenors,
            "term_structure_quality":   term_structure_quality,
        }

    except Exception as exc:
        logger.warning(
            "Options chain fetch failed for %s, using RV-based fallback: %s", ticker, exc
        )
        iv_source = "synthetic_fallback"
        # Fallback: slight IV premium over realized vol for all tenors
        fallback = safe_rv30 * 1.05
        iv7_raw  = fallback * 0.95
        iv14_raw = None
        iv30_raw = fallback
        iv60_raw = fallback * 1.02
        iv90_raw = fallback * 1.04

        iv_surface_metadata = {
            "iv_surface_status":        "synthetic_fallback",
            "required_tenor_available": False,
            "available_tenors":         [],
            "missing_tenors":           _ALL_TENORS,
            "term_structure_quality":   "partial",
        }

    # ── 3. Exposed IV values (can be None) and computation value for iv30 ────
    # iv30_for_computation: always a float — used internally for regime metrics.
    # iv30 (exposed):       None when iv30 is not available from the real chain.
    iv30_for_computation: float = (
        float(iv30_raw) if iv30_raw is not None else safe_rv30 * 1.05
    )

    # iv7/iv60/iv90: passed through as-is (None when not available from chain)
    iv7:  float | None = iv7_raw
    iv14: float | None = iv14_raw
    iv30: float | None = iv30_raw   # exposed in response
    iv60: float | None = iv60_raw
    iv90: float | None = iv90_raw

    # ── 4. Derived volatility metrics ─────────────────────────────────────────
    ratio = _safe(iv_rv_ratio(iv30_for_computation, safe_rv30), 1.0)
    vrp   = _safe(volatility_risk_premium(iv30_for_computation, safe_rv30), 0.0)

    # IV Rank proxy — rank of current IV30 vs rolling RV30 history (1-yr window)
    ivr              = _safe(_iv_rank_proxy(iv30_for_computation, rv30_series, lookback=252), 50.0)
    iv_rank_is_proxy = True

    # Z-score — how many std-devs is IV30 above the distribution of RV30?
    # Approximates (IV - RV) / std when IV history isn't available.
    rv30_finite = rv30_series.dropna()
    if len(rv30_finite) >= 2:
        anchor    = rv30_finite.iloc[-252:].values if len(rv30_finite) > 252 else rv30_finite.values
        mu_rv     = float(anchor.mean())
        std_rv    = float(anchor.std(ddof=1)) if len(anchor) > 1 else 1.0
        std_rv    = std_rv if std_rv > 0 else 1.0
        current_z = float(np.clip((iv30_for_computation - mu_rv) / std_rv, -3.0, 3.0))
    else:
        current_z = 0.0

    # term_structure accepts None for iv7/iv60/iv90; returns None slopes + "partial" shape
    ts = term_structure(iv7, iv30_for_computation, iv60, iv90)

    # ── 5. Macro penalties ────────────────────────────────────────────────────
    macro_pen = _compute_macro_penalties(provider)

    # ── 6. Regime score with penalty ─────────────────────────────────────────
    raw_score = regime_score({
        "iv_rank":     ivr,
        "vrp":         vrp,
        "iv_rv_ratio": ratio,
        "z_score":     current_z,
    })
    adjusted_score = round(
        float(np.clip(raw_score - macro_pen["total_penalty"], 0.0, 100.0)), 2
    )
    rec = trade_recommendation(adjusted_score)

    # ── 7. Sparklines (last 60 trading days) ─────────────────────────────────
    rv_spark      = rv30_series.dropna().iloc[-60:]
    spark_dates   = [d.strftime("%Y-%m-%d") for d in rv_spark.index]
    rv_spark_vals = [round(float(v), 2) for v in rv_spark]
    # No historical IV from options chain — repeat iv30_for_computation for alignment
    iv_spark_vals = [round(iv30_for_computation, 2)] * len(rv_spark)

    def _r(v: float | None) -> float | None:
        """Round to 2 dp, propagating None."""
        return round(v, 2) if v is not None else None

    return {
        "ticker": ticker,
        "price":  round(current_price, 2),
        "rv": {
            "rv10": round(_safe(rv10, 0.0), 2),
            "rv20": round(_safe(rv20, 0.0), 2),
            "rv30": round(safe_rv30, 2),
            "rv60": round(_safe(rv60, 0.0), 2),
        },
        "iv": {
            "iv7":  _r(iv7),
            "iv14": _r(iv14),
            "iv30": _r(iv30),   # None when not from real chain
            "iv60": _r(iv60),
            "iv90": _r(iv90),
        },
        "iv_rank":         round(ivr, 1),
        "iv_rv_ratio":     round(ratio, 3),
        "vrp":             round(vrp, 4),
        "iv_rv_zscore":    round(current_z, 2),
        "term_structure":  ts,
        "skew": {
            # Always use iv30_for_computation (never None) for derived skew estimates
            "skew_25d": round(iv30_for_computation * 0.08, 2),
            "atm_iv":   round(iv30_for_computation, 2),
            "put_25d":  round(iv30_for_computation * 1.08, 2),
            "call_25d": round(iv30_for_computation * 0.94, 2),
        },
        "regime_score": adjusted_score,
        "signal":       rec,
        "sparklines": {
            "dates": spark_dates,
            "rv30":  rv_spark_vals,
            "iv30":  iv_spark_vals,
        },
        # ── Metadata ──────────────────────────────────────────────────────────
        "provider":             "real",
        "iv_source":            iv_source,
        "iv_rank_is_proxy":     iv_rank_is_proxy,
        "as_of_date":           today.isoformat(),
        "macro_penalties":      macro_pen,
        "iv_surface_metadata":  iv_surface_metadata,
    }
