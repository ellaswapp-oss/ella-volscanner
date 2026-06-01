"""
data/real_provider.py
---------------------
Live data provider — Polygon (prices/OHLCV/options), FRED (macro), CBOE (VIX).

Implemented
-----------
  prices / OHLCV  — Polygon /v2/aggs/ticker                  (POLYGON_API_KEY)
  options_chain   — Polygon /v3/snapshot/options             (POLYGON_API_KEY)
  macro_series    — FRED DGS10, DCOILWTICO, BAMLH0A0HYM2    (FRED_API_KEY)
                  + CBOE VIX/VIX3M CSV (no key)
                  + SPY breadth proxy (POLYGON_API_KEY)

Pending
-------
  iv_history      — ORATS /data/hist/cores                   (ORATS_API_KEY)
  vix_curve       — CBOE public CSV (no key required)
  macro_snapshot  — FRED latest + Polygon spot VIX

Disk cache
----------
  Prices  : backend/data_cache/prices/{TICKER}_{start}_{end}.json
  Macro   : backend/data_cache/macro/{SERIES}_{start}_{end}.json
  Options : backend/data_cache/options/{TICKER}_{date}.json

Delete a file to force a fresh fetch.  Historical data never changes, so
the cache TTL is effectively infinite.  Only delete when you suspect the
end-of-series (most-recent observations) are stale.

Dependencies
------------
  requests  (already in environment — no additional install needed)
  numpy, pandas (already in environment)

Environment variables
---------------------
  POLYGON_API_KEY  — required for prices, SPY breadth proxy, options chain
  FRED_API_KEY     — required for US10Y, crude, credit spread
                     Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html
"""

from __future__ import annotations

import datetime
import io
import json
import logging
import math
import os
import pathlib
import time

import numpy as np
import pandas as pd
import requests

from app.data.base_provider import VolDataProvider, _start_from_days
from app.data.options_utils import OPTION_COLUMNS, normalize_polygon_contract

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Polygon REST constants
# ---------------------------------------------------------------------------

_POLYGON_BASE    = "https://api.polygon.io"
_AGGS_ENDPOINT   = "/v2/aggs/ticker/{ticker}/range/1/day/{from_}/{to}"
_REQUEST_TIMEOUT = 15
_MAX_RETRIES     = 2
_RETRY_BACKOFF   = 1.0

# ---------------------------------------------------------------------------
# FRED REST constants
# ---------------------------------------------------------------------------

_FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"

# FRED series IDs used in get_macro_series
_FRED_SERIES = {
    "us10y":         "DGS10",          # 10-Year Treasury CMT, daily
    "crude_oil":     "DCOILWTICO",     # WTI crude, daily
    "credit_spread": "BAMLH0A0HYM2",  # ICE BofA HY OAS, daily
}

# ---------------------------------------------------------------------------
# CBOE VIX public CSV URLs (no API key required)
# ---------------------------------------------------------------------------

_CBOE_VIX_URLS = {
    "VIX":   "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
    "VIX3M": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv",
}

# ---------------------------------------------------------------------------
# Cache paths  (parents[2] = backend/)
# ---------------------------------------------------------------------------

_BACKEND_ROOT    = pathlib.Path(__file__).resolve().parents[2]
_PRICES_CACHE    = _BACKEND_ROOT / "data_cache" / "prices"
_MACRO_CACHE     = _BACKEND_ROOT / "data_cache" / "macro"
_OPTIONS_CACHE   = _BACKEND_ROOT / "data_cache" / "options"

# ---------------------------------------------------------------------------
# Polygon options constants
# ---------------------------------------------------------------------------

_OPTIONS_SNAPSHOT = "/v3/snapshot/options/{ticker}"
_MAX_OPTION_PAGES = 20   # 20 × 250 = 5 000 contracts max

# Calendar days to prepend when fetching SPY for the 50-DMA breadth proxy.
# ~50 trading days ≈ 75 calendar days; add 35 days buffer.
_BREADTH_LOOKBACK = 110


def _prices_cache_path(ticker: str, start: datetime.date, end: datetime.date) -> pathlib.Path:
    return _PRICES_CACHE / f"{ticker.upper()}_{start}_{end}.json"


def _macro_cache_path(series_id: str, start: datetime.date, end: datetime.date) -> pathlib.Path:
    return _MACRO_CACHE / f"{series_id}_{start}_{end}.json"


