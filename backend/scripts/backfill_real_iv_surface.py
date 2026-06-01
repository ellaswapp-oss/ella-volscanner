"""
scripts/backfill_real_iv_surface.py
------------------------------------
Reconstruct historical ATM IV surfaces from Polygon historical options chains.

For each (ticker, date) pair this script:
  1. Fetches the end-of-day options chain snapshot from Polygon
     ``GET /v3/snapshot/options/{ticker}?date=YYYY-MM-DD``
  2. Computes ATM IV surface (iv7 / iv14 / iv30 / iv60 / iv90) using
     ``calculate_atm_iv_by_tenor()`` with the historical underlying price
     and the actual pricing date for correct DTE calculations.
  3. Validates the surface with ``validate_iv_surface()`` and records health.
  4. Persists to the Parquet IV store with
     ``source = "polygon_historical_options"``  (quality = "real").

Checkpointing
-------------
By default, dates already in the store are skipped (resume after interruption).
Pass ``--overwrite`` to replace existing rows.

Rate-limiting
-------------
Polygon enforces per-minute API limits.  The ``--delay`` flag (default 1.0s)
adds a pause between chain fetches.  On 429 responses, the script backs off
exponentially (up to ``--max-backoff`` seconds, default 60s).

Requirements
------------
  DATA_PROVIDER=real  (or set POLYGON_API_KEY in backend/.env)

Usage examples
--------------
Run from the ``backend/`` directory:

    # Backfill SPY for the last year
    DATA_PROVIDER=real python -m scripts.backfill_real_iv_surface --ticker SPY --days 252

    # Backfill all configured tickers over a specific range
    DATA_PROVIDER=real python -m scripts.backfill_real_iv_surface \\
        --start 2024-01-01 --end 2024-12-31

    # Dry-run to see what would be fetched without writing anything
    DATA_PROVIDER=real python -m scripts.backfill_real_iv_surface \\
        --ticker SPY --days 30 --dry-run --verbose

    # Slower rate to respect free-tier limits (5 req/min ≈ 12s between requests)
    DATA_PROVIDER=real python -m scripts.backfill_real_iv_surface \\
        --ticker SPY --days 252 --delay 12

    # Overwrite existing entries (re-fetch everything)
    DATA_PROVIDER=real python -m scripts.backfill_real_iv_surface \\
        --ticker SPY --days 30 --overwrite

Flags
-----
--tickers / --ticker  STR [STR ...]  Tickers to process (default: all settings.tickers)
--start               YYYY-MM-DD     Start of date range
--end                 YYYY-MM-DD     End of date range (default: today)
--days                N              Trailing trading days (alternative to --start)
--overwrite                          Replace existing store entries
--dry-run                            Report what would be fetched; write nothing
--verbose                            Print per-date progress (IV values + health)
--delay               FLOAT          Seconds between API calls (default 1.0)
--max-backoff         FLOAT          Max seconds to wait after a 429 (default 60)
--max-retries         INT            Retries per request on transient errors (default 3)
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import math
import os
import pathlib
import sys
import time
import traceback

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

import requests

from app.core.config import settings
from app.data.iv_store import store as _store
from app.data.iv_surface_health import validate_iv_surface
from app.data.options_utils import OPTION_COLUMNS, normalize_polygon_contract, calculate_atm_iv_by_tenor

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_real_iv")

# ---------------------------------------------------------------------------
# Polygon API constants (mirror real_provider.py, no import to keep script standalone)
# ---------------------------------------------------------------------------

_POLYGON_BASE       = "https://api.polygon.io"
_OPTIONS_SNAPSHOT   = "/v3/snapshot/options/{ticker}"
_AGGS_ENDPOINT      = "/v2/aggs/ticker/{ticker}/range/1/day/{from_}/{to}"
_MAX_OPTION_PAGES   = 20          # 20 × 250 = 5 000 contracts max
_REQUEST_TIMEOUT    = 20          # seconds; historical fetches can be slower

# Cache paths (same conventions as real_provider.py)
_OPTIONS_CACHE = _BACKEND_ROOT / "data_cache" / "options"
_PRICES_CACHE  = _BACKEND_ROOT / "data_cache" / "prices"

# Source label for historical Polygon data
_SOURCE = "polygon_historical_options"


# ---------------------------------------------------------------------------
# Trading calendar helper
# ---------------------------------------------------------------------------

def _trading_days(start: datetime.date, end: datetime.date) -> list[datetime.date]:
    """Return all Mon–Fri dates in [start, end] as a proxy for US trading days."""
    days: list[datetime.date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += datetime.timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# Cache helpers (compatible with real_provider.py cache format)
# ---------------------------------------------------------------------------

def _options_cache_path(ticker: str, date: datetime.date) -> pathlib.Path:
    return _OPTIONS_CACHE / f"{ticker.upper()}_{date}.json"


def _prices_cache_path(ticker: str, start: datetime.date, end: datetime.date) -> pathlib.Path:
    return _PRICES_CACHE / f"{ticker.upper()}_{start}_{end}.json"


def _save_options_cache(df_rows: list[dict], path: pathlib.Path) -> None:
    """Save normalised option rows to JSON cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"columns": OPTION_COLUMNS, "records": df_rows}, indent=2))


