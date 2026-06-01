"""
scripts/eod_workflow.py
-----------------------
End-of-day workflow runner: capture live IV → run scanner → save to journal.

Run this once after market close (~16:30–17:00 ET) each trading day.
Steps 1-3 are automated; the script prints a link for manual review (steps 4-6).

Usage
-----
    DATA_PROVIDER=real python -m scripts.eod_workflow

    # Custom scanner universe size
    DATA_PROVIDER=real python -m scripts.eod_workflow --max-tickers 100

    # Re-capture IV even if already stored today
    DATA_PROVIDER=real python -m scripts.eod_workflow --overwrite-iv

    # Override tickers for IV capture (defaults to settings.tickers)
    DATA_PROVIDER=real python -m scripts.eod_workflow --tickers SPY QQQ IWM

    # Preview what would happen without saving anything
    DATA_PROVIDER=real python -m scripts.eod_workflow --dry-run

Workflow
--------
1. Capture live ATM IV surface (Polygon) → IV store parquet
2. Run S&P 500 vol scanner (top N by mkt cap)
3. Save qualifying signals to scanner journal

Then open /scanner/today in the UI for steps 4-6:
4. Review top candidates
5. Mark broker-verified / trade-considered / trade-taken
6. Add notes
"""

from __future__ import annotations

import argparse
import datetime
import logging
import pathlib
import sys
import time

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

logger = logging.getLogger("eod_workflow")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_step(n: int, label: str, status: str = "") -> None:
    icon = "🔵" if not status else ("✅" if status == "ok" else ("⚠️ " if status == "warn" else "❌"))
    print(f"\n{icon} Step {n}: {label}")


def _print_result(msg: str, ok: bool = True) -> None:
    print(f"   {'✓' if ok else '✗'} {msg}")


# ---------------------------------------------------------------------------
# Step 1 — Capture live IV
# ---------------------------------------------------------------------------

def step_capture_iv(
    tickers:   list[str],
    date:      datetime.date,
    overwrite: bool,
    dry_run:   bool,
) -> dict:
    from app.data.registry import get_provider
    from app.data.options_utils import calculate_atm_iv_by_tenor
    from app.data.iv_store import store as _iv_store

    provider = get_provider()
    if provider.name != "real":
        logger.error("DATA_PROVIDER=%s — step 1 requires real provider", provider.name)
        return {"ok": False, "error": "DATA_PROVIDER must be 'real'", "written": 0}

    written = skipped = failed = 0
    date_str = date.isoformat()

    for ticker in tickers:
        if not overwrite and _iv_store.has_date(ticker, date_str):
            _print_result(f"{ticker}: already captured (use --overwrite-iv to replace)", ok=True)
            skipped += 1
            continue

        try:
            prices = provider.get_prices_days(ticker, 5)
            if prices is None or prices.empty:
                raise ValueError("price fetch returned nothing")
            spot = float(prices.iloc[-1])

            if hasattr(provider, "get_atm_options_chain"):
                chain = provider.get_atm_options_chain(ticker, spot)
            else:
                chain = provider.get_options_chain(ticker, date)

            if chain is None or chain.empty:
                raise ValueError("options chain is empty")

            iv_surf = calculate_atm_iv_by_tenor(chain, spot, pricing_date=date)
            iv30 = iv_surf.get("iv30")
            if iv30 is None:
                raise ValueError("iv30 could not be computed from chain")

            if dry_run:
                _print_result(f"{ticker}: iv30={iv30:.2f}% (dry-run — not saved)")
            else:
                _iv_store.upsert(ticker, date_str, iv_surf, "polygon_live_snapshot")
                _print_result(f"{ticker}: iv30={iv30:.2f}%")
            written += 1

        except Exception as exc:
            _print_result(f"{ticker}: FAILED — {exc}", ok=False)
            failed += 1

    return {"ok": failed == 0, "written": written, "skipped": skipped, "failed": failed}


# ---------------------------------------------------------------------------
# Step 2 — Run scanner
# ---------------------------------------------------------------------------

def step_run_scanner(max_tickers: int, dry_run: bool) -> dict:
    from app.data.registry import get_provider
    from app.scanners.sp500_vol_scanner import run_scan

    provider = get_provider()
    params = {
        "max_tickers":    max_tickers,
        "max_results":    max_tickers,
        "min_ratio":      1.30,
        "require_iv30":   True,
        "include_suspicious": True,
        "exclude_invalid":    True,
        "force_refresh":  True,   # always fresh on EOD run
        "min_valid_tenors": 2,
        "batch_size":     10,
        "delay_seconds":  0.2,
    }

    result = run_scan(params, provider)
    passed  = result.get("passed",  0)
    scanned = result.get("scanned", 0)
    elapsed = result.get("elapsed_seconds", 0.0)

    _print_result(f"Scanned {scanned} tickers in {elapsed:.1f}s — {passed} qualified")

    if passed and not dry_run:
        top = result.get("results", [])[:5]
        for r in top:
            _print_result(
                f"  {r['ticker']:5s}  IV/RV={r['iv_rv_ratio']:.2f}×  IV30={r['iv30']:.1f}%  score={r['score']:.0f}"
            )

    return {"ok": True, "passed": passed, "scanned": scanned, "result": result}


