"""
scripts/backfill_iv_surface.py
-------------------------------
Backfill historical ATM IV surfaces into the Parquet IV store.

Data sources (by date)
-----------------------
TODAY (DATA_PROVIDER=real, POLYGON_API_KEY set):
    → Fetch live options chain from Polygon.
    → Compute ATM IV surface via calculate_atm_iv_by_tenor.
    → source = "polygon_live_snapshot"

HISTORICAL (all other dates, DATA_PROVIDER=mock, --allow-synthetic passed):
    → Compute from price history using the mock IV model (OU process).
    → source = "synthetic_fallback"
    → WARNING: synthetic rows do NOT count as real data and will not satisfy
      the 80% real-IV threshold required for production backtests.

As live data accumulates day by day via capture_live_iv_surface.py, the
"real" rows grow and the backtest engine automatically prefers them over
synthetic data for matching dates.

Usage
-----
Run from the backend/ directory so the app package is on PYTHONPATH:

    # Seed all tickers with synthetic data (testing only)
    python -m scripts.backfill_iv_surface --allow-synthetic

    # Specify a custom date range (synthetic)
    python -m scripts.backfill_iv_surface --start 2024-01-01 --end 2024-06-30 --allow-synthetic

    # Only SPY and QQQ
    python -m scripts.backfill_iv_surface --tickers SPY QQQ --allow-synthetic

    # Overwrite entries that already exist in the store
    python -m scripts.backfill_iv_surface --overwrite --allow-synthetic

    # Show what would be written without actually writing
    python -m scripts.backfill_iv_surface --dry-run --verbose --allow-synthetic

    # Ingest today's live options chain (requires DATA_PROVIDER=real + POLYGON_API_KEY)
    DATA_PROVIDER=real python -m scripts.backfill_iv_surface --today-only

Flags
-----
--tickers        STR [STR ...]  Tickers to process (default: all settings.tickers)
--ticker         STR [STR ...]  Alias for --tickers
--start          YYYY-MM-DD     Start of date range (default: 252 trading days ago)
--end            YYYY-MM-DD     End of date range   (default: today)
--days           N              Approximate trading days to look back (e.g. 756 ≈ 3 years)
--overwrite                     Overwrite existing store entries (default: skip)
--dry-run                       Report what would be written; write nothing
--verbose                       Print per-date progress
--today-only                    Only ingest today's live options chain (no historical fill)
--delete-first                  Clear all existing data for each ticker before backfilling
--allow-synthetic               Required when DATA_PROVIDER=mock; allows synthetic_fallback rows
"""

from __future__ import annotations

import argparse
import datetime
import logging
import sys
import traceback
from pathlib import Path

# Ensure `app` package is importable when run as `python -m scripts.backfill_iv_surface`
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

import numpy as np
import pandas as pd

from app.core.config import settings
from app.data.iv_store import store as _store
from app.data.iv_provider import _get_synthetic
from app.data._mock_impl import MockDataProvider

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_iv")


# ---------------------------------------------------------------------------
# Trading calendar helper
# ---------------------------------------------------------------------------

