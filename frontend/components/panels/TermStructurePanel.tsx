"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { Card, CardHeader, CardBody } from "@/components/Card";
import { clsx } from "clsx";
import type { TickerSnapshot } from "@/lib/types";

// ---------------------------------------------------------------------------
// Shape styles
// ---------------------------------------------------------------------------
const SHAPE: Record<string, { label: string; cls: string; lineColor: string }> = {
  contango: { label: "Contango",  cls: "text-blue-400",   lineColor: "#60a5fa" },
  inverted: { label: "Inverted",  cls: "text-red-400",    lineColor: "#ef4444" },
  humped:   { label: "Humped",    cls: "text-orange-400", lineColor: "#f97316" },
  flat:     { label: "Flat",      cls: "text-slate-400",  lineColor: "#94a3b8" },
  partial:  { label: "Partial",   cls: "text-slate-500",  lineColor: "#64748b" },
};

// ---------------------------------------------------------------------------
// Curvature badge
// ---------------------------------------------------------------------------
function CurvatureBadge({ curvature }: { curvature: number | null | undefined }) {
  if (curvature == null) {
    return (
      <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[10px] text-slate-600">
        curve —
      </span>
    );
  }
  const sign = curvature >= 0 ? "+" : "";
  const label = Math.abs(curvature) < 0.3
    ? "uniform"
    : curvature > 0 ? "decelerating"
    : "accelerating";
  return (
    <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
      curve {sign}{curvature.toFixed(1)} <span className="text-slate-600">({label})</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Slope summary row
// ---------------------------------------------------------------------------
function SlopeRow({
  label,
  value,
}: {
  label: string;
  value: number | null | undefined;
}) {
  if (value == null) {
    return (
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-slate-500">{label}</span>
        <span className="font-mono text-[10px] text-slate-600">— pts</span>
      </div>
    );
  }
  const color = value > 0.5 ? "text-blue-400" : value < -0.5 ? "text-red-400" : "text-slate-400";
  return (
    <div className="flex items-center justify-between">
      <span className="text-[10px] text-slate-500">{label}</span>
      <span className={clsx("font-mono text-[10px]", color)}>
        {value >= 0 ? "+" : ""}{value.toFixed(1)} pts
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-ticker term structure mini card
// ---------------------------------------------------------------------------
function TickerCurve({ t }: { t: TickerSnapshot }) {
  // Filter out null/undefined IV values so the chart only shows real points
  const data = [
    { tenor: "7d",  iv: t.iv.iv7  },
    { tenor: "30d", iv: t.iv.iv30 },
    { tenor: "60d", iv: t.iv.iv60 },
    { tenor: "90d", iv: t.iv.iv90 },
  ].filter((d): d is { tenor: string; iv: number } => d.iv != null);

  const { front_slope, mid_slope, back_slope, total_slope, curvature, shape } = t.term_structure;
  const style = SHAPE[shape] ?? SHAPE.flat;

  const iv30 = t.iv.iv30 ?? 0;
  const yPad = iv30 * 0.08;
  const ivValues = data.map((d) => d.iv);
  const yMin = ivValues.length > 0 ? Math.floor(Math.min(...ivValues) - yPad) : 0;
  const yMax = ivValues.length > 0 ? Math.ceil(Math.max(...ivValues)  + yPad) : 50;

  return (
    <div className="rounded-lg border border-border bg-surface-3 p-3 space-y-2">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-white">{t.ticker}</span>
          <span className={clsx("text-xs font-semibold", style.cls)}>{style.label}</span>
        </div>
        <CurvatureBadge curvature={curvature} />
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={90}>
        <LineChart data={data} margin={{ top: 6, right: 6, left: -24, bottom: 0 }}>
          <XAxis
            dataKey="tenor"
            tick={{ fontSize: 9, fill: "#64748b" }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            domain={[yMin, yMax]}
            tick={{ fontSize: 9, fill: "#64748b" }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => `${v.toFixed(0)}`}
          />
          <Tooltip
            contentStyle={{
              background: "#1a1d2e",
              border: "1px solid #2d3148",
              fontSize: 11,
              borderRadius: 6,
            }}
            formatter={(v: number) => [`${v.toFixed(1)}%`, "IV"]}
          />
          {/* ATM reference — only render when iv30 is available */}
          {t.iv.iv30 != null && (
            <ReferenceLine
              y={t.iv.iv30}
              stroke="#2d3148"
              strokeDasharray="4 2"
            />
          )}
          <Line
            type="monotone"
            dataKey="iv"
            stroke={style.lineColor}
            strokeWidth={2}
            dot={{ r: 3.5, fill: "#1a1d2e", stroke: style.lineColor, strokeWidth: 2 }}
            activeDot={{ r: 5 }}
          />
        </LineChart>
      </ResponsiveContainer>

      {/* Slope breakdown */}
      <div className="space-y-0.5 border-t border-border pt-2">
        <SlopeRow label="Front (IV30−IV7)"  value={front_slope} />
        <SlopeRow label="Mid   (IV60−IV30)" value={mid_slope}   />
        <SlopeRow label="Back  (IV90−IV60)" value={back_slope}  />
        <div className="flex items-center justify-between border-t border-border pt-1 mt-1">
          <span className="text-[10px] font-semibold text-slate-400">Total</span>
          {total_slope != null ? (
            <span className={clsx(
              "font-mono text-[10px] font-semibold",
              total_slope > 1 ? "text-blue-400" : total_slope < -1 ? "text-red-400" : "text-slate-400"
            )}>
              {total_slope >= 0 ? "+" : ""}{total_slope.toFixed(1)} pts
            </span>
          ) : (
            <span className="font-mono text-[10px] font-semibold text-slate-600">— pts</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------
export function TermStructurePanel({ tickers }: { tickers: TickerSnapshot[] }) {
  return (
    <Card>
      <CardHeader
        title="Term Structure"
        subtitle="IV surface across 7 · 30 · 60 · 90 day tenors — ATM reference dashed"
      />
      <CardBody>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {tickers.map((t) => (
            <TickerCurve key={t.ticker} t={t} />
          ))}
        </div>
      </CardBody>
    </Card>
  );
}
