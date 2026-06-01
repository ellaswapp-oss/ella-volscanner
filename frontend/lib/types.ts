export interface RVMetrics {
  rv10: number;
  rv20: number;
  rv30: number;
  rv60: number;
}

export interface IVMetrics {
  iv7?:  number | null;  // null when outside DTE tolerance
  iv14?: number | null;  // live options chain only
  iv30?: number | null;  // null when not available from chain (see iv_surface_metadata)
  iv60?: number | null;  // null when outside DTE tolerance
  iv90?: number | null;  // null when outside DTE tolerance
}

export interface IVSurfaceMetadata {
  iv_surface_status:        string;  // "healthy" | "partial" | "unavailable" | "synthetic_fallback"
  required_tenor_available: boolean; // true when iv30 comes from a real chain
  available_tenors:         string[];
  missing_tenors:           string[];
  term_structure_quality:   string;  // "full" | "partial" | "unavailable"
}

export interface TermStructure {
  front_slope?: number | null;  // null when iv7 unavailable
  mid_slope?:   number | null;  // null when iv60 unavailable
  back_slope?:  number | null;  // null when iv60 or iv90 unavailable
  total_slope?: number | null;  // null when iv7 or iv90 unavailable
  curvature?:   number | null;  // null when front or back is null
  shape: "contango" | "inverted" | "humped" | "flat" | "partial";
}

export interface Skew {
  skew_25d: number;
  atm_iv: number;
  put_25d: number;
  call_25d: number;
}

export interface Signal {
  signal: "BUY_PREMIUM" | "NEUTRAL" | "SELL_SELECTIVE" | "SELL_AGGRESSIVE";
  label: string;
  color: "green" | "yellow" | "orange" | "red";
  description: string;
}

export interface Sparklines {
  dates: string[];
  rv30: number[];
  iv30: number[];
}

export interface TickerSnapshot {
  ticker: string;
  price: number;
  rv: RVMetrics;
  iv: IVMetrics;
  iv_rank: number;
  iv_rv_ratio: number;
  vrp: number;
  iv_rv_zscore: number;
  term_structure: TermStructure;
  skew: Skew;
  regime_score: number;
  signal: Signal;
  sparklines: Sparklines;
  error?: string;
  iv_surface_metadata?: IVSurfaceMetadata | null;
}

export interface MacroData {
  vix: number;
  vix_regime: string;
  credit_spread: number;
  yield_curve: number;
  financial_conditions: number;
}

export interface DashboardData {
  tickers: Record<string, TickerSnapshot>;
  macro: MacroData;
  market_regime: number;
  market_signal: Signal;
}
