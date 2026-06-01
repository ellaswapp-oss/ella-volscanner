"""
tests/test_live_signal.py
--------------------------
Tests for Phase 2 live IV surface integration:
  - live_vol_service.build_live_snapshot (with mock provider)
  - live_vol_service._compute_macro_penalties
  - live_vol_service._iv_rank_proxy
  - snapshot_builder.build_snapshot dispatch
  - market endpoint integration (mock & real paths)
"""

from __future__ import annotations

import datetime
import math
import os
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers to build stub providers / chains
# ---------------------------------------------------------------------------

def _make_prices(n: int = 410, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0, 0.012, n)
    prices  = 100.0 * np.cumprod(1 + returns)
    idx     = pd.date_range(end="2024-06-30", periods=n, freq="B")
    return pd.Series(prices, index=idx, name="SPY")


def _make_chain(reference_date: datetime.date | None = None) -> pd.DataFrame:
    """
    Minimal options chain — 2 contracts per expiration, ATM.
    Expirations are relative to reference_date (default: today) so that
    calculate_atm_iv_by_tenor can find them regardless of when tests run.
    """
    base  = reference_date or datetime.date.today()
    rows  = []
    exps  = [base + datetime.timedelta(days=d) for d in [7, 14, 30, 60, 90]]
    for exp in exps:
        for opt_type in ("call", "put"):
            rows.append({
                "underlying_ticker": "SPY",
                "expiration":        exp.isoformat(),
                "strike":            100.0,   # ATM (underlying_price in snap = ~100)
                "option_type":       opt_type,
                "bid":               2.0,
                "ask":               2.20,
                "mid":               2.10,
                "last":              2.10,
                "implied_volatility": 20.0,   # 20% stored as percentage
                "delta":             0.50 if opt_type == "call" else -0.50,
                "gamma":             0.05,
                "theta":             -0.10,
                "vega":              0.20,
                "open_interest":     1000,
                "volume":            500,
            })
    return pd.DataFrame(rows)


def _make_provider(chain: pd.DataFrame | None = None, macro_raises: bool = False) -> MagicMock:
    """Build a stub provider with prices and options chain."""
    provider            = MagicMock()
    provider.name       = "real"
    provider.is_live    = True
    provider.get_prices_days.return_value = _make_prices()
    provider.get_options_chain.return_value = (
        _make_chain() if chain is None else chain
    )

    if macro_raises:
        provider.get_macro_series.side_effect = ValueError("FRED key missing")
    else:
        # Macro series: vix1m, vix3m, us10y, crude_oil with 50 rows
        dates = pd.date_range(end="2024-06-30", periods=50, freq="B")
        provider.get_macro_series.return_value = pd.DataFrame({
            "vix1m":         [18.0] * 50,
            "vix3m":         [19.0] * 50,
            "us10y":         [4.20] * 50,
            "crude_oil":     [80.0] * 50,
            "credit_spread": [3.5]  * 50,
            "breadth":       [60.0] * 50,
            "spy_price":     [100.0]* 50,
        }, index=dates)

    return provider


# ===========================================================================
# TestIVRankProxy
# ===========================================================================

class TestIVRankProxy(unittest.TestCase):
    """Unit tests for _iv_rank_proxy."""

    def setUp(self):
        from app.services.live_vol_service import _iv_rank_proxy
        self._fn = _iv_rank_proxy

    def test_current_at_top_returns_near_100(self):
        rv = pd.Series([10.0, 15.0, 18.0, 20.0, 25.0])
        rank = self._fn(25.0, rv)
        self.assertAlmostEqual(rank, 100.0, places=0)

    def test_current_at_bottom_returns_near_0(self):
        rv = pd.Series([10.0, 15.0, 18.0, 20.0, 25.0])
        rank = self._fn(10.0, rv)
        self.assertAlmostEqual(rank, 0.0, places=0)

    def test_current_in_middle(self):
        rv = pd.Series([10.0, 20.0])
        rank = self._fn(15.0, rv)
        self.assertAlmostEqual(rank, 50.0, places=1)

    def test_short_series_returns_50(self):
        rank = self._fn(20.0, pd.Series([18.0]))
        self.assertEqual(rank, 50.0)

    def test_empty_series_returns_50(self):
        rank = self._fn(20.0, pd.Series([], dtype=float))
        self.assertEqual(rank, 50.0)

    def test_lookback_applied(self):
        # 300 values all = 10.0, except last 252 where some = 30.0
        low  = [10.0] * 48
        high = [10.0] * 240 + [30.0] * 12
        rv   = pd.Series(low + high)
        rank = self._fn(30.0, rv, lookback=252)
        # Within lookback 252: min=10, max=30; current=30 → rank 100
        self.assertAlmostEqual(rank, 100.0, places=0)


