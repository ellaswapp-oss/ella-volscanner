"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Cell,
} from "recharts";
import { Card, CardHeader, CardBody } from "@/components/Card";
import type { TickerSnapshot } from "@/lib/types";
import { clsx } from "clsx";

// ---------------------------------------------------------------------------
// Normalised skew percentage → colour band
// ---------------------------------------------------------------------------
function skewColor(skewPct: number): string {
  if (skewPct > 30) return "#ef4444";
  if (skewPct > 18) return "#f97316";
  if (skewPct > 10) return "#eab308";
  return "#22c55e";
}

// ---------------------------------------------------------------------------
// Custom tooltip
// ---------------------------------------------------------------------------
function SkewTooltip({ active, payload, label }: {
  active?: boolean;
  payload?: Array<{ payload: SkewRow }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-surface-2 p-2 text-[11px]">
      <p className="mb-1 font-bold text-white">{d.ticker}</p>
      <p className="text-slate-400">
        25Δ Put <span className="font-mono text-red-400">{d.put.toFixed(1)}%</span>
      </p>
      <p className="text-slate-400">
        ATM IV  <span className="font-mono text-white">{d.atm.toFixed(1)}%</span>
      </p>
      <p className="text-slate-400">
        25Δ Call <span className="font-mono text-green-400">{d.call.toFixed(1)}%</span>
      </p>
      <p className="mt-1 border-t border-border pt-1 text-slate-400">
        Skew <span className="font-mono text-white">{d.skewPct.toFixed(1)}%</span>
        <span className="ml-1 text-slate-600">of ATM</span>
      </p>
    </div>
  );
}

interface SkewRow {
  ticker: string;
  skewPct: number;
  put: number;
  atm: number;
  call: number;
  absSkew: number;
}

// ---------------------------------------------------------------------------
// Stat row below chart
// ---------------------------------------------------------------------------
function SkewStatRow({ d }: { d: SkewRow }) {
  return (
    <div className="flex items-center gap-2 py-1.5 border-b border-border last:border-0 text-[10px]">
      <span className="w-10 font-bold text-white">{d.ticker}</span>
      <span className="font-mono text-red-400 w-14">Put {d.put.toFixed(1)}%</span>
      <span className="font-mono text-slate-300 w-14">ATM {d.atm.toFixed(1)}%</span>
      <span className="font-mono text-green-400 w-16">Call {d.call.toFixed(1)}%</span>
      <div className="flex-1 h-1 rounded-full bg-surface-3 overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{
            width: `${Math.min(d.skewPct * 2.5, 100)}%`,
            backgroundColor: skewColor(d.skewPct),
          }}
        />
      </div>
      <span
        className="font-mono font-semibold w-10 text-right"
        style={{ color: skewColor(d.skewPct) }}
      >
        {d.skewPct.toFixed(1)}%
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------
export function SkewPanel({ tickers }: { tickers: TickerSnapshot[] }) {
  const data: SkewRow[] = tickers
    .filter((t) => !t.error)
    .map((t) => ({
      ticker:   t.ticker,
      skewPct:  parseFloat(((t.skew.skew_25d / t.skew.atm_iv) * 100).toFixed(2)),
      put:      t.skew.put_25d,
      atm:      t.skew.atm_iv,
      call:     t.skew.call_25d,
      absSkew:  t.skew.skew_25d,
    }))
    .sort((a, b) => b.skewPct - a.skewPct);

  return (
    <Card>
      <CardHeader
        title="Skew — 25Δ Put-Call"
        subtitle="Normalised put skew (% of ATM) · higher = pricier downside protection"
      />
      <CardBody className="space-y-4">
        {/* Bar chart — skew % of ATM */}
        <ResponsiveContainer width="100%" height={130}>
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 0, right: 8, left: 0, bottom: 0 }}
          >
            <XAxis
              type="number"
              tick={{ fontSize: 9, fill: "#64748b" }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v) => `${v}%`}
              domain={[0, "dataMax + 5"]}
            />
            <YAxis
              type="category"
              dataKey="ticker"
              tick={{ fontSize: 10, fill: "#94a3b8", fontWeight: 700 }}
              tickLine={false}
              axisLine={false}
              width={32}
            />
            <Tooltip content={<SkewTooltip />} cursor={{ fill: "#2d314822" }} />
            <ReferenceLine x={20} stroke="#2d3148" strokeDasharray="4 2" />
            <Bar dataKey="skewPct" radius={[0, 3, 3, 0]} maxBarSize={16}>
              {data.map((d) => (
                <Cell key={d.ticker} fill={skewColor(d.skewPct)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>

        {/* Detail rows */}
        <div className="border-t border-border pt-3">
          {data.map((d) => (
            <SkewStatRow key={d.ticker} d={d} />
          ))}
        </div>

        <p className="text-[9px] text-slate-700">
          Prototype: synthetic 25Δ skew. Replace with ORATS options chain in Phase 4.
        </p>
      </CardBody>
    </Card>
  );
}
