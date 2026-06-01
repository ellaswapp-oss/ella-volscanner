import { clsx } from "clsx";
import { CardShell, CardSkeleton, CardError } from "./CardShell";
import { getSignalStyles } from "@/lib/signals";
import type { TradeSignalResponse } from "@/lib/api";

interface Props {
  data:    TradeSignalResponse | null;
  loading: boolean;
  error:   string | null;
  ticker:  string;
}

const FACTOR_LABELS: Record<string, string> = {
  iv_rank:     "IV Rank",
  vrp:         "Vol Risk Premium",
  iv_rv_ratio: "IV / RV Ratio",
  z_score:     "IV/RV Z-Score",
};

function FactorRow({
  name, value, weight, contribution, isProxy,
}: {
  name: string; value: number; weight: number; contribution: string; isProxy?: boolean;
}) {
  const pct = Math.round(weight * 100);
  const contribColor =
    contribution === "very_high" ? "text-red-400"    :
    contribution === "high"      ? "text-orange-400"  :
    contribution === "moderate"  ? "text-yellow-400"  : "text-slate-500";

  return (
    <div className="flex items-center justify-between text-[11px]">
      <span className="text-slate-400 truncate mr-2">
        {FACTOR_LABELS[name] ?? name}
        {isProxy && (
          <span className="ml-1.5 rounded px-1 py-0.5 bg-amber-500/10 text-amber-400 text-[9px] font-medium">
            proxy
          </span>
        )}
      </span>
      <div className="flex items-center gap-3 shrink-0">
        <span className={clsx("font-medium", contribColor)}>{contribution}</span>
        <span className="text-slate-600 w-8 text-right">{pct}%</span>
      </div>
    </div>
  );
}

export function TradeCard({ data, loading, error, ticker }: Props) {
  if (loading) return <CardSkeleton rows={5} />;
  if (error)   return <CardError message={error} />;
  if (!data)   return null;

  const styles = getSignalStyles(data.signal.signal);
  const isLive = data.provider === "real";
  const hasProxy = data.iv_rank_is_proxy;
  const macroPen = data.macro_penalties;
  const hasMacroPenalties = isLive && macroPen?.available && macroPen.total_penalty > 0;

  return (
    <CardShell className={clsx("border", styles.border, styles.bg)}>
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-1">
            Trade Recommendation — {ticker}
          </p>
          <p className={clsx("text-lg font-bold", styles.text)}>
            {data.signal.label}
          </p>
          <p className="text-[11px] text-slate-500 mt-0.5 leading-snug max-w-xs">
            {data.signal.description}
          </p>
        </div>
        <div className={clsx(
          "rounded-xl px-3 py-1.5 text-center shrink-0 ml-3",
          styles.scoreBg ?? styles.bg
        )}>
          <p className={clsx("text-2xl font-bold tabular-nums", styles.text)}>
            {data.regime_score.toFixed(0)}
          </p>
          <p className="text-[9px] text-slate-500 uppercase tracking-wide">score</p>
        </div>
      </div>

      {/* Proxy warning — shown when iv_rank_is_proxy (live data) */}
      {hasProxy && (
        <div className="mb-3 rounded-lg bg-amber-500/10 border border-amber-500/20 px-2.5 py-2 text-[10px] text-amber-300 leading-snug">
          <span className="font-semibold">IV Rank is a proxy</span>
          {" — "}ranked against 1-yr RV30 history (no IV history available).
        </div>
      )}

      {/* Factor breakdown */}
      <div className="border-t border-border/50 pt-3 mb-3 space-y-2">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-600 mb-2">
          Factor Breakdown
        </p>
        {Object.entries(data.factors).map(([key, factor]) => (
          <FactorRow
            key={key}
            name={key}
            value={factor.value}
            weight={factor.weight}
            contribution={factor.contribution}
            isProxy={key === "iv_rank" && hasProxy}
          />
        ))}
      </div>

      {/* Macro penalties — shown only for live provider when penalties > 0 */}
      {hasMacroPenalties && macroPen && (
        <div className="border-t border-border/50 pt-3 mb-3">
          <div className="flex items-center justify-between mb-2">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-600">
              Macro Risk Penalties
            </p>
            <span className="text-[10px] font-medium text-red-400 tabular-nums">
              −{macroPen.total_penalty.toFixed(0)} pts
            </span>
          </div>
          <ul className="space-y-1">
            {macroPen.reasons.map((reason, i) => (
              <li key={i} className="flex items-start gap-1.5 text-[10px] text-slate-400">
                <span className="shrink-0 text-red-400 mt-0.5">⚠</span>
                {reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Playbook */}
      <div className="border-t border-border/50 pt-3">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-600 mb-2">
          Playbook
        </p>
        <ul className="space-y-1.5">
          {data.playbook.map((item, i) => (
            <li key={i} className="flex items-start gap-2 text-[11px] text-slate-300">
              <span className={clsx("mt-0.5 shrink-0 font-bold", styles.text)}>›</span>
              {item}
            </li>
          ))}
        </ul>
      </div>

      {/* Data source footer */}
      {isLive && (
        <div className="border-t border-border/50 mt-3 pt-2 flex items-center gap-2 flex-wrap">
          <span className="rounded-full bg-green-500/10 border border-green-500/20 px-2 py-0.5 text-[9px] font-medium text-green-400">
            Live
          </span>
          <span className="rounded-full bg-slate-700/50 border border-border px-2 py-0.5 text-[9px] text-slate-500">
            IV: {data.iv_source === "live_options_chain" ? "options chain" : "RV fallback"}
          </span>
          {data.as_of_date && (
            <span className="text-[9px] text-slate-600">{data.as_of_date}</span>
          )}
        </div>
      )}
    </CardShell>
  );
}
