"""
tests/test_research_readiness.py
---------------------------------
Tests for GET /api/research/readiness.

Coverage
--------
  _readiness_level()       — boundary conditions at 0, 62, 63, 99, 100, 251, 252
  _cal_days()              — conversion accuracy
  _ticker_readiness()      — empty store, real rows, valid vs invalid iv30
  _universe_summary()      — min-binding, label propagation
  _module_status()         — AVAILABLE / PARTIAL / NOT_READY
  _build_modules()         — correct universe binding per module
  research_readiness()     — full endpoint, top-level keys present
  HTTP GET /api/research/readiness — FastAPI integration
"""

from __future__ import annotations

import datetime
import math
import unittest.mock as mock

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.api.research import (
    _WARMUP, _USABLE, _STRONG, _IDEAL,
    _ETF_TICKERS, _SINGLE_TICKERS, _ALL_TICKERS,
    _readiness_level,
    _cal_days,
    _ticker_readiness,
    _universe_summary,
    _module_status,
    _build_modules,
    research_readiness,
)
from app.main import app

client = TestClient(app)

# ---------------------------------------------------------------------------
# _readiness_level
# ---------------------------------------------------------------------------

class TestReadinessLevel:
    def test_zero(self):
        assert _readiness_level(0) == "NOT_READY"

    def test_just_below_warmup(self):
        assert _readiness_level(_WARMUP - 1) == "NOT_READY"

    def test_at_warmup(self):
        assert _readiness_level(_WARMUP) == "EARLY"

    def test_just_below_usable(self):
        assert _readiness_level(_USABLE - 1) == "EARLY"

    def test_at_usable(self):
        assert _readiness_level(_USABLE) == "USABLE"

    def test_just_below_strong(self):
        assert _readiness_level(_STRONG - 1) == "USABLE"

    def test_at_strong(self):
        assert _readiness_level(_STRONG) == "STRONG"

    def test_above_strong(self):
        assert _readiness_level(1000) == "STRONG"


# ---------------------------------------------------------------------------
# _cal_days
# ---------------------------------------------------------------------------

class TestCalDays:
    def test_zero(self):
        assert _cal_days(0) == 0

    def test_five_trading_is_seven_cal(self):
        assert _cal_days(5) == 7

    def test_rounding_up(self):
        # 1 trading day → ceil(1 * 7/5) = ceil(1.4) = 2
        assert _cal_days(1) == 2

    def test_63_trading(self):
        assert _cal_days(63) == math.ceil(63 * 7 / 5)


# ---------------------------------------------------------------------------
# _module_status
# ---------------------------------------------------------------------------

class TestModuleStatus:
    def test_zero_rows(self):
        assert _module_status(0, 63) == "NOT_READY"

    def test_partial(self):
        assert _module_status(30, 63) == "PARTIAL"

    def test_exactly_meets_requirement(self):
        assert _module_status(63, 63) == "AVAILABLE"

    def test_exceeds_requirement(self):
        assert _module_status(300, 63) == "AVAILABLE"

    def test_zero_requirement_with_zero_rows(self):
        # Live dashboard — always available
        assert _module_status(0, 0) == "AVAILABLE"


# ---------------------------------------------------------------------------
# _ticker_readiness — with mocked iv_store
# ---------------------------------------------------------------------------

def _make_store_df(rows: list[dict]) -> pd.DataFrame:
    """Build a DataFrame in the same shape iv_store.get_history() returns."""
    if not rows:
        return pd.DataFrame(columns=["date", "ticker", "iv7", "iv14", "iv30",
                                     "iv60", "iv90", "source", "created_at", "iv_data_quality"])
    df = pd.DataFrame(rows)
    if "iv_data_quality" not in df.columns:
        quality_map = {
            "polygon_historical_options": "real",
            "polygon_live_snapshot": "real",
            "synthetic_fallback": "synthetic_fallback",
            "interpolated": "interpolated",
        }
        df["iv_data_quality"] = df["source"].map(quality_map).fillna("synthetic_fallback")
    return df