# ===========================================================================
# TestComputeMacroPenalties
# ===========================================================================

class TestComputeMacroPenalties(unittest.TestCase):
    """Unit tests for _compute_macro_penalties."""

    def _run(self, **override_series) -> dict:
        from app.services.live_vol_service import _compute_macro_penalties
        dates  = pd.date_range(end="2024-06-30", periods=50, freq="B")
        defaults = {
            "vix1m":     pd.Series([18.0] * 50, index=dates),
            "vix3m":     pd.Series([19.0] * 50, index=dates),
            "us10y":     pd.Series([4.20] * 50, index=dates),
            "crude_oil": pd.Series([80.0] * 50, index=dates),
        }
        defaults.update(override_series)
        macro_df = pd.DataFrame(defaults, index=dates)
        provider = MagicMock()
        provider.get_macro_series.return_value = macro_df
        return _compute_macro_penalties(provider)

    def test_no_penalties_normal_market(self):
        result = self._run()
        self.assertEqual(result["total_penalty"], 0.0)
        self.assertEqual(result["reasons"], [])
        self.assertTrue(result["available"])

    def test_vix_inversion_penalty(self):
        dates = pd.date_range(end="2024-06-30", periods=50, freq="B")
        result = self._run(
            vix1m=pd.Series([22.0] * 50, index=dates),
            vix3m=pd.Series([18.0] * 50, index=dates),
        )
        self.assertIn(8.0, [result["total_penalty"]])
        self.assertTrue(any("inverted" in r for r in result["reasons"]))

    def test_vix_ge_32_penalty(self):
        dates = pd.date_range(end="2024-06-30", periods=50, freq="B")
        result = self._run(
            vix1m=pd.Series([35.0] * 50, index=dates),
            vix3m=pd.Series([36.0] * 50, index=dates),  # no inversion
        )
        self.assertEqual(result["total_penalty"], 10.0)
        self.assertTrue(any("32" in r for r in result["reasons"]))

    def test_vix_inversion_plus_ge32_capped(self):
        # 8 + 10 = 18, still under 25 cap
        dates = pd.date_range(end="2024-06-30", periods=50, freq="B")
        result = self._run(
            vix1m=pd.Series([35.0] * 50, index=dates),
            vix3m=pd.Series([32.0] * 50, index=dates),
        )
        self.assertEqual(result["total_penalty"], 18.0)

    def test_yield_spike_penalty(self):
        # iloc[-21] must be the old value; need >= 30 low values so position 29 = 4.00
        dates   = pd.date_range(end="2024-06-30", periods=50, freq="B")
        yields  = [4.00] * 30 + [4.50] * 20   # iloc[-21]=4.00, iloc[-1]=4.50 → +50bps
        result  = self._run(
            us10y=pd.Series(yields, index=dates),
        )
        self.assertIn(5.0, [result["total_penalty"]])
        self.assertTrue(any("10Y" in r or "yield" in r.lower() for r in result["reasons"]))

    def test_crude_shock_penalty(self):
        # iloc[-21] must be the old value; need >= 30 low values so position 29 = 80.0
        dates    = pd.date_range(end="2024-06-30", periods=50, freq="B")
        crude    = [80.0] * 30 + [90.0] * 20   # iloc[-21]=80.0, iloc[-1]=90.0 → +12.5%
        result   = self._run(
            crude_oil=pd.Series(crude, index=dates),
        )
        self.assertIn(5.0, [result["total_penalty"]])
        self.assertTrue(any("crude" in r.lower() for r in result["reasons"]))

    def test_cap_at_25_all_penalties(self):
        # Trigger all four penalties: 8 + 10 + 5 + 5 = 28 → capped at 25
        dates   = pd.date_range(end="2024-06-30", periods=50, freq="B")
        yields  = [4.00] * 30 + [4.50] * 20   # iloc[-21]=4.00, iloc[-1]=4.50 → +50bps
        crude   = [80.0] * 30 + [90.0] * 20   # iloc[-21]=80.0, iloc[-1]=90.0 → +12.5%
        result  = self._run(
            vix1m=pd.Series([35.0] * 50, index=dates),  # >= 32
            vix3m=pd.Series([32.0] * 50, index=dates),  # VIX > VIX3M
            us10y=pd.Series(yields, index=dates),
            crude_oil=pd.Series(crude, index=dates),
        )
        self.assertEqual(result["total_penalty"], 25.0)

    def test_macro_exception_returns_unavailable(self):
        from app.services.live_vol_service import _compute_macro_penalties
        provider = MagicMock()
        provider.get_macro_series.side_effect = ValueError("no key")
        result = _compute_macro_penalties(provider)
        self.assertEqual(result["total_penalty"], 0.0)
        self.assertFalse(result["available"])

    def test_yield_below_threshold_no_penalty(self):
        dates  = pd.date_range(end="2024-06-30", periods=50, freq="B")
        yields = [4.00] * 29 + [4.35] * 21   # +35bps — below 40bps threshold
        result = self._run(us10y=pd.Series(yields, index=dates))
        self.assertEqual(result["total_penalty"], 0.0)


