"use client";

/**
 * /backtest/optimize
 * ------------------
 * Score-weight optimisation page.
 *
 * Shows the results of a 59 049-point grid search (19 683 weight combos × 3
 * thresholds) that maximises a composite objective:
 *
 *   obj = 0.40 × PCR_norm + 0.35 × PF_norm + 0.25 × MDD_norm − trade_penalty
 *
 * Sections
 * --------
 *   1. Search stats banner
 *   2. Objective formula card
 *   3. Best model vs current model comparison
 *   4. Equity curves (top 3 + current)
 *   5. Top-10 models table (metrics + weights)
 *   6. Weight sensitivity charts
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import {
  getScoreOptimization,
  type ScoreOptimizationResult,
  type OptimizedModel,
  type WeightSensitivityPoint,
  type RealIVFilter,
} from "@/lib/api";
import IVQualityBadge from "@/components/dashboard/IVQualityBadge";

// ---------------------------------------------------------------------------
// Colour palette
// ---------------------------------------------------------------------------

const RANK_COLORS: Record<number, string> = {
  1: "#34d399",  // emerald
  2: "#60a5fa",  // blue
  3: "#fbbf24",  // amber
};
const CURRENT_COLOR = "#94a3b8";   // slate

const COMP_COLORS: Record<string, string> = {
  iv_rv:         "#818cf8",
  iv_rank:       "#a78bfa",
  vix_ts:        "#34d399",
  breadth:       "#60a5fa",
  spy_200dma:    "#38bdf8",
  yield_stress:  "#f87171",
  oil_shock:     "#fb923c",
  vix_inversion: "#f43f5e",
  vix_spike:     "#e879f9",
};

const COMP_LABELS: Record<string, string> = {
  iv_rv:         "IV/RV",
  iv_rank:       "IV Rank",
  vix_ts:        "VIX TS",
  breadth:       "Breadth",
  spy_200dma:    "SPY 200d",
  yield_stress:  "Yield",
  oil_shock:     "Oil",
  vix_inversion: "VIX Inv",
  vix_spike:     "VIX Spike",
};

// ---------------------------------------------------------------------------
// Utility helpers
// ---------------------------------------------------------------------------

const pct  = (v: number | null, d = 1) =>
  v == null ? "—" : `${(v * 100).toFixed(d)}%`;
const fix2 = (v: number | null)  =>
  v == null ? "—" : v.toFixed(2);
const fix4 = (v: number)         => v.toFixed(4);
const absV = (v: number | null)  => (v == null ? null : Math.abs(v));

function delta(v: number | null, inverted = false): string {
  if (v == null) return "—";
  const sign = inverted ? (v < 0 ? "+" : v > 0 ? "−" : "") : v > 0 ? "+" : "";
  const col  = inverted
    ? v < 0 ? "text-emerald-400" : v > 0 ? "text-red-400" : "text-slate-400"
    : v > 0 ? "text-emerald-400" : v < 0 ? "text-red-400" : "text-slate-400";
  return `${sign}${Math.abs(v).toFixed(4)}`;
}
function deltaColor(v: number | null, inverted = false): string {
  if (v == null) return "text-slate-400";
  const pos = inverted ? v < 0 : v > 0;
  const neg = inverted ? v > 0 : v < 0;
  return pos ? "text-emerald-400" : neg ? "text-red-400" : "text-slate-400";
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
// Async state
// ---------------------------------------------------------------------------

interface AsyncState<T> {
  data:    T | null;
  loading: boolean;
  error:   string | null;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Slim stats banner */
function SearchStatsBanner({ d }: { d: ScoreOptimizationResult }) {
  const ss = d.search_stats;
  return (
    <div className="flex flex-wrap gap-4 text-xs text-slate-400 font-mono border border-border rounded-lg px-4 py-3 bg-surface-2">
      <span>
        <span className="text-slate-200 font-semibold">
          {ss.total_evaluated.toLocaleString()}
        </span>{" "}
        evaluations
      </span>
      <span className="text-slate-600">|</span>
      <span>
        <span className="text-slate-200 font-semibold">
          {ss.weight_combinations.toLocaleString()}
        </span>{" "}
        weight combos × {ss.thresholds_tested.length} thresholds
      </span>
      <span className="text-slate-600">|</span>
      <span>
        <span className="text-slate-200 font-semibold">
          {ss.candidates_in_pool}
        </span>{" "}
        V2 candidates in pool
      </span>
      <span className="text-slate-600">|</span>
      <span>
        completed in{" "}
        <span className="text-slate-200 font-semibold">{ss.elapsed_ms} ms</span>
      </span>
    </div>
  );
}

