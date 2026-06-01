"""
app/data/forward_returns.py
----------------------------
Forward return and realized-volatility calculation for scanner signal
back-testing.

All arithmetic uses adjusted daily close prices fetched from Polygon's
aggregate bars endpoint.

Calculation conventions
-----------------------
- Entry price  : adjusted close on the signal's ``scan_date``.
- Exit price   : adjusted close on the N-th *trading day* after ``scan_date``.
  (e.g. for N=5, we take ``closes[5]`` when closes[0] is scan_date.)
- Forward return (N-day)  : (exit / entry - 1) × 100, as %.
- Realized vol (N-day)    : annualised sample-std of N daily log-returns,
  i.e. sqrt(252) × std(log(p[t+1]/p[t]) for t in 0..N-1) × 100 as %.
- Premium capture (N-day) : IV30_at_signal − RV_N  (vol points).
  Positive ⟹ options richness was realised; seller kept premium.

Data availability
-----------------
``outcome_status`` is set to:
  ``pending``  – not a single window is complete
  ``partial``  – at least the 5-day window is done, but not the 20-day
  ``complete`` – all three windows (5, 10, 20) are done
"""

from __future__ import annotations

import datetime
import logging
import math
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WINDOWS         = [5, 10, 20]
_CALENDAR_DAYS  = 38   # fetch window: ~38 calendar days covers 20+ trading days


# ---------------------------------------------------------------------------
# Price fetching (Polygon daily aggs)
# ---------------------------------------------------------------------------

def fetch_daily_closes(
    ticker:    str,
    from_date: str,
    to_date:   str,
    api_key:   str | None = None,
) -> list[tuple[str, float]]:
    """
    Return ``[(date_str, adjusted_close), ...]`` sorted ascending.

    Parameters
    ----------
    ticker    : e.g. ``"AAPL"``
    from_date : ISO date, inclusive — should be the signal's scan_date
    to_date   : ISO date, inclusive
    api_key   : falls back to ``POLYGON_API_KEY`` environment variable
    """
    key = api_key or os.environ.get("POLYGON_API_KEY", "")
    if not key:
        raise EnvironmentError(
            "POLYGON_API_KEY is not set. "
            "Export it before running calculate_outcomes."
        )

    from polygon import RESTClient  # polygon-api-client
    client = RESTClient(key)

    bars = client.list_aggs(
        ticker=ticker,
        multiplier=1,
        timespan="day",
        from_=from_date,
        to=to_date,
        adjusted=True,
        sort="asc",
        limit=60,
    )

    results: list[tuple[str, float]] = []
    for bar in bars:
        dt = datetime.datetime.fromtimestamp(
            bar.timestamp / 1_000, tz=datetime.timezone.utc
        )
        results.append((dt.strftime("%Y-%m-%d"), float(bar.close)))

    return sorted(results, key=lambda x: x[0])


# ---------------------------------------------------------------------------
# Math
# ---------------------------------------------------------------------------

def _annualized_rv(log_returns: list[float]) -> float:
    """
    Annualized realized volatility (%) from a list of daily log-returns.
    Uses sample (N-1) variance, annualises with sqrt(252).
    Returns NaN if fewer than 2 observations.
    """
    n = len(log_returns)
    if n < 2:
        return float("nan")
    mu  = sum(log_returns) / n
    var = sum((r - mu) ** 2 for r in log_returns) / (n - 1)
    return math.sqrt(var * 252) * 100.0


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------

def calc_outcomes(
    iv30_at_signal: float,
    closes: list[tuple[str, float]],
) -> dict:
    """
    Compute forward outcomes from a sequence of daily closes.

    ``closes`` must be sorted ascending, with ``closes[0]`` being the
    signal's scan_date close (the entry price).  Each element is
    ``(date_str, adjusted_close)``.

    Returns a dict with keys:
      ``fwd_ret_{5,10,20}d``           float | None  (%)
      ``fwd_rv{5,10,20}d``             float | None  (annualised %)
      ``premium_capture_{5,10,20}d``   float | None  (vol pts)
      ``outcome_status``               str
    """
    if not closes:
        return _empty_outcomes()

    prices   = [p for _, p in closes]
    n        = len(prices)   # prices[0] = entry (scan_date close)

    # Daily log-returns: log(prices[i+1] / prices[i])
    log_rets = [
        math.log(prices[i + 1] / prices[i])
        for i in range(n - 1)
    ]

    result:    dict = {}
    done_flags: list[bool] = []

    for w in WINDOWS:
        ret_k = f"fwd_ret_{w}d"
        rv_k  = f"fwd_rv{w}d"
        cap_k = f"premium_capture_{w}d"

        # Need prices[0..w] → n > w  (i.e. at least w+1 prices)
        if n > w:
            entry  = prices[0]
            exit_p = prices[w]
            ret    = (exit_p / entry - 1.0) * 100.0
            rv     = _annualized_rv(log_rets[:w])
            cap    = iv30_at_signal - rv

            result[ret_k] = round(ret, 4)
            result[rv_k]  = round(rv,  2)
            result[cap_k] = round(cap, 2)
            done_flags.append(True)
        else:
            result[ret_k] = None
            result[rv_k]  = None
            result[cap_k] = None
            done_flags.append(False)

    if all(done_flags):
        result["outcome_status"] = "complete"
    elif any(done_flags):
        result["outcome_status"] = "partial"
    else:
        result["outcome_status"] = "pending"

    return result


def _empty_outcomes() -> dict:
    """Return an all-None outcome dict with ``pending`` status."""
    out: dict = {}
    for w in WINDOWS:
        out[f"fwd_ret_{w}d"]        = None
        out[f"fwd_rv{w}d"]          = None
        out[f"premium_capture_{w}d"] = None
    out["outcome_status"] = "pending"
    return out


# ---------------------------------------------------------------------------
# High-level convenience wrapper
# ---------------------------------------------------------------------------

def compute_signal_outcomes(
    ticker:    str,
    scan_date: str,
    iv30:      float,
    api_key:   str | None = None,
) -> dict:
    """
    Fetch price history from Polygon and compute all outcomes for one signal.

    Parameters
    ----------
    ticker    : e.g. ``"AAPL"``
    scan_date : ISO date string, e.g. ``"2026-05-27"``
    iv30      : annualised IV30 at signal time (as %, e.g. 24.5)
    api_key   : falls back to ``POLYGON_API_KEY`` env var

    Returns
    -------
    Dict with outcome columns + ``outcome_status``.
    All numeric values are ``None`` when data is unavailable.
    """
    today_str = datetime.date.today().isoformat()
    to_date   = min(
        (
            datetime.date.fromisoformat(scan_date)
            + datetime.timedelta(days=_CALENDAR_DAYS)
        ).isoformat(),
        today_str,
    )

    try:
        closes = fetch_daily_closes(ticker, scan_date, to_date, api_key)
    except Exception as exc:
        logger.warning(
            "forward_returns: price fetch failed for %s %s: %s",
            ticker, scan_date, exc,
        )
        return _empty_outcomes()

    if not closes:
        logger.debug("forward_returns: no closes for %s from %s", ticker, scan_date)
        return _empty_outcomes()

    return calc_outcomes(iv30, closes)
