"""
api/scanner.py
--------------
S&P 500 Live Volatility Scanner endpoint.

Routes
------
GET /api/scanner/sp500-vol
    Scan the S&P 500 universe and return tickers with elevated IV/RV premium.

    Query parameters
    ----------------
    min_ratio          float   default 1.30   — IV30/RV30 threshold
    require_iv30       bool    default true   — exclude tickers with no iv30
    include_suspicious bool    default true   — include surface_status=suspicious
    exclude_invalid    bool    default true   — exclude surface_status=invalid
    max_results        int     default 50     — cap on returned results
    max_tickers        int     default 50     — tickers to scan (first N by mkt cap)
    batch_size         int     default 10     — tickers per rate-limit batch
    delay_seconds      float   default 0.2   — sleep between batches
    min_open_interest  int     default 100   — per-contract liquidity filter
    min_volume         int     default 10    — per-contract liquidity filter
    max_spread_pct     float   default 0.25  — max (ask-bid)/mid per tenor
    min_valid_tenors   int     default 2     — min tenors passing quality checks
    force_refresh      bool    default false  — bypass same-day result cache
    sort_by            str     default score  — score|iv_rv_ratio|vrp_abs|iv30

Caching
-------
Results are cached in memory for the trading day.  The first scan of the day
will take time proportional to max_tickers × options-chain fetch time.
Subsequent calls (including page reloads) return the cached result instantly.
Pass force_refresh=true to re-run the full scan.
"""

from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.data.registry import get_provider
from app.data import scanner_journal as _journal
from app.scanners.sp500_vol_scanner import load_universe, run_scan


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class JournalReviewUpdate(BaseModel):
    """
    Partial update model for manual review fields on a journal signal.

    All fields are optional — only the fields present in the request body
    are written.  Pass ``null`` to clear a nullable field; omit a field
    entirely to leave it unchanged.
    """
    reviewed:          bool  | None = None
    broker_verified:   bool  | None = None
    actual_bid:        float | None = None
    actual_ask:        float | None = None
    actual_mid:        float | None = None
    actual_spread_pct: float | None = None
    trade_considered:  bool  | None = None
    trade_taken:       bool  | None = None
    notes:             str   | None = None

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/sp500-vol")
def scan_sp500_vol(
    # ── Core filters ──────────────────────────────────────────────────────
    min_ratio: float = Query(
        1.30, ge=1.0, le=5.0,
        description="Minimum IV30/RV30 ratio for a ticker to qualify",
    ),
    require_iv30: bool = Query(
        True,
        description="Exclude tickers where IV30 cannot be computed from the chain",
    ),
    include_suspicious: bool = Query(
        True,
        description="Include tickers whose IV surface is flagged 'suspicious'",
    ),
    exclude_invalid: bool = Query(
        True,
        description="Exclude tickers whose IV surface is flagged 'invalid'",
    ),
    # ── Result controls ───────────────────────────────────────────────────
    max_results: int = Query(
        50, ge=1, le=500,
        description="Maximum number of qualifying results to return",
    ),
    max_tickers: int = Query(
        50, ge=1, le=500,
        description="How many S&P 500 tickers to scan (first N by market cap)",
    ),
    sort_by: str = Query(
        "score",
        description=(
            "Sort field for results: "
            "score | iv_rv_ratio | vrp_abs | iv30 | regime_score | liquidity_score"
        ),
    ),
    # ── Batch controls ────────────────────────────────────────────────────
    batch_size: int = Query(
        10, ge=1, le=100,
        description="Number of tickers to process before applying delay_seconds",
    ),
    delay_seconds: float = Query(
        0.2, ge=0.0, le=10.0,
        description="Seconds to sleep between batches (rate-limit control)",
    ),
    # ── Liquidity filters ─────────────────────────────────────────────────
    min_open_interest: int = Query(
        100, ge=0,
        description="Minimum open interest per ATM contract",
    ),
    min_volume: int = Query(
        10, ge=0,
        description="Minimum daily volume per ATM contract",
    ),
    max_spread_pct: float = Query(
        0.25, ge=0.0, le=1.0,
        description="Maximum (ask-bid)/mid per ATM contract",
    ),
    min_valid_tenors: int = Query(
        2, ge=1, le=5,
        description="Minimum number of tenors that pass all quality checks",
    ),
    # ── Earnings / event filter ───────────────────────────────────────────
    exclude_earnings_within_days: int = Query(
        0, ge=0, le=60,
        description=(
            "Exclude tickers with earnings/events within this many days. "
            "0 = do not exclude (show with warning). "
            "Common values: 7, 14, 30."
        ),
    ),
    # ── Cache control ─────────────────────────────────────────────────────
    force_refresh: bool = Query(
        False,
        description="Re-run the full scan even if a cached result exists for today",
    ),
) -> dict:
    """
    Scan S&P 500 tickers for elevated implied-volatility premium opportunities.

    Returns tickers where:
      - IV30 is available from real options data
      - IV30 / RV30 ≥ min_ratio (default 1.30)
      - IV surface is healthy or suspicious (not invalid)
      - Macro regime is not dangerous (penalty < 20 pts)
      - Trade signal is SELL_SELECTIVE or SELL_AGGRESSIVE

    Results are ranked by composite ``score`` (IV/RV, regime, tenor coverage,
    liquidity) and cached for the current trading day.
    """
    _VALID_SORT = {
        "score", "iv_rv_ratio", "vrp_abs", "iv30", "regime_score", "liquidity_score",
    }
    if sort_by not in _VALID_SORT:
        sort_by = "score"

    provider = get_provider()

    params = {
        "min_ratio":                    min_ratio,
        "require_iv30":                 require_iv30,
        "include_suspicious":           include_suspicious,
        "exclude_invalid":              exclude_invalid,
        "max_results":                  max_results,
        "max_tickers":                  max_tickers,
        "batch_size":                   batch_size,
        "delay_seconds":                delay_seconds,
        "min_open_interest":            min_open_interest,
        "min_volume":                   min_volume,
        "max_spread_pct":               max_spread_pct,
        "min_valid_tenors":             min_valid_tenors,
        "exclude_earnings_within_days": exclude_earnings_within_days,
        "force_refresh":                force_refresh,
    }

    result = run_scan(params, provider)

    # ── Re-sort if a custom sort_by was requested ─────────────────────────
    if sort_by != "score" and result.get("results"):
        reverse = True
        result["results"].sort(
            key=lambda r: (r.get(sort_by) or 0.0),
            reverse=reverse,
        )

    # ── Universe metadata (for frontend) ─────────────────────────────────
    result["universe_total"] = len(load_universe())

    return result


