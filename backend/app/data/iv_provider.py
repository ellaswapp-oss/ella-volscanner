"""
data/iv_provider.py
-------------------
Smart IV series provider: merges persisted historical IV surfaces with
synthetic mock data for dates not yet in the store.

Priority (per trading day)
--------------------------
1. Store row with source="live_options_chain" → iv_data_quality="real"
2. Store row with source="synthetic"          → iv_data_quality="synthetic_fallback"
3. Date not in store (generated on-the-fly)  → iv_data_quality="synthetic_fallback"

Usage
-----
    from app.data.iv_provider import get_iv_series, get_quality_summary

    # Drop-in replacement for the mock IV history used by backtesting
    iv_df = get_iv_series("SPY", days=400)
    # iv_df has columns: iv7, iv30, iv60, iv90, iv_data_quality
    # DatetimeIndex aligned to trading calendar

    quality = get_quality_summary("SPY", days=400)
    # {"real": 5, "synthetic_fallback": 395, "interpolated": 0, "total": 400}
"""

from __future__ import annotations

import datetime
import logging
import math

import numpy as np
import pandas as pd

from app.data.iv_store import store as _store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Primary API
# ---------------------------------------------------------------------------

def get_iv_series(ticker: str, days: int, *, require_real_iv: bool = False) -> pd.DataFrame:
    """
    Return IV history for *ticker* over the last *days* trading days.

    The DataFrame has the same column layout as the mock provider's
    ``get_iv_history_days()`` (iv7/iv30/iv60/iv90 — all annualised %) plus
    an extra ``iv_data_quality`` column that records the provenance of each row.

    Algorithm
    ---------
    1. Generate the full synthetic series from the mock provider (baseline).
    2. Load stored IV rows for the same date range.
    3. Overwrite synthetic rows with stored rows where a match exists.
    4. Tag each row with its final data quality.
    5. Truncate to last ``days`` rows.
    6. Optionally filter to real-IV-only rows (require_real_iv=True).

    The DatetimeIndex comes from the synthetic series (guaranteed to be a
    complete trading-day calendar).  Stored rows are aligned by date string.
    """
    # ── 1. Synthetic baseline ─────────────────────────────────────────────
    from app.data.registry import get_provider
    provider   = get_provider()
    synthetic  = _get_synthetic(provider, ticker, days)

    # ── 2. Load stored rows ───────────────────────────────────────────────
    if synthetic.empty:
        result = synthetic
        result.attrs["real_iv_filter"] = {
            "enabled": require_real_iv,
            "rows_before_filter": 0,
            "rows_excluded": 0,
            "rows_remaining": 0,
        }
        return result

    start_str = synthetic.index.min().strftime("%Y-%m-%d")
    end_str   = synthetic.index.max().strftime("%Y-%m-%d")
    stored    = _store.get_history(ticker, start_str, end_str)

    if stored.empty:
        # Nothing in store — all synthetic
        synthetic["iv_data_quality"] = "synthetic_fallback"
        result = synthetic
    else:
        # ── 3. Merge: overwrite synthetic with stored rows ────────────────────
        stored_indexed = (
            stored.set_index(pd.to_datetime(stored["date"]))
            .sort_index()
        )

        result = synthetic.copy()
        result["iv_data_quality"] = "synthetic_fallback"

        for dt, row in stored_indexed.iterrows():
            if dt in result.index:
                for col in ("iv7", "iv30", "iv60", "iv90"):
                    val = row.get(col)
                    if _is_valid(val):
                        result.at[dt, col] = float(val)
                # iv14 might not exist in the synthetic baseline columns
                if "iv14" not in result.columns:
                    result["iv14"] = float("nan")
                val14 = row.get("iv14")
                if _is_valid(val14):
                    result.at[dt, "iv14"] = float(val14)
                quality = row.get("iv_data_quality", "synthetic_fallback")
                result.at[dt, "iv_data_quality"] = quality

    # ── 4. Keep last ``days`` rows ────────────────────────────────────────
    result = result.iloc[-days:] if len(result) > days else result

    # ── 5. Apply real-IV filter and attach summary ─────────────────────────
    rows_before = len(result)
    if require_real_iv:
        mask = (result["iv_data_quality"] == "real") & result["iv30"].notna()
        result = result[mask]
        rows_remaining = len(result)
        rows_excluded  = rows_before - rows_remaining
        pct_remaining  = round(rows_remaining / rows_before, 4) if rows_before > 0 else 0.0
        usable_date_start = str(result.index.min().date()) if not result.empty else None
        usable_date_end   = str(result.index.max().date()) if not result.empty else None
        result.attrs["real_iv_filter"] = {
            "enabled":           True,
            "rows_before_filter": rows_before,
            "rows_excluded":      rows_excluded,
            "rows_remaining":     rows_remaining,
            "usable_date_start":  usable_date_start,
            "usable_date_end":    usable_date_end,
            "pct_remaining":      pct_remaining,
        }
    else:
        result.attrs["real_iv_filter"] = {
            "enabled":           False,
            "rows_before_filter": rows_before,
            "rows_excluded":      0,
            "rows_remaining":     rows_before,
        }

    return result


def get_quality_summary(ticker: str, days: int, *, require_real_iv: bool = False) -> dict:
    """
    Return data-quality counts for *ticker* over the last *days* trading days.

    Example
    -------
    {"real": 5, "synthetic_fallback": 395, "interpolated": 0, "total": 400}

    When require_real_iv=True, only rows with iv_data_quality=="real" are counted.
    """
    from app.data.registry import get_provider
    provider  = get_provider()
    synthetic = _get_synthetic(provider, ticker, days)

    if synthetic.empty:
        return {"real": 0, "synthetic_fallback": 0, "interpolated": 0, "total": 0}

    start_str = synthetic.index.min().strftime("%Y-%m-%d")
    end_str   = synthetic.index.max().strftime("%Y-%m-%d")
    summary = _store.quality_summary(ticker, start_str, end_str)

    if require_real_iv:
        real_count = summary.get("real", 0)
        return {"real": real_count, "synthetic_fallback": 0, "interpolated": 0, "total": real_count}

    return summary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_synthetic(provider, ticker: str, days: int) -> pd.DataFrame:
    """
    Get the synthetic IV series from the active provider.

    For MockDataProvider this is the OU-process IV model.
    For RealDataProvider this falls back to MockDataProvider (since
    real provider's get_iv_history raises NotImplementedError).
    """
    try:
        return provider.get_iv_history_days(ticker, days)
    except NotImplementedError:
        # Real provider doesn't have IV history yet — use mock
        from app.data._mock_impl import MockDataProvider
        return MockDataProvider().get_iv_history_days(ticker, days)
    except Exception as exc:
        logger.warning("Failed to get synthetic IV for %s: %s", ticker, exc)
        return pd.DataFrame()


def _is_valid(v) -> bool:
    """True when v is a finite float (not None, not NaN)."""
    if v is None:
        return False
    try:
        f = float(v)
        return math.isfinite(f)
    except (TypeError, ValueError):
        return False
