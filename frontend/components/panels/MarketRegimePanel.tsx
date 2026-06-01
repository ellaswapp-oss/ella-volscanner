"use client";

import { Card, CardHeader, CardBody } from "@/components/Card";
import { ScoreGauge } from "@/components/ScoreGauge";
import { RegimeBadge } from "@/components/RegimeBadge";
import type { DashboardData, TickerSnapshot, Signal } from "@/lib/types";
import { clsx } from "clsx";

// Short labels that fit in a narrow column
const SHORT_LABEL: Record<string, string> = {
  BUY_PREMIUM:     "Buy Vol",
  NEUTRAL:         "Neutral",
  SELL_SELECTIVE:  "Sell Sel.",
  SELL_AGGRESSIVE: "Sell Agg.",
};

function SignalChip({ signal }: { signal: Signal }) {
  const colorCls = {
    green:  "bg-vol-green/10  text-vol-green  border-vol-green/30",
    yellow: "bg-vol-yellow/10 text-vol-yellow border-vol-yellow/30",
    orange: "bg-vol-orange/10 text-vol-orange border-vol-orange/30",
    red:    "bg-vol-red/10    text-vol-red    border-vol-red/30",
  }[signal.color];

  return (
    <span className={clsx("rounded border px-1.5 py-0.5 text-[10px] font-semibold whitespace-nowrap", colorCls)}>
      {SHORT_LABEL[signal.signal] ?? signal.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Score bar — coloured progress bar matching regime zone
// ---------------------------------------------------------------------------
function ScoreBar({ score }: { score: number }) {
  const color =
    score < 30 ? "bg-vol-green"
    : score < 50 ? "bg-vol-yellow"
    : score < 70 ? "bg-vol-orange"
    : "bg-vol-red";

  return (
    <div className="h-1.5 w-full rounded-full bg-surface-3 overflow-hidden">
      <div
        className={clsx("h-full rounded-full transition-all duration-700", color)}
        style={{ width: `${score}%` }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single ticker row in the sorted list
// ---------------------------------------------------------------------------
function TickerRow({ t }: { t: TickerSnapshot }) {
  const score = Math.round(t.regime_score);
  const ivr   = t.iv_rank.toFixed(0);
  const vrp   = t.vrp >= 0 ? `+${(t.vrp * 100).toFixed(1)}%` : `${(t.vrp * 100).toFixed(1)}%`;

  return (
    <div className="flex items-center gap-3 py-2 border-b border-border last:border-0">
      {/* Ticker */}
      <span className="w-11 shrink-0 text-xs font-bold text-white">{t.ticker}</span>

      {/* Score bar */}
      <div className="flex-1 min-w-0 space-y-0.5">
        <ScoreBar score={score} />
        <div className="flex justify-between">
          <span className="text-[9px] text-slate-600">IVR {ivr}</span>
          <span className="text-[9px] text-slate-600">VRP {vrp}</span>
        </div>
      </div>

      {/* Score number */}
      <span className="w-7 shrink-0 text-right font-mono text-xs font-semibold text-white">
        {score}
      </span>

      {/* Signal chip — abbreviated for narrow column */}
      <SignalChip signal={t.signal} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Market-wide composite indicator
// ---------------------------------------------------------------------------
function CompositeScore({
  score,
  signal,
}: {
  score: number;
  signal: DashboardData["market_signal"];
}) {
  return (
    <div className="flex flex-col items-center gap-2">
      <ScoreGauge score={score} size={110} />
      <div className="text-center space-y-1">
        <RegimeBadge signal={signal} />
        <p className="text-[10px] text-slate-500">SPY · QQQ · IWM composite</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------
export function MarketRegimePanel({ data }: { data: DashboardData }) {
  const tickers = Object.values(data.tickers)
    .filter((t): t is TickerSnapshot => !t.error)
    .sort((a, b) => b.regime_score - a.regime_score);

  return (
    <Card>
      <CardHeader
        title="Market Regime"
        subtitle="Composite volatility environment · higher = better for premium selling"
      />
      <CardBody>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
          {/* Left: composite gauge */}
          <div className="flex shrink-0 justify-center sm:justify-start">
            <CompositeScore
              score={data.market_regime}
              signal={data.market_signal}
            />
          </div>

          {/* Divider */}
          <div className="hidden sm:block w-px bg-border self-stretch" />

          {/* Right: sorted ticker rows */}
          <div className="flex-1 min-w-0">
            {tickers.map((t) => (
              <TickerRow key={t.ticker} t={t} />
            ))}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
