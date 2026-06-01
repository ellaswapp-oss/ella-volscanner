import { clsx } from "clsx";
import { CardShell, CardSkeleton, CardError } from "./CardShell";
import { getSignalStyles } from "@/lib/signals";
import type { VolatilityResponse } from "@/lib/api";

interface MetricConfig {
  label:       string;
  value:       string;
  sub:         string;
  signal?:     string;   // optional color tint
  barPct?:     number;   // optional 0-100 progress bar
}

function MetricCardInner({ label, value, sub, signal, barPct }: MetricConfig) {
  const styles = signal ? getSignalStyles(signal) : null;
  return (
    <CardShell>
      <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-2">
        {label}
      </p>
      <p className={clsx(
        "text-3xl font-bold tabular-nums mb-0.5",
        styles ? styles.text : "text-white"
      )}>
        {value}
      </p>
      <p className="text-[11px] text-slate-500 mb-3">{sub}</p>

      {barPct !== undefined && (
        <div>
          <div className="h-1 w-full rounded-full bg-surface-3 overflow-hidden">
            <div
              className={clsx(
                "h-full rounded-full transition-all duration-700",
                styles ? styles.dot : "bg-slate-400"
              )}
              style={{ width: `${Math.min(Math.max(barPct, 0), 100)}%` }}
            />
          </div>
          <div className="flex justify-between mt-1 text-[9px] text-slate-600">
            <span>0</span><span>50</span><span>100</span>
          </div>
        </div>
      )}
    </CardShell>
  );
}

// ---------------------------------------------------------------------------
// Individual metric cards
// ---------------------------------------------------------------------------

interface VolProps {
  data:    VolatilityResponse | null;
  loading: boolean;
  error:   string | null;
}

function signalFromRatio(ratio: number): string {
  if (ratio > 1.3)  return "SELL_AGGRESSIVE";
  if (ratio > 1.1)  return "SELL_SELECTIVE";
  if (ratio < 0.85) return "BUY_PREMIUM";
  return "NEUTRAL";
}

function signalFromVRP(vrp: number): string {
  if (vrp > 0.15)  return "SELL_AGGRESSIVE";
  if (vrp > 0.05)  return "SELL_SELECTIVE";
  if (vrp < -0.05) return "BUY_PREMIUM";
  return "NEUTRAL";
}

function signalFromZ(z: number): string {
  if (z > 1.5)  return "SELL_AGGRESSIVE";
  if (z > 0.5)  return "SELL_SELECTIVE";
  if (z < -0.5) return "BUY_PREMIUM";
  return "NEUTRAL";
}

export function IVRVRatioCard({ data, loading, error }: VolProps) {
  if (loading) return <CardSkeleton rows={1} />;
  if (error)   return <CardError message={error} />;
  if (!data)   return null;

  const r = data.iv_rv_ratio;
  return (
    <MetricCardInner
      label="IV / RV Ratio"
      value={`${r.toFixed(3)}×`}
      sub={r > 1 ? "IV premium — sellers favoured" : "IV discount — buyers favoured"}
      signal={signalFromRatio(r)}
    />
  );
}

export function VRPCard({ data, loading, error }: VolProps) {
  if (loading) return <CardSkeleton rows={1} />;
  if (error)   return <CardError message={error} />;
  if (!data)   return null;

  const vrp = data.vrp;
  const sign = vrp >= 0 ? "+" : "";
  return (
    <MetricCardInner
      label="Vol Risk Premium"
      value={`${sign}${(vrp * 100).toFixed(2)}%`}
      sub={vrp > 0 ? "IV above RV — sellers earn carry" : "IV below RV — premium is cheap"}
      signal={signalFromVRP(vrp)}
    />
  );
}

export function IVRankCard({ data, loading, error }: VolProps) {
  if (loading) return <CardSkeleton rows={2} />;
  if (error)   return <CardError message={error} />;
  if (!data)   return null;

  const ivr = data.iv_rank;
  const signal =
    ivr > 70 ? "SELL_AGGRESSIVE" :
    ivr > 50 ? "SELL_SELECTIVE"  :
    ivr < 30 ? "BUY_PREMIUM"     : "NEUTRAL";

  return (
    <MetricCardInner
      label="IV Rank"
      value={ivr.toFixed(1)}
      sub="Percentile within 1-year range"
      signal={signal}
      barPct={ivr}
    />
  );
}

export function ZScoreCard({ data, loading, error }: VolProps) {
  if (loading) return <CardSkeleton rows={1} />;
  if (error)   return <CardError message={error} />;
  if (!data)   return null;

  const z = data.iv_rv_zscore;
  const sign = z >= 0 ? "+" : "";
  return (
    <MetricCardInner
      label="IV/RV Z-Score"
      value={`${sign}${z.toFixed(2)}`}
      sub="Standard deviations vs 1-yr spread"
      signal={signalFromZ(z)}
    />
  );
}