# ---------------------------------------------------------------------------
# Journal — save
# ---------------------------------------------------------------------------

@router.post("/journal/save")
def save_journal(scan_date: str | None = Query(None, description="ISO date to save (default: today)")) -> dict:
    """
    Persist today's (or a specified date's) cached scan results to the
    signal journal parquet store.

    The in-memory scan cache must contain results for the requested date.
    Run GET /api/scanner/sp500-vol first if the cache is empty.
    """
    from app.scanners.sp500_vol_scanner import _SCAN_CACHE

    date_str = scan_date or datetime.date.today().isoformat()

    cached = _SCAN_CACHE.get(date_str)
    if not cached:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No cached scan results for {date_str}. "
                "Run GET /api/scanner/sp500-vol (with force_refresh=true if needed) first."
            ),
        )

    results = [r for r in cached.get("results", []) if r.get("ok", True)]
    written = _journal.save_scan(results, date_str)

    return {
        "saved":     True,
        "scan_date": date_str,
        "rows":      written,
        "path":      f"data_store/scanner_signals/{date_str}.parquet",
    }


# ---------------------------------------------------------------------------
# Journal — manual review update
# ---------------------------------------------------------------------------

@router.patch("/journal/{date}/{ticker}")
def patch_journal_signal(
    date:   str,
    ticker: str,
    update: JournalReviewUpdate,
) -> dict:
    """
    Apply manual review fields to a single journal signal.

    Only fields explicitly included in the request body are written; omitted
    fields are left as-is.  ``actual_mid`` and ``actual_spread_pct`` are
    auto-computed from ``actual_bid`` / ``actual_ask`` when those are updated.

    Path parameters
    ---------------
    date   : ISO date of the scan, e.g. ``2026-05-29``
    ticker : ticker symbol, e.g. ``NVDA`` (case-insensitive)

    Request body (all fields optional)
    -----------------------------------
    reviewed          bool
    broker_verified   bool
    actual_bid        float
    actual_ask        float
    actual_mid        float   (auto-filled when bid+ask provided)
    actual_spread_pct float   (auto-filled when bid+ask provided)
    trade_considered  bool
    trade_taken       bool
    notes             str

    Returns
    -------
    ``{"updated": true, "date": "...", "ticker": "...", "fields": [...]}``
    """
    # Collect only fields that were explicitly sent (not None = "omit")
    fields = {
        k: v
        for k, v in update.model_dump().items()
        if v is not None
    }
    if not fields:
        raise HTTPException(
            status_code=400,
            detail="Request body must include at least one review field to update.",
        )

    ok = _journal.update_review(date, ticker.upper(), fields)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No journal entry found for ticker {ticker.upper()!r} on {date!r}. "
                "Ensure the scan was saved with POST /api/scanner/journal/save first."
            ),
        )

    return {
        "updated": True,
        "date":    date,
        "ticker":  ticker.upper(),
        "fields":  list(fields.keys()),
    }


