"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { clsx } from "clsx";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Legend,
} from "recharts";
import { getIVRVSweep } from "@/lib/api";
import type { IVRVSweepResult, SweepVariation, RealIVFilter } from "@/lib/api";
import IVQualityBadge from "@/components/dashboard/IVQualityBadge";

// ---------------------------------------------------------------------------
// Palette for the 5 variations
// ---------------------------------------------------------------------------

const VAR_COLORS: Record<number, string> = {
  1: "#94a3b8",   // slate-400  — base
  2: "#60a5fa",   // blue-400   — + IV Rank
  3: "#34d399",   // emerald-400 — + Regime
  4: "#fb923c",   // orange-400 — + Contango
  5: "#a78bfa",   // violet-400 — full rule
};

const REGIME_META: Record<string, { label: string; color: string }> = {
  buy_premium:     { label: "Buy Vol  (< 30)",  color: "text-green-400" },
  neutral:         { label: "Neutral  (30–50)", color: "text-yellow-400" },
  sell_selective:  { label: "Sell Sel (50–70)", color: "text-orange-400" },
  sell_aggressive: { label: "Sell Agg (> 70)",  color: "text-red-400" },
};

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function pct(v: number | null | undefined, dec = 1, forceSign = false): string {
  if (v == null || !isFinite(v)) return "—";
  const sign = forceSign && v > 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(dec)}%`;
}

function num(v: number | null | undefined, dec = 2): string {
  if (v == null || !isFinite(v)) return "—";
  return v.toFixed(dec);
}

// For each metric, higher = better (except max_drawdown where less negative = better)
type Direction = "high" | "low";

function bestIdx(values: (number | null)[], dir: Direction): number {
  let best = -1;
  let bestVal = dir === "high" ? -Infinity : Infinity;
  values.forEach((v, i) => {
    if (v == null || !isFinite(v)) return;
    if (dir === "high" && v > bestVal) { bestVal = v; best = i; }
    if (dir === "low"  && v < bestVal) { bestVal = v; best = i; }
  });
  return best;
}

function worstIdx(values: (number | null)[], dir: Direction): number {
  let worst = -1;
  let worstVal = dir === "high" ? Infinity : -Infinity;
  values.forEach((v, i) => {
    if (v == null || !isFinite(v)) return;
    if (dir === "high" && v < worstVal) { worstVal = v; worst = i; }
    if (dir === "low"  && v > worstVal) { worstVal = v; worst = i; }
  });
  return worst;
}

// ---------------------------------------------------------------------------
// Real-IV filter summary banner
// ---------------------------------------------------------------------------

function RealIVFilterBanner({ filter }: { filter: RealIVFilter | undefined | null }) {
  if (!filter?.enabled) return null;
  const { rows_before_filter, rows_excluded, rows_remaining, usable_date_start, usable_date_end, pct_remaining } = filter;
  return (
    <div className="rounded-xl border border-blue-500/30 bg-blue-500/6 px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="shrink-0 rounded-full border border-blue-400/30 bg-blue-500/15 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-widest text-blue-300">
          Real IV Only
        </span>
        <div className="flex flex-wrap gap-2 text-[11px]">
          <span className="rounded bg-blue-400/10 px-1.5 py-0.5 text-blue-300">
            {rows_remaining.toLocaleString()} real rows used
          </span>
          <span className="rounded bg-slate-700/60 px-1.5 py-0.5 text-slate-400">
            {rows_excluded.toLocaleString()} synthetic excluded
          </span>
          {pct_remaining != null && (
            <span className="rounded bg-slate-700/40 px-1.5 py-0.5 text-slate-500">
              {(pct_remaining * 100).toFixed(1)}% of {rows_before_filter.toLocaleString()} total rows
            </span>
          )}
          {usable_date_start && usable_date_end && (
            <span className="rounded bg-slate-700/40 px-1.5 py-0.5 text-slate-500">
              {usable_date_start} → {usable_date_end}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sample-size warning chip (shown inline per variation when n_trades < 20)
// ---------------------------------------------------------------------------

function SampleSizeWarning() {
  return (
    <span className="ml-1.5 inline-flex items-center gap-0.5 rounded bg-amber-500/15 border border-amber-500/30 px-1.5 py-0.5 text-[9px] font-semibold text-amber-400">
      ⚠ &lt;20 trades
    </span>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded bg-surface-3", className)} />;
}

function PageSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-64 w-full" />
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Best-variation banner
// ---------------------------------------------------------------------------

function BestBanner({ v }: { v: SweepVariation }) {
  return (
    <div className="rounded-xl border border-violet-500/30 bg-violet-500/8 px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-2">
      <span className="shrink-0 rounded-full bg-violet-500/20 border border-violet-400/30 px-2.5 py-0.5 text-[10px] font-bold text-violet-300 uppercase tracking-widest">
        ★ Best Variation
      </span>
      <div className="flex-1">
        <p className="text-sm font-semibold text-white">
          {v.name}
          {v.sample_size_warning && <SampleSizeWarning />}
        </p>
        <p className="text-[11px] text-slate-400">{v.description}</p>
      </div>
      <div className="flex gap-4 shrink-0 text-[11px]">
        <div className="text-center">
          <p className="text-[9px] text-slate-500 uppercase tracking-widest">Sharpe</p>
          <p className="font-bold text-violet-300">{num(v.sharpe)}</p>
        </div>
        <div className="text-center">
          <p className="text-[9px] text-slate-500 uppercase tracking-widest">Win%</p>
          <p className="font-bold text-violet-300">{pct(v.win_rate, 0)}</p>
        </div>
        <div className="text-center">
          <p className="text-[9px] text-slate-500 uppercase tracking-widest">Trades</p>
          <p className={clsx("font-bold", v.sample_size_warning ? "text-amber-400" : "text-violet-300")}>
            {v.n_trades}
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Comparison table
// ---------------------------------------------------------------------------

interface MetricRow {
  label:    string;
  key:      keyof SweepVariation;
  fmt:      (v: SweepVariation) => string;
  dir:      Direction;
  isGroup?: boolean;
}

const METRIC_ROWS: MetricRow[] = [
  {
    label: "Trades (total)", key: "n_trades",
    fmt: (v) => String(v.n_trades), dir: "high",
  },
  {
    label: "Win Rate", key: "win_rate",
    fmt: (v) => pct(v.win_rate, 1), dir: "high",
  },
  {
    label: "Avg Return / trade", key: "avg_return",
    fmt: (v) => pct(v.avg_return, 2, true), dir: "high",
  },
  {
    label: "Median Return / trade", key: "median_return",
    fmt: (v) => pct(v.median_return, 2, true), dir: "high",
  },
  {
    label: "Sharpe Ratio", key: "sharpe",
    fmt: (v) => num(v.sharpe), dir: "high",
  },
  {
    label: "Sortino Ratio", key: "sortino",
    fmt: (v) => num(v.sortino), dir: "high",
  },
  {
    label: "Profit Factor", key: "profit_factor",
    fmt: (v) => num(v.profit_factor), dir: "high",
  },
  {
    label: "Expectancy", key: "expectancy",
    fmt: (v) => pct(v.expectancy, 2, true), dir: "high",
  },
  {
    label: "Max Drawdown", key: "max_drawdown",
    fmt: (v) => pct(v.max_drawdown, 1), dir: "low",
  },
  {
    label: "Best Trade", key: "best_trade",
    fmt: (v) => v.best_trade ? pct(v.best_trade.pnl, 1, true) : "—", dir: "high",
  },
  {
    label: "Worst Trade", key: "worst_trade",
    fmt: (v) => v.worst_trade ? pct(v.worst_trade.pnl, 1, true) : "—", dir: "low",
  },
];

function ComparisonTable({
  variations,
  bestId,
}: {
  variations: SweepVariation[];
  bestId: number;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface-2 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[540px]">
          <thead>
            <tr className="border-b border-border">
              <th className="py-3 pl-4 pr-3 text-left text-[9px] font-semibold uppercase tracking-widest text-slate-600 w-36">
                Metric
              </th>
              {variations.map((v) => {
                const isBest = v.id === bestId;
                return (
                  <th
                    key={v.id}
                    className={clsx(
                      "py-3 px-3 text-center text-[10px] font-semibold",
                      isBest
                        ? "bg-violet-500/10 text-violet-300"
                        : "text-slate-400"
                    )}
                  >
                    <div className="flex flex-col items-center gap-0.5">
                      {isBest && (
                        <span className="text-[8px] text-violet-400 font-bold uppercase tracking-widest">
                          ★ Best
                        </span>
                      )}
                      <span
                        className="inline-block w-2 h-2 rounded-full mb-0.5"
                        style={{ background: VAR_COLORS[v.id] }}
                      />
                      <span className="leading-tight text-center">{v.short}</span>
                      {v.sample_size_warning && (
                        <span className="text-[8px] text-amber-400 font-bold">⚠ {v.n_trades}t</span>
                      )}
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {METRIC_ROWS.map((row, ri) => {
              const rawVals = variations.map((v) => {
                const raw = v[row.key];
                if (raw == null) return null;
                if (typeof raw === "object" && "pnl" in (raw as object)) {
                  return (raw as { pnl: number }).pnl;
                }
                return typeof raw === "number" ? raw : null;
              });
              const bi = bestIdx(rawVals, row.dir);
              const wi = worstIdx(rawVals, row.dir);

              return (
                <tr
                  key={row.key as string}
                  className={clsx(
                    "border-b border-border/40",
                    ri % 2 === 0 ? "bg-transparent" : "bg-surface-3/20"
                  )}
                >
                  <td className="py-2.5 pl-4 pr-3 text-[10px] text-slate-500 font-medium whitespace-nowrap">
                    {row.label}
                  </td>
                  {variations.map((v, vi) => {
                    const isBest = v.id === bestId;
                    const isTop  = vi === bi;
                    const isBot  = vi === wi && bi !== wi;
                    return (
                      <td
                        key={v.id}
                        className={clsx(
                          "py-2.5 px-3 text-center text-xs font-semibold tabular-nums",
                          isBest && "bg-violet-500/5",
                          isTop  && "text-green-400",
                          isBot  && !isTop && "text-red-400/70",
                          !isTop && !isBot && "text-slate-300"
                        )}
                      >
                        {row.fmt(v)}
                        {isTop && (
                          <span className="ml-0.5 text-green-500 text-[8px]"> ▲</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Equity curve chart
// ---------------------------------------------------------------------------

function EquityChart({ variations }: { variations: SweepVariation[] }) {
  const maxN = Math.max(...variations.map((v) => v.equity_points.length), 1);

  const data = Array.from({ length: maxN }, (_, i) => {
    const pt: Record<string, number | null> = { trade_n: i + 1 };
    variations.forEach((v) => {
      pt[`v${v.id}`] = v.equity_points[i]?.equity ?? null;
    });
    return pt;
  });

  // Y-axis domain
  const allEquities = variations.flatMap((v) => v.equity_points.map((p) => p.equity));
  const minEq = allEquities.length ? Math.min(...allEquities) : 0.5;
  const maxEq = allEquities.length ? Math.max(...allEquities) : 1.5;
  const pad   = Math.max((maxEq - minEq) * 0.15, 0.05);

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-4">
        Equity Curves — Sequential (trade number on X-axis)
      </p>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data} margin={{ top: 4, right: 16, left: -16, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.10)" vertical={false} />
          <XAxis
            dataKey="trade_n"
            tick={{ fill: "#475569", fontSize: 9 }}
            axisLine={false}
            tickLine={false}
            label={{ value: "Trade #", position: "insideBottomRight", offset: -4, fontSize: 9, fill: "#475569" }}
          />
          <YAxis
            domain={[minEq - pad, maxEq + pad]}
            tick={{ fill: "#475569", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            width={40}
            tickFormatter={(v: number) => `${((v - 1) * 100).toFixed(0)}%`}
          />
          <Tooltip
            contentStyle={{
              background: "#1e293b",
              border: "1px solid rgba(100,116,139,0.25)",
              borderRadius: "6px",
              padding: "8px 12px",
              fontSize: "11px",
            }}
            labelStyle={{ color: "#94a3b8" }}
            labelFormatter={(v: number) => `Trade ${v}`}
            formatter={(val: number, name: string) => {
              const id = parseInt(name.replace("v", ""));
              const vr = variations.find((v) => v.id === id);
              return [`${((val - 1) * 100).toFixed(2)}%`, vr?.short ?? name];
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: "10px", paddingTop: "8px" }}
            formatter={(val: string) => {
              const id = parseInt(val.replace("v", ""));
              return variations.find((v) => v.id === id)?.short ?? val;
            }}
          />
          <ReferenceLine y={1} stroke="rgba(100,116,139,0.3)" />
          {variations.map((v) => (
            <Line
              key={v.id}
              type="stepAfter"
              dataKey={`v${v.id}`}
              stroke={VAR_COLORS[v.id]}
              strokeWidth={v.id === variations.find(x => x.equity_points.length)?.id ? 2 : 1.5}
              dot={false}
              connectNulls={false}
              activeDot={{ r: 4, strokeWidth: 0 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-ticker breakdown table
// ---------------------------------------------------------------------------

function TickerBreakdown({
  variations,
  tickers,
  bestId,
}: {
  variations: SweepVariation[];
  tickers: string[];
  bestId: number;
}) {
  const [activeId, setActiveId] = useState(bestId);
  const active = variations.find((v) => v.id === activeId)!;

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
          Per-Ticker Breakdown
        </p>
        {/* Variation tabs */}
        <div className="flex gap-1 flex-wrap">
          {variations.map((v) => (
            <button
              key={v.id}
              onClick={() => setActiveId(v.id)}
              className={clsx(
                "rounded px-2.5 py-1 text-[10px] font-medium transition-colors",
                activeId === v.id
                  ? "text-white"
                  : "text-slate-500 hover:text-slate-300 bg-surface-3"
              )}
              style={
                activeId === v.id
                  ? { background: VAR_COLORS[v.id] + "33", color: VAR_COLORS[v.id], border: `1px solid ${VAR_COLORS[v.id]}44` }
                  : {}
              }
            >
              {v.id === bestId && "★ "}{v.short}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[400px] text-[11px]">
          <thead>
            <tr className="border-b border-border/50">
              <th className="pb-2 text-left text-[9px] font-semibold uppercase tracking-widest text-slate-600 pr-3">
                Ticker
              </th>
              <th className="pb-2 text-center text-[9px] font-semibold uppercase tracking-widest text-slate-600 px-2">
                Trades
              </th>
              <th className="pb-2 text-center text-[9px] font-semibold uppercase tracking-widest text-slate-600 px-2">
                Win Rate
              </th>
              <th className="pb-2 text-right text-[9px] font-semibold uppercase tracking-widest text-slate-600 px-2">
                Avg Return
              </th>
              <th className="pb-2 text-right text-[9px] font-semibold uppercase tracking-widest text-slate-600 pl-2">
                Median Return
              </th>
            </tr>
          </thead>
          <tbody>
            {tickers.map((ticker) => {
              const row = active.by_ticker[ticker];
              if (!row) return null;
              const hasData = row.n_trades > 0;
              return (
                <tr key={ticker} className="border-b border-border/20 hover:bg-surface-3/20">
                  <td className="py-2 pr-3">
                    <span className="font-semibold text-white">{ticker}</span>
                  </td>
                  <td className="py-2 px-2 text-center text-slate-400">
                    {hasData ? row.n_trades : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="py-2 px-2 text-center">
                    {hasData && row.win_rate != null ? (
                      <span className={clsx(
                        "font-medium",
                        row.win_rate >= 0.5 ? "text-green-400" : "text-red-400"
                      )}>
                        {(row.win_rate * 100).toFixed(0)}%
                      </span>
                    ) : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="py-2 px-2 text-right">
                    {hasData && row.avg_return != null ? (
                      <span className={clsx(
                        "font-medium tabular-nums",
                        row.avg_return >= 0 ? "text-green-400" : "text-red-400"
                      )}>
                        {pct(row.avg_return, 2, true)}
                      </span>
                    ) : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="py-2 pl-2 text-right">
                    {hasData && row.median_return != null ? (
                      <span className={clsx(
                        "font-medium tabular-nums",
                        row.median_return >= 0 ? "text-green-400" : "text-red-400"
                      )}>
                        {pct(row.median_return, 2, true)}
                      </span>
                    ) : <span className="text-slate-600">—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Regime breakdown
// ---------------------------------------------------------------------------

function RegimeBreakdown({
  variations,
  bestId,
}: {
  variations: SweepVariation[];
  bestId: number;
}) {
  const [activeId, setActiveId] = useState(bestId);
  const active = variations.find((v) => v.id === activeId)!;

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
          Performance by Regime at Entry
        </p>
        <div className="flex gap-1 flex-wrap">
          {variations.map((v) => (
            <button
              key={v.id}
              onClick={() => setActiveId(v.id)}
              className={clsx(
                "rounded px-2.5 py-1 text-[10px] font-medium transition-colors",
                activeId === v.id ? "text-white" : "text-slate-500 hover:text-slate-300 bg-surface-3"
              )}
              style={
                activeId === v.id
                  ? { background: VAR_COLORS[v.id] + "33", color: VAR_COLORS[v.id], border: `1px solid ${VAR_COLORS[v.id]}44` }
                  : {}
              }
            >
              {v.id === bestId && "★ "}{v.short}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="border-b border-border/50">
              {["Regime", "n", "Win%", "Avg P&L", "Total P&L"].map((h) => (
                <th key={h} className={clsx(
                  "pb-2 text-[9px] font-semibold uppercase tracking-widest text-slate-600",
                  h === "Regime" ? "text-left pr-3" : "text-right px-3"
                )}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(REGIME_META).map(([key, meta]) => {
              const row = active.by_regime[key];
              if (!row) return null;
              return (
                <tr key={key} className="border-b border-border/20">
                  <td className={clsx("py-2 pr-3 font-medium", meta.color)}>
                    {meta.label}
                  </td>
                  <td className="py-2 px-3 text-right text-slate-400 tabular-nums">
                    {row.n}
                  </td>
                  <td className="py-2 px-3 text-right tabular-nums">
                    {row.win_rate != null ? (
                      <span className={row.win_rate >= 0.5 ? "text-green-400" : "text-slate-400"}>
                        {(row.win_rate * 100).toFixed(0)}%
                      </span>
                    ) : "—"}
                  </td>
                  <td className={clsx(
                    "py-2 px-3 text-right font-medium tabular-nums",
                    row.avg_pnl != null && row.avg_pnl >= 0 ? "text-green-400" : "text-red-400"
                  )}>
                    {row.avg_pnl != null ? pct(row.avg_pnl, 2, true) : "—"}
                  </td>
                  <td className={clsx(
                    "py-2 pl-3 text-right font-semibold tabular-nums",
                    row.total >= 0 ? "text-green-400" : "text-red-400"
                  )}>
                    {pct(row.total, 2, true)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Variation rules legend
// ---------------------------------------------------------------------------

function VariationLegend({ variations }: { variations: SweepVariation[] }) {
  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-3">
        Variation Rules
      </p>
      <div className="space-y-2">
        {variations.map((v) => (
          <div key={v.id} className="flex items-start gap-3">
            <span
              className="mt-0.5 shrink-0 inline-block w-2.5 h-2.5 rounded-full"
              style={{ background: VAR_COLORS[v.id] }}
            />
            <div>
              <span className="text-[11px] font-semibold text-slate-300">{v.name}</span>
              <p className="text-[10px] text-slate-600 leading-relaxed">{v.description}</p>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[9px] text-slate-700">
        All variations: SELL_PREMIUM · 10-day hold · no overlapping positions per ticker · mock data
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function BacktestPage() {
  const [result, setResult] = useState<IVRVSweepResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [requireRealIV, setRequireRealIV] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getIVRVSweep({ requireRealIV })
      .then((d) => setResult(d))
      .catch((e) => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  }, [requireRealIV]);

  const bestVar = result?.variations.find(
    (v) => v.id === result.best_variation_id
  ) ?? null;

  return (
    <main className="min-h-screen bg-surface px-4 py-5 md:px-6 md:py-7">

      {/* ── Header ──────────────────────────────────────────────────── */}
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <Link
              href="/"
              className="text-[10px] text-slate-500 hover:text-slate-300 transition-colors flex items-center gap-1"
            >
              ← Dashboard
            </Link>
            <span className="text-slate-700 text-[10px]">/</span>
            <span className="text-[10px] text-slate-400">Backtest</span>
          </div>
          <h1 className="text-xl font-bold tracking-tight text-white">
            IV/RV Ratio Backtest
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            {result
              ? `${result.tickers.join(" · ")} · 5 variations · ${result.data_source} data`
              : "Testing 5 filter variations of IV/RV > 1.30 rule across all tickers…"}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <Link
            href="/backtest/macro"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-[10px] font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
          >
            Macro Filters →
          </Link>
          <Link
            href="/backtest/grades"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-[10px] font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
          >
            Signal Grades →
          </Link>
          <Link
            href="/backtest/optimize"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-[10px] font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
          >
            Optimise Weights →
          </Link>
          <Link
            href="/backtest/walk-forward"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-[10px] font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
          >
            Walk-Forward →
          </Link>
          <span className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-[10px] font-mono text-slate-500">
            SELL_PREMIUM · 10d hold
          </span>
          <span className="shrink-0 rounded-full bg-yellow-500/10 border border-yellow-500/20 px-3 py-1 text-xs font-medium text-yellow-400">
            Mock Data
          </span>

          {/* Real-IV toggle */}
          <label className="shrink-0 flex items-center gap-2 cursor-pointer rounded-full border border-border bg-surface-3 px-3 py-1 text-[10px] font-medium text-slate-400 hover:border-slate-500 hover:text-slate-200 transition-colors select-none">
            <span
              className={clsx(
                "relative inline-block w-7 h-4 rounded-full transition-colors duration-200",
                requireRealIV ? "bg-blue-500" : "bg-slate-600"
              )}
            >
              <span
                className={clsx(
                  "absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-white shadow transition-transform duration-200",
                  requireRealIV ? "translate-x-3" : "translate-x-0"
                )}
              />
            </span>
            <input
              type="checkbox"
              className="sr-only"
              checked={requireRealIV}
              onChange={(e) => setRequireRealIV(e.target.checked)}
            />
            <span className={requireRealIV ? "text-blue-300" : ""}>
              Real IV only
            </span>
          </label>
        </div>
      </div>

      {/* ── States ──────────────────────────────────────────────────── */}
      {loading && <PageSkeleton />}

      {error && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-4">
          <p className="text-xs font-semibold text-red-400 mb-1">Failed to load backtest</p>
          <p className="font-mono text-[10px] text-red-300/60">{error}</p>
        </div>
      )}

      {result && bestVar && (
        <div className="space-y-4">

          {/* Real-IV filter summary */}
          <RealIVFilterBanner filter={result.real_iv_filter} />

          {/* Global sample-size warning when real-IV mode produces tiny samples */}
          {requireRealIV && result.variations.every((v) => v.n_trades < 20) && (
            <div className="flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-xs text-amber-400">
              <span className="mt-0.5 shrink-0 text-base leading-none">⚠</span>
              <span>
                <strong>Small sample warning:</strong> All variations have fewer than 20 trades
                in the real-IV window. Results are not statistically reliable. Consider widening
                the date range or toggling off &ldquo;Real IV only&rdquo; for exploratory analysis.
              </span>
            </div>
          )}

          {/* IV data-quality gate */}
          <IVQualityBadge gate={result.iv_quality} />

          {/* Best variation banner */}
          <BestBanner v={bestVar} />

          {/* Variation legend */}
          <VariationLegend variations={result.variations} />

          {/* Comparison table */}
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-2">
              Metric Comparison — All Tickers Pooled
            </p>
            <ComparisonTable
              variations={result.variations}
              bestId={result.best_variation_id}
            />
          </div>

          {/* Equity curves */}
          <EquityChart variations={result.variations} />

          {/* Per-ticker breakdown */}
          <TickerBreakdown
            variations={result.variations}
            tickers={result.tickers}
            bestId={result.best_variation_id}
          />

          {/* Regime breakdown */}
          <RegimeBreakdown
            variations={result.variations}
            bestId={result.best_variation_id}
          />

          <p className="text-center text-[10px] text-slate-700 pt-2">
            Phase 2 · synthetic prices · hardcoded IV surface · Phase 3 will wire live Polygon + ORATS data
          </p>
        </div>
      )}
    </main>
  );
}
