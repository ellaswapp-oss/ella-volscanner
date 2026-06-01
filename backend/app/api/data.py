"""
api/data.py
-----------
Data-layer inspection and diagnostic endpoints.

Routes
------
GET /api/data/provider-status
    Active provider name, live flag, and per-capability status.

GET /api/data/prices/{ticker}
    Daily OHLCV bars for a ticker.
    Accepts ``days`` or explicit ``start`` / ``end`` query params.

GET /api/data/macro
    Daily macro signal history: VIX1M, VIX3M, breadth, US10Y,
    crude oil, credit spread, SPY price — aligned to SPY calendar.

GET /api/data/options/{ticker}
    Current options chain.  Optional filters: date, expiration,
    option_type, near_atm.

GET /api/data/iv-surface/{ticker}
    ATM IV for tenors 7 / 14 / 30 / 60 / 90 days, plus the term
    structure curve and per-tenor ATM contract details.
"""

from __future__ import annotations

import datetime
import math

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from app.data.iv_store import store as _iv_store
from app.data.options_utils import calculate_atm_iv_by_tenor
from app.data.registry import get_provider
from app.core.config import settings


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _safe_float(v) -> float | None:
    """Return None if v is None or NaN, else float(v)."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> int | None:
    """Return None if v is None, NaN, or NA; else int(v)."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None

router = APIRouter(prefix="/api/data", tags=["data"])


# ---------------------------------------------------------------------------
# Provider status
# ---------------------------------------------------------------------------

@router.get("/provider-status")
def provider_status() -> dict:
    """
    Return metadata about the active data provider.

    Response shape
    --------------
    {
        "provider":     "mock" | "real",
        "label":        "Mock Data" | "Live Data",
        "is_live":      false | true,
        "capabilities": { ... }
    }
    """
    return get_provider().provider_status()


# ---------------------------------------------------------------------------
# Prices / OHLCV
# ---------------------------------------------------------------------------

@router.get("/prices/{ticker}")
def get_prices(
    ticker: str,
    days:  int | None = Query(default=400, ge=1, le=2520, description="Trading days of history"),
    start: str | None = Query(default=None, description="ISO start date (overrides days)"),
    end:   str | None = Query(default=None, description="ISO end date (default: today)"),
) -> dict:
    """
    Daily OHLCV bars for ``ticker``.

    Parameters
    ----------
    ticker : str
        Stock ticker symbol (case-insensitive).
    days : int, optional
        Number of trailing trading days.  Ignored when ``start`` is given.
    start : str, optional
        ISO 8601 start date (``YYYY-MM-DD``).
    end : str, optional
        ISO 8601 end date.  Defaults to today.

    Response shape
    --------------
    {
        "ticker":     "SPY",
        "provider":   "mock" | "real",
        "data_source": "...",
        "rows": [
            { "date": "2024-01-02", "open": 419.0, "high": 422.0,
              "low": 418.5, "close": 421.5, "volume": 75123456 },
            ...
        ],
        "stats": {
            "n_rows":     252,
            "first_date": "2023-01-03",
            "last_date":  "2024-01-02",
            "latest_close": 421.5
        }
    }
    """
    provider = get_provider()
    ticker   = ticker.upper()

    # Resolve date range
    end_date: datetime.date = (
        datetime.date.fromisoformat(end) if end else datetime.date.today()
    )

    try:
        if start:
            start_date = datetime.date.fromisoformat(start)
            df = provider.get_ohlcv(ticker, start_date, end_date)
        else:
            df = provider.get_ohlcv_days(ticker, days or 400)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No price data found for '{ticker}'.",
        )

    # Serialise rows — replace NaN with None for clean JSON
    rows = []
    for ts, row in df.iterrows():
        rows.append({
            "date":   ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts),
            "open":   None if (v := row.get("open")) is not None and math.isnan(v) else v,
            "high":   None if (v := row.get("high")) is not None and math.isnan(v) else v,
            "low":    None if (v := row.get("low"))  is not None and math.isnan(v) else v,
            "close":  float(row["close"]),
            "volume": None if (v := row.get("volume")) is not None and math.isnan(float(v)) else int(v) if v is not None else None,
        })

    latest_close = float(df["close"].iloc[-1]) if not df.empty else None
    first_date   = df.index[0].strftime("%Y-%m-%d")  if not df.empty else None
    last_date    = df.index[-1].strftime("%Y-%m-%d") if not df.empty else None

    return {
        "ticker":      ticker,
        "provider":    provider.name,
        "data_source": provider.label,
        "rows":        rows,
        "stats": {
            "n_rows":       len(rows),
            "first_date":   first_date,
            "last_date":    last_date,
            "latest_close": latest_close,
        },
    }


# ---------------------------------------------------------------------------
# Macro series
# ---------------------------------------------------------------------------

_MACRO_COLS = ["vix1m", "vix3m", "breadth", "us10y", "crude_oil",
               "credit_spread", "spy_price"]


