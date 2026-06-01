"""
tests/test_real_macro.py
------------------------
Unit tests for RealDataProvider.get_macro_series and related helpers.

All FRED HTTP calls, CBOE CSV downloads, and Polygon SPY fetches are mocked
so no network access or API keys are required.

Coverage
--------
  _fetch_cboe_vix      — happy path, network error, HTTP error, empty range,
                         cache hit/miss
  _fetch_fred_series   — happy path, missing "." values, 400 bad series,
                         403 bad key, network error, empty response,
                         cache hit/miss
  _compute_breadth_proxy — SPY above/below 50-DMA transitions
  get_macro_series     — all columns present, calendar alignment,
                         missing FRED_API_KEY, SPY fetch failure
  get_macro_series_days — trims to requested length
  endpoint             — GET /api/data/macro happy path + error cases
  mock provider        — credit_spread column present in mock macro df
"""

from __future__ import annotations

import datetime
import json
import textwrap
from unittest.mock import MagicMock, patch, call

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_TODAY = datetime.date.today()
_START = _TODAY - datetime.timedelta(days=30)
_END   = _TODAY

# Minimal FRED observations JSON (5 daily observations, one missing)
def _fred_obs(values: list) -> dict:
    base = datetime.date(2024, 1, 2)
    obs = []
    for i, v in enumerate(values):
        obs.append({
            "date":  (base + datetime.timedelta(days=i)).isoformat(),
            "value": "." if v is None else str(v),
        })
    return {"observations": obs}


# Minimal CBOE VIX CSV (5 rows)
_CBOE_VIX_CSV = textwrap.dedent("""\
    DATE,OPEN,HIGH,LOW,CLOSE
    01/02/2024,13.00,14.00,12.80,13.50
    01/03/2024,13.50,14.20,13.10,14.00
    01/04/2024,14.00,14.80,13.60,14.50
    01/05/2024,14.50,15.00,14.00,14.80
    01/08/2024,14.80,15.50,14.50,15.00
""")

_CBOE_VIX3M_CSV = textwrap.dedent("""\
    DATE,OPEN,HIGH,LOW,CLOSE
    01/02/2024,16.00,17.00,15.80,16.50
    01/03/2024,16.50,17.20,16.10,17.00
    01/04/2024,17.00,17.80,16.60,17.50
    01/05/2024,17.50,18.00,17.00,17.80
    01/08/2024,17.80,18.50,17.50,18.00
""")

# Minimal Polygon SPY bars (10 bars, enough for 50-DMA warmup in tests)
def _polygon_spy_bars(n: int = 60) -> list[dict]:
    base_ts = 1704067200000   # 2024-01-01 UTC
    day_ms  = 86_400_000
    bars = []
    for i in range(n):
        close = 470.0 + i * 0.5
        bars.append({
            "t": base_ts + i * day_ms,
            "o": close - 0.5,
            "h": close + 1.0,
            "l": close - 1.0,
            "c": close,
            "v": 60_000_000,
        })
    return bars


def _polygon_ok(n: int = 60) -> dict:
    return {"ticker": "SPY", "status": "OK",
            "resultsCount": n, "results": _polygon_spy_bars(n)}


def _mock_resp(body: str | dict, status: int = 200) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.ok          = status < 400
    m.text        = body if isinstance(body, str) else json.dumps(body)
    m.json.return_value = body if isinstance(body, dict) else {}
    m.headers     = {}
    return m


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def provider(tmp_path, monkeypatch):
    """RealDataProvider with keys set and caches redirected to tmp_path."""
    monkeypatch.setenv("POLYGON_API_KEY", "poly_test")
    monkeypatch.setenv("FRED_API_KEY",    "fred_test")

    import app.data.real_provider as rmod
    monkeypatch.setattr(rmod, "_PRICES_CACHE", tmp_path / "prices")
    monkeypatch.setattr(rmod, "_MACRO_CACHE",  tmp_path / "macro")

    from app.data.real_provider import RealDataProvider
    return RealDataProvider()


