"use client";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { CurvePoint } from "@/lib/api";

interface Props {
  curve: CurvePoint[];
}

export function TermStructureChart({ curve }: Props) {
  const data = curve.map((pt) => ({ name: pt.tenor, iv: +pt.iv.toFixed(2) }));

  const min = Math.min(...data.map((d) => d.iv));
  const max = Math.max(...data.map((d) => d.iv));
  const pad = Math.max((max - min) * 0.4, 1);

  return (
    <ResponsiveContainer width="100%" height={120}>
      <AreaChart data={data} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
        <defs>
          <linearGradient id="ivGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="10%" stopColor="#818cf8" stopOpacity={0.25} />
            <stop offset="95%" stopColor="#818cf8" stopOpacity={0.02} />
          </linearGradient>
        </defs>

        <CartesianGrid
          strokeDasharray="3 3"
          stroke="rgba(100,116,139,0.12)"
          vertical={false}
        />
        <XAxis
          dataKey="name"
          tick={{ fill: "#64748b", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          domain={[min - pad, max + pad]}
          tick={{ fill: "#64748b", fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v: number) => `${v.toFixed(0)}%`}
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
          formatter={(v: number) => [`${v.toFixed(1)}%`, "IV"]}
        />
        <Area
          type="monotone"
          dataKey="iv"
          stroke="#818cf8"
          strokeWidth={2}
          fill="url(#ivGradient)"
          dot={{ fill: "#818cf8", strokeWidth: 0, r: 3.5 }}
          activeDot={{ r: 5, fill: "#a5b4fc" }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
