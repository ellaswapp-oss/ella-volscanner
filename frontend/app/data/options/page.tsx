"use client";

/**
 * /data/options — Options Chain Diagnostic Page
 *
 * Displays the current options chain for a selected ticker along with:
 *   • Five-point IV term structure chart (7D → 90D)
 *   • IV surface summary cards
 *   • ATM contracts table (call IV / put IV per tenor)
 *   • Full chain table with expiration and type filters
 */

import React, { useCallback, useState } from "react";
import Link from "next/link";
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
import {
  getIVSurface,
  getIVSurfaceHealth,
  getOptionsChain,
  type ATMContract,
  type IVContractDetail,
  type IVSurfaceHealthResponse,
  type IVSurfaceResponse,
  type IVSurfaceWarning,
  type IVTenorDetail,
  type OptionRow,
  type OptionsChainResponse,
  type SpreadQuality,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TICKERS = ["SPY", "QQQ", "IWM", "AAPL", "NVDA", "TSLA"];
const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(v: number | null | undefined, decimals = 2): string {
  if (v == null) return "—";
  return v.toFixed(decimals);
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toFixed(2)}%`;
}

function typeColor(t: string | null): string {
  if (t === "call") return "text-green-400";
  if (t === "put")  return "text-red-400";
  return "text-slate-400";
}

function deltaColor(d: number | null): string {
  if (d == null) return "text-slate-300";
  if (d > 0.3)  return "text-green-300";
  if (d < -0.3) return "text-red-300";
  return "text-slate-300";
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SurfaceCard({
  label,
  iv,
}: {
  label: string;
  iv: number | null;
}) {
  const color =
    iv == null
      ? "text-slate-500"
      : iv < 15
      ? "text-green-400"
      : iv < 25
      ? "text-yellow-400"
      : "text-red-400";

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 text-center">
      <div className="text-xs text-slate-400 uppercase tracking-wide mb-1">
        {label}
      </div>
      <div className={`text-2xl font-bold ${color}`}>
        {iv != null ? `${iv.toFixed(1)}%` : "—"}
      </div>
    </div>
  );
}

const ALL_TENORS = ["7D", "14D", "30D", "60D", "90D"];

function TermStructureChart({ curve }: { curve: IVSurfaceResponse["curve"] }) {
  // Only plot tenors with non-null IV (tolerance-rejected tenors are absent)
  const plotData = curve.filter((p) => p.iv != null);
  const plottedTenors = new Set(plotData.map((p) => p.tenor));
  const missingTenors = ALL_TENORS.filter((t) => !plottedTenors.has(t));

  if (!plotData.length) return null;

  const ivVals     = plotData.map((p) => p.iv as number);
  const minIV      = Math.min(...ivVals);
  const maxIV      = Math.max(...ivVals);
  const isContango = plotData[plotData.length - 1].iv! >= plotData[0].iv!;

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-5">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="text-sm font-semibold text-slate-300 uppercase tracking-wide">
          IV Term Structure
        </h3>
        <div className="flex items-center gap-2 flex-wrap">
          {missingTenors.map((t) => (
            <span
              key={t}
              title="Outside DTE tolerance — no valid expiration found"
              className="inline-flex items-center gap-1 rounded border border-amber-600/40 bg-amber-900/20 px-1.5 py-0.5 text-[10px] font-semibold text-amber-400"
            >
              <span>○</span> {t} missing
            </span>
          ))}
          <span
            className={`text-xs font-medium px-2 py-0.5 rounded-full ${
              isContango
                ? "bg-green-900/40 text-green-400"
                : "bg-red-900/40 text-red-400"
            }`}
          >
            {isContango ? "Contango" : "Backwardation"}
          </span>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={plotData} margin={{ top: 4, right: 20, left: 0, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
          <XAxis
            dataKey="tenor"
            tick={{ fill: "#94a3b8", fontSize: 12 }}
            axisLine={{ stroke: "#475569" }}
          />
          <YAxis
            domain={[
              Math.floor(minIV * 0.95),
              Math.ceil(maxIV * 1.05),
            ]}
            tick={{ fill: "#94a3b8", fontSize: 11 }}
            axisLine={{ stroke: "#475569" }}
            tickFormatter={(v) => `${v}%`}
            width={46}
          />
          <Tooltip
            contentStyle={{ backgroundColor: "#1e293b", border: "1px solid #475569" }}
            formatter={(v: number) => [`${v.toFixed(2)}%`, "IV"]}
          />
          <ReferenceLine
            y={(minIV + maxIV) / 2}
            stroke="#475569"
            strokeDasharray="4 4"
          />
          <Line
            type="monotone"
            dataKey="iv"
            stroke="#6366f1"
            strokeWidth={2.5}
            dot={{ fill: "#6366f1", r: 5 }}
            activeDot={{ r: 7 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function ATMTable({ contracts }: { contracts: ATMContract[] }) {
  if (!contracts.length) return null;
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 overflow-hidden">
      <div className="px-5 py-3 border-b border-slate-700">
        <h3 className="text-sm font-semibold text-slate-300 uppercase tracking-wide">
          ATM Contracts by Tenor
        </h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 bg-slate-900/50">
              {["Tenor", "DTE", "Expiration", "Strike", "Call IV", "Put IV", "Avg IV"].map(
                (h) => (
                  <th
                    key={h}
                    className="px-4 py-2.5 text-xs font-medium text-slate-400 uppercase tracking-wide text-right first:text-left"
                  >
                    {h}
                  </th>
                )
              )}
            </tr>
          </thead>
          <tbody>
            {contracts.map((c) => (
              <tr
                key={c.tenor}
                className="border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors"
              >
                <td className="px-4 py-2.5 font-mono font-semibold text-indigo-400">
                  {c.tenor}
                </td>
                <td className="px-4 py-2.5 text-right text-slate-300">{c.dte}</td>
                <td className="px-4 py-2.5 text-right font-mono text-slate-300">
                  {c.expiration}
                </td>
                <td className="px-4 py-2.5 text-right text-slate-200">
                  {fmt(c.strike)}
                </td>
                <td className="px-4 py-2.5 text-right text-green-400">
                  {fmtPct(c.call_iv)}
                </td>
                <td className="px-4 py-2.5 text-right text-red-400">
                  {fmtPct(c.put_iv)}
                </td>
                <td className="px-4 py-2.5 text-right font-semibold text-slate-200">
                  {fmtPct(c.avg_iv)}
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
// IV Surface Health components
// ---------------------------------------------------------------------------

const HEALTH_STYLES: Record<string, string> = {
  healthy:    "bg-green-900/30 border-green-600/50 text-green-400",
  suspicious: "bg-amber-900/30 border-amber-600/50 text-amber-400",
  invalid:    "bg-red-900/30 border-red-600/50 text-red-400",
};
const HEALTH_ICON: Record<string, string> = {
  healthy: "✓", suspicious: "⚠", invalid: "✗",
};
const SPREAD_STYLES: Record<SpreadQuality, string> = {
  TIGHT:     "bg-green-900/40 text-green-400 border-green-700/40",
  FAIR:      "bg-blue-900/40 text-blue-400 border-blue-700/40",
  WIDE:      "bg-amber-900/40 text-amber-400 border-amber-700/40",
  VERY_WIDE: "bg-red-900/40 text-red-400 border-red-700/40",
  NO_QUOTE:  "bg-slate-800 text-slate-500 border-slate-700",
};
const WARN_SEVERITY_STYLES: Record<string, string> = {
  warning: "text-amber-400",
  invalid: "text-red-400",
};
const WARN_CODE_ICON: Record<string, string> = {
  missing_tenor:   "○",
  iv_out_of_range: "⊗",
  tenor_jump:      "↕",
  duplicate_iv:    "≈",
};

function HealthBadge({ status }: { status: IVSurfaceHealthResponse["status"] }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${HEALTH_STYLES[status]}`}
    >
      <span>{HEALTH_ICON[status]}</span>
      Surface: {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}