@router.get("/macro")
def get_macro(
    days:  int       = Query(default=400, ge=20, le=2520,
                             description="Trailing trading days"),
    start: str | None = Query(default=None, description="ISO start date"),
    end:   str | None = Query(default=None, description="ISO end date"),
) -> dict:
    """
    Daily macro signal history aligned to the SPY trading calendar.

    Columns
    -------
    vix1m, vix3m, breadth, us10y, crude_oil, credit_spread, spy_price

    Also returns per-row ``vix_spread`` (vix3m − vix1m, positive = contango).

    Response shape
    --------------
    {
        "provider":         "mock" | "real",
        "data_source":      "...",
        "breadth_is_proxy": bool,
        "stats":            { latest values + is_vix_inverted },
        "rows":             [ { date, vix1m, vix3m, vix_spread, breadth,
                                us10y, crude_oil, credit_spread, spy_price }, ... ]
    }
    """
    provider = get_provider()

    try:
        if start:
            start_date = datetime.date.fromisoformat(start)
            end_date   = datetime.date.fromisoformat(end) if end else datetime.date.today()
            df = provider.get_macro_series(start_date, end_date)
        else:
            df = provider.get_macro_series_days(days)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    if df.empty:
        raise HTTPException(status_code=404, detail="No macro data returned.")

    # Ensure credit_spread column exists (older mock versions may not have it)
    if "credit_spread" not in df.columns:
        df["credit_spread"] = float("nan")

    # Serialise rows
    rows = []
    for ts, row in df.iterrows():
        r: dict = {"date": ts.strftime("%Y-%m-%d")}
        for col in _MACRO_COLS:
            r[col] = _safe_float(row.get(col))
        v1 = _safe_float(row.get("vix1m"))
        v3 = _safe_float(row.get("vix3m"))
        r["vix_spread"] = round(v3 - v1, 4) if (v1 is not None and v3 is not None) else None
        rows.append(r)

    # Latest-values summary
    last = df.iloc[-1]
    lv1  = _safe_float(last.get("vix1m"))
    lv3  = _safe_float(last.get("vix3m"))

    stats = {
        "latest_vix":          lv1,
        "latest_vix3m":        lv3,
        "latest_vix_spread":   round(lv3 - lv1, 2) if (lv1 and lv3) else None,
        "is_vix_inverted":     bool(lv3 < lv1)     if (lv1 and lv3) else False,
        "latest_us10y":        _safe_float(last.get("us10y")),
        "latest_crude_oil":    _safe_float(last.get("crude_oil")),
        "latest_credit_spread":_safe_float(last.get("credit_spread")),
        "latest_spy":          _safe_float(last.get("spy_price")),
        "n_rows":              len(rows),
        "first_date":          rows[0]["date"]  if rows else None,
        "last_date":           rows[-1]["date"] if rows else None,
    }

    return {
        "provider":         provider.name,
        "data_source":      provider.label,
        "breadth_is_proxy": bool(getattr(provider, "uses_proxy_breadth", False)),
        "stats":            stats,
        "rows":             rows,
    }


# ---------------------------------------------------------------------------
# Options chain
# ---------------------------------------------------------------------------

_NEAR_ATM_PCT = 0.10   # ±10% of underlying price


