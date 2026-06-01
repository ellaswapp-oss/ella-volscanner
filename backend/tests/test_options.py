"""
tests/test_options.py
---------------------
Tests for options chain ingestion, IV surface calculation, and endpoints.

All Polygon HTTP calls are mocked via unittest.mock.patch — no network access
or API key is required.

Coverage
--------
  normalize_polygon_contract  — happy path, missing greeks, mid computed from bid/ask
  calculate_atm_iv_by_tenor   — five-tenor surface, empty chain, IV as percentage
  bs_price_and_greeks         — put-call parity, delta bounds, gamma/theta signs
  RealDataProvider            — happy path, HTTP 403, NOT_FOUND, pagination, cache
  MockDataProvider            — chain shape, BS bar relationships, determinism
  FastAPI endpoints           — /options/{ticker}, /iv-surface/{ticker}
"""

from __future__ import annotations

import datetime
import json
import math
import pathlib
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Shared test fixtures / data
# ---------------------------------------------------------------------------

_TODAY = datetime.date.today()


def _mock_resp(body, status_code: int = 200) -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.ok          = status_code < 400
    m.json.return_value = body
    m.text        = json.dumps(body) if isinstance(body, (dict, list)) else str(body)
    m.headers     = {}
    return m


# ---------------------------------------------------------------------------
# Minimal Polygon v3 snapshot contract
# ---------------------------------------------------------------------------

_CONTRACT = {
    "break_even_price": 426.50,
    "day": {
        "close":          8.50,
        "high":           9.00,
        "last_updated":   1704326400000000000,
        "low":            7.50,
        "open":           8.00,
        "previous_close": 7.30,
        "volume":         12345,
    },
    "details": {
        "contract_type":        "call",
        "exercise_style":       "american",
        "expiration_date":      "2024-06-21",
        "shares_per_contract":  100,
        "strike_price":         450.0,
        "ticker":               "O:SPY240621C00450000",
    },
    "greeks": {
        "delta": 0.432,
        "gamma": 0.022,
        "theta": -0.185,
        "vega":  0.321,
    },
    "implied_volatility": 0.1895,
    "last_quote": {
        "ask":          8.90,
        "ask_size":     100,
        "bid":          8.10,
        "bid_size":     80,
        "last_updated": 1704326400000000000,
        "midpoint":     8.50,
    },
    "open_interest": 45678,
    "underlying_asset": {
        "price":     456.50,
        "ticker":    "SPY",
        "timeframe": "REAL-TIME",
    },
}

_CONTRACT_PUT = {
    **_CONTRACT,
    "details": {**_CONTRACT["details"], "contract_type": "put", "strike_price": 440.0},
    "greeks":  {"delta": -0.35, "gamma": 0.019, "theta": -0.142, "vega": 0.287},
    "implied_volatility": 0.2120,
    "last_quote": {"bid": 6.20, "ask": 6.80, "midpoint": 6.50},
}

# Two more expirations for a realistic chain
_CONTRACT_EXP2 = {
    **_CONTRACT,
    "details": {**_CONTRACT["details"], "expiration_date": "2024-09-20"},
    "implied_volatility": 0.2100,
}
_CONTRACT_EXP2_PUT = {
    **_CONTRACT_PUT,
    "details": {**_CONTRACT_PUT["details"], "expiration_date": "2024-09-20"},
    "implied_volatility": 0.2350,
}


def _polygon_options_ok(results=None) -> dict:
    return {
        "status":     "OK",
        "request_id": "test_req",
        "results":    results or [_CONTRACT],
        "next_url":   None,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "test_key_123")
    import app.data.real_provider as rmod
    from app.data.real_provider import RealDataProvider
    monkeypatch.setattr(rmod, "_PRICES_CACHE",  tmp_path / "prices")
    monkeypatch.setattr(rmod, "_OPTIONS_CACHE", tmp_path / "options")
    return RealDataProvider()


@pytest.fixture()
def mock_provider():
    from app.data._mock_impl import MockDataProvider
    return MockDataProvider()


# ===========================================================================
# normalize_polygon_contract
# ===========================================================================

