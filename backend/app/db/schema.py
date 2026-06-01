"""
db/schema.py
------------
SQLite schema and connection helpers for the Vol Dashboard.

Tables
------
tickers                      — supported symbols + metadata
daily_prices                 — synthetic OHLCV per ticker per date
option_volatility_snapshots  — IV surface, RV windows, derived metrics
macro_snapshots              — market-wide VIX + regime score per date
trade_signals                — per-ticker signal + factors per date

Phase 3 swap
------------
Replace the mock snapshot seeder (scripts/save_snapshot.py) with live
Polygon / ORATS / FRED adapters.  The schema is live-data-ready; only
the ingest layer changes.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

# DB lives one level above app/, alongside requirements.txt
DB_PATH = Path(__file__).parent.parent.parent / "data" / "vol_dashboard.db"

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_STMTS = [
    # ------------------------------------------------------------------
    # tickers — master list of supported symbols
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS tickers (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol   TEXT    UNIQUE NOT NULL,
        name     TEXT,
        added_at TEXT    NOT NULL DEFAULT (date('now'))
    )
    """,

    # ------------------------------------------------------------------
    # daily_prices — synthetic OHLCV; swap for Polygon in Phase 3
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS daily_prices (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker_id INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
        date      TEXT    NOT NULL,               -- YYYY-MM-DD
        open      REAL,
        high      REAL,
        low       REAL,
        close     REAL    NOT NULL,
        volume    INTEGER,
        UNIQUE(ticker_id, date)
    )
    """,

    # ------------------------------------------------------------------
    # option_volatility_snapshots — daily IV/RV/derived-metric rows
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS option_volatility_snapshots (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker_id    INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
        date         TEXT    NOT NULL,             -- YYYY-MM-DD
        -- IV surface (annualised %)
        iv7          REAL NOT NULL,
        iv30         REAL NOT NULL,
        iv60         REAL NOT NULL,
        iv90         REAL NOT NULL,
        -- Realised vol windows (annualised %)
        rv10         REAL,
        rv20         REAL,
        rv30         REAL,
        rv60         REAL,
        -- Derived metrics
        iv_rank      REAL,                         -- 0-100
        iv_rv_ratio  REAL,                         -- iv30 / rv30
        vrp          REAL,                         -- (iv30 - rv30) / iv30
        iv_rv_zscore REAL,                         -- z-score of (iv30 - rv30)
        regime_score REAL,                         -- 0-100 composite
        signal       TEXT,                         -- BUY_PREMIUM | NEUTRAL | …
        UNIQUE(ticker_id, date)
    )
    """,

    # ------------------------------------------------------------------
    # macro_snapshots — one market-wide row per trading day
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS macro_snapshots (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        date          TEXT    NOT NULL UNIQUE,     -- YYYY-MM-DD
        vix           REAL,
        vix_regime    TEXT,
        market_regime REAL,
        market_signal TEXT
    )
    """,

    # ------------------------------------------------------------------
    # trade_signals — trade recommendation per ticker per day
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS trade_signals (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker_id    INTEGER NOT NULL REFERENCES tickers(id) ON DELETE CASCADE,
        date         TEXT    NOT NULL,
        signal       TEXT    NOT NULL,
        regime_score REAL,
        iv_rank      REAL,
        iv_rv_ratio  REAL,
        iv_rv_zscore REAL,
        vrp          REAL,
        UNIQUE(ticker_id, date)
    )
    """,

    # ------------------------------------------------------------------
    # Indices for common query patterns
    # ------------------------------------------------------------------
    "CREATE INDEX IF NOT EXISTS idx_ovs_ticker_date ON option_volatility_snapshots(ticker_id, date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ts_ticker_date  ON trade_signals(ticker_id, date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_dp_ticker_date  ON daily_prices(ticker_id, date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_macro_date      ON macro_snapshots(date DESC)",
]

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    """
    Open (or create) the SQLite database and return a connection.

    Caller is responsible for closing.  Use ``with get_db() as db:`` for
    auto-close in non-context-manager usage, or prefer the ``open_db()``
    context manager below.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL") # safe concurrent writes
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def open_db():
    """Context manager that commits on success, rolls back on error."""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables and indices (idempotent — safe to call every startup)."""
    with open_db() as db:
        for stmt in _CREATE_STMTS:
            db.execute(stmt)