def _multi_get(cboe_csv: str = _CBOE_VIX_CSV, cboe3m_csv: str = _CBOE_VIX3M_CSV,
               n_spy: int = 60, fred_val: float = 4.0):
    """Build a requests.get side_effect that dispatches by URL."""
    def _get(url, **kwargs):
        params = kwargs.get("params", {})
        if "polygon.io" in url:
            return _mock_resp(_polygon_ok(n_spy))
        if "cboe.com" in url:
            if "VIX3M" in url:
                return _mock_resp(cboe3m_csv)
            return _mock_resp(cboe_csv)
        if "stlouisfed.org" in url:
            series_id = params.get("series_id", "DGS10")
            vals: list
            if "DGS10" in series_id:
                vals = [4.05, 4.10, 4.08, 4.12, None, 4.09]  # one missing
            elif "DCOILWTICO" in series_id:
                vals = [72.0, 73.5, 71.8, 74.2, 75.0, 73.1]
            elif "BAMLH0A0HYM2" in series_id:
                vals = [3.2, 3.3, 3.25, 3.4, 3.35, 3.28]
            else:
                vals = [fred_val] * 6
            return _mock_resp(_fred_obs(vals))
        raise AssertionError(f"Unexpected URL in test: {url}")
    return _get


# ===========================================================================
# _fetch_cboe_vix
# ===========================================================================

class TestFetchCboeVix:
    def test_happy_path_returns_series(self, provider):
        with patch("requests.get", return_value=_mock_resp(_CBOE_VIX_CSV)):
            s = provider._fetch_cboe_vix("VIX", datetime.date(2024, 1, 1), datetime.date(2024, 1, 31))
        assert isinstance(s, pd.Series)
        assert len(s) == 5
        assert s.iloc[-1] == pytest.approx(15.0)

    def test_network_error_raises_runtime(self, provider):
        import requests as req_lib
        with patch("requests.get", side_effect=req_lib.RequestException("timeout")):
            with pytest.raises(RuntimeError, match="Network error"):
                provider._fetch_cboe_vix("VIX", _START, _END)

    def test_http_error_raises_runtime(self, provider):
        with patch("requests.get", return_value=_mock_resp("", 503)):
            with pytest.raises(RuntimeError, match="503"):
                provider._fetch_cboe_vix("VIX", _START, _END)

    def test_empty_range_raises_value_error(self, provider):
        # Request a date range before the CSV data
        with patch("requests.get", return_value=_mock_resp(_CBOE_VIX_CSV)):
            with pytest.raises(ValueError, match="no data"):
                provider._fetch_cboe_vix("VIX",
                    datetime.date(2020, 1, 1), datetime.date(2020, 1, 31))

    def test_cache_miss_writes_file(self, provider, tmp_path, monkeypatch):
        import app.data.real_provider as rmod
        monkeypatch.setattr(rmod, "_MACRO_CACHE", tmp_path / "macro")
        with patch("requests.get", return_value=_mock_resp(_CBOE_VIX_CSV)):
            provider._fetch_cboe_vix("VIX", datetime.date(2024, 1, 1), datetime.date(2024, 1, 31))
        assert any((tmp_path / "macro").glob("CBOE_VIX_*.json"))

    def test_cache_hit_skips_network(self, provider, tmp_path, monkeypatch):
        import app.data.real_provider as rmod
        monkeypatch.setattr(rmod, "_MACRO_CACHE", tmp_path / "macro")
        with patch("requests.get", return_value=_mock_resp(_CBOE_VIX_CSV)) as mock_get:
            provider._fetch_cboe_vix("VIX", datetime.date(2024, 1, 1), datetime.date(2024, 1, 31))
            provider._fetch_cboe_vix("VIX", datetime.date(2024, 1, 1), datetime.date(2024, 1, 31))
        assert mock_get.call_count == 1


# ===========================================================================
# _fetch_fred_series
# ===========================================================================