@router.get("/options/{ticker}")
def get_options_chain(
    ticker:      str,
    date:        str | None  = Query(default=None,  description="ISO pricing date (default: today)"),
    expiration:  str | None  = Query(default=None,  description="Filter by expiration YYYY-MM-DD"),
    option_type: str | None  = Query(default=None,  description="Filter: 'call' or 'put'"),
    near_atm:    bool        = Query(default=False, description="Restrict to strikes within ±10% of spot"),
) -> dict:
    """
    Options chain for ``ticker`` (all expirations, all strikes by default).

    Response shape
    --------------
    {
        "ticker":           "SPY",
        "provider":         "mock" | "real",
        "data_source":      "...",
        "pricing_date":     "2024-01-15",
        "underlying_price": 562.0,
        "n_contracts":      294,
        "rows": [
            {
                "underlying_ticker": "SPY",
                "expiration": "2024-01-19",
                "strike":     450.0,
                "option_type": "call",
                "bid": 8.10,  "ask": 8.90,  "mid": 8.50,  "last": 8.60,
                "implied_volatility": 18.95,   // percent
                "delta": 0.432, "gamma": 0.022,
                "theta": -0.185, "vega": 0.321,
                "open_interest": 45678, "volume": 12345
            }, ...
        ]
    }
    """
    provider     = get_provider()
    ticker       = ticker.upper()
    pricing_date = (
        datetime.date.fromisoformat(date) if date else datetime.date.today()
    )

    # Underlying price — used for near_atm filter
    try:
        price_s          = provider.get_prices_days(ticker, 5)
        underlying_price = float(price_s.iloc[-1]) if not price_s.empty else None
    except Exception:
        underlying_price = None

    try:
        df = provider.get_options_chain(ticker, pricing_date)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No options chain data returned for '{ticker}'.",
        )

    # ── Apply optional filters ─────────────────────────────────────────
    if expiration:
        df = df[df["expiration"] == expiration]
    if option_type:
        df = df[df["option_type"] == option_type.lower()]
    if near_atm and underlying_price:
        lo = underlying_price * (1 - _NEAR_ATM_PCT)
        hi = underlying_price * (1 + _NEAR_ATM_PCT)
        df = df[(df["strike"] >= lo) & (df["strike"] <= hi)]

    # ── Serialise rows ─────────────────────────────────────────────────
    rows = []
    for _, row in df.iterrows():
        iv_frac = _safe_float(row.get("implied_volatility"))
        rows.append({
            "underlying_ticker":  row.get("underlying_ticker"),
            "expiration":         row.get("expiration"),
            "strike":             _safe_float(row.get("strike")),
            "option_type":        row.get("option_type"),
            "bid":                _safe_float(row.get("bid")),
            "ask":                _safe_float(row.get("ask")),
            "mid":                _safe_float(row.get("mid")),
            "last":               _safe_float(row.get("last")),
            # Expose IV as percentage
            "implied_volatility": round(iv_frac * 100.0, 2) if iv_frac is not None else None,
            "delta":              _safe_float(row.get("delta")),
            "gamma":              _safe_float(row.get("gamma")),
            "theta":              _safe_float(row.get("theta")),
            "vega":               _safe_float(row.get("vega")),
            "open_interest":      _safe_int(row.get("open_interest")),
            "volume":             _safe_int(row.get("volume")),
        })

    return {
        "ticker":           ticker,
        "provider":         provider.name,
        "data_source":      provider.label,
        "pricing_date":     pricing_date.isoformat(),
        "underlying_price": underlying_price,
        "n_contracts":      len(rows),
        "rows":             rows,
    }


# ---------------------------------------------------------------------------
# IV surface
# ---------------------------------------------------------------------------

_TENOR_LABELS: dict[str, str] = {
    "iv7": "7D", "iv14": "14D", "iv30": "30D", "iv60": "60D", "iv90": "90D",
}
_TENOR_DAYS: dict[str, int] = {
    "iv7": 7, "iv14": 14, "iv30": 30, "iv60": 60, "iv90": 90,
}


@router.get("/iv-surface/{ticker}")
def get_iv_surface(
    ticker: str,
    date:   str | None = Query(default=None, description="ISO pricing date (default: today)"),
) -> dict:
    """
    ATM implied-volatility surface for ``ticker``.

    Computes five-point term structure from the options chain using the
    ``calculate_atm_iv_by_tenor`` helper (call+put average, nearest ATM
    strike per tenor).

    Response shape
    --------------
    {
        "ticker":           "SPY",
        "provider":         "mock",
        "data_source":      "...",
        "pricing_date":     "2024-01-15",
        "underlying_price": 562.0,
        "iv_surface": { "iv7": 13.5, "iv14": 14.2, "iv30": 15.8,
                        "iv60": 16.9, "iv90": 17.6 },
        "curve": [
            {"tenor": "7D",  "dte": 7,  "iv": 13.5},
            {"tenor": "14D", "dte": 14, "iv": 14.2},
            ...
        ],
        "atm_contracts": [
            {"tenor": "7D", "dte": 7, "expiration": "2024-01-22",
             "strike": 562.0, "call_iv": 13.3, "put_iv": 13.7, "avg_iv": 13.5},
            ...
        ]
    }
    """
    provider     = get_provider()
    ticker       = ticker.upper()
    pricing_date = (
        datetime.date.fromisoformat(date) if date else datetime.date.today()
    )

    # Underlying price
    try:
        price_s          = provider.get_prices_days(ticker, 5)
        underlying_price = float(price_s.iloc[-1]) if not price_s.empty else None
    except Exception:
        underlying_price = None

    if underlying_price is None:
        raise HTTPException(
            status_code=404,
            detail=f"Could not determine underlying price for '{ticker}'.",
        )

    # Fetch chain
    try:
        df = provider.get_options_chain(ticker, pricing_date)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    # IV surface (percentages) — includes _tenor_meta for DTE diagnostics
    iv_surface_raw = calculate_atm_iv_by_tenor(df, underlying_price, pricing_date)
    tenor_meta     = iv_surface_raw.pop("_tenor_meta", {})
    iv_surface     = {k: iv_surface_raw[k] for k in ("iv7", "iv14", "iv30", "iv60", "iv90")}

    # Build curve list — only include tenors with valid IV and within tolerance
    curve = [
        {
            "tenor":  _TENOR_LABELS[k],
            "dte":    _TENOR_DAYS[k],
            "iv":     iv_surface[k],
            "actual_dte":       tenor_meta.get(k, {}).get("actual_dte"),
            "dte_diff":         tenor_meta.get(k, {}).get("dte_diff"),
            "within_tolerance": tenor_meta.get(k, {}).get("within_tolerance", True),
        }
        for k in ("iv7", "iv14", "iv30", "iv60", "iv90")
        if iv_surface[k] is not None
    ]

    # Per-tenor ATM contract detail — built from tenor_meta to avoid re-selecting expirations
    atm_contracts = []
    if not df.empty:
        try:
            df_exp = df.copy()
            df_exp["_exp_date"] = pd.to_datetime(df_exp["expiration"]).dt.date
        except Exception:
            df_exp = pd.DataFrame()

        if not df_exp.empty:
            for key, dte in [("iv7", 7), ("iv14", 14), ("iv30", 30), ("iv60", 60), ("iv90", 90)]:
                m = tenor_meta.get(key, {})
                if not m.get("within_tolerance") or not m.get("expiration"):
                    continue   # skipped due to tolerance — don't include in ATM table
                exp_str  = m["expiration"]
                exp_date = datetime.date.fromisoformat(exp_str)
                exp_df   = df_exp[df_exp["_exp_date"] == exp_date]

                call_iv = put_iv = strike = None
                for opt_t, field in (("call", "call_iv"), ("put", "put_iv")):
                    leg = exp_df[exp_df["option_type"] == opt_t].dropna(
                        subset=["strike", "implied_volatility"]
                    )
                    if not leg.empty:
                        idx     = (leg["strike"] - underlying_price).abs().idxmin()
                        iv_frac = _safe_float(leg.loc[idx, "implied_volatility"])
                        if field == "call_iv":
                            call_iv = round(iv_frac * 100.0, 2) if iv_frac else None
                            strike  = _safe_float(leg.loc[idx, "strike"])
                        else:
                            put_iv  = round(iv_frac * 100.0, 2) if iv_frac else None

                iv_vals = [v for v in (call_iv, put_iv) if v is not None]
                avg_iv  = round(sum(iv_vals) / len(iv_vals), 2) if iv_vals else None

                atm_contracts.append({
                    "tenor":      _TENOR_LABELS[key],
                    "dte":        dte,
                    "actual_dte": m.get("actual_dte"),
                    "dte_diff":   m.get("dte_diff"),
                    "expiration": exp_str,
                    "strike":     strike,
                    "call_iv":    call_iv,
                    "put_iv":     put_iv,
                    "avg_iv":     avg_iv,
                })

    return {
        "ticker":           ticker,
        "provider":         provider.name,
        "data_source":      provider.label,
        "pricing_date":     pricing_date.isoformat(),
        "underlying_price": underlying_price,
        "iv_surface":       iv_surface,
        "curve":            curve,
        "atm_contracts":    atm_contracts,
    }