def _options_cache_path(ticker: str, date: datetime.date) -> pathlib.Path:
    return _OPTIONS_CACHE / f"{ticker.upper()}_{date}.json"


# ---------------------------------------------------------------------------
# RealDataProvider
# ---------------------------------------------------------------------------

class RealDataProvider(VolDataProvider):
    """
    Live data provider.

    Prices and OHLCV data come from Polygon.  Macro series (US10Y, crude,
    credit spread) come from FRED.  VIX and VIX3M come from CBOE public CSV
    files (no API key required).  Breadth is a binary proxy based on SPY's
    50-day moving average until true SPX constituent breadth is implemented.
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "real"

    @property
    def is_live(self) -> bool:
        return True

    @property
    def uses_proxy_breadth(self) -> bool:
        """
        True — breadth is currently SPY-close > SPY-50DMA → 100, else 0.
        True SPX constituent breadth (% members above 50-DMA) is pending
        until a suitable free data source is identified.
        """
        return True

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, dict]:
        poly  = bool(os.environ.get("POLYGON_API_KEY"))
        orats = bool(os.environ.get("ORATS_API_KEY"))
        fred  = bool(os.environ.get("FRED_API_KEY"))

        def _cap(configured: bool, implemented: bool, source: str, key: str | None) -> dict:
            if not implemented:
                status = "not_implemented"
            elif not configured:
                status = f"needs_{key}" if key else "ok"
            else:
                status = "ok"
            return {"implemented": implemented, "source": source,
                    "status": status, "configured": configured}

        return {
            "prices": _cap(
                poly, True,
                "Polygon /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}",
                "POLYGON_API_KEY",
            ),
            "macro_series": _cap(
                fred and poly, True,
                "FRED: DGS10, DCOILWTICO, BAMLH0A0HYM2  +  CBOE VIX/VIX3M CSV",
                "FRED_API_KEY (+ POLYGON_API_KEY for SPY calendar/breadth)",
            ),
            "iv_history": _cap(
                orats or poly, False,
                "ORATS /data/hist/cores OR Polygon options chains",
                "ORATS_API_KEY or POLYGON_API_KEY",
            ),
            "options_chain": _cap(
                poly, True,
                "Polygon /v3/snapshot/options/{underlyingAsset}  (paginated, up to 5 000 contracts)",
                "POLYGON_API_KEY",
            ),
            "vix_curve": _cap(
                True, False,
                "CBOE VIX_History.csv / VIX3M_History.csv (no key required)",
                None,
            ),
            "macro_snapshot": _cap(
                fred, False,
                "FRED latest observations + Polygon spot VIX",
                "FRED_API_KEY",
            ),
        }

    # ==================================================================
    # Prices — Polygon  (IMPLEMENTED)
    # ==================================================================

    def get_prices(
        self,
        ticker: str,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.Series:
        df = self._fetch_ohlcv(ticker, start_date, end_date)
        return df["close"].rename(ticker.upper())

    def get_ohlcv(
        self,
        ticker: str,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.DataFrame:
        return self._fetch_ohlcv(ticker, start_date, end_date)

    def get_ohlcv_days(self, ticker: str, days: int) -> pd.DataFrame:
        start = _start_from_days(days)
        end   = datetime.date.today()
        df    = self._fetch_ohlcv(ticker, start, end)
        return df.iloc[-days:] if len(df) > days else df

    # ==================================================================
    # Macro series — FRED + CBOE  (IMPLEMENTED)
    # ==================================================================

    def get_macro_series(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.DataFrame:
        """
        Fetch and align macro series to the SPY trading calendar.

        Data sources
        ------------
        vix1m        : CBOE VIX_History.csv        (no key)
        vix3m        : CBOE VIX3M_History.csv      (no key)
        us10y        : FRED DGS10                  (FRED_API_KEY)
        crude_oil    : FRED DCOILWTICO             (FRED_API_KEY)
        credit_spread: FRED BAMLH0A0HYM2           (FRED_API_KEY)
        breadth      : SPY close > SPY 50-DMA      (POLYGON_API_KEY; proxy)
        spy_price    : Polygon SPY closes           (POLYGON_API_KEY)

        Raises
        ------
        ValueError
            A required API key is not set or a series returned no data.
        RuntimeError
            A network or server error occurred.
        """
        fred_key = os.environ.get("FRED_API_KEY", "").strip()
        if not fred_key:
            raise ValueError(
                "FRED_API_KEY is not set. "
                "Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html "
                "and add it to backend/.env."
            )

        # ── 1. SPY prices (for calendar + breadth proxy) ──────────────
        spy_start = start_date - datetime.timedelta(days=_BREADTH_LOOKBACK)
        spy_full, spy_prices, spy_calendar = self._fetch_spy_for_macro(
            spy_start, start_date, end_date
        )

        # ── 2. VIX/VIX3M from CBOE ────────────────────────────────────
        vix1m = self._fetch_cboe_vix("VIX",   start_date, end_date)
        vix3m = self._fetch_cboe_vix("VIX3M", start_date, end_date)

        # ── 3. FRED series ────────────────────────────────────────────
        us10y         = self._fetch_fred_series("DGS10",         start_date, end_date, fred_key)
        crude_oil     = self._fetch_fred_series("DCOILWTICO",    start_date, end_date, fred_key)
        credit_spread = self._fetch_fred_series("BAMLH0A0HYM2", start_date, end_date, fred_key)

        # ── 4. Breadth proxy ──────────────────────────────────────────
        breadth = self._compute_breadth_proxy(spy_full, spy_calendar)

        # ── 5. Align all series to SPY calendar (forward-fill) ────────
        def align(s: pd.Series) -> pd.Series:
            return s.reindex(spy_calendar, method=None).ffill().bfill()

        return pd.DataFrame({
            "vix1m":         align(vix1m),
            "vix3m":         align(vix3m),
            "breadth":       breadth,
            "us10y":         align(us10y),
            "crude_oil":     align(crude_oil),
            "credit_spread": align(credit_spread),
            "spy_price":     spy_prices.reindex(spy_calendar).ffill(),
        }, index=spy_calendar)

    # ------------------------------------------------------------------
    # Macro helpers
    # ------------------------------------------------------------------

    def _fetch_spy_for_macro(
        self,
        spy_start: datetime.date,
        result_start: datetime.date,
        end_date: datetime.date,
    ) -> tuple[pd.Series, pd.Series, pd.DatetimeIndex]:
        """
        Fetch SPY close prices.

        Returns (spy_full, spy_prices, spy_calendar) where:
          spy_full     — full series from spy_start to end_date (for 50-DMA)
          spy_prices   — trimmed series from result_start to end_date
          spy_calendar — DatetimeIndex of trading days in result range
        """
        try:
            spy_full = self.get_prices("SPY", spy_start, end_date)
        except (ValueError, RuntimeError) as exc:
            raise ValueError(
                "SPY price history is required for the trading calendar and "
                f"breadth proxy but could not be fetched: {exc}"
            ) from exc

        spy_prices = spy_full.loc[pd.to_datetime(result_start):]
        return spy_full, spy_prices, spy_prices.index

    def _fetch_cboe_vix(
        self,
        symbol: str,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.Series:
        """
        Download VIX or VIX3M history from CBOE public CSV.

        CBOE CSV format
        ---------------
        DATE,OPEN,HIGH,LOW,CLOSE   (header; DATE is MM/DD/YYYY)

        No API key required.  Downloads the full history and filters to range.
        """
        url   = _CBOE_VIX_URLS.get(symbol)
        cache = _macro_cache_path(f"CBOE_{symbol}", start_date, end_date)

        if cache.exists():
            logger.debug("Cache hit: %s", cache)
            return self._load_series_cache(cache)

        if not url:
            raise ValueError(f"Unknown CBOE symbol: {symbol!r}")

        logger.debug("Fetching CBOE %s CSV from %s", symbol, url)
        try:
            resp = requests.get(url, timeout=_REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise RuntimeError(f"Network error fetching CBOE {symbol}: {exc}") from exc

        if not resp.ok:
            raise RuntimeError(
                f"CBOE returned HTTP {resp.status_code} for {symbol}. "
                f"Response: {resp.text[:200]}"
            )

        try:
            df = pd.read_csv(io.StringIO(resp.text))
            df.columns = [c.strip().upper() for c in df.columns]
            # CBOE uses MM/DD/YYYY; handle both with infer_datetime_format
            df["DATE"] = pd.to_datetime(df["DATE"], dayfirst=False)
            series = df.set_index("DATE")["CLOSE"].sort_index()
            series.name = symbol.lower()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to parse CBOE {symbol} CSV: {exc}"
            ) from exc

        series = series.loc[
            pd.to_datetime(start_date): pd.to_datetime(end_date)
        ]

        if series.empty:
            raise ValueError(
                f"CBOE {symbol} CSV has no data between {start_date} and {end_date}. "
                "The date range may be outside available CBOE history."
            )

        self._save_series_cache(series, cache)
        return series

    def _fetch_fred_series(
        self,
        series_id: str,
        start_date: datetime.date,
        end_date: datetime.date,
        api_key: str,
    ) -> pd.Series:
        """
        Fetch one FRED series via the JSON observations endpoint.

        FRED API
        --------
        GET https://api.stlouisfed.org/fred/series/observations
            ?series_id={id}&api_key={key}&file_type=json
            &observation_start={start}&observation_end={end}&sort_order=asc

        Missing values are represented as "." and are silently skipped.
        Forward-filling is performed later during calendar alignment.

        Raises
        ------
        ValueError  — bad series_id, API key rejected, or no non-missing data.
        RuntimeError — network error or unexpected server response.
        """
        cache = _macro_cache_path(series_id, start_date, end_date)
        if cache.exists():
            logger.debug("Cache hit: %s", cache)
            return self._load_series_cache(cache)

        params = {
            "series_id":         series_id,
            "api_key":           api_key,
            "file_type":         "json",
            "observation_start": start_date.isoformat(),
            "observation_end":   end_date.isoformat(),
            "sort_order":        "asc",
            "limit":             "100000",
        }

        last_exc: Exception | None = None
        resp = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = requests.get(_FRED_OBS_URL, params=params, timeout=_REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_BACKOFF * (attempt + 1))
                    continue
                raise RuntimeError(
                    f"Network error fetching FRED {series_id}: {exc}"
                ) from exc

            if resp.status_code == 400:
                raise ValueError(
                    f"FRED rejected request for series {series_id!r}. "
                    f"Check the series ID is valid. Response: {resp.text[:300]}"
                )
            if resp.status_code == 403:
                raise ValueError(
                    "FRED API rejected the request (HTTP 403). "
                    "Check that FRED_API_KEY is valid."
                )
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "60"))
                if attempt < _MAX_RETRIES:
                    logger.warning("FRED rate-limited; waiting %ds", wait)
                    time.sleep(min(wait, 60))
                    continue
                raise RuntimeError("FRED rate limit exceeded. Try again later.")
            if not resp.ok:
                raise RuntimeError(
                    f"FRED returned HTTP {resp.status_code} for {series_id}: "
                    f"{resp.text[:200]}"
                )

            break   # success

        observations = resp.json().get("observations", [])

        dates, values = [], []
        for obs in observations:
            raw = obs.get("value", ".")
            if raw == ".":          # FRED uses "." for missing values
                continue
            try:
                dates.append(pd.to_datetime(obs["date"]))
                values.append(float(raw))
            except (ValueError, KeyError):
                continue

        if not dates:
            raise ValueError(
                f"FRED returned no non-missing observations for series {series_id!r} "
                f"between {start_date} and {end_date}. "
                "The series may not cover this date range."
            )

        series = pd.Series(values, index=dates, name=series_id)
        self._save_series_cache(series, cache)
        return series

    @staticmethod
    def _compute_breadth_proxy(
        spy_full: pd.Series,
        calendar: pd.DatetimeIndex,
    ) -> pd.Series:
        """
        Binary breadth proxy: 100 when SPY close > 50-day SMA, else 0.

        This is a single-instrument proxy for the true SPX constituent
        breadth metric (% of members trading above their 50-DMA).
        It captures broad directional trend but misses dispersion within
        the index.  ``uses_proxy_breadth`` returns True to signal this.

        The first ~50 bars where the SMA hasn't warmed up are forward-filled
        from the first valid bar to avoid NaN gaps.
        """
        sma50  = spy_full.rolling(50, min_periods=1).mean()
        raw    = pd.Series(
            np.where(spy_full.values > sma50.values, 100.0, 0.0),
            index=spy_full.index,
            name="breadth",
        )
        return raw.reindex(calendar).ffill().bfill()

    # ==================================================================
    # Prices — shared OHLCV fetch + cache (Polygon)
    # ==================================================================

    def _fetch_ohlcv(
        self,
        ticker: str,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.DataFrame:
        """Fetch OHLCV bars from cache or Polygon API."""
        ticker = ticker.upper()
        cache  = _prices_cache_path(ticker, start_date, end_date)

        if cache.exists():
            logger.debug("Cache hit: %s", cache)
            return self._load_ohlcv_cache(cache)

        logger.debug("Cache miss — fetching from Polygon: %s %s→%s",
                     ticker, start_date, end_date)
        df = self._polygon_fetch(ticker, start_date, end_date)
        self._save_ohlcv_cache(df, cache)
        return df

    def _polygon_fetch(
        self,
        ticker: str,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> pd.DataFrame:
        api_key = os.environ.get("POLYGON_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "POLYGON_API_KEY is not set. "
                "Add it to backend/.env."
            )

        url = _POLYGON_BASE + _AGGS_ENDPOINT.format(
            ticker=ticker,
            from_=start_date.isoformat(),
            to=end_date.isoformat(),
        )
        params = {
            "adjusted": "true",
            "sort":     "asc",
            "limit":    "50000",
            "apiKey":   api_key,
        }

        resp = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_BACKOFF * (attempt + 1))
                    continue
                raise RuntimeError(
                    f"Network error fetching Polygon data for {ticker}: {exc}"
                ) from exc

            if resp.status_code == 403:
                raise ValueError(
                    "Polygon API rejected the request (HTTP 403). "
                    "Check that POLYGON_API_KEY is valid."
                )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "60"))
                if attempt < _MAX_RETRIES:
                    logger.warning("Polygon rate-limited; waiting %ds", retry_after)
                    time.sleep(min(retry_after, 60))
                    continue
                raise RuntimeError("Polygon rate limit exceeded.")
            if not resp.ok:
                raise RuntimeError(
                    f"Polygon returned HTTP {resp.status_code} for {ticker}. "
                    f"Response: {resp.text[:200]}"
                )
            break

        payload = resp.json()
        status  = payload.get("status", "")
        if status in ("NOT_FOUND", "ERROR"):
            raise ValueError(
                f"Polygon could not find data for ticker '{ticker}'. "
                f"Response status: {status!r}"
            )

        results = payload.get("results") or []
        if not results:
            raise ValueError(
                f"Polygon returned no price bars for '{ticker}' "
                f"between {start_date} and {end_date}."
            )

        return self._parse_polygon_results(results, ticker)

    @staticmethod
    def _parse_polygon_results(results: list[dict], ticker: str) -> pd.DataFrame:
        rows = []
        for bar in results:
            ts   = bar.get("t", 0)
            date = datetime.datetime.utcfromtimestamp(ts / 1000).date()
            rows.append({
                "date":   date.isoformat(),
                "open":   float(bar.get("o", float("nan"))),
                "high":   float(bar.get("h", float("nan"))),
                "low":    float(bar.get("l", float("nan"))),
                "close":  float(bar.get("c", float("nan"))),
                "volume": int(bar.get("v", 0)),
            })
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df.index.name = "date"
        return df

    # ==================================================================
    # Cache I/O — OHLCV
    # ==================================================================

    @staticmethod
    def _save_ohlcv_cache(df: pd.DataFrame, path: pathlib.Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [
            {
                "date":   ts.date().isoformat(),
                "open":   row["open"],
                "high":   row["high"],
                "low":    row["low"],
                "close":  row["close"],
                "volume": int(row["volume"]),
            }
            for ts, row in df.iterrows()
        ]
        path.write_text(json.dumps(records, indent=2))
        logger.debug("Cached %d OHLCV bars → %s", len(records), path)

    @staticmethod
    def _load_ohlcv_cache(path: pathlib.Path) -> pd.DataFrame:
        records = json.loads(path.read_text())
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df["volume"] = df["volume"].astype(int)
        return df

    # ==================================================================
    # Cache I/O — Series (FRED / CBOE)
    # ==================================================================

    @staticmethod
    def _save_series_cache(series: pd.Series, path: pathlib.Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [
            {"date": ts.date().isoformat(), "value": v}
            for ts, v in series.items()
            if not (isinstance(v, float) and math.isnan(v))
        ]
        payload = {"name": series.name, "records": records}
        path.write_text(json.dumps(payload, indent=2))
        logger.debug("Cached %d observations → %s", len(records), path)

    @staticmethod
    def _load_series_cache(path: pathlib.Path) -> pd.Series:
        data = json.loads(path.read_text())
        # Support both old format (bare list) and current format (dict with name)
        if isinstance(data, list):
            records, name = data, None
        else:
            records = data.get("records", [])
            name    = data.get("name")
        if not records:
            return pd.Series(dtype=float, name=name)
        dates  = pd.to_datetime([r["date"] for r in records])
        values = [r["value"] for r in records]
        return pd.Series(values, index=dates, dtype=float, name=name)

    # ==================================================================
    # Stub methods — not yet implemented
    # ==================================================================

    def get_iv_history(self, ticker, start_date, end_date):
        raise NotImplementedError(
            "IV history not yet implemented. "
            "Set ORATS_API_KEY (preferred) or POLYGON_API_KEY in backend/.env."
        )

    # ==================================================================
    # Options chain — Polygon snapshot  (IMPLEMENTED)
    # ==================================================================

    def get_options_chain(
        self,
        ticker: str,
        date: datetime.date,
    ) -> pd.DataFrame:
        """
        Fetch and normalize the full options chain for ``ticker``.

        Uses the Polygon ``/v3/snapshot/options/{ticker}`` endpoint.
        Paginates automatically via ``next_url`` (up to
        ``_MAX_OPTION_PAGES`` pages = 5 000 contracts).

        Parameters
        ----------
        ticker : str
            Underlying ticker symbol (case-insensitive).
        date : datetime.date
            Pricing date — used as the disk-cache key.  For today's date
            the snapshot is always "current".

        Returns
        -------
        pd.DataFrame
            Columns: ``OPTION_COLUMNS`` (15 fields).  IV is a decimal
            fraction (0.25 = 25 %).

        Raises
        ------
        ValueError
            ``POLYGON_API_KEY`` not set, HTTP 403/404, NOT_FOUND status.
        RuntimeError
            HTTP 5xx or network error.
        """
        ticker = ticker.upper()
        cache  = _options_cache_path(ticker, date)

        if cache.exists():
            logger.debug("Options cache hit: %s", cache)
            return self._load_options_cache(cache)

        raw_contracts = self._polygon_options_fetch(ticker)
        rows = [normalize_polygon_contract(c) for c in raw_contracts]
        df   = pd.DataFrame(rows, columns=OPTION_COLUMNS) if rows else pd.DataFrame(columns=OPTION_COLUMNS)

        _OPTIONS_CACHE.mkdir(parents=True, exist_ok=True)
        self._save_options_cache(df, cache)
        return df

    def get_atm_options_chain(
        self,
        ticker: str,
        spot_price: float,
        max_dte: int = 100,
        moneyness_band: float = 0.22,
    ) -> pd.DataFrame:
        """
        Fetch an ATM-centred options chain for IV surface computation.

        Unlike ``get_options_chain`` (which fetches up to 5 000 contracts
        sorted by strike ascending), this method filters the Polygon request
        to contracts within ``±moneyness_band`` of ``spot_price`` and
        expirations up to ``max_dte`` calendar days out.  The result is
        much smaller and always contains the near-ATM contracts needed for
        ``calculate_atm_iv_by_tenor``.

        **Not disk-cached** — always fetches live data.  Call once per
        ticker per day from the IV-surface capture script.

        Parameters
        ----------
        ticker         : underlying ticker, e.g. ``"QQQ"``
        spot_price     : current underlying price (used to set strike bounds)
        max_dte        : calendar days to look out (default 100 covers iv90)
        moneyness_band : fraction of spot to use as strike radius (default 0.22)

        Returns
        -------
        pd.DataFrame  with ``OPTION_COLUMNS``; IV as decimal fraction.
        """
        ticker = ticker.upper()
        today  = datetime.date.today()

        extra: dict = {
            "strike_price.gte": round(spot_price * (1 - moneyness_band), 2),
            "strike_price.lte": round(spot_price * (1 + moneyness_band), 2),
            "expiration_date.gte": today.isoformat(),
            "expiration_date.lte": (today + datetime.timedelta(days=max_dte)).isoformat(),
        }
        raw_contracts = self._polygon_options_fetch(ticker, extra_params=extra)
        rows = [normalize_polygon_contract(c) for c in raw_contracts]
        if not rows:
            return pd.DataFrame(columns=OPTION_COLUMNS)
        return pd.DataFrame(rows, columns=OPTION_COLUMNS)

    # ------------------------------------------------------------------
    # Options helpers
    # ------------------------------------------------------------------

    def _polygon_options_fetch(
        self,
        ticker: str,
        extra_params: dict | None = None,
    ) -> list[dict]:
        """
        Paginate through Polygon ``/v3/snapshot/options/{ticker}`` and return
        all raw contract dicts (up to ``_MAX_OPTION_PAGES`` pages).

        Parameters
        ----------
        ticker       : underlying ticker symbol
        extra_params : optional extra query parameters, e.g.::

            {
                "strike_price.gte": 580.0,
                "strike_price.lte": 890.0,
                "expiration_date.gte": "2026-05-29",
                "expiration_date.lte": "2026-08-31",
            }

            These are merged into the base ``{"limit": 250, "apiKey": …}``
            dict and sent with the *first* page request only (Polygon's
            ``next_url`` cursor already encodes them for subsequent pages).
        """
        api_key = os.environ.get("POLYGON_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "POLYGON_API_KEY is not set.  "
                "Add it to backend/.env to use the real options chain."
            )

        url    = _POLYGON_BASE + _OPTIONS_SNAPSHOT.format(ticker=ticker)
        params: dict = {"limit": 250, "apiKey": api_key}
        if extra_params:
            params.update(extra_params)
        contracts: list[dict] = []

        for page in range(_MAX_OPTION_PAGES):
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)
                    break
                except requests.RequestException as exc:
                    if attempt == _MAX_RETRIES:
                        raise RuntimeError(
                            f"Network error fetching Polygon options for {ticker}: {exc}"
                        ) from exc
                    time.sleep(_RETRY_BACKOFF)

            if resp.status_code == 403:
                raise ValueError(
                    f"Polygon returned 403 for options/{ticker}. "
                    "Check POLYGON_API_KEY."
                )
            if resp.status_code == 429:
                time.sleep(_RETRY_BACKOFF * 2)
                continue
            if not resp.ok:
                raise RuntimeError(
                    f"Polygon HTTP {resp.status_code} for options/{ticker}. "
                    f"Response: {resp.text[:300]}"
                )

            data    = resp.json()
            status  = data.get("status", "")
            results = data.get("results") or []

            if status == "NOT_FOUND":
                raise ValueError(
                    f"Polygon could not find options for ticker {ticker!r}. "
                    "Verify the ticker is optionable."
                )

            contracts.extend(results)

            next_url = data.get("next_url")
            if not next_url:
                break

            # Next page: use next_url directly, add apiKey if missing
            url    = next_url
            params = {"apiKey": api_key} if "apiKey=" not in next_url else {}
            logger.debug(
                "Options page %d fetched (%d contracts so far)", page + 1, len(contracts)
            )

        logger.info("Polygon options fetch: %d contracts for %s", len(contracts), ticker)
        return contracts

    @staticmethod
    def _save_options_cache(df: pd.DataFrame, path: pathlib.Path) -> None:
        """Serialize an options chain DataFrame to JSON."""
        records = []
        for _, row in df.iterrows():
            rec = {}
            for col in OPTION_COLUMNS:
                v = row.get(col)
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    rec[col] = None
                elif col in ("open_interest", "volume"):
                    rec[col] = int(v) if v is not None else None
                else:
                    rec[col] = v
            records.append(rec)
        path.write_text(json.dumps({"columns": OPTION_COLUMNS, "records": records}, indent=2))

    @staticmethod
    def _load_options_cache(path: pathlib.Path) -> pd.DataFrame:
        """Load an options chain DataFrame from JSON cache."""
        data    = json.loads(path.read_text())
        records = data.get("records", [])
        if not records:
            return pd.DataFrame(columns=OPTION_COLUMNS)
        df = pd.DataFrame(records, columns=OPTION_COLUMNS)
        # Restore numeric types
        for col in ("strike", "bid", "ask", "mid", "last", "implied_volatility",
                    "delta", "gamma", "theta", "vega"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in ("open_interest", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        return df

    def get_vix_curve(self, start_date, end_date):
        raise NotImplementedError(
            "VIX curve not yet implemented. "
            "No API key required — implement the CBOE CSV fetch."
        )

    def get_macro_snapshot(self) -> dict:
        raise NotImplementedError(
            "Macro snapshot not yet implemented. "
            "Set FRED_API_KEY (and optionally POLYGON_API_KEY) in backend/.env."
        )