# ===========================================================================
# TestBuildLiveSnapshot
# ===========================================================================

class TestBuildLiveSnapshot(unittest.TestCase):
    """Integration tests for build_live_snapshot using a stub provider."""

    def _snap(self, provider=None, **kw):
        from app.services.live_vol_service import build_live_snapshot
        p = provider or _make_provider(**kw)
        return build_live_snapshot("SPY", p)

    def test_returns_required_keys(self):
        snap = self._snap()
        required = {
            "ticker", "price", "rv", "iv", "iv_rank", "iv_rv_ratio",
            "vrp", "iv_rv_zscore", "term_structure", "skew",
            "regime_score", "signal", "sparklines",
            "provider", "iv_source", "iv_rank_is_proxy", "as_of_date",
            "macro_penalties", "iv_surface_metadata",
        }
        self.assertTrue(required.issubset(snap.keys()))

    def test_provider_is_real(self):
        snap = self._snap()
        self.assertEqual(snap["provider"], "real")

    def test_iv_source_live_options_chain(self):
        snap = self._snap()
        self.assertEqual(snap["iv_source"], "live_options_chain")

    def test_iv_rank_is_proxy_true(self):
        snap = self._snap()
        self.assertTrue(snap["iv_rank_is_proxy"])

    def test_iv_values_are_finite(self):
        snap = self._snap()
        for key in ("iv7", "iv30", "iv60", "iv90"):
            self.assertTrue(math.isfinite(snap["iv"][key]), f"{key} not finite")

    def test_iv14_present_when_chain_provides_it(self):
        snap = self._snap()
        # Chain has 14-day expiry; iv14 should be populated
        self.assertIsNotNone(snap["iv"].get("iv14"))

    def test_rv_values_are_finite(self):
        snap = self._snap()
        for key in ("rv10", "rv20", "rv30", "rv60"):
            self.assertTrue(math.isfinite(snap["rv"][key]), f"{key} not finite")

    def test_iv_rv_ratio_positive(self):
        snap = self._snap()
        self.assertGreater(snap["iv_rv_ratio"], 0)

    def test_iv_rank_in_range(self):
        snap = self._snap()
        self.assertGreaterEqual(snap["iv_rank"], 0)
        self.assertLessEqual(snap["iv_rank"], 100)

    def test_regime_score_in_range(self):
        snap = self._snap()
        self.assertGreaterEqual(snap["regime_score"], 0)
        self.assertLessEqual(snap["regime_score"], 100)

    def test_signal_is_valid(self):
        snap = self._snap()
        valid = {"BUY_PREMIUM", "NEUTRAL", "SELL_SELECTIVE", "SELL_AGGRESSIVE"}
        self.assertIn(snap["signal"]["signal"], valid)

    def test_sparklines_aligned(self):
        snap = self._snap()
        sp = snap["sparklines"]
        self.assertEqual(len(sp["dates"]), len(sp["rv30"]))
        self.assertEqual(len(sp["dates"]), len(sp["iv30"]))

    def test_fallback_on_chain_error(self):
        provider = _make_provider()
        provider.get_options_chain.side_effect = RuntimeError("Network error")
        snap = self._snap(provider=provider)
        self.assertEqual(snap["iv_source"], "synthetic_fallback")
        # Still returns valid IV values
        for key in ("iv7", "iv30", "iv60", "iv90"):
            self.assertTrue(math.isfinite(snap["iv"][key]))

    def test_no_macro_penalty_in_normal_market(self):
        snap = self._snap()
        self.assertEqual(snap["macro_penalties"]["total_penalty"], 0.0)
        self.assertTrue(snap["macro_penalties"]["available"])

    def test_macro_unavailable_zero_penalty(self):
        snap = self._snap(macro_raises=True)
        self.assertEqual(snap["macro_penalties"]["total_penalty"], 0.0)
        self.assertFalse(snap["macro_penalties"]["available"])
        # Score should still be computed (no penalty deducted)
        self.assertGreaterEqual(snap["regime_score"], 0)

    def test_macro_penalty_deducted_from_score(self):
        # Build a provider where VIX is inverted (8pt penalty)
        provider   = _make_provider()
        dates      = pd.date_range(end="2024-06-30", periods=50, freq="B")
        macro_df   = pd.DataFrame({
            "vix1m":         [22.0] * 50,
            "vix3m":         [18.0] * 50,   # inverted
            "us10y":         [4.20] * 50,
            "crude_oil":     [80.0] * 50,
            "credit_spread": [3.5]  * 50,
            "breadth":       [60.0] * 50,
            "spy_price":     [100.0]* 50,
        }, index=dates)
        provider.get_macro_series.return_value = macro_df

        from app.services.live_vol_service import build_live_snapshot
        snap = build_live_snapshot("SPY", provider)
        self.assertEqual(snap["macro_penalties"]["total_penalty"], 8.0)
        self.assertGreaterEqual(snap["regime_score"], 0)

    def test_as_of_date_is_today(self):
        snap = self._snap()
        self.assertEqual(snap["as_of_date"], datetime.date.today().isoformat())

    # -----------------------------------------------------------------------
    # IV surface metadata
    # -----------------------------------------------------------------------

    def test_iv_surface_metadata_present(self):
        snap = self._snap()
        meta = snap.get("iv_surface_metadata")
        self.assertIsNotNone(meta)
        for key in ("iv_surface_status", "required_tenor_available",
                    "available_tenors", "missing_tenors", "term_structure_quality"):
            self.assertIn(key, meta, f"iv_surface_metadata missing key: {key}")

    def test_iv_surface_metadata_full_chain(self):
        """Full chain with all expirations → healthy surface."""
        snap = self._snap()
        meta = snap["iv_surface_metadata"]
        self.assertTrue(meta["required_tenor_available"])
        self.assertEqual(meta["iv_surface_status"], "healthy")
        self.assertEqual(meta["term_structure_quality"], "full")
        self.assertIn("iv30", meta["available_tenors"])

    def test_partial_chain_missing_far_tenors(self):
        """Chain with only 7/14/30 DTE → iv60 and iv90 unavailable → partial surface."""
        base = datetime.date.today()
        chain = pd.DataFrame([
            {
                "underlying_ticker": "SPY",
                "expiration":        (base + datetime.timedelta(days=d)).isoformat(),
                "strike":            100.0,
                "option_type":       opt_type,
                "bid": 2.0, "ask": 2.20, "mid": 2.10, "last": 2.10,
                "implied_volatility": 20.0,
                "delta": 0.50 if opt_type == "call" else -0.50,
                "gamma": 0.05, "theta": -0.10, "vega": 0.20,
                "open_interest": 1000, "volume": 500,
            }
            for d in [7, 14, 30]
            for opt_type in ("call", "put")
        ])
        provider = _make_provider(chain=chain)
        snap     = self._snap(provider=provider)
        meta     = snap["iv_surface_metadata"]

        # iv30 is within ±10d tolerance (diff = 0)
        self.assertTrue(meta["required_tenor_available"])
        self.assertIn("iv30", meta["available_tenors"])
        # iv60 and iv90 are outside their tolerance thresholds
        self.assertIn("iv60", meta["missing_tenors"])
        self.assertIn("iv90", meta["missing_tenors"])
        self.assertEqual(meta["term_structure_quality"], "partial")
        self.assertEqual(meta["iv_surface_status"], "partial")
        # Exposed iv30 is a real value; iv60/iv90 are None
        self.assertIsNotNone(snap["iv"]["iv30"])
        self.assertIsNone(snap["iv"]["iv60"])
        self.assertIsNone(snap["iv"]["iv90"])
        # Regime score still computed from real iv30
        self.assertGreaterEqual(snap["regime_score"], 0)
        self.assertLessEqual(snap["regime_score"], 100)

    def test_no_iv30_in_chain(self):
        """Chain with no 20–40 DTE expirations → iv30 None → required_tenor_available False."""
        base = datetime.date.today()
        # Only 7d and 90d — nearest to 30d is 7d with DTE diff=23 > tolerance=10
        chain = pd.DataFrame([
            {
                "underlying_ticker": "SPY",
                "expiration":        (base + datetime.timedelta(days=d)).isoformat(),
                "strike":            100.0,
                "option_type":       opt_type,
                "bid": 2.0, "ask": 2.20, "mid": 2.10, "last": 2.10,
                "implied_volatility": 20.0,
                "delta": 0.50 if opt_type == "call" else -0.50,
                "gamma": 0.05, "theta": -0.10, "vega": 0.20,
                "open_interest": 1000, "volume": 500,
            }
            for d in [7, 90]
            for opt_type in ("call", "put")
        ])
        provider = _make_provider(chain=chain)
        snap     = self._snap(provider=provider)
        meta     = snap["iv_surface_metadata"]

        self.assertFalse(meta["required_tenor_available"])
        self.assertNotIn("iv30", meta["available_tenors"])
        self.assertIn("iv30", meta["missing_tenors"])
        self.assertEqual(meta["iv_surface_status"], "unavailable")
        self.assertIsNone(snap["iv"]["iv30"])
        # Regime score still computed (uses RV-based estimate for iv30)
        self.assertGreaterEqual(snap["regime_score"], 0)
        self.assertLessEqual(snap["regime_score"], 100)

    def test_fallback_iv_surface_metadata(self):
        """Chain fetch failure → synthetic_fallback metadata."""
        provider = _make_provider()
        provider.get_options_chain.side_effect = RuntimeError("Network error")
        snap = self._snap(provider=provider)
        meta = snap["iv_surface_metadata"]
        self.assertEqual(meta["iv_surface_status"], "synthetic_fallback")
        self.assertFalse(meta["required_tenor_available"])


