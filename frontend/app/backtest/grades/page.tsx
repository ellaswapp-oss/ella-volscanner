"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { clsx } from "clsx";
import {
  LineChart, Line, BarChart, Bar, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Legend,
} from "recharts";
import { getSignalGrades } from "@/lib/api";
import type {
  SignalGradesResult,
  GradePortfolio,
  ScoreComponent,
  SweepTickerStats,
  RealIVFilter,
} from "@/lib/api";
import IVQualityBadge from "@/components/dashboard/IVQualityBadge";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PORTFOLIO_COLORS: Record<number, string> = {
  1: "#34d399",   // emerald — A Only
  2: "#60a5fa",   // blue    — A+B
  3: "#fbbf24",   // amber   — A+B+C
  4: "#94a3b8",   // slate   — V2 Base
};

const GRADE_COLORS: Record<string, string> = {
  A: "#34d399",   // emerald
  B: "#60a5fa",   // blue
  C: "#fbbf24",   // amber
  F: "#ef4444",   // red
};

const VIX_BUCKET_META: Record<string, { label: string; color: string }> = {
  calm:     { label: "Calm  (< 18)",     color: "text-green-400"  },
  normal:   { label: "Normal (18–25)",   color: "text-blue-400"   },
  elevated: { label: "Elevated (25–32)", color: "text-orange-400" },
  spike:    { label: "Spike  (> 32)",    color: "text-red-400"    },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pct(v: number | null | undefined, dec = 1, sign = false): string {
  if (v == null || !isFinite(v)) return "—";
  const s = sign && v > 0 ? "+" : "";
  return `${s}${(v * 100).toFixed(dec)}%`;
}

function num(v: number | null | undefined, dec = 2): string {
  if (v == null || !isFinite(v)) return "—";
  return v.toFixed(dec);
}

type Dir = "high" | "low";

function bestIdx(vals: (number | null)[], dir: Dir) {
  let bi = -1, bv = dir === "high" ? -Infinity : Infinity;
  vals.forEach((v, i) => {
    if (v == null || !isFinite(v)) return;
    if (dir === "high" && v > bv) { bv = v; bi = i; }
    if (dir === "low"  && v < bv) { bv = v; bi = i; }
  });
  return bi;
}
function worstIdx(vals: (number | null)[], dir: Dir) {
  let wi = -1, wv = dir === "high" ? Infinity : -Infinity;
  vals.forEach((v, i) => {
    if (v == null || !isFinite(v)) return;
    if (dir === "high" && v < wv) { wv = v; wi = i; }
    if (dir === "low"  && v > wv) { wv = v; wi = i; }
  });
  return wi;
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
// Sample-size warning chip
// ---------------------------------------------------------------------------

function SampleSizeWarning() {
  return (
    <span className="ml-1.5 inline-flex items-center gap-0.5 rounded bg-amber-500/15 border border-amber-500/30 px-1.5 py-0.5 text-[9px] font-semibold text-amber-400">
      ⚠ &lt;20 trades
    </span>
  );
}

// ---------------------------------------------------------------------------
// Skeleton
// ---------------------------------------------------------------------------

function Sk({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded bg-surface-3", className)} />;
}
function PageSkeleton() {
  return (
    <div className="space-y-4">
      <Sk className="h-14 w-full" />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4"><Sk className="h-28" /><Sk className="h-28" /><Sk className="h-28" /><Sk className="h-28" /></div>
      <Sk className="h-56 w-full" />
      <Sk className="h-52 w-full" />
      <Sk className="h-48 w-full" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scoring rubric card
// ---------------------------------------------------------------------------

function ScoringRubric({ components }: { components: ScoreComponent[] }) {
  const pos = components.filter((c) => c.type === "positive");
  const neg = components.filter((c) => c.type === "negative");
  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-3">
        Scoring Rubric  ·  base = 0
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-0.5">
        {/* Positive */}
        <div>
          <p className="text-[9px] font-semibold uppercase tracking-widest text-slate-600 mb-1.5">
            Positive Factors
          </p>
          {pos.map((c) => (
            <div key={c.key} className="flex items-center justify-between py-1 border-b border-border/30 text-[11px]">
              <span className="text-slate-400">{c.label}</span>
              <span className="font-bold text-green-400 tabular-nums">+{c.value}</span>
            </div>
          ))}
          <div className="flex justify-between py-1.5 text-[11px]">
            <span className="text-slate-500">Max possible</span>
            <span className="font-bold text-green-400 tabular-nums">
              +{pos.reduce((s, c) => s + c.value, 0)}
            </span>
          </div>
        </div>
        {/* Negative */}
        <div>
          <p className="text-[9px] font-semibold uppercase tracking-widest text-slate-600 mb-1.5">
            Negative Factors
          </p>
          {neg.map((c) => (
            <div key={c.key} className="flex items-center justify-between py-1 border-b border-border/30 text-[11px]">
              <span className="text-slate-400">{c.label}</span>
              <span className="font-bold text-red-400 tabular-nums">{c.value}</span>
            </div>
          ))}
          <div className="mt-3 space-y-1">
            {[
              { grade: "A", range: "≥ 80", desc: "All macro aligned" },
              { grade: "B", range: "65–79", desc: "Contango confirmed" },
              { grade: "C", range: "50–64", desc: "Base V2, minor stress" },
              { grade: "F", range: "< 50", desc: "Skip — macro adverse" },
            ].map(({ grade, range, desc }) => (
              <div key={grade} className="flex items-center gap-2 text-[11px]">
                <span
                  className="shrink-0 w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold"
                  style={{ background: GRADE_COLORS[grade] + "22", color: GRADE_COLORS[grade] }}
                >
                  {grade}
                </span>
                <span className="font-mono tabular-nums text-slate-400 w-12">{range}</span>
                <span className="text-slate-600">{desc}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Score distribution bar chart
// ---------------------------------------------------------------------------

function ScoreHistogram({ result }: { result: SignalGradesResult }) {
  const data = result.score_distribution.map((b) => ({
    label:  `${b.score_min}`,
    count:  b.count,
    grade:  b.grade,
    range:  `${b.score_min}–${b.score_max}`,
  }));

  if (!data.length) return null;

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
          Score Distribution  ·  V2 Baseline Trades
        </p>
        <p className="text-[10px] text-slate-600">
          {result.score_distribution.reduce((s, b) => s + b.count, 0)} trades total
        </p>
      </div>
      <p className="text-[10px] text-slate-600 mb-3">
        5-point buckets — each bucket falls within one grade band
      </p>
      <ResponsiveContainer width="100%" height={120}>
        <BarChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.08)" vertical={false} />
          <XAxis dataKey="label" tick={{ fill: "#475569", fontSize: 9 }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fill: "#475569", fontSize: 9 }} axisLine={false} tickLine={false} allowDecimals={false} />
          <Tooltip
            contentStyle={{ background: "#1e293b", border: "1px solid rgba(100,116,139,0.25)", borderRadius: "6px", padding: "6px 10px", fontSize: "11px" }}
            labelStyle={{ color: "#94a3b8" }}
            labelFormatter={(v: string) => `Score ${v}–${parseInt(v)+4}`}
            formatter={(val: number, _: string, entry) => [
              `${val} trade${val !== 1 ? "s" : ""}`,
              `Grade ${(entry.payload as { grade: string }).grade}`,
            ]}
          />
          <Bar dataKey="count" radius={[2, 2, 0, 0]}>
            {data.map((d, i) => (
              <Cell key={i} fill={GRADE_COLORS[d.grade]} opacity={0.8} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      {/* Grade colour legend */}
      <div className="mt-2 flex gap-3 flex-wrap">
        {Object.entries(GRADE_COLORS).map(([g, col]) => (
          <div key={g} className="flex items-center gap-1 text-[10px]">
            <span className="w-2 h-2 rounded-sm" style={{ background: col }} />
            <span style={{ color: col }}>Grade {g}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Grade stats table  (did higher scores predict better outcomes?)
// ---------------------------------------------------------------------------

function GradeStatsTable({ result }: { result: SignalGradesResult }) {
  const grades = ["A", "B", "C", "F"] as const;

  const ROWS: { label: string; key: keyof typeof result.grade_stats["A"]; fmt: (v: number | null | undefined) => string; dir: Dir }[] = [
    { label: "Trades",          key: "n",                    fmt: (v) => v != null ? String(v) : "—",              dir: "high" },
    { label: "Win Rate",        key: "win_rate",             fmt: (v) => pct(v, 1),                                dir: "high" },
    { label: "Premium Capture", key: "premium_capture_rate", fmt: (v) => pct(v, 2, true),                          dir: "high" },
    { label: "Avg Return",      key: "avg_return",           fmt: (v) => pct(v, 2, true),                          dir: "high" },
    { label: "Median Return",   key: "median_return",        fmt: (v) => pct(v, 2, true),                          dir: "high" },
    { label: "Profit Factor",   key: "profit_factor",        fmt: (v) => num(v),                                   dir: "high" },
    { label: "Max Drawdown",    key: "max_drawdown",         fmt: (v) => pct(v, 1),                                dir: "low"  },
    { label: "Sharpe",          key: "sharpe",               fmt: (v) => num(v),                                   dir: "high" },
    { label: "Expectancy",      key: "expectancy",           fmt: (v) => pct(v, 2, true),                          dir: "high" },
  ];

  return (
    <div className="rounded-xl border border-border bg-surface-2 overflow-hidden">
      <div className="px-4 pt-4 pb-2">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
          Grade Performance  ·  Retrospective from V2 Baseline
        </p>
        <p className="text-[10px] text-slate-600 mt-0.5">
          Same V2 universe — do higher scores predict better outcomes?
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[400px]">
          <thead>
            <tr className="border-b border-border">
              <th className="py-2.5 pl-4 pr-3 text-left text-[9px] font-semibold uppercase tracking-widest text-slate-600 w-36">
                Metric
              </th>
              {grades.map((g) => (
                <th key={g} className="py-2.5 px-3 text-center">
                  <div className="flex flex-col items-center gap-0.5">
                    <span
                      className="w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold"
                      style={{ background: GRADE_COLORS[g] + "22", color: GRADE_COLORS[g] }}
                    >
                      {g}
                    </span>
                    <span className="text-[9px] text-slate-600">
                      {result.grade_meta[g]?.min === -99
                        ? "< 50"
                        : `≥ ${result.grade_meta[g]?.min}`}
                    </span>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ROWS.map((row, ri) => {
              const vals = grades.map((g) => {
                const s = result.grade_stats[g];
                const raw = s[row.key];
                return typeof raw === "number" ? raw : null;
              });
              const bi = bestIdx(vals, row.dir);
              const wi = worstIdx(vals, row.dir);
              return (
                <tr key={row.key as string} className={clsx("border-b border-border/40", ri % 2 !== 0 && "bg-surface-3/20")}>
                  <td className="py-2.5 pl-4 pr-3 text-[10px] text-slate-500 font-medium whitespace-nowrap">
                    {row.label}
                  </td>
                  {grades.map((g, gi) => {
                    const s   = result.grade_stats[g];
                    const raw = s[row.key];
                    const val = typeof raw === "number" ? raw : null;
                    const isTop = gi === bi;
                    const isBot = gi === wi && bi !== wi;
                    return (
                      <td
                        key={g}
                        className={clsx(
                          "py-2.5 px-3 text-center text-xs font-semibold tabular-nums",
                          isTop && "text-green-400",
                          isBot && !isTop && "text-red-400/70",
                          !isTop && !isBot && "text-slate-300"
                        )}
                      >
                        {row.fmt(val)}
                        {isTop && val != null && <span className="ml-0.5 text-[8px] text-green-500"> ▲</span>}
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
// Premium capture by grade  (bar chart)
// ---------------------------------------------------------------------------

function PremiumCaptureChart({ result }: { result: SignalGradesResult }) {
  const data = (["A", "B", "C", "F"] as const).map((g) => {
    const s = result.grade_stats[g];
    return {
      grade: g,
      pcr:   s.premium_capture_rate != null ? Math.round(s.premium_capture_rate * 1000) / 10 : null,
      n:     s.n,
    };
  }).filter((d) => d.n > 0);

  if (!data.length) return null;

  const minVal = Math.min(...data.map((d) => d.pcr ?? 0));
  const maxVal = Math.max(...data.map((d) => d.pcr ?? 0));
  const pad    = Math.max(Math.abs(maxVal - minVal) * 0.25, 2);

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-1">
        Premium Capture Rate by Grade
      </p>
      <p className="text-[10px] text-slate-600 mb-3">
        sum(IV−RV) / sum(IV) — value-weighted by IV at entry
      </p>
      <ResponsiveContainer width="100%" height={120}>
        <BarChart data={data} margin={{ top: 4, right: 12, left: -10, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.08)" vertical={false} />
          <XAxis dataKey="grade" tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis
            domain={[minVal - pad, maxVal + pad]}
            tick={{ fill: "#475569", fontSize: 9 }}
            axisLine={false} tickLine={false}
            tickFormatter={(v: number) => `${v.toFixed(1)}%`}
          />
          <Tooltip
            contentStyle={{ background: "#1e293b", border: "1px solid rgba(100,116,139,0.25)", borderRadius: "6px", padding: "6px 10px", fontSize: "11px" }}
            formatter={(val: number, _: string, entry) => [
              `${val.toFixed(1)}%`,
              `Grade ${(entry.payload as { grade: string }).grade} (n=${(entry.payload as { n: number }).n})`,
            ]}
          />
          <ReferenceLine y={0} stroke="rgba(100,116,139,0.4)" />
          <Bar dataKey="pcr" radius={[3, 3, 0, 0]}>
            {data.map((d, i) => (
              <Cell key={i} fill={GRADE_COLORS[d.grade]} opacity={d.pcr != null && d.pcr >= 0 ? 0.85 : 0.5} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Portfolio comparison table
// ---------------------------------------------------------------------------

interface MetricRow {
  label: string;
  rawVal: (p: GradePortfolio) => number | null;
  fmt:    (p: GradePortfolio) => string;
  dir:    Dir;
}

const METRIC_ROWS: MetricRow[] = [
  { label: "Trades",          rawVal: (p) => p.n_trades,             fmt: (p) => String(p.n_trades),             dir: "high" },
  { label: "Win Rate",        rawVal: (p) => p.win_rate,             fmt: (p) => pct(p.win_rate, 1),             dir: "high" },
  { label: "Premium Capture", rawVal: (p) => p.premium_capture_rate, fmt: (p) => pct(p.premium_capture_rate, 2, true), dir: "high" },
  { label: "Avg Return",      rawVal: (p) => p.avg_return,           fmt: (p) => pct(p.avg_return, 2, true),     dir: "high" },
  { label: "Median Return",   rawVal: (p) => p.median_return,        fmt: (p) => pct(p.median_return, 2, true),  dir: "high" },
  { label: "Sharpe",          rawVal: (p) => p.sharpe,               fmt: (p) => num(p.sharpe),                  dir: "high" },
  { label: "Sortino",         rawVal: (p) => p.sortino,              fmt: (p) => num(p.sortino),                 dir: "high" },
  { label: "Profit Factor",   rawVal: (p) => p.profit_factor,        fmt: (p) => num(p.profit_factor),           dir: "high" },
  { label: "Expectancy",      rawVal: (p) => p.expectancy,           fmt: (p) => pct(p.expectancy, 2, true),     dir: "high" },
  { label: "Max Drawdown",    rawVal: (p) => p.max_drawdown,         fmt: (p) => pct(p.max_drawdown, 1),         dir: "low"  },
  { label: "Best Trade",      rawVal: (p) => p.best_trade?.pnl ?? null, fmt: (p) => p.best_trade ? pct(p.best_trade.pnl, 1, true) : "—", dir: "high" },
  { label: "Worst Trade",     rawVal: (p) => p.worst_trade?.pnl ?? null, fmt: (p) => p.worst_trade ? pct(p.worst_trade.pnl, 1, true) : "—", dir: "low" },
];

function PortfolioTable({ portfolios }: { portfolios: GradePortfolio[] }) {
  // Best portfolio by Sharpe
  const bestSharpe = portfolios.reduce((best, p) => {
    if (p.sharpe != null && (best === null || p.sharpe > best.sharpe!)) return p;
    return best;
  }, null as GradePortfolio | null);
  const bestId = bestSharpe?.id ?? -1;

  return (
    <div className="rounded-xl border border-border bg-surface-2 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[480px]">
          <thead>
            <tr className="border-b border-border">
              <th className="py-3 pl-4 pr-3 text-left text-[9px] font-semibold uppercase tracking-widest text-slate-600 w-36">
                Metric
              </th>
              {portfolios.map((p) => (
                <th key={p.id} className={clsx("py-3 px-3 text-center text-[10px] font-semibold", p.id === bestId ? "text-white" : "text-slate-400")}>
                  <div className="flex flex-col items-center gap-1">
                    {p.id === bestId && (
                      <span className="text-[8px] uppercase tracking-widest font-bold" style={{ color: p.color }}>
                        ★ Best
                      </span>
                    )}
                    <span className="w-2.5 h-2.5 rounded-full" style={{ background: p.color }} />
                    <span className="leading-tight whitespace-nowrap">{p.short}</span>
                    <span className="text-[9px] font-mono text-slate-600">
                      {p.threshold > 0 ? `≥ ${p.threshold}` : "all"}
                    </span>
                    {p.sample_size_warning && (
                      <span className="text-[8px] text-amber-400 font-bold">⚠ {p.n_trades}t</span>
                    )}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {METRIC_ROWS.map((row, ri) => {
              const vals = portfolios.map((p) => row.rawVal(p));
              const bi = bestIdx(vals, row.dir);
              const wi = worstIdx(vals, row.dir);
              return (
                <tr key={row.label} className={clsx("border-b border-border/40", ri % 2 !== 0 && "bg-surface-3/20")}>
                  <td className="py-2.5 pl-4 pr-3 text-[10px] text-slate-500 font-medium whitespace-nowrap">
                    {row.label}
                  </td>
                  {portfolios.map((p, pi) => {
                    const isTop = pi === bi;
                    const isBot = pi === wi && bi !== wi;
                    const isBest = p.id === bestId;
                    return (
                      <td
                        key={p.id}
                        className={clsx(
                          "py-2.5 px-3 text-center text-xs font-semibold tabular-nums",
                          isBest && "bg-surface-3/30",
                          isTop  && "text-green-400",
                          isBot && !isTop && "text-red-400/70",
                          !isTop && !isBot && "text-slate-300"
                        )}
                      >
                        {row.fmt(p)}
                        {isTop && row.rawVal(p) != null && <span className="ml-0.5 text-[8px] text-green-500"> ▲</span>}
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

function EquityChart({ portfolios }: { portfolios: GradePortfolio[] }) {
  const maxN = Math.max(...portfolios.map((p) => p.equity_points.length), 1);
  const data = Array.from({ length: maxN }, (_, i) => {
    const pt: Record<string, number | null> = { trade_n: i + 1 };
    portfolios.forEach((p) => { pt[`p${p.id}`] = p.equity_points[i]?.equity ?? null; });
    return pt;
  });

  const allEq = portfolios.flatMap((p) => p.equity_points.map((e) => e.equity));
  const minEq = allEq.length ? Math.min(...allEq) : 0.7;
  const maxEq = allEq.length ? Math.max(...allEq) : 1.3;
  const pad   = Math.max((maxEq - minEq) * 0.15, 0.05);

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-4">
        Portfolio Equity Curves  ·  Sequential Trade Number
      </p>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data} margin={{ top: 4, right: 16, left: -14, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.10)" vertical={false} />
          <XAxis dataKey="trade_n" tick={{ fill: "#475569", fontSize: 9 }} axisLine={false} tickLine={false}
            label={{ value: "Trade #", position: "insideBottomRight", offset: -4, fontSize: 9, fill: "#475569" }} />
          <YAxis domain={[minEq - pad, maxEq + pad]} tick={{ fill: "#475569", fontSize: 9 }} axisLine={false} tickLine={false} width={42}
            tickFormatter={(v: number) => `${((v - 1) * 100).toFixed(0)}%`} />
          <Tooltip
            contentStyle={{ background: "#1e293b", border: "1px solid rgba(100,116,139,0.25)", borderRadius: "6px", padding: "8px 12px", fontSize: "11px" }}
            labelStyle={{ color: "#94a3b8" }}
            labelFormatter={(v: number) => `Trade ${v}`}
            formatter={(val: number, name: string) => {
              const id = parseInt(name.replace("p", ""));
              return [`${((val - 1) * 100).toFixed(2)}%`, portfolios.find((p) => p.id === id)?.short ?? name];
            }}
          />
          <Legend wrapperStyle={{ fontSize: "10px", paddingTop: "8px" }}
            formatter={(val: string) => {
              const id = parseInt(val.replace("p", ""));
              return portfolios.find((p) => p.id === id)?.short ?? val;
            }}
          />
          <ReferenceLine y={1} stroke="rgba(100,116,139,0.3)" />
          {portfolios.map((p) => (
            <Line key={p.id} type="stepAfter" dataKey={`p${p.id}`}
              stroke={PORTFOLIO_COLORS[p.id]} strokeWidth={p.id === 4 ? 1.5 : 2}
              dot={false} connectNulls={false} activeDot={{ r: 3, strokeWidth: 0 }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// VIX bucket breakdown  (tab by portfolio)
// ---------------------------------------------------------------------------

function VIXBucketBreakdown({ portfolios }: { portfolios: GradePortfolio[] }) {
  const [activeId, setActiveId] = useState(portfolios[0]?.id ?? 1);
  const active = portfolios.find((p) => p.id === activeId)!;

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
            Performance by VIX Environment
          </p>
          <p className="text-[10px] text-slate-600 mt-0.5">
            Calm &lt; 18  ·  Normal 18–25  ·  Elevated 25–32  ·  Spike &gt; 32
          </p>
        </div>
        <div className="flex gap-1 flex-wrap">
          {portfolios.map((p) => (
            <button key={p.id} onClick={() => setActiveId(p.id)}
              className={clsx("rounded px-2.5 py-1 text-[10px] font-medium transition-colors",
                activeId === p.id ? "text-white" : "text-slate-500 hover:text-slate-300 bg-surface-3")}
              style={activeId === p.id
                ? { background: PORTFOLIO_COLORS[p.id] + "33", color: PORTFOLIO_COLORS[p.id], border: `1px solid ${PORTFOLIO_COLORS[p.id]}44` }
                : {}}
            >
              {p.short}
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="border-b border-border/50">
              {["VIX Bucket", "n", "Win%", "Avg P&L", "Prem Capture", "Total"].map((h) => (
                <th key={h} className={clsx("pb-2 text-[9px] font-semibold uppercase tracking-widest text-slate-600",
                  h === "VIX Bucket" ? "text-left pr-3" : "text-right px-2")}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(VIX_BUCKET_META).map(([key, meta]) => {
              const row = active.by_vix_regime[key];
              const has = row && row.n > 0;
              const pcr = has && row.avg_pnl != null ? row.avg_pnl : null;
              return (
                <tr key={key} className="border-b border-border/20 hover:bg-surface-3/20">
                  <td className={clsx("py-2.5 pr-3 font-semibold whitespace-nowrap", meta.color)}>{meta.label}</td>
                  <td className="py-2.5 px-2 text-right text-slate-400">{has ? row.n : <span className="text-slate-700">0</span>}</td>
                  <td className="py-2.5 px-2 text-right tabular-nums">
                    {has && row.win_rate != null
                      ? <span className={row.win_rate >= 0.5 ? "text-green-400 font-medium" : "text-slate-400"}>{(row.win_rate * 100).toFixed(0)}%</span>
                      : <span className="text-slate-700">—</span>}
                  </td>
                  <td className={clsx("py-2.5 px-2 text-right font-medium tabular-nums",
                    !has || row.avg_pnl == null ? "text-slate-700"
                      : row.avg_pnl >= 0 ? "text-green-400" : "text-red-400")}>
                    {has && row.avg_pnl != null ? pct(row.avg_pnl, 2, true) : "—"}
                  </td>
                  <td className={clsx("py-2.5 px-2 text-right font-medium tabular-nums",
                    pcr == null ? "text-slate-700" : pcr >= 0 ? "text-green-400" : "text-red-400")}>
                    {pcr != null ? pct(pcr, 2, true) : "—"}
                  </td>
                  <td className={clsx("py-2.5 pl-2 text-right font-semibold tabular-nums",
                    !has ? "text-slate-700" : row.total >= 0 ? "text-green-400" : "text-red-400")}>
                    {has ? pct(row.total, 2, true) : "—"}
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
// Per-ticker breakdown  (tab by portfolio)
// ---------------------------------------------------------------------------

function TickerBreakdown({ portfolios, tickers }: { portfolios: GradePortfolio[]; tickers: string[] }) {
  const [activeId, setActiveId] = useState(portfolios[0]?.id ?? 1);
  const active = portfolios.find((p) => p.id === activeId)!;

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">Per-Ticker Breakdown</p>
        <div className="flex gap-1 flex-wrap">
          {portfolios.map((p) => (
            <button key={p.id} onClick={() => setActiveId(p.id)}
              className={clsx("rounded px-2.5 py-1 text-[10px] font-medium transition-colors",
                activeId === p.id ? "text-white" : "text-slate-500 hover:text-slate-300 bg-surface-3")}
              style={activeId === p.id
                ? { background: PORTFOLIO_COLORS[p.id] + "33", color: PORTFOLIO_COLORS[p.id], border: `1px solid ${PORTFOLIO_COLORS[p.id]}44` }
                : {}}
            >
              {p.short}
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[380px] text-[11px]">
          <thead>
            <tr className="border-b border-border/50">
              {["Ticker", "Trades", "Win Rate", "Avg Return", "Median"].map((h) => (
                <th key={h} className={clsx("pb-2 text-[9px] font-semibold uppercase tracking-widest text-slate-600",
                  h === "Ticker" ? "text-left pr-3" : "text-right px-2")}>
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
                  <td className="py-2 px-2 text-right text-slate-400">{has ? row.n_trades : <span className="text-slate-600">—</span>}</td>
                  <td className="py-2 px-2 text-right">
                    {has && row.win_rate != null
                      ? <span className={clsx("font-medium", row.win_rate >= 0.5 ? "text-green-400" : "text-red-400")}>{(row.win_rate * 100).toFixed(0)}%</span>
                      : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="py-2 px-2 text-right">
                    {has && row.avg_return != null
                      ? <span className={clsx("font-medium tabular-nums", row.avg_return >= 0 ? "text-green-400" : "text-red-400")}>{pct(row.avg_return, 2, true)}</span>
                      : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="py-2 pl-2 text-right">
                    {has && row.median_return != null
                      ? <span className={clsx("font-medium tabular-nums", row.median_return >= 0 ? "text-green-400" : "text-red-400")}>{pct(row.median_return, 2, true)}</span>
                      : <span className="text-slate-600">—</span>}
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
// Main page
// ---------------------------------------------------------------------------

export default function GradesPage() {
  const [result, setResult] = useState<SignalGradesResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState<string | null>(null);
  const [requireRealIV, setRequireRealIV] = useState(false);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getSignalGrades(undefined, requireRealIV)
      .then((d) => setResult(d))
      .catch((e) => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  }, [requireRealIV]);

  return (
    <main className="min-h-screen bg-surface px-4 py-5 md:px-6 md:py-7">

      {/* ── Header ────────────────────────────────────────────────────── */}
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <Link href="/" className="text-[10px] text-slate-500 hover:text-slate-300 transition-colors">← Dashboard</Link>
            <span className="text-slate-700 text-[10px]">/</span>
            <Link href="/backtest" className="text-[10px] text-slate-500 hover:text-slate-300 transition-colors">Backtest</Link>
            <span className="text-slate-700 text-[10px]">/</span>
            <Link href="/backtest/macro" className="text-[10px] text-slate-500 hover:text-slate-300 transition-colors">Macro</Link>
            <span className="text-slate-700 text-[10px]">/</span>
            <span className="text-[10px] text-slate-400">Signal Grades</span>
          </div>
          <h1 className="text-xl font-bold tracking-tight text-white">Signal Grades</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            {result
              ? `V2 scored 0–100 · ${result.tickers.join(" · ")} · ${result.data_source} data`
              : "Scoring V2 entries A–F based on macro conditions…"}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-[10px] font-mono text-slate-500">
            SELL_PREMIUM · 10d hold
          </span>
          <span className="shrink-0 rounded-full bg-yellow-500/10 border border-yellow-500/20 px-3 py-1 text-xs font-medium text-yellow-400">
            Mock Data
          </span>
          <Link
            href="/backtest/optimize"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-[10px] font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
          >
            Optimise Weights →
          </Link>

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

      {loading && <PageSkeleton />}

      {error && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-4">
          <p className="text-xs font-semibold text-red-400 mb-1">Failed to load signal grades</p>
          <p className="font-mono text-[10px] text-red-300/60">{error}</p>
        </div>
      )}

      {result && (
        <div className="space-y-4">

          {/* Real-IV filter summary */}
          <RealIVFilterBanner filter={result.real_iv_filter} />

          {/* Global sample-size warning */}
          {requireRealIV && result.portfolios.every((p) => p.n_trades < 20) && (
            <div className="flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-xs text-amber-400">
              <span className="mt-0.5 shrink-0 text-base leading-none">⚠</span>
              <span>
                <strong>Small sample warning:</strong> All portfolios have fewer than 20 trades
                in the real-IV window. Results are not statistically reliable. Consider widening
                the date range or toggling off &ldquo;Real IV only&rdquo; for exploratory analysis.
              </span>
            </div>
          )}

          {/* IV data-quality gate */}
          <IVQualityBadge gate={result.iv_quality} />

          {/* Scoring rubric */}
          <ScoringRubric components={result.score_components} />

          {/* Score distribution + Premium capture side-by-side */}
          <div className="grid gap-4 grid-cols-1 lg:grid-cols-2">
            <ScoreHistogram result={result} />
            <PremiumCaptureChart result={result} />
          </div>

          {/* Grade performance stats */}
          <GradeStatsTable result={result} />

          {/* Portfolio comparison */}
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-2">
              Portfolio Comparison  ·  By Grade Cutoff
            </p>
            <PortfolioTable portfolios={result.portfolios} />
          </div>

          {/* Equity curves */}
          <EquityChart portfolios={result.portfolios} />

          {/* VIX bucket breakdown */}
          <VIXBucketBreakdown portfolios={result.portfolios} />

          {/* Per-ticker breakdown */}
          <TickerBreakdown portfolios={result.portfolios} tickers={result.tickers} />

          <p className="text-center text-[10px] text-slate-700 pt-2">
            Phase 2 · synthetic prices · mock macro series · Phase 3 will validate grade → outcome correlation with live data
          </p>
        </div>
      )}
    </main>
  );
}