# ---------------------------------------------------------------------------
# Journal — history
# ---------------------------------------------------------------------------

@router.get("/history")
def get_history(
    limit_days: int = Query(365, ge=1, le=3650, description="Max calendar days to include"),
) -> dict:
    """
    Aggregated scanner signal history across all stored dates.

    Returns
    -------
    dates          : list of scan dates that have data
    daily_summary  : per-day signal counts and top-ticker info
    leaderboard    : per-ticker appearance counts and averages
    recent_signals : all signals from the last 30 days (newest first)
    total_signals  : total row count across all stored dates
    unique_tickers : distinct tickers ever flagged
    """
    df = _journal.load_all(limit_days=limit_days)

    if df.empty:
        return {
            "dates":          [],
            "daily_summary":  [],
            "leaderboard":    [],
            "recent_signals": [],
            "total_signals":  0,
            "unique_tickers": 0,
        }

    summary_df    = _journal.daily_summary()
    leaderboard_df = _journal.ticker_leaderboard()

    # Recent 30 days signals
    recent_dates = sorted(df["scan_date"].unique(), reverse=True)[:30]
    recent_df    = df[df["scan_date"].isin(recent_dates)].sort_values(
        ["scan_date", "scanner_score"], ascending=[False, False]
    )

    # NaN → None for clean JSON serialisation (outcome float cols may be NaN)
    def _clean(records: list[dict]) -> list[dict]:
        return [
            {k: (None if (isinstance(v, float) and v != v) else v)
             for k, v in row.items()}
            for row in records
        ]

    return {
        "dates":          sorted(df["scan_date"].unique().tolist(), reverse=True),
        "daily_summary":  summary_df.to_dict(orient="records"),
        "leaderboard":    leaderboard_df.to_dict(orient="records"),
        "recent_signals": _clean(recent_df.to_dict(orient="records")),
        "total_signals":  len(df),
        "unique_tickers": int(df["ticker"].nunique()),
    }


@router.post("/outcomes/calculate")
def calculate_outcomes(
    ticker: str | None = Query(
        None,
        description="Only calculate outcomes for this ticker (e.g. AAPL)",
    ),
    scan_date: str | None = Query(
        None,
        description="Only calculate outcomes for this specific scan date (YYYY-MM-DD)",
    ),
    force: bool = Query(
        False,
        description="Recalculate even if outcome_status is already 'complete'",
    ),
    min_days: int = Query(
        7, ge=0, le=365,
        description=(
            "Skip signals where fewer than this many calendar days have elapsed "
            "since the scan date (default 7 — ensures at least the 5-day window "
            "can be populated). Use 28 to ensure all three windows are available."
        ),
    ),
) -> dict:
    """
    Calculate forward returns, realized volatility, and premium capture for
    stored scanner journal signals.

    Fetches daily adjusted closes from Polygon for each qualifying signal
    and writes the results back into the parquet journal files.

    Windows computed
    ----------------
    - 5-day / 10-day / 20-day stock return (close-to-close)
    - 5-day / 10-day / 20-day annualised realized volatility
    - Premium capture = IV30_at_signal − forward_RV  (per window)

    The response includes counts of how many signals were calculated,
    skipped, and errored, plus the total journal rows updated.

    Note
    ----
    Each signal requires one Polygon API call.  For large batches this
    endpoint may take tens of seconds.  Run the standalone script
    ``python -m scripts.calculate_outcomes`` for large back-fills.
    """
    import time as _time
    import datetime as _dt
    from app.data.forward_returns import compute_signal_outcomes

    df = _journal.load_all()
    if df.empty:
        return {
            "calculated":   0,
            "skipped":      0,
            "errors":       0,
            "rows_updated": 0,
            "candidates":   0,
            "message":      "No journal signals found.",
        }

    # ── Build candidate set ─────────────────────────────────────────────────
    today   = datetime.date.today()
    cutoff  = (today - datetime.timedelta(days=min_days)).isoformat()
    cands   = df[df["scan_date"] <= cutoff]

    if ticker:
        cands = cands[cands["ticker"] == ticker.upper()]
    if scan_date:
        cands = cands[cands["scan_date"] == scan_date]
    if not force and "outcome_status" in cands.columns:
        cands = cands[cands["outcome_status"] != "complete"]

    n_candidates = len(cands)
    logger.info("outcomes/calculate: %d candidate(s)", n_candidates)

    # ── Calculate ────────────────────────────────────────────────────────────
    updates: list[dict] = []
    n_ok = n_errors = 0

    for _, row in cands.iterrows():
        t   = str(row["ticker"])
        sd  = str(row["scan_date"])
        iv  = float(row["iv30"])
        try:
            out = compute_signal_outcomes(t, sd, iv)
            out["ticker"]    = t
            out["scan_date"] = sd
            updates.append(out)
            n_ok += 1
        except Exception as exc:
            logger.warning("outcome calc failed %s %s: %s", t, sd, exc)
            n_errors += 1
        _time.sleep(0.35)   # Polygon rate-limit buffer

    # ── Write ────────────────────────────────────────────────────────────────
    rows_updated = _journal.update_outcomes(updates) if updates else 0

    return {
        "calculated":   n_ok,
        "skipped":      0,
        "errors":       n_errors,
        "rows_updated": rows_updated,
        "candidates":   n_candidates,
    }


