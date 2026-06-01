"""
db/repository.py
----------------
Thin read/write helpers.  All SQL lives here; callers never build queries.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.db.schema import open_db

# ---------------------------------------------------------------------------
# Tickers
# ---------------------------------------------------------------------------

def upsert_ticker(symbol: str, name: str | None = None) -> int:
    """Insert or ignore a ticker row; return its id."""
    with open_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO tickers(symbol, name) VALUES (?, ?)",
            (symbol.upper(), name),
        )
        row = db.execute(
            "SELECT id FROM tickers WHERE symbol = ?", (symbol.upper(),)
        ).fetchone()
    return row["id"]


def get_ticker_id(symbol: str) -> int | None:
    with open_db() as db:
        row = db.execute(
            "SELECT id FROM tickers WHERE symbol = ?", (symbol.upper(),)
        ).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# Daily prices
# ---------------------------------------------------------------------------

def upsert_daily_price(ticker_id: int, date: str, close: float,
                        open_: float | None = None, high: float | None = None,
                        low: float | None = None, volume: int | None = None) -> None:
    with open_db() as db:
        db.execute(
            """
            INSERT INTO daily_prices(ticker_id, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker_id, date) DO UPDATE SET
                open   = excluded.open,
                high   = excluded.high,
                low    = excluded.low,
                close  = excluded.close,
                volume = excluded.volume
            """,
            (ticker_id, date, open_, high, low, close, volume),
        )


def bulk_upsert_daily_prices(rows: list[dict]) -> None:
    """
    rows: list of dicts with keys ticker_id, date, close, and optionally
          open, high, low, volume.
    """
    with open_db() as db:
        db.executemany(
            """
            INSERT INTO daily_prices(ticker_id, date, open, high, low, close, volume)
            VALUES (:ticker_id, :date, :open, :high, :low, :close, :volume)
            ON CONFLICT(ticker_id, date) DO UPDATE SET
                open   = excluded.open,
                high   = excluded.high,
                low    = excluded.low,
                close  = excluded.close,
                volume = excluded.volume
            """,
            rows,
        )


# ---------------------------------------------------------------------------
# Volatility snapshots
# ---------------------------------------------------------------------------

def upsert_vol_snapshot(row: dict) -> None:
    """Insert or replace one option_volatility_snapshots row."""
    with open_db() as db:
        db.execute(
            """
            INSERT INTO option_volatility_snapshots
                (ticker_id, date, iv7, iv30, iv60, iv90,
                 rv10, rv20, rv30, rv60,
                 iv_rank, iv_rv_ratio, vrp, iv_rv_zscore, regime_score, signal)
            VALUES
                (:ticker_id, :date, :iv7, :iv30, :iv60, :iv90,
                 :rv10, :rv20, :rv30, :rv60,
                 :iv_rank, :iv_rv_ratio, :vrp, :iv_rv_zscore, :regime_score, :signal)
            ON CONFLICT(ticker_id, date) DO UPDATE SET
                iv7          = excluded.iv7,
                iv30         = excluded.iv30,
                iv60         = excluded.iv60,
                iv90         = excluded.iv90,
                rv10         = excluded.rv10,
                rv20         = excluded.rv20,
                rv30         = excluded.rv30,
                rv60         = excluded.rv60,
                iv_rank      = excluded.iv_rank,
                iv_rv_ratio  = excluded.iv_rv_ratio,
                vrp          = excluded.vrp,
                iv_rv_zscore = excluded.iv_rv_zscore,
                regime_score = excluded.regime_score,
                signal       = excluded.signal
            """,
            row,
        )


def bulk_upsert_vol_snapshots(rows: list[dict]) -> None:
    with open_db() as db:
        db.executemany(
            """
            INSERT INTO option_volatility_snapshots
                (ticker_id, date, iv7, iv30, iv60, iv90,
                 rv10, rv20, rv30, rv60,
                 iv_rank, iv_rv_ratio, vrp, iv_rv_zscore, regime_score, signal)
            VALUES
                (:ticker_id, :date, :iv7, :iv30, :iv60, :iv90,
                 :rv10, :rv20, :rv30, :rv60,
                 :iv_rank, :iv_rv_ratio, :vrp, :iv_rv_zscore, :regime_score, :signal)
            ON CONFLICT(ticker_id, date) DO UPDATE SET
                iv7          = excluded.iv7,
                iv30         = excluded.iv30,
                iv60         = excluded.iv60,
                iv90         = excluded.iv90,
                rv10         = excluded.rv10,
                rv20         = excluded.rv20,
                rv30         = excluded.rv30,
                rv60         = excluded.rv60,
                iv_rank      = excluded.iv_rank,
                iv_rv_ratio  = excluded.iv_rv_ratio,
                vrp          = excluded.vrp,
                iv_rv_zscore = excluded.iv_rv_zscore,
                regime_score = excluded.regime_score,
                signal       = excluded.signal
            """,
            rows,
        )


def get_vol_history(ticker_id: int, days: int = 90) -> list[dict]:
    """Return the last ``days`` volatility snapshots, newest first."""
    with open_db() as db:
        rows = db.execute(
            """
            SELECT date, iv7, iv30, iv60, iv90,
                   rv10, rv20, rv30, rv60,
                   iv_rank, iv_rv_ratio, vrp, iv_rv_zscore, regime_score, signal
            FROM   option_volatility_snapshots
            WHERE  ticker_id = ?
            ORDER  BY date DESC
            LIMIT  ?
            """,
            (ticker_id, days),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Macro snapshots
# ---------------------------------------------------------------------------

def upsert_macro_snapshot(row: dict) -> None:
    with open_db() as db:
        db.execute(
            """
            INSERT INTO macro_snapshots(date, vix, vix_regime, market_regime, market_signal)
            VALUES (:date, :vix, :vix_regime, :market_regime, :market_signal)
            ON CONFLICT(date) DO UPDATE SET
                vix           = excluded.vix,
                vix_regime    = excluded.vix_regime,
                market_regime = excluded.market_regime,
                market_signal = excluded.market_signal
            """,
            row,
        )


def bulk_upsert_macro_snapshots(rows: list[dict]) -> None:
    with open_db() as db:
        db.executemany(
            """
            INSERT INTO macro_snapshots(date, vix, vix_regime, market_regime, market_signal)
            VALUES (:date, :vix, :vix_regime, :market_regime, :market_signal)
            ON CONFLICT(date) DO UPDATE SET
                vix           = excluded.vix,
                vix_regime    = excluded.vix_regime,
                market_regime = excluded.market_regime,
                market_signal = excluded.market_signal
            """,
            rows,
        )


def get_macro_history(days: int = 90) -> list[dict]:
    with open_db() as db:
        rows = db.execute(
            """
            SELECT date, vix, vix_regime, market_regime, market_signal
            FROM   macro_snapshots
            ORDER  BY date DESC
            LIMIT  ?
            """,
            (days,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Trade signals
# ---------------------------------------------------------------------------

def bulk_upsert_trade_signals(rows: list[dict]) -> None:
    with open_db() as db:
        db.executemany(
            """
            INSERT INTO trade_signals
                (ticker_id, date, signal, regime_score, iv_rank, iv_rv_ratio,
                 iv_rv_zscore, vrp)
            VALUES
                (:ticker_id, :date, :signal, :regime_score, :iv_rank, :iv_rv_ratio,
                 :iv_rv_zscore, :vrp)
            ON CONFLICT(ticker_id, date) DO UPDATE SET
                signal       = excluded.signal,
                regime_score = excluded.regime_score,
                iv_rank      = excluded.iv_rank,
                iv_rv_ratio  = excluded.iv_rv_ratio,
                iv_rv_zscore = excluded.iv_rv_zscore,
                vrp          = excluded.vrp
            """,
            rows,
        )


def get_signal_history(ticker_id: int, days: int = 90) -> list[dict]:
    with open_db() as db:
        rows = db.execute(
            """
            SELECT ts.date, ts.signal, ts.regime_score,
                   ts.iv_rank, ts.iv_rv_ratio, ts.iv_rv_zscore, ts.vrp
            FROM   trade_signals ts
            WHERE  ts.ticker_id = ?
            ORDER  BY ts.date DESC
            LIMIT  ?
            """,
            (ticker_id, days),
        ).fetchall()
    return [dict(r) for r in rows]