# ===========================================================================
# TestSnapshotBuilderDispatch
# ===========================================================================

class TestSnapshotBuilderDispatch(unittest.TestCase):
    """Tests snapshot_builder routes to the correct service."""

    def test_mock_path_returns_metadata_defaults(self):
        """Mock provider path injects metadata fields with defaults."""
        with patch.dict(os.environ, {"DATA_PROVIDER": "mock"}):
            from app.data import registry
            registry._reset_provider()
            from app.services import snapshot_builder
            # Force reimport to pick up reset provider
            snap = snapshot_builder.build_snapshot("SPY")
            registry._reset_provider()

        self.assertEqual(snap["provider"],         "mock")
        self.assertEqual(snap["iv_source"],        "synthetic")
        self.assertFalse(snap["iv_rank_is_proxy"])
        self.assertIsNone(snap["iv"]["iv14"])
        self.assertIn("total_penalty", snap["macro_penalties"])

        # iv_surface_metadata defaults for mock path
        meta = snap.get("iv_surface_metadata")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["iv_surface_status"], "healthy")
        self.assertTrue(meta["required_tenor_available"])
        self.assertEqual(meta["term_structure_quality"], "full")

    def test_mock_path_ticker_data_present(self):
        with patch.dict(os.environ, {"DATA_PROVIDER": "mock"}):
            from app.data import registry
            registry._reset_provider()
            from app.services import snapshot_builder
            snap = snapshot_builder.build_snapshot("SPY")
            registry._reset_provider()

        required = {"ticker", "price", "rv", "iv", "iv_rank", "regime_score", "signal"}
        self.assertTrue(required.issubset(snap.keys()))

    def test_real_path_calls_live_service(self):
        """Real provider path calls build_live_snapshot."""
        mock_provider  = _make_provider()
        mock_live_snap = {
            "ticker": "SPY", "price": 100.0,
            "rv": {}, "iv": {}, "skew": {},
            "iv_rank": 50.0, "iv_rv_ratio": 1.1, "vrp": 0.05,
            "iv_rv_zscore": 0.5, "term_structure": {}, "sparklines": {},
            "regime_score": 60.0, "signal": {},
            "provider": "real", "iv_source": "live_options_chain",
            "iv_rank_is_proxy": True, "as_of_date": "2024-06-30",
            "macro_penalties": {"total_penalty": 0.0, "reasons": [], "available": True},
            "iv_surface_metadata": {
                "iv_surface_status": "healthy",
                "required_tenor_available": True,
                "available_tenors": ["iv7", "iv14", "iv30", "iv60", "iv90"],
                "missing_tenors": [],
                "term_structure_quality": "full",
            },
        }

        # snapshot_builder does a module-level `from app.data.registry import get_provider`
        # so we must patch the name in snapshot_builder's namespace, not in registry.
        with patch("app.services.snapshot_builder.get_provider", return_value=mock_provider), \
             patch("app.services.live_vol_service.build_live_snapshot",
                   return_value=mock_live_snap) as mock_live:
            from app.services import snapshot_builder
            snap = snapshot_builder.build_snapshot("SPY")

        mock_live.assert_called_once_with("SPY", mock_provider)
        self.assertEqual(snap["provider"], "real")