@router.get("/workflow/today")
def workflow_today() -> dict:
    """
    Status of today's 6-step EOD workflow.

    Returns per-step completion info so the /scanner/today UI can render a
    live checklist without the user having to navigate between pages.

    Steps
    -----
    1. iv_captured      — IV surface stored in IV store for today
    2. scan_run         — Scanner result in memory cache for today
    3. scan_saved       — Journal parquet for today exists on disk
    4. candidates_reviewed — at least one signal marked reviewed
    5. broker_verified  — at least one signal marked broker_verified
    6. notes_added      — at least one signal has a non-empty notes field
    """
    from app.data.iv_store import store as _iv_store
    from app.core.config import settings
    from app.scanners.sp500_vol_scanner import _SCAN_CACHE

    today_str = datetime.date.today().isoformat()

    # ── Step 1: IV captured ───────────────────────────────────────────────
    iv_tickers = list(settings.tickers)
    captured   = [t for t in iv_tickers if _iv_store.has_date(t, today_str)]
    iv_step    = {
        "complete": len(captured) == len(iv_tickers),
        "count":    len(captured),
        "total":    len(iv_tickers),
        "tickers":  captured,
        "detail":   f"{len(captured)} of {len(iv_tickers)} tickers captured",
    }

    # ── Step 2: Scanner run ───────────────────────────────────────────────
    # Check in-process cache first; fall back to journal existence so that
    # runs from the EOD CLI script (a separate process) are also detected.
    cached_scan   = _SCAN_CACHE.get(today_str)
    journal_today = _journal.load_date(today_str)  # empty DF if not saved yet

    if cached_scan:
        scan_step = {
            "complete":   True,
            "passed":     cached_scan.get("passed",  0),
            "scanned":    cached_scan.get("scanned", 0),
            "from_cache": cached_scan.get("from_cache", False),
            "detail":     (
                f"{cached_scan.get('passed', 0)} qualified "
                f"/ {cached_scan.get('scanned', 0)} scanned"
            ),
        }
    elif not journal_today.empty:
        # Journal exists → scan ran (possibly from CLI); in-memory cache empty
        n = len(journal_today)
        scan_step = {
            "complete": True,
            "passed":   n,
            "scanned":  None,
            "from_cache": False,
            "detail":   f"{n} qualified (cache cleared — re-run scanner to refresh)",
        }
    else:
        scan_step = {
            "complete": False,
            "passed":   0,
            "scanned":  0,
            "detail":   "Scanner not run today",
        }

    # ── Steps 3-6: Journal ────────────────────────────────────────────────
    df = journal_today  # already loaded above

    if not df.empty:
        n = len(df)
        reviewed   = int(df["reviewed"].sum())        if "reviewed"        in df.columns else 0
        verified   = int(df["broker_verified"].sum()) if "broker_verified" in df.columns else 0
        considered = int(df["trade_considered"].sum())if "trade_considered"in df.columns else 0
        traded     = int(df["trade_taken"].sum())     if "trade_taken"     in df.columns else 0
        with_notes = int((df["notes"].fillna("").str.strip() != "").sum()) if "notes" in df.columns else 0

        # Build per-signal status list for the UI
        signals_today = []
        for _, row in df.sort_values("scanner_score", ascending=False).iterrows():
            signals_today.append({
                "ticker":           row["ticker"],
                "scanner_score":    round(float(row["scanner_score"]), 1),
                "iv30":             round(float(row["iv30"]), 1),
                "iv_rv_ratio":      round(float(row["iv_rv_ratio"]), 3),
                "trade_signal":     row.get("trade_signal", ""),
                "reviewed":         bool(row.get("reviewed",         False)),
                "broker_verified":  bool(row.get("broker_verified",  False)),
                "trade_considered": bool(row.get("trade_considered", False)),
                "trade_taken":      bool(row.get("trade_taken",      False)),
                "notes":            str(row.get("notes", "") or ""),
                "event_risk_flag":  bool(row.get("event_risk_flag",  False)),
                "earnings_penalty": float(row.get("earnings_penalty", 0.0) or 0.0),
            })
    else:
        n = reviewed = verified = considered = traded = with_notes = 0
        signals_today = []

    save_step = {
        "complete": not df.empty,
        "count":    n,
        "detail":   f"{n} signals saved" if n else "Scan not saved yet",
    }
    review_step = {
        "complete": reviewed > 0,
        "count": reviewed, "total": n,
        "detail": f"{reviewed}/{n} signals reviewed" if n else "No signals",
    }
    verify_step = {
        "complete": verified > 0,
        "count": verified, "total": n,
        "detail": f"{verified}/{n} broker-verified" if n else "No signals",
    }
    notes_step = {
        "complete": with_notes > 0,
        "count": with_notes, "total": n,
        "considered": considered, "traded": traded,
        "detail": (
            f"{with_notes} with notes"
            + (f" · {traded} traded" if traded else "")
            + (f" · {considered} considered" if considered else "")
        ) if n else "No signals",
    }

    steps_complete = sum([
        iv_step["complete"], scan_step["complete"], save_step["complete"],
        review_step["complete"], verify_step["complete"], notes_step["complete"],
    ])

    return {
        "date":             today_str,
        "steps_complete":   steps_complete,
        "total_steps":      6,
        "steps": {
            "iv_captured":    iv_step,
            "scan_run":       scan_step,
            "scan_saved":     save_step,
            "reviewed":       review_step,
            "broker_verified":verify_step,
            "notes_added":    notes_step,
        },
        "signals_today":    signals_today,
    }


