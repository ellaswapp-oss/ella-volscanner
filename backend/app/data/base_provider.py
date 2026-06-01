"""
data/base_provider.py
---------------------
Abstract base class that every data provider must implement.

All concrete providers (mock, real/Polygon+ORATS+FRED) expose the same
interface so backtesting engines and API handlers are completely agnostic
about the data source.  Switching from mock to live data is a one-line
change in the .env file.

Abstract methods (formal interface)
------------------------------------
    get_prices(ticker, start_date, end_date)   → pd.Series  (daily closes)
    get_iv_history(ticker, start_date, end_date) → pd.DataFrame (iv7/30/60/90)
    get_options_chain(ticker, date)            → pd.DataFrame (full chain)
    get_vix_curve(start_date, end_date)        → pd.DataFrame (VIX term structure)
    get_macro_series(start_date, end_date)     → pd.DataFrame (macro signals)
    get_macro_snapshot()                       → dict         (current-day snapshot)

Convenience helpers (non-abstract, use ``days`` instead of date range)
------------------------------------------------------------------------
    get_prices_days(ticker, days)
    get_iv_history_days(ticker, days)
    get_macro_series_days(days)
"""

from __future__ import annotations

import datetime
from abc import ABC, abstractmethod

import pandas as pd


def _start_from_days(days: int) -> datetime.date:
    """Convert a ``days`` count to a calendar start date (2× buffer for weekends/holidays)."""
    return datetime.date.today() - datetime.timedelta(days=int(days * 1.5) + 10)