# ===========================================================================
# TestMarketEndpointsMockPath
# ===========================================================================

class TestMarketEndpointsMockPath(unittest.TestCase):
    """Integration tests for market endpoints using mock provider."""

    def setUp(self):
        # Ensure mock provider is active
        os.environ.pop("DATA_PROVIDER", None)
        from app.data import registry
        registry._reset_provider()

        from app.main import app
        self.client = TestClient(app)

    def tearDown(self):
        from app.data import registry
        registry._reset_provider()

    def test_volatility_mock_has_metadata(self):
        r = self.client.get("/api/volatility/SPY")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("provider",          data)
        self.assertIn("iv_source",         data)
        self.assertIn("iv_rank_is_proxy",  data)
        self.assertIn("as_of_date",        data)

    def test_volatility_mock_provider_is_mock(self):
        r = self.client.get("/api/volatility/SPY")
        self.assertEqual(r.json()["provider"], "mock")

    def test_volatility_mock_iv_rank_not_proxy(self):
        r = self.client.get("/api/volatility/SPY")
        self.assertFalse(r.json()["iv_rank_is_proxy"])

    def test_term_structure_has_metadata(self):
        r = self.client.get("/api/term-structure/SPY")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("provider",  data)
        self.assertIn("iv_source", data)

    def test_term_structure_no_14d_for_mock(self):
        r = self.client.get("/api/term-structure/SPY")
        curve = r.json()["curve"]
        tenors = [pt["tenor"] for pt in curve]
        self.assertNotIn("14d", tenors)

    def test_trade_signal_has_metadata(self):
        r = self.client.get("/api/trade-signal/SPY")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("provider",         data)
        self.assertIn("iv_source",        data)
        self.assertIn("iv_rank_is_proxy", data)
        self.assertIn("as_of_date",       data)

    def test_trade_signal_macro_penalties_present(self):
        r = self.client.get("/api/trade-signal/SPY")
        data = r.json()
        # Mock path sets macro_penalties (even though unavailable=False)
        self.assertIn("macro_penalties", data)

    def test_volatility_iv_has_iv14_null_for_mock(self):
        r = self.client.get("/api/volatility/SPY")
        iv = r.json()["iv"]
        self.assertIsNone(iv.get("iv14"))

    def test_invalid_ticker_404(self):
        r = self.client.get("/api/volatility/FAKE")
        self.assertEqual(r.status_code, 404)

    def test_volatility_mock_has_iv_surface_metadata(self):
        r = self.client.get("/api/volatility/SPY")
        data = r.json()
        self.assertIn("iv_surface_metadata", data)
        meta = data["iv_surface_metadata"]
        self.assertIsNotNone(meta)
        self.assertIn("iv_surface_status",        meta)
        self.assertIn("required_tenor_available",  meta)
        self.assertIn("available_tenors",          meta)
        self.assertIn("missing_tenors",            meta)
        self.assertIn("term_structure_quality",    meta)

    def test_trade_signal_mock_has_iv_surface_metadata(self):
        r = self.client.get("/api/trade-signal/SPY")
        self.assertIn("iv_surface_metadata", r.json())

    def test_term_structure_mock_has_iv_surface_metadata(self):
        r = self.client.get("/api/term-structure/SPY")
        self.assertIn("iv_surface_metadata", r.json())