# ---------------------------------------------------------------------------
# Historical IV surface  GET /api/data/historical-iv/{ticker}
# ---------------------------------------------------------------------------

@router.get("/iv-quality-progress")
def iv_quality_progress(
    data_days: int = Query(
        400, ge=30, le=2520,
        description="Window size in trading days (default 400 ≈ 1.6 years)",
    ),
) -> dict:
    """
    Per-ticker IV data-quality progress toward the GOOD (≥80% real) threshold.

    For each configured ticker returns:
      - real / interpolated / synthetic row counts in the window
      - real_pct (0.0–1.0) and status GOOD / MIXED / POOR
      - rows_needed_for_good  (0 once GOOD)
      - latest / earliest real IV date in the store
      - missing_real_ranges   — contiguous date gaps without live-chain data

    Also returns an aggregate summary across all tickers plus ready-to-run
    backfill commands for each ticker.
    """
    import math as _math

    today = datetime.date.today()

    # Convert trading-day window → approximate calendar-day window
    cal_days   = int(data_days * 365.25 / 252)
    win_start  = today - datetime.timedelta(days=cal_days)

    def _weekdays(start: datetime.date, end: datetime.date) -> list[str]:
        out, cur = [], start
        while cur <= end:
            if cur.weekday() < 5:
                out.append(cur.isoformat())
            cur += datetime.timedelta(days=1)
        return out

    def _missing_ranges(trading_days: list[str], real_dates: set[str]) -> list[dict]:
        """Find contiguous runs of trading days that have no real IV data."""
        gaps: list[dict] = []
        run_start: str | None = None
        run_count = 0
        for d in trading_days:
            if d not in real_dates:
                if run_start is None:
                    run_start = d
                run_count += 1
            else:
                if run_start is not None:
                    gaps.append({"start": run_start, "end": trading_days[trading_days.index(d) - 1], "count": run_count})
                    run_start, run_count = None, 0
        if run_start is not None:
            gaps.append({"start": run_start, "end": trading_days[-1], "count": run_count})
        return gaps

    all_trading_days = _weekdays(win_start, today)
    total_required   = len(all_trading_days)
    good_threshold   = _math.ceil(0.80 * total_required)

    ticker_results: list[dict] = []

    for sym in sorted(settings.tickers):
        df = _iv_store.get_history(sym, win_start.isoformat(), today.isoformat())

        if df.empty:
            real_rows  = interp_rows = synth_rows = 0
            latest_real = earliest_real = None
            real_dates:  set[str] = set()
        else:
            src = df["source"]
            # All source labels that count as "real" IV data
            _REAL_SOURCES = {
                "live_options_chain",         # legacy label
                "polygon_live_snapshot",
                "polygon_historical_options",
                "orats_historical",
                "optionmetrics_historical",
            }
            real_mask   = src.isin(_REAL_SOURCES)
            real_rows   = int(real_mask.sum())
            interp_rows = int((src == "interpolated").sum())
            synth_rows  = int((~real_mask & (src != "interpolated")).sum())

            real_df = df[real_mask]
            if not real_df.empty:
                latest_real   = real_df["date"].max()
                earliest_real = real_df["date"].min()
                real_dates    = set(real_df["date"].tolist())
            else:
                latest_real = earliest_real = None
                real_dates  = set()

        real_pct             = real_rows / total_required if total_required > 0 else 0.0
        rows_needed_for_good = max(0, good_threshold - real_rows)

        if real_pct >= 0.80:
            status = "GOOD"
        elif real_pct >= 0.40:
            status = "MIXED"
        else:
            status = "POOR"

        missing_ranges = _missing_ranges(all_trading_days, real_dates)

        # Backfill command for this ticker
        backfill_cmd = f"python scripts/backfill_iv_surface.py --ticker {sym} --days {data_days}"

        ticker_results.append({
            "ticker":               sym,
            "total_required_rows":  total_required,
            "real_rows":            real_rows,
            "interpolated_rows":    interp_rows,
            "synthetic_rows":       synth_rows,
            "real_pct":             round(real_pct, 4),
            "rows_needed_for_good": rows_needed_for_good,
            "latest_real_iv_date":  latest_real,
            "earliest_real_iv_date": earliest_real,
            "status":               status,
            "missing_real_ranges":  missing_ranges,
            "backfill_cmd":         backfill_cmd,
        })

    # Aggregate summary
    agg_real  = sum(t["real_rows"]  for t in ticker_results)
    agg_total = sum(t["total_required_rows"] for t in ticker_results)
    agg_synth = sum(t["synthetic_rows"] for t in ticker_results)
    agg_interp = sum(t["interpolated_rows"] for t in ticker_results)
    agg_pct   = agg_real / agg_total if agg_total > 0 else 0.0

    if agg_pct >= 0.80:
        agg_status = "GOOD"
    elif agg_pct >= 0.40:
        agg_status = "MIXED"
    else:
        agg_status = "POOR"

    all_tickers_cmd = (
        "python scripts/backfill_iv_surface.py "
        f"--tickers {' '.join(sorted(settings.tickers))} "
        f"--days {data_days}"
    )

    return {
        "window": {
            "data_days":       data_days,
            "start":           win_start.isoformat(),
            "end":             today.isoformat(),
            "total_trading_days": total_required,
            "good_threshold_rows": good_threshold,
        },
        "summary": {
            "total_required_rows":  agg_total,
            "real_rows":            agg_real,
            "interpolated_rows":    agg_interp,
            "synthetic_rows":       agg_synth,
            "real_pct":             round(agg_pct, 4),
            "status":               agg_status,
            "rows_needed_for_good": max(0, _math.ceil(0.80 * agg_total) - agg_real),
            "backfill_all_cmd":     all_tickers_cmd,
        },
        "tickers": ticker_results,
    }


