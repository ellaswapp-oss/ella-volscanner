"""
data/iv_store.py
----------------
Parquet-based persistence for daily ATM IV surfaces.

One Parquet file per ticker stores the full IV surface history:

    backend/data_cache/iv_history/{TICKER}_iv_surface.parquet

Columns
-------
date         str          "YYYY-MM-DD"
ticker       str          e.g. "SPY"
iv7          float        annualised % (may be NaN when unavailable)
iv14         float        annualised % (NaN for historical synthetic data)
iv30         float        annualised %
iv60         float        annualised %
iv90         float        annualised %
source       str          see Source Labels below
created_at   str          UTC ISO timestamp of when the row was last written

Source Labels
-------------
"polygon_live_snapshot"       Live options chain fetched from Polygon.io (same-day)
"polygon_historical_options"  Historical options chain from Polygon.io /v3/snapshot?date=
"orats_historical"            ORATS end-of-day historical IV data
"optionmetrics_historical"    OptionMetrics academic historical IV data
"synthetic_fallback"          Computed from price history via OU-process mock model
"interpolated"                Linearly interpolated between real anchor dates
"live_options_chain"          Legacy alias for polygon_live_snapshot (still accepted)
"synthetic"                   Legacy alias for synthetic_fallback (still accepted)

Data quality mapping
--------------------
source                         → iv_data_quality
"polygon_live_snapshot"        → "real"
"polygon_historical_options"   → "real"
"orats_historical"             → "real"
"optionmetrics_historical"     → "real"
"live_options_chain"           → "real"        (legacy)
"interpolated"                 → "interpolated"
"synthetic_fallback"           → "synthetic_fallback"
"synthetic"                    → "synthetic_fallback" (legacy)
(anything else)                → "synthetic_fallback"

Design decisions
----------------
- Atomic writes: load → modify → replace (not append-only) for simplicity.
  Parquet files are small enough (<5 MB for 5 years of 6 tickers) that a
  full rewrite on each upsert is negligible.
- Date strings (not datetime) for portable JSON-compatible keys.
- Module-level singleton ``store`` for convenient import::

      from app.data.iv_store import store
      store.upsert("SPY", "2024-06-30", {"iv30": 15.8, ...}, "live_options_chain")
"""

from __future__ import annotations

import datetime
import logging
import pathlib

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BACKEND_ROOT   = pathlib.Path(__file__).resolve().parents[2]
_IV_HISTORY_DIR = _BACKEND_ROOT / "data_cache" / "iv_history"

# ---------------------------------------------------------------------------
# Column schema
# ---------------------------------------------------------------------------

_STR_COLS   = ["date", "ticker", "source", "created_at"]
_FLOAT_COLS = ["iv7", "iv14", "iv30", "iv60", "iv90"]
_ALL_COLS   = ["date", "ticker", "iv7", "iv14", "iv30", "iv60", "iv90", "source", "created_at"]

# ---------------------------------------------------------------------------
# Quality mapping
# ---------------------------------------------------------------------------

_QUALITY: dict[str, str] = {
    # Current canonical source labels
    "polygon_live_snapshot":      "real",
    "polygon_historical_options": "real",
    "orats_historical":           "real",
    "optionmetrics_historical":   "real",
    "interpolated":               "interpolated",
    "synthetic_fallback":         "synthetic_fallback",
    # Legacy labels — kept for backward compat with existing Parquet files
    "live_options_chain":         "real",
    "synthetic":                  "synthetic_fallback",
}


def _source_to_quality(source: str) -> str:
    return _QUALITY.get(source, "synthetic_fallback")


# ===========================================================================
# IVSurfaceStore
# ===========================================================================

