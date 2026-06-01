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
import { getMacroSweep } from "@/lib/api";
import type { MacroSweepResult, MacroVariation, SweepTickerStats, RealIVFilter } from "@/lib/api";
import IVQualityBadge from "@/components/dashboard/IVQualityBadge";

// ---------------------------------------------------------------------------
// Colour palette  (7 variations)
// ---------------------------------------------------------------------------

const VAR_COLORS: Record<number, string> = {
  1: "#94a3b8",   // slate-400   — V2 baseline
  2: "#60a5fa",   // blue-400    — + VIX level
  3: "#34d399",   // emerald-400 — + VIX contango
  4: "#fbbf24",   // amber-400   — + breadth
  5: "#f97316",   // orange-400  — + yield stress
  6: "#ef4444",   // red-400     — + oil shock
  7: "#a78bfa",   // violet-400  — all macro filters
};

const VIX_BUCKET_META: Record<string, { label: string; color: string; desc: string }> = {
  calm:     { label: "Calm  (< 18)",    color: "text-green-400",  desc: "Low-vol complacency" },
  normal:   { label: "Normal (18–25)",  color: "text-blue-400",   desc: "Healthy vol regime"  },
  elevated: { label: "Elevated (25–32)",color: "text-orange-400", desc: "Stress / risk-off"   },
  spike:    { label: "Spike  (> 32)",   color: "text-red-400",    desc: "Acute vol shock"     },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pct(v: number | null | undefined, dec = 1, forceSign = false): string {
  if (v == null || !isFinite(v)) return "—";
  const s = forceSign && v > 0 ? "+" : "";
  return `${s}${(v * 100).toFixed(dec)}%`;
}

function num(v: number | null | undefined, dec = 2): string {
  if (v == null || !isFinite(v)) return "—";
  return v.toFixed(dec);
}

type Dir = "high" | "low";

function bestIdx(vals: (number | null)[], dir: Dir): number {
  let best = -1, bv = dir === "high" ? -Infinity : Infinity;
  vals.forEach((v, i) => {
    if (v == null || !isFinite(v)) return;
    if (dir === "high" && v > bv) { bv = v; best = i; }
    if (dir === "low"  && v < bv) { bv = v; best = i; }
  });
  return best;
}

function worstIdx(vals: (number | null)[], dir: Dir): number {
  let worst = -1, wv = dir === "high" ? Infinity : -Infinity;
  vals.forEach((v, i) => {
    if (v == null || !isFinite(v)) return;
    if (dir === "high" && v < wv) { wv = v; worst = i; }
    if (dir === "low"  && v > wv) { wv = v; worst = i; }
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
// Sample-size warning chip (per variation when n_trades < 20)
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
      <Skeleton className="h-8 w-72" />
      <Skeleton className="h-72 w-full" />
      <Skeleton className="h-52 w-full" />
      <Skeleton className="h-52 w-full" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Best-variation banner
// ---------------------------------------------------------------------------

function BestBanner({ v }: { v: MacroVariation }) {
  return (
    <div className="rounded-xl border border-violet-500/30 bg-violet-500/8 px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-2">
      <span className="shrink-0 rounded-full bg-violet-500/20 border border-violet-400/30 px-2.5 py-0.5 text-[10px] font-bold text-violet-300 uppercase tracking-widest">
        ★ Best Variation
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-white">
          {v.name}
          {v.sample_size_warning && <SampleSizeWarning />}
        </p>
        <p className="text-[11px] text-slate-400 leading-relaxed">{v.description}</p>
      </div>
      <div className="flex gap-4 shrink-0 text-[11px]">
        {[
          { label: "Sharpe",   val: num(v.sharpe)  },
          { label: "Win%",     val: pct(v.win_rate, 0) },
          { label: "Cap Rate", val: pct(v.premium_capture_rate, 1, true) },
          { label: "Trades",   val: String(v.n_trades) },
        ].map(({ label, val }) => (
          <div key={label} className="text-center">
            <p className="text-[9px] text-slate-500 uppercase tracking-widest">{label}</p>
            <p className="font-bold text-violet-300">{val}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter legend
// ---------------------------------------------------------------------------

function FilterLegend({ variations }: { variations: MacroVariation[] }) {
  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-3">
        Variation Rules
      </p>
      <div className="space-y-2">
        {variations.map((v) => (
          <div key={v.id} className="flex items-start gap-3">
            <span
              className="mt-0.5 shrink-0 w-2.5 h-2.5 rounded-full"
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
        All variations: SELL_PREMIUM · 10-day hold · non-overlapping per ticker · mock data
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Comparison table
// ---------------------------------------------------------------------------

interface MetricDef {
  label: string;
  rawVal: (v: MacroVariation) => number | null;
  fmt:    (v: MacroVariation) => string;
  dir:    Dir;
}

const METRICS: MetricDef[] = [
  {
    label: "Trades", dir: "high",
    rawVal: (v) => v.n_trades,
    fmt:    (v) => String(v.n_trades),
  },
  {
    label: "Win Rate", dir: "high",
    rawVal: (v) => v.win_rate,
    fmt:    (v) => pct(v.win_rate, 1),
  },
  {
    label: "Premium Capture", dir: "high",
    rawVal: (v) => v.premium_capture_rate,
    fmt:    (v) => pct(v.premium_capture_rate, 2, true),
  },
  {
    label: "Avg Return / trade", dir: "high",
    rawVal: (v) => v.avg_return,
    fmt:    (v) => pct(v.avg_return, 2, true),
  },
  {
    label: "Median Return", dir: "high",
    rawVal: (v) => v.median_return,
    fmt:    (v) => pct(v.median_return, 2, true),
  },
  {
    label: "Sharpe Ratio", dir: "high",
    rawVal: (v) => v.sharpe,
    fmt:    (v) => num(v.sharpe),
  },
  {
    label: "Sortino Ratio", dir: "high",
    rawVal: (v) => v.sortino,
    fmt:    (v) => num(v.sortino),
  },
  {
    label: "Profit Factor", dir: "high",
    rawVal: (v) => v.profit_factor,
    fmt:    (v) => num(v.profit_factor),
  },
  {
    label: "Expectancy", dir: "high",
    rawVal: (v) => v.expectancy,
    fmt:    (v) => pct(v.expectancy, 2, true),
  },
  {
    label: "Max Drawdown", dir: "low",
    rawVal: (v) => v.max_drawdown,
    fmt:    (v) => pct(v.max_drawdown, 1),
  },
  {
    label: "Best Trade", dir: "high",
    rawVal: (v) => v.best_trade?.pnl ?? null,
    fmt:    (v) => v.best_trade ? pct(v.best_trade.pnl, 1, true) : "—",
  },
  {
    label: "Worst Trade", dir: "low",
    rawVal: (v) => v.worst_trade?.pnl ?? null,
    fmt:    (v) => v.worst_trade ? pct(v.worst_trade.pnl, 1, true) : "—",
  },
];

function ComparisonTable({
  variations,
  bestId,
}: {
  variations: MacroVariation[];
  bestId: number;
}) {
  return (
    <div className="rounded-xl border border-border bg-surface-2 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[680px]">
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
                      "py-3 px-2 text-center text-[10px] font-semibold",
                      isBest ? "bg-violet-500/10 text-violet-300" : "text-slate-400"
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
                      <span className="leading-tight text-center whitespace-nowrap">{v.short}</span>
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
            {METRICS.map((m, ri) => {
              const rawVals = variations.map((v) => m.rawVal(v));
              const bi = bestIdx(rawVals, m.dir);
              const wi = worstIdx(rawVals, m.dir);
              return (
                <tr
                  key={m.label}
                  className={clsx(
                    "border-b border-border/40",
                    ri % 2 === 0 ? "bg-transparent" : "bg-surface-3/20"
                  )}
                >
                  <td className="py-2.5 pl-4 pr-3 text-[10px] text-slate-500 font-medium whitespace-nowrap">
                    {m.label}
                  </td>
                  {variations.map((v, vi) => {
                    const isBest = v.id === bestId;
                    const isTop  = vi === bi;
                    const isBot  = vi === wi && bi !== wi;
                    return (
                      <td
                        key={v.id}
                        className={clsx(
                          "py-2.5 px-2 text-center text-xs font-semibold tabular-nums",
                          isBest && "bg-violet-500/5",
                          isTop  && "text-green-400",
                          isBot && !isTop && "text-red-400/70",
                          !isTop && !isBot && "text-slate-300"
                        )}
                      >
                        {m.fmt(v)}
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
// Equity curves
// ---------------------------------------------------------------------------

function EquityChart({ variations }: { variations: MacroVariation[] }) {
  const maxN = Math.max(...variations.map((v) => v.equity_points.length), 1);

  const data = Array.from({ length: maxN }, (_, i) => {
    const pt: Record<string, number | null> = { trade_n: i + 1 };
    variations.forEach((v) => {
      pt[`v${v.id}`] = v.equity_points[i]?.equity ?? null;
    });
    return pt;
  });

  const allEq = variations.flatMap((v) => v.equity_points.map((p) => p.equity));
  const minEq = allEq.length ? Math.min(...allEq) : 0.7;
  const maxEq = allEq.length ? Math.max(...allEq) : 1.3;
  const pad   = Math.max((maxEq - minEq) * 0.15, 0.05);

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-4">
        Equity Curves — Sequential Trade Number
      </p>
      <ResponsiveContainer width="100%" height={210}>
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
            width={44}
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
              return [`${((val - 1) * 100).toFixed(2)}%`, variations.find((v) => v.id === id)?.short ?? name];
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
              strokeWidth={v.id === 7 ? 2 : 1.5}
              dot={false}
              connectNulls={false}
              activeDot={{ r: 3, strokeWidth: 0 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// VIX spike breakdown  (key output — performance during VIX > 25 / > 32)
// ---------------------------------------------------------------------------

function VIXSpikeBreakdown({
  variations,
  bestId,
}: {
  variations: MacroVariation[];
  bestId: number;
}) {
  const [activeId, setActiveId] = useState(bestId);
  const active = variations.find((v) => v.id === activeId)!;

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
            Performance by VIX Environment at Entry
          </p>
          <p className="text-[10px] text-slate-600 mt-0.5">
            Calm &lt; 18  ·  Normal 18–25  ·  Elevated 25–32  ·  Spike &gt; 32
          </p>
        </div>
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
                  ? {
                      background: VAR_COLORS[v.id] + "33",
                      color: VAR_COLORS[v.id],
                      border: `1px solid ${VAR_COLORS[v.id]}44`,
                    }
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
              {["VIX Bucket", "Description", "n", "Win%", "Avg P&L", "Prem Capture"].map((h) => (
                <th
                  key={h}
                  className={clsx(
                    "pb-2 text-[9px] font-semibold uppercase tracking-widest text-slate-600",
                    h === "VIX Bucket" || h === "Description" ? "text-left pr-3" : "text-right px-3"
                  )}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(VIX_BUCKET_META).map(([key, meta]) => {
              const row = active.by_vix_regime[key];
              const hasData = row && row.n > 0;

              // Premium capture within this bucket: avg_pnl is (IV-RV)/IV per trade,
              // which is the per-trade premium capture rate already
              const capRate = hasData && row.avg_pnl != null ? row.avg_pnl : null;

              return (
                <tr key={key} className="border-b border-border/20 hover:bg-surface-3/20">
                  <td className={clsx("py-2.5 pr-3 font-semibold whitespace-nowrap", meta.color)}>
                    {meta.label}
                  </td>
                  <td className="py-2.5 pr-3 text-slate-600 text-[10px]">
                    {meta.desc}
                  </td>
                  <td className="py-2.5 px-3 text-right text-slate-400 tabular-nums">
                    {hasData ? row.n : <span className="text-slate-700">0</span>}
                  </td>
                  <td className="py-2.5 px-3 text-right tabular-nums">
                    {hasData && row.win_rate != null ? (
                      <span className={row.win_rate >= 0.5 ? "text-green-400 font-medium" : "text-slate-400"}>
                        {(row.win_rate * 100).toFixed(0)}%
                      </span>
                    ) : <span className="text-slate-700">—</span>}
                  </td>
                  <td className={clsx(
                    "py-2.5 px-3 text-right font-medium tabular-nums",
                    !hasData || row.avg_pnl == null
                      ? "text-slate-700"
                      : row.avg_pnl >= 0
                        ? "text-green-400"
                        : "text-red-400"
                  )}>
                    {hasData && row.avg_pnl != null ? pct(row.avg_pnl, 2, true) : "—"}
                  </td>
                  <td className={clsx(
                    "py-2.5 pl-3 text-right font-medium tabular-nums",
                    capRate == null ? "text-slate-700"
                      : capRate >= 0 ? "text-green-400" : "text-red-400"
                  )}>
                    {capRate != null ? pct(capRate, 2, true) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Spike callout */}
      {(() => {
        const spike = active.by_vix_regime["spike"];
        if (!spike || spike.n === 0) {
          return (
            <p className="mt-3 text-[10px] text-slate-700 italic">
              No trades entered during VIX spike regime (&gt; 32) for this variation.
            </p>
          );
        }
        const spikePnl = spike.avg_pnl ?? 0;
        const baseline = variations.find((v) => v.id === 1)?.by_vix_regime["spike"];
        const baselinePnl = baseline?.avg_pnl ?? null;
        return (
          <div className={clsx(
            "mt-3 rounded-lg border px-3 py-2 text-[10px] leading-relaxed",
            spikePnl >= 0
              ? "border-green-500/20 bg-green-500/5 text-green-400"
              : "border-red-500/20 bg-red-500/5 text-red-400"
          )}>
            <span className="font-semibold">VIX Spike regime:</span>
            {" "}{spike.n} trade{spike.n !== 1 ? "s" : ""} · {(spike.win_rate ?? 0) * 100 | 0}% win rate · {pct(spikePnl, 2, true)} avg
            {baselinePnl != null && activeId !== 1 && (
              <span className="text-slate-500">
                {" "}(baseline: {pct(baselinePnl, 2, true)})
              </span>
            )}
          </div>
        );
      })()}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-ticker breakdown
// ---------------------------------------------------------------------------

function TickerBreakdown({
  variations,
  tickers,
  bestId,
}: {
  variations: MacroVariation[];
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
                  ? {
                      background: VAR_COLORS[v.id] + "33",
                      color: VAR_COLORS[v.id],
                      border: `1px solid ${VAR_COLORS[v.id]}44`,
                    }
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
              {["Ticker", "Trades", "Win Rate", "Avg Return", "Median Return"].map((h) => (
                <th
                  key={h}
                  className={clsx(
                    "pb-2 text-[9px] font-semibold uppercase tracking-widest text-slate-600",
                    h === "Ticker" ? "text-left pr-3" : "text-right px-2"
                  )}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tickers.map((ticker) => {
              const row: SweepTickerStats | undefined = active.by_ticker[ticker];
              const has = row && row.n_trades > 0;
              return (
                <tr key={ticker} className="border-b border-border/20 hover:bg-surface-3/20">
                  <td className="py-2 pr-3 font-semibold text-white">{ticker}</td>
                  <td className="py-2 px-2 text-right text-slate-400">
                    {has ? row.n_trades : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="py-2 px-2 text-right">
                    {has && row.win_rate != null ? (
                      <span className={clsx("font-medium", row.win_rate >= 0.5 ? "text-green-400" : "text-red-400")}>
                        {(row.win_rate * 100).toFixed(0)}%
                      </span>
                    ) : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="py-2 px-2 text-right">
                    {has && row.avg_return != null ? (
                      <span className={clsx("font-medium tabular-nums", row.avg_return >= 0 ? "text-green-400" : "text-red-400")}>
                        {pct(row.avg_return, 2, true)}
                      </span>
                    ) : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="py-2 pl-2 text-right">
                    {has && row.median_return != null ? (
                      <span className={clsx("font-medium tabular-nums", row.median_return >= 0 ? "text-green-400" : "text-red-400")}>
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
// Macro filter summary cards  (quick-read at a glance)
// ---------------------------------------------------------------------------

function FilterSummaryCards({
  variations,
  bestId,
}: {
  variations: MacroVariation[];
  bestId: number;
}) {
  const baseline = variations.find((v) => v.id === 1)!;
  const independent = variations.slice(1, 6);     // V2–V6 (individual filters)
  const combined  = variations.find((v) => v.id === 7)!;

  function delta(v: MacroVariation, key: "sharpe" | "win_rate" | "max_drawdown") {
    const bv = baseline[key];
    const vv = v[key];
    if (bv == null || vv == null) return null;
    return vv - bv;
  }

  return (
    <div className="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
      {independent.map((v) => {
        const dSharpe = delta(v, "sharpe");
        const dMDD    = delta(v, "max_drawdown");   // lower MDD = better = positive delta
        const isBest  = v.id === bestId;
        return (
          <div
            key={v.id}
            className={clsx(
              "rounded-xl border p-3 flex flex-col gap-1.5",
              isBest
                ? "border-violet-500/40 bg-violet-500/8"
                : "border-border bg-surface-2"
            )}
          >
            <div className="flex items-center gap-1.5">
              <span
                className="w-2 h-2 rounded-full shrink-0"
                style={{ background: VAR_COLORS[v.id] }}
              />
              <span className={clsx("text-[10px] font-semibold", isBest ? "text-violet-300" : "text-slate-300")}>
                {v.short}
              </span>
            </div>
            <div className="space-y-0.5">
              <p className="text-[9px] text-slate-600 uppercase tracking-widest">Sharpe vs base</p>
              <p className={clsx(
                "text-xs font-bold tabular-nums",
                dSharpe == null ? "text-slate-600"
                  : dSharpe > 0 ? "text-green-400"
                  : dSharpe < 0 ? "text-red-400"
                  : "text-slate-400"
              )}>
                {dSharpe == null ? "—"
                  : dSharpe > 0 ? `+${dSharpe.toFixed(2)}`
                  : dSharpe.toFixed(2)}
              </p>
            </div>
            <div className="space-y-0.5">
              <p className="text-[9px] text-slate-600 uppercase tracking-widest">MDD vs base</p>
              <p className={clsx(
                "text-xs font-bold tabular-nums",
                dMDD == null ? "text-slate-600"
                  : dMDD > 0 ? "text-green-400"   // max_drawdown is negative; less negative = better = delta > 0
                  : dMDD < 0 ? "text-red-400"
                  : "text-slate-400"
              )}>
                {dMDD == null ? "—"
                  : dMDD > 0 ? `+${(dMDD * 100).toFixed(1)}%`
                  : `${(dMDD * 100).toFixed(1)}%`}
              </p>
            </div>
            <div className="space-y-0.5">
              <p className="text-[9px] text-slate-600 uppercase tracking-widest">Trades</p>
              <p className="text-xs text-slate-400">
                {v.n_trades}
                {v.n_trades < baseline.n_trades && (
                  <span className="text-slate-600 ml-0.5">
                    (−{baseline.n_trades - v.n_trades})
                  </span>
                )}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function MacroSweepPage() {
  const [result, setResult] = useState<MacroSweepResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState<string | null>(null);
  const [requireRealIV, setRequireRealIV] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getMacroSweep(undefined, requireRealIV)
      .then((d) => setResult(d))
      .catch((e) => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  }, [requireRealIV]);

  const bestVar = result?.variations.find((v) => v.id === result.best_variation_id) ?? null;

  return (
    <main className="min-h-screen bg-surface px-4 py-5 md:px-6 md:py-7">

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <Link href="/" className="text-[10px] text-slate-500 hover:text-slate-300 transition-colors">
              ← Dashboard
            </Link>
            <span className="text-slate-700 text-[10px]">/</span>
            <Link href="/backtest" className="text-[10px] text-slate-500 hover:text-slate-300 transition-colors">
              Backtest
            </Link>
            <span className="text-slate-700 text-[10px]">/</span>
            <span className="text-[10px] text-slate-400">Macro Filters</span>
          </div>
          <h1 className="text-xl font-bold tracking-tight text-white">
            Macro Filter Sweep
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            {result
              ? `V2 baseline + 5 orthogonal macro filters · ${result.tickers.join(" · ")} · ${result.data_source} data`
              : "Testing VIX level, VIX term structure, breadth, yield stress, and oil shock filters…"}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <Link
            href="/backtest/grades"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-[10px] font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
          >
            Signal Grades →
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

      {/* ── States ──────────────────────────────────────────────────────── */}
      {loading && <PageSkeleton />}

      {error && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-4">
          <p className="text-xs font-semibold text-red-400 mb-1">Failed to load macro sweep</p>
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

          {/* Best banner */}
          <BestBanner v={bestVar} />

          {/* Quick-compare delta cards */}
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-2">
              Each Filter vs V2 Baseline — Sharpe & Drawdown Delta
            </p>
            <FilterSummaryCards
              variations={result.variations}
              bestId={result.best_variation_id}
            />
          </div>

          {/* Filter legend */}
          <FilterLegend variations={result.variations} />

          {/* Comparison table */}
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-2">
              Full Metric Comparison — All Tickers Pooled
            </p>
            <ComparisonTable
              variations={result.variations}
              bestId={result.best_variation_id}
            />
          </div>

          {/* Equity curves */}
          <EquityChart variations={result.variations} />

          {/* VIX environment breakdown — the key "during spikes" output */}
          <VIXSpikeBreakdown
            variations={result.variations}
            bestId={result.best_variation_id}
          />

          {/* Per-ticker breakdown */}
          <TickerBreakdown
            variations={result.variations}
            tickers={result.tickers}
            bestId={result.best_variation_id}
          />

          <p className="text-center text-[10px] text-slate-700 pt-2">
            Phase 2 · synthetic prices · mock macro series · Phase 3 will wire FRED (VIX, VXMT, 10Y, WTI) + breadth data
          </p>
        </div>
      )}
    </main>
  );
}
