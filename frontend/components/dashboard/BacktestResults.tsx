"use client";

import { clsx } from "clsx";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import { CardShell, CardSkeleton, CardError } from "./CardShell";
import type { RuleBacktestResult, RuleBacktestTrade, RegimePerfBucket } from "@/lib/api";

interface Props {
  data:    RuleBacktestResult | null;
  loading: boolean;
  error:   string | null;
  ticker:  string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pct(v: number | null, dec = 1): string {
  if (v == null || !isFinite(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(dec)}%`;
}

function fmt2(v: number | null): string {
  if (v == null || !isFinite(v)) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}`;
}

function pnlColor(v: number | null): string {
  if (v == null) return "text-slate-500";
  return v >= 0 ? "text-green-400" : "text-red-400";
}

// ---------------------------------------------------------------------------
// Stat tile
// ---------------------------------------------------------------------------

function Stat({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string;
}) {
  return (
    <div className="rounded-lg bg-surface-3/40 px-3 py-2.5">
      <p className="text-[9px] font-semibold uppercase tracking-widest text-slate-600 mb-1">
        {label}
      </p>
      <p className={clsx("text-xl font-bold tabular-nums", color ?? "text-white")}>
        {value}
      </p>
      {sub && <p className="text-[9px] text-slate-600 mt-0.5">{sub}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Trade highlight
// ---------------------------------------------------------------------------

function TradeHighlight({
  label,
  trade,
  accent,
}: {
  label: string;
  trade: RuleBacktestTrade;
  accent: string;
}) {
  return (
    <div className={clsx("rounded-lg border px-3 py-2", accent)}>
      <p className="text-[9px] font-semibold uppercase tracking-widest text-slate-500 mb-1">
        {label}
      </p>
      <p className={clsx("text-base font-bold tabular-nums", pnlColor(trade.pnl))}>
        {pct(trade.pnl)}
      </p>
      <p className="text-[9px] text-slate-500 font-mono mt-0.5">
        {trade.entry} → {trade.exit}
      </p>
      <div className="flex gap-3 mt-1.5 text-[9px] text-slate-600 font-mono">
        <span>IVR {trade.iv_rank.toFixed(0)}</span>
        <span>IV/RV {trade.iv_rv_ratio.toFixed(2)}×</span>
        <span>Reg {trade.regime_score.toFixed(0)}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Regime breakdown table
// ---------------------------------------------------------------------------

const REGIME_META: Record<string, { label: string; color: string }> = {
  buy_premium:     { label: "Buy Vol  (<30)",  color: "text-green-400" },
  neutral:         { label: "Neutral  (30-50)", color: "text-yellow-400" },
  sell_selective:  { label: "Sell Sel (50-70)", color: "text-orange-400" },
  sell_aggressive: { label: "Sell Agg (>70)",   color: "text-red-400" },
};

function RegimeTable({ breakdown }: { breakdown: Record<string, RegimePerfBucket> }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[10px]">
        <thead>
          <tr className="border-b border-border/50">
            <th className="pb-1.5 text-left text-slate-600 font-semibold uppercase tracking-widest">
              Regime
            </th>
            <th className="pb-1.5 text-center text-slate-600 font-semibold uppercase tracking-widest">
              n
            </th>
            <th className="pb-1.5 text-center text-slate-600 font-semibold uppercase tracking-widest">
              Win%
            </th>
            <th className="pb-1.5 text-right text-slate-600 font-semibold uppercase tracking-widest">
              Avg P&L
            </th>
            <th className="pb-1.5 text-right text-slate-600 font-semibold uppercase tracking-widest">
              Total
            </th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(REGIME_META).map(([key, meta]) => {
            const b = breakdown[key];
            if (!b) return null;
            return (
              <tr key={key} className="border-b border-border/20">
                <td className={clsx("py-1.5 font-medium", meta.color)}>
                  {meta.label}
                </td>
                <td className="py-1.5 text-center text-slate-400 tabular-nums">
                  {b.n}
                </td>
                <td className="py-1.5 text-center tabular-nums">
                  <span className={b.win_rate != null && b.win_rate > 0.5 ? "text-green-400" : "text-slate-400"}>
                    {b.win_rate != null ? `${(b.win_rate * 100).toFixed(0)}%` : "—"}
                  </span>
                </td>
                <td className={clsx("py-1.5 text-right tabular-nums font-medium", pnlColor(b.avg_pnl))}>
                  {pct(b.avg_pnl)}
                </td>
                <td className={clsx("py-1.5 text-right tabular-nums", pnlColor(b.total))}>
                  {pct(b.total)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Equity curve chart
// ---------------------------------------------------------------------------

function EquityCurve({ curve }: { curve: { date: string; equity: number }[] }) {
  if (curve.length < 2) {
    return (
      <div className="flex items-center justify-center h-28 text-[10px] text-slate-600">
        Not enough trade data to plot equity curve
      </div>
    );
  }

  // Downsample to ~80 points max so the chart stays readable
  const step = Math.max(1, Math.floor(curve.length / 80));
  const pts  = curve.filter((_, i) => i % step === 0 || i === curve.length - 1);

  // Sparse X-axis ticks — every ~15 points
  const tickStep = Math.max(1, Math.floor(pts.length / 5));
  const ticks = pts
    .filter((_, i) => i % tickStep === 0 || i === pts.length - 1)
    .map((p) => p.date.slice(5));  // MM-DD

  const final = pts[pts.length - 1].equity;
  const lineColor = final >= 1.0 ? "#34d399" : "#f87171";

  return (
    <ResponsiveContainer width="100%" height={120}>
      <AreaChart data={pts} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
        <defs>
          <linearGradient id="eqGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="10%" stopColor={lineColor} stopOpacity={0.2} />
            <stop offset="95%" stopColor={lineColor} stopOpacity={0.0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.10)" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={(v: string) => v.slice(5)}
          ticks={ticks}
          tick={{ fill: "#475569", fontSize: 9 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tick={{ fill: "#475569", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          width={36}
          tickFormatter={(v: number) => `${((v - 1) * 100).toFixed(0)}%`}
        />
        <Tooltip
          contentStyle={{
            background: "#1e293b",
            border: "1px solid rgba(100,116,139,0.25)",
            borderRadius: "6px",
            padding: "6px 10px",
            fontSize: "11px",
          }}
          labelStyle={{ color: "#94a3b8" }}
          itemStyle={{ color: "#e2e8f0" }}
          formatter={(v: number) => [`${((v - 1) * 100).toFixed(2)}%`, "Return"]}
          labelFormatter={(l: string) => l.slice(5)}
        />
        <ReferenceLine y={1} stroke="rgba(100,116,139,0.4)" />
        <Area
          type="stepAfter"
          dataKey="equity"
          stroke={lineColor}
          strokeWidth={1.5}
          fill="url(#eqGradient)"
          dot={false}
          activeDot={{ r: 4, fill: lineColor, strokeWidth: 0 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function BacktestResults({ data, loading, error, ticker }: Props) {
  if (loading) return <CardSkeleton rows={6} />;
  if (error)   return <CardError message={error} />;
  if (!data)   return null;

  const rule = data.rule;
  const noTrades = data.n_trades === 0;

  return (
    <CardShell>
      {/* ── Header ──────────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-2 mb-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-1">
            Rule Backtest — {ticker}
          </p>
          <p className="text-[11px] text-slate-500 leading-relaxed">
            Entry: IV/RV &gt; {rule.iv_rv_min}× · IVR &gt; {rule.iv_rank_min} ·
            regime {rule.regime_lo}–{rule.regime_hi} · hold {rule.holding_period}d
          </p>
        </div>
        <span className="shrink-0 rounded-full bg-surface-3 px-2.5 py-1 text-[9px] font-mono text-slate-500">
          mock data · {data.data_source}
        </span>
      </div>

      {noTrades ? (
        <p className="text-xs text-slate-600 py-6 text-center">
          No trades fired under these conditions — try relaxing the entry rules.
        </p>
      ) : (
        <>
          {/* ── Summary stats ────────────────────────────────────────── */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
            <Stat
              label="Trades"
              value={String(data.n_trades)}
              sub="non-overlapping"
            />
            <Stat
              label="Win Rate"
              value={data.win_rate != null ? `${(data.win_rate * 100).toFixed(0)}%` : "—"}
              color={data.win_rate != null && data.win_rate >= 0.5 ? "text-green-400" : "text-red-400"}
            />
            <Stat
              label="Avg Return"
              value={pct(data.avg_return)}
              sub="per trade"
              color={pnlColor(data.avg_return)}
            />
            <Stat
              label="Max Drawdown"
              value={data.max_drawdown != null ? `${(data.max_drawdown * 100).toFixed(1)}%` : "—"}
              color={data.max_drawdown != null && data.max_drawdown < -0.05 ? "text-red-400" : "text-slate-300"}
            />
          </div>

          {/* ── Secondary stats ──────────────────────────────────────── */}
          <div className="grid grid-cols-3 gap-2 mb-4">
            <Stat label="Sharpe"  value={fmt2(data.sharpe)}  />
            <Stat label="Sortino" value={fmt2(data.sortino)} />
            <Stat label="Profit Factor" value={fmt2(data.profit_factor)} />
          </div>

          {/* ── Equity curve ─────────────────────────────────────────── */}
          <div className="mb-4">
            <p className="text-[9px] font-semibold uppercase tracking-widest text-slate-600 mb-2">
              Equity Curve
            </p>
            <EquityCurve curve={data.equity_curve} />
          </div>

          {/* ── Best / Worst ─────────────────────────────────────────── */}
          {(data.best_trade || data.worst_trade) && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-4">
              {data.best_trade && (
                <TradeHighlight
                  label="Best Trade"
                  trade={data.best_trade}
                  accent="border-green-500/20 bg-green-500/5"
                />
              )}
              {data.worst_trade && (
                <TradeHighlight
                  label="Worst Trade"
                  trade={data.worst_trade}
                  accent="border-red-500/20 bg-red-500/5"
                />
              )}
            </div>
          )}

          {/* ── Performance by regime ────────────────────────────────── */}
          <div>
            <p className="text-[9px] font-semibold uppercase tracking-widest text-slate-600 mb-2">
              Performance by Regime
            </p>
            <RegimeTable breakdown={data.performance_by_regime} />
          </div>
        </>
      )}
    </CardShell>
  );
}
