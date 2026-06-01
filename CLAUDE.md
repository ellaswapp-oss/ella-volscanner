# Vol Dashboard

Full-stack volatility-trading dashboard at `vol-dashboard/`. Evaluates options trades using IV, RV, VRP, IV Rank, term structure, skew, macro stress, and regime-based signals across SPY · QQQ · IWM · AAPL · NVDA · TSLA.

## How to run

Backend (FastAPI, Python 3.11+):
```bash
cd backend
.venv/bin/uvicorn app.main:app --reload --port 8000   # DATA_PROVIDER=real to use Polygon/FRED/CBOE; unset = mock
```
Frontend (Next.js 14):
```bash
cd frontend && npm run dev   # http://localhost:3000
```
Both are pre-wired in `.claude/launch.json` (backend defaults to `DATA_PROVIDER=real`).

## Layout

```
backend/
  app/
    api/         # 7 routers: vol, market, history, backtest, data, research, scanner
    calculations/  engine.py (live) + legacy.py (backward-compat — re-exported from __init__, do not delete)
    services/    vol_service (mock) | live_vol_service (real) | snapshot_builder (dispatcher on DATA_PROVIDER)
    data/        registry + base + mock + real providers; iv_store, iv_provider, iv_surface_health,
                 macro_provider, options_utils, earnings_calendar, forward_returns, scanner_journal
    backtesting/ engine, signals, metrics, rule_backtest, iv_rv_sweep, macro_sweep,
                 signal_grades, score_optimization, walk_forward, objective_modes
    scanners/    sp500_vol_scanner
    db/          SQLite — schema.py + repository.py
    core/        config (regime weights live here), env handling
    models/      Pydantic response models
  scripts/       save_snapshot, capture_live_iv_surface, backfill_real_iv_surface,
                 backfill_real_macro, calculate_outcomes, eod_workflow
  tests/         pytest, ~566 collected
frontend/
  app/           Next 14 app router — see Pages below
  components/    panels/ (6) + ~14 shared components
  hooks/         useVolData (60s auto-refresh, AbortController cleanup)
  lib/           api.ts (typed client, 40+ endpoints), types.ts, utils.ts
  services/      additional client wrappers
```

## Backend conventions

- **Provider switching:** `DATA_PROVIDER` env var picks `MockDataProvider` (deterministic synthetic, no network) or `RealDataProvider` (Polygon prices/options, FRED macro, CBOE VIX/VIX3M). Selected via `app/data/registry.py` singleton — callers never branch.
- **Services dispatcher:** `snapshot_builder` routes to `live_vol_service` (real) or `vol_service` (mock). Adding a new endpoint? Mirror this pattern, don't branch in routers.
- **IV store:** Parquet under `app/data/`. Each row tagged `source = real | synthetic_fallback` and `quality`. Market endpoints expose `provider`, `iv_source`, `iv_rank_is_proxy`, `as_of_date` metadata.
- **IV Rank:** 252-day rolling window. When true history < 252d, proxies against RV30 and sets `iv_rank_is_proxy=true`.
- **DB:** SQLite at `backend/data/vol_dashboard.db` (gitignored). Tables: tickers, daily_prices, option_volatility_snapshots, macro_snapshots, trade_signals. Seed with `scripts/save_snapshot.py` (400 days of mock, idempotent UPSERT).
- **Readiness gating:** Real-IV coverage thresholds — warmup 63 rows, usable 100, strong 252. Exposed at `/api/research/readiness`.

## Frontend conventions