class VolDataProvider(ABC):
    """
    Uniform data interface for the vol-dashboard backend.

    Concrete implementations:
        MockDataProvider  — fully synthetic, deterministic, no network calls
        RealDataProvider  — Polygon (prices), ORATS (IV), FRED (macro), CBOE (VIX)
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier: ``'mock'`` or ``'real'``."""

    @property
    @abstractmethod
    def is_live(self) -> bool:
        """True when the provider makes real network calls."""

    @property
    def label(self) -> str:
        """Human-readable display label."""
        return "Live Data" if self.is_live else "Mock Data"

    @property
    def uses_proxy_breadth(self) -> bool:
        """
        True when the ``breadth`` column in ``get_macro_series`` is a proxy
        (binary 0/100 based on SPY 50-DMA) rather than true SPX constituent
        breadth (% of members above their individual 50-DMA).
        """
        return False

    # ------------------------------------------------------------------
    # Core abstract methods
    # ------------------------------------------------------------------

    @abstractmethod
    def get_prices(
        self,
        ticker: str,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.Series:
        """
        Daily adjusted closing prices for ``ticker``.

        Returns
        -------
        pd.Series indexed by business-day DatetimeIndex, values in USD.
        """

    @abstractmethod
    def get_iv_history(
        self,
        ticker: str,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.DataFrame:
        """
        Daily IV surface history for ``ticker``.

        Returns
        -------
        pd.DataFrame with columns [iv7, iv30, iv60, iv90] (annualised %)
        indexed by business-day DatetimeIndex.
        """

    @abstractmethod
    def get_options_chain(
        self,
        ticker: str,
        date: datetime.date,
    ) -> pd.DataFrame:
        """
        Full options chain snapshot for ``ticker`` on ``date``.

        Returns
        -------
        pd.DataFrame with at minimum columns:
            strike, expiration, option_type, bid, ask, mid, iv, delta, gamma
        """

    @abstractmethod
    def get_vix_curve(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.DataFrame:
        """
        Daily VIX term-structure history.

        Returns
        -------
        pd.DataFrame with columns [vix1m, vix3m, vix6m] (CBOE VIX indices)
        indexed by business-day DatetimeIndex.
        """

    @abstractmethod
    def get_macro_series(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.DataFrame:
        """
        Daily macro signal history aligned to the SPY trading calendar.

        Returns
        -------
        pd.DataFrame with columns:
            vix1m          — front-month VIX (CBOE VIX close)
            vix3m          — 3-month VIX (CBOE VIX3M close)
            breadth        — % SPX members above 50-DMA  (0–100;
                             may be a binary proxy — see ``uses_proxy_breadth``)
            us10y          — US 10Y Treasury yield, % (FRED DGS10)
            crude_oil      — WTI crude price, USD/bbl (FRED DCOILWTICO)
            credit_spread  — HY OAS or BAA-Treasury spread, % (FRED BAMLH0A0HYM2)
            spy_price      — SPY closing price
        """

    @abstractmethod
    def get_macro_snapshot(self) -> dict:
        """
        Current-day macro snapshot (scalars, not a time series).

        Returns
        -------
        dict with keys: vix, vix_regime, credit_spread, yield_curve,
                        financial_conditions
        """

    # ------------------------------------------------------------------
    # OHLCV (non-abstract — default wraps get_prices; override for real data)
    # ------------------------------------------------------------------

    def get_ohlcv(
        self,
        ticker: str,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.DataFrame:
        """
        Daily OHLCV bars for ``ticker``.

        Default implementation returns only the close column (from
        ``get_prices``).  Override in concrete providers that can fetch
        full OHLCV data (e.g. RealDataProvider via Polygon).

        Returns
        -------
        pd.DataFrame with columns [open, high, low, close, volume]
        indexed by business-day DatetimeIndex.  Columns other than
        ``close`` may be NaN in the default implementation.
        """
        closes = self.get_prices(ticker, start_date, end_date)
        return pd.DataFrame({
            "open":   float("nan"),
            "high":   float("nan"),
            "low":    float("nan"),
            "close":  closes,
            "volume": float("nan"),
        })

    # ------------------------------------------------------------------
    # Days-based convenience helpers (non-abstract)
    # ------------------------------------------------------------------

    def get_prices_days(self, ticker: str, days: int) -> pd.Series:
        """Return the last ``days`` trading-day closes (ending today)."""
        start = _start_from_days(days)
        end   = datetime.date.today()
        series = self.get_prices(ticker, start, end)
        return series.iloc[-days:] if len(series) > days else series

    def get_iv_history_days(self, ticker: str, days: int) -> pd.DataFrame:
        """Return the last ``days`` rows of IV surface history."""
        start = _start_from_days(days)
        end   = datetime.date.today()
        df    = self.get_iv_history(ticker, start, end)
        return df.iloc[-days:] if len(df) > days else df

    def get_macro_series_days(self, days: int) -> pd.DataFrame:
        """Return the last ``days`` rows of macro signal history."""
        start = _start_from_days(days)
        end   = datetime.date.today()
        df    = self.get_macro_series(start, end)
        return df.iloc[-days:] if len(df) > days else df

    def get_ohlcv_days(self, ticker: str, days: int) -> pd.DataFrame:
        """Return the last ``days`` OHLCV rows (ending today)."""
        start = _start_from_days(days)
        end   = datetime.date.today()
        df    = self.get_ohlcv(ticker, start, end)
        return df.iloc[-days:] if len(df) > days else df

    # ------------------------------------------------------------------
    # Capabilities introspection
    # ------------------------------------------------------------------

    @abstractmethod
    def capabilities(self) -> dict[str, dict]:
        """
        Describe each data capability: whether it is implemented and what
        external source it draws from.

        Return shape (per entry)
        ------------------------
        {
            "implemented": bool,
            "source":      str | None,   # e.g. "Polygon /v2/aggs"
            "status":      str,          # "ok" | "needs_<ENV_VAR>"
        }
        """

    def provider_status(self) -> dict:
        """Serialisable dict for ``GET /api/data/provider-status``."""
        return {
            "provider":     self.name,
            "label":        self.label,
            "is_live":      self.is_live,
            "capabilities": self.capabilities(),
        }