@router.get("/history/{ticker}")
def get_ticker_history(ticker: str) -> dict:
    """
    Full signal history for one ticker.

    Returns every date the ticker appeared in the scanner results, with
    all stored metrics and a forward-vol outcome placeholder.
    """
    ticker = ticker.upper()
    df     = _journal.load_ticker(ticker)

    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No journal entries found for {ticker}.",
        )

    df_sorted = df.sort_values("scan_date", ascending=False)

    # NaN → None so JSON serialises cleanly
    signals = [
        {k: (None if (isinstance(v, float) and v != v) else v)
         for k, v in row.items()}
        for row in df_sorted.to_dict(orient="records")
    ]

    # Summary metrics for signals with complete outcomes only
    complete = df[df.get("outcome_status", "pending") == "complete"] if "outcome_status" in df.columns else df.iloc[0:0]

    return {
        "ticker":                    ticker,
        "appearances":               len(df),
        "first_seen":                df["scan_date"].min(),
        "last_seen":                 df["scan_date"].max(),
        "avg_score":                 round(float(df["scanner_score"].mean()), 1),
        "avg_iv_rv_ratio":           round(float(df["iv_rv_ratio"].mean()), 3),
        "avg_iv30":                  round(float(df["iv30"].mean()), 1),
        "avg_rv30":                  round(float(df["rv30"].mean()), 1),
        "best_score":                round(float(df["scanner_score"].max()), 1),
        "outcomes_complete":         int(len(complete)),
        "avg_premium_capture_20d":   (
            round(float(complete["premium_capture_20d"].mean()), 2)
            if not complete.empty and "premium_capture_20d" in complete.columns
               and complete["premium_capture_20d"].notna().any()
            else None
        ),
        "signals":                   signals,
    }