def _load_options_cache(path: pathlib.Path) -> list[dict]:
    """Load normalised option rows from JSON cache. Returns empty list if unreadable."""
    try:
        data = json.loads(path.read_text())
        return data.get("records", [])
    except Exception as exc:
        logger.debug("Cache read failed for %s: %s", path, exc)
        return []


# ---------------------------------------------------------------------------
# Rate-limited Polygon fetch
# ---------------------------------------------------------------------------

def _polygon_historical_chain(
    ticker: str,
    date: datetime.date,
    api_key: str,
    *,
    delay: float = 1.0,
    max_backoff: float = 60.0,
    max_retries: int = 3,
) -> list[dict] | None:
    """
    Fetch the end-of-day options chain snapshot for ``ticker`` on ``date``.

    Uses ``GET /v3/snapshot/options/{ticker}?date=YYYY-MM-DD``.
    Paginates via ``next_url`` (up to ``_MAX_OPTION_PAGES`` pages).

    Returns
    -------
    list[dict]
        Raw Polygon contract dicts, or ``None`` on fatal error.

    Notes
    -----
    Historical options snapshots require at minimum a Polygon Starter
    subscription.  Free-tier accounts will receive HTTP 403.
    """
    cache = _options_cache_path(ticker, date)
    if cache.exists():
        cached = _load_options_cache(cache)
        if cached or cache.stat().st_size > 50:     # non-empty cache → use it
            logger.debug("Options cache hit: %s", cache)
            return cached

    url    = _POLYGON_BASE + _OPTIONS_SNAPSHOT.format(ticker=ticker)
    params: dict = {"limit": 250, "apiKey": api_key, "date": date.isoformat()}
    contracts: list[dict] = []

    for page in range(_MAX_OPTION_PAGES):
        resp = None
        for attempt in range(max_retries + 1):
            try:
                resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)
                break
            except requests.RequestException as exc:
                if attempt == max_retries:
                    logger.warning(
                        "Network error fetching %s %s (page %d): %s",
                        ticker, date, page + 1, exc,
                    )
                    return None
                wait = min(2.0 ** attempt, max_backoff)
                logger.debug("Request error, retry %d in %.0fs: %s", attempt + 1, wait, exc)
                time.sleep(wait)

        if resp is None:
            return None

        # Handle rate limiting
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", min(max_backoff, 60)))
            wait = min(retry_after, max_backoff)
            logger.warning("Rate-limited (429). Waiting %.0fs …", wait)
            time.sleep(wait)
            # Retry the same URL
            continue

        if resp.status_code == 403:
            logger.error(
                "Polygon returned 403 for %s on %s. "
                "Historical options snapshots require a Polygon Starter subscription or higher. "
                "Check your POLYGON_API_KEY and account tier.",
                ticker, date,
            )
            return None

        if resp.status_code == 404:
            logger.debug("No options data for %s on %s (404).", ticker, date)
            _save_options_cache([], cache)   # cache the empty result
            return []

        if not resp.ok:
            logger.warning(
                "Polygon HTTP %d for %s on %s: %s",
                resp.status_code, ticker, date, resp.text[:200],
            )
            return None

        # Guard against truncated JSON — Polygon occasionally returns a
        # partial response on busy pages.  Treat as transient; retry.
        try:
            data = resp.json()
        except ValueError as exc:
            logger.warning(
                "Truncated JSON for %s on %s (page %d, %d bytes): %s — retrying",
                ticker, date, page + 1, len(resp.content), exc,
            )
            time.sleep(min(2.0, max_backoff))
            continue

        status  = data.get("status", "")
        results = data.get("results") or []

        if status == "NOT_FOUND":
            logger.debug("NOT_FOUND: %s on %s — no options data available.", ticker, date)
            _save_options_cache([], cache)
            return []

        contracts.extend(results)

        next_url = data.get("next_url")
        if not next_url:
            break

        url    = next_url
        params = {"apiKey": api_key} if "apiKey=" not in next_url else {}
        logger.debug("Page %d: %d contracts so far for %s %s", page + 1, len(contracts), ticker, date)

        # Brief pause between pagination requests
        if delay > 0:
            time.sleep(min(delay * 0.2, 0.5))   # intra-request pause (fraction of full delay)

    # Normalise and cache
    rows = [normalize_polygon_contract(c) for c in contracts]
    _save_options_cache(rows, cache)
    logger.debug("Fetched %d contracts for %s %s", len(rows), ticker, date)
    return rows