# ---------------------------------------------------------------------------
# Step 3 — Save to journal
# ---------------------------------------------------------------------------

def step_save_journal(scan_result: dict, date: datetime.date, dry_run: bool) -> dict:
    from app.data import scanner_journal as journal

    date_str = date.isoformat()
    results  = [r for r in scan_result.get("results", []) if r.get("ok", True)]

    if not results:
        _print_result("No qualifying signals to save", ok=True)
        return {"ok": True, "rows": 0}

    if dry_run:
        _print_result(f"{len(results)} signal(s) would be saved (dry-run — not written)")
        return {"ok": True, "rows": 0}

    rows = journal.save_scan(results, date_str)
    _print_result(f"{rows} signal(s) saved → data_store/scanner_signals/{date_str}.parquet")
    return {"ok": True, "rows": rows}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="End-of-day vol workflow: capture IV → scan → save.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Workflow")[0].strip(),
    )
    parser.add_argument("--tickers",      nargs="+", metavar="SYM",
                        help="Tickers for IV capture (default: settings.tickers)")
    parser.add_argument("--max-tickers",  type=int, default=50,
                        help="Scanner universe size — top N by mkt cap (default 50)")
    parser.add_argument("--overwrite-iv", action="store_true",
                        help="Re-capture IV even if already stored today")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Preview without writing anything")
    parser.add_argument("--verbose",      action="store_true",
                        help="Show DEBUG logs")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from app.core.config import settings
    if settings.data_provider != "real":
        print(
            f"\n❌ DATA_PROVIDER={settings.data_provider!r}. "
            "This script requires DATA_PROVIDER=real.\n\n"
            "    DATA_PROVIDER=real python -m scripts.eod_workflow",
            file=sys.stderr,
        )
        return 1

    today     = datetime.date.today()
    iv_tickers = [t.upper() for t in (args.tickers or list(settings.tickers))]

    print(f"\n{'='*60}")
    print(f"  Vol Dashboard — EOD Workflow  {today.isoformat()}")
    if args.dry_run:
        print("  [DRY RUN — nothing will be written]")
    print(f"{'='*60}")

    # ── Step 1: Capture IV ────────────────────────────────────────────────
    _print_step(1, f"Capture live IV surface ({len(iv_tickers)} tickers)")
    t0 = time.monotonic()
    iv_result = step_capture_iv(iv_tickers, today, args.overwrite_iv, args.dry_run)
    print(f"   Elapsed: {time.monotonic()-t0:.1f}s")

    if not iv_result["ok"]:
        print(f"\n   ⚠️  Some IV captures failed — continuing with scanner anyway")

    # ── Step 2: Run scanner ───────────────────────────────────────────────
    _print_step(2, f"Run S&P 500 vol scanner (top {args.max_tickers} by mkt cap)")
    t0 = time.monotonic()
    try:
        scan_result_data = step_run_scanner(args.max_tickers, args.dry_run)
        print(f"   Elapsed: {time.monotonic()-t0:.1f}s")
    except Exception as exc:
        _print_result(f"Scanner failed: {exc}", ok=False)
        return 1

    # ── Step 3: Save to journal ───────────────────────────────────────────
    _print_step(3, "Save qualifying signals to journal")
    save_data = step_save_journal(
        scan_result_data.get("result", {}), today, args.dry_run
    )

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  Summary")
    print(f"{'='*60}")
    print(f"  IV capture:  {iv_result.get('written', 0)} written, {iv_result.get('skipped', 0)} skipped, {iv_result.get('failed', 0)} failed")
    print(f"  Scanner:     {scan_result_data.get('passed', 0)} qualified / {scan_result_data.get('scanned', 0)} scanned")
    print(f"  Journal:     {save_data.get('rows', 0)} signals saved")
    print()
    if not args.dry_run and save_data.get("rows", 0) > 0:
        print("  Next steps (steps 4–6):")
        print("  → Open http://localhost:3000/scanner/today")
        print("  → Review top candidates, verify spreads, mark trade decisions")
    print(f"{'='*60}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
