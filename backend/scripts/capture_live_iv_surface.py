"""
scripts/capture_live_iv_surface.py
------------------------------------
Capture today's live ATM IV surface from Polygon.io and persist it to the
Parquet IV store with source="polygon_live_snapshot".

Run this script once per trading day (e.g. at or after market close) to
accumulate real options-chain IV data. As days accumulate, backtests
transition from POOR/MIXED quality toward GOOD (≥80% real rows).

Requirements
------------
- DATA_PROVIDER=real  (enforced — script refuses to run under mock)
- POLYGON_API_KEY     must be set in environment or .env file

Usage
-----
Run from the backend/ directory so the app package is on PYTHONPATH:

    # Capture today's IV for all configured tickers
    DATA_PROVIDER=real python -m scripts.capture_live_iv_surface

    # Specific tickers only
    DATA_PROVIDER=real python -m scripts.capture_live_iv_surface --tickers SPY QQQ

    # Preview without writing
    DATA_PROVIDER=real python -m scripts.capture_live_iv_surface --dry-run --verbose

    # Force-overwrite today's row even if already stored
    DATA_PROVIDER=real python -m scripts.capture_live_iv_surface --overwrite

Flags
-----
--tickers  STR [STR ...]  Tickers to capture (default: all settings.tickers)
--ticker   STR [STR ...]  Alias for --tickers
--dry-run                 Report what would be written; write nothing
--verbose                 Print per-ticker IV values
--overwrite               Replace today's row even if already stored

Source label written
--------------------
source = "polygon_live_snapshot"
→ counts as "real" in quality gate (iv_data_quality = "real")
"""

from __future__ import annotations

import argparse
import datetime
import logging
import sys
from pathlib import Path

# Ensure `app` package is importable when run as `python -m scripts.capture_live_iv_surface`
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import settings
from app.data.iv_store import store as _store

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("capture_live_iv")

# Source label written by this script — maps to quality="real"
_SOURCE = "polygon_live_snapshot"


# ---------------------------------------------------------------------------
# IV surface fetch
# ---------------------------------------------------------------------------