# ---------------------------------------------------------------------------
# Bulk underlying price fetch  (one API call covers the whole date range)
# ---------------------------------------------------------------------------

def _bulk_fetch_prices(
    ticker: str,
    start_date: datetime.date,
    end_date: datetime.date,
    api_key: str,
    *,
    max_retries: int = 3,
) -> dict[str, float]:
    """
    Return a dict mapping "YYYY-MM-DD" → adjusted closing price for every
    trading day in [start_date, end_date].

    Strategy
    --------
    1. Scan all existing price-cache JSON files written by real_provider.py.
       They are lists of ``{"date": "YYYY-MM-DD", "close": float, ...}`` dicts.
    2. Fetch any remaining gap from Polygon /v2/aggs in a single call
       (limit=50000 covers ~200 years of daily bars).
    3. Write the fetched bars back to the price cache so real_provider.py
       can reuse them on subsequent runs.

    Making one bulk call instead of a per-date call avoids the rate-limit
    burst that occurs immediately after each options-chain page-fetch.
    """
    prices: dict[str, float] = {}
    ticker_up = ticker.upper()

    # ── Step 1: harvest existing price cache files ────────────────────────
    for cached_file in sorted(_PRICES_CACHE.glob(f"{ticker_up}_*.json")):
        try:
            raw = json.loads(cached_file.read_text())
            # real_provider.py saves OHLCV as a plain list of row-dicts
            rows = raw if isinstance(raw, list) else raw.get("records", [])
            for row in rows:
                d = row.get("date")
                c = row.get("close")
                if d and c is not None:
                    try:
                        prices[d] = float(c)
                    except (TypeError, ValueError):
                        pass
        except Exception:
            continue

    # ── Step 2: identify date range not covered by cache ──────────────────
    # We need [start_date, end_date].  If the cache already has the full
    # range there's nothing to fetch.
    need_start = start_date
    need_end   = end_date
    # Shift need_start forward past any dates we already have
    cur = start_date
    while cur <= end_date and cur.isoformat() in prices:
        cur += datetime.timedelta(days=1)
    if cur > end_date:
        logger.debug("Price cache fully covers %s %s→%s (%d bars)",
                     ticker_up, start_date, end_date, len(prices))
        return prices
    need_start = cur

    # ── Step 3: fetch bulk from Polygon aggs ──────────────────────────────
    # Add a 5-day buffer on each side to handle weekends / holidays at the
    # edges of the range so the first/last calendar dates get a valid bar.
    fetch_start = need_start - datetime.timedelta(days=5)
    fetch_end   = need_end   + datetime.timedelta(days=5)

    url = _POLYGON_BASE + _AGGS_ENDPOINT.format(
        ticker=ticker_up,
        from_=fetch_start.isoformat(),
        to=fetch_end.isoformat(),
    )
    params = {
        "adjusted": "true",
        "sort":     "asc",
        "limit":    "50000",   # covers ~200 years of daily bars
        "apiKey":   api_key,
    }

    resp = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)
            break
        except requests.RequestException as exc:
            if attempt == max_retries:
                logger.warning("Bulk price fetch network error for %s: %s", ticker_up, exc)
                return prices
            time.sleep(2.0 ** attempt)

    if resp is None or not resp.ok:
        logger.warning(
            "Bulk price fetch HTTP %s for %s — using cache-only prices (%d bars)",
            resp.status_code if resp is not None else "N/A", ticker_up, len(prices),
        )
        return prices

    results = resp.json().get("results") or []
    new_bars: list[dict] = []
    for bar in results:
        ts    = bar.get("t", 0)
        d     = datetime.datetime.utcfromtimestamp(ts / 1000).date().isoformat()
        close = bar.get("c")
        if close is not None:
            prices[d] = float(close)
            new_bars.append({
                "date":   d,
                "open":   bar.get("o"),
                "high":   bar.get("h"),
                "low":    bar.get("l"),
                "close":  float(close),
                "volume": int(bar.get("v", 0)),
            })

    # ── Step 4: persist to cache for future runs ───────────────────────────
    if new_bars:
        cache_path = _prices_cache_path(ticker_up, fetch_start, fetch_end)
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(new_bars, indent=2))
        except Exception as exc:
            logger.debug("Could not write price cache for %s: %s", ticker_up, exc)

    logger.info(
        "Bulk price fetch: %d bars for %s (%s → %s), %d total in map",
        len(results), ticker_up, fetch_start, fetch_end, len(prices),
    )
    return prices


