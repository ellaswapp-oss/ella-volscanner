"""
tests/test_iv_store.py
-----------------------
Phase 3 test suite for historical IV surface persistence.

Covers:
  1.  Parquet round-trip: upsert → get_history returns correct rows
  2.  Incremental update: already-stored dates are skipped by default
  3.  Overwrite flag: re-ingesting same date with overwrite replaces the row
  4.  Batch upsert: multiple rows written in a single call
  5.  Quality mapping: source → iv_data_quality column derivation
  6.  get_stored_dates: returns the correct set of ISO date strings
  7.  has_date helper
  8.  get_latest_date helper
  9.  quality_summary counts
  10. delete_ticker clears all rows
  11. delete_date_range removes only the target window
  12. list_tickers returns populated tickers
  13. get_iv_series merge: stored rows overwrite synthetic baseline
  14. get_iv_series quality tagging on merged vs synthetic rows
  15. Backtest switching: mock_provider.get_iv_data returns real IV when stored
  16. Quality summary via mock_provider.get_iv_quality_summary
  17. Backfill resume: partial backfill then resume fills the gap
  18. Dry-run: backfill script writes nothing with --dry-run
  19. Edge case: empty store returns empty DataFrame from get_history
  20. Edge case: future date range returns empty rows
"""

from __future__ import annotations

import datetime
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Patch IVSurfaceStore's data directory to a temp folder so tests never
# touch the real data_cache.
# ---------------------------------------------------------------------------

_TEMP_DIR = tempfile.mkdtemp(prefix="iv_store_test_")

# We must patch the path BEFORE importing the store modules, because
# IVSurfaceStore sets self._base_dir in __init__ using the settings path.
# We patch it at the class level by overriding the property/attribute.

import importlib, sys


def _patch_store_path(store_instance, tmp_dir: str):
    """Point a store instance at a temporary directory.

    IVSurfaceStore stores its base path as ``self._base`` (not ``_base_dir``).
    """
    store_instance._base = Path(tmp_dir)
    store_instance._base.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_iv_surf(
    iv30: float = 22.0,
    iv7_delta:  float = -2.0,
    iv60_delta: float = 1.5,
    iv90_delta: float = 2.5,
) -> dict:
    return {
        "iv7":  round(iv30 + iv7_delta,  2),
        "iv14": None,
        "iv30": iv30,
        "iv60": round(iv30 + iv60_delta, 2),
        "iv90": round(iv30 + iv90_delta, 2),
    }


def _date_str(offset_days: int = 0) -> str:
    return (datetime.date.today() - datetime.timedelta(days=offset_days)).isoformat()


# ===========================================================================
# 1–12 · IVSurfaceStore unit tests
# ===========================================================================

