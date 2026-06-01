"use client";

/**
 * /backtest/walk-forward
 * ----------------------
 * Walk-forward validation page with three objective modes.
 *
 * Rolling windows:  train 252d · test 63d · step 63d
 *
 * Objective modes (evaluated in one grid-search pass per window):
 *   balanced      — 0.40 PCR + 0.35 PF + 0.25 MDD  (default)
 *   pcr_stability — 0.60 PCR + 0.20 PF + 0.20 MDD + dispersion penalty
 *   conservative  — 0.40 PCR + 0.20 PF + 0.40 MDD
 *
 * Layout
 * ------
 *   1. Config / stats header  (with mode selector)
 *   2. Mode comparison panel  (3-mode metrics table + objective degradation bars)
 *   3. Summary comparison (IS · OOS · Current OOS)  ← hero section
 *   4. Degradation bar chart (per-metric, average across windows)
 *   5. OOS equity curve (walk-forward OOS vs current OOS vs IS)
 *   6. Weight turnover chart (per-window L1 weight change)
 *   7. Per-window table (dates, weights, IS/OOS metrics, degradation)
 *   8. Weight consistency heatmap (windows × components)
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
  getWalkForward,
  type WalkForwardResult,
  type WFWindow,
  type WFMetrics,
  type WFDegradation,
  type WFSummary,
  type WFEquityPoint,
  type ModeComparisonEntry,
  type RealIVFilter,
} from "@/lib/api";
import IVQualityBadge from "@/components/dashboard/IVQualityBadge";

// ---------------------------------------------------------------------------
// Colours
// ---------------------------------------------------------------------------

const IS_COLOR      = "#818cf8";   // violet  — in-sample
const OOS_COLOR     = "#34d399";   // emerald — walk-forward OOS
const CURRENT_COLOR = "#94a3b8";   // slate   — current hand-built OOS

/** Per-mode accent colours */
const MODE_COLORS: Record<string, string> = {
  balanced:      "#60a5fa",   // blue
  pcr_stability: "#a78bfa",   // purple
  conservative:  "#34d399",   // emerald
};

const COMP_LABELS: Record<string, string> = {
  iv_rv: "IV/RV", iv_rank: "IV Rank", vix_ts: "VIX TS",
  breadth: "Breadth", spy_200dma: "SPY 200d",
  yield_stress: "Yield", oil_shock: "Oil",
  vix_inversion: "VIX Inv", vix_spike: "VIX Spike",
};

const MODE_IDS = ["balanced", "pcr_stability", "conservative"] as const;
type ModeId = (typeof MODE_IDS)[number];

