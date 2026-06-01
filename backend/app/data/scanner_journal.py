"""
app/data/scanner_journal.py
-----------------------------
Persistent daily journal for scanner signals.

Storage
-------
  backend/data_store/scanner_signals/YYYY-MM-DD.parquet

One file per scan date; each file is a flat DataFrame of all qualifying
signals from that day's scan.  Files are written atomically (tmp → rename)
and are immutable once written for the day (re-save overwrites).

Schema
------
  scan_date                str   ISO date
  ticker                   str
  price                    float64
  iv30                     float64  annualised %
  rv30                     float64  annualised %
  iv_rv_ratio              float64
  volatility_risk_premium  float64  vrp_abs (vol points)
  surface_status           str   healthy | suspicious | invalid
  iv_source_quality        str   quoted_market_iv | polygon_model_iv_no_quote | invalid
  broker_verify_required   bool
  liquidity_score          float64  0–100
  macro_penalty            float64  pts
  earnings_penalty         float64  pts
  event_risk_flag          bool
  trade_signal             str   SELL_AGGRESSIVE | SELL_SELECTIVE
  scanner_score            float64  0–100

Reading
-------
  ``load_all()`` concatenates every parquet file present, newest first.
  ``load_date(date)`` returns a single day (empty DataFrame if not found).
  ``load_ticker(ticker)`` returns all rows for one ticker across all dates.
"""

from __future__ import annotations

import datetime
import logging
import pathlib
import tempfile
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]
_STORE_DIR    = _BACKEND_ROOT / "data_store" / "scanner_signals"
_STORE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

JOURNAL_COLUMNS: list[str] = [
    "scan_date",
    "ticker",
    "price",
    "iv30",
    "rv30",
    "iv_rv_ratio",
    "volatility_risk_premium",
    "surface_status",
    "iv_source_quality",
    "broker_verify_required",
    "liquidity_score",
    "macro_penalty",
    "earnings_penalty",
    "event_risk_flag",
    "trade_signal",
    "scanner_score",
]

# Outcome columns written by calculate_outcomes.py / POST /outcomes/calculate.
# These are absent from parquet files created before the outcome feature was
# added; _ensure_outcome_cols() fills them with None on load.
OUTCOME_COLUMNS: list[str] = [
    "fwd_ret_5d",
    "fwd_ret_10d",
    "fwd_ret_20d",
    "fwd_rv5d",
    "fwd_rv10d",
    "fwd_rv20d",
    "premium_capture_5d",
    "premium_capture_10d",
    "premium_capture_20d",
    "outcome_status",          # pending | partial | complete
]

# Manual review columns — written by PATCH /api/scanner/journal/{date}/{ticker}.
# Absent from older parquet files; _ensure_review_cols() fills them on load.
REVIEW_COLUMNS: list[str] = [
    "reviewed",            # bool   — analyst has looked at this signal
    "broker_verified",     # bool   — live bid/ask confirmed with a broker
    "actual_bid",          # float  — ATM option bid observed at broker
    "actual_ask",          # float  — ATM option ask observed at broker
    "actual_mid",          # float  — (bid+ask)/2 at broker
    "actual_spread_pct",   # float  — (ask-bid)/mid as a fraction
    "trade_considered",    # bool   — signal was seriously considered
    "trade_taken",         # bool   — trade was actually entered
    "notes",               # str    — free-form analyst notes
]

ALL_COLUMNS: list[str] = JOURNAL_COLUMNS + OUTCOME_COLUMNS + REVIEW_COLUMNS

_DTYPES: dict[str, str] = {
    "scan_date":               "str",
    "ticker":                  "str",
    "price":                   "float64",
    "iv30":                    "float64",
    "rv30":                    "float64",
    "iv_rv_ratio":             "float64",
    "volatility_risk_premium": "float64",
    "surface_status":          "str",
    "iv_source_quality":       "str",
    "broker_verify_required":  "bool",
    "liquidity_score":         "float64",
    "macro_penalty":           "float64",
    "earnings_penalty":        "float64",
    "event_risk_flag":         "bool",
    "trade_signal":            "str",
    "scanner_score":           "float64",
    # Outcome columns — nullable floats / str
    "fwd_ret_5d":              "float64",
    "fwd_ret_10d":             "float64",
    "fwd_ret_20d":             "float64",
    "fwd_rv5d":                "float64",
    "fwd_rv10d":               "float64",
    "fwd_rv20d":               "float64",
    "premium_capture_5d":      "float64",
    "premium_capture_10d":     "float64",
    "premium_capture_20d":     "float64",
    "outcome_status":          "str",
    # Review columns
    "reviewed":                "bool",
    "broker_verified":         "bool",
    "actual_bid":              "float64",
    "actual_ask":              "float64",
    "actual_mid":              "float64",
    "actual_spread_pct":       "float64",
    "trade_considered":        "bool",
    "trade_taken":             "bool",
    "notes":                   "str",
}

