"""
tests/test_real_provider.py
----------------------------
Unit tests for RealDataProvider.get_prices / get_ohlcv.

All Polygon HTTP calls are mocked via unittest.mock.patch so no network
access or API key is required.

Coverage
--------
  _polygon_fetch  — happy path, 403, 429, network error, NOT_FOUND, empty results
  get_prices      — returns pd.Series of closes; validates index/dtype
  get_ohlcv       — returns DataFrame with correct columns
  get_ohlcv_days  — trims to requested days
  disk cache      — hit bypasses requests.get; miss writes file; load round-trips
  endpoint        — GET /api/data/prices/{ticker} via FastAPI TestClient
  mock provider   — MockDataProvider.get_ohlcv returns full OHLCV schema
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import tempfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_TODAY = datetime.date.today()
_START = _TODAY - datetime.timedelta(days=10)
_END   = _TODAY

# A minimal Polygon aggregates response (3 bars)
_POLYGON_BARS = [
    {"t": 1704067200000, "o": 419.0, "h": 422.0, "l": 418.5, "c": 421.5, "v": 75_000_000},
    {"t": 1704153600000, "o": 421.5, "h": 425.0, "l": 420.0, "c": 424.0, "v": 68_000_000},
    {"t": 1704240000000, "o": 424.0, "h": 427.0, "l": 423.0, "c": 426.5, "v": 72_000_000},
]

_POLYGON_OK = {
    "ticker":       "SPY",
    "status":       "OK",
    "resultsCount": len(_POLYGON_BARS),
    "results":      _POLYGON_BARS,
}


def _mock_response(json_body: dict, status_code: int = 200) -> MagicMock:
    """Build a mock requests.Response."""
    m = MagicMock()
    m.status_code = status_code
    m.ok          = status_code < 400
    m.json.return_value = json_body
    m.text        = json.dumps(json_body)
    m.headers     = {}
    return m


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def provider(tmp_path, monkeypatch):
    """RealDataProvider with POLYGON_API_KEY set and cache pointed at tmp_path."""
    monkeypatch.setenv("POLYGON_API_KEY", "test_key_123")

    import app.data.real_provider as rmod
    from app.data.real_provider import RealDataProvider
    monkeypatch.setattr(rmod, "_PRICES_CACHE", tmp_path / "prices")

    return RealDataProvider()


# ===========================================================================
# _polygon_fetch — HTTP layer
# ===========================================================================

class TestPolygonFetch:
    def test_happy_path_returns_dataframe(self, provider):
        with patch("requests.get", return_value=_mock_response(_POLYGON_OK)):
            df = provider._fetch_ohlcv("SPY", _START, _END)

        assert isinstance(df, pd.DataFrame)
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}
        assert len(df) == 3
        assert df["close"].iloc[-1] == pytest.approx(426.5)

    def test_missing_api_key_raises_value_error(self, tmp_path, monkeypatch):
        monkeypatch.delenv("POLYGON_API_KEY", raising=False)
        from app.data.real_provider import RealDataProvider
        monkeypatch.setattr("app.data.real_provider._PRICES_CACHE", tmp_path / "prices")
        p = RealDataProvider()

        with pytest.raises(ValueError, match="POLYGON_API_KEY"):
            p._polygon_fetch("SPY", _START, _END)

    def test_http_403_raises_value_error(self, provider):
        with patch("requests.get", return_value=_mock_response({}, 403)):
            with pytest.raises(ValueError, match="403"):
                provider._polygon_fetch("SPY", _START, _END)

    def test_http_5xx_raises_runtime_error(self, provider):
        bad = _mock_response({"message": "Server Error"}, 500)
        bad.ok = False
        with patch("requests.get", return_value=bad):
            with pytest.raises(RuntimeError, match="500"):
                provider._polygon_fetch("SPY", _START, _END)

    def test_not_found_status_raises_value_error(self, provider):
        payload = {"status": "NOT_FOUND", "results": None}
        with patch("requests.get", return_value=_mock_response(payload)):
            with pytest.raises(ValueError, match="NOT_FOUND|not find"):
                provider._polygon_fetch("INVALID_TICKER", _START, _END)

    def test_empty_results_raises_value_error(self, provider):
        payload = {"status": "OK", "resultsCount": 0, "results": []}
        with patch("requests.get", return_value=_mock_response(payload)):
            with pytest.raises(ValueError, match="no price bars|invalid"):
                provider._polygon_fetch("SPY", _START, _END)

    def test_network_error_raises_runtime_error(self, provider):
        import requests as req_lib
        with patch("requests.get", side_effect=req_lib.RequestException("timeout")):
            with pytest.raises(RuntimeError, match="Network error"):
                provider._polygon_fetch("SPY", _START, _END)


# ===========================================================================
# get_prices — public interface
# ===========================================================================

class TestGetPrices:
    def test_returns_series_of_closes(self, provider):
        with patch("requests.get", return_value=_mock_response(_POLYGON_OK)):
            series = provider.get_prices("SPY", _START, _END)

        assert isinstance(series, pd.Series)
        assert series.name == "SPY"
        assert len(series) == 3
        assert series.iloc[-1] == pytest.approx(426.5)

    def test_index_is_datetime(self, provider):
        with patch("requests.get", return_value=_mock_response(_POLYGON_OK)):
            series = provider.get_prices("SPY", _START, _END)

        assert pd.api.types.is_datetime64_any_dtype(series.index)

    def test_ticker_uppercased(self, provider):
        with patch("requests.get", return_value=_mock_response(_POLYGON_OK)) as mock_get:
            provider.get_prices("spy", _START, _END)
        # URL should contain uppercase SPY
        call_url = mock_get.call_args[0][0]
        assert "/SPY/" in call_url


# ===========================================================================
# get_ohlcv — OHLCV DataFrame
# ===========================================================================

class TestGetOHLCV:
    def test_returns_dataframe_with_correct_columns(self, provider):
        with patch("requests.get", return_value=_mock_response(_POLYGON_OK)):
            df = provider.get_ohlcv("SPY", _START, _END)

        assert isinstance(df, pd.DataFrame)
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}

    def test_high_gte_low(self, provider):
        with patch("requests.get", return_value=_mock_response(_POLYGON_OK)):
            df = provider.get_ohlcv("SPY", _START, _END)

        assert (df["high"] >= df["low"]).all()

    def test_volume_is_integer(self, provider):
        with patch("requests.get", return_value=_mock_response(_POLYGON_OK)):
            df = provider.get_ohlcv("SPY", _START, _END)

        assert df["volume"].dtype in (int, "int64", "int32")

    def test_get_ohlcv_days_trims_to_requested_length(self, provider):
        # Return 3 bars; request 2 → should get last 2
        with patch("requests.get", return_value=_mock_response(_POLYGON_OK)):
            df = provider.get_ohlcv_days("SPY", 2)

        assert len(df) == 2
        assert df["close"].iloc[-1] == pytest.approx(426.5)


# ===========================================================================
# Disk cache
# ===========================================================================

class TestDiskCache:
    def test_cache_miss_writes_file(self, provider, tmp_path, monkeypatch):
        monkeypatch.setattr("app.data.real_provider._PRICES_CACHE", tmp_path / "prices")

        with patch("requests.get", return_value=_mock_response(_POLYGON_OK)):
            provider._fetch_ohlcv("SPY", _START, _END)

        cache_files = list((tmp_path / "prices").glob("SPY_*.json"))
        assert len(cache_files) == 1

    def test_cache_hit_skips_network(self, provider, tmp_path, monkeypatch):
        monkeypatch.setattr("app.data.real_provider._PRICES_CACHE", tmp_path / "prices")

        with patch("requests.get", return_value=_mock_response(_POLYGON_OK)) as mock_get:
            # First call — network hit
            provider._fetch_ohlcv("SPY", _START, _END)
            # Second call — should be cache hit
            provider._fetch_ohlcv("SPY", _START, _END)

        assert mock_get.call_count == 1   # only one real request

    def test_cache_round_trip(self, provider, tmp_path, monkeypatch):
        monkeypatch.setattr("app.data.real_provider._PRICES_CACHE", tmp_path / "prices")

        with patch("requests.get", return_value=_mock_response(_POLYGON_OK)):
            original = provider._fetch_ohlcv("SPY", _START, _END)

        loaded = provider._fetch_ohlcv("SPY", _START, _END)   # from cache

        pd.testing.assert_frame_equal(original, loaded)

    def test_save_then_load_preserves_dtypes(self, tmp_path):
        from app.data.real_provider import RealDataProvider

        idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
        df  = pd.DataFrame({
            "open":   [419.0, 421.0],
            "high":   [422.0, 425.0],
            "low":    [418.5, 420.0],
            "close":  [421.5, 424.0],
            "volume": [75_000_000, 68_000_000],
        }, index=idx)
        df.index.name = "date"

        cache = tmp_path / "test.json"
        RealDataProvider._save_ohlcv_cache(df, cache)
        loaded = RealDataProvider._load_ohlcv_cache(cache)

        assert loaded["volume"].dtype in (int, "int64", "int32")
        assert loaded["close"].iloc[0] == pytest.approx(421.5)


# ===========================================================================
# FastAPI endpoint
# ===========================================================================

class TestPricesEndpoint:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        """TestClient with mock provider pre-configured."""
        from fastapi.testclient import TestClient
        from app.main import app
        # Reset provider so we pick up the mock
        from app.data import registry
        monkeypatch.setattr(registry, "_provider", None)
        monkeypatch.delenv("DATA_PROVIDER", raising=False)
        registry._provider = None

        return TestClient(app)

    def test_prices_endpoint_mock_provider(self, client):
        resp = client.get("/api/data/prices/SPY?days=30")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ticker"]   == "SPY"
        assert body["provider"] == "mock"
        assert len(body["rows"]) == 30
        row = body["rows"][0]
        assert set(row.keys()) >= {"date", "open", "high", "low", "close", "volume"}

    def test_prices_endpoint_stats_shape(self, client):
        resp = client.get("/api/data/prices/SPY?days=60")
        body = resp.json()
        stats = body["stats"]
        assert stats["n_rows"] == 60
        assert stats["latest_close"] is not None
        assert stats["first_date"] is not None
        assert stats["last_date"]  is not None

    def test_prices_endpoint_case_insensitive(self, client):
        resp = client.get("/api/data/prices/spy?days=10")
        assert resp.status_code == 200
        assert resp.json()["ticker"] == "SPY"

    def test_prices_endpoint_unknown_ticker_real_provider(
        self, tmp_path, monkeypatch
    ):
        """Real provider with bad ticker → 404."""
        monkeypatch.setenv("DATA_PROVIDER", "real")
        monkeypatch.setenv("POLYGON_API_KEY", "test_key")
        monkeypatch.setattr("app.data.real_provider._PRICES_CACHE", tmp_path / "prices")

        from app.data import registry
        monkeypatch.setattr(registry, "_provider", None)
        registry._provider = None

        from fastapi.testclient import TestClient
        from app.main import app

        not_found_payload = {"status": "NOT_FOUND", "results": None}
        with patch("requests.get", return_value=_mock_response(not_found_payload)):
            client = TestClient(app)
            resp   = client.get("/api/data/prices/XXXX?days=10")

        assert resp.status_code == 404


# ===========================================================================
# MockDataProvider.get_ohlcv — synthetic OHLCV
# ===========================================================================

class TestMockOHLCV:
    @pytest.fixture()
    def mock_provider(self):
        from app.data._mock_impl import MockDataProvider
        return MockDataProvider()

    def test_get_ohlcv_days_returns_correct_shape(self, mock_provider):
        df = mock_provider.get_ohlcv_days("SPY", 100)
        assert len(df) == 100
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}

    def test_ohlcv_bar_relationships_hold(self, mock_provider):
        df = mock_provider.get_ohlcv_days("SPY", 200)
        assert (df["high"] >= df["open"]).all(),  "high < open found"
        assert (df["high"] >= df["close"]).all(), "high < close found"
        assert (df["low"]  <= df["open"]).all(),  "low > open found"
        assert (df["low"]  <= df["close"]).all(), "low > close found"

    def test_volume_positive(self, mock_provider):
        df = mock_provider.get_ohlcv_days("SPY", 50)
        assert (df["volume"] > 0).all()

    def test_deterministic_across_calls(self, mock_provider):
        df1 = mock_provider.get_ohlcv_days("SPY", 50)
        df2 = mock_provider.get_ohlcv_days("SPY", 50)
        pd.testing.assert_frame_equal(df1, df2)

    def test_different_tickers_differ(self, mock_provider):
        spy  = mock_provider.get_ohlcv_days("SPY",  50)
        nvda = mock_provider.get_ohlcv_days("NVDA", 50)
        # Close prices should be different
        assert not spy["close"].equals(nvda["close"])

    def test_all_supported_tickers(self, mock_provider):
        from app.data._mock_impl import TICKERS
        for ticker in TICKERS:
            df = mock_provider.get_ohlcv_days(ticker, 20)
            assert not df.empty, f"Empty OHLCV for {ticker}"
            assert (df["high"] >= df["low"]).all()
