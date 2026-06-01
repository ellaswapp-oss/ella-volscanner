import { clsx } from "clsx";
import { CardShell, CardSkeleton, CardError, Skeleton } from "./CardShell";
import { getSignalStyles, scoreToSignal } from "@/lib/signals";
import type { RegimeResponse } from "@/lib/api";

interface Props {
  data:    RegimeResponse | null;
  loading: boolean;
  error:   string | null;
}

export function RegimeCard({ data, loading, error }: Props) {
  if (loading) return <CardSkeleton rows={4} />;
  if (error)   return <CardError message={error} />;
  if (!data)   return null;

  const styles  = getSignalStyles(data.market_signal.signal);
  const sigKey  = scoreToSignal(data.market_regime);
  const scoreStyles = getSignalStyles(sigKey);

  // Top 3 tickers by score for quick overview
  const top3 = [...data.tickers]
    .sort((a, b) => b.regime_score - a.regime_score)
    .slice(0, 3);

  return (
    <CardShell>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
          Market Regime
        </p>
        <span className={clsx("rounded-full px-2 py-0.5 text-[10px] font-medium", styles.badge)}>
          {data.market_signal.label}
        </span>
      </div>

      {/* Score */}
      <div className="flex items-end gap-3 mb-4">
        <div className={clsx("rounded-xl px-4 py-2", scoreStyles.scoreBg)}>
          <span className={clsx("text-4xl font-bold tabular-nums", scoreStyles.text)}>
            {data.market_regime.toFixed(0)}
          </span>
          <span className="text-slate-500 text-sm ml-1">/100</span>
        </div>
        <div className="pb-1">
          <p className="text-[10px] text-slate-500">VIX</p>
          <p className="text-sm font-semibold text-white">{data.vix.toFixed(1)}</p>
          <p className="text-[10px] text-slate-500">{data.vix_regime}</p>
        </div>
      </div>

      {/* Segmented gauge — 4 color zones with a needle marker */}
      <div className="mb-4">
        {/* Zone bar */}
        <div className="relative h-2.5 w-full rounded-full overflow-hidden flex">
          {/* 0–30 buy */}
          <div className="h-full bg-green-500/35  shrink-0" style={{ width: "30%" }} />
          {/* 30–50 neutral */}
          <div className="h-full bg-yellow-500/35 shrink-0" style={{ width: "20%" }} />
          {/* 50–70 sell selective */}
          <div className="h-full bg-orange-500/35 shrink-0" style={{ width: "20%" }} />
          {/* 70–100 sell aggressive */}
          <div className="h-full bg-red-500/35    shrink-0" style={{ width: "30%" }} />
        </div>

        {/* Needle */}
        <div className="relative" style={{ height: "8px" }}>
          <div
            className={clsx(
              "absolute top-0 -translate-x-1/2 w-0.5 h-3.5 rounded-full transition-all duration-700",
              scoreStyles.dot
            )}
            style={{ left: `${Math.min(Math.max(data.market_regime, 1), 99)}%` }}
          />
        </div>

        {/* Zone labels */}
        <div className="flex justify-between mt-1 text-[9px] text-slate-600">
          <span className="text-green-600/80">Buy Vol</span>
          <span className="text-yellow-600/80">Neutral</span>
          <span className="text-orange-600/80">Sell Sel.</span>
          <span className="text-red-600/80">Sell Agg.</span>
        </div>
      </div>

      {/* Top tickers */}
      <div className="space-y-1.5">
        {top3.map((t) => {
          const ts = getSignalStyles(t.signal.signal);
          return (
            <div key={t.ticker} className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className={clsx("inline-block h-1.5 w-1.5 rounded-full shrink-0", ts.dot)} />
                <span className="text-xs font-semibold text-white">{t.ticker}</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="font-mono text-[10px] text-slate-500">
                  IVR {t.iv_rank.toFixed(0)}
                </span>
                <span className={clsx("font-mono text-xs font-semibold", ts.text)}>
                  {t.regime_score.toFixed(0)}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </CardShell>
  );
}
