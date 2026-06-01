"use client";

/**
 * /data/iv-quality — IV Backfill Progress Dashboard
 *
 * Shows per-ticker progress toward the GOOD (≥80% real IV) threshold:
 *   • Aggregate quality status pill
 *   • Per-ticker progress bars with real / synthetic breakdown
 *   • Rows needed to reach GOOD status
 *   • Missing real-data date ranges
 *   • One-click copy backfill commands per ticker + "all tickers" command
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  getIVQualityProgress,
  type IVQualityProgressResponse,
  type IVTickerProgress,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DAY_OPTIONS = [
  { label: "90 d",  value: 90  },
  { label: "180 d", value: 180 },
  { label: "1 yr",  value: 252 },
  { label: "2 yr",  value: 504 },
  { label: "3 yr",  value: 756 },
];

const STATUS_STYLES = {
  GOOD:  { pill: "bg-green-500/15 text-green-400 border-green-500/30", icon: "✓", bar: "bg-green-400" },
  MIXED: { pill: "bg-amber-500/15 text-amber-400 border-amber-500/30", icon: "⚠", bar: "bg-amber-400" },
  POOR:  { pill: "bg-red-500/15  text-red-400  border-red-500/30",     icon: "✕", bar: "bg-red-400"   },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pct(v: number): string { return `${(v * 100).toFixed(1)}%`; }

function useClipboard(timeout = 1500) {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = useCallback((text: string, key: string) => {
    navigator.clipboard.writeText(text).catch(() => {});
    setCopied(key);
    setTimeout(() => setCopied(null), timeout);
  }, [timeout]);
  return { copied, copy };
}

function StatusPill({ status }: { status: "GOOD" | "MIXED" | "POOR" }) {
  const s = STATUS_STYLES[status];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${s.pill}`}>
      <span className="text-[10px]">{s.icon}</span>
      {status}
    </span>
  );
}

function CopyButton({
  text, id, copied, onCopy,
}: { text: string; id: string; copied: string | null; onCopy: (t: string, k: string) => void }) {
  const isCopied = copied === id;
  return (
    <button
      onClick={() => onCopy(text, id)}
      title="Copy to clipboard"
      className={[
        "shrink-0 rounded px-2 py-1 text-[10px] font-medium transition-colors",
        isCopied
          ? "bg-green-500/20 text-green-400"
          : "bg-slate-700/50 text-slate-400 hover:bg-slate-700 hover:text-slate-200",
      ].join(" ")}
    >
      {isCopied ? "✓ Copied" : "Copy"}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Progress bar (shows real% toward 80% goal)
// ---------------------------------------------------------------------------

function ProgressBar({ ticker }: { ticker: IVTickerProgress }) {
  const realPct  = Math.min(ticker.real_pct * 100, 100);
  const synthPct = Math.min(
    (ticker.synthetic_rows / ticker.total_required_rows) * 100,
    100 - realPct,
  );
  const s = STATUS_STYLES[ticker.status];

  return (
    <div className="w-full">
      {/* Track */}
      <div className="relative h-3 rounded-full bg-slate-800 overflow-hidden">
        {/* Synthetic fill (full width, muted) */}
        <div
          className="absolute inset-y-0 left-0 bg-slate-700/60"
          style={{ width: `${Math.min((ticker.synthetic_rows + ticker.real_rows) / ticker.total_required_rows * 100, 100)}%` }}
        />
        {/* Real fill on top */}
        <div
          className={`absolute inset-y-0 left-0 transition-all duration-700 ${s.bar}`}
          style={{ width: `${realPct}%` }}
        />
        {/* 80% threshold marker */}
        <div
          className="absolute inset-y-0 w-0.5 bg-white/30"
          style={{ left: "80%" }}
          title="80% GOOD threshold"
        />
      </div>
      {/* Labels */}
      <div className="flex justify-between text-[10px] text-slate-500 mt-1">
        <span>{pct(ticker.real_pct)} real</span>
        <span className="text-slate-600">80% threshold</span>
        <span>100%</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-ticker card
// ---------------------------------------------------------------------------

