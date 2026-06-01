"use client";

/**
 * /data/historical-iv — Historical IV Surface Page
 *
 * Displays the persisted ATM IV surfaces for a selected ticker:
 *   • IV30 history line chart (colour-coded by data quality)
 *   • All-tenor term structure history (iv7 / iv30 / iv60 / iv90 on one chart)
 *   • IV vs IV30 ratio chart — highlights term-structure inversions
 *   • Inversion periods table (dates where iv7 > iv30)
 *   • Data-quality summary cards (real / synthetic / interpolated counts)
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  getHistoricalIV,
  type HistoricalIVResponse,
  type HistoricalIVRow,
  type IVDataQualitySummary,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TICKERS    = ["SPY", "QQQ", "IWM", "AAPL", "NVDA", "TSLA"];
const DAY_RANGES = [
  { label: "90 d",  value: 90  },
  { label: "180 d", value: 180 },
  { label: "1 yr",  value: 252 },
  { label: "2 yr",  value: 504 },
];

// Data-quality colours
const Q_COLORS: Record<string, string> = {
  real:               "#4ade80", // green-400
  interpolated:       "#facc15", // yellow-400
  synthetic_fallback: "#94a3b8", // slate-400
};

const Q_LABELS: Record<string, string> = {
  real:               "Live options chain",
  interpolated:       "Interpolated",
  synthetic_fallback: "Synthetic (OU model)",
};

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(2)}%`;
}

function qualityDot(q: HistoricalIVRow["iv_data_quality"]) {
  return (
    <span
      className="inline-block w-2 h-2 rounded-full mr-1"
      style={{ background: Q_COLORS[q] ?? "#64748b" }}
    />
  );
}

// ---------------------------------------------------------------------------
// Derived types for charts
// ---------------------------------------------------------------------------

interface ChartRow {
  date:    string;
  iv7:     number | null;
  iv30:    number | null;
  iv60:    number | null;
  iv90:    number | null;
  quality: HistoricalIVRow["iv_data_quality"];
  // For split-series colouring in IV30 chart
  iv30_real:  number | null;
  iv30_synth: number | null;
}

interface InversionRow {
  date:   string;
  iv7:    number;
  iv30:   number;
  spread: number; // iv7 - iv30; positive = inverted
}

// ---------------------------------------------------------------------------
// Data transforms
// ---------------------------------------------------------------------------

function toChartRows(rows: HistoricalIVRow[]): ChartRow[] {
  return rows.map((r) => ({
    date:       r.date,
    iv7:        r.iv7,
    iv30:       r.iv30,
    iv60:       r.iv60,
    iv90:       r.iv90,
    quality:    r.iv_data_quality,
    iv30_real:  r.iv_data_quality === "real" ? r.iv30 : null,
    iv30_synth: r.iv_data_quality !== "real" ? r.iv30 : null,
  }));
}

function findInversions(rows: HistoricalIVRow[]): InversionRow[] {
  return rows
    .filter((r) => r.iv7 != null && r.iv30 != null && r.iv7 > r.iv30)
    .map((r) => ({
      date:   r.date,
      iv7:    r.iv7!,
      iv30:   r.iv30!,
      spread: +(r.iv7! - r.iv30!).toFixed(2),
    }))
    .slice(-50); // show last 50 inversion days
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function QualityCards({ summary }: { summary: IVDataQualitySummary }) {
  const cards = [
    {
      label: "Live data",
      count: summary.real,
      pct: summary.total ? (summary.real / summary.total) * 100 : 0,
      color: "text-green-400 border-green-400/30 bg-green-400/5",
    },
    {
      label: "Interpolated",
      count: summary.interpolated,
      pct: summary.total ? (summary.interpolated / summary.total) * 100 : 0,
      color: "text-yellow-400 border-yellow-400/30 bg-yellow-400/5",
    },
    {
      label: "Synthetic",
      count: summary.synthetic_fallback,
      pct: summary.total ? (summary.synthetic_fallback / summary.total) * 100 : 0,
      color: "text-slate-400 border-slate-600/30 bg-slate-800/40",
    },
    {
      label: "Total rows",
      count: summary.total,
      pct: 100,
      color: "text-slate-200 border-slate-600/30 bg-slate-800/40",
    },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
      {cards.map((c) => (
        <div
          key={c.label}
          className={`rounded-lg border p-3 ${c.color}`}
        >
          <p className="text-xs uppercase tracking-wide opacity-60 mb-1">{c.label}</p>
          <p className="text-xl font-semibold">{c.count.toLocaleString()}</p>
          <p className="text-xs opacity-50 mt-0.5">{c.pct.toFixed(1)}%</p>
        </div>
      ))}
    </div>
  );
}

// IV30 history chart with real/synthetic split series
function IV30Chart({ data }: { data: ChartRow[] }) {
  const thinned = data.filter((_, i) => i % Math.max(1, Math.floor(data.length / 300)) === 0 || i === data.length - 1);

  return (
    <div className="bg-slate-900/70 border border-slate-700/50 rounded-xl p-4 mb-6">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-slate-200">IV30 History</h2>
        <div className="flex gap-4 text-xs text-slate-400">
          <span>
            <span className="inline-block w-3 h-0.5 bg-green-400 mr-1 align-middle" />
            Live
          </span>
          <span>
            <span className="inline-block w-3 h-0.5 bg-slate-500 mr-1 align-middle" />
            Synthetic
          </span>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={thinned} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" strokeOpacity={0.5} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: "#94a3b8" }}
            tickFormatter={(d: string) => d.slice(0, 7)}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 10, fill: "#94a3b8" }}
            tickFormatter={(v: number) => `${v.toFixed(0)}%`}
            width={38}
          />
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }}
            formatter={(v: number) => [`${v?.toFixed(2)}%`]}
            labelStyle={{ color: "#cbd5e1" }}
          />
          {/* Synthetic series underneath in muted colour */}
          <Line
            dataKey="iv30_synth"
            name="IV30 (synthetic)"
            stroke="#64748b"
            strokeWidth={1.5}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
          {/* Real series on top in green */}
          <Line
            dataKey="iv30_real"
            name="IV30 (live)"
            stroke="#4ade80"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// All-tenor chart
