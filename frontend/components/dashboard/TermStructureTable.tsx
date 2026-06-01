"use client";

import { clsx } from "clsx";
import { CardShell, CardSkeleton, CardError } from "./CardShell";
import { getSignalStyles } from "@/lib/signals";
import { TermStructureChart } from "./TermStructureChart";
import type { TermStructureResponse } from "@/lib/api";

interface Props {
  data:    TermStructureResponse | null;
  loading: boolean;
  error:   string | null;
  ticker:  string;
}

const SHAPE_LABEL: Record<string, string> = {
  contango: "Contango — upward sloping (normal carry)",
  inverted: "Inverted — front vol elevated (stress)",
  humped:   "Humped — mid-curve elevated",
  flat:     "Flat — little carry across term",
  partial:  "Partial — long-tenor data unavailable",
};

const SHAPE_SIGNAL: Record<string, string> = {
  contango: "SELL_SELECTIVE",
  inverted: "BUY_PREMIUM",
  humped:   "NEUTRAL",
  flat:     "NEUTRAL",
  partial:  "NEUTRAL",
};

export function TermStructureTable({ data, loading, error, ticker }: Props) {
  if (loading) return <CardSkeleton rows={3} />;
  if (error)   return <CardError message={error} />;
  if (!data)   return null;

  const shapeStyles = getSignalStyles(SHAPE_SIGNAL[data.shape] ?? "NEUTRAL");

  const slopes = [
    { label: "Front  (IV30 − IV7)",  value: data.front_slope },
    { label: "Mid    (IV60 − IV30)", value: data.mid_slope },
    { label: "Back   (IV90 − IV60)", value: data.back_slope },
    { label: "Total  (IV90 − IV7)",  value: data.total_slope },
  ];

  return (
    <CardShell>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-1">
            Term Structure — {ticker}
          </p>
          <div className="flex items-center gap-2">
            <span className={clsx("text-sm font-semibold capitalize", shapeStyles.text)}>
              {data.shape}
            </span>
            <span className="text-[11px] text-slate-500">
              {SHAPE_LABEL[data.shape]}
            </span>
          </div>
        </div>
        <span className={clsx("rounded-full px-2.5 py-1 text-[10px] font-medium shrink-0 ml-3", shapeStyles.badge)}>
          {data.curvature != null
            ? `Curvature ${data.curvature >= 0 ? "+" : ""}${data.curvature.toFixed(1)} pts`
            : "Curvature —"}
        </span>
      </div>

      {/* IV curve chart */}
      <div className="mb-4">
        <TermStructureChart curve={data.curve} />
      </div>

      {/* IV Tenor table */}
      <div className="overflow-x-auto mb-4">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              {data.curve.map((pt) => (
                <th key={pt.tenor} className="pb-2 text-center font-medium text-slate-500 text-[11px] uppercase tracking-wide">
                  {pt.tenor}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              {data.curve.map((pt, i) => {
                // Highlight the steepest change
                const isHighest = pt.iv === Math.max(...data.curve.map((p) => p.iv));
                return (
                  <td key={pt.tenor} className="pt-3 text-center">
                    <span className={clsx(
                      "text-base font-bold tabular-nums",
                      i === 0 ? "text-slate-300" : isHighest ? "text-white" : "text-slate-200"
                    )}>
                      {pt.iv.toFixed(1)}
                    </span>
                    <span className="text-slate-600 text-[10px]">%</span>
                    {i > 0 && (
                      <p className="text-[9px] mt-0.5 text-slate-600">
                        {(pt.iv - data.curve[i - 1].iv) >= 0 ? "▲" : "▼"}{" "}
                        {Math.abs(pt.iv - data.curve[i - 1].iv).toFixed(1)}
                      </p>
                    )}
                  </td>
                );
              })}
            </tr>
          </tbody>
        </table>
      </div>

      {/* Slope breakdown */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 border-t border-border pt-3">
        {slopes.map(({ label, value }) => (
          <div key={label} className="text-center">
            {value != null ? (
              <p className={clsx(
                "text-sm font-semibold tabular-nums",
                value > 0 ? "text-slate-300" : "text-orange-400"
              )}>
                {value >= 0 ? "+" : ""}{value.toFixed(1)}
                <span className="text-slate-600 text-[10px]"> pts</span>
              </p>
            ) : (
              <p className="text-sm font-semibold tabular-nums text-slate-600">—</p>
            )}
            <p className="text-[9px] text-slate-600 mt-0.5 font-mono">{label}</p>
          </div>
        ))}
      </div>
    </CardShell>
  );
}