- **All pages are client components** (`"use client"`). Flat top-level dirs (no `src/`).
- **No React Query / Redux / Zustand.** Local `useState/useEffect` + `useVolData` hook (60s polling).
- **Pages:** `/` (dashboard), `/backtest`, `/backtest/{grades,optimize,macro,walk-forward}`, `/research/readiness`, `/data/{prices,options,iv-quality,macro,historical-iv}`, `/scanner/{today,sp500,history}`.
- **6 dashboard panels:** MarketRegime, TradeRecommendation, IVvsRV, TermStructure, Skew, MacroStress. Shared: RegimeCard, TradeCard, MetricCard, IVRVBarChart, ZScoreHistoryChart, TermStructureTable, HistoricalTable, BacktestResults, TickerSelector, CardShell, IVQualityBadge, ScoreGauge, TVAreaChart.
- **Stack:** Next.js 14.2.3, React 18, TypeScript 5, Tailwind 3.4.1, Recharts 2.12.4, lightweight-charts 5.2.0, clsx.

## Backtesting

P&L model: `SELL = (IV_entry − RV_holding) / IV_entry`; `BUY = (RV_holding − IV_entry) / IV_entry`. Non-overlapping trades per ticker. Score optimization grid-searches 59,049 weight combos across 3 objective modes. Endpoints: `/api/backtest`, `/api/backtest/{compare,iv-rv-ratio,macro-sweep,signal-grades,score-optimization,walk-forward}`.

## S&P 500 scanner

`/api/scanner/sp500-vol` + `scanners/sp500_vol_scanner.py`: filters tickers with IV/RV > 1.30, healthy IV surface, non-dangerous macro, `SELL_SELECTIVE`/`SELL_AGGRESSIVE` signals. Results cached per trading day with batch rate-limit. EOD pipeline: `scripts/eod_workflow.py` → capture live IV → scan → journal.

- **Event flagging:** `data/earnings_calendar.py` returns the next earnings date so the scanner can flag event-driven IV. Static manual dict maintained quarterly; live Polygon source is a stub placeholder.
- **Outcome tracking:** `data/forward_returns.py` computes 5/10/20-day forward return, RV, and premium capture (`IV30_entry − RV_N`) for each signal. Called by `/api/scanner` and `scripts/calculate_outcomes.py`. Status: `pending` / `partial` / `complete`.

## Constants

- **Regime score weights** (sum to 1.0, in `app/core/config.py` AND `calculations/engine.py` default — verified): IV Rank 0.35, VRP 0.25, IV/RV ratio 0.25, z-score 0.15.
- **Trade signal thresholds:** 0–30 Buy Premium · 30–50 Neutral · 50–70 Sell Selective · 70–100 Sell Aggressive.

## Tests

`backend/.venv/bin/pytest -q` from `backend/` → **559 passed, 7 failed, 566 collected**.

**Known failures** (all in `tests/test_live_signal.py`):
1. `_compute_macro_penalties` returns `None` instead of `0.0` for `total_penalty` when macro data unavailable → `TypeError` at `live_vol_service.py:360`.
2. ATM-IV-by-tenor doesn't populate `iv7/iv14/iv30` for stub chains → `required_tenor_available=False` and 14d tenor missing.

## Roadmap status

Phases 1–6 substantially shipped (calculations, mock + real providers, options chain via Polygon, regime scoring with grid-search optimization, full backtest suite incl. walk-forward, S&P 500 scanner). **Phase 7 (alerts):** planned, not yet built. **ORATS / SpotGamma:** still planned production data sources; current real-provider stack is Polygon + FRED + CBOE only.

## Gotchas

- `calculations/legacy.py` is **actively re-exported** from `calculations/__init__.py` — keep. Provides `iv_rv_ratio` and `iv_rank` that return `None` on bad input (engine returns `nan`); existing tests depend on the `None` contract. Also re-exports `realized_vol`, `vol_risk_premium`, `iv_rv_zscore`, `term_structure_slopes`, `vol_regime_score`, `trade_signal`, `skew_metrics`.
- `data_cache/` (1.6 GB) and `data_store/` are runtime artifacts — gitignored. Don't commit.
- `.claude/` currently contains only `launch.json` (frontend + backend dev configs). Future shared `.claude/agents/`, `.claude/commands/`, or `settings.json` files will be tracked; `.claude/settings.local.json` and `.claude/*.local.*` are gitignored.