class TestNormalizeContract:
    def test_happy_path_all_fields_populated(self):
        from app.data.options_utils import normalize_polygon_contract
        row = normalize_polygon_contract(_CONTRACT)

        assert row["underlying_ticker"] == "SPY"
        assert row["expiration"]        == "2024-06-21"
        assert row["strike"]            == pytest.approx(450.0)
        assert row["option_type"]       == "call"
        assert row["bid"]               == pytest.approx(8.10)
        assert row["ask"]               == pytest.approx(8.90)
        assert row["mid"]               == pytest.approx(8.50)
        assert row["last"]              == pytest.approx(8.50)
        assert row["implied_volatility"] == pytest.approx(0.1895)
        assert row["delta"]             == pytest.approx(0.432)
        assert row["gamma"]             == pytest.approx(0.022)
        assert row["theta"]             == pytest.approx(-0.185)
        assert row["vega"]              == pytest.approx(0.321)
        assert row["open_interest"]     == 45678
        assert row["volume"]            == 12345

    def test_missing_greeks_returns_none(self):
        from app.data.options_utils import normalize_polygon_contract
        c = {**_CONTRACT}
        c.pop("greeks")
        row = normalize_polygon_contract(c)
        assert row["delta"] is None
        assert row["gamma"] is None

    def test_mid_computed_when_midpoint_absent(self):
        from app.data.options_utils import normalize_polygon_contract
        c = {**_CONTRACT}
        c["last_quote"] = {"bid": 8.00, "ask": 9.00}
        row = normalize_polygon_contract(c)
        assert row["mid"] == pytest.approx(8.50)

    def test_put_contract_type_normalised(self):
        from app.data.options_utils import normalize_polygon_contract
        row = normalize_polygon_contract(_CONTRACT_PUT)
        assert row["option_type"] == "put"

    def test_underlying_ticker_from_option_symbol_fallback(self):
        """If underlying_asset is absent, parse the option symbol."""
        from app.data.options_utils import normalize_polygon_contract
        c = {**_CONTRACT}
        c.pop("underlying_asset")
        row = normalize_polygon_contract(c)
        assert row["underlying_ticker"] == "SPY"

    def test_missing_iv_returns_none(self):
        from app.data.options_utils import normalize_polygon_contract
        c = {k: v for k, v in _CONTRACT.items() if k != "implied_volatility"}
        row = normalize_polygon_contract(c)
        assert row["implied_volatility"] is None


# ===========================================================================
# calculate_atm_iv_by_tenor
# ===========================================================================

class TestATMIVByTenor:
    @pytest.fixture()
    def sample_chain(self):
        """Minimal two-expiration chain with call + put per expiration."""
        from app.data.options_utils import normalize_polygon_contract
        contracts = [_CONTRACT, _CONTRACT_PUT, _CONTRACT_EXP2, _CONTRACT_EXP2_PUT]
        rows = [normalize_polygon_contract(c) for c in contracts]
        return pd.DataFrame(rows)

    def test_returns_all_tenor_keys(self, sample_chain):
        from app.data.options_utils import calculate_atm_iv_by_tenor
        result = calculate_atm_iv_by_tenor(
            sample_chain,
            underlying_price=456.50,
            pricing_date=datetime.date(2024, 5, 1),
        )
        # _tenor_meta is also present — check the five IV keys are a subset
        assert {"iv7", "iv14", "iv30", "iv60", "iv90"}.issubset(result.keys())

    def test_empty_chain_returns_all_none(self):
        from app.data.options_utils import calculate_atm_iv_by_tenor
        df     = pd.DataFrame(columns=["expiration", "strike", "option_type", "implied_volatility"])
        result = calculate_atm_iv_by_tenor(df, underlying_price=100.0)
        # _tenor_meta is a dict, not None — filter it out
        assert all(v is None for k, v in result.items() if not k.startswith("_"))

    def test_iv_returned_as_percentage(self, sample_chain):
        """IV is stored as decimal fraction (0.19); result must be ~19 %."""
        from app.data.options_utils import calculate_atm_iv_by_tenor
        # pricing_date=2024-05-22 → June 21 expiration is exactly 30 DTE (within ±10d tolerance)
        result = calculate_atm_iv_by_tenor(
            sample_chain,
            underlying_price=456.50,
            pricing_date=datetime.date(2024, 5, 22),
        )
        # 0.1895 fraction → ~18.95 %
        iv30 = result["iv30"]
        assert iv30 is not None
        assert 10.0 < iv30 < 40.0, f"Expected percent, got {iv30}"

    def test_closest_expiration_used(self, sample_chain):
        """iv30 should use the June expiration (30 DTE from 2024-05-22, within ±10d tolerance)."""
        from app.data.options_utils import calculate_atm_iv_by_tenor
        # pricing_date=2024-05-22 → 2024-06-21 is exactly 30 DTE (diff=0 ≤ tolerance=10)
        result = calculate_atm_iv_by_tenor(
            sample_chain,
            underlying_price=456.50,
            pricing_date=datetime.date(2024, 5, 22),
        )
        # June expiration is closest to 30d target and within tolerance
        assert result["iv30"] is not None

    def test_both_call_and_put_averaged(self, sample_chain):
        """Average of call IV (18.95%) and put IV (21.20%) ≈ 20.075%."""
        from app.data.options_utils import calculate_atm_iv_by_tenor
        # pricing_date=2024-05-22 → June 21 expiration is exactly 30 DTE (within ±10d tolerance)
        result = calculate_atm_iv_by_tenor(
            sample_chain,
            underlying_price=445.0,   # between 440 and 450 → picks either
            pricing_date=datetime.date(2024, 5, 22),
        )
        iv30 = result["iv30"]
        assert iv30 is not None
        # Should be between the two IVs converted to percentages
        assert 15.0 < iv30 < 28.0