function AllTenorChart({ data }: { data: ChartRow[] }) {
  const thinned = data.filter((_, i) => i % Math.max(1, Math.floor(data.length / 300)) === 0 || i === data.length - 1);

  return (
    <div className="bg-slate-900/70 border border-slate-700/50 rounded-xl p-4 mb-6">
      <h2 className="text-sm font-semibold text-slate-200 mb-3">
        Term Structure History (all tenors)
      </h2>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={thinned} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" strokeOpacity={0.5} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: "#94a3b8" }}
            tickFormatter={(d: string) => d.slice(0, 7)}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 10, fill: "#94a3b8" }}
            tickFormatter={(v: number) => `${v.toFixed(0)}%`}
            width={38}
          />
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }}
            formatter={(v: number) => [`${v?.toFixed(2)}%`]}
            labelStyle={{ color: "#cbd5e1" }}
          />
          <Legend
            wrapperStyle={{ fontSize: 11, color: "#94a3b8", paddingTop: 8 }}
          />
          <Line dataKey="iv7"  name="IV7"  stroke="#f87171" strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} />
          <Line dataKey="iv30" name="IV30" stroke="#60a5fa" strokeWidth={2}   dot={false} connectNulls isAnimationActive={false} />
          <Line dataKey="iv60" name="IV60" stroke="#a78bfa" strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} />
          <Line dataKey="iv90" name="IV90" stroke="#fb923c" strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// IV7 − IV30 spread chart (positive = short-end > long-end = inversion)