# ===========================================================================
# TestMarketEndpointsRealPath
# ===========================================================================

class TestMarketEndpointsRealPath(unittest.TestCase):
    """
    Integration tests for market endpoints using a patched real provider.
    No network calls are made — the provider is fully mocked.

    Note: snapshot_builder does a module-level `from app.data.registry import
    get_provider`, so we must patch `app.services.snapshot_builder.get_provider`
    (the name in snapshot_builder's namespace) rather than the registry module.
    """

    def setUp(self):
        self._provider = _make_provider()
        from app.main import app
        self._client = TestClient(app)

    def _req(self, path: str, provider=None):
        p = provider or self._provider
        with patch("app.services.snapshot_builder.get_provider", return_value=p):
            return self._client.get(path)

    def test_volatility_real_path_provider_real(self):
        r = self._req("/api/volatility/SPY")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["provider"], "real")

    def test_volatility_real_path_iv_source(self):
        r = self._req("/api/volatility/SPY")
        self.assertIn(r.json()["iv_source"], ("live_options_chain", "synthetic_fallback"))

    def test_volatility_real_path_iv_rank_is_proxy(self):
        r = self._req("/api/volatility/SPY")
        self.assertTrue(r.json()["iv_rank_is_proxy"])

    def test_trade_signal_real_path_has_macro_penalties(self):
        r = self._req("/api/trade-signal/SPY")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("macro_penalties", data)
        mp = data["macro_penalties"]
        self.assertIn("total_penalty", mp)
        self.assertIn("reasons",       mp)
        self.assertIn("available",     mp)

    def test_term_structure_real_path_has_14d(self):
        """14d curve point is present when iv14 is available from the stub chain."""
        r      = self._req("/api/term-structure/SPY")
        self.assertEqual(r.status_code, 200)
        tenors = [pt["tenor"] for pt in r.json()["curve"]]
        self.assertIn("14d", tenors)

    def test_trade_signal_regime_score_adjusted_for_penalty(self):
        """Regime score is reduced when macro penalty is applied."""
        # Inverted VIX curve → 8pt penalty
        provider = _make_provider()
        dates    = pd.date_range(end="2024-06-30", periods=50, freq="B")
        macro_df = pd.DataFrame({
            "vix1m":         [22.0] * 50,
            "vix3m":         [18.0] * 50,   # inverted
            "us10y":         [4.20] * 50,
            "crude_oil":     [80.0] * 50,
            "credit_spread": [3.5]  * 50,
            "breadth":       [60.0] * 50,
            "spy_price":     [100.0]* 50,
        }, index=dates)
        provider.get_macro_series.return_value = macro_df

        r    = self._req("/api/trade-signal/SPY", provider=provider)
        data = r.json()
        self.assertEqual(data["macro_penalties"]["total_penalty"], 8.0)
        self.assertGreaterEqual(data["regime_score"], 0.0)