// Colour for weight values in heatmap
function weightColor(val: number): string {
  if (val > 0) {
    const intensity = Math.min(val / 35, 1);
    return `rgba(52,211,153,${0.2 + intensity * 0.7})`;
  }
  const intensity = Math.min(Math.abs(val) / 25, 1);
  return `rgba(248,113,113,${0.2 + intensity * 0.7})`;
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

const pct   = (v: number | null, d = 1) =>
  v == null ? "—" : `${(v * 100).toFixed(d)}%`;
const fix2  = (v: number | null) =>
  v == null ? "—" : v.toFixed(2);
const fix4  = (v: number | null) =>
  v == null ? "—" : (v as number).toFixed(4);
const absV  = (v: number | null) => (v == null ? null : Math.abs(v));

/**
 * Return a colour class and sign prefix for a degradation value.
 * For "normal" metrics:  negative = worse OOS → red
 * For max_drawdown_abs:  positive = more DD   → red
 */
function degStyle(val: number | null, invertedGood = false) {
  if (val == null) return { color: "text-slate-500", prefix: "" };
  const pos = invertedGood ? val < 0 : val > 0;
  const neg = invertedGood ? val > 0 : val < 0;
  const color = pos ? "text-emerald-400" : neg ? "text-red-400" : "text-slate-400";
  const prefix = val > 0 ? "+" : "";
  return { color, prefix };
}

// ---------------------------------------------------------------------------
// Async state
// ---------------------------------------------------------------------------

interface AsyncState<T> {
  data: T | null; loading: boolean; error: string | null;
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
// Section: Mode comparison panel
// ---------------------------------------------------------------------------

function ModeComparisonPanel({
  modeComparison,
  activeMode,
  modeDefinitions,
}: {
  modeComparison: Record<string, ModeComparisonEntry>;
  activeMode: string;
  modeDefinitions: Record<string, { label: string; description: string }>;
}) {
  const modes = MODE_IDS.filter((m) => modeComparison[m]);

  // Objective degradation bar chart data
  const degBars = modes.map((m) => ({
    label: modeComparison[m].label,
    value: modeComparison[m].avg_degradation_obj,
    mode:  m,
  })).filter((b) => b.value != null);

  const rows: { label: string; fmt: (e: ModeComparisonEntry) => string; mddLike?: boolean }[] = [
    { label: "IS Obj (avg)",   fmt: (e) => e.is_obj_avg != null   ? e.is_obj_avg.toFixed(4)   : "—" },
    { label: "OOS Obj (avg)",  fmt: (e) => e.oos_obj_avg != null  ? e.oos_obj_avg.toFixed(4)  : "—" },
    { label: "Avg Deg (Obj)",  fmt: (e) => {
        const v = e.avg_degradation_obj;
        if (v == null) return "—";
        return `${v > 0 ? "+" : ""}${v.toFixed(4)}`;
      }
    },
    { label: "OOS Trades",     fmt: (e) => String(e.oos_n_trades) },
    { label: "OOS Win Rate",   fmt: (e) => pct(e.oos_win_rate) },
    { label: "OOS PCR",        fmt: (e) => pct(e.oos_pcr) },
    { label: "OOS Profit Fac", fmt: (e) => fix2(e.oos_profit_factor) },
    { label: "OOS Sharpe",     fmt: (e) => fix2(e.oos_sharpe) },
    { label: "OOS Max DD",     fmt: (e) => pct(e.oos_mdd_abs), mddLike: true },
    { label: "Avg Wt Turnover",fmt: (e) => e.avg_weight_turnover != null ? String(e.avg_weight_turnover) : "—" },
  ];

  return (
    <div className="rounded-xl border border-border bg-surface-2 overflow-hidden">
      <div className="px-4 pt-4 pb-2 border-b border-border flex flex-wrap items-center gap-3">
        <div>
          <h2 className="text-sm font-bold text-slate-100">Mode Comparison</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            All three objective modes evaluated in one grid-search pass per window.
          </p>
        </div>
        <div className="flex gap-2 flex-wrap text-[10px]">
          {modes.map((m) => (
            <span key={m} className="flex items-center gap-1 px-2 py-0.5 rounded-full border border-slate-700">
              <span className="w-2 h-2 rounded-full inline-block"
                style={{ background: MODE_COLORS[m] }} />
              <span className="text-slate-300">{modeDefinitions[m]?.label ?? m}</span>
              {m === activeMode && <span className="text-slate-500">(active)</span>}
            </span>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-0 divide-y lg:divide-y-0 lg:divide-x divide-border">
        {/* Metrics table */}
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="border-b border-border">
                <th className="px-4 py-2 text-left text-slate-500">Metric</th>
                {modes.map((m) => (
                  <th key={m} className="px-4 py-2 text-right">
                    <span style={{ color: MODE_COLORS[m] }}>{modeComparison[m].label}</span>
                    {m === activeMode && (
                      <div className="text-[9px] text-slate-500 font-normal">active</div>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri} className="border-b border-border/40 hover:bg-surface-3">
                  <td className="px-4 py-1.5 text-slate-400">{row.label}</td>
                  {modes.map((m) => {
                    const entry = modeComparison[m];
                    const txt   = row.fmt(entry);
                    // Colour degradation rows
                    let cls = "text-slate-300";
                    if (row.label === "Avg Deg (Obj)") {
                      const v = entry.avg_degradation_obj;
                      cls = v == null ? "text-slate-500" : v < 0 ? "text-red-400" : "text-emerald-400";
                    }
                    if (row.label === "OOS Max DD") {
                      const v = entry.oos_mdd_abs;
                      cls = v == null ? "text-slate-500" : v > 0.2 ? "text-red-400" : "text-emerald-400";
                    }
                    return (
                      <td key={m} className={`px-4 py-1.5 text-right ${cls}`}>
                        {txt}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Objective degradation by mode (mini bar chart) */}
        <div className="p-4">
          <p className="text-xs font-semibold text-slate-300 mb-1">
            Objective Degradation by Mode
          </p>
          <p className="text-[10px] text-slate-500 mb-3">
            Avg (OOS − IS) objective per window. Red = overfitting.
          </p>
          {degBars.length > 0 ? (
            <ResponsiveContainer width="100%" height={120}>
              <BarChart
                data={degBars}
                layout="vertical"
                margin={{ top: 0, right: 20, bottom: 0, left: 90 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" horizontal={false} />
                <XAxis
                  type="number"
                  tick={{ fontSize: 10, fill: "#64748b" }}
                  tickFormatter={(v) => v.toFixed(3)}
                />
                <YAxis
                  type="category"
                  dataKey="label"
                  width={90}
                  tick={{ fontSize: 10, fill: "#94a3b8" }}
                />
                <Tooltip
                  contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", fontSize: 11 }}
                  formatter={(v: number) => [`${v > 0 ? "+" : ""}${v.toFixed(4)}`, "Avg deg"]}
                />
                <ReferenceLine x={0} stroke="#475569" strokeWidth={1.5} />
                <Bar dataKey="value" radius={[0, 3, 3, 0]}>
                  {degBars.map((b, i) => (
                    <Cell
                      key={i}
                      fill={(b.value ?? 0) >= 0 ? "#34d399" : "#f87171"}
                      fillOpacity={b.mode === activeMode ? 1.0 : 0.55}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-slate-600 text-xs">No data</p>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section: Summary comparison
// ---------------------------------------------------------------------------

interface MetricRow {
  label:  string;
  isKey:  (m: WFMetrics) => number | null;
  oosKey: (m: WFMetrics) => number | null;
  fmt:    (v: number | null) => string;
  degKey: keyof WFDegradation | null;
  mddLike?: boolean;
}

const METRIC_ROWS: MetricRow[] = [
  {
    label: "Trades",
    isKey:  (m) => m.n_trades,
    oosKey: (m) => m.n_trades,
    fmt:    (v) => v == null ? "—" : String(v),
    degKey: "n_trades",
  },
  {
    label: "Win Rate",
    isKey:  (m) => m.win_rate,
    oosKey: (m) => m.win_rate,
    fmt:    pct,
    degKey: "win_rate",
  },
  {
    label: "Avg Return",
    isKey:  (m) => m.avg_return,
    oosKey: (m) => m.avg_return,
    fmt:    pct,
    degKey: "avg_return",
  },
  {
    label: "Med Return",
    isKey:  (m) => m.median_return,
    oosKey: (m) => m.median_return,
    fmt:    pct,
    degKey: null,
  },
  {
    label: "Prem Capture",
    isKey:  (m) => m.premium_capture_rate,
    oosKey: (m) => m.premium_capture_rate,
    fmt:    pct,
    degKey: "premium_capture_rate",
  },
  {
    label: "Profit Factor",
    isKey:  (m) => m.profit_factor,
    oosKey: (m) => m.profit_factor,
    fmt:    fix2,
    degKey: "profit_factor",
  },
  {
    label: "Sharpe",
    isKey:  (m) => m.sharpe,
    oosKey: (m) => m.sharpe,
    fmt:    fix2,
    degKey: "sharpe",
  },
  {
    label: "Max Drawdown",
    isKey:  (m) => absV(m.max_drawdown),
    oosKey: (m) => absV(m.max_drawdown),
    fmt:    pct,
    degKey: "max_drawdown_abs",
    mddLike: true,
  },
];

function SummaryComparison({ summary }: { summary: WFSummary }) {
  const { in_sample_pooled, oos_pooled, current_oos_pooled, avg_degradation,
          in_sample_pooled_obj, oos_pooled_obj, current_oos_pooled_obj } = summary;

  return (
    <div className="rounded-xl border border-border bg-surface-2 overflow-hidden">
      <div className="px-4 pt-4 pb-2 border-b border-border">
        <h2 className="text-sm font-bold text-slate-100">
          Summary: In-Sample vs Out-of-Sample
        </h2>
        <p className="text-xs text-slate-500 mt-0.5">
          Pooled across all {summary.n_windows} windows.
          Degradation = OOS − in-sample (negative = worse out-of-sample).
          {summary.avg_weight_turnover != null && (
            <span> · Avg weight turnover: <strong className="text-slate-300">{summary.avg_weight_turnover}</strong> L1 pts/window</span>
          )}
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="border-b border-border">
              <th className="px-4 py-2.5 text-left text-slate-500">Metric</th>
              <th className="px-4 py-2.5 text-right">
                <span className="text-violet-400">In-Sample</span>
                <div className="text-[10px] text-slate-500 font-normal">(train windows)</div>
              </th>
              <th className="px-4 py-2.5 text-right">
                <span className="text-emerald-400">Walk-Fwd OOS</span>
                <div className="text-[10px] text-slate-500 font-normal">(test windows)</div>
              </th>
              <th className="px-4 py-2.5 text-right">
                <span style={{ color: CURRENT_COLOR }}>Current OOS</span>
                <div className="text-[10px] text-slate-500 font-normal">(hand-built)</div>
              </th>
              <th className="px-4 py-2.5 text-right text-slate-500">
                Avg Deg
                <div className="text-[10px] font-normal">(OOS−IS / window)</div>
              </th>
            </tr>
          </thead>
          <tbody>
            {/* Objective row — highlighted */}
            <tr className="border-b border-border bg-slate-900/60">
              <td className="px-4 py-2.5 text-slate-300 font-semibold">Objective</td>
              <td className="px-4 py-2.5 text-right text-violet-300 font-semibold">
                {in_sample_pooled_obj.toFixed(4)}
              </td>
              <td className="px-4 py-2.5 text-right text-emerald-300 font-semibold">
                {oos_pooled_obj.toFixed(4)}
              </td>
              <td className="px-4 py-2.5 text-right font-semibold" style={{ color: CURRENT_COLOR }}>
                {current_oos_pooled_obj.toFixed(4)}
              </td>
              <td className="px-4 py-2.5 text-right">
                {(() => {
                  const v = avg_degradation.objective;
                  const { color, prefix } = degStyle(v, false);
                  return (
                    <span className={`font-bold ${color}`}>
                      {v == null ? "—" : `${prefix}${v.toFixed(4)}`}
                    </span>
                  );
                })()}
              </td>
            </tr>

            {METRIC_ROWS.map((row) => {
              const isVal  = row.isKey(in_sample_pooled);
              const oosVal = row.oosKey(oos_pooled);
              const curVal = row.oosKey(current_oos_pooled);
              const degVal = row.degKey
                ? (avg_degradation as Record<string, number | null>)[row.degKey] as number | null
                : null;
              const { color, prefix } = degStyle(degVal, row.mddLike);
              return (
                <tr key={row.label} className="border-b border-border/40 hover:bg-surface-3">
                  <td className="px-4 py-2 text-slate-400">{row.label}</td>
                  <td className="px-4 py-2 text-right text-violet-300/80">{row.fmt(isVal)}</td>
                  <td className="px-4 py-2 text-right text-emerald-300/80">{row.fmt(oosVal)}</td>
                  <td className="px-4 py-2 text-right text-slate-400">{row.fmt(curVal)}</td>
                  <td className={`px-4 py-2 text-right ${color}`}>
                    {degVal == null
                      ? "—"
                      : `${prefix}${
                          row.label === "Trades"
                            ? String(Math.round(degVal))
                            : row.label === "Profit Factor" || row.label === "Sharpe"
                              ? degVal.toFixed(3)
                              : degVal.toFixed(4)
                        }`}
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
// Section: Degradation bar chart (per metric, active mode)
// ---------------------------------------------------------------------------

function DegradationChart({ summary }: { summary: WFSummary }) {
  const deg = summary.avg_degradation;

  const bars = [
    { label: "Objective",    value: deg.objective,            inv: false },
    { label: "Win Rate",     value: deg.win_rate != null      ? deg.win_rate * 100 : null,              inv: false },
    { label: "Prem Capture", value: deg.premium_capture_rate != null ? deg.premium_capture_rate * 100 : null, inv: false },
    { label: "Profit Factor",value: deg.profit_factor,        inv: false },
    { label: "Sharpe",       value: deg.sharpe,               inv: false },
    { label: "MDD Δ",        value: deg.max_drawdown_abs != null ? deg.max_drawdown_abs * 100 : null,   inv: true },
  ].filter((b) => b.value != null);

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <h3 className="text-sm font-semibold text-slate-200 mb-1">
        Average Degradation per Window — OOS minus In-Sample (active mode)
      </h3>
      <p className="text-xs text-slate-500 mb-3">
        Red bars = OOS performs worse than in-sample (overfitting signal).
        Green = OOS outperforms (possibly noise with small samples).
      </p>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart
          data={bars}
          layout="vertical"
          margin={{ top: 0, right: 24, bottom: 0, left: 80 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" horizontal={false} />
          <XAxis
            type="number"
            tick={{ fontSize: 10, fill: "#64748b" }}
            tickFormatter={(v) => v.toFixed(2)}
          />
          <YAxis type="category" dataKey="label" width={80}
            tick={{ fontSize: 11, fill: "#94a3b8" }} />
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", fontSize: 11 }}
            formatter={(v: number) => [`${v > 0 ? "+" : ""}${v.toFixed(3)}`, "Avg deg"]}
          />
          <ReferenceLine x={0} stroke="#475569" strokeWidth={1.5} />
          <Bar dataKey="value" radius={[0, 3, 3, 0]}>
            {bars.map((b, i) => {
              const isGood = b.inv ? (b.value ?? 0) <= 0 : (b.value ?? 0) >= 0;
              return <Cell key={i} fill={isGood ? "#34d399" : "#f87171"} fillOpacity={0.8} />;
            })}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section: OOS equity chart
// ---------------------------------------------------------------------------

function OOSEquityChart({
  oos,
  current,
  is: inSample,
}: {
  oos:       WFEquityPoint[];
  current:   WFEquityPoint[];
  is:        WFEquityPoint[];
}) {
  const maxLen = Math.max(oos.length, current.length, inSample.length);
  const chartData = Array.from({ length: maxLen }, (_, i) => ({
    trade_n: i + 1,
    oos:     oos[i]?.equity      ?? null,
    current: current[i]?.equity  ?? null,
    is:      inSample[i]?.equity ?? null,
  }));

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <h3 className="text-sm font-semibold text-slate-200 mb-1">
        Equity Curves — Walk-Forward OOS vs Current OOS vs In-Sample
      </h3>
      <p className="text-xs text-slate-500 mb-3">
        OOS uses the active mode's optimised weights from each window's train period.
        Gap between IS and OOS curves visualises degradation.
      </p>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis dataKey="trade_n" tick={{ fontSize: 10, fill: "#64748b" }}
            label={{ value: "Trade #", position: "insideBottom", offset: -2, fontSize: 10, fill: "#64748b" }} />
          <YAxis
            tickFormatter={(v) => `${((v - 1) * 100).toFixed(0)}%`}
            tick={{ fontSize: 10, fill: "#64748b" }}
          />
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", fontSize: 11 }}
            formatter={(v: number, name: string) => {
              const label = name === "oos" ? "Walk-Fwd OOS"
                : name === "current" ? "Current OOS" : "In-Sample";
              return [`${((v - 1) * 100).toFixed(2)}%`, label];
            }}
          />
          <Line type="stepAfter" dataKey="is"      stroke={IS_COLOR}      dot={false} strokeWidth={1.5} strokeDasharray="5 3" connectNulls />
          <Line type="stepAfter" dataKey="oos"     stroke={OOS_COLOR}     dot={false} strokeWidth={2}   connectNulls />
          <Line type="stepAfter" dataKey="current" stroke={CURRENT_COLOR} dot={false} strokeWidth={1.5} strokeDasharray="3 2" connectNulls />
          <ReferenceLine y={1} stroke="#334155" strokeDasharray="2 2" />
        </LineChart>
      </ResponsiveContainer>
      <div className="flex flex-wrap gap-4 mt-2 text-xs">
        {[
          { color: IS_COLOR,      label: "In-Sample (train windows)", dashed: true },
          { color: OOS_COLOR,     label: "Walk-Fwd OOS", dashed: false },
          { color: CURRENT_COLOR, label: "Current OOS", dashed: true },
        ].map((l) => (
          <span key={l.label} className="flex items-center gap-1.5">
            <span className={`w-5 h-0.5 inline-block ${l.dashed ? "opacity-60" : ""}`}
              style={{ background: l.color }} />
            <span className="text-slate-400">{l.label}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section: Weight turnover chart
// ---------------------------------------------------------------------------

function WeightTurnoverChart({ windows }: { windows: WFWindow[] }) {
  const data = windows.map((w) => ({
    label:    `W${w.window_id}`,
    turnover: w.weight_turnover,
    isFirst:  w.window_id === 1,
  }));

  const avg = windows.length > 1
    ? windows.slice(1).reduce((s, w) => s + w.weight_turnover, 0) / (windows.length - 1)
    : 0;

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <h3 className="text-sm font-semibold text-slate-200 mb-1">
        Weight Turnover per Window
      </h3>
      <p className="text-xs text-slate-500 mb-3">
        L1 weight change between consecutive window optima (active mode).
        High turnover = grid search finds very different weights each window = instability.
        {avg > 0 && (
          <> Avg: <strong className="text-slate-300">{avg.toFixed(1)}</strong> pts (excl. first window)</>
        )}
      </p>
      <ResponsiveContainer width="100%" height={140}>
        <BarChart
          data={data}
          margin={{ top: 4, right: 12, bottom: 0, left: 0 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: "#64748b" }} />
          <YAxis tick={{ fontSize: 10, fill: "#64748b" }}
            label={{ value: "L1 Δ", angle: -90, position: "insideLeft", fontSize: 9, fill: "#64748b", dy: 20 }} />
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", fontSize: 11 }}
            formatter={(v: number) => [`${v.toFixed(1)} pts`, "Weight turnover"]}
          />
          {avg > 0 && (
            <ReferenceLine y={avg} stroke="#f59e0b" strokeDasharray="4 2"
              label={{ value: "avg", position: "right", fontSize: 9, fill: "#f59e0b" }} />
          )}
          <Bar dataKey="turnover" radius={[3, 3, 0, 0]}>
            {data.map((d, i) => (
              <Cell
                key={i}
                fill={d.isFirst ? "#334155" : d.turnover > avg * 1.5 ? "#f87171" : "#60a5fa"}
                fillOpacity={d.isFirst ? 0.4 : 0.8}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <p className="text-[10px] text-slate-600 mt-1">
        First window has 0 turnover by definition (no prior window to compare).
        <span className="ml-2 text-[10px]">
          <span className="inline-block w-2 h-2 rounded-sm mr-0.5" style={{ background: "#f87171" }} />High turnover
          <span className="inline-block w-2 h-2 rounded-sm ml-2 mr-0.5" style={{ background: "#60a5fa" }} />Normal
        </span>
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section: Per-window table
// ---------------------------------------------------------------------------

function WindowTable({ windows }: { windows: WFWindow[] }) {
  const [expanded, setExpanded] = useState<number | null>(null);

  return (
    <div className="rounded-xl border border-border bg-surface-2 overflow-x-auto">
      <div className="px-4 pt-4 pb-2 border-b border-border">
        <h3 className="text-sm font-semibold text-slate-200">Per-Window Detail</h3>
        <p className="text-xs text-slate-500">Click a row to expand weights and full metrics.</p>
      </div>
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="border-b border-border text-slate-500">
            {[
              "#", "Train", "Test",
              "Cands (tr/te)",
              "IS Trades", "IS PCR", "IS Obj",
              "OOS Trades", "OOS PCR", "OOS Obj",
              "Cur OOS Obj",
              "Deg Obj", "Deg PCR", "Deg PF", "Deg Sharpe",
              "Wt Δ",
            ].map((h) => (
              <th key={h} className="px-3 py-2 text-right first:text-left whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {windows.map((w) => {
            const deg     = w.degradation;
            const tr      = w.train_metrics;
            const te      = w.test_metrics;
            const isOpen  = expanded === w.window_id;
            const degObjS = degStyle(deg.objective, false);
            const degPCRS = degStyle(deg.premium_capture_rate, false);
            const degPFS  = degStyle(deg.profit_factor, false);
            const degShS  = degStyle(deg.sharpe, false);

            return (
              <>
                <tr
                  key={`row-${w.window_id}`}
                  className={`border-b border-border/40 cursor-pointer
                    ${isOpen ? "bg-slate-900/60" : "hover:bg-surface-3"}`}
                  onClick={() => setExpanded(isOpen ? null : w.window_id)}
                >
                  <td className="px-3 py-2 text-left text-slate-400 font-semibold">
                    {isOpen ? "▾" : "▸"} {w.window_id}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-400 whitespace-nowrap">
                    {w.train_period.start}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-400 whitespace-nowrap">
                    {w.test_period.start}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-500">
                    {w.n_train_candidates}/{w.n_test_candidates}
                  </td>
                  <td className="px-3 py-2 text-right text-violet-300/80">{tr.n_trades}</td>
                  <td className="px-3 py-2 text-right text-violet-300/80">{pct(tr.premium_capture_rate)}</td>
                  <td className="px-3 py-2 text-right text-violet-300/80">{fix4(w.train_objective)}</td>
                  <td className="px-3 py-2 text-right text-emerald-300/80">{te.n_trades}</td>
                  <td className="px-3 py-2 text-right text-emerald-300/80">{pct(te.premium_capture_rate)}</td>
                  <td className="px-3 py-2 text-right text-emerald-300/80">{fix4(w.test_objective)}</td>
                  <td className="px-3 py-2 text-right" style={{ color: CURRENT_COLOR }}>
                    {fix4(w.current_test_objective)}
                  </td>
                  <td className={`px-3 py-2 text-right font-semibold ${degObjS.color}`}>
                    {deg.objective == null ? "—" : `${degObjS.prefix}${deg.objective.toFixed(3)}`}
                  </td>
                  <td className={`px-3 py-2 text-right ${degPCRS.color}`}>
                    {deg.premium_capture_rate == null ? "—"
                      : `${degPCRS.prefix}${(deg.premium_capture_rate * 100).toFixed(1)}%`}
                  </td>
                  <td className={`px-3 py-2 text-right ${degPFS.color}`}>
                    {deg.profit_factor == null ? "—"
                      : `${degPFS.prefix}${deg.profit_factor.toFixed(2)}`}
                  </td>
                  <td className={`px-3 py-2 text-right ${degShS.color}`}>
                    {deg.sharpe == null ? "—" : `${degShS.prefix}${deg.sharpe.toFixed(2)}`}
                  </td>
                  <td className="px-3 py-2 text-right text-slate-400">
                    {w.window_id === 1 ? <span className="text-slate-600">—</span>
                      : <span className={w.weight_turnover > 60 ? "text-red-400" : "text-slate-300"}>
                          {w.weight_turnover}
                        </span>
                    }
                  </td>
                </tr>

                {isOpen && (
                  <tr key={`expand-${w.window_id}`} className="border-b border-border bg-slate-950/60">
                    <td colSpan={16} className="px-4 py-3">
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        {/* Weights */}
                        <div>
                          <p className="text-[11px] text-slate-400 font-semibold mb-2">
                            Best weights (threshold ≥ {w.best_threshold}) · turnover {w.window_id === 1 ? "—" : w.weight_turnover}
                          </p>
                          <div className="flex flex-wrap gap-1">
                            {Object.entries(w.best_weights).map(([k, v]) => (
                              <span
                                key={k}
                                className="px-2 py-0.5 rounded border border-slate-700 text-[10px] font-mono"
                                style={{ background: weightColor(v), color: "#e2e8f0" }}
                              >
                                {COMP_LABELS[k] ?? k}: {v > 0 ? `+${v}` : v}
                              </span>
                            ))}
                          </div>
                        </div>
                        {/* Metric grid */}
                        <div className="grid grid-cols-3 gap-1 text-[11px]">
                          {(["win_rate", "avg_return", "median_return",
                             "premium_capture_rate", "profit_factor", "sharpe"] as const).map((k) => (
                            <div key={k} className="flex flex-col">
                              <span className="text-slate-500 capitalize">{k.replace(/_/g, " ")}</span>
                              <span className="text-violet-300/80">
                                {pct(tr[k as keyof WFMetrics] as number | null)}
                              </span>
                              <span className="text-emerald-300/80">
                                {pct(te[k as keyof WFMetrics] as number | null)}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section: Weight consistency heatmap
// ---------------------------------------------------------------------------

function WeightHeatmap({ windows }: { windows: WFWindow[] }) {
  const components = Object.keys(COMP_LABELS);

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <h3 className="text-sm font-semibold text-slate-200 mb-1">
        Weight Consistency Heatmap
      </h3>
      <p className="text-xs text-slate-500 mb-3">
        Weight selected by the grid search for each component in each window.
        Consistent colours = stable preferences. High variance = instability.
      </p>
      <div className="overflow-x-auto">
        <table className="text-[10px] font-mono">
          <thead>
            <tr>
              <th className="px-2 py-1 text-left text-slate-500 whitespace-nowrap w-16">Window</th>
              {components.map((c) => (
                <th key={c} className="px-2 py-1 text-center text-slate-500 whitespace-nowrap">
                  {COMP_LABELS[c]}
                </th>
              ))}
              <th className="px-2 py-1 text-center text-slate-500">Th</th>
              <th className="px-2 py-1 text-center text-slate-500">Δ</th>
            </tr>
          </thead>
          <tbody>
            {windows.map((w) => (
              <tr key={w.window_id} className="border-t border-border/30">
                <td className="px-2 py-1 text-slate-400 whitespace-nowrap">
                  W{w.window_id} {w.test_period.start.slice(0, 7)}
                </td>
                {components.map((c) => {
                  const v = w.best_weights[c as keyof typeof w.best_weights] ?? 0;
                  return (
                    <td key={c} className="px-2 py-1 text-center" style={{ background: weightColor(v) }}>
                      <span className="text-slate-200">{v > 0 ? `+${v}` : v}</span>
                    </td>
                  );
                })}
                <td className="px-2 py-1 text-center text-slate-300">{w.best_threshold}</td>
                <td className={`px-2 py-1 text-center ${
                  w.window_id === 1 ? "text-slate-600"
                    : w.weight_turnover > 60 ? "text-red-400" : "text-slate-400"
                }`}>
                  {w.window_id === 1 ? "—" : w.weight_turnover}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex gap-4 mt-3 text-[10px] text-slate-500">
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded" style={{ background: weightColor(35) }} />
          Large positive weight
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded" style={{ background: weightColor(-25) }} />
          Large negative weight
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded" style={{ background: weightColor(0) }} />
          Zero weight
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const MODE_LABELS: Record<string, { short: string; full: string; color: string }> = {
  balanced:      { short: "Balanced",   full: "0.40 PCR + 0.35 PF + 0.25 MDD",                  color: "#60a5fa" },
  pcr_stability: { short: "PCR Stable", full: "0.60 PCR + 0.20 PF + 0.20 MDD + dispersion pen", color: "#a78bfa" },
  conservative:  { short: "Conserv.",   full: "0.40 PCR + 0.20 PF + 0.40 MDD",                  color: "#34d399" },
};

export default function WalkForwardPage() {
  const [state, setState] = useState<AsyncState<WalkForwardResult>>({
    data: null, loading: true, error: null,
  });
  const [dataDays,      setDataDays]      = useState(756);
  const [trainDays,     setTrainDays]     = useState(252);
  const [testDays,      setTestDays]      = useState(63);
  const [stepDays,      setStepDays]      = useState(63);
  const [objectiveMode, setObjectiveMode] = useState<string>("balanced");
  const [requireRealIV, setRequireRealIV] = useState(false);

  useEffect(() => {
    setState({ data: null, loading: true, error: null });
    getWalkForward({ dataDays, trainDays, testDays, stepDays, objectiveMode, requireRealIV })
      .then((data) => setState({ data, loading: false, error: null }))
      .catch((e)   => setState({ data: null, loading: false, error: String(e) }));
  }, [dataDays, trainDays, testDays, stepDays, objectiveMode, requireRealIV]);

  const { data, loading, error } = state;
  const nWindows = data?.params.n_windows ?? 0;
  const elapsed  = data?.elapsed_ms;

  return (
    <main className="min-h-screen bg-background text-foreground p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-wrap items-start gap-3 mb-6">
        <div className="flex-1 min-w-0">
          <h1 className="text-xl font-bold text-slate-100">Walk-Forward Validation</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Rolling optimisation — train {trainDays}d · test {testDays}d · step {stepDays}d ·{" "}
            {nWindows > 0 ? `${nWindows} windows` : "…"}
            {elapsed ? ` · ${elapsed} ms` : ""}
            {" · "}
            <span style={{ color: MODE_LABELS[objectiveMode]?.color }}>
              {MODE_LABELS[objectiveMode]?.short}
            </span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          {/* Objective mode selector */}
          <div className="flex rounded-lg border border-border overflow-hidden text-[11px]">
            {(Object.entries(MODE_LABELS) as [string, typeof MODE_LABELS[string]][]).map(([id, info]) => (
              <button
                key={id}
                onClick={() => setObjectiveMode(id)}
                className={`px-3 py-1.5 transition-colors border-r border-border last:border-r-0
                  ${objectiveMode === id
                    ? "bg-slate-800 text-slate-100 font-medium"
                    : "text-slate-400 hover:text-slate-200 hover:bg-surface-3"
                  }`}
                title={info.full}
              >
                <span className="w-1.5 h-1.5 rounded-full inline-block mr-1.5"
                  style={{ background: objectiveMode === id ? info.color : "#475569" }} />
                {info.short}
              </button>
            ))}
          </div>

          {/* Window params */}
          <select value={dataDays} onChange={(e) => setDataDays(Number(e.target.value))}
            className="bg-surface-2 border border-border text-slate-300 text-xs rounded px-2 py-1">
            {[400, 504, 756, 1008, 1260].map((d) => (
              <option key={d} value={d}>{d}d data</option>
            ))}
          </select>
          <select value={trainDays} onChange={(e) => setTrainDays(Number(e.target.value))}
            className="bg-surface-2 border border-border text-slate-300 text-xs rounded px-2 py-1">
            {[126, 189, 252, 315].map((d) => (
              <option key={d} value={d}>train {d}d</option>
            ))}
          </select>
          <select value={testDays} onChange={(e) => setTestDays(Number(e.target.value))}
            className="bg-surface-2 border border-border text-slate-300 text-xs rounded px-2 py-1">
            {[21, 42, 63].map((d) => (
              <option key={d} value={d}>test {d}d</option>
            ))}
          </select>

          {/* Nav */}
          <Link href="/backtest/optimize"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1
              text-[10px] font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors">
            ← Optimise
          </Link>
          <Link href="/backtest"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1
              text-[10px] font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors">
            ← Backtest
          </Link>

          {/* Real-IV toggle */}
          <label className="shrink-0 flex items-center gap-2 cursor-pointer rounded-full border border-border bg-surface-3 px-3 py-1 text-[10px] font-medium text-slate-400 hover:border-slate-500 hover:text-slate-200 transition-colors select-none">
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
        </div>
      </div>

      {loading && (
        <div className="flex flex-col items-center justify-center py-24 gap-3">
          <div className="w-8 h-8 border-2 border-violet-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-slate-400 text-sm">
            Running walk-forward grid search across {nWindows || "…"} windows (3 modes)…
          </p>
        </div>
      )}

      {error && !loading && (
        <div className="rounded-xl border border-red-800 bg-red-900/20 p-6 text-red-300 text-sm">
          {error}
        </div>
      )}

      {data?.error && (
        <div className="rounded-xl border border-amber-800 bg-amber-900/20 p-4 text-amber-300 text-sm">
          {data.error}
        </div>
      )}

      {data && !loading && !data.error && data.summary && (
        <div className="flex flex-col gap-6">

          {/* Real-IV filter summary */}
          <RealIVFilterBanner filter={data.real_iv_filter} />

          {/* IV data-quality gate */}
          <IVQualityBadge gate={data.iv_quality} />

          {/* 1 — Mode comparison (all 3 modes side-by-side) */}
          {data.mode_comparison && (
            <ModeComparisonPanel
              modeComparison={data.mode_comparison}
              activeMode={data.objective_mode}
              modeDefinitions={data.mode_definitions ?? {}}
            />
          )}

          {/* 2 — Summary comparison (active mode hero) */}
          <SummaryComparison summary={data.summary} />

          {/* 3 — Degradation bar chart */}
          <DegradationChart summary={data.summary} />

          {/* 4 — OOS equity curve */}
          <OOSEquityChart
            oos={data.oos_equity}
            current={data.current_oos_equity}
            is={data.is_equity}
          />

          {/* 5 — Weight turnover */}
          {data.windows.length > 1 && (
            <WeightTurnoverChart windows={data.windows} />
          )}

          {/* 6 — Per-window table */}
          {data.windows.length > 0 && (
            <WindowTable windows={data.windows} />
          )}

          {/* 7 — Weight heatmap */}
          {data.windows.length > 0 && (
            <WeightHeatmap windows={data.windows} />
          )}
        </div>
      )}
    </main>
  );
}
