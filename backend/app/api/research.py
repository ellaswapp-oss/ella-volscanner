"""
api/research.py
---------------
Research readiness endpoints.

Routes
------
GET /api/research/readiness
    Per-ticker and universe-level real-IV coverage report showing how close
    each ticker and the overall universe is to the data thresholds required
    for reliable backtesting and optimisation.
"""

from __future__ import annotations

import datetime
import math

import pandas as pd
from fastapi import APIRouter

from app.core.config import settings
from app.data.iv_store import store as _iv_store

router = APIRouter(prefix="/api/research", tags=["research"])

# ---------------------------------------------------------------------------
# Milestone thresholds (trading days of real IV30 data)
# ---------------------------------------------------------------------------

_WARMUP  = 63    # minimum bars for a meaningful backtest (lookback // 4)
_USABLE  = 100   # solid for single-signal backtests
_STRONG  = 252   # one full trading year — required for walk-forward / optimisation
_IDEAL   = 504   # two years — ideal for walk-forward

# ---------------------------------------------------------------------------
# Universe definitions
# ---------------------------------------------------------------------------

_ETF_TICKERS    = list(settings.index_tickers)          # ["SPY", "QQQ", "IWM"]
_SINGLE_TICKERS = [t for t in settings.tickers if t not in _ETF_TICKERS]  # ["AAPL", "NVDA", "TSLA"]
_ALL_TICKERS    = list(settings.tickers)                # all 6


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _readiness_level(n: int) -> str:
    """Map a valid-iv30-row count to a readiness label."""
    if n >= _STRONG:
        return "STRONG"
    if n >= _USABLE:
        return "USABLE"
    if n >= _WARMUP:
        return "EARLY"
    return "NOT_READY"


def _cal_days(trading_days: int) -> int:
    """Rough conversion: 5 trading days ≈ 7 calendar days."""
    return math.ceil(trading_days * 7 / 5)


def _ticker_readiness(ticker: str) -> dict:
    """
    Compute all readiness metrics for a single ticker.

    Uses iv_store.get_history() which already derives iv_data_quality from
    the source column, so no duplicate quality-mapping logic is needed here.
    """
    df = _iv_store.get_history(ticker)          # includes iv_data_quality col

    if df.empty:
        return _empty_ticker(ticker)

    real_df  = df[df["iv_data_quality"] == "real"]
    valid_df = real_df[real_df["iv30"].notna()] if not real_df.empty else real_df

    real_rows       = int(len(real_df))
    valid_iv30_rows = int(len(valid_df))

    first_real_date  = str(valid_df["date"].min()) if not valid_df.empty else None
    latest_real_date = str(valid_df["date"].max()) if not valid_df.empty else None

    # Trading-day span of the real-IV window
    if first_real_date and latest_real_date:
        usable_trading_days = int(len(pd.bdate_range(first_real_date, latest_real_date)))
    else:
        usable_trading_days = 0

    days_63  = max(0, _WARMUP  - valid_iv30_rows)
    days_100 = max(0, _USABLE  - valid_iv30_rows)
    days_252 = max(0, _STRONG  - valid_iv30_rows)

    return {
        "ticker":                   ticker,
        "real_rows":                real_rows,
        "valid_iv30_rows":          valid_iv30_rows,
        "first_real_date":          first_real_date,
        "latest_real_date":         latest_real_date,
        "usable_trading_days":      usable_trading_days,
        "days_until_warmup_63":     days_63,
        "days_until_100":           days_100,
        "days_until_252":           days_252,
        "cal_days_until_warmup_63": _cal_days(days_63),
        "cal_days_until_100":       _cal_days(days_100),
        "cal_days_until_252":       _cal_days(days_252),
        "readiness":                _readiness_level(valid_iv30_rows),
    }


def _empty_ticker(ticker: str) -> dict:
    return {
        "ticker":                   ticker,
        "real_rows":                0,
        "valid_iv30_rows":          0,
        "first_real_date":          None,
        "latest_real_date":         None,
        "usable_trading_days":      0,
        "days_until_warmup_63":     _WARMUP,
        "days_until_100":           _USABLE,
        "days_until_252":           _STRONG,
        "cal_days_until_warmup_63": _cal_days(_WARMUP),
        "cal_days_until_100":       _cal_days(_USABLE),
        "cal_days_until_252":       _cal_days(_STRONG),
        "readiness":                "NOT_READY",
    }


def _universe_summary(tickers: list[str], label: str, ticker_map: dict) -> dict:
    """Collapse per-ticker results to a universe-level summary."""
    rows = [ticker_map[t]["valid_iv30_rows"] for t in tickers]
    min_rows = min(rows) if rows else 0
    return {
        "label":               label,
        "tickers":             tickers,
        "min_valid_iv30_rows": min_rows,
        "readiness":           _readiness_level(min_rows),
    }


