"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { clsx } from "clsx";
import {
  LineChart, Line,
  AreaChart, Area,
  BarChart, Bar, Cell,
  XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, Legend,
  ResponsiveContainer,
} from "recharts";
import { getMacroSeries } from "@/lib/api";
import type { MacroSeriesResponse, MacroRow } from "@/lib/api";

// ---------------------------------------------------------------------------
// Thin series for chart performance
// ---------------------------------------------------------------------------

function thin<T>(rows: T[], maxPts = 300): T[] {
  if (rows.length <= maxPts) return rows;
  const step = Math.ceil(rows.length / maxPts);
  return rows.filter((_, i) => i % step === 0 || i === rows.length - 1);
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

const fmt2 = (v: number | null | undefined) =>
  v == null ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const fmtDate = (iso: string | null | undefined) =>
  iso ? new Date(iso + "T00:00:00").toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—";

const xFmt = (d: string) => d.slice(0, 7);   // YYYY-MM for X-axis

// ---------------------------------------------------------------------------
// Shared chart tooltip
// ---------------------------------------------------------------------------

function MacroTooltip({
  active, payload,
  keys,
}: {
  active?: boolean;
  payload?: any[];
  keys: { key: string; label: string; color: string; unit?: string }[];
}) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload as MacroRow;
  return (
    <div className="rounded-lg bg-slate-800 border border-white/10 px-3 py-2 text-xs shadow-xl min-w-[130px]">
      <p className="font-semibold text-slate-300 mb-1.5">{fmtDate(row.date)}</p>
      {keys.map(({ key, label, color, unit }) => {
        const v = (row as any)[key] as number | null;
        return (
          <p key={key} style={{ color }} className="tabular-nums">
            {label}
            <span className="ml-1 font-medium">
              {v == null ? "—" : `${fmt2(v)}${unit ?? ""}`}
            </span>
          </p>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Reusable mini chart
// ---------------------------------------------------------------------------

function SeriesChart({
  title,
  subtitle,
  rows,
  dataKey,
  color,
  unit,
  yTickFmt,
}: {
  title: string;
  subtitle?: string;
  rows: MacroRow[];
  dataKey: keyof MacroRow;
  color: string;
  unit?: string;
  yTickFmt?: (v: number) => string;
}) {
  const vals = rows.map((r) => (r[dataKey] as number | null) ?? 0);
  const lo   = Math.min(...vals) * 0.97;
  const hi   = Math.max(...vals) * 1.03;

  return (
    <div className="rounded-xl bg-white/5 border border-white/10 p-4">
      <p className="text-sm font-semibold text-slate-200">{title}</p>
      {subtitle && <p className="text-xs text-slate-500 mb-3">{subtitle}</p>}
      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={rows} margin={{ left: 0, right: 4, top: 4, bottom: 0 }}>
          <defs>
            <linearGradient id={`grad_${dataKey}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor={color} stopOpacity={0.25} />
              <stop offset="95%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#ffffff0a" />
          <XAxis
            dataKey="date"
            tick={{ fill: "#475569", fontSize: 10 }}
            tickFormatter={xFmt}
            interval="preserveStartEnd"
            minTickGap={55}
          />
          <YAxis
            domain={[lo, hi]}
            tick={{ fill: "#475569", fontSize: 10 }}
            tickFormatter={yTickFmt ?? ((v) => v.toFixed(1))}
            width={46}
          />
          <Tooltip
            content={<MacroTooltip keys={[{ key: dataKey as string, label: title, color, unit }]} />}
          />
          <Area
            type="monotone"
            dataKey={dataKey as string}
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#grad_${dataKey})`}
            dot={false}
            activeDot={{ r: 3, fill: color }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// VIX dual-line chart
// ---------------------------------------------------------------------------

function VIXChart({ rows }: { rows: MacroRow[] }) {
  const allV = rows.flatMap((r) => [r.vix1m ?? 0, r.vix3m ?? 0]);
  const lo   = Math.min(...allV) * 0.97;
  const hi   = Math.max(...allV) * 1.03;

  const keys = [
    { key: "vix1m",  label: "VIX (1M)",  color: "#60a5fa" },
    { key: "vix3m",  label: "VIX3M",     color: "#a78bfa" },
  ];

  return (
    <div className="rounded-xl bg-white/5 border border-white/10 p-4">
      <p className="text-sm font-semibold text-slate-200">VIX 1M vs VIX3M</p>
      <p className="text-xs text-slate-500 mb-3">Front-month and 3-month implied volatility</p>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={rows} margin={{ left: 0, right: 4, top: 4, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#ffffff0a" />
          <XAxis
            dataKey="date"
            tick={{ fill: "#475569", fontSize: 10 }}
            tickFormatter={xFmt}
            interval="preserveStartEnd"
            minTickGap={55}
          />
          <YAxis
            domain={[lo, hi]}
            tick={{ fill: "#475569", fontSize: 10 }}
            tickFormatter={(v) => v.toFixed(0)}
            width={36}
          />
          <Tooltip content={<MacroTooltip keys={keys as any} />} />
          <Legend
            wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
            formatter={(v) => keys.find((k) => k.key === v)?.label ?? v}
          />
          {keys.map(({ key, color }) => (
            <Line
              key={key}
              type="monotone"
              dataKey={key}
              stroke={color}
              strokeWidth={1.5}
              dot={false}
              activeDot={{ r: 3 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// VIX spread (contango/backwardation) chart
// ---------------------------------------------------------------------------

function VIXSpreadChart({ rows }: { rows: MacroRow[] }) {
  const vals = rows.map((r) => r.vix_spread ?? 0);
  const extremum = Math.max(Math.abs(Math.min(...vals)), Math.abs(Math.max(...vals))) * 1.1;

  return (
    <div className="rounded-xl bg-white/5 border border-white/10 p-4">
      <p className="text-sm font-semibold text-slate-200">VIX Term Structure Spread</p>
      <p className="text-xs text-slate-500 mb-3">
        VIX3M − VIX1M &nbsp;·&nbsp; <span className="text-emerald-400">+ = contango</span>
        &nbsp;·&nbsp; <span className="text-red-400">− = backwardation / stress</span>
      </p>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={rows} margin={{ left: 0, right: 4, top: 4, bottom: 0 }} barSize={2}>
          <CartesianGrid strokeDasharray="3 3" stroke="#ffffff0a" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fill: "#475569", fontSize: 10 }}
            tickFormatter={xFmt}
            interval="preserveStartEnd"
            minTickGap={55}
          />
          <YAxis
            domain={[-extremum, extremum]}
            tick={{ fill: "#475569", fontSize: 10 }}
            tickFormatter={(v) => v.toFixed(1)}
            width={36}
          />
          <Tooltip
            content={<MacroTooltip keys={[{ key: "vix_spread", label: "VIX Spread", color: "#94a3b8" }]} />}
          />
          <ReferenceLine y={0} stroke="#ffffff20" strokeWidth={1} />
          <Bar dataKey="vix_spread" radius={0}>
            {rows.map((r, i) => (
              <Cell
                key={i}
                fill={(r.vix_spread ?? 0) >= 0 ? "#34d39980" : "#f8717180"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Latest values table
// ---------------------------------------------------------------------------

function LatestTable({ stats, breadthIsProxy }: {
  stats: MacroSeriesResponse["stats"];
  breadthIsProxy: boolean;
}) {
  const items = [
    { label: "VIX (1M)",       value: stats.latest_vix,           unit: "",    color: "text-blue-400" },
    { label: "VIX3M",          value: stats.latest_vix3m,          unit: "",    color: "text-violet-400" },
    { label: "VIX Spread",     value: stats.latest_vix_spread,     unit: "",    color: stats.is_vix_inverted ? "text-red-400" : "text-emerald-400" },
    { label: "US 10Y Yield",   value: stats.latest_us10y,          unit: "%",   color: "text-amber-400" },
    { label: "WTI Crude",      value: stats.latest_crude_oil,      unit: " $/bbl", color: "text-orange-400" },
    { label: "HY Credit Spread", value: stats.latest_credit_spread, unit: "%",  color: "text-pink-400" },
    { label: "SPY Price",      value: stats.latest_spy,            unit: "",    color: "text-slate-300", prefix: "$" },
  ];

  return (
    <div className="rounded-xl bg-white/5 border border-white/10 overflow-hidden">
      <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-300">Latest Values</h2>
        <div className="flex items-center gap-3 text-xs text-slate-500">
          <span>{fmtDate(stats.last_date)}</span>
          {stats.is_vix_inverted && (
            <span className="text-red-400 font-semibold animate-pulse">⚠ VIX INVERTED</span>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-px bg-white/5">
        {items.map(({ label, value, unit, color, prefix }) => (
          <div key={label} className="bg-slate-950/60 px-4 py-3">
            <p className="text-xs text-slate-500 mb-0.5">{label}</p>
            <p className={clsx("text-lg font-semibold tabular-nums", color)}>
              {value == null ? "—" : `${prefix ?? ""}${fmt2(value)}${unit}`}
            </p>
          </div>
        ))}
      </div>
      {breadthIsProxy && (
        <p className="px-5 py-2 text-xs text-amber-400/70 border-t border-white/5">
          ⚠ Breadth is a proxy: SPY close {">"} SPY 50-DMA → 100, else 0.
          True SPX constituent breadth is pending.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stat chip
// ---------------------------------------------------------------------------

function Chip({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded-lg bg-white/5 border border-white/10 px-4 py-3 flex items-center gap-3">
      <div>
        <p className="text-xs text-slate-500">{label}</p>
        <p className={clsx("text-sm font-semibold", color ?? "text-white")}>{value}</p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function MacroDiagnosticPage() {
  const [days,    setDays]    = useState(400);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);
  const [result,  setResult]  = useState<MacroSeriesResponse | null>(null);

  async function fetchData(d: number) {
    setLoading(true);
    setError(null);
    try {
      const data = await getMacroSeries({ days: d });
      setResult(data);
    } catch (e: any) {
      setError(e.message ?? "Unknown error");
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  // Auto-fetch on mount
  useEffect(() => { fetchData(days); }, []);    // eslint-disable-line

  const chartRows = result ? thin(result.rows) : [];
  const isLive    = result?.provider === "real";
  const badgeCls  = isLive
    ? "bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30"
    : "bg-amber-500/15  text-amber-400  ring-1 ring-amber-500/30";

  return (
    <main className="min-h-screen bg-slate-950 text-white p-6 lg:p-10">
      {/* ── Header ──────────────────────────────────────────────── */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex gap-3 items-center mb-1 text-xs text-slate-500">
            <Link href="/" className="hover:text-slate-300 transition-colors">← Dashboard</Link>
            <span>·</span>
            <Link href="/data/prices" className="hover:text-slate-300 transition-colors">Price Data</Link>
          </div>
          <h1 className="text-2xl font-bold tracking-tight">Macro Data Diagnostic</h1>
          <p className="mt-1 text-sm text-slate-400">
            Live macro signals — VIX, US10Y, crude oil, credit spread, aligned to SPY calendar.
          </p>
        </div>
        {result && (
          <span className={clsx("mt-1 inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-inset", badgeCls)}>
            <span className={clsx("h-1.5 w-1.5 rounded-full", isLive ? "bg-emerald-400" : "bg-amber-400")} />
            {result.data_source}
          </span>
        )}
      </div>

      {/* ── Controls ────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-end gap-3 mb-6">
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-400">Days</label>
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="rounded-lg bg-white/5 border border-white/10 px-2 py-1.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            {[60, 120, 252, 400, 504, 756].map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        </div>
        <button
          onClick={() => fetchData(days)}
          disabled={loading}
          className={clsx(
            "px-5 py-1.5 rounded-lg text-sm font-medium transition-colors",
            loading
              ? "bg-blue-800/40 text-blue-400 cursor-not-allowed"
              : "bg-blue-600 hover:bg-blue-500 text-white",
          )}
        >
          {loading ? "Loading…" : "Refresh"}
        </button>

        {result && (
          <div className="flex gap-2 ml-auto">
            <Chip label="Rows"       value={result.stats.n_rows.toLocaleString()} />
            <Chip label="First Date" value={fmtDate(result.stats.first_date)} />
            <Chip label="Last Date"  value={fmtDate(result.stats.last_date)} />
          </div>
        )}
      </div>

      {/* ── Error ───────────────────────────────────────────────── */}
      {error && (
        <div className="mb-6 rounded-xl bg-red-500/10 border border-red-500/30 px-5 py-4">
          <p className="text-sm font-semibold text-red-400 mb-1">Error</p>
          <p className="text-sm text-red-300">{error}</p>
          {error.includes("FRED_API_KEY") && (
            <p className="mt-2 text-xs text-slate-400">
              Get a free FRED API key at{" "}
              <a href="https://fred.stlouisfed.org/docs/api/api_key.html"
                 target="_blank" rel="noreferrer"
                 className="text-blue-400 hover:underline">
                fred.stlouisfed.org
              </a>{" "}
              and add it to <code className="bg-white/5 px-1 rounded">backend/.env</code>.
            </p>
          )}
        </div>
      )}

      {/* ── Content ─────────────────────────────────────────────── */}
      {result && (
        <div className="space-y-4">
          {/* Row 1: VIX dual-line (full width) */}
          <VIXChart rows={chartRows} />

          {/* Row 2: VIX spread | US10Y */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <VIXSpreadChart rows={chartRows} />
            <SeriesChart
              title="US 10-Year Yield"
              subtitle="FRED DGS10"
              rows={chartRows}
              dataKey="us10y"
              color="#fbbf24"
              unit="%"
              yTickFmt={(v) => `${v.toFixed(1)}%`}
            />
          </div>

          {/* Row 3: Crude | Credit Spread */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <SeriesChart
              title="WTI Crude Oil"
              subtitle="FRED DCOILWTICO"
              rows={chartRows}
              dataKey="crude_oil"
              color="#f97316"
              unit=" $/bbl"
              yTickFmt={(v) => `$${v.toFixed(0)}`}
            />
            <SeriesChart
              title="HY Credit Spread"
              subtitle="FRED BAMLH0A0HYM2 (OAS)"
              rows={chartRows}
              dataKey="credit_spread"
              color="#f472b6"
              unit="%"
              yTickFmt={(v) => `${v.toFixed(1)}%`}
            />
          </div>

          {/* Latest values table */}
          <LatestTable stats={result.stats} breadthIsProxy={result.breadth_is_proxy} />
        </div>
      )}

      {/* ── Loading skeleton ──────────────────────────────────────── */}
      {loading && !result && (
        <div className="space-y-4">
          {[220, 180, 180].map((h, i) => (
            <div key={i} className={`rounded-xl bg-white/5 border border-white/10 animate-pulse`} style={{ height: h }} />
          ))}
        </div>
      )}

      {/* ── Empty state ─────────────────────────────────────────── */}
      {!result && !loading && !error && (
        <div className="flex flex-col items-center justify-center py-24 text-slate-600">
          <p className="text-4xl mb-4">📊</p>
          <p className="text-base font-medium">Loading macro data…</p>
        </div>
      )}
    </main>
  );
}