# ===========================================================================
# bs_price_and_greeks
# ===========================================================================

class TestBSPriceAndGreeks:
    def _call(self, S=100, K=100, T=0.25, r=0.05, sigma=0.20):
        from app.data.options_utils import bs_price_and_greeks
        return bs_price_and_greeks(S, K, T, r, sigma, "call")

    def _put(self, S=100, K=100, T=0.25, r=0.05, sigma=0.20):
        from app.data.options_utils import bs_price_and_greeks
        return bs_price_and_greeks(S, K, T, r, sigma, "put")

    def test_call_price_positive(self):
        price, *_ = self._call()
        assert price > 0

    def test_put_price_positive(self):
        price, *_ = self._put()
        assert price > 0

    def test_put_call_parity(self):
        """C - P = S - K * e^(-rT) (European, no dividends)."""
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.20
        c_price, *_ = self._call(S, K, T, r, sigma)
        p_price, *_ = self._put(S, K, T, r, sigma)
        parity = S - K * math.exp(-r * T)
        assert c_price - p_price == pytest.approx(parity, abs=0.01)

    def test_call_delta_in_zero_one(self):
        _, delta, *_ = self._call()
        assert 0.0 < delta < 1.0

    def test_put_delta_in_neg_one_zero(self):
        _, delta, *_ = self._put()
        assert -1.0 < delta < 0.0

    def test_gamma_positive(self):
        _, _, gamma, *_ = self._call()
        assert gamma > 0

    def test_theta_negative_for_long(self):
        """Long options lose value over time → theta < 0."""
        _, _, _, theta, _ = self._call()
        assert theta < 0

    def test_vega_positive(self):
        *_, vega = self._call()
        assert vega > 0

    def test_deep_otm_call_near_zero(self):
        """Deep OTM option should have near-zero price."""
        from app.data.options_utils import bs_price_and_greeks
        price, *_ = bs_price_and_greeks(100, 200, 0.10, 0.05, 0.20, "call")
        assert price < 0.01

    def test_expiry_at_zero_returns_intrinsic(self):
        """At T=0 the price must equal the intrinsic value."""
        from app.data.options_utils import bs_price_and_greeks
        price, delta, gamma, theta, vega = bs_price_and_greeks(110, 100, 0.0, 0.05, 0.20, "call")
        assert price == pytest.approx(10.0, abs=0.01)
        assert gamma == 0.0
        assert theta == 0.0
        assert vega  == 0.0


# ===========================================================================
# RealDataProvider.get_options_chain
# ===========================================================================

