"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import { clsx } from "clsx";
import {
  getScannerHistory,
  getTickerHistory,
  calculateOutcomes,
  updateJournalSignal,
  type ScannerHistory,
  type TickerHistory,
  type DailySummary,
  type TickerLeaderboardEntry,
  type JournalSignal,
  type JournalReviewUpdate,
  type OutcomeCalculateResult,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(v: number | null | undefined, decimals = 1, suffix = "") {
  if (v == null || !isFinite(v)) return "—";
  return v.toFixed(decimals) + suffix;
}

function signalColor(sig: string) {
  if (sig === "SELL_AGGRESSIVE") return "text-red-400";
  if (sig === "SELL_SELECTIVE")  return "text-orange-400";
  return "text-zinc-400";
}

function scoreColor(s: number) {
  if (s >= 70) return "text-red-400";
  if (s >= 55) return "text-orange-400";
  if (s >= 40) return "text-amber-400";
  return "text-zinc-300";
}

function ratioColor(r: number) {
  if (r >= 1.8) return "text-red-400";
  if (r >= 1.5) return "text-orange-400";
  if (r >= 1.3) return "text-amber-300";
  return "text-zinc-300";
}

// ---------------------------------------------------------------------------
// Review status
// ---------------------------------------------------------------------------

type ReviewStatus = "new" | "reviewed" | "verified" | "traded";

function getReviewStatus(s: JournalSignal): ReviewStatus {
  if (s.trade_taken)     return "traded";
  if (s.broker_verified) return "verified";
  if (s.reviewed)        return "reviewed";
  return "new";
}

const REVIEW_STATUS_META: Record<ReviewStatus, { label: string; badge: string }> = {
  new:      { label: "New",      badge: "bg-zinc-800 text-zinc-500 border border-zinc-700" },
  reviewed: { label: "Reviewed", badge: "bg-blue-900/40 text-blue-300 border border-blue-700/50" },
  verified: { label: "Verified", badge: "bg-emerald-900/40 text-emerald-300 border border-emerald-700/50" },
  traded:   { label: "Traded",   badge: "bg-amber-900/40 text-amber-300 border border-amber-600/50" },
};

function ReviewStatusBadge({ signal }: { signal: JournalSignal }) {
  const status = getReviewStatus(signal);
  const { label, badge } = REVIEW_STATUS_META[status];
  return (
    <span className={clsx("px-1.5 py-0.5 rounded text-[10px] font-semibold whitespace-nowrap", badge)}>
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Outcome chip
// ---------------------------------------------------------------------------

function outcomeChip(s: JournalSignal) {
  const status = s.outcome_status ?? "pending";
  if (status === "pending") return <span className="text-zinc-700 text-[10px]">pending</span>;
  const cap = s.premium_capture_20d ?? s.premium_capture_10d ?? s.premium_capture_5d;
  if (cap == null) return <span className="text-zinc-500 text-[10px]">{status}</span>;
  if (cap > 0)
    return <span className="text-emerald-400 text-[10px] font-semibold" title={`+${cap.toFixed(1)} vpts`}>✓ +{cap.toFixed(1)}v</span>;
  return <span className="text-red-400 text-[10px] font-semibold" title={`${cap.toFixed(1)} vpts`}>✗ {cap.toFixed(1)}v</span>;
}

// ---------------------------------------------------------------------------
// FwdRetCell
// ---------------------------------------------------------------------------

function FwdRetCell({ ret, rv, cap }: { ret: number | null; rv: number | null; cap: number | null }) {
  if (ret == null) return <span className="text-zinc-700">—</span>;
  const retColor = ret > 2 ? "text-red-400" : ret < -2 ? "text-emerald-400" : "text-zinc-300";
  return (
    <div className="flex flex-col leading-tight">
      <span className={clsx("font-semibold tabular-nums", retColor)}>
        {ret >= 0 ? "+" : ""}{ret.toFixed(1)}%
      </span>
      {rv  != null && <span className="text-zinc-600 text-[9px] tabular-nums">rv {rv.toFixed(1)}%</span>}
      {cap != null && (
        <span className={clsx("text-[9px] tabular-nums", cap >= 0 ? "text-emerald-600" : "text-red-600")}>
          {cap >= 0 ? "+" : ""}{cap.toFixed(1)}v
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Inline signal review form
// ---------------------------------------------------------------------------

type SaveState = "idle" | "saving" | "saved" | "error";

function SignalReviewForm({
  signal,
  onUpdate,
}: {
  signal:   JournalSignal;
  onUpdate: (fields: JournalReviewUpdate) => Promise<void>;
}) {
  const [saveState, setSaveState]   = useState<SaveState>("idle");
  const [saveError, setSaveError]   = useState<string | null>(null);
  const [localNotes, setLocalNotes] = useState(signal.notes ?? "");
  const [localBid,   setLocalBid]   = useState(signal.actual_bid   != null ? String(signal.actual_bid)   : "");
  const [localAsk,   setLocalAsk]   = useState(signal.actual_ask   != null ? String(signal.actual_ask)   : "");
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const save = useCallback(async (fields: JournalReviewUpdate) => {
    setSaveState("saving");
    setSaveError(null);
    try {
      await onUpdate(fields);
      setSaveState("saved");
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => setSaveState("idle"), 2000);
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : String(e));
      setSaveState("error");
    }
  }, [onUpdate]);

  const toggle = (field: keyof JournalReviewUpdate, val: boolean) => save({ [field]: val });

  const commitBidAsk = () => {
    const bid = parseFloat(localBid);
    const ask = parseFloat(localAsk);
    if (!isNaN(bid) && !isNaN(ask)) save({ actual_bid: bid, actual_ask: ask });
    else if (!isNaN(bid))           save({ actual_bid: bid });
    else if (!isNaN(ask))           save({ actual_ask: ask });
  };

  const commitNotes = () => {
    if (localNotes !== (signal.notes ?? "")) save({ notes: localNotes });
  };

  return (
    <div className="bg-zinc-900/60 border-t border-zinc-800/60 px-5 py-4">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-xs">

        {/* Review status */}
        <div>
          <p className="text-zinc-500 uppercase tracking-wide text-[10px] font-semibold mb-2">Review Status</p>
          <div className="space-y-2">
            {([
              ["reviewed",        "Reviewed",         signal.reviewed]        as const,
              ["broker_verified", "Broker Verified",  signal.broker_verified] as const,
              ["trade_considered","Trade Considered",  signal.trade_considered] as const,
              ["trade_taken",     "Trade Taken",       signal.trade_taken]     as const,
            ] satisfies [keyof JournalReviewUpdate, string, boolean][]).map(([field, label, val]) => (
              <label key={field} className="flex items-center gap-2 cursor-pointer group">
                <input
                  type="checkbox"
                  checked={val}
                  onChange={(e) => toggle(field, e.target.checked)}
                  className="w-3.5 h-3.5 accent-blue-500"
                />
                <span className={clsx("transition-colors", val ? "text-zinc-200" : "text-zinc-500 group-hover:text-zinc-400")}>
                  {label}
                </span>
                {field === "trade_taken" && val && (
                  <span className="ml-1 text-amber-400 text-[10px] font-semibold">● Live</span>
                )}
              </label>
            ))}
          </div>
        </div>

        {/* Broker quote */}
        <div>
          <p className="text-zinc-500 uppercase tracking-wide text-[10px] font-semibold mb-2">Broker Quote</p>
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <label className="text-zinc-600 w-8 shrink-0">Bid</label>
              <input
                type="number"
                step="0.01"
                value={localBid}
                onChange={(e) => setLocalBid(e.target.value)}
                onBlur={commitBidAsk}
                placeholder="0.00"
                className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 w-20 text-zinc-200 text-xs focus:outline-none focus:border-blue-500 tabular-nums"
              />
            </div>
            <div className="flex items-center gap-2">
              <label className="text-zinc-600 w-8 shrink-0">Ask</label>
              <input
                type="number"
                step="0.01"
                value={localAsk}
                onChange={(e) => setLocalAsk(e.target.value)}
                onBlur={commitBidAsk}
                placeholder="0.00"
                className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 w-20 text-zinc-200 text-xs focus:outline-none focus:border-blue-500 tabular-nums"
              />
            </div>
            {signal.actual_mid != null && (
              <p className="text-zinc-600">
                Mid <span className="text-zinc-300 tabular-nums">{signal.actual_mid.toFixed(2)}</span>
                {signal.actual_spread_pct != null && (
                  <span className="ml-2">
                    Spread <span className="text-zinc-400 tabular-nums">{(signal.actual_spread_pct * 100).toFixed(1)}%</span>
                  </span>
                )}
              </p>
            )}
          </div>
        </div>

        {/* Notes */}
        <div>
          <p className="text-zinc-500 uppercase tracking-wide text-[10px] font-semibold mb-2">Notes</p>
          <textarea
            value={localNotes}
            onChange={(e) => setLocalNotes(e.target.value)}
            onBlur={commitNotes}
            rows={4}
            placeholder="Spread was wide… earnings risk too close… skipping."
            className="w-full bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-zinc-300 text-xs focus:outline-none focus:border-blue-500 resize-none leading-relaxed"
          />
        </div>
      </div>

      {/* Save status */}
      <div className="mt-2 h-4">
        {saveState === "saving" && <span className="text-zinc-500 text-[10px]">Saving…</span>}
        {saveState === "saved"  && <span className="text-emerald-500 text-[10px]">✓ Saved</span>}
        {saveState === "error"  && <span className="text-red-400 text-[10px]">✗ {saveError}</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stat pill
// ---------------------------------------------------------------------------

function StatPill({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="flex flex-col gap-0.5 px-4 py-3 bg-zinc-900 border border-zinc-800 rounded-lg min-w-[90px]">
      <span className="text-[10px] uppercase tracking-wide text-zinc-500 font-medium">{label}</span>
      <span className="text-xl font-bold text-zinc-100 tabular-nums">{value}</span>
      {sub && <span className="text-[10px] text-zinc-500">{sub}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Daily summary table
// ---------------------------------------------------------------------------

function DailySummaryTable({ rows }: { rows: DailySummary[] }) {
  if (!rows.length)
    return <p className="text-zinc-600 text-sm py-4 text-center">No scan history yet.</p>;
  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-800">
      <table className="w-full text-sm">
        <thead className="border-b border-zinc-800 bg-zinc-900/60">
          <tr>
            {["DATE","SIGNALS","AVG SCORE","AVG IV/RV","TOP TICKER","ALL TICKERS"].map((h) => (
              <th key={h} className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-zinc-500">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/60">
          {rows.map((r) => (
            <tr key={r.scan_date} className="hover:bg-zinc-800/20 transition-colors">
              <td className="px-4 py-2.5 text-zinc-300 font-mono text-xs">{r.scan_date}</td>
              <td className="px-4 py-2.5 text-zinc-100 font-bold tabular-nums">{r.signal_count}</td>
              <td className={clsx("px-4 py-2.5 tabular-nums font-semibold", scoreColor(r.avg_score))}>{fmt(r.avg_score)}</td>
              <td className={clsx("px-4 py-2.5 tabular-nums", ratioColor(r.avg_iv_rv_ratio))}>{fmt(r.avg_iv_rv_ratio, 3)}×</td>
              <td className="px-4 py-2.5 font-bold text-zinc-100">{r.top_ticker}</td>
              <td className="px-4 py-2.5 text-zinc-500 text-xs">{r.tickers}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Ticker leaderboard
// ---------------------------------------------------------------------------

function LeaderboardTable({
  rows, onSelect, selected,
}: {
  rows:     TickerLeaderboardEntry[];
  onSelect: (t: string) => void;
  selected: string | null;
}) {
  if (!rows.length)
    return <p className="text-zinc-600 text-sm py-4 text-center">No tickers yet.</p>;
  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-800">
      <table className="w-full text-sm">
        <thead className="border-b border-zinc-800 bg-zinc-900/60">
          <tr>
            {["TICKER","SEEN","AVG SCORE","BEST SCORE","AVG IV/RV","AVG IV30","AVG RV30","FIRST","LAST"].map((h) => (
              <th key={h} className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-zinc-500">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/60">
          {rows.map((r) => (
            <tr
              key={r.ticker}
              className={clsx(
                "cursor-pointer transition-colors",
                selected === r.ticker ? "bg-blue-900/20 border-l-2 border-blue-500" : "hover:bg-zinc-800/20",
              )}
              onClick={() => onSelect(r.ticker === selected ? "" : r.ticker)}
            >
              <td className="px-4 py-2.5 font-bold text-zinc-100">{r.ticker}</td>
              <td className="px-4 py-2.5 text-zinc-100 tabular-nums font-semibold">{r.appearances}</td>
              <td className={clsx("px-4 py-2.5 tabular-nums font-semibold", scoreColor(r.avg_score))}>{fmt(r.avg_score)}</td>
              <td className={clsx("px-4 py-2.5 tabular-nums", scoreColor(r.best_score))}>{fmt(r.best_score)}</td>
              <td className={clsx("px-4 py-2.5 tabular-nums", ratioColor(r.avg_iv_rv_ratio))}>{fmt(r.avg_iv_rv_ratio, 3)}×</td>
              <td className="px-4 py-2.5 tabular-nums text-zinc-300">{fmt(r.avg_iv30)}%</td>
              <td className="px-4 py-2.5 tabular-nums text-zinc-400">{fmt(r.avg_rv30)}%</td>
              <td className="px-4 py-2.5 text-zinc-500 text-xs font-mono">{r.first_seen}</td>
              <td className="px-4 py-2.5 text-zinc-500 text-xs font-mono">{r.last_seen}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Ticker drill-down  (with inline review editing + filters)
// ---------------------------------------------------------------------------

type ReviewFilter = "all" | "reviewed" | "verified" | "traded" | "unreviewed";

const FILTER_LABELS: Record<ReviewFilter, string> = {
  all:        "All",
  unreviewed: "New",
  reviewed:   "Reviewed",
  verified:   "Verified",
  traded:     "Traded",
};

function TickerDrillDown({ ticker }: { ticker: string }) {
  const [data,     setData]     = useState<TickerHistory | null>(null);
  const [signals,  setSignals]  = useState<JournalSignal[]>([]);
  const [loading,  setLoading]  = useState(true);
  const [error,    setError]    = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [filter,   setFilter]   = useState<ReviewFilter>("all");

  useEffect(() => {
    setLoading(true);
    setError(null);
    setExpanded(new Set());
    getTickerHistory(ticker)
      .then((d) => { setData(d); setSignals(d.signals); })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [ticker]);

  const handleUpdate = useCallback(
    async (scanDate: string, fields: JournalReviewUpdate) => {
      await updateJournalSignal(scanDate, ticker, fields);
      // Optimistic update of local signal state
      setSignals((prev) =>
        prev.map((s) => s.scan_date === scanDate ? { ...s, ...fields } : s)
      );
    },
    [ticker],
  );

  const toggleExpanded = (scanDate: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(scanDate) ? next.delete(scanDate) : next.add(scanDate);
      return next;
    });

  if (loading)
    return (
      <div className="mt-4 p-4 bg-zinc-900 rounded-lg border border-zinc-800 text-zinc-500 text-sm animate-pulse">
        Loading {ticker} history…
      </div>
    );
  if (error || !data)
    return (
      <div className="mt-4 p-4 bg-red-900/20 rounded-lg border border-red-800 text-red-400 text-sm">
        {error ?? "Unknown error"}
      </div>
    );

  // Counts for filter chip labels
  const statusCounts = {
    all:        signals.length,
    unreviewed: signals.filter((s) => !s.reviewed && !s.broker_verified && !s.trade_taken).length,
    reviewed:   signals.filter((s) => s.reviewed).length,
    verified:   signals.filter((s) => s.broker_verified).length,
    traded:     signals.filter((s) => s.trade_taken).length,
  };

  const filtered =
    filter === "all"        ? signals :
    filter === "unreviewed" ? signals.filter((s) => !s.reviewed && !s.broker_verified && !s.trade_taken) :
    filter === "reviewed"   ? signals.filter((s) => s.reviewed)        :
    filter === "verified"   ? signals.filter((s) => s.broker_verified) :
                              signals.filter((s) => s.trade_taken);

  return (
    <div className="mt-4 rounded-lg border border-blue-800/40 bg-zinc-900/60 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-6 px-5 py-3 bg-blue-900/10 border-b border-blue-800/30 flex-wrap gap-y-2">
        <span className="font-bold text-lg text-zinc-100">{data.ticker}</span>
        <span className="text-zinc-500 text-sm">
          {data.appearances} appearance{data.appearances !== 1 ? "s" : ""} ·{" "}
          {data.first_seen} → {data.last_seen}
          {data.outcomes_complete > 0 && (
            <span className="ml-2 text-zinc-600">
              · {data.outcomes_complete} outcome{data.outcomes_complete !== 1 ? "s" : ""} complete
            </span>
          )}
        </span>
        <div className="ml-auto flex flex-wrap gap-4 text-sm">
          <span className="text-zinc-500">
            Avg score{" "}
            <span className={clsx("font-bold", scoreColor(data.avg_score))}>{fmt(data.avg_score)}</span>
          </span>
          <span className="text-zinc-500">
            Avg IV/RV{" "}
            <span className={clsx("font-semibold", ratioColor(data.avg_iv_rv_ratio))}>
              {fmt(data.avg_iv_rv_ratio, 3)}×
            </span>
          </span>
          {data.avg_premium_capture_20d != null && (
            <span className="text-zinc-500">
              Avg cap 20d{" "}
              <span className={clsx("font-semibold", data.avg_premium_capture_20d >= 0 ? "text-emerald-400" : "text-red-400")}>
                {data.avg_premium_capture_20d >= 0 ? "+" : ""}{data.avg_premium_capture_20d.toFixed(1)}v
              </span>
            </span>
          )}
        </div>
      </div>

      {/* Review filter chips */}
      <div className="flex items-center gap-2 px-5 py-2.5 border-b border-zinc-800/50 bg-zinc-900/30 flex-wrap">
        <span className="text-[10px] uppercase tracking-wide text-zinc-600 mr-1">Filter</span>
        {(Object.keys(FILTER_LABELS) as ReviewFilter[]).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={clsx(
              "flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium transition-all border",
              filter === f
                ? "bg-blue-600/30 border-blue-500/50 text-blue-300"
                : "bg-zinc-800/40 border-zinc-700/50 text-zinc-500 hover:text-zinc-300",
            )}
          >
            {FILTER_LABELS[f]}
            <span className={clsx(
              "text-[10px] tabular-nums",
              filter === f ? "text-blue-400" : "text-zinc-700",
            )}>
              {statusCounts[f]}
            </span>
          </button>
        ))}
        {statusCounts.traded > 0 && (
          <span className="ml-auto text-[10px] text-amber-500 font-medium">
            {statusCounts.traded} traded · {statusCounts.verified} verified
          </span>
        )}
      </div>

      {/* Signal table */}
      {filtered.length === 0 ? (
        <p className="px-5 py-8 text-zinc-600 text-sm text-center">No signals match this filter.</p>
      ) : (
        <table className="w-full text-xs">
          <thead className="border-b border-zinc-800 bg-zinc-900/40">
            <tr>
              <th className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wide text-zinc-600 w-6"></th>
              {["STATUS","DATE","PRICE","IV30","RV30","IV/RV","VRP","SIGNAL","SCORE",
                "MACRO−","EARN−","EVENT","FWD 5D","FWD 10D","FWD 20D","OUTCOME"].map((h) => (
                <th key={h} className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wide text-zinc-600">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => {
              const isOpen = expanded.has(s.scan_date);
              return (
                <tbody key={s.scan_date}>
                  <tr
                    className={clsx(
                      "cursor-pointer transition-colors border-b border-zinc-800/40",
                      isOpen ? "bg-zinc-800/30" : "hover:bg-zinc-800/20",
                    )}
                    onClick={() => toggleExpanded(s.scan_date)}
                  >
                    {/* Expand toggle */}
                    <td className="px-3 py-2 text-zinc-600 text-center select-none">
                      {isOpen ? "▾" : "▸"}
                    </td>
                    {/* Status badge */}
                    <td className="px-3 py-2"><ReviewStatusBadge signal={s} /></td>
                    <td className="px-3 py-2 text-zinc-400 font-mono">{s.scan_date}</td>
                    <td className="px-3 py-2 text-zinc-400">${fmt(s.price, 2)}</td>
                    <td className="px-3 py-2 text-zinc-200">{fmt(s.iv30)}%</td>
                    <td className="px-3 py-2 text-zinc-400">{fmt(s.rv30)}%</td>
                    <td className={clsx("px-3 py-2 font-semibold", ratioColor(s.iv_rv_ratio))}>{fmt(s.iv_rv_ratio, 3)}×</td>
                    <td className="px-3 py-2 text-zinc-300">+{fmt(s.volatility_risk_premium)}</td>
                    <td className={clsx("px-3 py-2 font-semibold", signalColor(s.trade_signal))}>
                      {s.trade_signal === "SELL_AGGRESSIVE" ? "Aggr." : "Sel."}
                    </td>
                    <td className={clsx("px-3 py-2 font-bold", scoreColor(s.scanner_score))}>{fmt(s.scanner_score)}</td>
                    <td className="px-3 py-2 text-zinc-500">{s.macro_penalty > 0 ? `−${s.macro_penalty.toFixed(0)}` : "—"}</td>
                    <td className="px-3 py-2 text-zinc-500">{s.earnings_penalty > 0 ? `−${s.earnings_penalty.toFixed(0)}` : "—"}</td>
                    <td className="px-3 py-2">{s.event_risk_flag ? <span className="text-amber-400">⚠</span> : <span className="text-zinc-700">—</span>}</td>
                    <td className="px-3 py-2"><FwdRetCell ret={s.fwd_ret_5d}  rv={s.fwd_rv5d}  cap={s.premium_capture_5d}  /></td>
                    <td className="px-3 py-2"><FwdRetCell ret={s.fwd_ret_10d} rv={s.fwd_rv10d} cap={s.premium_capture_10d} /></td>
                    <td className="px-3 py-2"><FwdRetCell ret={s.fwd_ret_20d} rv={s.fwd_rv20d} cap={s.premium_capture_20d} /></td>
                    <td className="px-3 py-2">{outcomeChip(s)}</td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={17}>
                        <SignalReviewForm
                          signal={s}
                          onUpdate={(fields) => handleUpdate(s.scan_date, fields)}
                        />
                      </td>
                    </tr>
                  )}
                </tbody>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recent signals feed
// ---------------------------------------------------------------------------

function RecentSignals({ signals }: { signals: JournalSignal[] }) {
  if (!signals.length) return null;
  const byDate: Record<string, JournalSignal[]> = {};
  for (const s of signals) (byDate[s.scan_date] ||= []).push(s);

  return (
    <div className="space-y-4">
      {Object.entries(byDate).map(([date, sigs]) => (
        <div key={date}>
          <h3 className="text-xs text-zinc-500 font-mono mb-2 uppercase tracking-wide">{date}</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
            {sigs.map((s, i) => (
              <div
                key={i}
                className={clsx(
                  "flex items-center gap-3 border rounded-lg px-4 py-3 transition-colors",
                  s.trade_taken     ? "bg-amber-950/20 border-amber-800/30" :
                  s.broker_verified ? "bg-emerald-950/20 border-emerald-800/30" :
                  s.reviewed        ? "bg-blue-950/20 border-blue-800/30" :
                                      "bg-zinc-900 border-zinc-800",
                )}
              >
                <div className="flex flex-col min-w-[52px]">
                  <span className="font-bold text-zinc-100">{s.ticker}</span>
                  <span className={clsx("text-[10px] font-semibold", signalColor(s.trade_signal))}>
                    {s.trade_signal === "SELL_AGGRESSIVE" ? "Aggr." : "Sel."}
                  </span>
                </div>
                <div className="flex flex-col flex-1 min-w-0">
                  <div className="flex items-center gap-2 text-xs">
                    <span className={clsx("font-semibold", ratioColor(s.iv_rv_ratio))}>{fmt(s.iv_rv_ratio, 2)}×</span>
                    <span className="text-zinc-600">·</span>
                    <span className="text-zinc-400">IV {fmt(s.iv30)}%</span>
                    <span className="text-zinc-600">·</span>
                    <span className="text-zinc-500">RV {fmt(s.rv30)}%</span>
                  </div>
                  <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                    <ReviewStatusBadge signal={s} />
                    {s.event_risk_flag && <span className="text-[10px] text-amber-400">⚠ event</span>}
                    {s.earnings_penalty > 0 && <span className="text-[10px] text-yellow-500">earn −{s.earnings_penalty.toFixed(0)}</span>}
                  </div>
                </div>
                <div className="flex flex-col items-end shrink-0">
                  <span className={clsx("text-lg font-bold tabular-nums", scoreColor(s.scanner_score))}>
                    {fmt(s.scanner_score, 0)}
                  </span>
                  <span className="text-[10px] text-zinc-600">score</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ScannerHistoryPage() {
  const [history,        setHistory]        = useState<ScannerHistory | null>(null);
  const [loading,        setLoading]        = useState(true);
  const [error,          setError]          = useState<string | null>(null);
  const [selectedTicker, setSelected]       = useState<string | null>(null);
  const [tab,            setTab]            = useState<"daily" | "leaderboard" | "feed">("daily");
  const [calculating,    setCalculating]    = useState(false);
  const [calcResult,     setCalcResult]     = useState<OutcomeCalculateResult | null>(null);
  const [calcError,      setCalcError]      = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getScannerHistory()
      .then(setHistory)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleCalcOutcomes = useCallback(async () => {
    setCalculating(true);
    setCalcResult(null);
    setCalcError(null);
    try {
      const res = await calculateOutcomes({ minDays: 7 });
      setCalcResult(res);
      load();
    } catch (e: unknown) {
      setCalcError(e instanceof Error ? e.message : String(e));
    } finally {
      setCalculating(false);
    }
  }, [load]);

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 p-6 max-w-[1600px] mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between mb-6 gap-4">
        <div>
          <Link href="/scanner/sp500" className="text-zinc-500 text-sm hover:text-zinc-300 transition-colors mb-2 inline-block">
            ← Scanner
          </Link>
          <h1 className="text-2xl font-bold text-zinc-100">Signal Journal</h1>
          <p className="text-zinc-500 text-sm mt-1">
            Historical record of every scanner signal · click any signal row to review &amp; annotate
          </p>
        </div>

        <div className="flex flex-col items-end gap-2 shrink-0">
          <div className="flex gap-2">
            <button
              onClick={handleCalcOutcomes}
              disabled={calculating || loading}
              className={clsx(
                "px-4 py-2 text-sm rounded-lg transition-colors border font-medium",
                calculating
                  ? "bg-zinc-800 border-zinc-700 text-zinc-500 cursor-not-allowed"
                  : "bg-zinc-800 border-zinc-600 text-zinc-300 hover:bg-zinc-700 hover:text-zinc-100",
              )}
            >
              {calculating ? "Calculating…" : "⚡ Calculate Outcomes"}
            </button>
            <button
              onClick={load}
              disabled={loading}
              className="px-4 py-2 text-sm bg-zinc-800 hover:bg-zinc-700 rounded-lg text-zinc-300 transition-colors"
            >
              ↻ Refresh
            </button>
          </div>

          {calcResult && !calculating && (
            <p className="text-xs text-emerald-400">
              ✓ {calcResult.calculated} calculated · {calcResult.rows_updated} row{calcResult.rows_updated !== 1 ? "s" : ""} updated
              {calcResult.errors > 0 && <span className="text-amber-400 ml-2">· {calcResult.errors} error{calcResult.errors !== 1 ? "s" : ""}</span>}
            </p>
          )}
          {calcResult?.candidates === 0 && !calculating && (
            <p className="text-xs text-zinc-500">No signals ready yet — need ≥ 7 days elapsed.</p>
          )}
          {calcError && !calculating && (
            <p className="text-xs text-red-400">{calcError}</p>
          )}
        </div>
      </div>

      {/* Stats */}
      {history && (
        <div className="flex flex-wrap gap-3 mb-6">
          <StatPill label="Total Signals"  value={history.total_signals}  sub="all time" />
          <StatPill label="Unique Tickers" value={history.unique_tickers} sub="ever flagged" />
          <StatPill label="Scan Days"      value={history.dates.length}   sub="with data" />
          {history.leaderboard[0] && (
            <StatPill label="Most Repeated" value={history.leaderboard[0].ticker} sub={`${history.leaderboard[0].appearances}×`} />
          )}
          {history.daily_summary[0] && (
            <StatPill label="Last Scan" value={history.daily_summary[0].scan_date} sub={`${history.daily_summary[0].signal_count} signals`} />
          )}
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && history?.total_signals === 0 && (
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-8 py-12 text-center">
          <p className="text-zinc-400 text-lg mb-2">No signals saved yet.</p>
          <p className="text-zinc-600 text-sm">
            Run a scan on the{" "}
            <Link href="/scanner/sp500" className="text-blue-400 hover:underline">Scanner page</Link>
            {" "}then click <span className="font-semibold text-zinc-300">💾 Save Today&apos;s Scan</span> to start building the journal.
          </p>
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-800 bg-red-900/20 px-4 py-3 text-red-400 text-sm mb-4">{error}</div>
      )}

      {loading && (
        <div className="text-zinc-500 text-sm animate-pulse">Loading journal…</div>
      )}

      {/* Tabs */}
      {history && history.total_signals > 0 && (
        <>
          <div className="flex gap-2 mb-4">
            {(["daily", "leaderboard", "feed"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={clsx(
                  "px-4 py-1.5 rounded-lg text-sm font-medium transition-colors",
                  tab === t ? "bg-blue-600 text-white" : "bg-zinc-800 text-zinc-400 hover:text-zinc-200",
                )}
              >
                {t === "daily" ? "Daily Summary" : t === "leaderboard" ? "Ticker Leaderboard" : "Signal Feed"}
              </button>
            ))}
          </div>

          {tab === "daily"       && <DailySummaryTable rows={history.daily_summary} />}
          {tab === "leaderboard" && (
            <>
              <LeaderboardTable rows={history.leaderboard} onSelect={(t) => setSelected(t || null)} selected={selectedTicker} />
              {selectedTicker && <TickerDrillDown ticker={selectedTicker} />}
            </>
          )}
          {tab === "feed" && <RecentSignals signals={history.recent_signals} />}
        </>
      )}

      {/* Legend */}
      {history && history.total_signals > 0 && (
        <div className="mt-8 text-[11px] text-zinc-700 space-y-1">
          <p>
            <span className="text-zinc-500">Review flow: </span>
            New → click row to expand → check Reviewed → verify spread at broker → check Broker Verified + fill bid/ask → check Trade Taken.
          </p>
          <p>
            <span className="text-zinc-500">FWD columns: </span>
            stock return / realized vol / premium capture · green = stock fell (good for vol seller).
          </p>
          <p>
            <span className="text-zinc-500">Outcome: </span>
            ✓ = premium_capture_20d &gt; 0 · ✗ = vol underpriced ·{" "}
            Click <span className="text-zinc-400">⚡ Calculate Outcomes</span> to back-fill once ≥ 7 days elapsed.
          </p>
        </div>
      )}
    </div>
  );
}