@router.get("/iv-surface-health/{ticker}")
def get_iv_surface_health(
    ticker: str,
    date:   str | None = Query(default=None, description="ISO pricing date (default: today)"),
) -> dict:
    """
    IV surface sanity checks and per-tenor bid/ask diagnostics.

    Response shape
    --------------
    {
        "ticker":             "SPY",
        "provider":           "real",
        "pricing_date":       "2026-05-26",
        "underlying_price":   562.0,
        "status":             "healthy" | "suspicious" | "invalid",
        "warnings": [
            {
                "severity": "warning" | "invalid",
                "code":     "missing_tenor" | "iv_out_of_range" | "tenor_jump" | "duplicate_iv",
                "message":  "...",
                ...context keys...
            }
        ],
        "iv_surface":  { "iv7": 12.5, "iv14": null, "iv30": 14.5, "iv60": 28.9, "iv90": 28.9 },
        "tenor_details": [
            {
                "tenor":      "7D",
                "tenor_key":  "iv7",
                "dte_target": 7,
                "expiration": "2026-06-02",
                "dte_actual": 7,
                "avg_iv":     12.5,
                "call": {
                    "strike": 562.0,
                    "iv": 12.3, "bid": 4.20, "ask": 4.50, "mid": 4.35,
                    "spread": 0.30, "spread_pct": 6.9,
                    "spread_quality": "FAIR",
                    "open_interest": 45678, "volume": 12345
                },
                "put": { ... }
            },
            ...
        ],
        "source_expirations": ["2026-06-02", "2026-06-09", ...],
        "n_expirations":      18
    }
    """
    from app.data.iv_surface_health import validate_iv_surface, spread_quality

    provider     = get_provider()
    ticker_up    = ticker.upper()
    pricing_date = (
        datetime.date.fromisoformat(date) if date else datetime.date.today()
    )

    # Underlying price
    try:
        price_s          = provider.get_prices_days(ticker_up, 5)
        underlying_price = float(price_s.iloc[-1]) if not price_s.empty else None
    except Exception:
        underlying_price = None

    if underlying_price is None:
        raise HTTPException(
            status_code=404,
            detail=f"Could not determine underlying price for '{ticker_up}'.",
        )

    # Options chain
    try:
        df = provider.get_options_chain(ticker_up, pricing_date)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No options chain data for '{ticker_up}'.",
        )

    # Parse expiration dates once
    df = df.copy()
    try:
        df["_exp_date"] = pd.to_datetime(df["expiration"]).dt.date
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse expiration dates: {exc}") from exc

    future_exps: list[datetime.date] = sorted(
        {d for d in df["_exp_date"].dropna() if d > pricing_date}
    )
    source_expirations = [d.isoformat() for d in future_exps]

    # IV surface (percentages) — extract _tenor_meta for DTE diagnostics
    iv_surface_raw = calculate_atm_iv_by_tenor(df, underlying_price, pricing_date)
    tenor_meta     = iv_surface_raw.pop("_tenor_meta", {})
    iv_surface     = {k: iv_surface_raw[k] for k in ("iv7", "iv14", "iv30", "iv60", "iv90")}

    # Validate — pass tenor_meta for Rules 5 & 6
    status, warnings = validate_iv_surface(iv_surface, tenor_meta)

    # Per-tenor detail: ATM contracts with bid/ask spread info
    # Uses tenor_meta for expiration selection — no re-computation.
    tenor_details: list[dict] = []
    for key, dte_target in [
        ("iv7", 7), ("iv14", 14), ("iv30", 30), ("iv60", 60), ("iv90", 90)
    ]:
        m              = tenor_meta.get(key, {})
        within         = m.get("within_tolerance", True)
        dte_actual     = m.get("actual_dte")
        dte_diff       = m.get("dte_diff")
        exp_str        = m.get("expiration")

        detail: dict = {
            "tenor":            _TENOR_LABELS[key],
            "tenor_key":        key,
            "dte_target":       dte_target,
            "expiration":       exp_str if within else None,
            "dte_actual":       dte_actual,
            "dte_diff":         dte_diff,
            "within_tolerance": within,
            "avg_iv":           iv_surface.get(key),
            "call":             None,
            "put":              None,
        }

        # Only look up contract data when the expiration is within tolerance
        if within and exp_str:
            chosen_date = datetime.date.fromisoformat(exp_str)
            exp_slice   = df[df["_exp_date"] == chosen_date]

            for opt_type in ("call", "put"):
                leg = exp_slice[exp_slice["option_type"] == opt_type].dropna(subset=["strike"])
                if leg.empty:
                    continue
                idx_atm = (leg["strike"] - underlying_price).abs().idxmin()
                row     = leg.loc[idx_atm]

                iv_frac = _safe_float(row.get("implied_volatility"))
                if iv_frac is not None:
                    iv_pct = round(iv_frac * 100.0, 2) if iv_frac <= 5.0 else round(iv_frac, 2)
                else:
                    iv_pct = None

                bid    = _safe_float(row.get("bid"))
                ask    = _safe_float(row.get("ask"))
                mid    = _safe_float(row.get("mid"))
                spread = round(ask - bid, 4) if (bid is not None and ask is not None) else None
                spread_pct = None
                if spread is not None and mid and mid > 0:
                    spread_pct = round((spread / mid) * 100.0, 2)

                detail[opt_type] = {
                    "strike":         _safe_float(row.get("strike")),
                    "iv":             iv_pct,
                    "bid":            bid,
                    "ask":            ask,
                    "mid":            mid,
                    "spread":         spread,
                    "spread_pct":     spread_pct,
                    "spread_quality": spread_quality(bid, ask),
                    "open_interest":  _safe_int(row.get("open_interest")),
                    "volume":         _safe_int(row.get("volume")),
                }

        tenor_details.append(detail)

    return {
        "ticker":             ticker_up,
        "provider":           provider.name,
        "pricing_date":       pricing_date.isoformat(),
        "underlying_price":   underlying_price,
        "status":             status,
        "warnings":           warnings,
        "iv_surface":         iv_surface,
        "tenor_details":      tenor_details,
        "source_expirations": source_expirations,
        "n_expirations":      len(source_expirations),
    }