class TestRealOptionsChain:
    def test_happy_path_returns_dataframe(self, provider):
        resp = _mock_resp(_polygon_options_ok([_CONTRACT, _CONTRACT_PUT]))
        with patch("requests.get", return_value=resp):
            df = provider.get_options_chain("SPY", _TODAY)

        assert isinstance(df, pd.DataFrame)
        assert set(df.columns) >= {"underlying_ticker", "expiration", "strike",
                                   "option_type", "implied_volatility"}
        assert len(df) == 2

    def test_schema_columns(self, provider):
        from app.data.options_utils import OPTION_COLUMNS
        with patch("requests.get", return_value=_mock_resp(_polygon_options_ok())):
            df = provider.get_options_chain("SPY", _TODAY)
        assert list(df.columns) == OPTION_COLUMNS

    def test_iv_stored_as_decimal_fraction(self, provider):
        with patch("requests.get", return_value=_mock_resp(_polygon_options_ok())):
            df = provider.get_options_chain("SPY", _TODAY)
        iv = float(df["implied_volatility"].iloc[0])
        assert 0.0 < iv < 5.0, f"Expected decimal fraction, got {iv}"

    def test_missing_api_key_raises_value_error(self, tmp_path, monkeypatch):
        monkeypatch.delenv("POLYGON_API_KEY", raising=False)
        import app.data.real_provider as rmod
        from app.data.real_provider import RealDataProvider
        monkeypatch.setattr(rmod, "_OPTIONS_CACHE", tmp_path / "options")
        p = RealDataProvider()
        with pytest.raises(ValueError, match="POLYGON_API_KEY"):
            p.get_options_chain("SPY", _TODAY)

    def test_http_403_raises_value_error(self, provider):
        with patch("requests.get", return_value=_mock_resp({}, 403)):
            with pytest.raises(ValueError, match="403"):
                provider.get_options_chain("SPY", _TODAY)

    def test_not_found_status_raises_value_error(self, provider):
        body = {"status": "NOT_FOUND", "results": None}
        with patch("requests.get", return_value=_mock_resp(body)):
            with pytest.raises(ValueError, match="NOT_FOUND|not find"):
                provider.get_options_chain("XXXX", _TODAY)

    def test_pagination_follows_next_url(self, provider, tmp_path, monkeypatch):
        """Two-page response: first page returns next_url, second page has no next_url."""
        import app.data.real_provider as rmod
        monkeypatch.setattr(rmod, "_OPTIONS_CACHE", tmp_path / "options2")

        page1 = {
            "status":   "OK",
            "results":  [_CONTRACT],
            "next_url": "https://api.polygon.io/v3/snapshot/options/SPY?cursor=abc",
        }
        page2 = {
            "status":   "OK",
            "results":  [_CONTRACT_PUT],
            "next_url": None,
        }
        side_effects = [_mock_resp(page1), _mock_resp(page2)]

        with patch("requests.get", side_effect=side_effects) as mock_get:
            df = provider.get_options_chain("SPY", _TODAY)

        assert mock_get.call_count == 2
        assert len(df) == 2

    def test_cache_miss_writes_file(self, provider, tmp_path, monkeypatch):
        import app.data.real_provider as rmod
        cache_dir = tmp_path / "options_cm"
        monkeypatch.setattr(rmod, "_OPTIONS_CACHE", cache_dir)

        with patch("requests.get", return_value=_mock_resp(_polygon_options_ok())):
            provider.get_options_chain("SPY", _TODAY)

        cache_files = list(cache_dir.glob("SPY_*.json"))
        assert len(cache_files) == 1

    def test_cache_hit_skips_network(self, provider, tmp_path, monkeypatch):
        import app.data.real_provider as rmod
        cache_dir = tmp_path / "options_ch"
        monkeypatch.setattr(rmod, "_OPTIONS_CACHE", cache_dir)

        with patch("requests.get", return_value=_mock_resp(_polygon_options_ok())) as mock_get:
            provider.get_options_chain("SPY", _TODAY)
            provider.get_options_chain("SPY", _TODAY)   # should hit cache

        assert mock_get.call_count == 1

    def test_cache_round_trip(self, provider, tmp_path, monkeypatch):
        import app.data.real_provider as rmod
        cache_dir = tmp_path / "options_rt"
        monkeypatch.setattr(rmod, "_OPTIONS_CACHE", cache_dir)

        with patch("requests.get", return_value=_mock_resp(_polygon_options_ok())):
            original = provider.get_options_chain("SPY", _TODAY)

        loaded = provider.get_options_chain("SPY", _TODAY)   # from cache
        pd.testing.assert_frame_equal(
            original.reset_index(drop=True),
            loaded.reset_index(drop=True),
            check_dtype=False,   # Int64 vs int64 for OI/volume after cache
        )


# ===========================================================================
# MockDataProvider.get_options_chain
# ===========================================================================