/** Objective formula explanation */
function ObjectiveCard() {
  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <h3 className="text-sm font-semibold text-slate-200 mb-3">
        Objective Function
      </h3>
      <p className="font-mono text-sm text-violet-300 mb-3">
        obj = 0.40 × PCR<sub>norm</sub> + 0.35 × PF<sub>norm</sub> + 0.25 ×
        MDD<sub>norm</sub> − trade_penalty
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs text-slate-400">
        {[
          ["PCR_norm", "clip((pcr + 30%) / 60%, 0, 1)", "40%"],
          ["PF_norm",  "clip(pf / 3.0, 0, 1)",          "35%"],
          ["MDD_norm", "clip(1 − |mdd| / 50%, 0, 1)",   "25%"],
          ["penalty",  "max(0, (10 − n) × 0.05)",       "subtracted"],
        ].map(([name, formula, weight]) => (
          <div key={name} className="flex items-start gap-2">
            <span className="font-mono text-slate-300 w-20 shrink-0">{name}</span>
            <span className="flex-1">{formula}</span>
            <span className="text-violet-400 shrink-0">{weight}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Side-by-side model metric comparison */
function ModelComparisonCards({
  best,
  current,
  comparison,
}: {
  best: OptimizedModel;
  current: OptimizedModel;
  comparison: ScoreOptimizationResult["comparison"];
}) {
  const rows: Array<{
    label: string;
    bestVal: string;
    curVal: string;
    diffVal: number | null;
    inverted?: boolean;
  }> = [
    {
      label:    "Objective",
      bestVal:  fix4(best.objective_score),
      curVal:   fix4(current.objective_score),
      diffVal:  comparison?.objective_improvement ?? null,
    },
    {
      label:    "Trades",
      bestVal:  String(best.n_trades),
      curVal:   String(current.n_trades),
      diffVal:  comparison ? comparison.trade_count_change : null,
    },
    {
      label:    "Win Rate",
      bestVal:  pct(best.win_rate),
      curVal:   pct(current.win_rate),
      diffVal:  null,
    },
    {
      label:    "Prem Capture",
      bestVal:  pct(best.premium_capture_rate),
      curVal:   pct(current.premium_capture_rate),
      diffVal:  comparison?.pcr_improvement ?? null,
    },
    {
      label:    "Profit Factor",
      bestVal:  fix2(best.profit_factor),
      curVal:   fix2(current.profit_factor),
      diffVal:  comparison?.profit_factor_improvement ?? null,
    },
    {
      label:    "Max Drawdown",
      bestVal:  pct(absV(best.max_drawdown)),
      curVal:   pct(absV(current.max_drawdown)),
      diffVal:  comparison?.drawdown_improvement ?? null,
    },
    {
      label:    "Sharpe",
      bestVal:  fix2(best.sharpe),
      curVal:   fix2(current.sharpe),
      diffVal:  comparison?.sharpe_improvement ?? null,
    },
    {
      label:    "Threshold",
      bestVal:  String(best.threshold),
      curVal:   String(current.threshold),
      diffVal:  null,
    },
  ];

  const WeightTag = ({
    k,
    v,
    baseline,
  }: {
    k: string;
    v: number;
    baseline: number;
  }) => {
    const diff = v - baseline;
    const bg =
      diff > 0
        ? "bg-emerald-900/40 text-emerald-300 border-emerald-700"
        : diff < 0
          ? "bg-red-900/40 text-red-300 border-red-700"
          : "bg-slate-800 text-slate-400 border-slate-600";
    return (
      <span
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] font-mono ${bg}`}
      >
        <span className="text-slate-500">{COMP_LABELS[k]}</span>
        <span>{v > 0 ? `+${v}` : v}</span>
      </span>
    );
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      {/* Best model */}
      <div className="rounded-xl border border-emerald-800/50 bg-surface-2 p-4">
        <div className="flex items-center gap-2 mb-3">
          <span className="w-2 h-2 rounded-full bg-emerald-400 shrink-0" />
          <span className="text-sm font-semibold text-emerald-300">
            Optimized #1
          </span>
          <span className="ml-auto text-xs font-mono text-slate-400">
            th ≥ {best.threshold}
          </span>
        </div>
        {/* weights */}
        <div className="flex flex-wrap gap-1 mb-3">
          {Object.entries(best.weights).map(([k, v]) => (
            <WeightTag
              key={k}
              k={k}
              v={v}
              baseline={(current.weights as Record<string, number>)[k]}
            />
          ))}
        </div>
        {/* metrics */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          {rows.map((r) => (
            <div key={r.label} className="flex justify-between">
              <span className="text-slate-500">{r.label}</span>
              <span className="font-mono text-slate-200">{r.bestVal}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Current model */}
      <div className="rounded-xl border border-slate-700 bg-surface-2 p-4">
        <div className="flex items-center gap-2 mb-3">
          <span className="w-2 h-2 rounded-full bg-slate-400 shrink-0" />
          <span className="text-sm font-semibold text-slate-300">
            Current Hand-Built
          </span>
          <span className="ml-auto text-xs font-mono text-slate-400">
            th ≥ {current.threshold}
          </span>
        </div>
        {/* weights */}
        <div className="flex flex-wrap gap-1 mb-3">
          {Object.entries(current.weights).map(([k, v]) => (
            <span
              key={k}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-slate-600 bg-slate-800 text-[10px] font-mono text-slate-400"
            >
              <span className="text-slate-500">{COMP_LABELS[k]}</span>
              <span>{v > 0 ? `+${v}` : v}</span>
            </span>
          ))}
        </div>
        {/* metrics */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          {rows.map((r) => (
            <div key={r.label} className="flex justify-between">
              <span className="text-slate-500">{r.label}</span>
              <div className="flex items-center gap-1 font-mono">
                <span className="text-slate-200">{r.curVal}</span>
                {r.diffVal != null && (
                  <span
                    className={`text-[10px] ${deltaColor(r.diffVal, r.inverted)}`}
                  >
                    ({delta(r.diffVal, r.inverted)})
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Multi-line equity chart: top 3 + current */
function EquityChart({ models, current }: { models: OptimizedModel[]; current: OptimizedModel }) {
  const top3 = models.slice(0, 3).filter((m) => m.equity_points?.length);

  // Merge all trades into one aligned dataset (by trade_n for each series)
  const maxLen = Math.max(
    ...top3.map((m) => m.equity_points!.length),
    current.equity_points?.length ?? 0,
  );

  const chartData = Array.from({ length: maxLen }, (_, i) => {
    const row: Record<string, number | null> = { trade_n: i + 1 };
    top3.forEach((m, ri) => {
      row[`model_${ri + 1}`] = m.equity_points?.[i]?.equity ?? null;
    });
    row["current"] = current.equity_points?.[i]?.equity ?? null;
    return row;
  });

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <h3 className="text-sm font-semibold text-slate-200 mb-4">
        Equity Curves — Top 3 vs Current
      </h3>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis
            dataKey="trade_n"
            tick={{ fontSize: 10, fill: "#64748b" }}
            label={{ value: "Trade #", position: "insideBottom", offset: -2, fontSize: 10, fill: "#64748b" }}
          />
          <YAxis
            tickFormatter={(v) => `${((v - 1) * 100).toFixed(0)}%`}
            tick={{ fontSize: 10, fill: "#64748b" }}
          />
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", fontSize: 11 }}
            formatter={(v: number, name: string) => {
              const label =
                name === "current"
                  ? "Current"
                  : `Optimized #${name.replace("model_", "")}`;
              return [`${((v - 1) * 100).toFixed(2)}%`, label];
            }}
          />
          {top3.map((_, ri) => (
            <Line
              key={`model_${ri + 1}`}
              type="stepAfter"
              dataKey={`model_${ri + 1}`}
              stroke={RANK_COLORS[ri + 1] ?? "#64748b"}
              dot={false}
              strokeWidth={2}
              connectNulls
            />
          ))}
          <Line
            type="stepAfter"
            dataKey="current"
            stroke={CURRENT_COLOR}
            dot={false}
            strokeWidth={1.5}
            strokeDasharray="4 3"
            connectNulls
          />
          <ReferenceLine y={1} stroke="#334155" strokeDasharray="2 2" />
        </LineChart>
      </ResponsiveContainer>
      <div className="flex flex-wrap gap-3 mt-3 text-xs">
        {top3.map((_, ri) => (
          <span key={ri} className="flex items-center gap-1.5">
            <span
              className="w-3 h-0.5 inline-block rounded"
              style={{ background: RANK_COLORS[ri + 1] }}
            />
            <span className="text-slate-400">Optimized #{ri + 1}</span>
          </span>
        ))}
        <span className="flex items-center gap-1.5">
          <span className="w-3 h-0.5 inline-block rounded bg-slate-400" style={{ background: CURRENT_COLOR }} />
          <span className="text-slate-400">Current</span>
        </span>
      </div>
    </div>
  );
}

/** Top-10 metrics table */
function Top10MetricsTable({ models }: { models: OptimizedModel[] }) {
  return (
    <div className="rounded-xl border border-border bg-surface-2 overflow-x-auto">
      <div className="px-4 pt-4 pb-2">
        <h3 className="text-sm font-semibold text-slate-200">
          Top 10 Models — Metrics
        </h3>
        <p className="text-xs text-slate-500 mt-0.5">
          Ranked by composite objective. Duplicate metrics indicate multiple
          weight configurations achieving the same optimal portfolio.
        </p>
      </div>
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="border-b border-border text-slate-500">
            {["#", "Obj", "Th", "N", "WR", "PCR", "PF", "MDD", "Sharpe", "PCR c.", "PF c.", "MDD c.", "Penalty"].map(
              (h) => (
                <th key={h} className="px-3 py-2 text-right first:text-left whitespace-nowrap">
                  {h}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {models.map((m) => {
            const bd = m.objective_breakdown;
            const isTop = m.rank === 1;
            return (
              <tr
                key={`${m.rank}-${m.threshold}-${JSON.stringify(m.weights)}`}
                className={`border-b border-border/40 ${isTop ? "bg-emerald-900/10" : "hover:bg-surface-3"}`}
              >
                <td className="px-3 py-2 text-left">
                  <span
                    className="font-semibold"
                    style={{ color: RANK_COLORS[m.rank ?? 99] ?? "#64748b" }}
                  >
                    #{m.rank}
                  </span>
                </td>
                <td className="px-3 py-2 text-right text-slate-200">
                  {fix4(m.objective_score)}
                </td>
                <td className="px-3 py-2 text-right text-slate-400">
                  {m.threshold}
                </td>
                <td className="px-3 py-2 text-right text-slate-300">
                  {m.n_trades}
                </td>
                <td className="px-3 py-2 text-right">{pct(m.win_rate)}</td>
                <td
                  className={`px-3 py-2 text-right ${
                    (m.premium_capture_rate ?? 0) >= 0
                      ? "text-emerald-400"
                      : "text-red-400"
                  }`}
                >
                  {pct(m.premium_capture_rate)}
                </td>
                <td
                  className={`px-3 py-2 text-right ${
                    (m.profit_factor ?? 0) >= 1.5
                      ? "text-emerald-400"
                      : "text-slate-300"
                  }`}
                >
                  {fix2(m.profit_factor)}
                </td>
                <td className="px-3 py-2 text-right text-red-400">
                  {pct(absV(m.max_drawdown))}
                </td>
                <td
                  className={`px-3 py-2 text-right ${
                    (m.sharpe ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"
                  }`}
                >
                  {fix2(m.sharpe)}
                </td>
                <td className="px-3 py-2 text-right text-slate-400">
                  {fix4(bd.pcr_component)}
                </td>
                <td className="px-3 py-2 text-right text-slate-400">
                  {fix4(bd.pf_component)}
                </td>
                <td className="px-3 py-2 text-right text-slate-400">
                  {fix4(bd.mdd_component)}
                </td>
                <td
                  className={`px-3 py-2 text-right ${
                    bd.trade_penalty < 0 ? "text-red-400" : "text-slate-500"
                  }`}
                >
                  {fix4(bd.trade_penalty)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Top-10 weights table */
function Top10WeightsTable({ models, current }: { models: OptimizedModel[]; current: OptimizedModel }) {
  const keys = Object.keys(models[0]?.weights ?? {}) as Array<keyof typeof models[0]["weights"]>;
  const cw = current.weights;

  return (
    <div className="rounded-xl border border-border bg-surface-2 overflow-x-auto">
      <div className="px-4 pt-4 pb-2">
        <h3 className="text-sm font-semibold text-slate-200">
          Top 10 Models — Weights
        </h3>
        <p className="text-xs text-slate-500 mt-0.5">
          Coloured relative to current hand-built weights.{" "}
          <span className="text-emerald-400">Green</span> = higher weight,{" "}
          <span className="text-red-400">red</span> = lower weight.
        </p>
      </div>
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="border-b border-border text-slate-500">
            <th className="px-3 py-2 text-left">#</th>
            {keys.map((k) => (
              <th key={k} className="px-3 py-2 text-right whitespace-nowrap">
                {COMP_LABELS[k]}
              </th>
            ))}
            <th className="px-3 py-2 text-right">Dist</th>
          </tr>
        </thead>
        <tbody>
          {/* current row first */}
          <tr className="border-b border-border bg-slate-900/40">
            <td className="px-3 py-2 text-left text-slate-400">base</td>
            {keys.map((k) => (
              <td key={k} className="px-3 py-2 text-right text-slate-400">
                {(cw as Record<string, number>)[k] > 0
                  ? `+${(cw as Record<string, number>)[k]}`
                  : (cw as Record<string, number>)[k]}
              </td>
            ))}
            <td className="px-3 py-2 text-right text-slate-500">—</td>
          </tr>
          {models.map((m) => {
            const isTop = m.rank === 1;
            return (
              <tr
                key={`w-${m.rank}-${m.threshold}-${JSON.stringify(m.weights)}`}
                className={`border-b border-border/40 ${isTop ? "bg-emerald-900/10" : "hover:bg-surface-3"}`}
              >
                <td className="px-3 py-2 text-left">
                  <span style={{ color: RANK_COLORS[m.rank ?? 99] ?? "#64748b" }}>
                    #{m.rank}
                  </span>
                </td>
                {keys.map((k) => {
                  const v    = (m.weights as Record<string, number>)[k];
                  const base = (cw as Record<string, number>)[k];
                  const diff = v - base;
                  const cls  =
                    diff > 0
                      ? "text-emerald-400"
                      : diff < 0
                        ? "text-red-400"
                        : "text-slate-400";
                  return (
                    <td key={k} className={`px-3 py-2 text-right ${cls}`}>
                      {v > 0 ? `+${v}` : v}
                    </td>
                  );
                })}
                <td className="px-3 py-2 text-right text-slate-500">
                  {m.weight_distance_from_current ?? "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Weight sensitivity — one bar chart per component */
function WeightSensitivityGrid({
  sensitivity,
}: {
  sensitivity: Record<string, WeightSensitivityPoint[]>;
}) {
  const keys = Object.keys(COMP_LABELS);

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <h3 className="text-sm font-semibold text-slate-200 mb-1">
        Weight Sensitivity
      </h3>
      <p className="text-xs text-slate-500 mb-4">
        Average objective score at each grid value across all 59 049 evaluations.
        Higher bars = that weight level consistently improves the objective.
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
        {keys.map((k) => {
          const pts = sensitivity[k] ?? [];
          const maxAvg = Math.max(...pts.map((p) => p.avg_objective), 0.001);
          const minAvg = Math.min(...pts.map((p) => p.avg_objective));
          const range  = maxAvg - minAvg;
          const color  = COMP_COLORS[k] ?? "#64748b";

          return (
            <div key={k} className="flex flex-col gap-1">
              <span className="text-[11px] font-semibold text-slate-300">
                {COMP_LABELS[k]}
              </span>
              <ResponsiveContainer width="100%" height={80}>
                <BarChart
                  data={pts}
                  margin={{ top: 4, right: 0, bottom: 0, left: 0 }}
                >
                  <CartesianGrid strokeDasharray="2 2" stroke="#1e293b" vertical={false} />
                  <XAxis
                    dataKey="weight"
                    tick={{ fontSize: 9, fill: "#64748b" }}
                    tickFormatter={(v) => String(v)}
                  />
                  <YAxis
                    domain={[minAvg - range * 0.1, maxAvg + range * 0.1]}
                    tick={{ fontSize: 8, fill: "#64748b" }}
                    width={28}
                    tickFormatter={(v) => v.toFixed(2)}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "#0f172a",
                      border: "1px solid #1e293b",
                      fontSize: 10,
                    }}
                    formatter={(v: number) => [v.toFixed(4), "avg obj"]}
                    labelFormatter={(l) => `weight = ${l}`}
                  />
                  <Bar dataKey="avg_objective" radius={[2, 2, 0, 0]}>
                    {pts.map((pt, i) => {
                      const intensity = range > 0 ? (pt.avg_objective - minAvg) / range : 0.5;
                      const opacity   = 0.4 + intensity * 0.6;
                      return (
                        <Cell
                          key={i}
                          fill={color}
                          fillOpacity={opacity}
                        />
                      );
                    })}
                  </Bar>
                  <ReferenceLine y={0} stroke="#334155" strokeDasharray="2 2" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Export button — only enabled when iv_quality.status === "GOOD"
// ---------------------------------------------------------------------------

function ExportButton({ data }: { data: ScoreOptimizationResult }) {
  const quality = data.iv_quality;
  const isGood  = quality?.status === "GOOD";

  function handleExport() {
    const payload = {
      exported_at:    new Date().toISOString(),
      iv_quality:     quality,
      top_models:     data.top_models,
      current_model:  data.current_model,
      comparison:     data.comparison,
      search_stats:   data.search_stats,
      tickers:        data.tickers,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = `score_optimization_${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        onClick={handleExport}
        disabled={!isGood}
        title={
          isGood
            ? "Export optimisation results to JSON"
            : `Export blocked: IV data quality is ${quality?.status ?? "unknown"} — needs GOOD (≥80% real IV)`
        }
        className={[
          "rounded-lg border px-4 py-2 text-xs font-semibold transition-colors",
          isGood
            ? "border-green-500/40 bg-green-500/10 text-green-400 hover:bg-green-500/20 cursor-pointer"
            : "border-slate-700 bg-slate-800/40 text-slate-600 cursor-not-allowed opacity-60",
        ].join(" ")}
      >
        ↓ Export Results
      </button>
      {!isGood && quality && (
        <span className="text-[10px] text-slate-600">
          Blocked — quality is {quality.status}
        </span>
      )}
    </div>
  );
}

export default function OptimizePage() {
  const [state, setState] = useState<AsyncState<ScoreOptimizationResult>>({
    data:    null,
    loading: true,
    error:   null,
  });
  const [dataDays, setDataDays] = useState(400);
  const [requireRealIV, setRequireRealIV] = useState(false);

  useEffect(() => {
    setState({ data: null, loading: true, error: null });
    getScoreOptimization(dataDays, requireRealIV)
      .then((data) => setState({ data, loading: false, error: null }))
      .catch((e)   => setState({ data: null, loading: false, error: String(e) }));
  }, [dataDays, requireRealIV]);

  const { data, loading, error } = state;

  return (
    <main className="min-h-screen bg-background text-foreground p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <div className="flex-1 min-w-0">
          <h1 className="text-xl font-bold text-slate-100 truncate">
            Score-Weight Optimisation
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Grid search over 9 scoring components · {(19683 * 3).toLocaleString()} evaluations ·
            maximize PCR + profit factor, minimize drawdown
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0 flex-wrap">
          <select
            value={dataDays}
            onChange={(e) => setDataDays(Number(e.target.value))}
            className="bg-surface-2 border border-border text-slate-300 text-xs rounded px-2 py-1"
          >
            {[200, 400, 756, 1260].map((d) => (
              <option key={d} value={d}>
                {d}d
              </option>
            ))}
          </select>

          {/* Real-IV toggle */}
          <label className="shrink-0 flex items-center gap-2 cursor-pointer rounded-full border border-border bg-surface-2 px-3 py-1 text-[10px] font-medium text-slate-400 hover:border-slate-500 hover:text-slate-200 transition-colors select-none">
            <span
              className={`relative inline-block w-7 h-4 rounded-full transition-colors duration-200 ${requireRealIV ? "bg-blue-500" : "bg-slate-600"}`}
            >
              <span
                className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-white shadow transition-transform duration-200 ${requireRealIV ? "translate-x-3" : "translate-x-0"}`}
              />
            </span>
            <input
              type="checkbox"
              className="sr-only"
              checked={requireRealIV}
              onChange={(e) => setRequireRealIV(e.target.checked)}
            />
            <span className={requireRealIV ? "text-blue-300" : ""}>Real IV only</span>
          </label>

          <Link
            href="/backtest/walk-forward"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-[10px] font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
          >
            Walk-Forward →
          </Link>
          <Link
            href="/backtest/grades"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-[10px] font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
          >
            Signal Grades →
          </Link>
          <Link
            href="/backtest"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-[10px] font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
          >
            ← Backtest
          </Link>
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex flex-col items-center justify-center py-24 gap-3">
          <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-slate-400 text-sm">
            Running 59 049 evaluations…
          </p>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-xl border border-red-800 bg-red-900/20 p-6 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Content */}
      {data && !loading && (
        <div className="flex flex-col gap-6">

          {/* Real-IV filter summary */}
          <RealIVFilterBanner filter={data.real_iv_filter} />

          {/* Sample-size warning when optimiser finds no good candidates */}
          {data.sample_size_warning && (
            <div className="flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-xs text-amber-400">
              <span className="mt-0.5 shrink-0 text-base leading-none">⚠</span>
              <span>
                <strong>Small sample warning:</strong> The best model has fewer than 20 trades
                in the real-IV window. Optimisation results are not statistically reliable.
                Consider widening the date range or toggling off &ldquo;Real IV only&rdquo;.
              </span>
            </div>
          )}

          {/* IV data-quality gate + export button */}
          <div className="flex flex-wrap items-start justify-between gap-3">
            <IVQualityBadge gate={data.iv_quality} />
            <ExportButton data={data} />
          </div>

          <SearchStatsBanner d={data} />

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
            <div className="xl:col-span-2">
              <ObjectiveCard />
            </div>
            <div className="xl:col-span-1 rounded-xl border border-border bg-surface-2 p-4 text-xs text-slate-400">
              <h3 className="text-sm font-semibold text-slate-200 mb-2">
                Search Space
              </h3>
              {Object.entries(data.weight_grid).map(([k, vals]) => (
                <div key={k} className="flex justify-between py-0.5 border-b border-border/30">
                  <span className="text-slate-400">{COMP_LABELS[k]}</span>
                  <span className="font-mono text-slate-300">
                    [{(vals as number[]).join(", ")}]
                  </span>
                </div>
              ))}
            </div>
          </div>

          {data.top_models.length > 0 && (
            <>
              <ModelComparisonCards
                best={data.top_models[0]}
                current={data.current_model}
                comparison={data.comparison}
              />
              <EquityChart
                models={data.top_models}
                current={data.current_model}
              />
              <Top10MetricsTable models={data.top_models} />
              <Top10WeightsTable
                models={data.top_models}
                current={data.current_model}
              />
              <WeightSensitivityGrid sensitivity={data.weight_sensitivity} />
            </>
          )}

          {data.top_models.length === 0 && (
            <div className="rounded-xl border border-border bg-surface-2 p-8 text-center text-slate-400 text-sm">
              No qualifying models found. Try increasing{" "}
              <span className="font-mono text-slate-300">data_days</span>.
            </div>
          )}
        </div>
      )}
    </main>
  );
}
