"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { CardShell, CardSkeleton, CardError } from "./CardShell";
import type { VolatilityResponse } from "@/lib/api";

interface Props {
  data:    VolatilityResponse | null;
  loading: boolean;
  error:   string | null;
  ticker:  string;
}

export function IVRVBarChart({ data, loading, error, ticker }: Props) {
  if (loading) return <CardSkeleton rows={2} />;
  if (error)   return <CardError message={error} />;
  if (!data)   return null;

  // Pair nearest-tenor windows: IV 7/30/60/90 vs RV 10/20/30/60.
  // Skip tenors where IV is null (outside DTE tolerance or chain not available).
  const chartData = [
    data.iv.iv7  != null ? { window: "~7d",  IV: +data.iv.iv7.toFixed(1),  RV: +data.rv.rv10.toFixed(1) } : null,
    data.iv.iv30 != null ? { window: "~30d", IV: +data.iv.iv30.toFixed(1), RV: +data.rv.rv20.toFixed(1) } : null,
    data.iv.iv60 != null ? { window: "~60d", IV: +data.iv.iv60.toFixed(1), RV: +data.rv.rv30.toFixed(1) } : null,
    data.iv.iv90 != null ? { window: "~90d", IV: +data.iv.iv90.toFixed(1), RV: +data.rv.rv60.toFixed(1) } : null,
  ].filter((row): row is { window: string; IV: number; RV: number } => row !== null);

  return (
    <CardShell>
      <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-3">
        IV vs Realized Vol — {ticker}
      </p>

      <ResponsiveContainer width="100%" height={140}>
        <BarChart
          data={chartData}
          barCategoryGap="28%"
          barGap={3}
          margin={{ top: 0, right: 8, left: -18, bottom: 0 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="rgba(100,116,139,0.12)"
            vertical={false}
          />
          <XAxis
            dataKey="window"
            tick={{ fill: "#64748b", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "#64748b", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => `${v}%`}
            width={34}
          />
          <Tooltip
            contentStyle={{
              background: "#1e293b",
              border: "1px solid rgba(100,116,139,0.25)",
              borderRadius: "6px",
              padding: "6px 10px",
              fontSize: "11px",
            }}
            labelStyle={{ color: "#94a3b8" }}
            itemStyle={{ color: "#e2e8f0" }}
            formatter={(v: number) => `${v.toFixed(1)}%`}
          />
          <Legend
            wrapperStyle={{ fontSize: "10px", paddingTop: "6px", color: "#64748b" }}
            iconType="circle"
            iconSize={6}
          />
          <Bar dataKey="IV" name="Impl. Vol" fill="#818cf8" radius={[2, 2, 0, 0]} />
          <Bar dataKey="RV" name="Realized Vol" fill="#34d399" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>

      <p className="text-[9px] text-slate-700 text-center mt-1">
        {chartData.length < 4
          ? "Partial IV surface — some tenors unavailable · IV above RV → sellers earn positive carry"
          : "IV above RV → sellers earn positive carry"}
      </p>
    </CardShell>
  );
}
