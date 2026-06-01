"use client";

import { clsx } from "clsx";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { CardShell, CardSkeleton, CardError } from "./CardShell";
import { getSignalStyles } from "@/lib/signals";

interface Props {
  ticker:   string;
  currentZ: number;
  loading:  boolean;
  error:    string | null;
}

/** Deterministic 61-day mock z-score series using sine/cosine walk. */
function mockHistory(ticker: string, currentZ: number) {
  const seed0 = ticker.split("").reduce((s, c) => s + c.charCodeAt(0), 0);
  const N = 60;

  // Work backwards from currentZ
  const raw: number[] = [currentZ];
  let z = currentZ;
  for (let i = 1; i <= N; i++) {
    const s = seed0 + i * 37;
    const delta = Math.sin(s * 0.43) * 0.28 + Math.cos(s * 0.17) * 0.14;
    z -= delta;
    z = Math.max(-3.4, Math.min(3.4, z));
    raw.unshift(+z.toFixed(2));
  }

  const today = new Date();
  return raw.map((val, i) => {
    const d = new Date(today);
    d.setDate(today.getDate() - (N - i));
    return {
      date: d.toLocaleDateString("en-US", { month: "short", day: "numeric" }),
      z: val,
    };
  });
}

export function ZScoreHistoryChart({ ticker, currentZ, loading, error }: Props) {
  if (loading) return <CardSkeleton rows={2} />;
  if (error)   return <CardError message={error} />;

  const data = mockHistory(ticker, currentZ);

  const signal =
    currentZ >  1.5 ? "SELL_AGGRESSIVE" :
    currentZ >  0.5 ? "SELL_SELECTIVE"  :
    currentZ < -0.5 ? "BUY_PREMIUM"     : "NEUTRAL";
  const styles = getSignalStyles(signal);

  // Sparse x-axis ticks — keep first, last, and every 15th
  const tickSet = new Set([data[0].date, data[14].date, data[29].date, data[44].date, data[60].date]);
  const xTicks = data.filter((d) => tickSet.has(d.date)).map((d) => d.date);

  return (
    <CardShell>
      <div className="flex items-center justify-between mb-3">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
          IV/RV Z-Score History — {ticker}
        </p>
        <span className={clsx("text-sm font-bold tabular-nums", styles.text)}>
          {currentZ >= 0 ? "+" : ""}
          {currentZ.toFixed(2)}σ
        </span>
      </div>

      <ResponsiveContainer width="100%" height={140}>
        <LineChart data={data} margin={{ top: 4, right: 24, left: -20, bottom: 0 }}>
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="rgba(100,116,139,0.10)"
            vertical={false}
          />
          <XAxis
            dataKey="date"
            ticks={xTicks}
            tick={{ fill: "#475569", fontSize: 9 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            domain={[-3, 3]}
            ticks={[-2, -1, 0, 1, 2]}
            tick={{ fill: "#475569", fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            width={26}
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
            formatter={(v: number) => [
              `${v >= 0 ? "+" : ""}${v.toFixed(2)}σ`,
              "Z-Score",
            ]}
          />

          {/* Sell zone bands */}
          <ReferenceLine y={2}  stroke="rgba(239,68,68,0.35)"  strokeDasharray="3 3"
            label={{ value: "+2σ", position: "insideRight", fontSize: 8, fill: "rgba(239,68,68,0.6)" }} />
          <ReferenceLine y={1}  stroke="rgba(251,146,60,0.35)" strokeDasharray="3 3"
            label={{ value: "+1σ", position: "insideRight", fontSize: 8, fill: "rgba(251,146,60,0.6)" }} />
          {/* Zero */}
          <ReferenceLine y={0}  stroke="rgba(100,116,139,0.35)" />
          {/* Buy zone bands */}
          <ReferenceLine y={-1} stroke="rgba(52,211,153,0.35)" strokeDasharray="3 3"
            label={{ value: "-1σ", position: "insideRight", fontSize: 8, fill: "rgba(52,211,153,0.6)" }} />
          <ReferenceLine y={-2} stroke="rgba(52,211,153,0.5)"  strokeDasharray="3 3"
            label={{ value: "-2σ", position: "insideRight", fontSize: 8, fill: "rgba(52,211,153,0.7)" }} />

          <Line
            type="monotone"
            dataKey="z"
            stroke="#818cf8"
            strokeWidth={1.5}
            dot={false}
            activeDot={{ r: 4, fill: "#a5b4fc", strokeWidth: 0 }}
          />
        </LineChart>
      </ResponsiveContainer>

      <p className="text-[9px] text-slate-700 text-center mt-1">
        60-day mock history · Phase 3 will wire live data
      </p>
    </CardShell>
  );
}