_EMPTY = pd.DataFrame(columns=ALL_COLUMNS)


# ---------------------------------------------------------------------------
# Column-backfill helpers
# ---------------------------------------------------------------------------

def _ensure_outcome_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Add missing outcome columns with appropriate defaults."""
    for col in OUTCOME_COLUMNS:
        if col not in df.columns:
            df[col] = "pending" if col == "outcome_status" else float("nan")
    return df


def _ensure_review_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add missing manual-review columns with appropriate defaults so that
    DataFrames loaded from older parquet files have a uniform schema.
    Mutates and returns *df*.
    """
    _BOOL_COLS  = {"reviewed", "broker_verified", "trade_considered", "trade_taken"}
    _FLOAT_COLS = {"actual_bid", "actual_ask", "actual_mid", "actual_spread_pct"}
    for col in REVIEW_COLUMNS:
        if col not in df.columns:
            if col in _BOOL_COLS:
                df[col] = False
            elif col in _FLOAT_COLS:
                df[col] = float("nan")
            else:        # notes
                df[col] = ""
    return df


def _coerce_df(df: pd.DataFrame) -> pd.DataFrame:
    """Apply all column-backfill helpers in one call."""
    return _ensure_review_cols(_ensure_outcome_cols(df))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parquet_path(date: str) -> pathlib.Path:
    return _STORE_DIR / f"{date}.parquet"


def _all_parquet_files() -> list[pathlib.Path]:
    """Return all parquet files, sorted newest-first."""
    return sorted(_STORE_DIR.glob("*.parquet"), reverse=True)


