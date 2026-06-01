"use client";

import { useState } from "react";
import Link from "next/link";
import { clsx } from "clsx";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { getPrices } from "@/lib/api";
import type { PricesResponse, OHLCVRow } from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TICKERS = ["SPY", "QQQ", "IWM", "AAPL", "NVDA", "TSLA"];

function fmt(n: number | null | undefined, decimals = 2): string {
  if (n == null) return "—";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtVol(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso + "T00:00:00").toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  });
}

// Thin the series for the chart (show at most 400 points)
function thinRows(rows: OHLCVRow[], maxPts = 400): OHLCVRow[] {
  if (rows.length <= maxPts) return rows;
  const step = Math.ceil(rows.length / maxPts);
  return rows.filter((_, i) => i % step === 0 || i === rows.length - 1);
}

// ---------------------------------------------------------------------------
// Stat card
// ---------------------------------------------------------------------------

function StatCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="rounded-xl bg-white/5 border border-white/10 px-5 py-4">
      <p className="text-xs text-slate-400 uppercase tracking-wider">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-white">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Custom tooltip
// ---------------------------------------------------------------------------

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  const row: OHLCVRow = payload[0].payload;
  return (
    <div className="rounded-lg bg-slate-800 border border-white/10 px-3 py-2 text-xs shadow-xl">
      <p className="font-semibold text-slate-300 mb-1">{fmtDate(row.date)}</p>
      <p className="text-emerald-400">Close  {fmt(row.close)}</p>
      {row.open  != null && <p className="text-slate-400">Open   {fmt(row.open)}</p>}
      {row.high  != null && <p className="text-slate-400">High   {fmt(row.high)}</p>}
      {row.low   != null && <p className="text-slate-400">Low    {fmt(row.low)}</p>}
      {row.volume != null && <p className="text-slate-500">Vol    {fmtVol(row.volume)}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DataPricesPage() {
  const [ticker,  setTicker]  = useState("SPY");
  const [days,    setDays]    = useState(400);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);
  const [result,  setResult]  = useState<PricesResponse | null>(null);

  async function handleFetch() {
    setLoading(true);
    setError(null);
    try {
      const data = await getPrices(ticker.trim().toUpperCase(), { days });
      setResult(data);
    } catch (e: any) {
      setError(e.message ?? "Unknown error");
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  const chartRows = result ? thinRows(result.rows) : [];

  // Price domain — add 2% padding
  const closes    = chartRows.map((r) => r.close).filter(Boolean) as number[];
  const yMin      = closes.length ? Math.min(...closes) * 0.98 : "auto";
  const yMax      = closes.length ? Math.max(...closes) * 1.02 : "auto";

  const isLive    = result?.provider === "real";
  const badgeCls  = isLive
    ? "bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30"
    : "bg-amber-500/15  text-amber-400  ring-1 ring-amber-500/30";

  return (
    <main className="min-h-screen bg-slate-950 text-white p-6 lg:p-10">
      {/* ── Header ────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between mb-8">
        <div>
          <Link
            href="/"
            className="text-xs text-slate-500 hover:text-slate-300 transition-colors mb-2 block"
          >
            ← Dashboard
          </Link>
          <h1 className="text-2xl font-bold tracking-tight">
            Price Data Diagnostic
          </h1>
          <p className="mt-1 text-sm text-slate-400">
            Inspect daily OHLCV bars served by the active data provider.
          </p>
        </div>

        {result && (
          <span
            className={clsx(
              "mt-1 inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-inset",
              badgeCls,
            )}
          >
            <span
              className={clsx(
                "h-1.5 w-1.5 rounded-full",
                isLive ? "bg-emerald-400" : "bg-amber-400",
              )}
            />
            {result.data_source}
          </span>
        )}
      </div>

      {/* ── Controls ──────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-end gap-3 mb-8">
        {/* Ticker quick-select */}
        <div className="flex gap-1.5">
          {TICKERS.map((t) => (
            <button
              key={t}
              onClick={() => setTicker(t)}
              className={clsx(
                "px-3 py-1.5 rounded-lg text-xs font-medium transition-colors",
                ticker === t
                  ? "bg-blue-600 text-white"
                  : "bg-white/5 text-slate-400 hover:bg-white/10 hover:text-white",
              )}
            >
              {t}
            </button>
          ))}
        </div>

        {/* Manual ticker input */}
        <div className="flex gap-2">
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && handleFetch()}
            placeholder="Ticker…"
            className="w-28 rounded-lg bg-white/5 border border-white/10 px-3 py-1.5 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        {/* Days select */}
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-400">Days</label>
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="rounded-lg bg-white/5 border border-white/10 px-2 py-1.5 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            {[60, 120, 252, 400, 504, 756].map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>

        <button
          onClick={handleFetch}
          disabled={loading || !ticker}
          className={clsx(
            "px-5 py-1.5 rounded-lg text-sm font-medium transition-colors",
            loading || !ticker
              ? "bg-blue-800/40 text-blue-400 cursor-not-allowed"
              : "bg-blue-600 hover:bg-blue-500 text-white",
          )}
        >
          {loading ? "Loading…" : "Fetch"}
        </button>
      </div>

      {/* ── Error ─────────────────────────────────────────────────── */}
      {error && (
        <div className="mb-6 rounded-xl bg-red-500/10 border border-red-500/30 px-5 py-4">
          <p className="text-sm font-semibold text-red-400 mb-1">Error</p>
          <p className="text-sm text-red-300">{error}</p>
        </div>
      )}

      {/* ── Stats grid ────────────────────────────────────────────── */}
      {result && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
            <StatCard
              label="Latest Close"
              value={`$${fmt(result.stats.latest_close)}`}
              sub={result.ticker}
            />
            <StatCard
              label="Rows"
              value={result.stats.n_rows.toLocaleString()}
              sub="trading days"
            />
            <StatCard
              label="First Date"
              value={fmtDate(result.stats.first_date)}
            />
            <StatCard
              label="Last Date"
              value={fmtDate(result.stats.last_date)}
            />
          </div>

          {/* ── Chart ─────────────────────────────────────────────── */}
          <div className="rounded-xl bg-white/5 border border-white/10 p-5 mb-6">
            <h2 className="text-sm font-semibold text-slate-300 mb-4">
              {result.ticker} — Close Price
            </h2>
            <ResponsiveContainer width="100%" height={320}>
              <AreaChart data={chartRows} margin={{ left: 0, right: 8, top: 4, bottom: 0 }}>
                <defs>
                  <linearGradient id="closeGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff0f" />
                <XAxis
                  dataKey="date"
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  tickFormatter={(d) => d.slice(0, 7)}   /* YYYY-MM */
                  interval="preserveStartEnd"
                  minTickGap={60}
                />
                <YAxis
                  domain={[yMin, yMax]}
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  tickFormatter={(v) => `$${v.toFixed(0)}`}
                  width={60}
                />
                <Tooltip content={<ChartTooltip />} />
                <Area
                  type="monotone"
                  dataKey="close"
                  stroke="#3b82f6"
                  strokeWidth={1.5}
                  fill="url(#closeGrad)"
                  dot={false}
                  activeDot={{ r: 3, fill: "#3b82f6" }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* ── Raw data table (first 10 + last 10 rows) ──────────── */}
          <div className="rounded-xl bg-white/5 border border-white/10 overflow-hidden">
            <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-300">
                Sample Rows
              </h2>
              <span className="text-xs text-slate-500">
                first 10 + last 10
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-white/10">
                    {["Date", "Open", "High", "Low", "Close", "Volume"].map((h) => (
                      <th
                        key={h}
                        className="px-4 py-2 text-right first:text-left font-medium text-slate-400 uppercase tracking-wider"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[
                    ...result.rows.slice(0, 10),
                    ...(result.rows.length > 20
                      ? [null]   // separator
                      : []),
                    ...result.rows.slice(-10),
                  ].map((row, i) =>
                    row === null ? (
                      <tr key="sep" className="border-b border-white/5">
                        <td
                          colSpan={6}
                          className="py-1 text-center text-slate-600 text-xs italic"
                        >
                          · · · {result.rows.length - 20} rows hidden · · ·
                        </td>
                      </tr>
                    ) : (
                      <tr
                        key={row.date + i}
                        className="border-b border-white/5 hover:bg-white/5 transition-colors"
                      >
                        <td className="px-4 py-2 text-slate-300 tabular-nums">{row.date}</td>
                        <td className="px-4 py-2 text-right tabular-nums text-slate-400">
                          {row.open != null ? `$${fmt(row.open)}` : "—"}
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums text-slate-400">
                          {row.high != null ? `$${fmt(row.high)}` : "—"}
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums text-slate-400">
                          {row.low != null ? `$${fmt(row.low)}` : "—"}
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums font-medium text-white">
                          ${fmt(row.close)}
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums text-slate-400">
                          {fmtVol(row.volume)}
                        </td>
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* ── Empty state ───────────────────────────────────────────── */}
      {!result && !loading && !error && (
        <div className="flex flex-col items-center justify-center py-24 text-slate-600">
          <p className="text-4xl mb-4">📈</p>
          <p className="text-base font-medium">Select a ticker and click Fetch</p>
          <p className="text-sm mt-1">
            Uses{" "}
            <span className="font-mono bg-white/5 px-1 rounded">
              GET /api/data/prices/&#123;ticker&#125;
            </span>
          </p>
        </div>
      )}
    </main>
  );
}