def _price_for_date(price_map: dict[str, float], date: datetime.date) -> float | None:
    """
    Look up the closing price for *date* with a ±5-day fallback window.

    Handles weekends and market holidays — returns the most-recent prior
    close when the target date itself has no bar.
    """
    for delta in range(6):   # today, then up to 5 prior calendar days
        key = (date - datetime.timedelta(days=delta)).isoformat()
        if key in price_map:
            return price_map[key]
    return None


# ---------------------------------------------------------------------------
# Per-date IV computation
# ---------------------------------------------------------------------------

def _compute_iv_for_date(
    ticker: str,
    date: datetime.date,
    api_key: str,
    price_map: dict[str, float],
    *,
    delay: float,
    max_backoff: float,
    max_retries: int,
) -> tuple[dict | None, str | None, str]:
    """
    Fetch chain + look up price → compute IV surface for one (ticker, date).

    Parameters
    ----------
    price_map : pre-built "YYYY-MM-DD" → close price dict from _bulk_fetch_prices().
                Avoids a per-date Polygon aggs call that would burst the rate limit
                immediately after the multi-page chain fetch.

    Returns
    -------
    (iv_surf, health_status, note)
        iv_surf       : dict {iv7/iv14/iv30/iv60/iv90} or None on failure
        health_status : "healthy" | "suspicious" | "invalid" | None
        note          : short human-readable status string
    """
    import pandas as pd

    # 1. Fetch raw contracts
    raw_contracts = _polygon_historical_chain(
        ticker, date, api_key,
        delay=delay, max_backoff=max_backoff, max_retries=max_retries,
    )

    if raw_contracts is None:
        return None, None, "fetch_error"

    if len(raw_contracts) == 0:
        return None, None, "no_data"

    # 2. Build DataFrame
    try:
        chain = pd.DataFrame(raw_contracts, columns=OPTION_COLUMNS)
        for col in ("strike", "bid", "ask", "mid", "last", "implied_volatility",
                    "delta", "gamma", "theta", "vega"):
            if col in chain.columns:
                chain[col] = pd.to_numeric(chain[col], errors="coerce")
    except Exception as exc:
        logger.debug("DataFrame construction failed for %s %s: %s", ticker, date, exc)
        return None, None, "parse_error"

    # 3. Underlying price — look up from pre-fetched map (no extra API call)
    underlying_price = _price_for_date(price_map, date)
    if underlying_price is None or underlying_price <= 0:
        return None, None, "no_price"

    # 4. Compute IV surface
    try:
        iv_raw = calculate_atm_iv_by_tenor(chain, underlying_price, pricing_date=date)
    except Exception as exc:
        logger.debug("IV computation failed for %s %s: %s", ticker, date, exc)
        return None, None, "iv_error"

    tenor_meta = iv_raw.pop("_tenor_meta", {})
    iv_surf = {k: iv_raw.get(k) for k in ("iv7", "iv14", "iv30", "iv60", "iv90")}

    # 5. Surface health check
    try:
        health_status, _warnings = validate_iv_surface(iv_surf, tenor_meta)
    except Exception:
        health_status = None

    # 6. Classify by tenor availability
    n_tenors = sum(1 for v in iv_surf.values() if v is not None)

    if n_tenors == 0:
        # All tenors None — Polygon returned the live chain for this historical
        # date (expirations are too far out for any tenor to be in-tolerance).
        # Treat as no_data so we don't write an all-NaN row to the store.
        return None, None, "no_data"

    if iv_surf.get("iv30") is None:
        # Some tenors available (e.g. iv60/iv90) but iv30 missing — still
        # useful as partial diagnostics; write with iv30=NaN.
        note = "no_iv30"
        return iv_surf, health_status, note

    note = f"ok_{n_tenors}t"   # e.g. "ok_3t" = 3 tenors available
    return iv_surf, health_status, note