function TickerCard({
  ticker, dataDays, copied, onCopy,
}: {
  ticker:   IVTickerProgress;
  dataDays: number;
  copied:   string | null;
  onCopy:   (t: string, k: string) => void;
}) {
  const [showRanges, setShowRanges] = useState(false);
  const s = STATUS_STYLES[ticker.status];
  const missingCount = ticker.missing_real_ranges.reduce((a, r) => a + r.count, 0);

  return (
    <div className="rounded-xl border border-slate-700/50 bg-slate-900/70 p-4">
      {/* Header row */}
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-3">
          <span className="text-base font-bold text-slate-100">{ticker.ticker}</span>
          <StatusPill status={ticker.status} />
        </div>
        <div className="text-right text-[11px] text-slate-500">
          {ticker.real_rows > 0 ? (
            <>
              <div>Latest real: <span className="text-slate-300">{ticker.latest_real_iv_date}</span></div>
              <div>Earliest real: <span className="text-slate-300">{ticker.earliest_real_iv_date}</span></div>
            </>
          ) : (
            <span className="text-slate-600 italic">no real data</span>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <ProgressBar ticker={ticker} />

      {/* Stats row */}
      <div className="grid grid-cols-4 gap-2 mt-3 mb-3">
        {[
          { label: "Real",        val: ticker.real_rows,        color: "text-green-400" },
          { label: "Interpolated", val: ticker.interpolated_rows, color: "text-yellow-400" },
          { label: "Synthetic",   val: ticker.synthetic_rows,   color: "text-slate-400" },
          { label: "Total",       val: ticker.total_required_rows, color: "text-slate-200" },
        ].map(({ label, val, color }) => (
          <div key={label} className="rounded bg-slate-800/50 p-2 text-center">
            <p className={`text-sm font-semibold ${color}`}>{val.toLocaleString()}</p>
            <p className="text-[10px] text-slate-600">{label}</p>
          </div>
        ))}
      </div>

      {/* Rows-to-GOOD indicator */}
      {ticker.status !== "GOOD" && (
        <div className="mb-3 rounded-lg border border-slate-700/40 bg-slate-800/30 px-3 py-2 flex items-center justify-between">
          <span className="text-xs text-slate-400">
            Rows needed for <span className="text-green-400 font-semibold">GOOD</span>
          </span>
          <span className="text-sm font-bold text-amber-400">
            {ticker.rows_needed_for_good.toLocaleString()} rows
          </span>
        </div>
      )}

      {/* Missing ranges toggle */}
      {missingCount > 0 && (
        <div className="mb-3">
          <button
            onClick={() => setShowRanges((v) => !v)}
            className="text-[11px] text-slate-500 hover:text-slate-300 transition-colors"
          >
            {showRanges ? "▾" : "▸"} {ticker.missing_real_ranges.length} missing range{ticker.missing_real_ranges.length !== 1 ? "s" : ""} ({missingCount.toLocaleString()} trading days without real data)
          </button>
          {showRanges && (
            <div className="mt-2 max-h-40 overflow-y-auto rounded bg-slate-800/50 p-2 space-y-0.5">
              {ticker.missing_real_ranges.map((r, i) => (
                <div key={i} className="flex justify-between text-[10px]">
                  <span className="text-slate-400 font-mono">{r.start} → {r.end}</span>
                  <span className="text-slate-600">{r.count} days</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Backfill command */}
      <div className="flex items-center gap-2 rounded-lg bg-slate-950/70 border border-slate-800 px-3 py-2">
        <code className="flex-1 text-[11px] text-slate-300 font-mono truncate">
          {ticker.backfill_cmd}
        </code>
        <CopyButton
          text={ticker.backfill_cmd}
          id={`cmd-${ticker.ticker}`}
          copied={copied}
          onCopy={onCopy}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Aggregate summary panel
// ---------------------------------------------------------------------------

function SummaryPanel({
  data, copied, onCopy,
}: {
  data:    IVQualityProgressResponse;
  copied:  string | null;
  onCopy:  (t: string, k: string) => void;
}) {
  const { summary, window: win } = data;
  const s = STATUS_STYLES[summary.status];

  return (
    <div className={`rounded-xl border p-5 mb-6 ${s.pill}`}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <h2 className="text-lg font-bold text-slate-100">Aggregate Status</h2>
            <StatusPill status={summary.status} />
          </div>
          <p className="text-xs text-slate-400">
            {win.data_days} trading-day window · {win.start} → {win.end} ·
            threshold: {win.good_threshold_rows.toLocaleString()} real rows per ticker
          </p>
        </div>

        {/* Aggregate counts */}
        <div className="flex gap-4 text-center">
          {[
            { label: "Real",     val: summary.real_rows,        color: "text-green-400" },
            { label: "Synthetic", val: summary.synthetic_rows,  color: "text-slate-400" },
            { label: "Total",    val: summary.total_required_rows, color: "text-slate-200" },
          ].map(({ label, val, color }) => (
            <div key={label}>
              <p className={`text-xl font-bold ${color}`}>{val.toLocaleString()}</p>
              <p className="text-[10px] text-slate-500">{label}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Real % overall bar */}
      <div className="mt-4">
        <div className="relative h-2 rounded-full bg-slate-800/60 overflow-hidden">
          <div
            className={`absolute inset-y-0 left-0 ${s.bar} transition-all duration-700`}
            style={{ width: `${Math.min(summary.real_pct * 100, 100)}%` }}
          />
          <div className="absolute inset-y-0 w-0.5 bg-white/20" style={{ left: "80%" }} />
        </div>
        <div className="flex justify-between text-[10px] text-slate-600 mt-1">
          <span>{pct(summary.real_pct)} real across all tickers</span>
          <span>80% target</span>
        </div>
      </div>

      {summary.status !== "GOOD" && (
        <p className="mt-3 text-xs text-amber-400">
          ⚠ {summary.rows_needed_for_good.toLocaleString()} more real rows needed across all tickers to reach GOOD status.
          Backtests should not be used for production decisions until quality is GOOD.
        </p>
      )}

      {/* All-tickers command */}
      <div className="mt-4 flex items-center gap-2 rounded-lg bg-slate-950/70 border border-slate-700/50 px-3 py-2">
        <code className="flex-1 text-[11px] text-slate-300 font-mono truncate">
          {summary.backfill_all_cmd}
        </code>
        <CopyButton
          text={summary.backfill_all_cmd}
          id="cmd-all"
          copied={copied}
          onCopy={onCopy}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function IVQualityPage() {
  const [dataDays, setDataDays] = useState(400);
  const [data,     setData]     = useState<IVQualityProgressResponse | null>(null);
  const [loading,  setLoading]  = useState(true);
  const [error,    setError]    = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const { copied, copy } = useClipboard();

  const load = useCallback((days: number) => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setLoading(true);
    setError(null);
    getIVQualityProgress(days)
      .then((d) => { setData(d); setLoading(false); })
      .catch((e) => { if (e?.name !== "AbortError") { setError(String(e?.message ?? e)); setLoading(false); } });
  }, []);

  useEffect(() => { load(dataDays); }, [dataDays, load]);

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 p-4 md:p-8">
      <div className="max-w-4xl mx-auto">

        {/* Header */}
        <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
          <div>
            <div className="flex items-center gap-2 mb-1 flex-wrap text-[10px] text-slate-500">
              <Link href="/" className="hover:text-slate-300 transition-colors">← Dashboard</Link>
              <span className="text-slate-700">/</span>
              <Link href="/data/historical-iv" className="hover:text-slate-300 transition-colors">Historical IV</Link>
              <span className="text-slate-700">/</span>
              <span className="text-slate-400">IV Quality Progress</span>
            </div>
            <h1 className="text-2xl font-bold text-slate-100">IV Backfill Progress</h1>
            <p className="text-sm text-slate-400 mt-1">
              Per-ticker progress toward ≥80% real options-chain IV (GOOD status)
            </p>
          </div>

          {/* Window selector */}
          <div className="flex rounded-lg overflow-hidden border border-slate-700 text-xs shrink-0">
            {DAY_OPTIONS.map((o) => (
              <button
                key={o.value}
                onClick={() => setDataDays(o.value)}
                className={[
                  "px-3 py-1.5 transition-colors",
                  dataDays === o.value
                    ? "bg-slate-600 text-white font-semibold"
                    : "bg-slate-800 text-slate-400 hover:bg-slate-700",
                ].join(" ")}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>

        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center h-48 text-slate-400 gap-2">
            <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
            </svg>
            Loading quality progress…
          </div>
        )}

        {/* Error */}
        {error && !loading && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 mb-6 text-red-400 text-sm">
            ⚠ {error}
          </div>
        )}

        {/* Content */}
        {data && !loading && (
          <>
            {/* Aggregate panel */}
            <SummaryPanel data={data} copied={copied} onCopy={copy} />

            {/* How to use */}
            <div className="rounded-xl border border-slate-700/40 bg-slate-900/50 p-4 mb-6 text-xs text-slate-400">
              <h3 className="text-slate-300 font-semibold mb-2">How to reach GOOD status</h3>
              <ol className="list-decimal list-inside space-y-1">
                <li>Set <code className="bg-slate-800 px-1 rounded text-[10px]">DATA_PROVIDER=real</code> and configure <code className="bg-slate-800 px-1 rounded text-[10px]">POLYGON_API_KEY</code></li>
                <li>Run the per-ticker command below each progress bar (or the all-tickers command above) once per day</li>
                <li>Status advances <span className="text-red-400">POOR</span> → <span className="text-amber-400">MIXED</span> → <span className="text-green-400">GOOD</span> as real rows accumulate</li>
                <li>At GOOD, the Score Optimisation export button unlocks</li>
              </ol>
            </div>

            {/* Per-ticker cards */}
            <div className="grid grid-cols-1 gap-4">
              {data.tickers.map((t) => (
                <TickerCard
                  key={t.ticker}
                  ticker={t}
                  dataDays={dataDays}
                  copied={copied}
                  onCopy={copy}
                />
              ))}
            </div>

            {/* Footer */}
            <p className="mt-6 text-[10px] text-slate-600 text-center">
              Window: {data.window.start} → {data.window.end} ·
              {data.window.total_trading_days} trading days ·
              GOOD threshold: {data.window.good_threshold_rows} real rows per ticker
            </p>
          </>
        )}
      </div>
    </main>
  );
}
