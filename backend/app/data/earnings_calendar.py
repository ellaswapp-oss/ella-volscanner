"""
app/data/earnings_calendar.py
------------------------------
Earnings / major-event calendar for the S&P 500 scanner.

Returns the next confirmed or estimated earnings date for a ticker so the
scanner can flag elevated IV that may be event-driven rather than structurally
mispriced.

Source priority
---------------
1. Live Polygon earnings endpoint (GET /vX/reference/financials) if key set
   — currently a placeholder; Polygon's public tier returns historical only.
2. Manual calendar  (static dict below — maintained quarterly)
3. Unknown → returns None for all date fields.

Adding a live source
--------------------
Replace ``_fetch_live(ticker)`` with a real Polygon / Finnhub / Nasdaq call.
The rest of the pipeline is already wired.

Manual calendar maintenance
---------------------------
Dates are ISO strings ``"YYYY-MM-DD"`` representing the expected REPORT date
(after-market or before-market close — intra-day precision not required).
Keep entries sorted chronologically; the loader picks the first date ≥ today.
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Manual earnings calendar
# Key   → ticker (uppercase)
# Value → list of ISO report-date strings, in chronological order
#
# Covers the top-50 S&P 500 names scanned by default.
# Updated for the Q1 / Q2 2026 reporting cycle (as of 2026-05-27).
# ---------------------------------------------------------------------------

_MANUAL_CALENDAR: dict[str, list[str]] = {
    # ── Imminent / near-term (reporting May–June 2026) ─────────────────────
    # NVDA  fiscal yr ends Jan; Q1 FY2027 (Feb–Apr) → late May
    "NVDA":  ["2026-05-28", "2026-08-27"],
    # CRM   fiscal yr ends Jan; Q1 FY2027 (Feb–Apr) → late May
    "CRM":   ["2026-05-28", "2026-08-27"],
    # COST  fiscal yr ends Aug; Q3 FY2026 (Mar–May) → early June
    "COST":  ["2026-06-05", "2026-09-25"],
    # AVGO  fiscal yr ends Oct; Q2 FY2026 (Feb–Apr) → early June
    "AVGO":  ["2026-06-11", "2026-09-10"],
    # ADBE  fiscal yr ends Nov; Q2 FY2026 (Mar–May) → mid June
    "ADBE":  ["2026-06-18", "2026-09-17"],
    # ACN   fiscal yr ends Aug; Q3 FY2026 (Mar–May) → mid June
    "ACN":   ["2026-06-19", "2026-09-25"],
    # ORCL  fiscal yr ends May; Q4 FY2026 (Mar–May) → late June
    "ORCL":  ["2026-06-25", "2026-09-17"],

    # ── Q2 2026 results cycle (reporting July–August 2026) ─────────────────
    "JPM":   ["2026-07-14", "2026-10-13"],
    "BAC":   ["2026-07-14", "2026-10-13"],
    "GS":    ["2026-07-14", "2026-10-14"],
    "TSLA":  ["2026-07-22", "2026-10-20"],
    "GOOGL": ["2026-07-28", "2026-10-27"],
    "GOOG":  ["2026-07-28", "2026-10-27"],
    "META":  ["2026-07-29", "2026-10-28"],
    "V":     ["2026-07-28", "2026-10-27"],
    "MA":    ["2026-07-29", "2026-10-28"],
    "MSFT":  ["2026-07-29", "2026-10-28"],
    "ABBV":  ["2026-07-28", "2026-10-27"],
    "KO":    ["2026-07-23", "2026-10-22"],
    "MCD":   ["2026-07-28", "2026-10-27"],
    "AAPL":  ["2026-07-31", "2026-10-30"],
    "AMZN":  ["2026-08-01", "2026-10-30"],
    "PG":    ["2026-07-25", "2026-10-20"],
    "JNJ":   ["2026-07-15", "2026-10-14"],
    "LIN":   ["2026-07-31", "2026-10-28"],
    "PEP":   ["2026-07-14", "2026-10-07"],
    "HD":    ["2026-08-12", "2026-11-18"],
    "QCOM":  ["2026-07-29", "2026-10-29"],    # Q3 FY2026
    "BRK.B": ["2026-08-07", "2026-11-06"],
    "AMD":   ["2026-07-28", "2026-10-27"],
    "CAT":   ["2026-07-28", "2026-10-27"],
    "ABT":   ["2026-07-16", "2026-10-15"],
    "ACN":   ["2026-06-19", "2026-09-25"],    # already listed above; harmless dup
}

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def _fetch_live(ticker: str) -> Optional[datetime.date]:
    """
    Placeholder for a live earnings-date API call.

    To activate: replace this stub with a Polygon /vX/reference/financials,
    Finnhub /calendar/earnings, or Nasdaq earnings calendar request.
    Return the next report date as a ``datetime.date``, or None on failure.
    """
    return None


def get_next_earnings(
    ticker: str,
    today: datetime.date | None = None,
) -> Optional[datetime.date]:
    """
    Return the next earnings date for *ticker* on or after *today*.

    Tries live source first, then falls back to the manual calendar.
    Returns None when the date is unknown.
    """
    if today is None:
        today = datetime.date.today()

    # 1. Live source (placeholder — returns None by default)
    try:
        live = _fetch_live(ticker)
        if live is not None and live >= today:
            return live
    except Exception as exc:
        logger.debug("Earnings live fetch failed for %s: %s", ticker, exc)

    # 2. Manual calendar
    dates = _MANUAL_CALENDAR.get(ticker.upper(), [])
    for ds in dates:
        try:
            d = datetime.date.fromisoformat(ds)
            if d >= today:
                return d
        except ValueError:
            logger.warning("Bad date in earnings calendar for %s: %r", ticker, ds)

    return None


def get_earnings_info(
    ticker: str,
    today: datetime.date | None = None,
) -> dict:
    """
    Return a dict of earnings-awareness fields for one ticker.

    Fields
    ------
    next_earnings_date   : str | None   — ISO date of next report
    days_to_earnings     : int | None   — calendar days from today
    earnings_within_14d  : bool         — True if ≤14 days away
    earnings_within_30d  : bool         — True if ≤30 days away
    event_risk_flag      : bool         — True if any event risk detected
    event_risk_reason    : str | None   — human-readable reason
    score_penalty        : float        — recommended score deduction (0–20)
    """
    if today is None:
        today = datetime.date.today()

    nxt = get_next_earnings(ticker, today)

    if nxt is None:
        return {
            "next_earnings_date":  None,
            "days_to_earnings":    None,
            "earnings_within_14d": False,
            "earnings_within_30d": False,
            "event_risk_flag":     False,
            "event_risk_reason":   None,
            "score_penalty":       0.0,
        }

    dte = (nxt - today).days

    within_14 = dte <= 14
    within_30 = dte <= 30
    risk_flag = within_30

    if dte <= 7:
        penalty = 20.0
        reason  = f"Earnings in {dte}d — IV likely pricing event premium"
    elif dte <= 14:
        penalty = 15.0
        reason  = f"Earnings in {dte}d — elevated IV may be event-driven"
    elif dte <= 30:
        penalty = 10.0
        reason  = f"Earnings in {dte}d — monitor; IV may widen further"
    else:
        penalty = 0.0
        reason  = None
        risk_flag = False

    return {
        "next_earnings_date":  nxt.isoformat(),
        "days_to_earnings":    dte,
        "earnings_within_14d": within_14,
        "earnings_within_30d": within_30,
        "event_risk_flag":     risk_flag,
        "event_risk_reason":   reason,
        "score_penalty":       penalty,
    }