class TestTickerReadiness:
    def _patch(self, df: pd.DataFrame):
        return mock.patch(
            "app.api.research._iv_store.get_history",
            return_value=df,
        )

    def test_empty_store(self):
        empty = _make_store_df([])
        with self._patch(empty):
            r = _ticker_readiness("SPY")
        assert r["real_rows"] == 0
        assert r["valid_iv30_rows"] == 0
        assert r["readiness"] == "NOT_READY"
        assert r["days_until_warmup_63"] == _WARMUP
        assert r["first_real_date"] is None

    def test_real_rows_with_valid_iv30(self):
        rows = [
            {
                "date": f"2026-0{m}-{d:02d}",
                "ticker": "SPY",
                "iv7": 14.0, "iv14": 14.5, "iv30": 15.0, "iv60": 16.0, "iv90": 17.0,
                "source": "polygon_historical_options",
                "created_at": "2026-05-01T00:00:00Z",
            }
            for (m, d) in [(4, 17), (4, 18), (4, 19), (4, 20), (4, 21)]  # 5 rows
        ]
        df = _make_store_df(rows)
        with self._patch(df):
            r = _ticker_readiness("SPY")
        assert r["real_rows"] == 5
        assert r["valid_iv30_rows"] == 5
        assert r["days_until_warmup_63"] == 58
        assert r["readiness"] == "NOT_READY"
        assert r["first_real_date"] == "2026-04-17"
        assert r["latest_real_date"] == "2026-04-21"

    def test_real_rows_with_nan_iv30_excluded(self):
        import math
        rows = [
            {
                "date": "2026-04-17",
                "ticker": "SPY",
                "iv7": 14.0, "iv14": float("nan"), "iv30": float("nan"),
                "iv60": 16.0, "iv90": 17.0,
                "source": "polygon_historical_options",
                "created_at": "2026-05-01T00:00:00Z",
            },
            {
                "date": "2026-04-18",
                "ticker": "SPY",
                "iv7": 14.0, "iv14": float("nan"), "iv30": 15.0,
                "iv60": 16.0, "iv90": 17.0,
                "source": "polygon_historical_options",
                "created_at": "2026-05-01T00:00:00Z",
            },
        ]
        df = _make_store_df(rows)
        with self._patch(df):
            r = _ticker_readiness("SPY")
        assert r["real_rows"] == 2          # both rows are "real" quality
        assert r["valid_iv30_rows"] == 1    # only the one with non-NaN iv30

    def test_synthetic_rows_excluded(self):
        rows = [
            {
                "date": "2026-04-17",
                "ticker": "SPY",
                "iv7": 14.0, "iv14": float("nan"), "iv30": 15.0,
                "iv60": 16.0, "iv90": 17.0,
                "source": "synthetic_fallback",
                "created_at": "2026-05-01T00:00:00Z",
            },
        ]
        df = _make_store_df(rows)
        with self._patch(df):
            r = _ticker_readiness("SPY")
        assert r["real_rows"] == 0
        assert r["valid_iv30_rows"] == 0

    def test_readiness_at_strong_threshold(self):
        rows = [
            {
                "date": f"2025-01-{d:02d}",
                "ticker": "SPY",
                "iv7": 14.0, "iv14": 14.5, "iv30": 15.0, "iv60": 16.0, "iv90": 17.0,
                "source": "polygon_historical_options",
                "created_at": "2026-01-01T00:00:00Z",
            }
            for d in range(1, 32)
        ] * 9   # 279 rows — above STRONG threshold
        df = _make_store_df(rows[:_STRONG])  # exactly 252
        with self._patch(df):
            r = _ticker_readiness("SPY")
        assert r["valid_iv30_rows"] == _STRONG
        assert r["readiness"] == "STRONG"
        assert r["days_until_warmup_63"] == 0
        assert r["days_until_100"] == 0
        assert r["days_until_252"] == 0


# ---------------------------------------------------------------------------
# _universe_summary
# ---------------------------------------------------------------------------

