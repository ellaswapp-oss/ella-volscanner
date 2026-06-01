/**
 * lib/api.ts
 * ----------
 * Unified typed API client for the Vol Dashboard backend.
 *
 * Three route groups
 * ------------------
 *   api.vol.*       /api/vol/*         dashboard data, snapshots, macro
 *   api.market.*    /api/*             regime, volatility, term-structure, trade-signal
 *   api.backtest.*  /api/backtest/*    signal backtests + signal comparison
 *
 * Environment
 * -----------
 *   NEXT_PUBLIC_API_URL    = http://localhost:8000/api/vol   (vol routes)
 *   NEXT_PUBLIC_SERVER_URL = http://localhost:8000           (market + backtest)
 *
 * Usage
 * -----
 *   import { api } from "@/lib/api";
 *
 *   // Server component — awaited directly
 *   const data   = await api.vol.dashboard();
 *   const regime = await api.market.regime();
 *   const run    = await api.backtest.run({ ticker: "SPY", signal_type: "iv_rv_ratio" });
 *
 *   // Client component — inside useEffect / SWR / React Query
 *   const result = await api.backtest.compare({ ticker: "QQQ", holding_period: 21 });
 */

import type {
  DashboardData,
  IVMetrics,
  MacroData,
  RVMetrics,
  Signal,
  Sparklines,
  TermStructure,
  TickerSnapshot,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Base URLs
//
// NEXT_PUBLIC_API_BASE_URL   preferred single-origin shorthand (just the host)
// NEXT_PUBLIC_SERVER_URL     alias accepted for backwards compatibility
// NEXT_PUBLIC_API_URL        vol-specific prefix (http://host/api/vol)
// ---------------------------------------------------------------------------

const SERVER_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  process.env.NEXT_PUBLIC_SERVER_URL ??
  "http://localhost:8000";

const VOL_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? `${SERVER_BASE}/api/vol`;

// ---------------------------------------------------------------------------
// Core fetch helper
// ---------------------------------------------------------------------------

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    next: { revalidate: 60 },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} — ${url}: ${body}`);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Query-string builder
// ---------------------------------------------------------------------------

type Primitive = string | number | boolean;

function qs(params: Record<string, Primitive | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

// ===========================================================================
// Types — market endpoints
// ===========================================================================

/** One ticker row inside the /api/regime response. */
export interface RegimeTicker {
  ticker:       string;
  regime_score: number;
  signal:       Signal;
  iv_rank:      number;
  iv_rv_ratio:  number;
  vrp:          number;
  iv_rv_zscore: number;
}

/** Full response from GET /api/regime */
export interface RegimeResponse {
  market_regime:  number;
  market_signal:  Signal;
  tickers:        RegimeTicker[];
  vix:            number;
  vix_regime:     string;
  data_source:    string;
}

/** Macro risk penalties applied to the regime score (live provider only). */
export interface MacroPenalty {
  total_penalty: number;   // points deducted from regime score (0–25)
  reasons:       string[]; // human-readable descriptions of each penalty
  available:     boolean;  // false when macro data could not be fetched
}

/**
 * IV surface quality metadata.
 *
 * iv_surface_status
 *   "healthy"            — all core tenors (7/30/60/90) present
 *   "partial"            — iv30 available but some long-tenor data missing
 *   "unavailable"        — iv30 missing from chain; regime uses RV-based estimate
 *   "synthetic_fallback" — chain fetch failed; all IVs are synthetic
 *
 * required_tenor_available
 *   true when iv30 was computed from a real options chain within ±10d tolerance.
 *
 * term_structure_quality
 *   "full"        — all three segment slopes (front/mid/back) are computable
 *   "partial"     — some slopes are null (missing 60d/90d expirations)
 *   "unavailable" — iv30 absent; no slopes possible
 */
export interface IVSurfaceMetadata {
  iv_surface_status:        string;   // "healthy" | "partial" | "unavailable" | "synthetic_fallback"
  required_tenor_available: boolean;
  available_tenors:         string[];
  missing_tenors:           string[];
  term_structure_quality:   string;   // "full" | "partial" | "unavailable"
}

/** Full response from GET /api/volatility/{ticker} */
export interface VolatilityResponse {
  ticker:               string;
  price:                number;
  iv:                   IVMetrics;
  rv:                   RVMetrics;
  iv_rank:              number;
  iv_rv_ratio:          number;
  vrp:                  number;
  iv_rv_zscore:         number;
  sparklines:           Sparklines;
  data_source:          string;
  provider:             string;
  iv_source:            string;
  iv_rank_is_proxy:     boolean;
  as_of_date:           string;
  iv_surface_metadata?: IVSurfaceMetadata | null;
}

/** One (tenor, days, iv) point on the vol curve. */
export interface CurvePoint {
  tenor: string;
  days:  number;
  iv:    number;
}

/** Full response from GET /api/term-structure/{ticker} */
export interface TermStructureResponse extends TermStructure {
  ticker:               string;
  curve:                CurvePoint[];
  data_source:          string;
  provider:             string;
  iv_source:            string;
  iv_surface_metadata?: IVSurfaceMetadata | null;
}

/** Per-factor detail inside the trade-signal response. */
export interface SignalFactor {
  value:        number;
  weight:       number;
  contribution: "low" | "moderate" | "high" | "very_high";
}

/** Full response from GET /api/trade-signal/{ticker} */
export interface TradeSignalResponse {
  ticker:               string;
  regime_score:         number;
  signal:               Signal;
  factors: {
    iv_rank:     SignalFactor;
    vrp:         SignalFactor;
    iv_rv_ratio: SignalFactor;
    z_score:     SignalFactor;
  };
  playbook:             string[];
  data_source:          string;
  provider:             string;
  iv_source:            string;
  iv_rank_is_proxy:     boolean;
  as_of_date:           string;
  macro_penalties?:     MacroPenalty;
  iv_surface_metadata?: IVSurfaceMetadata | null;
}

// ===========================================================================
// Types — history endpoints
// ===========================================================================

/** One row returned by GET /api/history/{ticker} */
export interface HistoryRow {
  date:         string;
  iv7:          number;
  iv30:         number;
  iv60:         number;
  iv90:         number;
  rv10:         number | null;
  rv20:         number | null;
  rv30:         number | null;
  rv60:         number | null;
  iv_rank:      number | null;
  iv_rv_ratio:  number | null;
  vrp:          number | null;
  iv_rv_zscore: number;
  regime_score: number;
  signal:       string;
}

/** Full response from GET /api/history/{ticker} */
export interface HistoryResponse {
  ticker:      string;
  rows:        HistoryRow[];
  data_source: string;
}

/** Full response from GET /api/history/macro */
export interface MacroHistoryRow {
  date:          string;
  vix:           number | null;
  vix_regime:    string | null;
  market_regime: number | null;
  market_signal: string | null;
}

// ===========================================================================
// Types — IV/RV sweep (5-variation comparison)
// ===========================================================================

/** Equity point keyed by sequential trade number (not date). */
export interface TradeEquityPoint {
  trade_n: number;
  equity:  number;
}

/** Per-ticker summary inside a sweep variation. */
export interface SweepTickerStats {
  n_trades:      number;
  win_rate:      number | null;
  avg_return:    number | null;
  median_return: number | null;
}

/** One variation's complete results inside the sweep. */
export interface SweepVariation {
  id:            number;
  name:          string;
  short:         string;
  description:   string;
  rules:         Record<string, number | boolean>;
  // Aggregate metrics
  n_trades:      number;
  win_rate:      number | null;
  avg_return:    number | null;
  median_return: number | null;
  max_drawdown:  number | null;
  sharpe:        number | null;
  sortino:       number | null;
  profit_factor: number | null;
  expectancy:    number | null;
  best_trade:    RuleBacktestTrade | null;
  worst_trade:   RuleBacktestTrade | null;
  by_ticker:     Record<string, SweepTickerStats>;
  by_regime:     Record<string, RegimePerfBucket>;
  equity_points: TradeEquityPoint[];
  /** true when require_real_iv=true and n_trades < 20 */
  sample_size_warning?: boolean;
}

/** Full response from GET /api/backtest/iv-rv-ratio */
// ===========================================================================
// Types — IV data-quality gate (shared across all backtest responses)
// ===========================================================================

/**
 * Aggregated IV data-quality metadata injected into every backtest response.
 *
 * status:
 *   "GOOD"  — real_iv_pct ≥ 80 % — results are trustworthy
 *   "MIXED" — 40 % ≤ real_iv_pct < 80 % — treat with caution
 *   "POOR"  — real_iv_pct < 40 % — not suitable for production decisions
 */
export interface IVQualityGate {
  total:                  number;
  real:                   number;
  interpolated:           number;
  synthetic_fallback:     number;
  real_iv_pct:            number;   // 0.0 – 1.0
  synthetic_fallback_pct: number;   // 0.0 – 1.0
  status:                 "GOOD" | "MIXED" | "POOR";
  warning:                string | null;
}

export interface IVRVSweepResult {
  variations:        SweepVariation[];
  best_variation_id: number;
  tickers:           string[];
  data_source:       string;
  iv_quality?:       IVQualityGate;
  real_iv_filter?:   RealIVFilter;
}

// ===========================================================================
// Types — rule-based backtest endpoint
// ===========================================================================

/** One simulated trade from the rule backtest. */
export interface RuleBacktestTrade {
  entry:        string;
  exit:         string;
  iv_entry:     number;
  rv_hold:      number;
  iv_rv_ratio:  number;
  iv_rank:      number;
  regime_score: number;
  regime_label: string;
  pnl:          number;
  is_winner:    boolean;
}

/** Regime performance bucket from the rule backtest. */
export interface RegimePerfBucket {
  n:        number;
  win_rate: number | null;
  avg_pnl:  number | null;
  total:    number;
}

/** Full response from GET /api/backtest/{ticker} */
export interface RuleBacktestResult {
  ticker: string;
  rule: {
    iv_rv_min:      number;
    iv_rank_min:    number;
    regime_lo:      number;
    regime_hi:      number;
    holding_period: number;
  };
  n_trades:      number;
  win_rate:      number | null;
  avg_return:    number | null;
  total_return:  number;
  max_drawdown:  number | null;
  sharpe:        number | null;
  sortino:       number | null;
  expectancy:    number | null;
  profit_factor: number | null;
  best_trade:    RuleBacktestTrade | null;
  worst_trade:   RuleBacktestTrade | null;
  performance_by_regime: Record<string, RegimePerfBucket>;
  equity_curve:  EquityPoint[];
  trades:        RuleBacktestTrade[];
  data_source:   string;
  iv_data_quality?: {
    real:               number;
    synthetic_fallback: number;
    interpolated:       number;
    total:              number;
  };
}

// ===========================================================================
// Types — macro filter sweep (7-variation comparison)
// ===========================================================================

/** VIX environment bucket for macro sweep performance breakdowns. */
export interface VIXRegimeBucket {
  n:        number;
  win_rate: number | null;
  avg_pnl:  number | null;
  total:    number;
}

/** One trade captured in the macro sweep. */
export interface MacroTrade {
  ticker:      string;
  entry:       string;
  exit:        string;
  iv_entry:    number;
  rv_hold:     number;
  iv_rv_ratio: number;
  iv_rank:     number;
  vix_entry:   number;
  vix_bucket:  "calm" | "normal" | "elevated" | "spike";
  pnl:         number;
  is_winner:   boolean;
}

/** One variation's complete results inside the macro sweep. */
export interface MacroVariation {
  id:          number;
  name:        string;
  short:       string;
  description: string;
  filters:     Record<string, number | boolean>;
  // Aggregate metrics
  n_trades:             number;
  win_rate:             number | null;
  premium_capture_rate: number | null;
  avg_return:           number | null;
  median_return:        number | null;
  max_drawdown:         number | null;
  sharpe:               number | null;
  sortino:              number | null;
  profit_factor:        number | null;
  expectancy:           number | null;
  best_trade:           MacroTrade | null;
  worst_trade:          MacroTrade | null;
  // Breakdowns
  by_ticker:     Record<string, SweepTickerStats>;
  by_vix_regime: Record<string, VIXRegimeBucket>;
  equity_points: TradeEquityPoint[];
  /** true when require_real_iv=true and n_trades is 0 < n < 20 */
  sample_size_warning?: boolean;
}

/** Full response from GET /api/backtest/macro-sweep */
export interface MacroSweepResult {
  variations:        MacroVariation[];
  best_variation_id: number;
  tickers:           string[];
  data_source:       string;
  macro_filters:     Record<string, string>;
  iv_quality?:       IVQualityGate;
  real_iv_filter?:   RealIVFilter;
}

// ===========================================================================
// Types — backtest endpoints
// ===========================================================================

export type BacktestSignalType =
  | "iv_rv_ratio"
  | "iv_rank"
  | "term_structure"
  | "vix_regime"
  | "combined";

/** Threshold and window config echoed back in the result. */
export interface BacktestConfig {
  holding_period:    number;
  lookback:          number;
  iv_rv_threshold:   number;
  iv_rank_threshold: number;
  vix_low:           number;
  vix_elevated:      number;
  ts_contango_min:   number;
}

/** Win/loss stats for one direction (sell or buy). */
export interface DirectionStats {
  n:        number;
  win_rate: number | null;
  avg_pnl:  number | null;
}

/** P&L summary for one regime bucket. */
export interface RegimeBucket {
  n:        number;
  avg_pnl:  number | null;
  win_rate: number | null;
  total:    number;
}

/** One simulated trade. */
export interface BacktestTrade {
  entry:        string;
  exit:         string;
  direction:    "SELL_PREMIUM" | "BUY_PREMIUM";
  iv_entry:     number;
  rv_hold:      number;
  pnl:          number;
  regime_score: number;
  regime_label: "buy_premium" | "neutral" | "sell_selective" | "sell_aggressive";
  iv_rank:      number;
  iv_rv_ratio:  number | null;
  vrp_pct:      number | null;
}

/** One point on the daily equity curve. */
export interface EquityPoint {
  date:   string;   // "YYYY-MM-DD"
  equity: number;   // starts near 1.0
}

/** Full response from GET /api/backtest */
export interface BacktestResult {
  ticker:        string;
  signal_type:   string;
  config:        BacktestConfig;
  // Aggregate metrics (null when fewer than 2 trades or undefined input)
  n_trades:      number;
  sharpe:        number | null;
  sortino:       number | null;
  max_drawdown:  number | null;
  calmar:        number | null;
  win_rate:      number | null;
  expectancy:    number | null;
  profit_factor: number | null;
  total_pnl:     number;
  avg_pnl:       number;
  // Direction breakdown
  sell_stats:    DirectionStats;
  buy_stats:     DirectionStats;
  // Regime breakdown (4 buckets, score 0-100 split at 30/50/70)
  regime_breakdown: {
    buy_premium:      RegimeBucket;
    neutral:          RegimeBucket;
    sell_selective:   RegimeBucket;
    sell_aggressive:  RegimeBucket;
  };
  equity_curve: EquityPoint[];
  trades:       BacktestTrade[];
  data_source:  string;
}

/** One row in the comparison summary table. */
export interface ComparisonSummaryRow {
  signal:       string;
  n_trades:     number;
  sharpe:       number | null;
  max_drawdown: number | null;
  win_rate:     number | null;
  expectancy:   number | null;
  total_pnl:    number;
}

/** Full response from GET /api/backtest/compare */
export interface ComparisonResult {
  ticker:         string;
  holding_period: number;
  summary:        ComparisonSummaryRow[];
  details:        Record<BacktestSignalType, BacktestResult>;
  data_source:    string;
}

// ---------------------------------------------------------------------------
// Param shapes for backtest endpoints
// ---------------------------------------------------------------------------

export interface BacktestParams {
  ticker?:            string;
  signal_type?:       BacktestSignalType;
  holding_period?:    number;   // 1–252, default 30
  lookback?:          number;   // 20–1260, default 252
  iv_rv_threshold?:   number;   // 0.5–5.0, default 1.20
  iv_rank_threshold?: number;   // 0–100, default 50
  vix_low?:           number;   // default 15
  vix_elevated?:      number;   // default 20
  vix_spike?:         number;   // default 35
  ts_contango_min?:   number;   // default 0
  data_days?:         number;   // 100–2520, default 400
}

export interface CompareParams {
  ticker?:            string;
  holding_period?:    number;
  iv_rv_threshold?:   number;
  iv_rank_threshold?: number;
  data_days?:         number;
}

// ===========================================================================
// API client
// ===========================================================================

export const api = {

  // -------------------------------------------------------------------------
  // vol — full dashboard payloads
  // -------------------------------------------------------------------------
  vol: {
    /** All tickers + macro in one payload (used by the main dashboard page). */
    dashboard: () =>
      fetchJSON<DashboardData>(`${VOL_BASE}/dashboard`),

    /** Single ticker snapshot. */
    ticker: (sym: string) =>
      fetchJSON<TickerSnapshot>(`${VOL_BASE}/ticker/${sym.toUpperCase()}`),

    /** List of supported tickers. */
    tickers: () =>
      fetchJSON<{ tickers: string[] }>(`${VOL_BASE}/tickers`),

    /** Macro stress indicators (VIX, credit spread, yield curve, …). */
    macro: () =>
      fetchJSON<MacroData>(`${VOL_BASE}/macro`),
  },

  // -------------------------------------------------------------------------
  // market — focused single-concern responses
  // -------------------------------------------------------------------------
  market: {
    /** Market-wide regime score + per-ticker breakdown sorted by score. */
    regime: () =>
      fetchJSON<RegimeResponse>(`${SERVER_BASE}/api/regime`),

    /** IV surface, multi-window RV, VRP, and 60-day sparklines for one ticker. */
    volatility: (ticker: string) =>
      fetchJSON<VolatilityResponse>(
        `${SERVER_BASE}/api/volatility/${ticker.toUpperCase()}`
      ),

    /** Vol term structure curve + slope/shape classification for one ticker. */
    termStructure: (ticker: string) =>
      fetchJSON<TermStructureResponse>(
        `${SERVER_BASE}/api/term-structure/${ticker.toUpperCase()}`
      ),

    /** Trade recommendation, factor breakdown, and strategy playbook. */
    tradeSignal: (ticker: string) =>
      fetchJSON<TradeSignalResponse>(
        `${SERVER_BASE}/api/trade-signal/${ticker.toUpperCase()}`
      ),

    /** Historical daily snapshots for one ticker (newest first). */
    history: (ticker: string, days = 90) =>
      fetchJSON<HistoryResponse>(
        `${SERVER_BASE}/api/history/${ticker.toUpperCase()}?days=${days}`
      ),

    /** Rule-based backtest (IV/RV > 1.30, IVR > 50, regime 40–75, 10d hold). */
    ruleBacktest: (ticker: string) =>
      fetchJSON<RuleBacktestResult>(
        `${SERVER_BASE}/api/backtest/${ticker.toUpperCase()}`
      ),
  },

  // -------------------------------------------------------------------------
  // backtest — signal performance analytics
  // -------------------------------------------------------------------------
  backtest: {
    /**
     * Run one signal type backtest.
     *
     * Returns full metrics (Sharpe, Sortino, max drawdown, Calmar, win rate,
     * expectancy, profit factor), direction/regime breakdowns, daily equity
     * curve, and the list of individual simulated trades.
     */
    run: (params: BacktestParams = {}) =>
      fetchJSON<BacktestResult>(
        `${SERVER_BASE}/api/backtest${qs(params as Record<string, Primitive | undefined>)}`
      ),

    /**
     * Run all five signal types on the same ticker and return a comparison
     * table alongside the full per-signal detail objects.
     */
    compare: (params: CompareParams = {}) =>
      fetchJSON<ComparisonResult>(
        `${SERVER_BASE}/api/backtest/compare${qs(
          params as Record<string, Primitive | undefined>
        )}`
      ),
  },
};

// ---------------------------------------------------------------------------
// Flat re-exports — backwards compatibility with services/api.ts callers
// ---------------------------------------------------------------------------
export const dashboard = api.vol.dashboard;
export const ticker    = api.vol.ticker;
export const macro     = api.vol.macro;
export const tickers   = api.vol.tickers;

// ---------------------------------------------------------------------------
// Named function exports — simple import style for components
//
//   import { getVolatility, getRegime } from "@/lib/api";
//   const data = await getVolatility("SPY");
// ---------------------------------------------------------------------------

/** IV surface, multi-window RV, VRP, and 60-day sparklines. */
export function getVolatility(ticker: string): Promise<VolatilityResponse> {
  return api.market.volatility(ticker);
}

/** Market-wide regime score and per-ticker breakdown. */
export function getRegime(): Promise<RegimeResponse> {
  return api.market.regime();
}

/** Vol term structure curve, slopes, and shape classification. */
export function getTermStructure(ticker: string): Promise<TermStructureResponse> {
  return api.market.termStructure(ticker);
}

/** Trade recommendation, factor weights, and strategy playbook. */
export function getTradeSignal(ticker: string): Promise<TradeSignalResponse> {
  return api.market.tradeSignal(ticker);
}

/** Historical daily snapshots (newest first). */
export function getHistory(ticker: string, days = 90): Promise<HistoryResponse> {
  return api.market.history(ticker, days);
}

/** Rule-based backtest result for one ticker. */
export function getRuleBacktest(ticker: string): Promise<RuleBacktestResult> {
  return api.market.ruleBacktest(ticker);
}

// ===========================================================================
// Real-IV filter summary (injected into all backtest responses)
// ===========================================================================

/**
 * Summary of the real-IV-only filter applied when require_real_iv=true.
 *
 * enabled          — false when the filter was not requested
 * rows_before_filter — total IV rows in the requested window (all quality tiers)
 * rows_excluded    — rows dropped (synthetic + interpolated)
 * rows_remaining   — rows that passed (real quality, iv30 not null)
 * usable_date_start/end — first and last date with real IV30
 * pct_remaining    — rows_remaining / rows_before_filter
 */
export interface RealIVFilter {
  enabled:              boolean;
  rows_before_filter:   number;
  rows_excluded:        number;
  rows_remaining:       number;
  /** Earliest date with real IV30 (may be absent on aggregated sweep filters). */
  usable_date_start?:   string | null;
  /** Latest date with real IV30 (may be absent on aggregated sweep filters). */
  usable_date_end?:     string | null;
  /** rows_remaining / rows_before_filter */
  pct_remaining?:       number;
}

/** IV/RV sweep: 5 filter variations × all tickers. */
export function getIVRVSweep(options?: {
  dataDays?:      number;
  requireRealIV?: boolean;
}): Promise<IVRVSweepResult> {
  const q = new URLSearchParams();
  if (options?.dataDays)      q.set("data_days",       String(options.dataDays));
  if (options?.requireRealIV) q.set("require_real_iv", "true");
  const qs = q.toString() ? `?${q.toString()}` : "";
  return fetchJSON<IVRVSweepResult>(`${SERVER_BASE}/api/backtest/iv-rv-ratio${qs}`);
}

/** Macro filter sweep: V2 baseline + 5 macro filters + all-combined × all tickers. */
export function getMacroSweep(dataDays?: number, requireRealIV?: boolean): Promise<MacroSweepResult> {
  const q = new URLSearchParams();
  if (dataDays)      q.set("data_days",       String(dataDays));
  if (requireRealIV) q.set("require_real_iv", "true");
  const qs = q.toString() ? `?${q.toString()}` : "";
  return fetchJSON<MacroSweepResult>(`${SERVER_BASE}/api/backtest/macro-sweep${qs}`);
}

// ===========================================================================
// Types — signal grades (scored + graded backtest)
// ===========================================================================

/** One trade from the graded simulation, with score breakdown. */
export interface GradedTrade {
  ticker:      string;
  entry:       string;
  exit:        string;
  iv_entry:    number;
  rv_hold:     number;
  iv_rv_ratio: number;
  iv_rank:     number;
  vix_entry:   number;
  vix_bucket:  "calm" | "normal" | "elevated" | "spike";
  score:       number;
  grade:       "A" | "B" | "C" | "F";
  score_components: Record<string, number>;
  pnl:         number;
  is_winner:   boolean;
}

/** One portfolio strategy result (A-only / A+B / A+B+C / V2 baseline). */
export interface GradePortfolio {
  id:          number;
  name:        string;
  short:       string;
  description: string;
  threshold:   number;
  color:       string;
  grades_included: string[];
  // Aggregate metrics
  n_trades:             number;
  win_rate:             number | null;
  premium_capture_rate: number | null;
  avg_return:           number | null;
  median_return:        number | null;
  max_drawdown:         number | null;
  sharpe:               number | null;
  sortino:              number | null;
  profit_factor:        number | null;
  expectancy:           number | null;
  best_trade:           GradedTrade | null;
  worst_trade:          GradedTrade | null;
  by_ticker:            Record<string, SweepTickerStats>;
  by_vix_regime:        Record<string, VIXRegimeBucket>;
  equity_points:        TradeEquityPoint[];
  /** true when require_real_iv=true and n_trades is 0 < n < 20 */
  sample_size_warning?: boolean;
}

/** Per-grade retrospective stats from the V2 baseline universe. */
export interface GradeStats {
  n:                    number;
  win_rate:             number | null;
  premium_capture_rate: number | null;
  avg_return:           number | null;
  median_return:        number | null;
  max_drawdown:         number | null;
  sharpe:               number | null;
  profit_factor:        number | null;
  expectancy:           number | null;
}

/** One bar in the score histogram. */
export interface ScoreDistBucket {
  score_min: number;
  score_max: number;
  count:     number;
  grade:     "A" | "B" | "C" | "F";
}

/** One row in the scoring rubric. */
export interface ScoreComponent {
  key:   string;
  label: string;
  value: number;
  type:  "positive" | "negative";
}

/** Grade threshold definition. */
export interface GradeMeta {
  label: string;
  min:   number;
  max:   number;
  desc:  string;
}

/** Full response from GET /api/backtest/signal-grades */
export interface SignalGradesResult {
  portfolios:         GradePortfolio[];
  grade_stats:        Record<string, GradeStats>;
  score_distribution: ScoreDistBucket[];
  score_components:   ScoreComponent[];
  grade_meta:         Record<string, GradeMeta>;
  tickers:            string[];
  data_source:        string;
  iv_quality?:        IVQualityGate;
  real_iv_filter?:    RealIVFilter;
}

/** Signal grades: scored V2 entries bucketed A/B/C/F with 4 portfolio comparisons. */
export function getSignalGrades(dataDays?: number, requireRealIV?: boolean): Promise<SignalGradesResult> {
  const q = new URLSearchParams();
  if (dataDays)      q.set("data_days",       String(dataDays));
  if (requireRealIV) q.set("require_real_iv", "true");
  const qs = q.toString() ? `?${q.toString()}` : "";
  return fetchJSON<SignalGradesResult>(`${SERVER_BASE}/api/backtest/signal-grades${qs}`);
}

// ---------------------------------------------------------------------------
// Score-weight optimisation types
// ---------------------------------------------------------------------------

export interface OptimizationWeights {
  [key: string]:  number;   // index signature for Record<string,number> compatibility
  iv_rv:         number;
  iv_rank:       number;
  vix_ts:        number;
  breadth:       number;
  spy_200dma:    number;
  yield_stress:  number;
  oil_shock:     number;
  vix_inversion: number;
  vix_spike:     number;
}

export interface ObjBreakdown {
  pcr_component:  number;
  pf_component:   number;
  mdd_component:  number;
  trade_penalty:  number;
}

export interface OptimizedModel {
  rank:                          number | null;
  objective_score:               number;
  weights:                       OptimizationWeights;
  threshold:                     number;
  objective_breakdown:           ObjBreakdown;
  weight_distance_from_current?: number;
  equity_points:                 TradeEquityPoint[] | null;
  // portfolio metrics
  n_trades:             number;
  win_rate:             number | null;
  premium_capture_rate: number | null;
  profit_factor:        number | null;
  max_drawdown:         number | null;
  sharpe:               number | null;
  sortino:              number | null;
  avg_return:           number | null;
  median_return:        number | null;
  expectancy:           number | null;
  best_trade:           Record<string, unknown> | null;
  worst_trade:          Record<string, unknown> | null;
}

export interface OptimizationComparison {
  objective_improvement:      number;
  pcr_improvement:            number;
  profit_factor_improvement:  number;
  drawdown_improvement:       number;  // positive = drawdown got smaller
  trade_count_change:         number;
  sharpe_improvement:         number;
}

export interface SearchStats {
  total_evaluated:    number;
  weight_combinations: number;
  thresholds_tested:  number[];
  candidates_in_pool: number;
  elapsed_ms:         number;
}

export interface WeightSensitivityPoint {
  weight:        number;
  avg_objective: number;
  count:         number;
}

export interface ScoreOptimizationResult {
  top_models:          OptimizedModel[];
  current_model:       OptimizedModel;
  comparison:          OptimizationComparison | null;
  search_stats:        SearchStats;
  weight_sensitivity:  Record<string, WeightSensitivityPoint[]>;
  weight_grid:         Record<string, number[]>;
  objective_definition: Record<string, unknown>;
  tickers:             string[];
  data_source:         string;
  iv_quality?:         IVQualityGate;
  real_iv_filter?:     RealIVFilter;
  sample_size_warning?: boolean;
}

/** Score-weight grid search: find optimal scoring component weights. */
export function getScoreOptimization(dataDays?: number, requireRealIV?: boolean): Promise<ScoreOptimizationResult> {
  const q = new URLSearchParams();
  if (dataDays)      q.set("data_days",       String(dataDays));
  if (requireRealIV) q.set("require_real_iv", "true");
  const qs = q.toString() ? `?${q.toString()}` : "";
  return fetchJSON<ScoreOptimizationResult>(`${SERVER_BASE}/api/backtest/score-optimization${qs}`);
}

// ---------------------------------------------------------------------------
// Walk-forward validation types
// ---------------------------------------------------------------------------

export interface WFMetrics {
  n_trades:             number;
  win_rate:             number | null;
  avg_return:           number | null;
  median_return:        number | null;
  premium_capture_rate: number | null;
  profit_factor:        number | null;
  sharpe:               number | null;
  sortino:              number | null;
  max_drawdown:         number | null;
  expectancy:           number | null;
}

export interface WFDegradation {
  [key: string]:        number | null;  // index signature for Record compat
  objective:            number | null;  // OOS − IS  (negative = worse OOS)
  win_rate:             number | null;
  premium_capture_rate: number | null;
  profit_factor:        number | null;
  sharpe:               number | null;
  max_drawdown_abs:     number | null;  // positive = more drawdown in OOS
  avg_return:           number | null;
  n_trades:             number | null;
}

export interface WFEquityPoint {
  trade_n:   number;
  equity:    number;
  window_id: number | null;
  date:      string;
}

export interface WFWindow {
  window_id:     number;
  train_period:  { start: string; end: string; n_trading_days: number };
  test_period:   { start: string; end: string; n_trading_days: number };
  n_train_candidates: number;
  n_test_candidates:  number;
  best_weights:    OptimizationWeights;
  best_threshold:  number;
  train_objective: number;
  test_objective:  number;
  current_test_objective: number;
  train_metrics:          WFMetrics;
  test_metrics:           WFMetrics;
  current_test_metrics:   WFMetrics;
  degradation:            WFDegradation;
  weight_turnover:        number;   // L1 weight change from previous window
  train_equity:           WFEquityPoint[];
  test_equity:            WFEquityPoint[];
}

export interface WFSummary {
  n_windows:                number;
  in_sample_pooled_obj:     number;
  oos_pooled_obj:           number;
  current_oos_pooled_obj:   number;
  in_sample_pooled:         WFMetrics;
  oos_pooled:               WFMetrics;
  current_oos_pooled:       WFMetrics;
  avg_degradation:          WFDegradation;
  avg_weight_turnover:      number | null;
}

/** One mode's aggregate results in the mode comparison table. */
export interface ModeComparisonEntry {
  label:               string;
  description:         string;
  is_obj_avg:          number | null;
  oos_obj_avg:         number | null;
  avg_degradation_obj: number | null;
  oos_n_trades:        number;
  oos_win_rate:        number | null;
  oos_pcr:             number | null;
  oos_profit_factor:   number | null;
  oos_sharpe:          number | null;
  oos_mdd_abs:         number | null;   // abs value — positive = more drawdown
  avg_weight_turnover: number | null;
}

export interface WalkForwardResult {
  windows:             WFWindow[];
  summary:             WFSummary | null;
  oos_equity:          WFEquityPoint[];
  is_equity:           WFEquityPoint[];
  current_oos_equity:  WFEquityPoint[];
  mode_comparison:     Record<string, ModeComparisonEntry> | null;
  objective_mode:      string;
  mode_definitions:    Record<string, { label: string; description: string }>;
  params: {
    train_days:     number;
    test_days:      number;
    step_days:      number;
    data_days:      number;
    n_windows:      number;
    n_macro_dates:  number;
  };
  component_keys: string[];
  tickers:        string[];
  data_source:    string;
  elapsed_ms:     number;
  error?:         string;
  iv_quality?:    IVQualityGate;
  real_iv_filter?: RealIVFilter;
}

/** Walk-forward validation: rolling train/test window optimisation with 3 objective modes. */
export function getWalkForward(params?: {
  dataDays?:      number;
  trainDays?:     number;
  testDays?:      number;
  stepDays?:      number;
  objectiveMode?: string;   // "balanced" | "pcr_stability" | "conservative"
  requireRealIV?: boolean;
}): Promise<WalkForwardResult> {
  const q = new URLSearchParams();
  if (params?.dataDays)      q.set("data_days",       String(params.dataDays));
  if (params?.trainDays)     q.set("train_days",      String(params.trainDays));
  if (params?.testDays)      q.set("test_days",       String(params.testDays));
  if (params?.stepDays)      q.set("step_days",       String(params.stepDays));
  if (params?.objectiveMode) q.set("objective_mode",  params.objectiveMode);
  if (params?.requireRealIV) q.set("require_real_iv", "true");
  const qs = q.toString() ? `?${q.toString()}` : "";
  return fetchJSON<WalkForwardResult>(`${SERVER_BASE}/api/backtest/walk-forward${qs}`);
}

// ---------------------------------------------------------------------------
// Data provider status
// ---------------------------------------------------------------------------

/** Per-capability status from the active provider. */
export interface ProviderCapability {
  implemented: boolean;
  source:      string | null;
  status:      string;
  configured?: boolean;
}

/** Full response from GET /api/data/provider-status */
export interface ProviderStatus {
  provider:     "mock" | "real";
  label:        "Mock Data" | "Live Data";
  is_live:      boolean;
  capabilities: Record<string, ProviderCapability>;
}

/** Active data provider info — used by the ProviderBadge component. */
export function getProviderStatus(): Promise<ProviderStatus> {
  return fetchJSON<ProviderStatus>(`${SERVER_BASE}/api/data/provider-status`, {
    // Revalidate every 60 s; provider rarely changes without a server restart.
    next: { revalidate: 60 },
  });
}

// ---------------------------------------------------------------------------
// Prices / OHLCV
// ---------------------------------------------------------------------------

/** One OHLCV bar returned by GET /api/data/prices/{ticker} */
export interface OHLCVRow {
  date:   string;   // "YYYY-MM-DD"
  open:   number | null;
  high:   number | null;
  low:    number | null;
  close:  number;
  volume: number | null;
}

/** Summary stats attached to the prices response. */
export interface PriceStats {
  n_rows:        number;
  first_date:    string | null;
  last_date:     string | null;
  latest_close:  number | null;
}

/** Full response from GET /api/data/prices/{ticker} */
export interface PricesResponse {
  ticker:      string;
  provider:    "mock" | "real";
  data_source: string;
  rows:        OHLCVRow[];
  stats:       PriceStats;
}

// ---------------------------------------------------------------------------
// Macro series
// ---------------------------------------------------------------------------

/** One row from GET /api/data/macro */
export interface MacroRow {
  date:           string;   // "YYYY-MM-DD"
  vix1m:          number | null;
  vix3m:          number | null;
  vix_spread:     number | null;   // vix3m − vix1m; positive = contango
  breadth:        number | null;   // 0–100; may be proxy (SPY 50-DMA)
  us10y:          number | null;   // %
  crude_oil:      number | null;   // USD/bbl
  credit_spread:  number | null;   // %
  spy_price:      number | null;   // USD
}

/** Latest-values summary from GET /api/data/macro */
export interface MacroStats {
  latest_vix:           number | null;
  latest_vix3m:         number | null;
  latest_vix_spread:    number | null;
  is_vix_inverted:      boolean;
  latest_us10y:         number | null;
  latest_crude_oil:     number | null;
  latest_credit_spread: number | null;
  latest_spy:           number | null;
  n_rows:               number;
  first_date:           string | null;
  last_date:            string | null;
}

/** Full response from GET /api/data/macro */
export interface MacroSeriesResponse {
  provider:         "mock" | "real";
  data_source:      string;
  breadth_is_proxy: boolean;
  stats:            MacroStats;
  rows:             MacroRow[];
}

/** Fetch daily macro signal history. */
export function getMacroSeries(options?: {
  days?:  number;
  start?: string;
  end?:   string;
}): Promise<MacroSeriesResponse> {
  const q = new URLSearchParams();
  if (options?.days)  q.set("days",  String(options.days));
  if (options?.start) q.set("start", options.start);
  if (options?.end)   q.set("end",   options.end);
  const qs = q.toString() ? `?${q.toString()}` : "";
  return fetchJSON<MacroSeriesResponse>(
    `${SERVER_BASE}/api/data/macro${qs}`,
    { next: { revalidate: 0 } },
  );
}

/** Fetch daily OHLCV bars for a ticker. */
export function getPrices(
  ticker: string,
  options?: { days?: number; start?: string; end?: string },
): Promise<PricesResponse> {
  const q = new URLSearchParams();
  if (options?.days)  q.set("days",  String(options.days));
  if (options?.start) q.set("start", options.start);
  if (options?.end)   q.set("end",   options.end);
  const qs = q.toString() ? `?${q.toString()}` : "";
  return fetchJSON<PricesResponse>(
    `${SERVER_BASE}/api/data/prices/${ticker.toUpperCase()}${qs}`,
    { next: { revalidate: 0 } },   // always fresh in the diagnostic tool
  );
}

// ---------------------------------------------------------------------------
// Options chain
// ---------------------------------------------------------------------------

/** One row from GET /api/data/options/{ticker} */
export interface OptionRow {
  underlying_ticker:  string | null;
  expiration:         string;           // "YYYY-MM-DD"
  strike:             number | null;
  option_type:        "call" | "put" | null;
  bid:                number | null;
  ask:                number | null;
  mid:                number | null;
  last:               number | null;
  implied_volatility: number | null;    // percent (e.g. 18.95)
  delta:              number | null;
  gamma:              number | null;
  theta:              number | null;
  vega:               number | null;
  open_interest:      number | null;
  volume:             number | null;
}

/** Full response from GET /api/data/options/{ticker} */
export interface OptionsChainResponse {
  ticker:           string;
  provider:         "mock" | "real";
  data_source:      string;
  pricing_date:     string;             // "YYYY-MM-DD"
  underlying_price: number | null;
  n_contracts:      number;
  rows:             OptionRow[];
}

/** One point on the IV term structure curve. */
export interface IVCurvePoint {
  tenor: string;    // "7D" | "14D" | "30D" | "60D" | "90D"
  dte:   number;
  iv:    number;    // percent
}

/** Per-tenor ATM contract detail inside the IV surface response. */
export interface ATMContract {
  tenor:      string;
  dte:        number;
  expiration: string;
  strike:     number | null;
  call_iv:    number | null;   // percent
  put_iv:     number | null;   // percent
  avg_iv:     number | null;   // percent
}

/** IV surface dict with one value per tenor. */
export interface IVSurface {
  iv7:  number | null;
  iv14: number | null;
  iv30: number | null;
  iv60: number | null;
  iv90: number | null;
}

/** Full response from GET /api/data/iv-surface/{ticker} */
export interface IVSurfaceResponse {
  ticker:           string;
  provider:         "mock" | "real";
  data_source:      string;
  pricing_date:     string;
  underlying_price: number | null;
  iv_surface:       IVSurface;
  curve:            IVCurvePoint[];
  atm_contracts:    ATMContract[];
}

/** Fetch the full options chain for a ticker. */
export function getOptionsChain(
  ticker: string,
  options?: {
    date?:        string;
    expiration?:  string;
    option_type?: "call" | "put";
    near_atm?:    boolean;
  },
): Promise<OptionsChainResponse> {
  const q = new URLSearchParams();
  if (options?.date)        q.set("date",        options.date);
  if (options?.expiration)  q.set("expiration",  options.expiration);
  if (options?.option_type) q.set("option_type", options.option_type);
  if (options?.near_atm)    q.set("near_atm",    "true");
  const qs = q.toString() ? `?${q.toString()}` : "";
  return fetchJSON<OptionsChainResponse>(
    `${SERVER_BASE}/api/data/options/${ticker.toUpperCase()}${qs}`,
    { next: { revalidate: 0 } },
  );
}

/** Fetch the ATM IV surface (term structure) for a ticker. */
export function getIVSurface(
  ticker: string,
  options?: { date?: string },
): Promise<IVSurfaceResponse> {
  const q = new URLSearchParams();
  if (options?.date) q.set("date", options.date);
  const qs = q.toString() ? `?${q.toString()}` : "";
  return fetchJSON<IVSurfaceResponse>(
    `${SERVER_BASE}/api/data/iv-surface/${ticker.toUpperCase()}${qs}`,
    { next: { revalidate: 0 } },
  );
}

// ===========================================================================
// Types — historical IV surface (Phase 3)
// ===========================================================================

/** One daily row from GET /api/data/historical-iv/{ticker} */
export interface HistoricalIVRow {
  date:            string;
  ticker:          string;
  iv7:             number | null;
  iv14:            number | null;
  iv30:            number | null;
  iv60:            number | null;
  iv90:            number | null;
  source:          string;
  iv_data_quality: "real" | "interpolated" | "synthetic_fallback";
}

export interface IVDataQualitySummary {
  real:               number;
  synthetic_fallback: number;
  interpolated:       number;
  total:              number;
}

export interface HistoricalIVResponse {
  ticker:               string;
  rows:                 HistoricalIVRow[];
  data_quality_summary: IVDataQualitySummary;
  date_range:           { start: string; end: string };
  n_rows:               number;
  store_populated:      boolean;
  message?:             string;
}

/**
 * Fetch historical IV surfaces from the Parquet store for a ticker.
 *
 * @param ticker    Ticker symbol (case-insensitive)
 * @param options   Optional filters: start/end (YYYY-MM-DD) or days count
 */
export function getHistoricalIV(
  ticker: string,
  options?: { start?: string; end?: string; days?: number },
): Promise<HistoricalIVResponse> {
  const q = new URLSearchParams();
  if (options?.start) q.set("start", options.start);
  if (options?.end)   q.set("end",   options.end);
  if (options?.days)  q.set("days",  String(options.days));
  const qs = q.toString() ? `?${q.toString()}` : "";
  return fetchJSON<HistoricalIVResponse>(
    `${SERVER_BASE}/api/data/historical-iv/${ticker.toUpperCase()}${qs}`,
    { next: { revalidate: 0 } },
  );
}

// ===========================================================================
// Types — IV quality progress (GET /api/data/iv-quality-progress)
// ===========================================================================

export interface IVMissingRange {
  start: string;
  end:   string;
  count: number;
}

export interface IVTickerProgress {
  ticker:                string;
  total_required_rows:   number;
  real_rows:             number;
  interpolated_rows:     number;
  synthetic_rows:        number;
  real_pct:              number;   // 0.0 – 1.0
  rows_needed_for_good:  number;
  latest_real_iv_date:   string | null;
  earliest_real_iv_date: string | null;
  status:                "GOOD" | "MIXED" | "POOR";
  missing_real_ranges:   IVMissingRange[];
  backfill_cmd:          string;
}

export interface IVQualityProgressSummary {
  total_required_rows:   number;
  real_rows:             number;
  interpolated_rows:     number;
  synthetic_rows:        number;
  real_pct:              number;
  status:                "GOOD" | "MIXED" | "POOR";
  rows_needed_for_good:  number;
  backfill_all_cmd:      string;
}

export interface IVQualityProgressResponse {
  window: {
    data_days:           number;
    start:               string;
    end:                 string;
    total_trading_days:  number;
    good_threshold_rows: number;
  };
  summary: IVQualityProgressSummary;
  tickers: IVTickerProgress[];
}

export function getIVQualityProgress(
  dataDays?: number,
): Promise<IVQualityProgressResponse> {
  const q = dataDays ? `?data_days=${dataDays}` : "";
  return fetchJSON<IVQualityProgressResponse>(
    `${SERVER_BASE}/api/data/iv-quality-progress${q}`,
    { next: { revalidate: 0 } },
  );
}

// ---------------------------------------------------------------------------
// IV Surface Health
// ---------------------------------------------------------------------------

export type IVSurfaceWarningCode =
  | "missing_tenor"
  | "iv_out_of_range"
  | "tenor_jump"
  | "duplicate_iv"
  | "tenor_out_of_tolerance"
  | "duplicate_expiration";

export type SpreadQuality = "TIGHT" | "FAIR" | "WIDE" | "VERY_WIDE" | "NO_QUOTE";

export interface IVSurfaceWarning {
  severity:   "warning" | "invalid";
  code:       IVSurfaceWarningCode;
  message:    string;
  tenor?:     string;
  tenors?:    string[];
  value?:     number;
  jump?:      number;
  diff?:      number;
  // tenor_out_of_tolerance fields
  target_dte?:  number;
  actual_dte?:  number;
  dte_diff?:    number;
  tolerance?:   number;
  expiration?:  string;
}

export interface IVContractDetail {
  strike:         number | null;
  iv:             number | null;
  bid:            number | null;
  ask:            number | null;
  mid:            number | null;
  spread:         number | null;
  spread_pct:     number | null;
  spread_quality: SpreadQuality;
  open_interest:  number | null;
  volume:         number | null;
}

export interface IVTenorDetail {
  tenor:            string;           // "7D"
  tenor_key:        string;           // "iv7"
  dte_target:       number;           // 7
  expiration:       string | null;    // null when outside tolerance
  dte_actual:       number | null;
  dte_diff:         number | null;
  within_tolerance: boolean;
  avg_iv:           number | null;
  call:             IVContractDetail | null;
  put:              IVContractDetail | null;
}

export interface IVSurfaceHealthResponse {
  ticker:             string;
  provider:           string;
  pricing_date:       string;
  underlying_price:   number;
  status:             "healthy" | "suspicious" | "invalid";
  warnings:           IVSurfaceWarning[];
  iv_surface: {
    iv7:  number | null;
    iv14: number | null;
    iv30: number | null;
    iv60: number | null;
    iv90: number | null;
  };
  tenor_details:      IVTenorDetail[];
  source_expirations: string[];
  n_expirations:      number;
}

export function getIVSurfaceHealth(
  ticker: string,
  date?: string,
): Promise<IVSurfaceHealthResponse> {
  const q = date ? `?date=${date}` : "";
  return fetchJSON<IVSurfaceHealthResponse>(
    `${SERVER_BASE}/api/data/iv-surface-health/${ticker}${q}`,
    { next: { revalidate: 0 } },
  );
}

// ===========================================================================
// Types — Research Readiness (GET /api/research/readiness)
// ===========================================================================

/** Readiness level labels, ordered from least to most data. */
export type ReadinessLevel = "NOT_READY" | "EARLY" | "USABLE" | "STRONG";

/** Module trust status. */
export type ModuleStatus = "AVAILABLE" | "PARTIAL" | "NOT_READY";

/** Per-ticker real-IV coverage and milestone countdowns. */
export interface TickerReadiness {
  ticker:                   string;
  real_rows:                number;
  valid_iv30_rows:          number;
  first_real_date:          string | null;
  latest_real_date:         string | null;
  usable_trading_days:      number;
  /** Trading days needed to reach the 63-bar warmup threshold. */
  days_until_warmup_63:     number;
  /** Trading days needed to reach 100 valid IV30 rows. */
  days_until_100:           number;
  /** Trading days needed to reach 252 valid IV30 rows. */
  days_until_252:           number;
  /** Calendar-day estimates (≈ trading days × 7/5). */
  cal_days_until_warmup_63: number;
  cal_days_until_100:       number;
  cal_days_until_252:       number;
  readiness:                ReadinessLevel;
}

/** Universe-level aggregate readiness (binding = minimum across tickers). */
export interface UniverseReadiness {
  label:               string;
  tickers:             string[];
  min_valid_iv30_rows: number;
  readiness:           ReadinessLevel;
}

/** One entry in the module trust table. */
export interface ResearchModule {
  name:          string;
  description:   string;
  /** Which ticker universe provides the binding row count. */
  universe:      "any" | "etf" | "all";
  rows_required: number;
  rows_current:  number;
  status:        ModuleStatus;
  notes:         string;
}

/** Full response from GET /api/research/readiness. */
export interface ResearchReadinessResponse {
  as_of:      string;
  thresholds: {
    warmup_63:  number;
    usable_100: number;
    strong_252: number;
    ideal_504:  number;
  };
  tickers:   TickerReadiness[];
  universes: {
    etf:         UniverseReadiness;
    single_name: UniverseReadiness;
    all:         UniverseReadiness;
  };
  modules:   ResearchModule[];
}

/** Fetch the real-IV research readiness report. */
export function getResearchReadiness(): Promise<ResearchReadinessResponse> {
  return fetchJSON<ResearchReadinessResponse>(
    `${SERVER_BASE}/api/research/readiness`,
    { next: { revalidate: 0 } },
  );
}

// ===========================================================================
// Types — S&P 500 Vol Scanner  (GET /api/scanner/sp500-vol)
// ===========================================================================

export type SpreadQualityLabel = "TIGHT" | "FAIR" | "WIDE" | "VERY_WIDE" | "NO_QUOTE";
export type ScannerSortField   =
  | "score" | "iv_rv_ratio" | "vrp_abs" | "iv30" | "regime_score" | "liquidity_score";

/** How the IV value was sourced from Polygon. */
export type IVSourceQuality =
  | "quoted_market_iv"          // real bid/ask quotes — tradeable spread available
  | "polygon_model_iv_no_quote" // Polygon model IV only; no market quote returned
  | "invalid";                  // IV could not be computed

/** One passing ticker from the scanner. */
export interface ScannerTickerResult {
  ticker:             string;
  price:              number;
  rv30:               number;
  iv30:               number;
  iv_rv_ratio:        number;
  vrp_abs:            number;    // IV30 - RV30 in vol points
  vrp_pct:            number;    // (IV30 - RV30) / RV30 as %
  iv_rank:            number;    // 0–100
  iv_surface_status:  string;    // "healthy" | "suspicious" | "invalid"
  available_tenors:   string[];
  missing_tenors:     string[];
  macro_penalty:      number;
  macro_reasons:      string[];
  regime_score:       number;
  signal:             Signal;
  score:              number;    // composite scanner rank score 0–100
  // Liquidity
  atm_open_interest:  number | null;
  atm_volume:         number | null;
  spread_pct:         number | null;
  spread_quality:     SpreadQualityLabel;
  contract_count:     number;
  valid_tenors:       number;
  liquidity_score:    number;    // 0–100
  // IV provenance
  iv_source_quality:       IVSourceQuality;
  broker_verify_required:  boolean;
  // Earnings / event awareness
  next_earnings_date:       string | null;   // ISO date or null
  days_to_earnings:         number | null;
  earnings_within_14d:      boolean;
  earnings_within_30d:      boolean;
  event_risk_flag:          boolean;
  event_risk_reason:        string | null;
  earnings_score_penalty:   number;          // pts deducted from score
}

/** One entry in the errors list. */
export interface ScannerError {
  ticker: string;
  error:  string;
}

/** Full response from GET /api/scanner/sp500-vol */
export interface SP500ScanResult {
  scan_timestamp:   string;
  scan_date:        string;
  total_universe:   number;
  universe_total:   number;   // full S&P 500 size (not just what was scanned)
  scanned:          number;
  passed:           number;
  failed:           number;
  skipped:          number;
  error_count:      number;
  elapsed_seconds:  number;
  from_cache:       boolean;
  // Macro environment
  macro_dangerous:       boolean;
  macro_penalty:         number | null;   // null when macro data unavailable
  macro_reasons:         string[];
  macro_available:       boolean;
  macro_status:          "available" | "unavailable" | "partial";
  macro_warning:         string | null;
  macro_missing_fields:  string[];
  // Results
  results:          ScannerTickerResult[];
  errors:           ScannerError[];
  params_used:      Record<string, number | boolean | string>;
}

/** Parameters for the S&P 500 vol scanner. */
export interface SP500ScanParams {
  minRatio?:          number;   // default 1.30
  requireIv30?:       boolean;  // default true
  includeSuspicious?: boolean;  // default true
  excludeInvalid?:    boolean;  // default true
  maxResults?:        number;   // default 50
  maxTickers?:        number;   // default 50
  batchSize?:         number;   // default 10
  delaySeconds?:      number;   // default 0.2
  minOpenInterest?:   number;   // default 100
  minVolume?:         number;   // default 10
  maxSpreadPct?:      number;   // default 0.25
  minValidTenors?:              number;   // default 2
  excludeEarningsWithinDays?:   number;   // 0=off, 7/14/30 to filter
  forceRefresh?:                boolean;  // default false
  sortBy?:                      ScannerSortField;
}

/**
 * Run (or retrieve cached) S&P 500 volatility scan.
 *
 * First call of the day triggers a full scan (slow).
 * Subsequent calls return the in-memory cached result instantly.
 * Pass forceRefresh=true to re-run.
 */
export function getSP500Scan(params: SP500ScanParams = {}): Promise<SP500ScanResult> {
  const q = new URLSearchParams();
  if (params.minRatio          != null) q.set("min_ratio",           String(params.minRatio));
  if (params.requireIv30       != null) q.set("require_iv30",        String(params.requireIv30));
  if (params.includeSuspicious != null) q.set("include_suspicious",  String(params.includeSuspicious));
  if (params.excludeInvalid    != null) q.set("exclude_invalid",     String(params.excludeInvalid));
  if (params.maxResults        != null) q.set("max_results",         String(params.maxResults));
  if (params.maxTickers        != null) q.set("max_tickers",         String(params.maxTickers));
  if (params.batchSize         != null) q.set("batch_size",          String(params.batchSize));
  if (params.delaySeconds      != null) q.set("delay_seconds",       String(params.delaySeconds));
  if (params.minOpenInterest   != null) q.set("min_open_interest",   String(params.minOpenInterest));
  if (params.minVolume         != null) q.set("min_volume",          String(params.minVolume));
  if (params.maxSpreadPct      != null) q.set("max_spread_pct",      String(params.maxSpreadPct));
  if (params.minValidTenors           != null) q.set("min_valid_tenors",              String(params.minValidTenors));
  if (params.excludeEarningsWithinDays != null) q.set("exclude_earnings_within_days", String(params.excludeEarningsWithinDays));
  if (params.forceRefresh)                     q.set("force_refresh",                "true");
  if (params.sortBy            != null) q.set("sort_by",             params.sortBy);
  const qs = q.toString() ? `?${q.toString()}` : "";
  return fetchJSON<SP500ScanResult>(
    `${SERVER_BASE}/api/scanner/sp500-vol${qs}`,
    { cache: "no-store" },
  );
}

// ---------------------------------------------------------------------------
// Scanner Journal
// ---------------------------------------------------------------------------

export interface JournalSignal {
  scan_date:               string;
  ticker:                  string;
  price:                   number;
  iv30:                    number;
  rv30:                    number;
  iv_rv_ratio:             number;
  volatility_risk_premium: number;
  surface_status:          string;
  iv_source_quality:       IVSourceQuality;
  broker_verify_required:  boolean;
  liquidity_score:         number;
  macro_penalty:           number;
  earnings_penalty:        number;
  event_risk_flag:         boolean;
  trade_signal:            string;
  scanner_score:           number;
  // Manual review fields (false/null/"" until PATCH /journal/{date}/{ticker})
  reviewed:          boolean;
  broker_verified:   boolean;
  actual_bid:        number | null;
  actual_ask:        number | null;
  actual_mid:        number | null;
  actual_spread_pct: number | null;
  trade_considered:  boolean;
  trade_taken:       boolean;
  notes:             string;
  // Forward return / RV outcomes (null until calculate_outcomes runs)
  fwd_ret_5d:          number | null;
  fwd_ret_10d:         number | null;
  fwd_ret_20d:         number | null;
  fwd_rv5d:            number | null;
  fwd_rv10d:           number | null;
  fwd_rv20d:           number | null;
  premium_capture_5d:  number | null;
  premium_capture_10d: number | null;
  premium_capture_20d: number | null;
  outcome_status:      "pending" | "partial" | "complete";
}

export interface DailySummary {
  scan_date:      string;
  signal_count:   number;
  avg_score:      number;
  avg_iv_rv_ratio: number;
  top_ticker:     string;
  tickers:        string;  // comma-separated
}

export interface TickerLeaderboardEntry {
  ticker:          string;
  appearances:     number;
  avg_score:       number;
  avg_iv_rv_ratio: number;
  avg_iv30:        number;
  avg_rv30:        number;
  first_seen:      string;
  last_seen:       string;
  best_score:      number;
}

export interface ScannerHistory {
  dates:          string[];
  daily_summary:  DailySummary[];
  leaderboard:    TickerLeaderboardEntry[];
  recent_signals: JournalSignal[];
  total_signals:  number;
  unique_tickers: number;
}

export interface TickerHistory {
  ticker:                  string;
  appearances:             number;
  first_seen:              string;
  last_seen:               string;
  avg_score:               number;
  avg_iv_rv_ratio:         number;
  avg_iv30:                number;
  avg_rv30:                number;
  best_score:              number;
  outcomes_complete:       number;
  avg_premium_capture_20d: number | null;
  signals:                 JournalSignal[];
}

/** Partial update for manual review fields on a journal signal. */
export interface JournalReviewUpdate {
  reviewed?:          boolean;
  broker_verified?:   boolean;
  actual_bid?:        number | null;
  actual_ask?:        number | null;
  actual_mid?:        number | null;
  actual_spread_pct?: number | null;
  trade_considered?:  boolean;
  trade_taken?:       boolean;
  notes?:             string;
}

export interface JournalSaveResult {
  saved:     boolean;
  scan_date: string;
  rows:      number;
  path:      string;
}

export interface OutcomeCalculateResult {
  calculated:   number;
  skipped:      number;
  errors:       number;
  rows_updated: number;
  candidates:   number;
  message?:     string;
}

export function saveScanJournal(scanDate?: string): Promise<JournalSaveResult> {
  const qs = scanDate ? `?scan_date=${scanDate}` : "";
  return fetchJSON<JournalSaveResult>(
    `${SERVER_BASE}/api/scanner/journal/save${qs}`,
    { method: "POST", cache: "no-store" },
  );
}

export function getScannerHistory(limitDays = 365): Promise<ScannerHistory> {
  return fetchJSON<ScannerHistory>(
    `${SERVER_BASE}/api/scanner/history?limit_days=${limitDays}`,
    { cache: "no-store" },
  );
}

export function getTickerHistory(ticker: string): Promise<TickerHistory> {
  return fetchJSON<TickerHistory>(
    `${SERVER_BASE}/api/scanner/history/${encodeURIComponent(ticker)}`,
    { cache: "no-store" },
  );
}

/** Apply manual review fields to one journal signal via PATCH. */
export function updateJournalSignal(
  date:   string,
  ticker: string,
  update: JournalReviewUpdate,
): Promise<{ updated: boolean; date: string; ticker: string; fields: string[] }> {
  return fetchJSON(
    `${SERVER_BASE}/api/scanner/journal/${encodeURIComponent(date)}/${encodeURIComponent(ticker)}`,
    {
      method:  "PATCH",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(update),
      cache:   "no-store",
    },
  );
}

// ---------------------------------------------------------------------------
// Workflow status
// ---------------------------------------------------------------------------

export interface WorkflowStep {
  complete: boolean;
  detail:   string;
  count?:   number;
  total?:   number;
}

export interface WorkflowSignal {
  ticker:           string;
  scanner_score:    number;
  iv30:             number;
  iv_rv_ratio:      number;
  trade_signal:     string;
  reviewed:         boolean;
  broker_verified:  boolean;
  trade_considered: boolean;
  trade_taken:      boolean;
  notes:            string;
  event_risk_flag:  boolean;
  earnings_penalty: number;
}

export interface WorkflowStatus {
  date:           string;
  steps_complete: number;
  total_steps:    number;
  steps: {
    iv_captured:    WorkflowStep & { tickers?: string[] };
    scan_run:       WorkflowStep & { passed?: number; scanned?: number };
    scan_saved:     WorkflowStep;
    reviewed:       WorkflowStep;
    broker_verified:WorkflowStep;
    notes_added:    WorkflowStep & { considered?: number; traded?: number };
  };
  signals_today: WorkflowSignal[];
}

export function getWorkflowStatus(): Promise<WorkflowStatus> {
  return fetchJSON<WorkflowStatus>(
    `${SERVER_BASE}/api/scanner/workflow/today`,
    { cache: "no-store" },
  );
}

export function calculateOutcomes(params?: {
  ticker?:    string;
  scanDate?:  string;
  force?:     boolean;
  minDays?:   number;
}): Promise<OutcomeCalculateResult> {
  const qs = new URLSearchParams();
  if (params?.ticker)           qs.set("ticker",    params.ticker);
  if (params?.scanDate)         qs.set("scan_date", params.scanDate);
  if (params?.force)            qs.set("force",     "true");
  if (params?.minDays != null)  qs.set("min_days",  String(params.minDays));
  const query = qs.toString() ? `?${qs}` : "";
  return fetchJSON<OutcomeCalculateResult>(
    `${SERVER_BASE}/api/scanner/outcomes/calculate${query}`,
    { method: "POST", cache: "no-store" },
  );
}