class TestMockOptionsChain:
    def test_returns_dataframe(self, mock_provider):
        df = mock_provider.get_options_chain("SPY", _TODAY)
        assert isinstance(df, pd.DataFrame)

    def test_correct_schema_columns(self, mock_provider):
        from app.data.options_utils import OPTION_COLUMNS
        df = mock_provider.get_options_chain("SPY", _TODAY)
        assert list(df.columns) == OPTION_COLUMNS

    def test_non_empty_chain(self, mock_provider):
        df = mock_provider.get_options_chain("SPY", _TODAY)
        assert len(df) > 0

    def test_call_delta_positive(self, mock_provider):
        df    = mock_provider.get_options_chain("SPY", _TODAY)
        calls = df[df["option_type"] == "call"]["delta"].dropna()
        assert (calls > 0).all(), "All call deltas should be positive"

    def test_put_delta_negative(self, mock_provider):
        df   = mock_provider.get_options_chain("SPY", _TODAY)
        puts = df[df["option_type"] == "put"]["delta"].dropna()
        assert (puts < 0).all(), "All put deltas should be negative"

    def test_gamma_non_negative(self, mock_provider):
        """Gamma is non-negative; deep OTM contracts may round to 0.0."""
        df = mock_provider.get_options_chain("SPY", _TODAY)
        assert (df["gamma"].dropna() >= 0).all()

    def test_iv_stored_as_decimal_fraction(self, mock_provider):
        df = mock_provider.get_options_chain("SPY", _TODAY)
        iv = df["implied_volatility"].dropna()
        assert (iv > 0).all() and (iv < 5.0).all(), "IV should be a decimal fraction"

    def test_deterministic_across_calls(self, mock_provider):
        df1 = mock_provider.get_options_chain("SPY", _TODAY)
        df2 = mock_provider.get_options_chain("SPY", _TODAY)
        pd.testing.assert_frame_equal(df1, df2)

    def test_different_tickers_differ(self, mock_provider):
        spy  = mock_provider.get_options_chain("SPY",  _TODAY)
        nvda = mock_provider.get_options_chain("NVDA", _TODAY)
        # IV levels should differ substantially between tickers
        spy_iv  = float(spy["implied_volatility"].dropna().mean())
        nvda_iv = float(nvda["implied_volatility"].dropna().mean())
        assert abs(spy_iv - nvda_iv) > 0.05  # NVDA should have much higher IV

    def test_has_both_calls_and_puts(self, mock_provider):
        df = mock_provider.get_options_chain("SPY", _TODAY)
        assert "call" in df["option_type"].values
        assert "put"  in df["option_type"].values

    def test_all_supported_tickers(self, mock_provider):
        from app.data._mock_impl import TICKERS
        for ticker in TICKERS:
            df = mock_provider.get_options_chain(ticker, _TODAY)
            assert not df.empty, f"Empty chain for {ticker}"
            assert (df["gamma"].dropna() >= 0).all()


# ===========================================================================
# FastAPI endpoints
# ===========================================================================

