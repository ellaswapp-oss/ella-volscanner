"use client";

import { Card, CardHeader, CardBody } from "@/components/Card";
import { TVAreaChart } from "@/components/charts/TVAreaChart";
import type { TickerSnapshot } from "@/lib/types";
import { clsx } from "clsx";

// ---------------------------------------------------------------------------
// Stat cell
// ---------------------------------------------------------------------------
function Stat({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: "green" | "yellow" | "orange" | "red" | "blue";
}) {
  const colorCls = {
    green:  "text-vol-green",
    yellow: "text-vol-yellow",
    orange: "text-vol-orange",
    red:    "text-vol-red",
    blue:   "text-blue-400",
  };
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wider text-slate-500">{label}</span>
      <span className={clsx("font-mono text-sm font-semibold", color ? colorCls[color] : "text-white")}>
        {value}
      </span>
      {sub && <span className="text-[10px] text-slate-500">{sub}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Colour helpers
// ---------------------------------------------------------------------------
function vrpColor(vrp: number): "green" | "yellow" | "orange" | "red" {
  if (vrp >  0.35) return "red";
  if (vrp >  0.10) return "orange";
  if (vrp < -0.10) return "green";
  return "yellow";
}

function zColor(z: number): "green" | "yellow" | "orange" | "red" {
  if (z >  1.5) return "red";
  if (z >  0.5) return "orange";
  if (z < -1.0) return "green";
  return "yellow";
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------
export function IVvsRVPanel({ ticker }: { ticker: TickerSnapshot }) {
  const { sparklines, iv, rv, vrp, iv_rv_zscore: z } = ticker;

  // Build TradingView data arrays
  const ivData = sparklines.dates.map((d, i) => ({ time: d, value: sparklines.iv30[i] }));
  const rvData = sparklines.dates.map((d, i) => ({ time: d, value: sparklines.rv30[i] }));

  const vrpSign  = vrp >= 0 ? "+" : "";
  const vrpPct   = `${vrpSign}${(vrp * 100).toFixed(1)}%`;
  const zSign    = z >= 0 ? "+" : "";
  const ivRatio  = ticker.iv_rv_ratio.toFixed(2);

  return (
    <Card>
      <CardHeader
        title={`IV vs Realized Vol — ${ticker.ticker}`}
        subtitle="IV30 area (blue) · RV30 line (orange) · last 60 trading days"
      />
      <CardBody className="space-y-4">
        {/* Primary metrics */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="IV30" value={iv.iv30 != null ? `${iv.iv30.toFixed(1)}%` : "—"} color="blue" />
          <Stat label="RV30" value={`${rv.rv30.toFixed(1)}%`} />
          <Stat
            label="VRP"
            value={vrpPct}
            sub={vrp > 0 ? "Sellers favored" : "Buyers favored"}
            color={vrpColor(vrp)}
          />
          <Stat
            label="IV/RV Z"
            value={`${zSign}${z.toFixed(2)}`}
            sub="vs 1-yr spread"
            color={zColor(z)}
          />
        </div>

        {/* TradingView chart */}
        <TVAreaChart iv={ivData} rv={rvData} height={175} />

        {/* Secondary metrics — RV windows + IV surface */}
        <div className="grid grid-cols-4 gap-2 border-t border-border pt-3">
          <Stat label="RV10" value={`${rv.rv10.toFixed(1)}%`} />
          <Stat label="RV20" value={`${rv.rv20.toFixed(1)}%`} />
          <Stat label="RV30" value={`${rv.rv30.toFixed(1)}%`} />
          <Stat label="RV60" value={`${rv.rv60.toFixed(1)}%`} />
        </div>
        <div className="grid grid-cols-4 gap-2 border-t border-border pt-3">
          <Stat label="IV7"  value={iv.iv7  != null ? `${iv.iv7.toFixed(1)}%`  : "—"} color="blue" />
          <Stat label="IV30" value={iv.iv30 != null ? `${iv.iv30.toFixed(1)}%` : "—"} color="blue" />
          <Stat label="IV60" value={iv.iv60 != null ? `${iv.iv60.toFixed(1)}%` : "—"} color="blue" />
          <Stat label="IV90" value={iv.iv90 != null ? `${iv.iv90.toFixed(1)}%` : "—"} color="blue" />
        </div>

        {/* IV / RV ratio */}
        <div className="flex items-center justify-between rounded-lg bg-surface-3 px-3 py-2">
          <span className="text-xs text-slate-400">IV / RV Ratio</span>
          <span className={clsx(
            "font-mono text-sm font-semibold",
            parseFloat(ivRatio) > 1.2 ? "text-vol-orange"
            : parseFloat(ivRatio) < 0.85 ? "text-vol-green"
            : "text-white"
          )}>
            {ivRatio}×
          </span>
        </div>
      </CardBody>
    </Card>
  );
}