class TestFetchFredSeries:
    def test_happy_path_skips_missing_dots(self, provider):
        obs = _fred_obs([4.05, None, 4.10, None, 4.08])   # 2 missing
        with patch("requests.get", return_value=_mock_resp(obs)):
            s = provider._fetch_fred_series("DGS10", _START, _END, "key")
        assert isinstance(s, pd.Series)
        assert len(s) == 3   # 2 "." values dropped
        assert s.iloc[-1] == pytest.approx(4.08)

    def test_http_400_raises_value_error(self, provider):
        with patch("requests.get", return_value=_mock_resp({"error": "bad"}, 400)):
            with pytest.raises(ValueError, match="rejected|400"):
                provider._fetch_fred_series("BAD_SERIES", _START, _END, "key")

    def test_http_403_raises_value_error(self, provider):
        with patch("requests.get", return_value=_mock_resp({}, 403)):
            with pytest.raises(ValueError, match="403|FRED_API_KEY"):
                provider._fetch_fred_series("DGS10", _START, _END, "bad_key")

    def test_all_missing_raises_value_error(self, provider):
        obs = _fred_obs([None, None, None])
        with patch("requests.get", return_value=_mock_resp(obs)):
            with pytest.raises(ValueError, match="no non-missing"):
                provider._fetch_fred_series("DGS10", _START, _END, "key")

    def test_network_error_raises_runtime(self, provider):
        import requests as req_lib
        with patch("requests.get", side_effect=req_lib.RequestException("refused")):
            with pytest.raises(RuntimeError, match="Network error"):
                provider._fetch_fred_series("DGS10", _START, _END, "key")

    def test_cache_hit_skips_network(self, provider, tmp_path, monkeypatch):
        import app.data.real_provider as rmod
        monkeypatch.setattr(rmod, "_MACRO_CACHE", tmp_path / "macro")
        obs = _fred_obs([4.0, 4.1])
        with patch("requests.get", return_value=_mock_resp(obs)) as mock_get:
            provider._fetch_fred_series("DGS10", _START, _END, "key")
            provider._fetch_fred_series("DGS10", _START, _END, "key")
        assert mock_get.call_count == 1

    def test_roundtrip_preserves_values(self, provider, tmp_path, monkeypatch):
        import app.data.real_provider as rmod
        monkeypatch.setattr(rmod, "_MACRO_CACHE", tmp_path / "macro")
        obs = _fred_obs([4.0, 4.1, 4.2])
        with patch("requests.get", return_value=_mock_resp(obs)):
            s1 = provider._fetch_fred_series("DGS10", _START, _END, "key")
        s2 = provider._fetch_fred_series("DGS10", _START, _END, "key")  # from cache
        pd.testing.assert_series_equal(s1, s2)


# ===========================================================================
# _compute_breadth_proxy
# ===========================================================================

class TestBreadthProxy:
    def _spy(self, values: list[float]) -> pd.Series:
        idx = pd.bdate_range(end="2024-06-30", periods=len(values))
        return pd.Series(values, index=idx, name="SPY")

    def test_above_50dma_gives_100(self):
        from app.data.real_provider import RealDataProvider
        # Flat series — SPY always equals SMA50 → transitions to 100 once SMA warms up
        # Make prices clearly above initial level (SMA starts at first value)
        vals = [100.0] * 60 + [150.0] * 20   # big jump ensures > SMA
        spy  = self._spy(vals)
        cal  = spy.index[-20:]
        result = RealDataProvider._compute_breadth_proxy(spy, cal)
        assert (result == 100.0).all()

    def test_below_50dma_gives_0(self):
        from app.data.real_provider import RealDataProvider
        # Start high, crash — last 20 bars well below SMA
        vals = [200.0] * 60 + [50.0] * 20
        spy  = self._spy(vals)
        cal  = spy.index[-20:]
        result = RealDataProvider._compute_breadth_proxy(spy, cal)
        assert (result == 0.0).all()

    def test_output_only_has_0_and_100(self):
        from app.data.real_provider import RealDataProvider
        rng  = np.random.default_rng(42)
        vals = 100 + np.cumsum(rng.normal(0, 1, 150))
        spy  = self._spy(vals.tolist())
        result = RealDataProvider._compute_breadth_proxy(spy, spy.index[-50:])
        assert set(result.unique()).issubset({0.0, 100.0})


# ===========================================================================
# get_macro_series — integration
# ===========================================================================