# ---------------------------------------------------------------------------
# Per-ticker backfill loop
# ---------------------------------------------------------------------------

def backfill_ticker(
    ticker: str,
    dates: list[datetime.date],
    api_key: str,
    *,
    overwrite:   bool  = False,
    dry_run:     bool  = False,
    verbose:     bool  = False,
    delay:       float = 1.0,
    max_backoff: float = 60.0,
    max_retries: int   = 3,
) -> dict:
    """
    Backfill IV surfaces from Polygon historical options for *ticker*.

    Returns
    -------
    dict with keys: ticker, written, skipped, failed, no_data,
                    n_iv30_ok, n_full_surface, health_counts
    """
    stored_dates = _store.get_stored_dates(ticker) if not overwrite else set()

    # ── Pre-fetch the full price history in one Polygon aggs call ─────────
    # This avoids the per-date burst that rate-limits the price request
    # immediately after paginating a large options chain.
    price_map: dict[str, float] = {}
    if dates:
        logger.info("Pre-fetching price history for %s (%s → %s) …",
                    ticker, dates[0], dates[-1])
        price_map = _bulk_fetch_prices(
            ticker, dates[0], dates[-1], api_key, max_retries=max_retries,
        )
        if not price_map:
            logger.warning(
                "Could not fetch any prices for %s — all dates will fail with no_price. "
                "Check POLYGON_API_KEY and network connectivity.", ticker,
            )

    n_write        = 0
    n_skip         = 0
    n_fail         = 0
    n_no_data      = 0
    n_iv30_ok      = 0
    n_full_surface = 0
    health_counts: dict[str, int] = {"healthy": 0, "suspicious": 0, "invalid": 0, "unknown": 0}
    batch: list[dict] = []

    total = len(dates)
    for i, date in enumerate(dates):
        date_str = date.isoformat()

        # Checkpointing — skip already stored dates (unless overwrite)
        if date_str in stored_dates:
            n_skip += 1
            if verbose:
                logger.info("  [SKIP ] %s %s — already stored", ticker, date_str)
            continue

        # Fast skip: if price is not in map, the chain fetch would succeed but
        # the IV computation would fail with no_price.  Skip now to avoid the
        # expensive chain API call for dates outside Polygon's history window.
        if price_map and _price_for_date(price_map, date) is None:
            n_no_data += 1
            if verbose:
                logger.info(
                    "  [NODAT] %s %s — no price data (outside Polygon history window)",
                    ticker, date_str,
                )
            continue

        if verbose or (i % 20 == 0):
            logger.info(
                "  [%d/%d] %s %s …",
                i + 1, total, ticker, date_str,
            )

        # Fetch + compute
        try:
            iv_surf, health, note = _compute_iv_for_date(
                ticker, date, api_key, price_map,
                delay=delay, max_backoff=max_backoff, max_retries=max_retries,
            )
        except Exception as exc:
            logger.warning(
                "  [FAIL ] %s %s — unexpected error: %s",
                ticker, date_str, exc,
            )
            if verbose:
                traceback.print_exc()
            n_fail += 1
            if delay > 0:
                time.sleep(delay)
            continue

        # No data available for this date (market closed, Polygon gap, etc.)
        if note == "no_data":
            n_no_data += 1
            if verbose:
                logger.info("  [NODAT] %s %s — no options data from Polygon", ticker, date_str)
            if delay > 0:
                time.sleep(delay)
            continue

        # Fetch or computation error
        if iv_surf is None or note in ("fetch_error", "parse_error", "iv_error", "no_price"):
            n_fail += 1
            logger.warning("  [FAIL ] %s %s — %s", ticker, date_str, note)
            if delay > 0:
                time.sleep(delay)
            continue

        # Track IV30 availability and surface completeness
        if iv_surf.get("iv30") is not None:
            n_iv30_ok += 1
        n_non_null = sum(1 for v in iv_surf.values() if v is not None)
        if n_non_null == 5:
            n_full_surface += 1

        # Health count
        if health in health_counts:
            health_counts[health] += 1
        else:
            health_counts["unknown"] += 1

        if verbose:
            iv30 = iv_surf.get("iv30")
            iv7  = iv_surf.get("iv7")
            iv60 = iv_surf.get("iv60")
            logger.info(
                "  [%-5s] %s %s — iv30=%s%% iv7=%s%% iv60=%s%% tenors=%d %s",
                note, ticker, date_str,
                f"{iv30:.2f}" if iv30 is not None else "—",
                f"{iv7:.2f}"  if iv7  is not None else "—",
                f"{iv60:.2f}" if iv60 is not None else "—",
                n_non_null,
                f"[{health}]" if health else "",
            )

        if not dry_run:
            batch.append({"date": date_str, "iv_surf": iv_surf, "source": _SOURCE})
        n_write += 1

        # Rate limiting between chain fetches
        if delay > 0:
            time.sleep(delay)

    # Flush batch to store (single Parquet write per ticker)
    if batch and not dry_run:
        written = _store.upsert_batch(ticker, batch)
        if written != len(batch):
            logger.warning("%s: expected %d writes, got %d", ticker, len(batch), written)

    return {
        "ticker":        ticker,
        "written":       n_write,
        "skipped":       n_skip,
        "failed":        n_fail,
        "no_data":       n_no_data,
        "n_iv30_ok":     n_iv30_ok,
        "n_full_surface": n_full_surface,
        "health_counts": health_counts,
    }