function SpreadChart({ data }: { data: ChartRow[] }) {
  const spreadData = data
    .filter((r) => r.iv7 != null && r.iv30 != null)
    .map((r) => ({
      date:   r.date,
      spread: +((r.iv7! - r.iv30!)).toFixed(3),
    }));

  const thinned = spreadData.filter((_, i) => i % Math.max(1, Math.floor(spreadData.length / 300)) === 0 || i === spreadData.length - 1);

  return (
    <div className="bg-slate-900/70 border border-slate-700/50 rounded-xl p-4 mb-6">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-sm font-semibold text-slate-200">
          IV7 − IV30 Spread
        </h2>
        <span className="text-xs text-slate-500">positive = front-end inversion</span>
      </div>
      <ResponsiveContainer width="100%" height={160}>
        <AreaChart data={thinned} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" strokeOpacity={0.5} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: "#94a3b8" }}
            tickFormatter={(d: string) => d.slice(0, 7)}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 10, fill: "#94a3b8" }}
            tickFormatter={(v: number) => `${v.toFixed(1)}%`}
            width={40}
          />
          <Tooltip
            contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }}
            formatter={(v: number) => [`${v?.toFixed(2)}%`, "IV7 − IV30"]}
            labelStyle={{ color: "#cbd5e1" }}
          />
          <ReferenceLine y={0} stroke="#475569" strokeDasharray="4 2" />
          <Area
            dataKey="spread"
            stroke="#f59e0b"
            fill="#f59e0b"
            fillOpacity={0.15}
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// Inversion periods table
function InversionTable({ rows }: { rows: InversionRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="bg-slate-900/70 border border-slate-700/50 rounded-xl p-4 mb-6">
        <h2 className="text-sm font-semibold text-slate-200 mb-2">Term Structure Inversions</h2>
        <p className="text-xs text-slate-500 italic">No inversion days (IV7 &gt; IV30) found in this date range.</p>
      </div>
    );
  }

  return (
    <div className="bg-slate-900/70 border border-slate-700/50 rounded-xl p-4 mb-6">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-slate-200">Term Structure Inversions</h2>
        <span className="text-xs text-amber-400 bg-amber-400/10 border border-amber-400/20 rounded px-2 py-0.5">
          {rows.length} day{rows.length !== 1 ? "s" : ""}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs text-slate-300">
          <thead>
            <tr className="text-slate-500 border-b border-slate-700">
              <th className="text-left pb-2 font-medium">Date</th>
              <th className="text-right pb-2 font-medium">IV7</th>
              <th className="text-right pb-2 font-medium">IV30</th>
              <th className="text-right pb-2 font-medium">Spread</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {rows.map((r) => (
              <tr key={r.date} className="hover:bg-slate-800/40">
                <td className="py-1.5">{r.date}</td>
                <td className="text-right text-amber-400">{r.iv7.toFixed(2)}%</td>
                <td className="text-right">{r.iv30.toFixed(2)}%</td>
                <td className="text-right text-red-400">+{r.spread.toFixed(2)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Recent data table (last 20 rows)
function RecentDataTable({ rows }: { rows: HistoricalIVRow[] }) {
  const recent = [...rows].reverse().slice(0, 20);

  return (
    <div className="bg-slate-900/70 border border-slate-700/50 rounded-xl p-4 mb-6">
      <h2 className="text-sm font-semibold text-slate-200 mb-3">Recent IV Surface (last 20 days)</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-xs text-slate-300">
          <thead>
            <tr className="text-slate-500 border-b border-slate-700">
              <th className="text-left pb-2 font-medium">Date</th>
              <th className="text-right pb-2 font-medium">IV7</th>
              <th className="text-right pb-2 font-medium">IV30</th>
              <th className="text-right pb-2 font-medium">IV60</th>
              <th className="text-right pb-2 font-medium">IV90</th>
              <th className="text-left pb-2 font-medium pl-3">Source</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {recent.map((r) => (
              <tr key={r.date} className="hover:bg-slate-800/40">
                <td className="py-1.5">{r.date}</td>
                <td className="text-right">{fmtPct(r.iv7)}</td>
                <td className="text-right font-medium">{fmtPct(r.iv30)}</td>
                <td className="text-right">{fmtPct(r.iv60)}</td>
                <td className="text-right">{fmtPct(r.iv90)}</td>
                <td className="pl-3">
                  <span className="flex items-center gap-1">
                    {qualityDot(r.iv_data_quality)}
                    <span style={{ color: Q_COLORS[r.iv_data_quality] ?? "#94a3b8" }}>
                      {r.iv_data_quality === "real" ? "live" : r.iv_data_quality === "interpolated" ? "interp" : "synth"}
                    </span>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export default function HistoricalIVPage() {
  const [ticker, setTicker]   = useState<string>("SPY");
  const [days,   setDays]     = useState<number>(252);
  const [data,   setData]     = useState<HistoricalIVResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState<string | null>(null);

  const fetchRef = useRef<AbortController | null>(null);

  const load = useCallback(
    async (sym: string, d: number) => {
      fetchRef.current?.abort();
      const ctrl = new AbortController();
      fetchRef.current = ctrl;

      setLoading(true);
      setError(null);

      try {
        const res = await getHistoricalIV(sym, { days: d });
        if (!ctrl.signal.aborted) {
          setData(res);
        }
      } catch (e: unknown) {
        if (!ctrl.signal.aborted) {
          setError(e instanceof Error ? e.message : "Failed to fetch historical IV");
        }
      } finally {
        if (!ctrl.signal.aborted) {
          setLoading(false);
        }
      }
    },
    [],
  );

  // Initial fetch
  useEffect(() => {
    void load(ticker, days);
  }, [ticker, days, load]);

  // ---------------------------------------------------------------------------
  // Derived chart data
  // ---------------------------------------------------------------------------
  const chartRows  = data?.rows ? toChartRows(data.rows) : [];
  const inversions = data?.rows ? findInversions(data.rows) : [];

  const realCount  = data?.data_quality_summary.real ?? 0;
  const totalCount = data?.data_quality_summary.total ?? 0;
  const realPct    = totalCount ? ((realCount / totalCount) * 100).toFixed(1) : "0.0";

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 p-4 md:p-8">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="max-w-5xl mx-auto mb-6">
        <div className="flex items-start justify-between flex-wrap gap-3">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Link href="/" className="text-xs text-slate-500 hover:text-slate-300 transition-colors">
                ← Dashboard
              </Link>
              <span className="text-slate-700">/</span>
              <Link href="/data/options" className="text-xs text-slate-500 hover:text-slate-300 transition-colors">
                Options chain
              </Link>
            </div>
            <h1 className="text-2xl font-bold text-slate-100">Historical IV Surface</h1>
            <p className="text-sm text-slate-400 mt-1">
              Persisted ATM implied volatility by tenor · Parquet store ·{" "}
              {realCount > 0
                ? <span className="text-green-400">{realCount} live rows ({realPct}%)</span>
                : <span className="text-slate-500">no live data yet — run backfill script</span>
              }
            </p>
          </div>

          {/* Controls */}
          <div className="flex gap-2 flex-wrap items-center">
            {/* Ticker selector */}
            <div className="flex rounded-lg overflow-hidden border border-slate-700 text-xs">
              {TICKERS.map((t) => (
                <button
                  key={t}
                  onClick={() => setTicker(t)}
                  className={`px-3 py-1.5 transition-colors ${
                    ticker === t
                      ? "bg-blue-600 text-white font-semibold"
                      : "bg-slate-800 text-slate-400 hover:bg-slate-700"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>

            {/* Day range selector */}
            <div className="flex rounded-lg overflow-hidden border border-slate-700 text-xs">
              {DAY_RANGES.map((r) => (
                <button
                  key={r.value}
                  onClick={() => setDays(r.value)}
                  className={`px-3 py-1.5 transition-colors ${
                    days === r.value
                      ? "bg-slate-600 text-white font-semibold"
                      : "bg-slate-800 text-slate-400 hover:bg-slate-700"
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-5xl mx-auto">

        {/* ── Loading / error states ─────────────────────────────────────── */}
        {loading && (
          <div className="flex items-center justify-center h-48 text-slate-400">
            <div className="flex items-center gap-2">
              <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              Loading historical IV…
            </div>
          </div>
        )}

        {error && !loading && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 mb-6 text-red-400 text-sm">
            ⚠ {error}
          </div>
        )}

        {/* ── Empty-store notice ─────────────────────────────────────────── */}
        {!loading && !error && data && !data.store_populated && (
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-5 mb-6">
            <p className="text-amber-400 font-semibold mb-1">No data in store for {ticker}</p>
            <p className="text-slate-400 text-sm mb-3">
              Run the backfill script from the <code className="bg-slate-800 px-1 rounded text-xs">backend/</code> directory to populate the store:
            </p>
            <pre className="text-xs bg-slate-900 border border-slate-700 rounded p-3 text-slate-300 overflow-x-auto">
{`# Seed synthetic data for the last 252 trading days
python -m scripts.backfill_iv_surface --tickers ${ticker} --verbose

# Ingest today's live options chain (requires Polygon API key)
DATA_PROVIDER=real python -m scripts.backfill_iv_surface --tickers ${ticker} --today-only`}
            </pre>
            {data.message && (
              <p className="text-slate-500 text-xs mt-2 italic">{data.message}</p>
            )}
          </div>
        )}

        {/* ── Data quality summary ───────────────────────────────────────── */}
        {!loading && data && data.store_populated && (
          <>
            <QualityCards summary={data.data_quality_summary} />

            {/* Date range info */}
            {data.n_rows > 0 && (
              <p className="text-xs text-slate-500 mb-4">
                Showing <strong className="text-slate-300">{data.n_rows.toLocaleString()}</strong> daily surfaces
                from <strong className="text-slate-300">{data.date_range.start}</strong>
                {" "}to{" "}
                <strong className="text-slate-300">{data.date_range.end}</strong>
                {" "}· ticker <strong className="text-slate-300">{data.ticker}</strong>
              </p>
            )}

            {/* ── Charts ──────────────────────────────────────────────── */}
            {chartRows.length > 0 && (
              <>
                <IV30Chart data={chartRows} />
                <AllTenorChart data={chartRows} />
                <SpreadChart data={chartRows} />
              </>
            )}

            {/* ── Inversion table ──────────────────────────────────────── */}
            <InversionTable rows={inversions} />

            {/* ── Recent data table ────────────────────────────────────── */}
            {data.rows.length > 0 && <RecentDataTable rows={data.rows} />}

            {/* ── Legend ───────────────────────────────────────────────── */}
            <div className="bg-slate-900/70 border border-slate-700/50 rounded-xl p-4">
              <h2 className="text-sm font-semibold text-slate-200 mb-3">Data Quality Legend</h2>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {Object.entries(Q_LABELS).map(([key, label]) => (
                  <div key={key} className="flex items-start gap-2">
                    <span
                      className="inline-block w-3 h-3 rounded-full mt-0.5 flex-shrink-0"
                      style={{ background: Q_COLORS[key] }}
                    />
                    <div>
                      <p className="text-xs font-medium" style={{ color: Q_COLORS[key] }}>
                        {key === "real" ? "Real" : key === "interpolated" ? "Interpolated" : "Synthetic"}
                      </p>
                      <p className="text-xs text-slate-500">{label}</p>
                    </div>
                  </div>
                ))}
              </div>
              <p className="text-xs text-slate-600 mt-3 border-t border-slate-800 pt-3">
                IV values are annualised percentages (e.g. 25.00 = 25% p.a.).
                Real data comes from live Polygon options chains via{" "}
                <code className="text-slate-500 text-[10px]">calculate_atm_iv_by_tenor()</code>.
                Synthetic data uses an Ornstein–Uhlenbeck mean-reversion model calibrated to each ticker.
              </p>
            </div>
          </>
        )}
      </div>
    </main>
  );
}