def _module_status(rows_current: int, rows_required: int) -> str:
    """
    AVAILABLE — rows_current >= rows_required
    PARTIAL   — at least 1 row but below threshold (runs with caveats)
    NOT_READY — no real rows yet
    """
    if rows_current >= rows_required:
        return "AVAILABLE"
    if rows_current >= 1:
        return "PARTIAL"
    return "NOT_READY"


def _build_modules(ticker_map: dict) -> list[dict]:
    """
    Build the module trust table.

    Binding constraint per module:
      - IV/RV backtest / Grades / Macro sweep: ETF universe minimum
      - Walk-Forward / Optimisation:           ETF universe minimum
      - Live Dashboard / Diagnostics:          any ticker
    """
    etf_rows = min(ticker_map[t]["valid_iv30_rows"] for t in _ETF_TICKERS)
    any_rows = max(ticker_map[t]["valid_iv30_rows"] for t in _ALL_TICKERS)

    def _notes(rows_needed: int, rows_current: int, extra: str = "") -> str:
        if rows_needed == 0:
            return extra or "Available immediately."
        if rows_current >= rows_needed:
            return "Threshold met." + (f" {extra}" if extra else "")
        gap = rows_needed - rows_current
        note = f"{gap} more real-IV30 rows needed (~{_cal_days(gap)} calendar days at current rate)."
        return f"{note} {extra}".strip()

    return [
        {
            "name":          "Live Dashboard",
            "description":   "Real-time IV surface, regime score, trade signal",
            "universe":      "any",
            "rows_required": 0,
            "rows_current":  any_rows,
            "status":        "AVAILABLE",
            "notes":         "Always available — uses live options chain, no history needed.",
        },
        {
            "name":          "Real-IV Diagnostics",
            "description":   "IV quality progress, backfill coverage, surface health checks",
            "universe":      "any",
            "rows_required": 1,
            "rows_current":  any_rows,
            "status":        _module_status(any_rows, 1),
            "notes":         _notes(1, any_rows, "Needs ≥1 real IV row for any ticker."),
        },
        {
            "name":          "IV/RV Backtest",
            "description":   "5-variation sweep, macro filters, signal grade portfolios",
            "universe":      "etf",
            "rows_required": _WARMUP,
            "rows_current":  etf_rows,
            "status":        _module_status(etf_rows, _WARMUP),
            "notes":         _notes(_WARMUP, etf_rows,
                                    "Needs 63+ valid IV30 rows across SPY/QQQ/IWM."),
        },
        {
            "name":          "Score Optimisation",
            "description":   "59 049-point grid search for optimal signal-component weights",
            "universe":      "etf",
            "rows_required": _STRONG,
            "rows_current":  etf_rows,
            "status":        _module_status(etf_rows, _STRONG),
            "notes":         _notes(_STRONG, etf_rows,
                                    f"Needs 252+ rows minimum; {_IDEAL}+ recommended for stability."),
        },
        {
            "name":          "Walk-Forward Validation",
            "description":   "Rolling IS/OOS window optimisation with degradation analysis",
            "universe":      "etf",
            "rows_required": _STRONG,
            "rows_current":  etf_rows,
            "status":        _module_status(etf_rows, _STRONG),
            "notes":         _notes(_STRONG, etf_rows,
                                    "Needs 252+ rows across ETF universe."),
        },
    ]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/readiness")
def research_readiness() -> dict:
    """
    Real-IV research readiness report.

    Returns per-ticker coverage metrics and milestone countdowns, universe-level
    readiness summaries, and a module trust table showing which research modules
    are currently reliable given the available real-IV history.

    Readiness levels (based on valid IV30 row count)
    -------------------------------------------------
    NOT_READY  < 63   rows — below backtest warmup threshold
    EARLY      63–99  rows — minimal viable for simple backtests
    USABLE     100–251 rows — solid for single-signal analysis
    STRONG     252+  rows — full year, reliable for optimisation
    """
    as_of = datetime.date.today().isoformat()

    # ── Per-ticker ────────────────────────────────────────────────────────────
    ticker_results = {t: _ticker_readiness(t) for t in _ALL_TICKERS}

    # ── Universes ─────────────────────────────────────────────────────────────
    universes = {
        "etf": _universe_summary(
            _ETF_TICKERS, "ETF Universe (SPY / QQQ / IWM)", ticker_results
        ),
        "single_name": _universe_summary(
            _SINGLE_TICKERS, "Single-Name Universe (AAPL / NVDA / TSLA)", ticker_results
        ),
        "all": _universe_summary(
            _ALL_TICKERS, "Full Universe (all 6 tickers)", ticker_results
        ),
    }

    # ── Module trust table ────────────────────────────────────────────────────
    modules = _build_modules(ticker_results)

    # ── Milestone thresholds (for frontend reference) ─────────────────────────
    thresholds = {
        "warmup_63":  _WARMUP,
        "usable_100": _USABLE,
        "strong_252": _STRONG,
        "ideal_504":  _IDEAL,
    }

    return {
        "as_of":      as_of,
        "thresholds": thresholds,
        "tickers":    list(ticker_results.values()),
        "universes":  universes,
        "modules":    modules,
    }