class TestOptionsEndpoint:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.data import registry
        monkeypatch.setattr(registry, "_provider", None)
        monkeypatch.delenv("DATA_PROVIDER", raising=False)
        registry._provider = None
        return TestClient(app)

    def test_options_endpoint_returns_200(self, client):
        resp = client.get("/api/data/options/SPY")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ticker"]   == "SPY"
        assert body["provider"] == "mock"
        assert body["n_contracts"] > 0
        row = body["rows"][0]
        assert set(row.keys()) >= {
            "expiration", "strike", "option_type",
            "bid", "ask", "mid", "implied_volatility", "delta",
        }

    def test_options_iv_exposed_as_percentage(self, client):
        """The API must return IV as a percentage, not a decimal fraction."""
        resp = client.get("/api/data/options/SPY")
        body = resp.json()
        ivs = [r["implied_volatility"] for r in body["rows"] if r["implied_volatility"] is not None]
        assert all(v > 1.0 for v in ivs), "IV should be percent, not decimal fraction"

    def test_options_expiration_filter(self, client):
        # Get available expirations first
        all_resp  = client.get("/api/data/options/SPY")
        all_rows  = all_resp.json()["rows"]
        first_exp = all_rows[0]["expiration"]

        filtered = client.get(f"/api/data/options/SPY?expiration={first_exp}")
        assert filtered.status_code == 200
        for row in filtered.json()["rows"]:
            assert row["expiration"] == first_exp

    def test_options_type_filter(self, client):
        resp = client.get("/api/data/options/SPY?option_type=call")
        assert resp.status_code == 200
        for row in resp.json()["rows"]:
            assert row["option_type"] == "call"

    def test_options_near_atm_reduces_count(self, client):
        full    = client.get("/api/data/options/SPY")
        near    = client.get("/api/data/options/SPY?near_atm=true")
        assert full.json()["n_contracts"] >= near.json()["n_contracts"]

    def test_options_case_insensitive(self, client):
        resp = client.get("/api/data/options/spy")
        assert resp.status_code == 200
        assert resp.json()["ticker"] == "SPY"

    def test_iv_surface_endpoint_returns_200(self, client):
        resp = client.get("/api/data/iv-surface/SPY")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ticker"]  == "SPY"
        assert "iv_surface"    in body
        assert "curve"         in body
        assert "atm_contracts" in body

    def test_iv_surface_all_tenors_present(self, client):
        resp   = client.get("/api/data/iv-surface/SPY")
        surf   = resp.json()["iv_surface"]
        assert set(surf.keys()) == {"iv7", "iv14", "iv30", "iv60", "iv90"}
        for k, v in surf.items():
            if v is not None:
                assert 1.0 < v < 200.0, f"{k} = {v} out of plausible percent range"

    def test_iv_surface_curve_has_five_points(self, client):
        resp  = client.get("/api/data/iv-surface/SPY")
        curve = resp.json()["curve"]
        assert len(curve) == 5
        dtes  = [p["dte"] for p in curve]
        assert dtes == sorted(dtes), "Curve should be ordered by DTE"

    def test_iv_surface_unknown_ticker_real_provider(
        self, tmp_path, monkeypatch
    ):
        """Real provider + NOT_FOUND → 404."""
        monkeypatch.setenv("DATA_PROVIDER",    "real")
        monkeypatch.setenv("POLYGON_API_KEY",  "test_key")
        import app.data.real_provider as rmod
        monkeypatch.setattr(rmod, "_PRICES_CACHE",  tmp_path / "prices")
        monkeypatch.setattr(rmod, "_OPTIONS_CACHE", tmp_path / "options")

        from app.data import registry
        monkeypatch.setattr(registry, "_provider", None)
        registry._provider = None

        from fastapi.testclient import TestClient
        from app.main import app

        not_found = {"status": "NOT_FOUND", "results": None}
        with patch("requests.get", return_value=_mock_resp(not_found)):
            client = TestClient(app)
            resp   = client.get("/api/data/iv-surface/XXXX")

        assert resp.status_code == 404


# ===========================================================================
# IV surface helper (module-level function, not via HTTP)
# ===========================================================================

class TestCalculateATMIVByTenorIntegration:
    def test_mock_chain_produces_full_surface(self):
        """End-to-end: mock chain → five-tenor IV surface, all non-None."""
        from app.data._mock_impl import MockDataProvider, _BASE_PRICE
        from app.data.options_utils import calculate_atm_iv_by_tenor

        mp    = MockDataProvider()
        chain = mp.get_options_chain("SPY", _TODAY)
        surf  = calculate_atm_iv_by_tenor(chain, _BASE_PRICE["SPY"], _TODAY)

        for key in ("iv7", "iv14", "iv30", "iv60", "iv90"):
            assert surf[key] is not None, f"{key} should not be None"
            assert 5.0 < surf[key] < 150.0, f"{key} = {surf[key]} is outside [5, 150]%"

    def test_surface_term_structure_shape(self):
        """For a normal market, longer tenors should generally have higher IV."""
        from app.data._mock_impl import MockDataProvider, _BASE_PRICE
        from app.data.options_utils import calculate_atm_iv_by_tenor

        mp    = MockDataProvider()
        chain = mp.get_options_chain("SPY", _TODAY)
        surf  = calculate_atm_iv_by_tenor(chain, _BASE_PRICE["SPY"], _TODAY)

        iv7  = surf["iv7"]
        iv90 = surf["iv90"]
        # Normal contango: longer DTE has higher IV (not always guaranteed but
        # the mock is designed with contango surface)
        assert iv7  is not None
        assert iv90 is not None
        assert iv90 >= iv7 * 0.80, "iv90 should not be drastically below iv7"