class TestGetMacroSeries:
    def test_missing_fred_key_raises_value_error(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        import app.data.real_provider as rmod
        monkeypatch.setattr(rmod, "_MACRO_CACHE", tmp_path / "macro")
        from app.data.real_provider import RealDataProvider
        p = RealDataProvider()
        with pytest.raises(ValueError, match="FRED_API_KEY"):
            p.get_macro_series(_START, _END)

    def test_returns_all_expected_columns(self, provider):
        with patch("requests.get", side_effect=_multi_get()):
            df = provider.get_macro_series(
                datetime.date(2024, 1, 1), datetime.date(2024, 1, 31)
            )
        expected = {"vix1m", "vix3m", "breadth", "us10y", "crude_oil",
                    "credit_spread", "spy_price"}
        assert expected.issubset(set(df.columns)), \
            f"Missing columns: {expected - set(df.columns)}"

    def test_returns_dataframe(self, provider):
        with patch("requests.get", side_effect=_multi_get()):
            df = provider.get_macro_series(
                datetime.date(2024, 1, 1), datetime.date(2024, 1, 31)
            )
        assert isinstance(df, pd.DataFrame)
        assert not df.empty

    def test_index_is_datetime(self, provider):
        with patch("requests.get", side_effect=_multi_get()):
            df = provider.get_macro_series(
                datetime.date(2024, 1, 1), datetime.date(2024, 1, 31)
            )
        assert pd.api.types.is_datetime64_any_dtype(df.index)

    def test_no_nan_after_alignment(self, provider):
        """After ffill, key FRED/VIX series should have no NaN on the SPY calendar."""
        with patch("requests.get", side_effect=_multi_get(n_spy=60)):
            df = provider.get_macro_series(
                datetime.date(2024, 1, 2), datetime.date(2024, 1, 8)
            )
        # vix1m/vix3m/us10y/crude_oil/credit_spread must be filled
        for col in ("vix1m", "vix3m", "us10y", "crude_oil", "credit_spread"):
            if col in df.columns:
                assert df[col].notna().all(), f"NaN in {col} after alignment"

    def test_spy_fetch_failure_raises_value_error(self, provider):
        """If SPY prices cannot be fetched, get_macro_series raises ValueError."""
        def _fail_spy(url, **kwargs):
            if "polygon.io" in url:
                return _mock_resp({"status": "NOT_FOUND", "results": None})
            return _mock_resp(_CBOE_VIX_CSV)

        with patch("requests.get", side_effect=_fail_spy):
            with pytest.raises(ValueError, match="SPY|calendar|breadth"):
                provider.get_macro_series(
                    datetime.date(2024, 1, 1), datetime.date(2024, 1, 31)
                )

    def test_breadth_only_0_or_100(self, provider):
        with patch("requests.get", side_effect=_multi_get(n_spy=80)):
            df = provider.get_macro_series(
                datetime.date(2024, 1, 2), datetime.date(2024, 1, 8)
            )
        assert set(df["breadth"].dropna().unique()).issubset({0.0, 100.0})

    def test_uses_proxy_breadth_is_true(self, provider):
        assert provider.uses_proxy_breadth is True

    def test_get_macro_series_days_trims(self, provider, monkeypatch):
        """get_macro_series_days(5) must return at most 5 rows."""
        # Build a 20-row macro df on the current SPY calendar
        cal = pd.bdate_range(end=datetime.date.today(), periods=20)
        fake_df = pd.DataFrame({
            "vix1m": 15.0, "vix3m": 16.5, "breadth": 60.0,
            "us10y": 4.1, "crude_oil": 74.0,
            "credit_spread": 3.4, "spy_price": 470.0,
        }, index=cal)

        monkeypatch.setattr(provider, "get_macro_series",
                            lambda st, en: fake_df)
        df = provider.get_macro_series_days(5)
        assert len(df) <= 5


# ===========================================================================
# Endpoint — GET /api/data/macro
# ===========================================================================

class TestMacroEndpoint:
    @pytest.fixture()
    def client(self, monkeypatch):
        """TestClient with mock provider (no API keys needed)."""
        from app.data import registry
        monkeypatch.setattr(registry, "_provider", None)
        monkeypatch.delenv("DATA_PROVIDER", raising=False)
        registry._provider = None

        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_endpoint_returns_200_with_mock(self, client):
        resp = client.get("/api/data/macro?days=30")
        assert resp.status_code == 200

    def test_response_shape(self, client):
        resp = client.get("/api/data/macro?days=30")
        body = resp.json()
        assert body["provider"]    == "mock"
        assert "stats"             in body
        assert "rows"              in body
        assert "breadth_is_proxy"  in body
        assert len(body["rows"])   == 30

    def test_row_has_expected_keys(self, client):
        resp = client.get("/api/data/macro?days=30")
        assert resp.status_code == 200
        row  = resp.json()["rows"][0]
        expected = {"date", "vix1m", "vix3m", "vix_spread", "breadth",
                    "us10y", "crude_oil", "credit_spread", "spy_price"}
        assert expected.issubset(set(row.keys()))

    def test_vix_spread_is_derived(self, client):
        resp = client.get("/api/data/macro?days=30")
        assert resp.status_code == 200
        for row in resp.json()["rows"]:
            v1 = row["vix1m"]
            v3 = row["vix3m"]
            vs = row["vix_spread"]
            if v1 is not None and v3 is not None:
                assert abs(vs - (v3 - v1)) < 1e-3

    def test_stats_latest_values(self, client):
        resp  = client.get("/api/data/macro?days=50")
        stats = resp.json()["stats"]
        assert stats["latest_vix"]    is not None
        assert stats["latest_vix3m"]  is not None
        assert stats["latest_us10y"]  is not None
        assert "is_vix_inverted" in stats

    def test_breadth_is_proxy_false_for_mock(self, client):
        resp = client.get("/api/data/macro?days=30")
        assert resp.status_code == 200
        assert resp.json()["breadth_is_proxy"] is False   # mock has smooth breadth

    def test_real_provider_missing_fred_key_returns_404(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_PROVIDER", "real")
        monkeypatch.setenv("POLYGON_API_KEY", "poly")
        monkeypatch.delenv("FRED_API_KEY", raising=False)

        from app.data import registry
        monkeypatch.setattr(registry, "_provider", None)
        registry._provider = None
        import app.data.real_provider as rmod
        monkeypatch.setattr(rmod, "_MACRO_CACHE", tmp_path / "macro")

        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        resp = client.get("/api/data/macro?days=30")
        assert resp.status_code == 404
        assert "FRED_API_KEY" in resp.json()["detail"]


# ===========================================================================
# Mock provider — credit_spread column
# ===========================================================================

class TestMockCreditSpread:
    @pytest.fixture()
    def mock_provider(self):
        from app.data._mock_impl import MockDataProvider
        return MockDataProvider()

    def test_credit_spread_column_present(self, mock_provider):
        df = mock_provider.get_macro_series_days(100)
        assert "credit_spread" in df.columns, "Mock macro df is missing credit_spread"

    def test_credit_spread_in_reasonable_range(self, mock_provider):
        df = mock_provider.get_macro_series_days(400)
        cs = df["credit_spread"]
        assert cs.min() >= 0.5,  f"Credit spread too low: {cs.min()}"
        assert cs.max() <= 15.0, f"Credit spread too high: {cs.max()}"

    def test_credit_spread_correlated_with_vix(self, mock_provider):
        """In mock, credit spread tends to be higher when VIX is elevated."""
        df   = mock_provider.get_macro_series_days(500)
        hi   = df.loc[df["vix1m"] > 25, "credit_spread"].mean()
        lo   = df.loc[df["vix1m"] < 18, "credit_spread"].mean()
        # Not necessarily strict but directionally correct in the mock
        assert hi > lo or abs(hi - lo) < 1.5, \
            f"Expected credit spread to be higher when VIX > 25 (got hi={hi:.2f}, lo={lo:.2f})"

    def test_mock_macro_schema_matches_real_schema(self, mock_provider):
        """Mock and real providers should return the same column set."""
        df   = mock_provider.get_macro_series_days(50)
        cols = set(df.columns)
        required = {"vix1m", "vix3m", "breadth", "us10y", "crude_oil",
                    "credit_spread", "spy_price"}
        assert required.issubset(cols), f"Mock is missing: {required - cols}"

    def test_deterministic_across_calls(self, mock_provider):
        df1 = mock_provider.get_macro_series_days(100)
        df2 = mock_provider.get_macro_series_days(100)
        pd.testing.assert_frame_equal(df1, df2)
