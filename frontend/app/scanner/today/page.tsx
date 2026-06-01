"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { clsx } from "clsx";
import {
  getWorkflowStatus,
  updateJournalSignal,
  saveScanJournal,
  type WorkflowStatus,
  type WorkflowSignal,
  type JournalReviewUpdate,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(v: number | null | undefined, d = 1) {
  if (v == null || !isFinite(v)) return "—";
  return v.toFixed(d);
}

function signalColor(sig: string) {
  return sig === "SELL_AGGRESSIVE" ? "text-red-400" :
         sig === "SELL_SELECTIVE"  ? "text-orange-400" : "text-zinc-400";
}

// ---------------------------------------------------------------------------
// Step indicator
// ---------------------------------------------------------------------------

function StepRow({
  n, label, detail, complete, action,
}: {
  n:        number;
  label:    string;
  detail:   string;
  complete: boolean;
  action?:  React.ReactNode;
}) {
  return (
    <div className={clsx(
      "flex items-start gap-4 px-5 py-4 border-b border-zinc-800/60 transition-colors",
      complete ? "bg-zinc-900/20" : "bg-zinc-900/50",
    )}>
      {/* Step circle */}
      <div className={clsx(
        "w-8 h-8 rounded-full shrink-0 flex items-center justify-center text-sm font-bold mt-0.5 border-2",
        complete
          ? "bg-emerald-500/20 border-emerald-500/60 text-emerald-400"
          : "bg-zinc-800 border-zinc-700 text-zinc-500",
      )}>
        {complete ? "✓" : n}
      </div>

      {/* Label + detail */}
      <div className="flex-1 min-w-0">
        <p className={clsx("font-semibold text-sm", complete ? "text-zinc-300" : "text-zinc-200")}>
          {label}
        </p>
        <p className={clsx("text-xs mt-0.5", complete ? "text-zinc-600" : "text-zinc-500")}>
          {detail}
        </p>
      </div>

      {/* Action button/link */}
      {action && <div className="shrink-0 self-center">{action}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Signal review card (inline for today's candidates)
// ---------------------------------------------------------------------------

type SaveState = "idle" | "saving" | "saved" | "error";

function CandidateCard({
  signal,
  onUpdate,
}: {
  signal:   WorkflowSignal;
  onUpdate: (fields: JournalReviewUpdate) => Promise<void>;
}) {
  const [saving, setSaving]     = useState<SaveState>("idle");
  const [notes,  setNotes]      = useState(signal.notes ?? "");
  const [bid,    setBid]        = useState("");
  const [ask,    setAsk]        = useState("");

  const save = async (fields: JournalReviewUpdate) => {
    setSaving("saving");
    try {
      await onUpdate(fields);
      setSaving("saved");
      setTimeout(() => setSaving("idle"), 1500);
    } catch {
      setSaving("error");
    }
  };

  const toggleBool = (field: keyof JournalReviewUpdate, val: boolean) =>
    save({ [field]: val });

  const commitBidAsk = () => {
    const b = parseFloat(bid), a = parseFloat(ask);
    if (!isNaN(b) && !isNaN(a)) save({ actual_bid: b, actual_ask: a });
  };

  const commitNotes = () => {
    if (notes !== signal.notes) save({ notes });
  };

  // Derive status
  const status =
    signal.trade_taken     ? "traded"   :
    signal.broker_verified ? "verified" :
    signal.reviewed        ? "reviewed" : "new";

  const statusBadge = {
    new:      "bg-zinc-800 text-zinc-500 border-zinc-700",
    reviewed: "bg-blue-900/40 text-blue-300 border-blue-700/50",
    verified: "bg-emerald-900/40 text-emerald-300 border-emerald-700/50",
    traded:   "bg-amber-900/40 text-amber-300 border-amber-600/50",
  }[status];

  return (
    <div className={clsx(
      "rounded-xl border overflow-hidden transition-colors",
      signal.trade_taken     ? "border-amber-700/30"   :
      signal.broker_verified ? "border-emerald-700/30" :
      signal.reviewed        ? "border-blue-700/30"    :
                               "border-zinc-800",
    )}>
      {/* Header row */}
      <div className="flex items-center gap-4 px-4 py-3 bg-zinc-900/60">
        <div className="flex flex-col">
          <span className="font-bold text-zinc-100 text-base">{signal.ticker}</span>
          <span className={clsx("text-[10px] font-semibold", signalColor(signal.trade_signal))}>
            {signal.trade_signal === "SELL_AGGRESSIVE" ? "Sell Aggr." : "Sell Sel."}
          </span>
        </div>

        <div className="flex items-center gap-3 text-xs flex-wrap">
          <span>
            <span className="text-zinc-600">IV/RV </span>
            <span className="font-bold text-zinc-100 tabular-nums">{fmt(signal.iv_rv_ratio, 2)}×</span>
          </span>
          <span>
            <span className="text-zinc-600">IV30 </span>
            <span className="text-zinc-300 tabular-nums">{fmt(signal.iv30)}%</span>
          </span>
          <span>
            <span className="text-zinc-600">Score </span>
            <span className="font-bold text-blue-400 tabular-nums">{fmt(signal.scanner_score, 0)}</span>
          </span>
          {signal.event_risk_flag && (
            <span className="text-amber-400 text-[10px]">⚠ event</span>
          )}
          {signal.earnings_penalty > 0 && (
            <span className="text-yellow-500 text-[10px]">earn −{signal.earnings_penalty.toFixed(0)}</span>
          )}
        </div>

        <span className={clsx("ml-auto px-2 py-0.5 rounded text-[10px] font-semibold border", statusBadge)}>
          {status.charAt(0).toUpperCase() + status.slice(1)}
        </span>

        {saving === "saving" && <span className="text-zinc-500 text-[10px]">saving…</span>}
        {saving === "saved"  && <span className="text-emerald-500 text-[10px]">✓</span>}
        {saving === "error"  && <span className="text-red-400 text-[10px]">error</span>}
      </div>

      {/* Review controls */}
      <div className="grid grid-cols-3 gap-4 px-4 py-3 bg-zinc-950/40 text-xs">

        {/* Checkboxes */}
        <div className="space-y-2">
          <p className="text-zinc-600 uppercase tracking-wide text-[10px] font-semibold mb-1">Status</p>
          {([
            ["reviewed",         "Reviewed",          signal.reviewed]         ,
            ["broker_verified",  "Broker Verified",   signal.broker_verified]  ,
            ["trade_considered", "Trade Considered",  signal.trade_considered] ,
            ["trade_taken",      "Trade Taken",        signal.trade_taken]      ,
          ] as [keyof JournalReviewUpdate, string, boolean][]).map(([f, lbl, val]) => (
            <label key={String(f)} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={val}
                onChange={(e) => toggleBool(f, e.target.checked)}
                className="w-3.5 h-3.5 accent-blue-500"
              />
              <span className={val ? "text-zinc-200" : "text-zinc-500"}>{lbl}</span>
            </label>
          ))}
        </div>

        {/* Broker quote */}
        <div>
          <p className="text-zinc-600 uppercase tracking-wide text-[10px] font-semibold mb-1">Broker Quote</p>
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="text-zinc-600 w-6">Bid</span>
              <input
                type="number" step="0.01" value={bid}
                onChange={(e) => setBid(e.target.value)}
                onBlur={commitBidAsk}
                placeholder="0.00"
                className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 w-20 text-zinc-200 text-xs focus:outline-none focus:border-blue-500 tabular-nums"
              />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-zinc-600 w-6">Ask</span>
              <input
                type="number" step="0.01" value={ask}
                onChange={(e) => setAsk(e.target.value)}
                onBlur={commitBidAsk}
                placeholder="0.00"
                className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 w-20 text-zinc-200 text-xs focus:outline-none focus:border-blue-500 tabular-nums"
              />
            </div>
          </div>
        </div>

        {/* Notes */}
        <div>
          <p className="text-zinc-600 uppercase tracking-wide text-[10px] font-semibold mb-1">Notes</p>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            onBlur={commitNotes}
            rows={4}
            placeholder="Spread wide… skipping. / Earnings too close."
            className="w-full bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-zinc-300 text-xs focus:outline-none focus:border-blue-500 resize-none leading-relaxed"
          />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Progress bar
// ---------------------------------------------------------------------------

function ProgressBar({ done, total }: { done: number; total: number }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const color =
    pct === 100 ? "bg-emerald-500" :
    pct >= 50   ? "bg-blue-500"    :
                  "bg-zinc-600";
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
        <div className={clsx("h-full rounded-full transition-all", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-zinc-500 tabular-nums shrink-0">{done}/{total}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function TodayPage() {
  const [status,  setStatus]  = useState<WorkflowStatus | null>(null);
  const [signals, setSignals] = useState<WorkflowSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);
  const [saving,  setSaving]  = useState(false);
  const [saved,   setSaved]   = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getWorkflowStatus()
      .then((s) => { setStatus(s); setSignals(s.signals_today); })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleSaveScan = async () => {
    setSaving(true);
    try {
      await saveScanJournal();
      setSaved(true);
      setTimeout(() => { setSaved(false); load(); }, 1500);
    } catch { /* swallow — UI will show stale state */ }
    finally { setSaving(false); }
  };

  const handleUpdate = useCallback(
    async (ticker: string, fields: JournalReviewUpdate) => {
      if (!status) return;
      await updateJournalSignal(status.date, ticker, fields);
      setSignals((prev) =>
        prev.map((s) => s.ticker === ticker ? { ...s, ...fields } : s)
      );
    },
    [status],
  );

  const today = status?.date ?? new Date().toISOString().split("T")[0];

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 p-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <Link href="/" className="text-zinc-600 hover:text-zinc-400 text-sm transition-colors">← Dashboard</Link>
            <span className="text-zinc-700">/</span>
            <Link href="/scanner/sp500" className="text-zinc-600 hover:text-zinc-400 text-sm transition-colors">Scanner</Link>
          </div>
          <h1 className="text-2xl font-bold text-zinc-100">Today&apos;s Workflow</h1>
          <p className="text-zinc-500 text-sm mt-0.5">{today}</p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="px-4 py-2 text-sm bg-zinc-800 hover:bg-zinc-700 rounded-lg text-zinc-300 transition-colors"
        >
          ↻ Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-800 bg-red-900/20 px-4 py-3 text-red-400 text-sm mb-4">{error}</div>
      )}
      {loading && (
        <div className="text-zinc-500 text-sm animate-pulse mb-4">Loading workflow status…</div>
      )}

      {status && (
        <>
          {/* Progress */}
          <div className="mb-5">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-zinc-500 uppercase tracking-wide">Daily progress</span>
              <span className="text-xs text-zinc-400">
                {status.steps_complete}/{status.total_steps} steps complete
              </span>
            </div>
            <ProgressBar done={status.steps_complete} total={status.total_steps} />
          </div>

          {/* Step checklist */}
          <div className="rounded-xl border border-zinc-800 overflow-hidden mb-6">

            <StepRow
              n={1} label="Capture live IV surface"
              complete={status.steps.iv_captured.complete}
              detail={status.steps.iv_captured.detail}
              action={
                !status.steps.iv_captured.complete ? (
                  <code className="text-[10px] bg-zinc-800 px-2 py-1 rounded text-zinc-400 font-mono">
                    python -m scripts.eod_workflow
                  </code>
                ) : undefined
              }
            />

            <StepRow
              n={2} label="Run S&P 500 scanner"
              complete={status.steps.scan_run.complete}
              detail={status.steps.scan_run.detail}
              action={
                !status.steps.scan_run.complete ? (
                  <Link
                    href="/scanner/sp500"
                    className="px-3 py-1.5 rounded-lg text-xs font-medium bg-blue-600 hover:bg-blue-500 text-white transition-colors"
                  >
                    Open Scanner →
                  </Link>
                ) : status.steps.scan_run.complete && !status.steps.scan_saved.complete ? (
                  <button
                    onClick={handleSaveScan}
                    disabled={saving}
                    className="px-3 py-1.5 rounded-lg text-xs font-medium bg-zinc-700 hover:bg-zinc-600 text-zinc-200 transition-colors"
                  >
                    {saving ? "Saving…" : saved ? "✓ Saved" : "Save →"}
                  </button>
                ) : undefined
              }
            />

            <StepRow
              n={3} label="Save scan to journal"
              complete={status.steps.scan_saved.complete}
              detail={status.steps.scan_saved.detail}
              action={
                status.steps.scan_run.complete && !status.steps.scan_saved.complete ? (
                  <button
                    onClick={handleSaveScan}
                    disabled={saving}
                    className="px-3 py-1.5 rounded-lg text-xs font-medium bg-zinc-700 hover:bg-zinc-600 text-zinc-200 transition-colors"
                  >
                    {saving ? "Saving…" : saved ? "✓ Saved" : "💾 Save Scan"}
                  </button>
                ) : undefined
              }
            />

            <StepRow
              n={4} label="Review top candidates"
              complete={status.steps.reviewed.complete}
              detail={status.steps.reviewed.detail}
            />

            <StepRow
              n={5} label="Broker verify / mark trade decisions"
              complete={status.steps.broker_verified.complete}
              detail={status.steps.broker_verified.detail}
            />

            <StepRow
              n={6} label="Add notes"
              complete={status.steps.notes_added.complete}
              detail={status.steps.notes_added.detail}
            />
          </div>

          {/* Candidate cards (steps 4-6 inline) */}
          {signals.length > 0 && (
            <>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-zinc-300">
                  Today&apos;s Candidates
                  <span className="ml-2 text-zinc-600 font-normal">{signals.length} signals</span>
                </h2>
                <Link
                  href="/scanner/history"
                  className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
                >
                  Full journal →
                </Link>
              </div>

              <div className="space-y-3">
                {signals.map((s) => (
                  <CandidateCard
                    key={s.ticker}
                    signal={s}
                    onUpdate={(fields) => handleUpdate(s.ticker, fields)}
                  />
                ))}
              </div>
            </>
          )}

          {/* Empty state for steps 4-6 */}
          {signals.length === 0 && status.steps.scan_saved.complete && (
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/40 px-6 py-10 text-center">
              <p className="text-zinc-500 text-sm">No qualifying signals saved for today.</p>
              <p className="text-zinc-700 text-xs mt-1">Run a scan and save it first.</p>
            </div>
          )}

          {signals.length === 0 && !status.steps.scan_saved.complete && (
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/20 px-6 py-8 text-center text-zinc-600 text-sm">
              Complete steps 1–3 to see today&apos;s candidates here.
            </div>
          )}

          {/* CLI hint */}
          <div className="mt-8 rounded-lg bg-zinc-900/40 border border-zinc-800 px-4 py-3 text-xs text-zinc-600 font-mono">
            <p className="text-zinc-500 mb-1 font-sans">Automate steps 1–3:</p>
            DATA_PROVIDER=real python -m scripts.eod_workflow
          </div>
        </>
      )}
    </div>
  );
}
