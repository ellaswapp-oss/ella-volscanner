"""
services/snapshot_builder.py
-----------------------------
Provider-agnostic snapshot dispatcher.

Routes snapshot requests to the appropriate service based on the active
data provider (DATA_PROVIDER env var):

    DATA_PROVIDER=real  → live_vol_service.build_live_snapshot()
    DATA_PROVIDER=mock  → vol_service.build_ticker_snapshot()  (+ metadata defaults)

Both paths return a dict with the same top-level keys, so market.py handlers
require no branching logic.
"""

from __future__ import annotations

import datetime

from app.data.registry import get_provider
from app.services.vol_service import build_ticker_snapshot, build_dashboard


def build_snapshot(ticker: str) -> dict:
    """
    Build a full ticker snapshot from the active provider.

    Parameters
    ----------
    ticker : str
        Uppercase ticker symbol (validated upstream by the API layer).

    Returns
    -------
    dict
        All keys from build_ticker_snapshot plus:
            provider          : "mock" | "real"
            iv_source         : "synthetic" | "live_options_chain" | "synthetic_fallback"
            iv_rank_is_proxy  : bool
            as_of_date        : "YYYY-MM-DD"
            macro_penalties   : {total_penalty, reasons, available}
            iv["iv14"]        : float | None
    """
    provider = get_provider()

    if provider.name == "real":
        from app.services.live_vol_service import build_live_snapshot
        return build_live_snapshot(ticker, provider)

    # ── Mock path ─────────────────────────────────────────────────────────────
    snap = build_ticker_snapshot(ticker)

    # Inject standard metadata fields
    snap["provider"]          = "mock"
    snap["iv_source"]         = "synthetic"
    snap["iv_rank_is_proxy"]  = False
    snap["as_of_date"]        = datetime.date.today().isoformat()
    snap["macro_penalties"]   = {
        "total_penalty": 0.0,
        "reasons":       [],
        "available":     False,
    }
    # iv14 is not produced by the mock IV history; default to None
    if "iv" in snap:
        snap["iv"]["iv14"] = None

    # Mock has iv7/iv30/iv60/iv90 from synthetic IV surface; iv14 intentionally absent.
    snap["iv_surface_metadata"] = {
        "iv_surface_status":        "healthy",
        "required_tenor_available": True,
        "available_tenors":         ["iv7", "iv30", "iv60", "iv90"],
        "missing_tenors":           ["iv14"],
        "term_structure_quality":   "full",
    }

    return snap


def build_dashboard_snapshot() -> dict:
    """
    Build the market-wide dashboard from the active provider.

    Phase 2 note: the regime endpoint still uses the mock build_dashboard()
    regardless of DATA_PROVIDER.  Full live regime support is Phase 3.
    """
    return build_dashboard()