@router.get("/historical-iv/{ticker}")
def get_historical_iv(
    ticker: str,
    start: str | None = Query(None, description="Start date YYYY-MM-DD (default: 1 year ago)"),
    end:   str | None = Query(None, description="End date YYYY-MM-DD (default: today)"),
    days:  int | None = Query(None, ge=1, le=2520, description="Alternative to start/end: last N trading days"),
    require_real_iv: bool = Query(False, description="Filter to real-quality IV rows only (exclude synthetic_fallback and interpolated)"),
) -> dict:
    """
    Historical ATM IV surfaces stored in the Parquet IV store.

    Rows are tagged with ``iv_data_quality``:
      "real"               — from live Polygon options chain
      "synthetic_fallback" — generated from mock price model or RV×1.05
      "interpolated"       — linearly filled between real observations

    Use ``scripts/backfill_iv_surface.py`` to populate the store.

    Response includes a ``data_quality_summary`` with counts per quality tier
    and the full row-by-row history.
    """
    sym = ticker.upper()
    if sym not in settings.tickers:
        raise HTTPException(
            status_code=404,
            detail=f"{sym!r} not in supported tickers: {sorted(settings.tickers)}",
        )

    # Resolve date range
    end_date   = end   or datetime.date.today().isoformat()
    start_date = start or (
        datetime.date.today() - datetime.timedelta(days=int((days or 252) * 1.5))
    ).isoformat()

    df = _iv_store.get_history(sym, start_date, end_date)

    # Apply real-IV filter if requested
    real_iv_filter_summary: dict | None = None
    if require_real_iv and not df.empty:
        rows_before = len(df)
        df = df[df["iv_data_quality"] == "real"]
        rows_remaining = len(df)
        rows_excluded  = rows_before - rows_remaining
        pct_remaining  = round(rows_remaining / rows_before, 4) if rows_before > 0 else 0.0
        real_iv_filter_summary = {
            "enabled":            True,
            "rows_before_filter": rows_before,
            "rows_excluded":      rows_excluded,
            "rows_remaining":     rows_remaining,
            "pct_remaining":      pct_remaining,
        }
    elif not df.empty:
        real_iv_filter_summary = {
            "enabled":            False,
            "rows_before_filter": len(df),
            "rows_excluded":      0,
            "rows_remaining":     len(df),
        }

    if df.empty:
        return {
            "ticker":                sym,
            "rows":                  [],
            "data_quality_summary":  {"real": 0, "synthetic_fallback": 0, "interpolated": 0, "total": 0},
            "date_range":            {"start": start_date, "end": end_date},
            "n_rows":                0,
            "store_populated":       False,
            "real_iv_filter":        real_iv_filter_summary or {"enabled": require_real_iv},
            "message":               (
                "No historical IV data found. "
                "Run scripts/backfill_iv_surface.py to populate the store."
            ),
        }

    # Serialize rows — replace NaN with None for JSON
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "date":            row["date"],
            "ticker":          row["ticker"],
            "iv7":             _safe_float(row.get("iv7")),
            "iv14":            _safe_float(row.get("iv14")),
            "iv30":            _safe_float(row.get("iv30")),
            "iv60":            _safe_float(row.get("iv60")),
            "iv90":            _safe_float(row.get("iv90")),
            "source":          row.get("source", "synthetic"),
            "iv_data_quality": row.get("iv_data_quality", "synthetic_fallback"),
        })

    # Quality summary
    quality_counts: dict[str, int] = {"real": 0, "synthetic_fallback": 0, "interpolated": 0}
    for r in rows:
        q = r["iv_data_quality"]
        quality_counts[q] = quality_counts.get(q, 0) + 1

    return {
        "ticker": sym,
        "rows":   rows,
        "data_quality_summary": {
            **quality_counts,
            "total": len(rows),
        },
        "date_range": {
            "start": df["date"].min(),
            "end":   df["date"].max(),
        },
        "n_rows":          len(rows),
        "store_populated": True,
        "real_iv_filter":  real_iv_filter_summary or {"enabled": False},
    }