def _fetch_iv_surface(ticker: str, date: datetime.date) -> dict | None:
    """
    Fetch today's live ATM IV surface from Polygon.

    Uses ``get_atm_options_chain`` (ATM-centred, strike-filtered) so that
    high-priced underlyings like QQQ ($700+) don't hit the 5 000-contract
    page cap before reaching near-money options.

    Returns a dict {iv7, iv14, iv30, iv60, iv90} (values in %) or None on failure.
    """
    try:
        from app.data.registry import get_provider
        from app.data.options_utils import calculate_atm_iv_by_tenor

        provider = get_provider()
        if provider.name != "real":
            logger.error(
                "Provider is '%s', not 'real'. Set DATA_PROVIDER=real.", provider.name
            )
            return None

        # Price first — needed to centre the strike filter
        prices = provider.get_prices_days(ticker, 5)
        if prices is None or prices.empty:
            logger.warning("%s: could not fetch recent prices", ticker)
            return None
        underlying_price = float(prices.iloc[-1])

        # ATM-centred fetch: ±22% moneyness, up to 100 DTE — always live
        if hasattr(provider, "get_atm_options_chain"):
            chain = provider.get_atm_options_chain(ticker, underlying_price)
            logger.debug("%s: ATM chain fetched — %d contracts", ticker, len(chain))
        else:
            # Fallback for mock provider or older provider versions
            chain = provider.get_options_chain(ticker, date)

        if chain is None or chain.empty:
            logger.warning("%s: options chain is empty for %s", ticker, date)
            return None

        iv_surf = calculate_atm_iv_by_tenor(chain, underlying_price, pricing_date=date)
        return iv_surf

    except Exception as exc:
        logger.warning("%s: failed to fetch live IV surface: %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Per-ticker capture
# ---------------------------------------------------------------------------

def capture_ticker(
    ticker: str,
    date: datetime.date,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    """
    Capture today's live IV surface for *ticker* and write to the store.

    Returns {"ticker", "status"} where status is one of:
      "written"  — row successfully stored
      "skipped"  — already exists and overwrite=False
      "failed"   — could not compute IV surface
      "dry_run"  — would have written (dry-run mode)
    """
    date_str = date.isoformat()

    # Skip if already stored (unless overwrite requested)
    if not overwrite and _store.has_date(ticker, date_str):
        if verbose:
            logger.info("  [SKIP] %s %s — already stored (use --overwrite to replace)", ticker, date_str)
        return {"ticker": ticker, "status": "skipped"}

    iv_surf = _fetch_iv_surface(ticker, date)
    if iv_surf is None or iv_surf.get("iv30") is None:
        logger.warning("  [FAIL] %s %s — could not compute live IV surface", ticker, date_str)
        return {"ticker": ticker, "status": "failed"}

    if verbose:
        def _v(k: str) -> float:
            """Return tenor value or NaN — handles explicit-None entries."""
            val = iv_surf.get(k)
            return val if val is not None else float("nan")

        logger.info(
            "  [%-7s] %s %s — iv7=%.2f%% iv30=%.2f%% iv60=%.2f%% iv90=%.2f%%",
            "DRY-RUN" if dry_run else "OK",
            ticker, date_str,
            _v("iv7"), _v("iv30"), _v("iv60"), _v("iv90"),
        )

    if dry_run:
        return {"ticker": ticker, "status": "dry_run"}

    _store.upsert(ticker, date_str, iv_surf, _SOURCE)
    logger.info(
        "  [STORED] %s %s — iv30=%.2f%% (source=%s)",
        ticker, date_str, iv_surf.get("iv30", float("nan")), _SOURCE,
    )
    return {"ticker": ticker, "status": "written"}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture today's live IV surface from Polygon.io.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Requirements")[0].strip(),
    )

    ticker_group = parser.add_mutually_exclusive_group()
    ticker_group.add_argument(
        "--tickers", nargs="+", metavar="SYM",
        help="Tickers to capture (default: all configured tickers)",
    )
    ticker_group.add_argument(
        "--ticker", nargs="+", metavar="SYM",
        help="Alias for --tickers",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be written without writing anything",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print per-ticker IV values",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Replace today's row even if already stored",
    )

    args = parser.parse_args(argv)

    # ------------------------------------------------------------------
    # Enforce real provider — this script must not run under mock
    # ------------------------------------------------------------------
    if settings.data_provider != "real":
        print(
            f"ERROR: DATA_PROVIDER={settings.data_provider!r}. "
            "This script requires DATA_PROVIDER=real and a valid POLYGON_API_KEY.\n"
            "Set the environment variable before running:\n\n"
            "    DATA_PROVIDER=real python -m scripts.capture_live_iv_surface",
            file=sys.stderr,
        )
        return 1

    if not settings.polygon_api_key:
        print(
            "ERROR: POLYGON_API_KEY is not set. "
            "Add it to your .env file or export it before running.",
            file=sys.stderr,
        )
        return 1

    today = datetime.date.today()
    raw_tickers = args.ticker or args.tickers or list(settings.tickers)
    tickers = [t.upper() for t in raw_tickers]

    logger.info(
        "Capturing live IV surface for %d ticker(s) on %s%s%s",
        len(tickers), today,
        " [DRY RUN]" if args.dry_run else "",
        " [OVERWRITE]" if args.overwrite else "",
    )

    counts = {"written": 0, "skipped": 0, "failed": 0, "dry_run": 0}
    for ticker in tickers:
        logger.info("Processing %s …", ticker)
        result = capture_ticker(
            ticker, today,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        counts[result["status"]] += 1

    # Summary
    logger.info(
        "Done. written=%d  skipped=%d  failed=%d%s",
        counts["written"], counts["skipped"], counts["failed"],
        f"  dry_run={counts['dry_run']}" if args.dry_run else "",
    )
    if args.dry_run:
        logger.info("(dry-run — nothing persisted)")

    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
