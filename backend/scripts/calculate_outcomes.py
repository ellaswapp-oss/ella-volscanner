"""
scripts/calculate_outcomes.py
------------------------------
Back-fill forward return and realized-volatility outcomes for stored
scanner journal signals.

For each qualifying signal the script:
  1. Fetches daily adjusted closes from Polygon (scan_date → +38 cal days).
  2. Computes 5/10/20-day stock returns, realized vols, and premium captures.
  3. Writes results back into the corresponding parquet file atomically.

Usage
-----
  # Calculate all signals with ≥ 7 calendar days elapsed:
  DATA_PROVIDER=real python -m scripts.calculate_outcomes

  # Only a specific ticker:
  DATA_PROVIDER=real python -m scripts.calculate_outcomes --ticker NVDA

  # Only a specific scan date:
  DATA_PROVIDER=real python -m scripts.calculate_outcomes --date 2026-05-01

  # Force-recalculate already-complete signals:
  DATA_PROVIDER=real python -m scripts.calculate_outcomes --force

  # Preview without writing:
  DATA_PROVIDER=real python -m scripts.calculate_outcomes --dry-run

  # Require ≥ 20 calendar days before calculating (ensures 20-day window):
  DATA_PROVIDER=real python -m scripts.calculate_outcomes --min-days 28

Options
-------
  --ticker     Only process this ticker (case-insensitive)
  --date       Only process this ISO scan date (YYYY-MM-DD)
  --force      Recalculate even if outcome_status == 'complete'
  --min-days   Minimum calendar days since scan_date (default 7)
  --dry-run    Show what would be done without writing anything
  --delay      Seconds between Polygon API calls (default 0.4)
  --verbose    Print INFO-level logs

Rate limits
-----------
  Polygon free tier: 5 req/min.  With --delay 12 you stay under that.
  Paid "Starter" (unlimited req/min) works fine at --delay 0.3.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import pathlib
import sys
import time

# Allow running as ``python -m scripts.calculate_outcomes`` from backend/
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.data import scanner_journal as journal
from app.data.forward_returns import compute_signal_outcomes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Back-fill forward-return outcomes for scanner journal signals.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--ticker",    metavar="SYM",  help="Only process this ticker")
    p.add_argument("--date",      metavar="DATE", help="Only process this scan date (YYYY-MM-DD)")
    p.add_argument("--force",     action="store_true",
                   help="Recalculate even if outcome_status == 'complete'")
    p.add_argument("--min-days",  type=int, default=7, metavar="N",
                   help="Skip signals with < N calendar days elapsed (default 7)")
    p.add_argument("--dry-run",   action="store_true",
                   help="Show what would be calculated without writing")
    p.add_argument("--delay",     type=float, default=0.4, metavar="SEC",
                   help="Seconds between Polygon API calls (default 0.4)")
    p.add_argument("--verbose",   action="store_true", help="Show INFO logs")
    return p.parse_args()


def _fmt_outcome(out: dict) -> str:
    """One-liner summary of a computed outcome dict."""
    status = out.get("outcome_status", "?")
    ret20  = out.get("fwd_ret_20d")
    cap20  = out.get("premium_capture_20d")
    parts  = [f"status={status}"]
    if ret20 is not None:
        parts.append(f"ret20d={ret20:+.2f}%")
    if cap20 is not None:
        parts.append(f"cap20d={cap20:+.1f}vpts")
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()

    log_level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Load all journal signals ────────────────────────────────────────────
    print("Loading scanner journal…")
    df = journal.load_all()
    if df.empty:
        print("No signals found in journal. Run a scan and save it first.")
        sys.exit(0)

    total_rows = len(df)
    print(f"  {total_rows} total signals across {df['scan_date'].nunique()} date(s).")

    # ── Build candidate set ─────────────────────────────────────────────────
    today   = datetime.date.today()
    cutoff  = (today - datetime.timedelta(days=args.min_days)).isoformat()
    candidates = df[df["scan_date"] <= cutoff].copy()

    if args.ticker:
        candidates = candidates[candidates["ticker"] == args.ticker.upper()]
    if args.date:
        candidates = candidates[candidates["scan_date"] == args.date]
    if not args.force and "outcome_status" in candidates.columns:
        candidates = candidates[candidates["outcome_status"] != "complete"]

    n_candidates = len(candidates)
    if n_candidates == 0:
        print("No signals need outcome calculation (all complete or too recent).")
        print(f"  Use --force to recalculate all, or --min-days 0 to include today.")
        sys.exit(0)

    print(
        f"  {n_candidates} signal(s) selected for calculation "
        f"(≥ {args.min_days}d elapsed, not complete)."
    )
    if args.dry_run:
        print("  [DRY RUN — nothing will be written]\n")

    # ── Process ────────────────────────────────────────────────────────────
    updates: list[dict] = []
    n_ok = n_errors = 0

    for i, (_, row) in enumerate(candidates.iterrows(), 1):
        ticker    = str(row["ticker"])
        scan_date = str(row["scan_date"])
        iv30      = float(row["iv30"])

        prefix = f"  [{i}/{n_candidates}] {ticker} {scan_date}"

        try:
            out = compute_signal_outcomes(ticker, scan_date, iv30)
            out["ticker"]    = ticker
            out["scan_date"] = scan_date

            print(f"{prefix}: {_fmt_outcome(out)}")
            updates.append(out)
            n_ok += 1

        except Exception as exc:
            print(f"{prefix}: ERROR — {exc}")
            n_errors += 1

        # Rate-limit Polygon calls (skip sleep after last item)
        if i < n_candidates:
            time.sleep(args.delay)

    # ── Write ───────────────────────────────────────────────────────────────
    print()
    if args.dry_run:
        print(f"DRY RUN complete: {n_ok} would be calculated, {n_errors} errors.")
        return

    if updates:
        print(f"Writing {len(updates)} update(s) to parquet files…")
        written = journal.update_outcomes(updates)
        print(f"  ✓ {written} row(s) updated.")
    else:
        print("Nothing to write.")

    print(f"\nDone: {n_ok} calculated, {n_errors} error(s).")


if __name__ == "__main__":
    main()
