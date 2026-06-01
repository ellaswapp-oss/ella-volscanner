"""
data/_mock_impl.py
------------------
Concrete VolDataProvider backed entirely by deterministic synthetic data.

No network calls are made.  All series are seeded by ticker name (via MD5)
so results are reproducible across runs regardless of PYTHONHASHSEED.

This module is an implementation detail — import via ``registry.get_provider()``
or through the backward-compat facades in mock_provider.py / macro_provider.py.
"""

from __future__ import annotations

import datetime
import hashlib
import math

import numpy as np
import pandas as pd

from app.data.base_provider import VolDataProvider
from app.data.options_utils import OPTION_COLUMNS, bs_price_and_greeks

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TICKERS: list[str] = ["SPY", "QQQ", "IWM", "AAPL", "NVDA", "TSLA"]

_IV_SURFACE: dict[str, dict[str, float]] = {
    "SPY":  {"iv7": 13.5,  "iv30": 15.8,  "iv60": 16.9,  "iv90": 17.6},
    "QQQ":  {"iv7": 16.8,  "iv30": 19.4,  "iv60": 20.7,  "iv90": 21.5},
    "IWM":  {"iv7": 19.3,  "iv30": 22.6,  "iv60": 23.9,  "iv90": 24.8},
    "AAPL": {"iv7": 22.1,  "iv30": 26.4,  "iv60": 28.2,  "iv90": 29.5},
    "NVDA": {"iv7": 41.2,  "iv30": 47.5,  "iv60": 51.3,  "iv90": 53.8},
    "TSLA": {"iv7": 56.4,  "iv30": 64.2,  "iv60": 69.1,  "iv90": 72.6},
}

_BASE_PRICE: dict[str, float] = {
    "SPY":  562.0,
    "QQQ":  476.0,
    "IWM":  198.0,
    "AAPL": 213.0,
    "NVDA": 875.0,
    "TSLA": 172.0,
}

_DAILY_SIGMA: dict[str, float] = {
    t: _IV_SURFACE[t]["iv30"] / 100 / (252 ** 0.5)
    for t in TICKERS
}

_MACRO_SNAPSHOT: dict = {
    "vix":                  18.4,
    "vix_regime":           "Normal",
    "credit_spread":         2.15,
    "yield_curve":           0.32,
    "financial_conditions": -0.12,
}


def _md5_seed(key: str) -> int:
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % (2 ** 31)


# ---------------------------------------------------------------------------
# Options chain helpers (used by _build_synthetic_chain)
# ---------------------------------------------------------------------------

def _opt_strike_step(price: float) -> float:
    """Round strike increment appropriate for the price level."""
    if price < 25:    return 0.50
    if price < 100:   return 1.00
    if price < 250:   return 2.50
    if price < 500:   return 5.00
    if price < 1_000: return 10.00
    return 25.00


def _next_fridays(start_date: datetime.date, count: int) -> list[datetime.date]:
    """Return the next ``count`` Fridays strictly after ``start_date``."""
    days_ahead = (4 - start_date.weekday()) % 7  # 4 = Friday
    if days_ahead == 0:
        days_ahead = 7  # don't include today if it's already Friday
    first_friday = start_date + datetime.timedelta(days=days_ahead)
    return [first_friday + datetime.timedelta(weeks=i) for i in range(count)]


def _interp_iv_surface(surface: dict, dte: int) -> float:
    """
    Linearly interpolate IV (percent) from the surface dict for a given DTE.

    Surface knots: iv7 (7d), iv30 (30d), iv60 (60d), iv90 (90d).
    """
    tenors = [
        (7,  surface.get("iv7", surface["iv30"])),
        (30, surface["iv30"]),
        (60, surface["iv60"]),
        (90, surface["iv90"]),
    ]
    if dte <= tenors[0][0]:
        return tenors[0][1]
    if dte >= tenors[-1][0]:
        return tenors[-1][1]
    for i in range(len(tenors) - 1):
        t0, iv0 = tenors[i]
        t1, iv1 = tenors[i + 1]
        if t0 <= dte <= t1:
            frac = (dte - t0) / (t1 - t0)
            return iv0 + frac * (iv1 - iv0)
    return tenors[-1][1]