# ===========================================================================
# TestSchemaValidation
# ===========================================================================

class TestSchemaValidation(unittest.TestCase):
    """Pydantic schema round-trip tests for new fields."""

    def test_iv_metrics_iv14_optional(self):
        from app.models.schemas import IVMetrics
        # Without iv14
        m = IVMetrics(iv7=18.0, iv30=20.0, iv60=21.0, iv90=22.0)
        self.assertIsNone(m.iv14)

    def test_iv_metrics_iv14_with_value(self):
        from app.models.schemas import IVMetrics
        m = IVMetrics(iv7=18.0, iv14=19.0, iv30=20.0, iv60=21.0, iv90=22.0)
        self.assertEqual(m.iv14, 19.0)

    def test_macro_penalty_model(self):
        from app.models.schemas import MacroPenalty
        mp = MacroPenalty(total_penalty=8.0, reasons=["VIX inverted"], available=True)
        self.assertEqual(mp.total_penalty, 8.0)
        self.assertEqual(len(mp.reasons), 1)

    def test_trade_signal_response_with_macro_penalty(self):
        from app.models.schemas import (
            MacroPenalty, TradeSignal, TradeSignalResponse, SignalFactor
        )
        sig = TradeSignal(
            signal="SELL_SELECTIVE",
            label="Sell Selectively",
            color="orange",
            description="test",
        )
        mp = MacroPenalty(total_penalty=8.0, reasons=["VIX inverted"], available=True)
        sf = SignalFactor(value=60.0, weight=0.35, contribution="high")
        resp = TradeSignalResponse(
            ticker="SPY",
            regime_score=55.0,
            signal=sig,
            factors={"iv_rank": sf, "vrp": sf, "iv_rv_ratio": sf, "z_score": sf},
            playbook=["test play"],
            macro_penalties=mp,
            provider="real",
            iv_source="live_options_chain",
            iv_rank_is_proxy=True,
            as_of_date="2024-06-30",
        )
        self.assertEqual(resp.macro_penalties.total_penalty, 8.0)
        self.assertTrue(resp.iv_rank_is_proxy)

    def test_volatility_response_metadata_defaults(self):
        from app.models.schemas import VolatilityResponse, IVMetrics, RVMetrics, Sparklines
        iv = IVMetrics(iv7=18.0, iv30=20.0, iv60=21.0, iv90=22.0)
        rv = RVMetrics(rv10=16.0, rv20=17.0, rv30=18.0, rv60=19.0)
        sp = Sparklines(dates=[], rv30=[], iv30=[])
        r  = VolatilityResponse(
            ticker="SPY", price=100.0, iv=iv, rv=rv,
            iv_rank=55.0, iv_rv_ratio=1.1, vrp=0.05, iv_rv_zscore=0.5,
            sparklines=sp,
        )
        self.assertEqual(r.provider, "mock")
        self.assertEqual(r.iv_source, "synthetic")
        self.assertFalse(r.iv_rank_is_proxy)
        self.assertIsNone(r.iv_surface_metadata)

    def test_iv_metrics_optional_iv7_iv60_iv90(self):
        """iv7, iv60, iv90 are Optional — only iv14 was Optional before."""
        from app.models.schemas import IVMetrics
        # All optional except iv30 was required — now iv30 is also Optional
        m = IVMetrics(iv30=20.0)
        self.assertIsNone(m.iv7)
        self.assertIsNone(m.iv60)
        self.assertIsNone(m.iv90)
        self.assertIsNone(m.iv14)

    def test_iv_metrics_iv30_optional(self):
        """iv30 can also be None (when not available from chain)."""
        from app.models.schemas import IVMetrics
        m = IVMetrics()
        self.assertIsNone(m.iv30)

    def test_iv_surface_metadata_model(self):
        from app.models.schemas import IVSurfaceMetadata
        meta = IVSurfaceMetadata(
            iv_surface_status="partial",
            required_tenor_available=True,
            available_tenors=["iv7", "iv30"],
            missing_tenors=["iv14", "iv60", "iv90"],
            term_structure_quality="partial",
        )
        self.assertEqual(meta.iv_surface_status, "partial")
        self.assertTrue(meta.required_tenor_available)
        self.assertIn("iv30", meta.available_tenors)

    def test_term_structure_response_optional_slopes(self):
        """TermStructureResponse accepts None for all slope fields."""
        from app.models.schemas import TermStructureResponse
        r = TermStructureResponse(
            ticker="SPY",
            curve=[],
            shape="partial",
        )
        self.assertIsNone(r.front_slope)
        self.assertIsNone(r.mid_slope)
        self.assertIsNone(r.back_slope)
        self.assertIsNone(r.total_slope)
        self.assertIsNone(r.curvature)

    def test_volatility_response_with_iv_surface_metadata(self):
        from app.models.schemas import (
            VolatilityResponse, IVMetrics, RVMetrics, Sparklines, IVSurfaceMetadata
        )
        iv   = IVMetrics(iv7=17.0, iv30=20.0)  # iv60/iv90 omitted → None
        rv   = RVMetrics(rv10=16.0, rv20=17.0, rv30=18.0, rv60=19.0)
        sp   = Sparklines(dates=[], rv30=[], iv30=[])
        meta = IVSurfaceMetadata(
            iv_surface_status="partial",
            required_tenor_available=True,
            available_tenors=["iv7", "iv30"],
            missing_tenors=["iv14", "iv60", "iv90"],
            term_structure_quality="partial",
        )
        r = VolatilityResponse(
            ticker="SPY", price=100.0, iv=iv, rv=rv,
            iv_rank=50.0, iv_rv_ratio=1.1, vrp=0.05, iv_rv_zscore=0.3,
            sparklines=sp, iv_surface_metadata=meta,
        )
        self.assertIsNone(r.iv.iv60)
        self.assertEqual(r.iv_surface_metadata.iv_surface_status, "partial")


if __name__ == "__main__":
    unittest.main()