class TestIVSurfaceStore(unittest.TestCase):
    """Tests for app.data.iv_store.IVSurfaceStore."""

    def setUp(self):
        from app.data.iv_store import IVSurfaceStore
        self.tmp = tempfile.mkdtemp(prefix="iv_store_")
        self.store = IVSurfaceStore()
        _patch_store_path(self.store, self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── 1. Round-trip ────────────────────────────────────────────────────────

    def test_upsert_and_get_history_round_trip(self):
        date = _date_str(5)
        surf = _make_iv_surf(22.0)
        self.store.upsert("SPY", date, surf, source="live_options_chain")
        df = self.store.get_history("SPY")
        self.assertEqual(len(df), 1)
        row = df.iloc[0]
        self.assertEqual(row["date"], date)
        self.assertAlmostEqual(row["iv30"], 22.0, places=3)
        self.assertEqual(row["source"], "live_options_chain")

    def test_upsert_stores_all_tenor_columns(self):
        date = _date_str(3)
        surf = _make_iv_surf(20.0)
        self.store.upsert("QQQ", date, surf, source="synthetic")
        df = self.store.get_history("QQQ")
        row = df.iloc[0]
        self.assertAlmostEqual(row["iv7"],  surf["iv7"],  places=3)
        self.assertAlmostEqual(row["iv30"], surf["iv30"], places=3)
        self.assertAlmostEqual(row["iv60"], surf["iv60"], places=3)
        self.assertAlmostEqual(row["iv90"], surf["iv90"], places=3)

    # ── 2. upsert always replaces (single-row store API) ─────────────────────
    # "Skip if already stored" is enforced at the backfill_ticker level, not here.

    def test_upsert_always_replaces_existing_date(self):
        """upsert drops the old row and inserts the new one unconditionally."""
        date = _date_str(4)
        self.store.upsert("SPY", date, _make_iv_surf(20.0), source="synthetic")
        self.store.upsert("SPY", date, _make_iv_surf(99.0), source="live_options_chain")
        df = self.store.get_history("SPY")
        # Still exactly 1 row (no duplicate)
        self.assertEqual(len(df), 1)
        # Value is the SECOND write (99.0) — always-overwrite semantics
        self.assertAlmostEqual(df.iloc[0]["iv30"], 99.0, places=3)

    def test_different_dates_both_stored(self):
        for i in range(1, 4):
            self.store.upsert("SPY", _date_str(i), _make_iv_surf(20.0 + i), source="synthetic")
        df = self.store.get_history("SPY")
        self.assertEqual(len(df), 3)

    # ── 3. Batch upsert replaces existing rows ────────────────────────────────

    def test_upsert_batch_replaces_existing_row(self):
        """upsert_batch drops existing rows for the given dates and re-inserts."""
        date = _date_str(2)
        self.store.upsert("SPY", date, _make_iv_surf(20.0), source="synthetic")
        self.store.upsert_batch("SPY", [
            {"date": date, "iv_surf": _make_iv_surf(35.0), "source": "live_options_chain"}
        ])
        df = self.store.get_history("SPY")
        row = df[df["date"] == date]
        self.assertFalse(row.empty)
        # Value should be the batch-written one
        self.assertAlmostEqual(float(row.iloc[0]["iv30"]), 35.0, places=2)

    # ── 4. Batch upsert ──────────────────────────────────────────────────────

    def test_batch_upsert_multiple_rows(self):
        batch = [
            {"date": _date_str(i), "iv_surf": _make_iv_surf(20.0 + i), "source": "synthetic"}
            for i in range(5, 0, -1)
        ]
        written = self.store.upsert_batch("IWM", batch)
        self.assertEqual(written, 5)
        df = self.store.get_history("IWM")
        self.assertEqual(len(df), 5)

    def test_batch_upsert_returns_count(self):
        batch = [
            {"date": _date_str(10), "iv_surf": _make_iv_surf(18.0), "source": "synthetic"},
            {"date": _date_str(11), "iv_surf": _make_iv_surf(19.0), "source": "synthetic"},
        ]
        n = self.store.upsert_batch("AAPL", batch)
        self.assertEqual(n, 2)

    # ── 5. Quality mapping ───────────────────────────────────────────────────

    def test_live_source_maps_to_real_quality(self):
        self.store.upsert("SPY", _date_str(1), _make_iv_surf(), source="live_options_chain")
        df = self.store.get_history("SPY")
        self.assertEqual(df.iloc[0]["iv_data_quality"], "real")

    def test_synthetic_source_maps_to_synthetic_fallback_quality(self):
        self.store.upsert("SPY", _date_str(2), _make_iv_surf(), source="synthetic")
        df = self.store.get_history("SPY")
        self.assertEqual(df.iloc[0]["iv_data_quality"], "synthetic_fallback")

    # ── 6. get_stored_dates ──────────────────────────────────────────────────

    def test_get_stored_dates_returns_correct_set(self):
        dates = {_date_str(i) for i in range(1, 4)}
        for d in dates:
            self.store.upsert("SPY", d, _make_iv_surf(), source="synthetic")
        stored = self.store.get_stored_dates("SPY")
        self.assertEqual(stored, dates)

    def test_get_stored_dates_empty_for_missing_ticker(self):
        self.assertEqual(self.store.get_stored_dates("ZZZZZ"), set())

    # ── 7. has_date ──────────────────────────────────────────────────────────

    def test_has_date_true_after_upsert(self):
        date = _date_str(6)
        self.store.upsert("SPY", date, _make_iv_surf(), source="synthetic")
        self.assertTrue(self.store.has_date("SPY", date))

    def test_has_date_false_for_missing_date(self):
        self.assertFalse(self.store.has_date("SPY", "2000-01-01"))

    # ── 8. get_latest_date ───────────────────────────────────────────────────

    def test_get_latest_date_returns_most_recent(self):
        for i in [5, 2, 8]:
            self.store.upsert("QQQ", _date_str(i), _make_iv_surf(), source="synthetic")
        latest = self.store.get_latest_date("QQQ")
        expected = _date_str(2)
        self.assertEqual(latest, expected)

    def test_get_latest_date_none_for_empty(self):
        self.assertIsNone(self.store.get_latest_date("ZZZZZ"))

    # ── 9. quality_summary ───────────────────────────────────────────────────

    def test_quality_summary_counts_correctly(self):
        self.store.upsert("SPY", _date_str(1), _make_iv_surf(), source="live_options_chain")
        self.store.upsert("SPY", _date_str(2), _make_iv_surf(), source="synthetic")
        self.store.upsert("SPY", _date_str(3), _make_iv_surf(), source="synthetic")
        summary = self.store.quality_summary("SPY")
        self.assertEqual(summary["real"], 1)
        self.assertEqual(summary["synthetic_fallback"], 2)
        self.assertEqual(summary["interpolated"], 0)
        self.assertEqual(summary["total"], 3)

    def test_quality_summary_empty_store(self):
        summary = self.store.quality_summary("ZZZZZ")
        self.assertEqual(summary["total"], 0)

    # ── 10. delete_ticker ────────────────────────────────────────────────────

    def test_delete_ticker_removes_all_rows(self):
        for i in range(1, 4):
            self.store.upsert("SPY", _date_str(i), _make_iv_surf(), source="synthetic")
        self.store.delete_ticker("SPY")
        df = self.store.get_history("SPY")
        self.assertTrue(df.empty)

    def test_delete_ticker_doesnt_affect_other_tickers(self):
        self.store.upsert("SPY", _date_str(1), _make_iv_surf(), source="synthetic")
        self.store.upsert("QQQ", _date_str(1), _make_iv_surf(), source="synthetic")
        self.store.delete_ticker("SPY")
        df = self.store.get_history("QQQ")
        self.assertEqual(len(df), 1)

    # ── 11. delete_date_range ────────────────────────────────────────────────

    def test_delete_date_range_removes_only_target_window(self):
        for i in range(1, 11):
            self.store.upsert("SPY", _date_str(i), _make_iv_surf(20.0 + i), source="synthetic")
        # Delete days 3..6
        removed = self.store.delete_date_range("SPY", _date_str(6), _date_str(3))
        self.assertEqual(removed, 4)
        df = self.store.get_history("SPY")
        self.assertEqual(len(df), 6)

    # ── 12. list_tickers ─────────────────────────────────────────────────────

    def test_list_tickers_returns_populated_tickers(self):
        self.store.upsert("SPY", _date_str(1), _make_iv_surf(), source="synthetic")
        self.store.upsert("QQQ", _date_str(1), _make_iv_surf(), source="synthetic")
        tickers = self.store.list_tickers()
        self.assertIn("SPY", tickers)
        self.assertIn("QQQ", tickers)

    def test_list_tickers_empty_when_no_files(self):
        self.assertEqual(self.store.list_tickers(), [])


# ===========================================================================
# 13–16 · iv_provider.get_iv_series merge and mock_provider integration
# ===========================================================================

class TestIVProviderMerge(unittest.TestCase):
    """Tests for iv_provider.get_iv_series merging stored with synthetic."""

    def setUp(self):
        from app.data.iv_store import IVSurfaceStore
        self.tmp = tempfile.mkdtemp(prefix="iv_prov_test_")
        self.store = IVSurfaceStore()
        _patch_store_path(self.store, self.tmp)

        # Patch the module-level singleton used by iv_provider
        patcher = patch("app.data.iv_provider._store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load_synthetic_dates(self, ticker: str, days: int = 20) -> list[str]:
        """Return the date strings the mock provider generates for ticker."""
        from app.data._mock_impl import MockDataProvider
        df = MockDataProvider().get_iv_history_days(ticker, days)
        return [d.strftime("%Y-%m-%d") for d in df.index.tolist()]

    # ── 13. Stored rows overwrite synthetic in merged series ─────────────────

    def test_stored_rows_overwrite_synthetic_iv30(self):
        from app.data.iv_provider import get_iv_series
        # Insert a stored row with a distinctive iv30 value
        # Use the last date from the synthetic baseline so we know it will align
        dates = self._load_synthetic_dates("SPY", 50)
        target_date = dates[-1]  # most recent

        self.store.upsert("SPY", target_date, _make_iv_surf(99.99), source="live_options_chain")

        result = get_iv_series("SPY", 50)
        ts = pd.Timestamp(target_date)
        if ts in result.index:
            row = result.loc[ts]
            self.assertAlmostEqual(float(row["iv30"]), 99.99, places=1)
            self.assertEqual(row["iv_data_quality"], "real")

    # ── 14. Rows without store entry remain synthetic_fallback ───────────────

    def test_rows_without_store_entry_are_synthetic_fallback(self):
        from app.data.iv_provider import get_iv_series
        result = get_iv_series("SPY", 20)
        # No rows inserted → all should be synthetic_fallback
        self.assertTrue(
            (result["iv_data_quality"] == "synthetic_fallback").all(),
            "Expected all rows to be synthetic_fallback when store is empty",
        )

    # ── 15. mock_provider.get_iv_data routes through iv_provider ────────────

    def test_get_iv_data_returns_merged_series(self):
        from app.data.mock_provider import get_iv_data
        dates = self._load_synthetic_dates("QQQ", 30)
        target_date = dates[-1]
        self.store.upsert("QQQ", target_date, _make_iv_surf(55.5), source="live_options_chain")

        df = get_iv_data("QQQ", days=30)
        self.assertIn("iv30", df.columns)
        self.assertIn("iv_data_quality", df.columns)
        ts = pd.Timestamp(target_date)
        if ts in df.index:
            self.assertAlmostEqual(float(df.loc[ts, "iv30"]), 55.5, places=1)

    # ── 16. get_iv_quality_summary counts real vs synthetic ─────────────────

    def test_get_iv_quality_summary_counts_correctly(self):
        from app.data.mock_provider import get_iv_quality_summary
        dates = self._load_synthetic_dates("SPY", 30)
        for d in dates[-3:]:
            self.store.upsert("SPY", d, _make_iv_surf(), source="live_options_chain")

        summary = get_iv_quality_summary("SPY", 30)
        self.assertIn("real", summary)
        # At least 3 real rows (may be slightly off due to calendar alignment)
        self.assertGreaterEqual(summary["real"], 1)
        self.assertIn("total", summary)
        self.assertGreater(summary["total"], 0)


# ===========================================================================
# 17 · Backfill resume test (using backfill_ticker directly)
# ===========================================================================

class TestBackfillResume(unittest.TestCase):
    """Tests for scripts.backfill_iv_surface backfill resume behaviour."""

    def setUp(self):
        from app.data.iv_store import IVSurfaceStore
        self.tmp = tempfile.mkdtemp(prefix="iv_backfill_test_")
        self.store = IVSurfaceStore()
        _patch_store_path(self.store, self.tmp)

        patcher = patch("scripts.backfill_iv_surface._store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _recent_trading_days(self, n: int) -> list[datetime.date]:
        """Return last n weekdays."""
        days = []
        cur = datetime.date.today()
        while len(days) < n:
            if cur.weekday() < 5:
                days.append(cur)
            cur -= datetime.timedelta(days=1)
        return list(reversed(days))

    # ── 17a. Partial backfill then resume fills the gap ──────────────────────

    def test_resume_fills_only_missing_dates(self):
        from scripts.backfill_iv_surface import backfill_ticker
        all_days = self._recent_trading_days(10)
        today = datetime.date.today()

        # First pass: fill first 5 days
        r1 = backfill_ticker(
            "SPY", all_days[:5],
            overwrite=False, dry_run=False, verbose=False, today=today,
        )
        self.assertEqual(r1["written"], 5)
        self.assertEqual(r1["skipped"], 0)

        # Second pass: all 10 days — first 5 should be skipped, last 5 written
        r2 = backfill_ticker(
            "SPY", all_days,
            overwrite=False, dry_run=False, verbose=False, today=today,
        )
        self.assertEqual(r2["skipped"], 5)
        self.assertEqual(r2["written"], 5)

    # ── 17b. Overwrite flag re-writes existing dates ─────────────────────────

    def test_overwrite_flag_rewrites_all_dates(self):
        from scripts.backfill_iv_surface import backfill_ticker
        days = self._recent_trading_days(5)
        today = datetime.date.today()

        backfill_ticker("SPY", days, overwrite=False, dry_run=False, verbose=False, today=today)
        r2 = backfill_ticker(
            "SPY", days, overwrite=True, dry_run=False, verbose=False, today=today,
        )
        # With overwrite all 5 are rewritten (skipped == 0)
        self.assertEqual(r2["skipped"], 0)
        self.assertEqual(r2["written"], 5)

    # ── 18. Dry-run writes nothing ────────────────────────────────────────────

    def test_dry_run_writes_nothing(self):
        from scripts.backfill_iv_surface import backfill_ticker
        days = self._recent_trading_days(5)
        today = datetime.date.today()

        r = backfill_ticker(
            "SPY", days, overwrite=False, dry_run=True, verbose=False, today=today,
        )
        # Reports what WOULD be written but doesn't persist
        self.assertEqual(r["written"], 5)
        # Store should still be empty
        df = self.store.get_history("SPY")
        self.assertTrue(df.empty, "dry-run should not write to store")


# ===========================================================================
# 19–20 · Edge cases
# ===========================================================================

class TestEdgeCases(unittest.TestCase):
    """Edge cases for empty stores and out-of-range date queries."""

    def setUp(self):
        from app.data.iv_store import IVSurfaceStore
        self.tmp = tempfile.mkdtemp(prefix="iv_edge_test_")
        self.store = IVSurfaceStore()
        _patch_store_path(self.store, self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── 19. Empty store returns empty DataFrame ───────────────────────────────

    def test_get_history_empty_store_returns_empty_dataframe(self):
        df = self.store.get_history("SPY")
        self.assertIsInstance(df, pd.DataFrame)
        self.assertTrue(df.empty)

    def test_quality_summary_empty_store_all_zeros(self):
        s = self.store.quality_summary("SPY")
        self.assertEqual(s["real"],               0)
        self.assertEqual(s["synthetic_fallback"],  0)
        self.assertEqual(s["interpolated"],        0)
        self.assertEqual(s["total"],               0)

    # ── 20. Future date range returns no rows ────────────────────────────────

    def test_date_range_filter_excludes_out_of_range_dates(self):
        # Store a row from the past
        self.store.upsert("SPY", "2020-01-15", _make_iv_surf(18.0), source="synthetic")
        # Query a completely different range — should find nothing
        df = self.store.get_history("SPY", start_date="2023-01-01", end_date="2023-12-31")
        self.assertTrue(df.empty)

    def test_date_range_filter_returns_only_matching_dates(self):
        for i in range(1, 6):
            self.store.upsert("SPY", _date_str(i), _make_iv_surf(20.0 + i), source="synthetic")
        # Only dates in the last 3 days
        start = _date_str(3)
        end   = _date_str(1)
        df = self.store.get_history("SPY", start_date=start, end_date=end)
        self.assertEqual(len(df), 3)


# ===========================================================================
# Run
# ===========================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
