"use client";

/**
 * IVQualityBadge
 * ---------------
 * Reusable data-quality indicator for all backtest pages.
 *
 * Shows:
 *   • A coloured GOOD / MIXED / POOR status pill
 *   • A tooltip row with real / synthetic / total counts
 *   • An amber warning banner when status is MIXED or POOR
 *
 * Usage
 * -----
 *   import IVQualityBadge from "@/components/dashboard/IVQualityBadge";
 *   <IVQualityBadge gate={data.iv_quality} />
 */

import React from "react";
import type { IVQualityGate } from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATUS_STYLES: Record<IVQualityGate["status"], string> = {
  GOOD:  "bg-green-500/15  text-green-400  border-green-500/30",
  MIXED: "bg-amber-500/15  text-amber-400  border-amber-500/30",
  POOR:  "bg-red-500/15    text-red-400    border-red-500/30",
};

const STATUS_ICON: Record<IVQualityGate["status"], string> = {
  GOOD:  "✓",
  MIXED: "⚠",
  POOR:  "✕",
};

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface Props {
  gate: IVQualityGate | undefined | null;
  /** Suppress the warning banner (e.g. already shown elsewhere). Default false */
  hideWarning?: boolean;
  className?: string;
}

export default function IVQualityBadge({ gate, hideWarning = false, className = "" }: Props) {
  if (!gate) return null;

  const { status, real, synthetic_fallback, interpolated, total, real_iv_pct, warning } = gate;

  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      {/* ── Status pill + breakdown ─────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3">
        {/* Pill */}
        <span
          className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold tracking-wide ${STATUS_STYLES[status]}`}
        >
          <span className="text-[10px]">{STATUS_ICON[status]}</span>
          Data Quality: {status}
        </span>

        {/* Breakdown chips */}
        <span className="flex gap-1.5 text-[11px] text-slate-400">
          <span className="rounded bg-green-400/10 px-1.5 py-0.5 text-green-400">
            {real.toLocaleString()} real ({pct(real_iv_pct)})
          </span>
          {interpolated > 0 && (
            <span className="rounded bg-yellow-400/10 px-1.5 py-0.5 text-yellow-400">
              {interpolated.toLocaleString()} interpolated
            </span>
          )}
          <span className="rounded bg-slate-700/60 px-1.5 py-0.5 text-slate-400">
            {synthetic_fallback.toLocaleString()} synthetic
          </span>
          <span className="rounded bg-slate-700/40 px-1.5 py-0.5 text-slate-500">
            {total.toLocaleString()} total
          </span>
        </span>
      </div>

      {/* ── Warning banner ───────────────────────────────────────────────── */}
      {!hideWarning && warning && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-400">
          <span className="mt-0.5 shrink-0">⚠</span>
          <span>{warning}</span>
        </div>
      )}
    </div>
  );
}