def _opt_skew(moneyness: float, opt_type: str) -> float:
    """
    Simple equity volatility skew multiplier.

    Equity options exhibit a "smirk": OTM puts carry higher IV than equidistant
    OTM calls.  moneyness = strike/S - 1 (negative for OTM puts).
    """
    if opt_type == "put":
        otm = max(0.0, -moneyness)      # positive when strike < S
        return 1.0 + 0.15 * otm
    else:                               # call
        otm = max(0.0, moneyness)       # positive when strike > S
        return 1.0 + 0.05 * otm


# ---------------------------------------------------------------------------
# MockDataProvider
# ---------------------------------------------------------------------------

class MockDataProvider(VolDataProvider):
    """
    Fully synthetic data provider — no network calls, completely deterministic.

    Suitable for development, CI, and backtesting without API credentials.
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "mock"

    @property
    def is_live(self) -> bool:
        return False

    # ------------------------------------------------------------------
    # Core abstract methods
    # ------------------------------------------------------------------

    def get_prices(
        self,
        ticker: str,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.Series:
        days = max(20, int(np.busday_count(start_date, end_date)) + 5)
        return self._synthetic_prices(ticker, days)

    def get_iv_history(
        self,
        ticker: str,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.DataFrame:
        days = max(20, int(np.busday_count(start_date, end_date)) + 5)
        return self._build_iv_df(ticker, days)

    def get_options_chain(
        self,
        ticker: str,
        date: datetime.date,
    ) -> pd.DataFrame:
        """
        Synthetic Black-Scholes options chain seeded by ticker and date.

        Generates contracts for the next 6 weekly expirations with ATM-centred
        strike grids (±12 strikes).  Bar relationships (high >= low, etc.) hold
        by construction.  Results are deterministic for a given (ticker, date).
        """
        return self._build_synthetic_chain(ticker, date)

    def get_vix_curve(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.DataFrame:
        """Mock: VIX curve derived from the macro series vix1m/vix3m columns."""
        days = max(20, int(np.busday_count(start_date, end_date)) + 5)
        macro = self.get_macro_series_days(days)
        return macro[["vix1m", "vix3m"]].rename(columns={"vix1m": "vix1m", "vix3m": "vix3m"})

    def get_macro_series(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.DataFrame:
        days = max(20, int(np.busday_count(start_date, end_date)) + 5)
        return self.get_macro_series_days(days)

    def get_macro_snapshot(self) -> dict:
        return dict(_MACRO_SNAPSHOT)

    # ------------------------------------------------------------------
    # Days-based overrides (more efficient than the base-class round-trip)
    # ------------------------------------------------------------------

    def get_prices_days(self, ticker: str, days: int) -> pd.Series:
        return self._synthetic_prices(ticker, days)

    def get_iv_history_days(self, ticker: str, days: int) -> pd.DataFrame:
        return self._build_iv_df(ticker, days)

    def get_macro_series_days(self, days: int) -> pd.DataFrame:
        return self._build_macro_df(days)

    def get_ohlcv(
        self,
        ticker: str,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.DataFrame:
        days = max(20, int(np.busday_count(start_date, end_date)) + 5)
        return self._build_ohlcv_df(ticker, days)

    def get_ohlcv_days(self, ticker: str, days: int) -> pd.DataFrame:
        return self._build_ohlcv_df(ticker, days)

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, dict]:
        return {
            "prices": {
                "implemented": True,
                "source":      "Synthetic GBM (seeded by ticker)",
                "status":      "ok",
            },
            "iv_history": {
                "implemented": True,
                "source":      "Synthetic OU process (seeded by ticker)",
                "status":      "ok",
            },
            "options_chain": {
                "implemented": True,
                "source":      "Synthetic Black-Scholes chain (seeded by ticker+date)",
                "status":      "ok",
            },
            "vix_curve": {
                "implemented": True,
                "source":      "Derived from macro_series vix1m/vix3m",
                "status":      "ok",
            },
            "macro_series": {
                "implemented": True,
                "source":      "Synthetic OU/AR processes (deterministic)",
                "status":      "ok",
            },
            "macro_snapshot": {
                "implemented": True,
                "source":      "Hardcoded snapshot",
                "status":      "ok",
            },
        }

    # ------------------------------------------------------------------
    # Internal generators
    # ------------------------------------------------------------------

    def _synthetic_prices(self, ticker: str, days: int) -> pd.Series:
        """Geometric Brownian Motion price path, seeded by ticker name."""
        base  = _BASE_PRICE.get(ticker, 100.0)
        sigma = _DAILY_SIGMA.get(ticker, 0.012)
        drift = 0.00025

        rng     = np.random.default_rng(seed=_md5_seed(ticker))
        returns = rng.normal(drift, sigma, days)
        prices  = base * np.exp(np.cumsum(returns))

        idx = pd.bdate_range(end=datetime.date.today(), periods=days)
        return pd.Series(prices, index=idx, name=ticker)

    def _build_iv_df(self, ticker: str, days: int) -> pd.DataFrame:
        """Mean-reverting IV surface history, current value pinned to snapshot."""
        surface      = _IV_SURFACE.get(ticker, _IV_SURFACE["SPY"])
        iv30_current = surface["iv30"]

        ratio_iv7  = surface["iv7"]  / iv30_current
        ratio_iv60 = surface["iv60"] / iv30_current
        ratio_iv90 = surface["iv90"] / iv30_current

        rng = np.random.default_rng(seed=_md5_seed(ticker))
        iv30_history = self._ou_series(
            n=days,
            current=iv30_current,
            long_mean=iv30_current * 0.9,
            kappa=0.05,
            sigma=iv30_current * 0.018,
            rng=rng,
        )

        idx = pd.bdate_range(end=datetime.date.today(), periods=len(iv30_history))
        return pd.DataFrame({
            "iv7":  iv30_history * ratio_iv7,
            "iv30": iv30_history,
            "iv60": iv30_history * ratio_iv60,
            "iv90": iv30_history * ratio_iv90,
        }, index=idx)

    def _build_macro_df(self, days: int) -> pd.DataFrame:
        """
        Macro signal time series aligned to the SPY trading calendar.

        Columns: vix1m, vix3m, breadth, us10y, crude_oil, spy_price
        """
        spy_prices = self._synthetic_prices("SPY", days=days)
        spy_iv     = self._build_iv_df("SPY", days=days)
        idx        = spy_prices.index.intersection(spy_iv.index)
        spy_prices = spy_prices.loc[idx]
        n          = len(idx)

        # VIX1M — independent OU with spike events
        rng_vix = np.random.default_rng(_md5_seed("vix1m_ou_spikes"))
        vix1m   = np.zeros(n)
        vix1m[0] = 18.0
        for i in range(1, n):
            kappa  = 0.12 if vix1m[i - 1] > 30 else 0.04
            revert = kappa * (18.0 - vix1m[i - 1])
            shock  = rng_vix.normal(0, 1.4)
            if rng_vix.random() < 0.03:
                shock += rng_vix.uniform(5.0, 15.0)
            vix1m[i] = vix1m[i - 1] + revert + shock
        vix1m = np.clip(vix1m, 10.0, 80.0)

        # VIX3M — AR(1) spread above VIX1M; inverts when front-month spikes
        rng_ts  = np.random.default_rng(_md5_seed("vix3m_spread_ar1"))
        _MU     = 2.5
        spread  = np.zeros(n)
        spread[0] = _MU
        for i in range(1, n):
            spread[i] = _MU + 0.88 * (spread[i - 1] - _MU) + rng_ts.normal(0, 0.55)
            if vix1m[i] > 28:
                spread[i] -= rng_ts.uniform(2.0, 6.5)
        vix3m = np.clip(vix1m + spread, 8.0, 90.0)

        # Breadth — % SPX above 50-DMA, correlated with SPY 21-day return
        rng_br  = np.random.default_rng(_md5_seed("breadth_pct_above_50dma"))
        spy_log = np.log(spy_prices / spy_prices.shift(1)).fillna(0).values
        spy_r21 = pd.Series(spy_log).rolling(21).sum().fillna(0).values
        b_tgt   = np.clip(60.0 + spy_r21 * 200.0, 18.0, 88.0)
        breadth = np.zeros(n)
        breadth[0] = 60.0
        for i in range(1, n):
            breadth[i] = (
                0.90 * breadth[i - 1]
                + 0.10 * b_tgt[i]
                + rng_br.normal(0, 2.2)
            )
        breadth = np.clip(breadth, 12.0, 93.0)

        # US 10Y yield — mean-reverting around 4.25%
        rng_y  = np.random.default_rng(_md5_seed("us10y_yield_walk"))
        us10y  = np.zeros(n)
        us10y[0] = 4.25
        for i in range(1, n):
            us10y[i] = (
                us10y[i - 1]
                + 0.002 * (4.25 - us10y[i - 1])
                + rng_y.normal(0, 0.032)
            )
        us10y = np.clip(us10y, 1.5, 7.5)

        # WTI crude — mean-reverting around $75 with rare ±4% shocks
        rng_c  = np.random.default_rng(_md5_seed("crude_wti_walk_spikes"))
        crude  = np.zeros(n)
        crude[0] = 75.0
        for i in range(1, n):
            revert = 0.003 * (75.0 - crude[i - 1])
            shock  = rng_c.normal(0, 0.85)
            if rng_c.random() < 0.015:
                shock += crude[i - 1] * rng_c.choice([-0.04, 0.04])
            crude[i] = crude[i - 1] + revert + shock
        crude = np.clip(crude, 32.0, 140.0)

        # HY OAS credit spread — OU around 3.5%, widens when VIX is elevated
        rng_cs   = np.random.default_rng(_md5_seed("credit_spread_hy_oas"))
        cs       = np.zeros(n)
        cs[0]    = 3.5
        for i in range(1, n):
            revert = 0.025 * (3.5 - cs[i - 1])
            shock  = rng_cs.normal(0, 0.06)
            if vix1m[i] > 25:       # spread widens in high-vol regimes
                shock += rng_cs.uniform(0.02, 0.20)
            cs[i] = cs[i - 1] + revert + shock
        credit_spread = np.clip(cs, 1.0, 15.0)

        return pd.DataFrame({
            "vix1m":         vix1m,
            "vix3m":         vix3m,
            "breadth":       breadth,
            "us10y":         us10y,
            "crude_oil":     crude,
            "credit_spread": credit_spread,
            "spy_price":     spy_prices.values,
        }, index=idx)

    def _build_ohlcv_df(self, ticker: str, days: int) -> pd.DataFrame:
        """
        Synthetic OHLCV bars.

        Close prices come from GBM (same seed as ``_synthetic_prices``).
        Open, high, low, and volume are derived from close with consistent
        intraday noise so bar relationships (high >= max(open,close), etc.)
        always hold.
        """
        closes  = self._synthetic_prices(ticker, days)
        sigma   = _DAILY_SIGMA.get(ticker, 0.012)
        rng     = np.random.default_rng(seed=_md5_seed(f"{ticker}_ohlcv"))

        n       = len(closes)
        c_arr   = closes.values

        # Open: previous close ± small overnight gap
        gap     = rng.normal(0, sigma * 0.4, n)
        o_arr   = np.empty(n)
        o_arr[0] = c_arr[0] * (1 + gap[0])
        o_arr[1:] = c_arr[:-1] * (1 + gap[1:])

        # Intraday range: symmetric noise around the open-close midpoint
        intra   = np.abs(rng.normal(0, sigma * 1.5, n)) * closes.values
        mid     = (o_arr + c_arr) / 2
        h_arr   = np.maximum(o_arr, c_arr) + intra * 0.6
        l_arr   = np.minimum(o_arr, c_arr) - intra * 0.4

        # Typical volume: base_vol × lognormal noise
        base_vol: dict[str, float] = {
            "SPY": 75_000_000, "QQQ": 50_000_000, "IWM": 35_000_000,
            "AAPL": 60_000_000, "NVDA": 45_000_000, "TSLA": 80_000_000,
        }
        bv   = base_vol.get(ticker, 20_000_000)
        vol  = (bv * rng.lognormal(0, 0.3, n)).astype(int)

        return pd.DataFrame({
            "open":   o_arr,
            "high":   h_arr,
            "low":    l_arr,
            "close":  c_arr,
            "volume": vol,
        }, index=closes.index)

    def _build_synthetic_chain(
        self,
        ticker: str,
        date: datetime.date,
    ) -> pd.DataFrame:
        """
        Build a Black-Scholes synthetic options chain.

        Strategy
        --------
        * Underlying spot  : ``_BASE_PRICE[ticker]``
        * IV surface       : ``_IV_SURFACE[ticker]``  (interpolated by DTE)
        * Expirations      : next 6 Fridays from ``date``
        * Strikes          : ATM ± 12 steps (step size based on price level)
        * Skew             : simple equity skew (OTM puts elevated)
        * Volume / OI      : deterministic via per-contract MD5 seed
        """
        surface = _IV_SURFACE.get(ticker, _IV_SURFACE["SPY"])
        S       = _BASE_PRICE.get(ticker, 100.0)
        r       = 0.05  # risk-free rate (approx)

        step    = _opt_strike_step(S)
        atm     = round(S / step) * step
        strikes = [atm + i * step for i in range(-12, 13)]

        expirations = _next_fridays(date, count=12)

        rows: list[dict] = []
        for exp in expirations:
            dte = (exp - date).days
            if dte <= 0:
                continue
            T          = dte / 365.0
            iv_pct     = _interp_iv_surface(surface, dte)
            sigma_base = iv_pct / 100.0

            for strike in strikes:
                for opt_type in ("call", "put"):
                    moneyness = strike / S - 1.0
                    skew      = _opt_skew(moneyness, opt_type)
                    sigma     = max(0.01, sigma_base * skew)

                    price, delta, gamma, theta, vega = bs_price_and_greeks(
                        S, strike, T, r, sigma, opt_type
                    )

                    if price < 0.02:
                        continue  # skip effectively worthless deep-OTM

                    # Bid-ask spread: wider for OTM / longer-dated / high-price
                    half_spread = max(0.03, price * 0.025 + max(0, 0.5 - abs(delta)) * price * 0.04)
                    bid  = round(max(0.01, price - half_spread), 2)
                    ask  = round(price + half_spread, 2)
                    mid  = round((bid + ask) / 2, 2)

                    # Deterministic volume / OI seeded by contract identity
                    rng_c = np.random.default_rng(
                        _md5_seed(f"{ticker}_{exp.isoformat()}_{strike}_{opt_type}")
                    )
                    oi  = int(rng_c.integers(50, 50_000))
                    vol = int(rng_c.integers(0, max(1, oi // 8)))
                    # Small noise on last price (deterministic)
                    last = round(max(0.01, price * float(1 + rng_c.uniform(-0.015, 0.015))), 2)

                    rows.append({
                        "underlying_ticker": ticker,
                        "expiration":        exp.isoformat(),
                        "strike":            float(strike),
                        "option_type":       opt_type,
                        "bid":               bid,
                        "ask":               ask,
                        "mid":               mid,
                        "last":              last,
                        "implied_volatility": round(sigma, 6),
                        "delta":             round(delta, 4),
                        "gamma":             round(gamma, 6),
                        "theta":             round(theta, 6),
                        "vega":              round(vega, 6),
                        "open_interest":     oi,
                        "volume":            vol,
                    })

        if not rows:
            return pd.DataFrame(columns=OPTION_COLUMNS)
        return pd.DataFrame(rows, columns=OPTION_COLUMNS)

    @staticmethod
    def _ou_series(
        n: int,
        current: float,
        long_mean: float,
        kappa: float,
        sigma: float,
        rng: np.random.Generator,
    ) -> np.ndarray:
        """Ornstein-Uhlenbeck process pinned to ``current`` at the final step."""
        path = np.empty(n)
        path[0] = long_mean
        for i in range(1, n):
            path[i] = (
                path[i - 1]
                + kappa * (long_mean - path[i - 1])
                + sigma * rng.standard_normal()
            )
        path += current - path[-1]
        return path.clip(min=1.0)
