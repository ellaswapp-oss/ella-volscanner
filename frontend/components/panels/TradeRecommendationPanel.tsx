"use client";

import { useState } from "react";
import { Card, CardHeader, CardBody } from "@/components/Card";
import { ScoreGauge } from "@/components/ScoreGauge";
import { RegimeBadge } from "@/components/RegimeBadge";
import type { TickerSnapshot } from "@/lib/types";
import { clsx } from "clsx";

// ---------------------------------------------------------------------------
// Normalise raw factor values → 0-100 (mirrors backend market.py logic)
// ---------------------------------------------------------------------------
function normIVRank(v: number)  { return Math.min(Math.max(v, 0), 100); }
function normVRP(v: number)     { return Math.min(Math.max((v + 0.5) / 1.0 * 100, 0), 100); }
function normRatio(v: number)   { return Math.min(Math.max((v - 0.5) / 1.5 * 100, 0), 100); }
function normZ(v: number)       { return Math.min(Math.max((v + 3.0) / 6.0 * 100, 0), 100); }

// ---------------------------------------------------------------------------
// Playbook per signal
// ---------------------------------------------------------------------------
const PLAYBOOK: Record<string, string[]> = {
  BUY_PREMIUM: [
    "Long straddles / strangles before catalysts",
    "Buy ATM options — own gamma, not theta",
    "Calendar spreads (buy front, sell back)",
    "Debit spreads for directional exposure",
  ],
  NEUTRAL: [
    "Iron condors with wider-than-normal wings",
    "Reduce size until signal clarifies",
    "Ratio spreads with defined risk",
    "Avoid naked short positions",
  ],
  SELL_SELECTIVE: [
    "Cash-secured puts on quality names at support",
    "Covered calls at near-term resistance",
    "Iron condors on low-beta, range-bound names",
    "Short strangles 30–45 DTE, strict delta limits",
  ],
  SELL_AGGRESSIVE: [
    "Short strangles across the portfolio",
    "Large iron condors / iron butterflies",
    "Sell premium across the full term structure",
    "Maintain delta-neutral book, strict size limits",
  ],
};

// ---------------------------------------------------------------------------
// Factor row — label, value, normalised 0-100 bar, weight
// ---------------------------------------------------------------------------
function FactorBar({
  label,
  rawValue,
  normValue,
  weight,
  valueFmt,
}: {
  label: string;
  rawValue: number;
  normValue: number;
  weight: number;
  valueFmt: string;
}) {
  const barColor =
    normValue >= 70 ? "bg-vol-red"
    : normValue >= 50 ? "bg-vol-orange"
    : normValue >= 30 ? "bg-vol-yellow"
    : "bg-vol-green";

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-slate-400">{label}</span>
        <div className="flex items-center gap-2">
          <span className="font-mono text-white">{valueFmt}</span>
          <span className="text-slate-600">{Math.round(normValue)}/100</span>
          <span className="w-10 text-right text-slate-600">{(weight * 100).toFixed(0)}% wt</span>
        </div>
      </div>
      <div className="h-1.5 w-full rounded-full bg-surface-3 overflow-hidden">
        <div
          className={clsx("h-full rounded-full transition-all duration-700", barColor)}
          style={{ width: `${normValue}%` }}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Factor breakdown for one ticker
// ---------------------------------------------------------------------------
function FactorBreakdown({ t }: { t: TickerSnapshot }) {
  const ivr = t.iv_rank;
  const vrp = t.vrp;
  const ratio = t.iv_rv_ratio;
  const z = t.iv_rv_zscore;

  const vrpSign = vrp >= 0 ? "+" : "";
  const zSign   = z >= 0   ? "+" : "";

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between mb-1">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
          Factor Breakdown — {t.ticker}
        </p>
        <RegimeBadge signal={t.signal} />
      </div>

      <FactorBar
        label="IV Rank"
        rawValue={ivr}
        normValue={normIVRank(ivr)}
        weight={0.35}
        valueFmt={`${ivr.toFixed(1)}`}
      />
      <FactorBar
        label="Vol Risk Premium"
        rawValue={vrp}
        normValue={normVRP(vrp)}
        weight={0.25}
        valueFmt={`${vrpSign}${(vrp * 100).toFixed(1)}%`}
      />
      <FactorBar
        label="IV / RV Ratio"
        rawValue={ratio}
        normValue={normRatio(ratio)}
        weight={0.25}
        valueFmt={`${ratio.toFixed(2)}×`}
      />
      <FactorBar
        label="IV/RV Z-Score"
        rawValue={z}
        normValue={normZ(z)}
        weight={0.15}
        valueFmt={`${zSign}${z.toFixed(2)}`}
      />

      {/* Weight legend */}
      <p className="text-[9px] text-slate-700 pt-1">
        Weights: IVR 35% · VRP 25% · Ratio 25% · Z 15%
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sorted ticker row
// ---------------------------------------------------------------------------
function TickerRow({ t, active, onClick }: { t: TickerSnapshot; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "w-full flex items-center gap-3 rounded-lg px-2 py-2 text-left transition-colors",
        active ? "bg-surface-3 ring-1 ring-border" : "hover:bg-surface-3/50"
      )}
    >
      <ScoreGauge score={t.regime_score} size={44} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold text-white">{t.ticker}</span>
          <span className="font-mono text-[10px] text-slate-500">
            ${t.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </span>
        </div>
        <RegimeBadge signal={t.signal} />
      </div>
      <div className="shrink-0 text-right">
        <p className="font-mono text-xs font-semibold text-white">{t.regime_score.toFixed(1)}</p>
        <p className="text-[10px] text-slate-500">
          IVR {t.iv_rank.toFixed(0)}
        </p>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Playbook section
// ---------------------------------------------------------------------------
function PlaybookSection({ t }: { t: TickerSnapshot }) {
  const items = PLAYBOOK[t.signal.signal] ?? PLAYBOOK.NEUTRAL;
  return (
    <div className="rounded-lg border border-border bg-surface-3 p-3 space-y-2">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
        Playbook — {t.signal.label}
      </p>
      <p className="text-[11px] text-slate-400 italic">{t.signal.description}</p>
      <ul className="space-y-1.5">
        {items.map((s, i) => (
          <li key={i} className="flex items-start gap-2 text-xs text-slate-300">
            <span className="mt-0.5 shrink-0 text-slate-500">›</span>
            {s}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel — client component for interactive ticker selection
// ---------------------------------------------------------------------------
export function TradeRecommendationPanel({ tickers }: { tickers: TickerSnapshot[] }) {
  const sorted = [...tickers]
    .filter((t) => !t.error)
    .sort((a, b) => b.regime_score - a.regime_score);

  const [selectedIdx, setSelectedIdx] = useState(0);
  const selected = sorted[selectedIdx] ?? sorted[0];

  return (
    <Card>
      <CardHeader
        title="Trade Recommendations"
        subtitle="Click a ticker to see its factor breakdown"
      />
      <CardBody>
        <div className="grid gap-4 lg:grid-cols-5">
          {/* Ticker list — 2 cols */}
          <div className="lg:col-span-2 space-y-1">
            {sorted.map((t, i) => (
              <TickerRow
                key={t.ticker}
                t={t}
                active={i === selectedIdx}
                onClick={() => setSelectedIdx(i)}
              />
            ))}
          </div>

          {/* Factor breakdown + playbook — 3 cols */}
          <div className="lg:col-span-3 space-y-4">
            {selected && <FactorBreakdown t={selected} />}
            {selected && <PlaybookSection t={selected} />}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