function WarningsPanel({ warnings }: { warnings: IVSurfaceWarning[] }) {
  if (!warnings.length) {
    return (
      <div className="flex items-center gap-2 text-sm text-green-400">
        <span>✓</span>
        <span>No surface warnings detected.</span>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {warnings.map((w, i) => (
        <div
          key={i}
          className={`flex items-start gap-3 rounded-lg border border-slate-700 bg-slate-900/50 px-3 py-2 text-xs ${WARN_SEVERITY_STYLES[w.severity] ?? "text-slate-300"}`}
        >
          <span className="mt-px text-sm font-bold shrink-0">
            {WARN_CODE_ICON[w.code] ?? "!"}
          </span>
          <div>
            <span className="font-semibold uppercase tracking-wide opacity-70 mr-1.5">
              {w.code.replace(/_/g, " ")}
            </span>
            {w.message}
          </div>
        </div>
      ))}
    </div>
  );
}

function SpreadBadge({ quality }: { quality: SpreadQuality }) {
  return (
    <span
      className={`inline-flex rounded border px-1.5 py-0.5 text-[10px] font-semibold tracking-wide ${SPREAD_STYLES[quality]}`}
    >
      {quality.replace("_", " ")}
    </span>
  );
}

function ContractCell({ c, label }: { c: IVContractDetail | null; label: string }) {
  if (!c) return <td className="px-3 py-2.5 text-slate-600 text-center" colSpan={4}>—</td>;
  return (
    <>
      <td className="px-3 py-2.5 text-right text-slate-200 font-mono">
        {c.iv != null ? `${c.iv.toFixed(2)}%` : "—"}
      </td>
      <td className="px-3 py-2.5 text-right text-slate-400">
        {c.bid != null && c.ask != null ? `${c.bid.toFixed(2)} / ${c.ask.toFixed(2)}` : "—"}
      </td>
      <td className="px-3 py-2.5 text-right text-slate-400">
        {c.spread_pct != null ? `${c.spread_pct.toFixed(1)}%` : "—"}
      </td>
      <td className="px-3 py-2.5 text-right">
        <SpreadBadge quality={c.spread_quality} />
      </td>
    </>
  );
}

function TenorDetailsTable({ details }: { details: IVTenorDetail[] }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 overflow-hidden">
      <div className="px-5 py-3 border-b border-slate-700">
        <h3 className="text-sm font-semibold text-slate-300 uppercase tracking-wide">
          Selected ATM Contracts &amp; Spread Quality
        </h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-700 bg-slate-900/50">
              <th className="px-3 py-2 text-left text-slate-400 uppercase tracking-wide">Tenor</th>
              <th className="px-3 py-2 text-left text-slate-400 uppercase tracking-wide">Expiration</th>
              <th className="px-3 py-2 text-right text-slate-400 uppercase tracking-wide">DTE</th>
              <th className="px-3 py-2 text-right text-slate-400 uppercase tracking-wide">Diff</th>
              <th className="px-3 py-2 text-right text-slate-400 uppercase tracking-wide">Avg IV</th>
              {/* Call cols */}
              <th className="px-3 py-2 text-right text-green-500/70 uppercase tracking-wide">Call IV</th>
              <th className="px-3 py-2 text-right text-green-500/70 uppercase tracking-wide">Call B/A</th>
              <th className="px-3 py-2 text-right text-green-500/70 uppercase tracking-wide">C Sprd%</th>
              <th className="px-3 py-2 text-right text-green-500/70 uppercase tracking-wide">C Quality</th>
              {/* Put cols */}
              <th className="px-3 py-2 text-right text-red-500/70 uppercase tracking-wide">Put IV</th>
              <th className="px-3 py-2 text-right text-red-500/70 uppercase tracking-wide">Put B/A</th>
              <th className="px-3 py-2 text-right text-red-500/70 uppercase tracking-wide">P Sprd%</th>
              <th className="px-3 py-2 text-right text-red-500/70 uppercase tracking-wide">P Quality</th>
            </tr>
          </thead>
          <tbody>
            {details.map((d) => (
              <tr
                key={d.tenor_key}
                className={`border-b border-slate-700/40 hover:bg-slate-700/20 ${
                  !d.within_tolerance ? "opacity-50" : ""
                }`}
              >
                <td className="px-3 py-2.5 font-mono font-semibold text-indigo-400">
                  {d.tenor}
                  {!d.within_tolerance && (
                    <span className="ml-1 text-amber-400" title="Outside DTE tolerance">○</span>
                  )}
                </td>
                <td className="px-3 py-2.5 font-mono text-slate-300">
                  {d.expiration ?? <span className="text-slate-600 italic">out of tolerance</span>}
                </td>
                <td className="px-3 py-2.5 text-right text-slate-300">
                  {d.dte_actual != null ? `${d.dte_actual}d` : "—"}
                </td>
                <td className={`px-3 py-2.5 text-right ${
                  d.dte_diff != null && d.dte_diff > 0
                    ? d.within_tolerance ? "text-slate-400" : "text-amber-400"
                    : "text-slate-600"
                }`}>
                  {d.dte_diff != null ? (d.dte_diff === 0 ? "exact" : `+${d.dte_diff}d`) : "—"}
                </td>
                <td className="px-3 py-2.5 text-right font-semibold text-slate-200">
                  {d.avg_iv != null ? `${d.avg_iv.toFixed(2)}%` : "—"}
                </td>
                {d.within_tolerance ? (
                  <>
                    <ContractCell c={d.call} label="call" />
                    <ContractCell c={d.put} label="put" />
                  </>
                ) : (
                  <td colSpan={8} className="px-3 py-2.5 text-center text-slate-600 italic text-xs">
                    nearest expiration {d.dte_actual}DTE is outside ±{
                      d.dte_target === 7 ? 4 : d.dte_target === 14 ? 7 :
                      d.dte_target === 30 ? 10 : d.dte_target === 60 ? 14 : 21
                    }d tolerance
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ExpirationList({ expirations }: { expirations: string[] }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-4">
      <div className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">
        Source Expirations ({expirations.length})
      </div>
      <div className="flex flex-wrap gap-1.5">
        {expirations.map((exp) => (
          <span
            key={exp}
            className="rounded border border-slate-600 bg-slate-900/60 px-2 py-0.5 font-mono text-xs text-slate-300"
          >
            {exp}
          </span>
        ))}
      </div>
    </div>
  );
}

function IVSurfaceHealthSection({ health }: { health: IVSurfaceHealthResponse }) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800/50 p-5 mb-6 space-y-5">
      {/* Header row */}
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-sm font-semibold text-slate-300 uppercase tracking-wide">
          Surface Health Diagnostics
        </h2>
        <HealthBadge status={health.status} />
        <span className="text-xs text-slate-500">
          {health.n_expirations} expirations · spot ${health.underlying_price.toFixed(2)} · {health.pricing_date}
        </span>
      </div>

      {/* Warnings */}
      <WarningsPanel warnings={health.warnings} />

      {/* Tenor details table */}
      <TenorDetailsTable details={health.tenor_details} />

      {/* Expiration list */}
      <ExpirationList expirations={health.source_expirations} />
    </div>
  );
}

function ChainTable({
  rows,
  expFilter,
  typeFilter,
}: {
  rows:       OptionRow[];
  expFilter:  string;
  typeFilter: string;
}) {
  const [page, setPage] = useState(0);

  // Client-side filter (on top of server-side)
  const filtered = rows.filter((r) => {
    if (expFilter  && r.expiration  !== expFilter)                  return false;
    if (typeFilter && r.option_type !== typeFilter)                  return false;
    return true;
  });

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
  const visible    = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const expirations = Array.from(new Set(rows.map((r) => r.expiration))).sort();

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 overflow-hidden">
      <div className="flex items-center gap-4 px-5 py-3 border-b border-slate-700 flex-wrap">
        <h3 className="text-sm font-semibold text-slate-300 uppercase tracking-wide">
          Chain ({filtered.length} contracts)
        </h3>
        <span className="text-xs text-slate-500">
          Page {page + 1} / {Math.max(1, totalPages)}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-700 bg-slate-900/50">
              {["Exp", "Type", "Strike", "Bid", "Ask", "Mid", "IV %", "Delta", "Gamma", "Theta", "Vega", "OI", "Vol"].map(
                (h) => (
                  <th
                    key={h}
                    className="px-3 py-2 font-medium text-slate-400 uppercase tracking-wide text-right first:text-left"
                  >
                    {h}
                  </th>
                )
              )}
            </tr>
          </thead>
          <tbody>
            {visible.map((r, i) => (
              <tr
                key={i}
                className="border-b border-slate-700/30 hover:bg-slate-700/20 transition-colors"
              >
                <td className="px-3 py-1.5 font-mono text-slate-300">{r.expiration}</td>
                <td className={`px-3 py-1.5 font-semibold uppercase ${typeColor(r.option_type)}`}>
                  {r.option_type ?? "—"}
                </td>
                <td className="px-3 py-1.5 text-right text-slate-200">{fmt(r.strike)}</td>
                <td className="px-3 py-1.5 text-right text-slate-300">{fmt(r.bid)}</td>
                <td className="px-3 py-1.5 text-right text-slate-300">{fmt(r.ask)}</td>
                <td className="px-3 py-1.5 text-right text-slate-200">{fmt(r.mid)}</td>
                <td className="px-3 py-1.5 text-right text-yellow-400">{fmtPct(r.implied_volatility)}</td>
                <td className={`px-3 py-1.5 text-right ${deltaColor(r.delta)}`}>
                  {fmt(r.delta, 3)}
                </td>
                <td className="px-3 py-1.5 text-right text-slate-400">{fmt(r.gamma, 4)}</td>
                <td className="px-3 py-1.5 text-right text-orange-400">{fmt(r.theta, 4)}</td>
                <td className="px-3 py-1.5 text-right text-slate-400">{fmt(r.vega, 4)}</td>
                <td className="px-3 py-1.5 text-right text-slate-300">
                  {r.open_interest != null ? r.open_interest.toLocaleString() : "—"}
                </td>
                <td className="px-3 py-1.5 text-right text-slate-400">
                  {r.volume != null ? r.volume.toLocaleString() : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {totalPages > 1 && (
        <div className="flex items-center gap-2 px-5 py-3 border-t border-slate-700">
          <button
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
            className="px-3 py-1 text-xs rounded bg-slate-700 text-slate-300 disabled:opacity-40 hover:bg-slate-600"
          >
            ← Prev
          </button>
          <span className="text-xs text-slate-400">
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, filtered.length)} of{" "}
            {filtered.length}
          </span>
          <button
            disabled={page >= totalPages - 1}
            onClick={() => setPage((p) => p + 1)}
            className="px-3 py-1 text-xs rounded bg-slate-700 text-slate-300 disabled:opacity-40 hover:bg-slate-600"
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function OptionsPage() {
  const [ticker,     setTicker]     = useState("SPY");
  const [expFilter,  setExpFilter]  = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [nearATM,    setNearATM]    = useState(false);

  const [surface,  setSurface]  = useState<IVSurfaceResponse | null>(null);
  const [chain,    setChain]    = useState<OptionsChainResponse | null>(null);
  const [health,   setHealth]   = useState<IVSurfaceHealthResponse | null>(null);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState<string | null>(null);

  const handleFetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSurface(null);
    setChain(null);
    setHealth(null);

    try {
      const [surf, ch, hlth] = await Promise.all([
        getIVSurface(ticker),
        getOptionsChain(ticker, { near_atm: nearATM }),
        getIVSurfaceHealth(ticker),
      ]);
      setSurface(surf);
      setChain(ch);
      setHealth(hlth);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [ticker, nearATM]);

  const expirations = chain
    ? Array.from(new Set(chain.rows.map((r) => r.expiration))).sort()
    : [];

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 p-6">
      {/* Header */}
      <div className="max-w-7xl mx-auto">
        <div className="flex items-center gap-3 mb-6">
          <Link
            href="/"
            className="text-slate-400 hover:text-slate-200 text-sm transition-colors"
          >
            ← Dashboard
          </Link>
          <span className="text-slate-600">/</span>
          <Link
            href="/data/prices"
            className="text-slate-400 hover:text-slate-200 text-sm transition-colors"
          >
            Prices
          </Link>
          <span className="text-slate-600">/</span>
          <Link
            href="/data/macro"
            className="text-slate-400 hover:text-slate-200 text-sm transition-colors"
          >
            Macro
          </Link>
          <span className="text-slate-600">/</span>
          <span className="text-slate-200 text-sm font-medium">Options</span>
        </div>

        <div className="mb-8">
          <h1 className="text-2xl font-bold text-white mb-1">Options Chain</h1>
          <p className="text-slate-400 text-sm">
            Current options chain with IV term structure and ATM Greeks.
          </p>
        </div>

        {/* Controls */}
        <div className="flex flex-wrap items-end gap-4 mb-8 p-4 bg-slate-800 rounded-lg border border-slate-700">
          <div>
            <label className="block text-xs text-slate-400 mb-1">Ticker</label>
            <select
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
            >
              {TICKERS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">Filter</label>
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
            >
              <option value="">All types</option>
              <option value="call">Calls only</option>
              <option value="put">Puts only</option>
            </select>
          </div>

          <div>
            <label className="block text-xs text-slate-400 mb-1">Expiration</label>
            <select
              value={expFilter}
              onChange={(e) => setExpFilter(e.target.value)}
              className="bg-slate-700 border border-slate-600 rounded px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-indigo-500"
            >
              <option value="">All expirations</option>
              {expirations.map((exp) => (
                <option key={exp} value={exp}>
                  {exp}
                </option>
              ))}
            </select>
          </div>

          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={nearATM}
              onChange={(e) => setNearATM(e.target.checked)}
              className="w-4 h-4 accent-indigo-500"
            />
            <span className="text-sm text-slate-300">Near ATM (±10%)</span>
          </label>

          <button
            onClick={handleFetch}
            disabled={loading}
            className="px-5 py-1.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 rounded text-sm font-medium transition-colors"
          >
            {loading ? "Loading…" : "Fetch Chain"}
          </button>

          {chain && (
            <span className="text-xs text-slate-400 self-center">
              Provider:{" "}
              <span className="text-slate-200 font-medium">{chain.provider}</span>
              {" · "}
              {chain.n_contracts.toLocaleString()} contracts
              {chain.underlying_price != null && (
                <>
                  {" · "}Spot:{" "}
                  <span className="text-slate-200">${chain.underlying_price.toFixed(2)}</span>
                </>
              )}
            </span>
          )}
        </div>

        {/* Error */}
        {error && (
          <div className="mb-6 p-4 bg-red-900/30 border border-red-700 rounded-lg text-red-300 text-sm">
            <strong>Error:</strong> {error}
          </div>
        )}

        {/* IV Surface cards */}
        {surface && (
          <div className="grid grid-cols-5 gap-3 mb-6">
            {(["iv7", "iv14", "iv30", "iv60", "iv90"] as const).map((k) => (
              <SurfaceCard
                key={k}
                label={k.replace("iv", "") + "D IV"}
                iv={surface.iv_surface[k]}
              />
            ))}
          </div>
        )}

        {/* Charts + ATM table */}
        {surface && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            <TermStructureChart curve={surface.curve} />
            <ATMTable contracts={surface.atm_contracts} />
          </div>
        )}

        {/* Surface health diagnostics */}
        {health && <IVSurfaceHealthSection health={health} />}

        {/* Chain table */}
        {chain && chain.rows.length > 0 && (
          <ChainTable
            rows={chain.rows}
            expFilter={expFilter}
            typeFilter={typeFilter}
          />
        )}

        {/* Empty state */}
        {!surface && !chain && !loading && !error && (
          <div className="flex flex-col items-center justify-center py-20 text-slate-500">
            <div className="text-5xl mb-4">📊</div>
            <p className="text-lg font-medium mb-1">Select a ticker and click Fetch Chain</p>
            <p className="text-sm">
              Loads the current options chain with IV term structure.
            </p>
          </div>
        )}

        {loading && (
          <div className="flex items-center justify-center py-20 text-slate-400">
            <div className="animate-spin w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full mr-3" />
            Fetching options chain…
          </div>
        )}
      </div>
    </div>
  );
}