def _trading_days(start: datetime.date, end: datetime.date) -> list[datetime.date]:
    """Return all weekdays between start and end inclusive (proxy for trading days)."""
    days: list[datetime.date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:   # Monday–Friday
            days.append(cur)
        cur += datetime.timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# IV surface computation helpers
# ---------------------------------------------------------------------------

def _iv_from_synthetic(ticker: str, date: datetime.date) -> dict | None:
    """
    Compute ATM IV surface from the mock provider's synthetic IV history.

    Returns a dict {iv7, iv30, iv60, iv90} or None if insufficient data.
    """
    try:
        mock     = MockDataProvider()
        # Use 400 days of synthetic IV; extract the row closest to target date
        iv_df    = mock.get_iv_history_days(ticker, 400)

        target_ts = pd.Timestamp(date)
        if target_ts not in iv_df.index:
            # Find nearest date
            idx = iv_df.index.searchsorted(target_ts)
            if idx >= len(iv_df):
                idx = len(iv_df) - 1
            target_ts = iv_df.index[idx]

        row = iv_df.loc[target_ts]
        return {
            "iv7":  float(row.get("iv7",  row.get("iv30") * 0.95)),
            "iv14": None,
            "iv30": float(row["iv30"]),
            "iv60": float(row.get("iv60", row["iv30"] * 1.02)),
            "iv90": float(row.get("iv90", row["iv30"] * 1.04)),
        }
    except Exception as exc:
        logger.debug("Synthetic IV failed for %s %s: %s", ticker, date, exc)
        return None


def _iv_from_live_chain(ticker: str, date: datetime.date) -> dict | None:
    """
    Compute ATM IV surface from the live Polygon options chain.

    Only called when DATA_PROVIDER=real and today == date.
    Returns a dict {iv7, iv14, iv30, iv60, iv90} or None on failure.
    """
    try:
        from app.data.registry import get_provider
        from app.data.options_utils import calculate_atm_iv_by_tenor

        provider = get_provider()
        if provider.name != "real":
            return None

        chain = provider.get_options_chain(ticker, date)
        if chain.empty:
            return None

        prices = provider.get_prices_days(ticker, 5)
        if prices.empty:
            return None
        underlying_price = float(prices.iloc[-1])

        return calculate_atm_iv_by_tenor(chain, underlying_price, pricing_date=date)

    except Exception as exc:
        logger.warning("Live chain failed for %s %s: %s", ticker, date, exc)
        return None


# ---------------------------------------------------------------------------
# Core backfill loop
# ---------------------------------------------------------------------------

def backfill_ticker(
    ticker:       str,
    dates:        list[datetime.date],
    *,
    overwrite:    bool = False,
    dry_run:      bool = False,
    verbose:      bool = False,
    today:        datetime.date,
) -> dict:
    """
    Backfill IV surfaces for *ticker* over the given list of *dates*.

    Returns a summary dict with counts for skipped/written/failed.
    """
    stored_dates   = _store.get_stored_dates(ticker) if not overwrite else set()

    n_skip   = 0
    n_write  = 0
    n_fail   = 0
    batch: list[dict] = []

    for date in dates:
        date_str = date.isoformat()

        # Skip if already stored (unless overwrite)
        if date_str in stored_dates and not overwrite:
            n_skip += 1
            if verbose:
                logger.info("  [SKIP] %s %s — already stored", ticker, date_str)
            continue

        # Choose data source
        if date == today:
            iv_surf = _iv_from_live_chain(ticker, date)
            source  = "polygon_live_snapshot"
            if iv_surf is None:
                # Fallback to synthetic for today too
                iv_surf = _iv_from_synthetic(ticker, date)
                source  = "synthetic_fallback"
        else:
            iv_surf = _iv_from_synthetic(ticker, date)
            source  = "synthetic_fallback"

        if iv_surf is None or iv_surf.get("iv30") is None:
            n_fail += 1
            if verbose:
                logger.warning("  [FAIL] %s %s — could not compute IV", ticker, date_str)
            continue

        if verbose:
            q = "real" if source == "live_options_chain" else "synth"
            logger.info(
                "  [%-5s] %s %s — iv30=%.2f%% iv7=%.2f%%",
                q, ticker, date_str,
                iv_surf.get("iv30", float("nan")),
                iv_surf.get("iv7",  float("nan")),
            )

        if not dry_run:
            batch.append({"date": date_str, "iv_surf": iv_surf, "source": source})
        n_write += 1

    # Flush batch write (one Parquet write per ticker)
    if batch and not dry_run:
        written = _store.upsert_batch(ticker, batch)
        if written != len(batch):
            logger.warning("%s: expected %d writes, got %d", ticker, len(batch), written)

    return {"ticker": ticker, "written": n_write, "skipped": n_skip, "failed": n_fail}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill historical ATM IV surfaces into the Parquet IV store.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage")[0].strip(),
    )
    # --tickers / --ticker  (both accepted)
    ticker_group = parser.add_mutually_exclusive_group()
    ticker_group.add_argument(
        "--tickers", nargs="+", metavar="SYM",
        help="Tickers to backfill (default: all configured tickers)",
    )
    ticker_group.add_argument(
        "--ticker", nargs="+", metavar="SYM",
        help="Alias for --tickers",
    )
    parser.add_argument(
        "--start", metavar="YYYY-MM-DD",
        default=None,
        help="Start date (default: 252 trading days ago)",
    )
    parser.add_argument(
        "--end", metavar="YYYY-MM-DD",
        default=None,
        help="End date (default: today)",
    )
    parser.add_argument(
        "--days", metavar="N", type=int,
        default=None,
        help="Number of calendar days to look back (alternative to --start)",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Overwrite existing store entries (default: skip)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be written without writing anything",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print per-date progress",
    )
    parser.add_argument(
        "--today-only", action="store_true",
        help="Only ingest today's live options chain",
    )
    parser.add_argument(
        "--delete-first", action="store_true",
        help="Delete all existing data for each ticker before backfilling",
    )
    parser.add_argument(
        "--allow-synthetic", action="store_true",
        help=(
            "Required when DATA_PROVIDER=mock. Explicitly permits writing "
            "synthetic_fallback rows (for testing only — these do not count "
            "as real data toward the 80%% quality threshold)."
        ),
    )
    args = parser.parse_args(argv)

    # ------------------------------------------------------------------
    # Guard: prevent accidental synthetic writes in mock mode
    # ------------------------------------------------------------------
    if settings.data_provider == "mock" and not args.allow_synthetic and not args.today_only:
        print(
            "ERROR: Mock provider would create synthetic IV rows. "
            "Use DATA_PROVIDER=real or pass --allow-synthetic for testing.",
            file=sys.stderr,
        )
        return 1

    today    = datetime.date.today()
    end_date = datetime.date.fromisoformat(args.end) if args.end else today

    # --days N  →  convert approximate trading days to calendar days, then to start date
    # 1 trading year = 252 days ≈ 365.25 calendar days  →  factor = 365.25/252 ≈ 1.4496
    if args.days is not None:
        cal_days   = int(args.days * 365.25 / 252)
        start_date = today - datetime.timedelta(days=cal_days)
    elif args.start:
        start_date = datetime.date.fromisoformat(args.start)
    else:
        start_date = today - datetime.timedelta(days=int(252 * 1.5))

    if args.today_only:
        start_date = end_date = today

    all_dates = _trading_days(start_date, end_date)
    if not all_dates:
        logger.warning("No trading days in range %s – %s", start_date, end_date)
        return 0

    # --ticker is an alias for --tickers; fall back to all configured tickers
    raw_tickers = args.ticker or args.tickers or list(settings.tickers)
    tickers     = [t.upper() for t in raw_tickers]
    logger.info(
        "Backfilling %d tickers × %d dates (%s – %s)%s%s",
        len(tickers), len(all_dates), start_date, end_date,
        " [DRY RUN]"     if args.dry_run     else "",
        " [OVERWRITE]"   if args.overwrite   else "",
    )

    totals = {"written": 0, "skipped": 0, "failed": 0}

    for ticker in tickers:
        if args.delete_first and not args.dry_run:
            _store.delete_ticker(ticker)
            logger.info("Deleted existing store for %s", ticker)

        logger.info("Processing %s …", ticker)
        result = backfill_ticker(
            ticker, all_dates,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
            verbose=args.verbose,
            today=today,
        )
        logger.info(
            "  %s: written=%d  skipped=%d  failed=%d",
            ticker, result["written"], result["skipped"], result["failed"],
        )
        for k in totals:
            totals[k] += result[k]

    logger.info(
        "Done. total written=%d  skipped=%d  failed=%d%s",
        totals["written"], totals["skipped"], totals["failed"],
        "  (dry-run — nothing persisted)" if args.dry_run else "",
    )
    return 0 if totals["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
