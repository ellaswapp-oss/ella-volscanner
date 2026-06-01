"""
data/mock_provider.py
---------------------
Backward-compatible facade — delegates every call to the active provider
returned by ``registry.get_provider()``.

All existing imports across the codebase continue to work without change:

    from app.data.mock_provider import TICKERS, get_price_history, get_iv_data, get_macro_data

When DATA_PROVIDER=real these functions will call the RealDataProvider instead,
making the entire backtesting engine data-source agnostic with zero refactoring
of the calling code.

IV data priority (get_iv_data)
------------------------------
1. Stored rows in IVSurfaceStore (source="live_options_chain") → quality="real"
2. Stored rows in IVSurfaceStore (source="synthetic")          → quality="synthetic_fallback"
3. Synthetic mock data for dates not yet in the store          → quality="synthetic_fallback"

The returned DataFrame has an extra ``iv_data_quality`` column that backtests
can use to report data provenance.  Existing code that ignores this column
continues to work unchanged.

Do not add business logic here.  Put new data functions in the provider classes
(MockDataProvider / RealDataProvider) and expose them via registry.get_provider().
"""

from __future__ import annotations

import pandas as pd

from app.data._mock_impl import TICKERS   # constant — always the canonical ticker list
from app.data.registry import get_provider


# ---------------------------------------------------------------------------
# Backward-compatible module-level functions
# ---------------------------------------------------------------------------

def get_price_history(ticker: str, days: int = 400) -> pd.Series:
    """Daily closing prices for ``ticker`` over the last ``days`` trading days."""
    return get_provider().get_prices_days(ticker, days)


def get_iv_data(ticker: str, days: int = 400, *, require_real_iv: bool = False) -> pd.DataFrame:
    """
    IV surface (iv7/30/60/90) for ``ticker`` over the last ``days`` trading days.

    Uses the IV store (iv_provider.get_iv_series) to prefer real options-chain
    data over synthetic mock data wherever available.  Falls back gracefully to
    pure synthetic when the store is empty or unavailable.

    The returned DataFrame includes an ``iv_data_quality`` column
    ("real" | "synthetic_fallback" | "interpolated") for downstream reporting.

    When require_real_iv=True, only rows where iv_data_quality=="real" and
    iv30 is not NaN are returned.  The filter summary is stored in
    df.attrs["real_iv_filter"].
    """
    try:
        from app.data.iv_provider import get_iv_series
        return get_iv_series(ticker, days, require_real_iv=require_real_iv)
    except Exception:
        # Absolute fallback — always return something for backtests
        return get_provider().get_iv_history_days(ticker, days)


def get_macro_data() -> dict:
    """Current-day macro snapshot (VIX, credit spread, yield curve, NFCI)."""
    return get_provider().get_macro_snapshot()


# ---------------------------------------------------------------------------
# IV data quality helper (for API responses)
# ---------------------------------------------------------------------------

def get_iv_quality_summary(ticker: str, days: int = 400) -> dict:
    """
    Return IV data quality counts for *ticker* over the last *days* trading days.

    Example
    -------
    {"real": 5, "synthetic_fallback": 395, "interpolated": 0, "total": 400}
    """
    try:
        from app.data.iv_provider import get_quality_summary
        return get_quality_summary(ticker, days)
    except Exception:
        return {"real": 0, "synthetic_fallback": days, "interpolated": 0, "total": days}