class TestUniverseSummary:
    def _make_ticker_map(self, rows_by_ticker: dict[str, int]) -> dict:
        return {
            t: {"valid_iv30_rows": n, "readiness": _readiness_level(n)}
            for t, n in rows_by_ticker.items()
        }

    def test_min_binding(self):
        m = self._make_ticker_map({"SPY": 80, "QQQ": 30, "IWM": 100})
        r = _universe_summary(["SPY", "QQQ", "IWM"], "ETF", m)
        assert r["min_valid_iv30_rows"] == 30
        assert r["readiness"] == "NOT_READY"

    def test_all_strong(self):
        m = self._make_ticker_map({"SPY": 300, "QQQ": 280, "IWM": 252})
        r = _universe_summary(["SPY", "QQQ", "IWM"], "ETF", m)
        assert r["min_valid_iv30_rows"] == 252
        assert r["readiness"] == "STRONG"

    def test_label_preserved(self):
        m = self._make_ticker_map({"X": 10})
        r = _universe_summary(["X"], "My Label", m)
        assert r["label"] == "My Label"

    def test_tickers_preserved(self):
        m = self._make_ticker_map({"A": 1, "B": 2})
        r = _universe_summary(["A", "B"], "Test", m)
        assert r["tickers"] == ["A", "B"]


# ---------------------------------------------------------------------------
# _build_modules
# ---------------------------------------------------------------------------

class TestBuildModules:
    def _ticker_map_with_etf_rows(self, etf_n: int, single_n: int = 0) -> dict:
        d = {}
        for t in _ETF_TICKERS:
            d[t] = {"valid_iv30_rows": etf_n, "readiness": _readiness_level(etf_n)}
        for t in _SINGLE_TICKERS:
            d[t] = {"valid_iv30_rows": single_n, "readiness": _readiness_level(single_n)}
        return d

    def test_live_dashboard_always_available(self):
        m = _build_modules(self._ticker_map_with_etf_rows(0))
        live = next(x for x in m if x["name"] == "Live Dashboard")
        assert live["status"] == "AVAILABLE"

    def test_diagnostics_available_with_one_row(self):
        m = _build_modules(self._ticker_map_with_etf_rows(1))
        diag = next(x for x in m if x["name"] == "Real-IV Diagnostics")
        assert diag["status"] == "AVAILABLE"

    def test_diagnostics_not_ready_with_zero_rows(self):
        m = _build_modules(self._ticker_map_with_etf_rows(0, 0))
        diag = next(x for x in m if x["name"] == "Real-IV Diagnostics")
        assert diag["status"] == "NOT_READY"

    def test_backtest_partial_below_warmup(self):
        m = _build_modules(self._ticker_map_with_etf_rows(30))
        bt = next(x for x in m if x["name"] == "IV/RV Backtest")
        assert bt["status"] == "PARTIAL"
        assert bt["rows_required"] == _WARMUP

    def test_backtest_available_at_warmup(self):
        m = _build_modules(self._ticker_map_with_etf_rows(_WARMUP))
        bt = next(x for x in m if x["name"] == "IV/RV Backtest")
        assert bt["status"] == "AVAILABLE"

    def test_walk_forward_not_ready_below_strong(self):
        m = _build_modules(self._ticker_map_with_etf_rows(100))
        wf = next(x for x in m if x["name"] == "Walk-Forward Validation")
        assert wf["status"] == "PARTIAL"
        assert wf["rows_required"] == _STRONG

    def test_walk_forward_available_at_strong(self):
        m = _build_modules(self._ticker_map_with_etf_rows(_STRONG))
        wf = next(x for x in m if x["name"] == "Walk-Forward Validation")
        assert wf["status"] == "AVAILABLE"

    def test_five_modules_returned(self):
        m = _build_modules(self._ticker_map_with_etf_rows(0))
        assert len(m) == 5

    def test_all_modules_have_required_keys(self):
        m = _build_modules(self._ticker_map_with_etf_rows(50))
        required_keys = {"name", "description", "universe", "rows_required",
                         "rows_current", "status", "notes"}
        for mod in m:
            assert required_keys.issubset(mod.keys()), f"Module {mod['name']} missing keys"


# ---------------------------------------------------------------------------
# research_readiness() — full function
# ---------------------------------------------------------------------------

