"""
api/history.py
--------------
Historical signal and snapshot endpoints.

Endpoints
---------
GET /api/history/{ticker}?days=90
    Last N daily rows: IV surface, RV, derived metrics, signal.

GET /api/history/macro?days=90
    Last N daily market-wide regime + VIX snapshots.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.db.schema import init_db
from app.db.repository import get_ticker_id, get_vol_history, get_macro_history

router = APIRouter(prefix="/api/history", tags=["history"])

# Ensure tables exist (no-op if already created by save_snapshot.py)
init_db()


# ---------------------------------------------------------------------------
# Macro history  (define before /{ticker} so path is matched first)
# ---------------------------------------------------------------------------

@router.get("/macro")
def macro_history(
    days: int = Query(90, ge=1, le=500, description="Number of trading days to return"),
) -> dict:
    """
    Return the last ``days`` daily market-wide regime / VIX rows,
    newest first.

    If the database has not been seeded yet (save_snapshot.py was not run),
    returns an empty list so the frontend can still render gracefully.
    """
    rows = get_macro_history(days=days)
    return {"rows": rows, "data_source": "db" if rows else "empty"}


# ---------------------------------------------------------------------------
# Per-ticker history
# ---------------------------------------------------------------------------

@router.get("/{ticker}")
def ticker_history(
    ticker: str,
    days: int = Query(90, ge=1, le=500, description="Number of trading days to return"),
) -> dict:
    """
    Return the last ``days`` daily volatility snapshots for one ticker,
    newest first.

    Each row includes: date, iv7/30/60/90, rv10/20/30/60, iv_rank,
    iv_rv_ratio, vrp, iv_rv_zscore, regime_score, signal.

    If the database has not been seeded (save_snapshot.py was not run),
    returns an empty list so the frontend renders gracefully.
    """
    sym = ticker.upper()
    if sym not in settings.tickers:
        raise HTTPException(
            status_code=404,
            detail=f"{sym!r} not in supported tickers: {sorted(settings.tickers)}",
        )

    tid = get_ticker_id(sym)
    if tid is None:
        # DB not seeded yet — return empty list, not an error
        return {"ticker": sym, "rows": [], "data_source": "empty"}

    rows = get_vol_history(tid, days=days)
    return {"ticker": sym, "rows": rows, "data_source": "db"}