# ---------------------------------------------------------------------------
# Historical IV quality diagnostics  GET /api/data/historical-iv-quality/{ticker}
# ---------------------------------------------------------------------------

_REAL_SOURCES_SET = {
    "polygon_live_snapshot",
    "polygon_historical_options",
    "orats_historical",
    "optionmetrics_historical",
    "live_options_chain",  # legacy
}


@router.get("/historical-iv-quality/{ticker}")
def get_historical_iv_quality(
    ticker: str,
    start: str | None = Query(None, description="Start date YYYY-MM-DD (default: 1 year ago)"),
    end:   str | None = Query(None, description="End date YYYY-MM-DD (default: today)"),
    days:  int | None = Query(None, ge=1, le=2520, description="Trailing trading days"),
) -> dict:
    """
    IV surface quality diagnostics for a single ticker.

    Reports the percentage of trading days with:
      - valid IV30 (required for core signal)
      - full surface (all 5 tenors: iv7, iv14, iv30, iv60, iv90)
      - health breakdown by source label

    Designed to be used after running ``scripts/backfill_real_iv_surface.py``
    to confirm how much real historical IV data is stored.

    Response shape
    --------------
    {
        "ticker":         "SPY",
        "date_range":     { "start": "2024-01-01", "end": "2025-01-01" },
        "n_stored":       252,
        "n_trading_days": 252,
        "coverage_pct":   1.0,
        "iv30_available_pct":    0.97,
        "full_surface_pct":      0.61,
        "source_breakdown": { "polygon_historical_options": 240, "synthetic_fallback": 12 },
        "quality_breakdown": { "real": 240, "synthetic_fallback": 12, "interpolated": 0 },
        "missing_iv30_dates":   ["2024-06-21", ...],
        "incomplete_surface_dates": ["2024-06-21", ...],
        "store_populated": true
    }
    """
    sym = ticker.upper()
    if sym not in settings.tickers:
        raise HTTPException(
            status_code=404,
            detail=f"{sym!r} not in supported tickers: {sorted(settings.tickers)}",
        )

    today      = datetime.date.today()
    end_date   = datetime.date.fromisoformat(end) if end else today
    start_date = (
        datetime.date.fromisoformat(start) if start
        else today - datetime.timedelta(days=int((days or 252) * 1.5))
    )

    df = _iv_store.get_history(sym, start_date.isoformat(), end_date.isoformat())

    # Trading-day calendar (Mon–Fri proxy) for coverage denominator
    def _weekdays(s: datetime.date, e: datetime.date) -> list[str]:
        out, cur = [], s
        while cur <= e:
            if cur.weekday() < 5:
                out.append(cur.isoformat())
            cur += datetime.timedelta(days=1)
        return out

    trading_days   = _weekdays(start_date, end_date)
    n_trading_days = len(trading_days)

    if df.empty:
        return {
            "ticker":                   sym,
            "date_range":               {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "n_stored":                 0,
            "n_trading_days":           n_trading_days,
            "coverage_pct":             0.0,
            "iv30_available_pct":       0.0,
            "full_surface_pct":         0.0,
            "source_breakdown":         {},
            "quality_breakdown":        {"real": 0, "synthetic_fallback": 0, "interpolated": 0},
            "missing_iv30_dates":       [],
            "incomplete_surface_dates": [],
            "store_populated":          False,
            "message": (
                "No historical IV data found. "
                "Run scripts/backfill_real_iv_surface.py to populate the store."
            ),
        }

    n_stored      = len(df)
    coverage_pct  = n_stored / n_trading_days if n_trading_days > 0 else 0.0

    # IV30 availability: row has a non-NaN iv30 value
    iv30_ok_mask  = df["iv30"].notna()
    n_iv30_ok     = int(iv30_ok_mask.sum())
    iv30_pct      = n_iv30_ok / n_trading_days if n_trading_days > 0 else 0.0

    # Full surface: all 5 tenors present and non-NaN
    full_mask = (
        df["iv7"].notna()
        & df["iv14"].notna()
        & df["iv30"].notna()
        & df["iv60"].notna()
        & df["iv90"].notna()
    )
    n_full    = int(full_mask.sum())
    full_pct  = n_full / n_trading_days if n_trading_days > 0 else 0.0

    # Source breakdown (counts per source label)
    source_breakdown: dict[str, int] = df["source"].value_counts().to_dict()

    # Quality breakdown (real / interpolated / synthetic_fallback)
    quality_breakdown: dict[str, int] = {"real": 0, "synthetic_fallback": 0, "interpolated": 0}
    for _, cnt in df["iv_data_quality"].value_counts().items():
        pass
    quality_breakdown = df["iv_data_quality"].value_counts().to_dict()
    quality_breakdown.setdefault("real", 0)
    quality_breakdown.setdefault("synthetic_fallback", 0)
    quality_breakdown.setdefault("interpolated", 0)

    # Dates where IV30 is missing (stored but NaN) or not stored at all
    stored_date_set   = set(df["date"].tolist())
    missing_iv30_rows = df[~iv30_ok_mask]["date"].tolist()
    missing_from_store = [d for d in trading_days if d not in stored_date_set]
    missing_iv30_dates = sorted(set(missing_iv30_rows) | set(missing_from_store))

    # Dates stored but without a full surface
    incomplete_surface_dates = sorted(df[~full_mask]["date"].tolist())

    return {
        "ticker":     sym,
        "date_range": {
            "start": start_date.isoformat(),
            "end":   end_date.isoformat(),
        },
        "n_stored":                 n_stored,
        "n_trading_days":           n_trading_days,
        "coverage_pct":             round(coverage_pct, 4),
        "iv30_available_pct":       round(iv30_pct, 4),
        "full_surface_pct":         round(full_pct, 4),
        "source_breakdown":         source_breakdown,
        "quality_breakdown":        quality_breakdown,
        "missing_iv30_dates":       missing_iv30_dates[:100],   # cap at 100 for response size
        "incomplete_surface_dates": incomplete_surface_dates[:100],
        "store_populated":          True,
    }