# ---------------------------------------------------------------------------
# Diagnostics summary
# ---------------------------------------------------------------------------

def _print_diagnostics(results: list[dict], dry_run: bool) -> None:
    """Print a per-ticker diagnostics table after the backfill run."""
    suffix = " (dry-run — nothing persisted)" if dry_run else ""
    print(f"\n{'─' * 80}")
    print(f"  BACKFILL COMPLETE{suffix}")
    print(f"{'─' * 80}")
    print(f"  {'TICKER':<8}  {'WRITTEN':>7}  {'SKIP':>6}  {'FAIL':>6}  "
          f"{'NO_DATA':>7}  {'IV30%':>6}  {'FULL%':>6}  HEALTH")
    print(f"  {'─'*8}  {'─'*7}  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*6}  {'─'*6}  {'─'*20}")

    totals = {"written": 0, "skipped": 0, "failed": 0, "no_data": 0,
              "n_iv30_ok": 0, "n_full_surface": 0}
    total_processed = 0

    for r in results:
        n_processed = r["written"] + r["failed"]   # excludes skipped/no_data
        iv30_pct    = (r["n_iv30_ok"]      / n_processed * 100) if n_processed > 0 else 0.0
        full_pct    = (r["n_full_surface"] / n_processed * 100) if n_processed > 0 else 0.0
        hc          = r["health_counts"]
        health_str  = (
            f"H:{hc.get('healthy', 0)} S:{hc.get('suspicious', 0)} "
            f"X:{hc.get('invalid', 0)}"
        )
        print(
            f"  {r['ticker']:<8}  {r['written']:>7}  {r['skipped']:>6}  {r['failed']:>6}  "
            f"{r['no_data']:>7}  {iv30_pct:>5.1f}%  {full_pct:>5.1f}%  {health_str}"
        )
        for k in totals:
            totals[k] += r.get(k, 0)
        total_processed += n_processed

    if len(results) > 1:
        iv30_pct = (totals["n_iv30_ok"]      / total_processed * 100) if total_processed > 0 else 0.0
        full_pct = (totals["n_full_surface"] / total_processed * 100) if total_processed > 0 else 0.0
        print(f"  {'─'*8}  {'─'*7}  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*6}  {'─'*6}")
        print(
            f"  {'TOTAL':<8}  {totals['written']:>7}  {totals['skipped']:>6}  "
            f"{totals['failed']:>6}  {totals['no_data']:>7}  "
            f"{iv30_pct:>5.1f}%  {full_pct:>5.1f}%"
        )

    print(f"{'─' * 80}\n")
    print(
        f"  IV30% = fraction of processed dates with a valid IV30 (required for core signal)\n"
        f"  FULL% = fraction of processed dates with all 5 tenors (iv7/iv14/iv30/iv60/iv90)\n"
        f"  HEALTH: H=healthy  S=suspicious  X=invalid\n"
    )
    print(
        "  Inspect quality: "
        "curl http://localhost:8000/api/data/historical-iv-quality/<TICKER>\n"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reconstruct historical ATM IV surfaces from Polygon historical options chains.\n"
            "Stores results with source='polygon_historical_options' (quality='real')."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Flags")[0].strip(),
    )

    # Tickers
    ticker_group = parser.add_mutually_exclusive_group()
    ticker_group.add_argument(
        "--tickers", nargs="+", metavar="SYM",
        help="Tickers to backfill (default: all configured tickers)",
    )
    ticker_group.add_argument(
        "--ticker", nargs="+", metavar="SYM",
        help="Alias for --tickers",
    )

    # Date range
    parser.add_argument("--start", metavar="YYYY-MM-DD",
                        help="Start of date range")
    parser.add_argument("--end",   metavar="YYYY-MM-DD",
                        help="End of date range (default: today)")
    parser.add_argument("--days",  metavar="N", type=int,
                        help="Trailing trading days (alternative to --start)")

    # Behaviour flags
    parser.add_argument("--overwrite",    action="store_true",
                        help="Replace existing store entries (default: skip)")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Report what would be fetched without writing anything")
    parser.add_argument("--verbose",      action="store_true",
                        help="Print per-date progress")
    parser.add_argument("--delete-first", action="store_true",
                        help="Delete all existing data for each ticker before starting")

    # Rate-limiting
    parser.add_argument("--delay",       metavar="SECS", type=float, default=1.0,
                        help="Seconds between API calls (default 1.0; use 12 for free tier)")
    parser.add_argument("--max-backoff", metavar="SECS", type=float, default=60.0,
                        help="Max wait in seconds after a 429 (default 60)")
    parser.add_argument("--max-retries", metavar="N",    type=int,   default=3,
                        help="Retries per request on transient errors (default 3)")

    args = parser.parse_args(argv)

    # ------------------------------------------------------------------
    # API key guard
    # ------------------------------------------------------------------
    api_key = os.environ.get("POLYGON_API_KEY", "").strip()
    if not api_key:
        print(
            "ERROR: POLYGON_API_KEY is not set.\n"
            "Add it to backend/.env or export it before running this script.\n"
            "Historical options snapshots require a Polygon Starter subscription or higher.",
            file=sys.stderr,
        )
        return 1

    # ------------------------------------------------------------------
    # Date range resolution
    # ------------------------------------------------------------------
    today    = datetime.date.today()
    end_date = datetime.date.fromisoformat(args.end) if args.end else today

    if args.days is not None:
        # Convert approximate trading days → calendar days
        cal_days   = int(args.days * 365.25 / 252)
        start_date = today - datetime.timedelta(days=cal_days)
    elif args.start:
        start_date = datetime.date.fromisoformat(args.start)
    else:
        # Default: 1 year
        start_date = today - datetime.timedelta(days=int(252 * 365.25 / 252))

    all_dates = _trading_days(start_date, end_date)
    if not all_dates:
        logger.warning("No trading days in range %s – %s", start_date, end_date)
        return 0

    # ------------------------------------------------------------------
    # Ticker list
    # ------------------------------------------------------------------
    raw_tickers = args.ticker or args.tickers or list(settings.tickers)
    tickers     = [t.upper() for t in raw_tickers]

    # ------------------------------------------------------------------
    # Pre-flight summary
    # ------------------------------------------------------------------
    logger.info(
        "Backfilling %d tickers × %d dates (%s – %s) | delay=%.1fs%s%s%s",
        len(tickers), len(all_dates), start_date, end_date,
        args.delay,
        " [DRY RUN]"     if args.dry_run     else "",
        " [OVERWRITE]"   if args.overwrite   else "",
        " [DELETE FIRST]" if args.delete_first else "",
    )
    logger.info("Tickers: %s", ", ".join(tickers))
    logger.info(
        "Estimated time: ~%.0f min (%.0f req × %.1fs delay)",
        len(tickers) * len(all_dates) * args.delay / 60,
        len(tickers) * len(all_dates),
        args.delay,
    )

    # ------------------------------------------------------------------
    # Main backfill loop
    # ------------------------------------------------------------------
    all_results: list[dict] = []

    for ticker in tickers:
        if args.delete_first and not args.dry_run:
            _store.delete_ticker(ticker)
            logger.info("Deleted existing store for %s", ticker)

        logger.info("Processing %s (%d dates) …", ticker, len(all_dates))
        result = backfill_ticker(
            ticker, all_dates, api_key,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
            verbose=args.verbose,
            delay=args.delay,
            max_backoff=args.max_backoff,
            max_retries=args.max_retries,
        )
        logger.info(
            "%s — written=%d  skipped=%d  failed=%d  no_data=%d  "
            "iv30_ok=%d  full_surf=%d",
            ticker,
            result["written"], result["skipped"], result["failed"],
            result["no_data"], result["n_iv30_ok"], result["n_full_surface"],
        )
        all_results.append(result)

    # ------------------------------------------------------------------
    # Diagnostics summary table
    # ------------------------------------------------------------------
    _print_diagnostics(all_results, dry_run=args.dry_run)

    total_failed = sum(r["failed"] for r in all_results)
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