def _result_to_row(r: dict, scan_date: str) -> dict:
    """Extract journal columns from a scanner result dict."""
    signal = r.get("signal") or {}
    return {
        "scan_date":               scan_date,
        "ticker":                  r.get("ticker", ""),
        "price":                   float(r.get("price") or 0.0),
        "iv30":                    float(r.get("iv30") or 0.0),
        "rv30":                    float(r.get("rv30") or 0.0),
        "iv_rv_ratio":             float(r.get("iv_rv_ratio") or 0.0),
        "volatility_risk_premium": float(r.get("vrp_abs") or 0.0),
        "surface_status":          str(r.get("iv_surface_status", "")),
        "iv_source_quality":       str(r.get("iv_source_quality", "")),
        "broker_verify_required":  bool(r.get("broker_verify_required", False)),
        "liquidity_score":         float(r.get("liquidity_score") or 0.0),
        "macro_penalty":           float(r.get("macro_penalty") or 0.0),
        "earnings_penalty":        float(r.get("earnings_score_penalty") or 0.0),
        "event_risk_flag":         bool(r.get("event_risk_flag", False)),
        "trade_signal":            str(signal.get("signal", "")),
        "scanner_score":           float(r.get("score") or 0.0),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_scan(results: list[dict], scan_date: str | None = None) -> int:
    """
    Persist scanner results for *scan_date* (default: today).

    Overwrites any existing file for that date.  Returns the number of
    rows written.

    Parameters
    ----------
    results   : list of qualifying scanner result dicts (``ok=True`` rows)
    scan_date : ISO date string, e.g. ``"2026-05-27"``; defaults to today
    """
    if scan_date is None:
        scan_date = datetime.date.today().isoformat()

    if not results:
        logger.info("Scanner journal: nothing to save for %s", scan_date)
        return 0

    rows = [_result_to_row(r, scan_date) for r in results]
    df   = pd.DataFrame(rows, columns=JOURNAL_COLUMNS)

    # Atomic write: temp file → rename
    dest = _parquet_path(scan_date)
    tmp  = dest.with_suffix(".tmp.parquet")
    try:
        df.to_parquet(tmp, engine="pyarrow", index=False)
        tmp.replace(dest)
        logger.info(
            "Scanner journal: saved %d signal(s) for %s → %s",
            len(df), scan_date, dest.name,
        )
        return len(df)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        logger.error("Scanner journal: failed to save %s: %s", scan_date, exc)
        raise


def load_date(date: str) -> pd.DataFrame:
    """Return all signals for a single *date*. Empty DataFrame if not found."""
    p = _parquet_path(date)
    if not p.exists():
        return _EMPTY.copy()
    try:
        return _coerce_df(pd.read_parquet(p, engine="pyarrow"))
    except Exception as exc:
        logger.warning("Scanner journal: failed to load %s: %s", p.name, exc)
        return _EMPTY.copy()


def load_all(limit_days: int = 365) -> pd.DataFrame:
    """
    Concatenate all journal files, newest first.

    Parameters
    ----------
    limit_days : cap on how many recent calendar days to include
    """
    files = _all_parquet_files()[:limit_days]
    if not files:
        return _EMPTY.copy()

    frames: list[pd.DataFrame] = []
    for p in files:
        try:
            frames.append(_coerce_df(pd.read_parquet(p, engine="pyarrow")))
        except Exception as exc:
            logger.warning("Scanner journal: skip corrupt %s: %s", p.name, exc)

    if not frames:
        return _EMPTY.copy()

    return pd.concat(frames, ignore_index=True)


def update_outcomes(updates: list[dict]) -> int:
    """
    Persist forward-return and RV outcomes into the appropriate parquet files.

    Each dict in *updates* must contain ``scan_date`` and ``ticker`` plus
    any subset of the :data:`OUTCOME_COLUMNS`.  Rows are matched by
    ``(scan_date, ticker)``.  One atomic read-modify-write is performed
    per affected parquet file.

    Returns the number of journal rows actually updated.
    """
    if not updates:
        return 0

    # Group updates by scan_date so we do one file I/O per day
    from collections import defaultdict
    by_date: dict[str, list[dict]] = defaultdict(list)
    for u in updates:
        by_date[u["scan_date"]].append(u)

    total_updated = 0

    for date_str, rows in by_date.items():
        p = _parquet_path(date_str)
        if not p.exists():
            logger.warning("Scanner journal: no parquet for %s — skipping", date_str)
            continue

        try:
            df = pd.read_parquet(p, engine="pyarrow")
            df = _ensure_outcome_cols(df)
        except Exception as exc:
            logger.error("Scanner journal: failed to read %s for update: %s", p.name, exc)
            continue

        updated_in_file = 0
        for row in rows:
            ticker = str(row.get("ticker", "")).upper()
            mask   = df["ticker"] == ticker
            if not mask.any():
                logger.debug("Scanner journal: ticker %s not in %s — skip", ticker, date_str)
                continue

            for col in OUTCOME_COLUMNS:
                if col in row:
                    df.loc[mask, col] = row[col]
            updated_in_file += int(mask.sum())

        if updated_in_file == 0:
            continue

        # Atomic write
        tmp = p.with_suffix(".tmp.parquet")
        try:
            df.to_parquet(tmp, engine="pyarrow", index=False)
            tmp.replace(p)
            logger.info(
                "Scanner journal: updated %d outcome row(s) in %s",
                updated_in_file, p.name,
            )
            total_updated += updated_in_file
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            logger.error("Scanner journal: failed to write outcomes to %s: %s", p.name, exc)

    return total_updated


def update_review(date: str, ticker: str, fields: dict) -> bool:
    """
    Apply manual review fields to a single journal row.

    Only keys present in :data:`REVIEW_COLUMNS` are written; any other keys
    in *fields* are silently ignored.  Performs an atomic read-modify-write
    on the parquet file for *date*.

    Parameters
    ----------
    date   : ISO date string, e.g. ``"2026-05-29"``
    ticker : ticker symbol (case-insensitive)
    fields : mapping of ``{column_name: value}`` to apply

    Returns
    -------
    ``True`` if the row was found and updated, ``False`` if the parquet file
    or the ticker row does not exist.
    """
    ticker = ticker.upper()
    p      = _parquet_path(date)

    if not p.exists():
        logger.warning("Scanner journal: no parquet for %s — cannot update review", date)
        return False

    try:
        df = _coerce_df(pd.read_parquet(p, engine="pyarrow"))
    except Exception as exc:
        logger.error("Scanner journal: failed to read %s for review update: %s", p.name, exc)
        return False

    mask = df["ticker"] == ticker
    if not mask.any():
        logger.warning("Scanner journal: ticker %s not found in %s", ticker, date)
        return False

    # Write only recognised review columns
    for col, val in fields.items():
        if col not in REVIEW_COLUMNS:
            continue
        # Compute actual_mid from bid/ask if not explicitly provided
        if col in ("actual_bid", "actual_ask"):
            df.loc[mask, col] = val
            bid = df.loc[mask, "actual_bid"].iloc[0]
            ask = df.loc[mask, "actual_ask"].iloc[0]
            if pd.notna(bid) and pd.notna(ask) and ask > 0:
                df.loc[mask, "actual_mid"]         = round((bid + ask) / 2, 4)
                df.loc[mask, "actual_spread_pct"]  = round((ask - bid) / ((bid + ask) / 2), 4)
        else:
            df.loc[mask, col] = val

    tmp = p.with_suffix(".tmp.parquet")
    try:
        df.to_parquet(tmp, engine="pyarrow", index=False)
        tmp.replace(p)
        logger.info(
            "Scanner journal: review updated for %s on %s — fields: %s",
            ticker, date, list(fields.keys()),
        )
        return True
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        logger.error("Scanner journal: failed to write review for %s %s: %s", ticker, date, exc)
        return False


def load_ticker(ticker: str) -> pd.DataFrame:
    """Return all journal rows for one ticker across all dates."""
    df = load_all()
    if df.empty:
        return _EMPTY.copy()
    return df[df["ticker"] == ticker.upper()].reset_index(drop=True)


def list_dates() -> list[str]:
    """Return ISO date strings for all stored scan dates, newest first."""
    return [p.stem for p in _all_parquet_files()]


def daily_summary() -> pd.DataFrame:
    """
    One row per scan date with aggregated stats.

    Columns: scan_date, signal_count, avg_score, avg_iv_rv_ratio,
             top_ticker (highest-score ticker), tickers (comma-separated list)
    """
    df = load_all()
    if df.empty:
        return pd.DataFrame(columns=[
            "scan_date", "signal_count", "avg_score",
            "avg_iv_rv_ratio", "top_ticker", "tickers",
        ])

    rows = []
    for date, grp in df.groupby("scan_date"):
        top_row = grp.nlargest(1, "scanner_score").iloc[0]
        rows.append({
            "scan_date":      date,
            "signal_count":   len(grp),
            "avg_score":      round(grp["scanner_score"].mean(), 1),
            "avg_iv_rv_ratio": round(grp["iv_rv_ratio"].mean(), 3),
            "top_ticker":     top_row["ticker"],
            "tickers":        ",".join(grp.sort_values("scanner_score", ascending=False)["ticker"].tolist()),
        })

    return (
        pd.DataFrame(rows)
        .sort_values("scan_date", ascending=False)
        .reset_index(drop=True)
    )


def ticker_leaderboard() -> pd.DataFrame:
    """
    One row per ticker across all stored dates.

    Columns: ticker, appearances, avg_score, avg_iv_rv_ratio,
             avg_iv30, avg_rv30, first_seen, last_seen, best_score
    """
    df = load_all()
    if df.empty:
        return pd.DataFrame(columns=[
            "ticker", "appearances", "avg_score", "avg_iv_rv_ratio",
            "avg_iv30", "avg_rv30", "first_seen", "last_seen", "best_score",
        ])

    rows = []
    for ticker, grp in df.groupby("ticker"):
        rows.append({
            "ticker":          ticker,
            "appearances":     len(grp),
            "avg_score":       round(grp["scanner_score"].mean(), 1),
            "avg_iv_rv_ratio": round(grp["iv_rv_ratio"].mean(), 3),
            "avg_iv30":        round(grp["iv30"].mean(), 1),
            "avg_rv30":        round(grp["rv30"].mean(), 1),
            "first_seen":      grp["scan_date"].min(),
            "last_seen":       grp["scan_date"].max(),
            "best_score":      round(grp["scanner_score"].max(), 1),
        })

    return (
        pd.DataFrame(rows)
        .sort_values("appearances", ascending=False)
        .reset_index(drop=True)
    )