class TestResearchReadiness:
    def test_top_level_keys(self):
        result = research_readiness()
        assert set(result.keys()) == {"as_of", "thresholds", "tickers", "universes", "modules"}

    def test_as_of_is_today(self):
        result = research_readiness()
        assert result["as_of"] == datetime.date.today().isoformat()

    def test_thresholds_values(self):
        result = research_readiness()
        t = result["thresholds"]
        assert t["warmup_63"]  == 63
        assert t["usable_100"] == 100
        assert t["strong_252"] == 252
        assert t["ideal_504"]  == 504

    def test_all_tickers_present(self):
        result = research_readiness()
        returned = {r["ticker"] for r in result["tickers"]}
        assert returned == set(_ALL_TICKERS)

    def test_universes_keys(self):
        result = research_readiness()
        assert set(result["universes"].keys()) == {"etf", "single_name", "all"}

    def test_etf_universe_tickers(self):
        result = research_readiness()
        assert result["universes"]["etf"]["tickers"] == _ETF_TICKERS

    def test_single_name_universe_tickers(self):
        result = research_readiness()
        assert result["universes"]["single_name"]["tickers"] == _SINGLE_TICKERS

    def test_ticker_result_keys(self):
        result = research_readiness()
        expected = {
            "ticker", "real_rows", "valid_iv30_rows", "first_real_date",
            "latest_real_date", "usable_trading_days",
            "days_until_warmup_63", "days_until_100", "days_until_252",
            "cal_days_until_warmup_63", "cal_days_until_100", "cal_days_until_252",
            "readiness",
        }
        for t in result["tickers"]:
            assert expected.issubset(t.keys()), f"Ticker {t['ticker']} missing keys"

    def test_days_until_non_negative(self):
        result = research_readiness()
        for t in result["tickers"]:
            assert t["days_until_warmup_63"] >= 0
            assert t["days_until_100"] >= 0
            assert t["days_until_252"] >= 0

    def test_cal_days_geq_trading_days(self):
        result = research_readiness()
        for t in result["tickers"]:
            assert t["cal_days_until_warmup_63"] >= t["days_until_warmup_63"]

    def test_valid_iv30_lte_real_rows(self):
        result = research_readiness()
        for t in result["tickers"]:
            assert t["valid_iv30_rows"] <= t["real_rows"]

    def test_readiness_consistent_with_valid_rows(self):
        result = research_readiness()
        for t in result["tickers"]:
            expected = _readiness_level(t["valid_iv30_rows"])
            assert t["readiness"] == expected, (
                f"{t['ticker']}: readiness={t['readiness']} but "
                f"valid_iv30={t['valid_iv30_rows']} → expected {expected}"
            )

    def test_five_modules(self):
        result = research_readiness()
        assert len(result["modules"]) == 5

    def test_module_statuses_are_valid(self):
        result = research_readiness()
        valid = {"AVAILABLE", "PARTIAL", "NOT_READY"}
        for m in result["modules"]:
            assert m["status"] in valid


# ---------------------------------------------------------------------------
# HTTP integration
# ---------------------------------------------------------------------------

class TestHTTPEndpoint:
    def test_get_200(self):
        r = client.get("/api/research/readiness")
        assert r.status_code == 200

    def test_response_is_json(self):
        r = client.get("/api/research/readiness")
        body = r.json()
        assert isinstance(body, dict)

    def test_response_has_all_top_keys(self):
        body = client.get("/api/research/readiness").json()
        assert "as_of"      in body
        assert "thresholds" in body
        assert "tickers"    in body
        assert "universes"  in body
        assert "modules"    in body

    def test_tickers_count(self):
        body = client.get("/api/research/readiness").json()
        assert len(body["tickers"]) == len(_ALL_TICKERS)

    def test_modules_count(self):
        body = client.get("/api/research/readiness").json()
        assert len(body["modules"]) == 5

    def test_live_dashboard_always_available_via_http(self):
        body = client.get("/api/research/readiness").json()
        live = next(m for m in body["modules"] if m["name"] == "Live Dashboard")
        assert live["status"] == "AVAILABLE"

    def test_universe_etf_min_lte_all_tickers(self):
        body = client.get("/api/research/readiness").json()
        etf_min = body["universes"]["etf"]["min_valid_iv30_rows"]
        for t in body["tickers"]:
            if t["ticker"] in _ETF_TICKERS:
                assert etf_min <= t["valid_iv30_rows"]
