"""
data/macro_provider.py
----------------------
Backward-compatible facade — delegates to the active provider returned by
``registry.get_provider()``.

Existing imports across the codebase continue to work without change:

    from app.data.macro_provider import get_macro_series

When DATA_PROVIDER=real this will call the RealDataProvider instead,
making the macro filter sweep data-source agnostic with zero refactoring.

Do not add business logic here.  Put macro generation in the provider
classes (MockDataProvider / RealDataProvider).
"""

from __future__ import annotations

import pandas as pd

from app.data.registry import get_provider


def get_macro_series(days: int = 400) -> pd.DataFrame:
    """
    Macro signal time series for the last ``days`` trading days.

    Returns
    -------
    pd.DataFrame with columns:
        vix1m, vix3m, breadth, us10y, crude_oil, spy_price
    Indexed by business-day DatetimeIndex ending today.
    """
    return get_provider().get_macro_series_days(days)