class IVSurfaceStore:
    """
    Parquet-backed daily ATM IV surface store.

    Thread-safety
    -------------
    Each method does a full load-modify-save cycle using pandas.  For
    single-process ingest (backfill script + one API server) this is fine.
    For concurrent writers, add file locking or use a database.
    """

    def __init__(self, base_dir: pathlib.Path = _IV_HISTORY_DIR) -> None:
        self._base = pathlib.Path(base_dir)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _path(self, ticker: str) -> pathlib.Path:
        return self._base / f"{ticker.upper()}_iv_surface.parquet"

    # ------------------------------------------------------------------
    # Empty DataFrame (canonical schema)
    # ------------------------------------------------------------------

    @staticmethod
    def _empty() -> pd.DataFrame:
        return pd.DataFrame(columns=_ALL_COLS).astype(
            {c: "float64" for c in _FLOAT_COLS} | {c: "object" for c in _STR_COLS}
        )

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def load(self, ticker: str) -> pd.DataFrame:
        """Load the full IV history for *ticker*. Returns empty DataFrame if not found."""
        path = self._path(ticker)
        if not path.exists():
            return self._empty()
        try:
            df = pd.read_parquet(path)
            # Ensure float columns have correct dtype after round-trip
            for col in _FLOAT_COLS:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df[_ALL_COLS] if set(_ALL_COLS).issubset(df.columns) else self._empty()
        except Exception as exc:
            logger.warning("Failed to load IV store for %s: %s", ticker, exc)
            return self._empty()

    def _save(self, ticker: str, df: pd.DataFrame) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        path = self._path(ticker)
        df = df.sort_values("date").reset_index(drop=True)
        df.to_parquet(path, index=False, engine="auto")

    # ------------------------------------------------------------------
    # Single-row upsert
    # ------------------------------------------------------------------

    def upsert(
        self,
        ticker: str,
        date: str | datetime.date,
        iv_surf: dict,
        source: str,
    ) -> None:
        """
        Insert or replace the IV surface for one (ticker, date).

        Parameters
        ----------
        ticker   : uppercase ticker symbol
        date     : "YYYY-MM-DD" string or datetime.date
        iv_surf  : dict with keys iv7/iv14/iv30/iv60/iv90 (values in %)
        source   : "polygon_live_snapshot" | "orats_historical" | "optionmetrics_historical"
                   | "synthetic_fallback" | "interpolated"
                   (legacy: "live_options_chain" | "synthetic" still accepted)
        """
        date_str  = str(date)
        now_str   = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        ticker_up = ticker.upper()

        df = self.load(ticker_up)
        # Drop any existing row for this date
        df = df[df["date"] != date_str]

        new_row = {
            "date":       date_str,
            "ticker":     ticker_up,
            "iv7":        _to_float(iv_surf.get("iv7")),
            "iv14":       _to_float(iv_surf.get("iv14")),
            "iv30":       _to_float(iv_surf.get("iv30")),
            "iv60":       _to_float(iv_surf.get("iv60")),
            "iv90":       _to_float(iv_surf.get("iv90")),
            "source":     source,
            "created_at": now_str,
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        self._save(ticker_up, df)

    # ------------------------------------------------------------------
    # Batch upsert (efficient for backfill)
    # ------------------------------------------------------------------

    def upsert_batch(self, ticker: str, rows: list[dict]) -> int:
        """
        Bulk insert or replace rows for *ticker*.

        Each dict in *rows* must have keys: date, iv_surf (dict), source.
        Returns the number of rows written.

        Example
        -------
        store.upsert_batch("SPY", [
            {"date": "2024-01-02", "iv_surf": {"iv30": 15.2}, "source": "synthetic"},
            ...
        ])
        """
        if not rows:
            return 0

        now_str   = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
        ticker_up = ticker.upper()
        new_dates = {str(r["date"]) for r in rows}

        df = self.load(ticker_up)
        df = df[~df["date"].isin(new_dates)]

        new_records = [
            {
                "date":       str(r["date"]),
                "ticker":     ticker_up,
                "iv7":        _to_float(r["iv_surf"].get("iv7")),
                "iv14":       _to_float(r["iv_surf"].get("iv14")),
                "iv30":       _to_float(r["iv_surf"].get("iv30")),
                "iv60":       _to_float(r["iv_surf"].get("iv60")),
                "iv90":       _to_float(r["iv_surf"].get("iv90")),
                "source":     r.get("source", "synthetic"),
                "created_at": r.get("created_at", now_str),
            }
            for r in rows
        ]
        df = pd.concat([df, pd.DataFrame(new_records)], ignore_index=True)
        self._save(ticker_up, df)
        return len(new_records)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_history(
        self,
        ticker: str,
        start_date: str | datetime.date | None = None,
        end_date:   str | datetime.date | None = None,
    ) -> pd.DataFrame:
        """
        Return stored IV rows for *ticker*, optionally filtered by date range.

        The returned DataFrame includes a derived ``iv_data_quality`` column
        ("real" | "interpolated" | "synthetic_fallback") computed from ``source``.
        """
        df = self.load(ticker)
        if start_date is not None:
            df = df[df["date"] >= str(start_date)]
        if end_date is not None:
            df = df[df["date"] <= str(end_date)]
        if not df.empty:
            df["iv_data_quality"] = df["source"].map(_source_to_quality).fillna("synthetic_fallback")
        else:
            df["iv_data_quality"] = pd.Series(dtype="object")
        return df.sort_values("date").reset_index(drop=True)

    def get_latest_date(self, ticker: str) -> str | None:
        """Return the most-recent stored date string, or None if empty."""
        df = self.load(ticker)
        if df.empty:
            return None
        return df["date"].max()

    def get_stored_dates(self, ticker: str) -> set[str]:
        """Return the set of all stored date strings for *ticker*."""
        df = self.load(ticker)
        return set(df["date"].tolist())

    def has_date(self, ticker: str, date: str | datetime.date) -> bool:
        return str(date) in self.get_stored_dates(ticker)

    # ------------------------------------------------------------------
    # Quality summary
    # ------------------------------------------------------------------

    def quality_summary(
        self,
        ticker: str,
        start_date: str | datetime.date | None = None,
        end_date:   str | datetime.date | None = None,
    ) -> dict:
        """
        Return counts by data quality for *ticker* in the optional date range.

        Example
        -------
        {"real": 1, "synthetic_fallback": 251, "interpolated": 0, "total": 252}
        """
        df = self.get_history(ticker, start_date, end_date)
        if df.empty:
            return {"real": 0, "synthetic_fallback": 0, "interpolated": 0, "total": 0}
        counts = df["iv_data_quality"].value_counts().to_dict()
        return {
            "real":               counts.get("real", 0),
            "synthetic_fallback": counts.get("synthetic_fallback", 0),
            "interpolated":       counts.get("interpolated", 0),
            "total":              len(df),
        }

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def delete_ticker(self, ticker: str) -> None:
        """Delete all stored data for *ticker* (for full re-backfill)."""
        path = self._path(ticker)
        if path.exists():
            path.unlink()
            logger.info("Deleted IV store: %s", path)

    def delete_date_range(
        self,
        ticker: str,
        start_date: str | datetime.date,
        end_date:   str | datetime.date,
    ) -> int:
        """Delete rows within [start_date, end_date] inclusive. Returns number deleted."""
        df = self.load(ticker)
        mask    = (df["date"] >= str(start_date)) & (df["date"] <= str(end_date))
        n_del   = int(mask.sum())
        df      = df[~mask]
        self._save(ticker, df)
        return n_del

    def list_tickers(self) -> list[str]:
        """Return tickers that have a stored Parquet file."""
        if not self._base.exists():
            return []
        return sorted(
            p.stem.replace("_iv_surface", "")
            for p in self._base.glob("*_iv_surface.parquet")
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float(v) -> float:
    """Convert to float; return NaN for None / non-finite values."""
    if v is None:
        return float("nan")
    try:
        f = float(v)
        return f if not (f != f) else float("nan")   # NaN check via self-inequality
    except (TypeError, ValueError):
        return float("nan")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

store = IVSurfaceStore()
