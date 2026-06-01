"use client";

import { clsx } from "clsx";
import { CardShell, CardSkeleton, CardError } from "./CardShell";
import { getSignalStyles } from "@/lib/signals";
import type { HistoryResponse, HistoryRow } from "@/lib/api";

interface Props {
  data:    HistoryResponse | null;
  loading: boolean;
  error:   string | null;
  ticker:  string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(v: number | null | undefined, dec = 2, suffix = ""): string {
  if (v == null || !isFinite(v)) return "—";
  return `${v >= 0 ? "" : ""}${v.toFixed(dec)}${suffix}`;
}

function ratioColor(ratio: number | null): string {
  if (ratio == null) return "text-slate-500";
  if (ratio > 1.3) return "text-orange-400";
  if (ratio > 1.1) return "text-yellow-400";
  if (ratio < 0.9) return "text-green-400";
  return "text-slate-300";
}

function zColor(z: number): string {
  if (z > 1.5) return "text-red-400";
  if (z > 0.5) return "text-orange-400";
  if (z < -0.5) return "text-green-400";
  return "text-slate-400";
}

function regimeBar(score: number): string {
  // Returns a Tailwind bg class matching the regime zone
  if (score < 30) return "bg-green-400";
  if (score < 50) return "bg-yellow-400";
  if (score < 70) return "bg-orange-400";
  return "bg-red-400";
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

function TableRow({ row }: { row: HistoryRow }) {
  const styles = getSignalStyles(row.signal);
  const z = row.iv_rv_zscore ?? 0;

  return (
    <tr className="border-b border-border/40 hover:bg-surface-3/30 transition-colors">
      {/* Date */}
      <td className="py-2 pl-3 pr-2 text-[10px] font-mono text-slate-500 whitespace-nowrap">
        {row.date.slice(5)}   {/* MM-DD */}
      </td>

      {/* IV Rank */}
      <td className="py-2 px-2 text-center">
        <span className={clsx(
          "text-xs font-semibold tabular-nums",
          row.iv_rank != null && row.iv_rank > 70 ? "text-red-400" :
          row.iv_rank != null && row.iv_rank > 50 ? "text-orange-400" :
          row.iv_rank != null && row.iv_rank < 30 ? "text-green-400" : "text-slate-300"
        )}>
          {fmt(row.iv_rank, 1)}
        </span>
      </td>

      {/* IV/RV Ratio */}
      <td className="py-2 px-2 text-center">
        <span className={clsx("text-xs font-semibold tabular-nums", ratioColor(row.iv_rv_ratio))}>
          {fmt(row.iv_rv_ratio, 3)}×
        </span>
      </td>

      {/* IV30 */}
      <td className="py-2 px-2 text-center text-xs text-slate-400 tabular-nums">
        {fmt(row.iv30, 1)}%
      </td>

      {/* RV30 */}
      <td className="py-2 px-2 text-center text-xs text-slate-500 tabular-nums">
        {fmt(row.rv30, 1)}%
      </td>

      {/* Z-Score */}
      <td className="py-2 px-2 text-center">
        <span className={clsx("text-xs font-semibold tabular-nums", zColor(z))}>
          {z >= 0 ? "+" : ""}{z.toFixed(2)}σ
        </span>
      </td>

      {/* Regime Score + mini bar */}
      <td className="py-2 px-2">
        <div className="flex items-center gap-1.5 justify-end">
          <div className="h-1 w-10 rounded-full bg-surface-3 overflow-hidden">
            <div
              className={clsx("h-full rounded-full", regimeBar(row.regime_score))}
              style={{ width: `${Math.min(row.regime_score, 100)}%` }}
            />
          </div>
          <span className="text-[10px] font-mono text-slate-400 w-6 text-right shrink-0">
            {row.regime_score.toFixed(0)}
          </span>
        </div>
      </td>

      {/* Signal badge */}
      <td className="py-2 pr-3 pl-2 text-right">
        <span className={clsx("rounded-full px-2 py-0.5 text-[9px] font-semibold whitespace-nowrap", styles.badge)}>
          {styles.shortLabel}
        </span>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function HistoricalTable({ data, loading, error, ticker }: Props) {
  if (loading) return <CardSkeleton rows={5} />;
  if (error)   return <CardError message={error} />;
  if (!data || data.rows.length === 0) {
    return (
      <CardShell>
        <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-2">
          Signal History — {ticker}
        </p>
        <p className="text-xs text-slate-600 py-4 text-center">
          No history yet — run <code className="font-mono text-slate-500">python -m scripts.save_snapshot</code> to seed data.
        </p>
      </CardShell>
    );
  }

  // Show most recent 60 rows (already newest-first from API)
  const rows = data.rows.slice(0, 60);

  return (
    <CardShell>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
          Signal History — {ticker}
        </p>
        <span className="text-[9px] text-slate-600 font-mono">
          {rows.length} days · newest first
        </span>
      </div>

      {/* Scrollable table */}
      <div className="overflow-auto" style={{ maxHeight: "280px" }}>
        <table className="w-full min-w-[500px]">
          <thead className="sticky top-0 bg-surface-2 z-10">
            <tr className="border-b border-border">
              <th className="pb-2 pl-3 pr-2 text-left text-[9px] font-semibold uppercase tracking-widest text-slate-600">
                Date
              </th>
              <th className="pb-2 px-2 text-center text-[9px] font-semibold uppercase tracking-widest text-slate-600">
                IVR
              </th>
              <th className="pb-2 px-2 text-center text-[9px] font-semibold uppercase tracking-widest text-slate-600">
                IV/RV
              </th>
              <th className="pb-2 px-2 text-center text-[9px] font-semibold uppercase tracking-widest text-slate-600">
                IV30
              </th>
              <th className="pb-2 px-2 text-center text-[9px] font-semibold uppercase tracking-widest text-slate-600">
                RV30
              </th>
              <th className="pb-2 px-2 text-center text-[9px] font-semibold uppercase tracking-widest text-slate-600">
                Z-Score
              </th>
              <th className="pb-2 px-2 text-right text-[9px] font-semibold uppercase tracking-widest text-slate-600">
                Regime
              </th>
              <th className="pb-2 pr-3 pl-2 text-right text-[9px] font-semibold uppercase tracking-widest text-slate-600">
                Signal
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <TableRow key={row.date} row={row} />
            ))}
          </tbody>
        </table>
      </div>
    </CardShell>
  );
}
