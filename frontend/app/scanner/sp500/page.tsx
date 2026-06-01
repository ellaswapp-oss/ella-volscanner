"use client";

import { useState, useCallback, useEffect, type ReactNode } from "react";
import Link from "next/link";
import { clsx } from "clsx";
import {
  getSP500Scan,
  saveScanJournal,
  type SP500ScanResult,
  type ScannerTickerResult,
  type SP500ScanParams,
  type ScannerSortField,
  type SpreadQualityLabel,
  type IVSourceQuality,
  type JournalSaveResult,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SORT_LABELS: Record<ScannerSortField, string> = {
  score:           "Score",
  iv_rv_ratio:     "IV/RV",
  vrp_abs:         "VRP (pts)",
  iv30:            "IV30",
  regime_score:    "Regime",
  liquidity_score: "Liquidity",
};

const SIGNAL_META: Record<string, { label: string; color: string; dot: string }> = {
  SELL_AGGRESSIVE: {
    label: "Sell Aggr.",
    color: "text-red-400",
    dot:   "bg-red-400",
  },
  SELL_SELECTIVE: {
    label: "Sell Sel.",
    color: "text-orange-400",
    dot:   "bg-orange-400",
  },
  NEUTRAL: {
    label: "Neutral",
    color: "text-yellow-400",
    dot:   "bg-yellow-400",
  },
  BUY_PREMIUM: {
    label: "Buy Prem.",
    color: "text-emerald-400",
    dot:   "bg-emerald-400",
  },
};

const SURFACE_META: Record<string, { color: string; badge: string }> = {
  healthy:    { color: "text-emerald-400", badge: "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30" },
  suspicious: { color: "text-amber-400",   badge: "bg-amber-500/15  text-amber-300  border border-amber-500/30"  },
  invalid:    { color: "text-red-400",     badge: "bg-red-500/15    text-red-300    border border-red-500/30"    },
};

const SPREAD_META: Record<SpreadQualityLabel, { label: string; badge: string }> = {
  TIGHT:    { label: "Tight",     badge: "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30" },
  FAIR:     { label: "Fair",      badge: "bg-blue-500/15    text-blue-300    border border-blue-500/30"    },
  WIDE:     { label: "Wide",      badge: "bg-amber-500/15   text-amber-300   border border-amber-500/30"   },
  VERY_WIDE:{ label: "V.Wide",    badge: "bg-red-500/15     text-red-300     border border-red-500/30"     },
  NO_QUOTE: { label: "No Quote",  badge: "bg-zinc-700/40    text-zinc-400    border border-zinc-600/30"    },
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SignalBadge({ signal }: { signal: string }) {
  const m = SIGNAL_META[signal] ?? SIGNAL_META["NEUTRAL"];
  return (
    <span className={clsx("flex items-center gap-1 text-xs font-medium", m.color)}>
      <span className={clsx("h-1.5 w-1.5 rounded-full shrink-0", m.dot)} />
      {m.label}
    </span>
  );
}

function SurfaceBadge({ status }: { status: string }) {
  const m = SURFACE_META[status] ?? SURFACE_META["suspicious"];
  return (
    <span className={clsx("px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide", m.badge)}>
      {status}
    </span>
  );
}

function SpreadBadge({ quality }: { quality: SpreadQualityLabel }) {
  const m = SPREAD_META[quality] ?? SPREAD_META["NO_QUOTE"];
  return (
    <span className={clsx("px-1.5 py-0.5 rounded text-[10px] font-semibold", m.badge)}>
      {m.label}
    </span>
  );
}

const IV_SOURCE_META: Record<IVSourceQuality, { label: string; badge: string; title: string }> = {
  quoted_market_iv: {
    label: "Mkt Quote",
    badge: "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30",
    title: "IV derived from live bid/ask market quotes",
  },
  polygon_model_iv_no_quote: {
    label: "Model IV",
    badge: "bg-yellow-500/15 text-yellow-300 border border-yellow-500/30",
    title: "Polygon model IV — no market quote returned. Verify spread with your broker before trading.",
  },
  invalid: {
    label: "Invalid",
    badge: "bg-red-500/15 text-red-400 border border-red-500/30",
    title: "IV could not be computed",
  },
};

function IVSourceBadge({ quality }: { quality: IVSourceQuality }) {
  const m = IV_SOURCE_META[quality] ?? IV_SOURCE_META["invalid"];
  return (
    <span
      className={clsx("px-1.5 py-0.5 rounded text-[10px] font-semibold", m.badge)}
      title={m.title}
    >
      {m.label}
    </span>
  );
}

function EarningsBadge({ row }: { row: ScannerTickerResult }) {
  const dte = row.days_to_earnings;
  if (!row.event_risk_flag || dte == null) {
    // No known event within 30 days — soft grey
    return (
      <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-zinc-700/40 text-zinc-500 border border-zinc-600/30">
        No event
      </span>
    );
  }
  const urgentColor =
    dte <= 7  ? "bg-red-500/15 text-red-300 border-red-500/30" :
    dte <= 14 ? "bg-orange-500/15 text-orange-300 border-orange-500/30" :
                "bg-amber-500/15 text-amber-300 border-amber-500/30";
  return (
    <span
      className={clsx("px-1.5 py-0.5 rounded text-[10px] font-semibold border", urgentColor)}
      title={row.event_risk_reason ?? undefined}
    >
      Earn {dte}d
    </span>
  );
}

function TenorPips({ available, missing }: { available: string[]; missing: string[] }) {
  const all = ["iv7", "iv14", "iv30", "iv60", "iv90"];
  const short = (t: string) => t.replace("iv", "") + "D";
  return (
    <div className="flex gap-0.5">
      {all.map((t) => {
        const ok = available.includes(t);
        return (
          <span
            key={t}
            title={t}
            className={clsx(
              "inline-block h-2 w-2 rounded-sm",
              ok ? "bg-emerald-400" : "bg-zinc-700",
            )}
          />
        );
      })}
    </div>
  );
}

function LiquidityBar({ score }: { score: number }) {
  const color =
    score >= 70 ? "bg-emerald-400" :
    score >= 40 ? "bg-amber-400"   :
                  "bg-red-400";
  return (
    <div className="flex items-center gap-1">
      <div className="h-1.5 w-16 rounded-full bg-zinc-700 overflow-hidden">
        <div className={clsx("h-full rounded-full transition-all", color)} style={{ width: `${score}%` }} />
      </div>
      <span className="text-[10px] text-zinc-400 tabular-nums">{score.toFixed(0)}</span>
    </div>
  );
}

type MacroStatus = "available" | "unavailable" | "partial";

function MacroBanner({ penalty, reasons, dangerous, status, warning }: {
  penalty:   number | null;
  reasons:   string[];
  dangerous: boolean;
  status:    MacroStatus;
  warning:   string | null;
}) {
  // ── Macro data fully unavailable ──────────────────────────────────────
  if (status === "unavailable") {
    return (
      <div className="flex items-start gap-3 rounded-lg border border-zinc-600/40 bg-zinc-800/30 px-4 py-3 text-sm text-zinc-400">
        <span className="shrink-0 mt-0.5">⚠</span>
        <div>
          <span className="font-semibold text-zinc-300">Macro data unavailable</span>
          <span className="ml-2 opacity-70">
            Regime filter not applied — scores may be understated in stressed markets.
          </span>
        </div>
      </div>
    );
  }

  // ── Partial macro data ────────────────────────────────────────────────
  if (status === "partial") {
    return (
      <div className="rounded-lg border border-amber-600/30 bg-amber-900/10 px-4 py-3 text-sm text-amber-400">
        <span className="font-semibold mr-2">⚡ Macro data partial</span>
        {warning && <span className="opacity-75">{warning}</span>}
        {reasons.length > 0 && (
          <div className="mt-1.5 space-y-0.5">
            {reasons.map((r, i) => (
              <span key={i} className="block text-xs opacity-80">• {r}</span>
            ))}
          </div>
        )}
      </div>
    );
  }

  // ── Macro available + no active penalty ───────────────────────────────
  if (!penalty) return null;

  // ── Macro available + penalty active ─────────────────────────────────
  return (
    <div className={clsx(
      "rounded-lg border px-4 py-3 text-sm",
      dangerous
        ? "border-red-500/40 bg-red-500/10 text-red-300"
        : "border-amber-500/40 bg-amber-500/10 text-amber-300",
    )}>
      <span className="font-semibold mr-2">
        {dangerous ? "⚠️ Dangerous Macro Regime" : "⚡ Macro Penalties Active"}
        {" "}(−{penalty.toFixed(0)} pts)
      </span>
      {reasons.map((r, i) => (
        <span key={i} className="opacity-80 mr-3">{r}</span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scan quality panel
// ---------------------------------------------------------------------------

function QualityCount({ n, label, variant }: {
  n:       number;
  label:   string;
  variant: "emerald" | "amber" | "yellow" | "red" | "zinc";
}) {
  const cls = {
    emerald: "text-emerald-400",
    amber:   "text-amber-400",
    yellow:  "text-yellow-400",
    red:     "text-red-400",
    zinc:    "text-zinc-500",
  }[variant];
  return (
    <span className="flex items-center gap-1 whitespace-nowrap">
      <span className={clsx("font-bold tabular-nums", cls)}>{n}</span>
      <span className="text-zinc-600">{label}</span>
    </span>
  );
}

function QualitySection({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2 min-w-0">
      <span className="text-[10px] uppercase tracking-wide text-zinc-600 font-semibold shrink-0 whitespace-nowrap">
        {label}
      </span>
      <div className="flex items-center gap-2 flex-wrap">{children}</div>
    </div>
  );
}

function QualityDivider() {
  return <div className="w-px h-4 bg-zinc-800 shrink-0" />;
}

function ScanQualityPanel({
  results,
  macroStatus,
  macroWarning,
}: {
  results:      ScannerTickerResult[];
  macroStatus:  MacroStatus;
  macroWarning: string | null;
}) {
  const n = results.length;
  if (n === 0) return null;

  const count = (fn: (r: ScannerTickerResult) => boolean) =>
    results.filter(fn).length;

  const ivQuoted     = count(r => r.iv_source_quality === "quoted_market_iv");
  const ivModel      = count(r => r.iv_source_quality === "polygon_model_iv_no_quote");
  const ivInvalid    = count(r => r.iv_source_quality === "invalid");
  const earFlagged   = count(r => r.event_risk_flag);
  const earClear     = n - earFlagged;
  const sfHealthy    = count(r => r.iv_surface_status === "healthy");
  const sfSuspicious = count(r => r.iv_surface_status === "suspicious");
  const sfInvalid    = count(r => r.iv_surface_status === "invalid");
  const brokerVerify = count(r => r.broker_verify_required);

  const macroColor =
    macroStatus === "available" ? "text-emerald-400" :
    macroStatus === "partial"   ? "text-amber-400"   :
                                  "text-zinc-500";

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 bg-zinc-900/30 border border-zinc-800/60 rounded-xl px-5 py-3 text-xs">
      <span className="text-[10px] uppercase tracking-widest text-zinc-600 font-semibold shrink-0">
        Scan Quality
      </span>

      <QualityDivider />

      {/* IV Source */}
      <QualitySection label="IV source">
        {ivQuoted  > 0 && <QualityCount n={ivQuoted}  label="quoted"  variant="emerald" />}
        {ivModel   > 0 && <QualityCount n={ivModel}   label="model"   variant="yellow"  />}
        {ivInvalid > 0 && <QualityCount n={ivInvalid} label="invalid" variant="red"     />}
        {ivQuoted === 0 && ivModel === 0 && ivInvalid === 0 && (
          <span className="text-zinc-700">—</span>
        )}
      </QualitySection>

      <QualityDivider />

      {/* Macro */}
      <QualitySection label="Macro">
        <span
          className={clsx("font-semibold", macroColor)}
          title={macroWarning ?? undefined}
        >
          {macroStatus}{macroStatus !== "available" ? " ⚠" : " ✓"}
        </span>
      </QualitySection>

      <QualityDivider />

      {/* Earnings risk */}
      <QualitySection label="Earnings risk">
        {earFlagged > 0 && <QualityCount n={earFlagged} label="flagged" variant="amber" />}
        {earClear   > 0 && <QualityCount n={earClear}   label="clear"   variant="zinc"  />}
      </QualitySection>

      <QualityDivider />

      {/* IV Surfaces */}
      <QualitySection label="Surfaces">
        {sfHealthy    > 0 && <QualityCount n={sfHealthy}    label="healthy"    variant="emerald" />}
        {sfSuspicious > 0 && <QualityCount n={sfSuspicious} label="suspicious" variant="amber"   />}
        {sfInvalid    > 0 && <QualityCount n={sfInvalid}    label="invalid"    variant="red"     />}
      </QualitySection>

      {/* Broker verify count — show in panel only when partial (not when all require it,
          since BrokerVerifyBanner below covers that case more prominently) */}
      {brokerVerify > 0 && brokerVerify < n && (
        <>
          <QualityDivider />
          <QualitySection label="Broker verify">
            <QualityCount n={brokerVerify} label="required" variant="yellow" />
          </QualitySection>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Broker verification warning
// ---------------------------------------------------------------------------

function BrokerVerifyBanner({ brokerVerify, total }: { brokerVerify: number; total: number }) {
  if (brokerVerify === 0) return null;

  const allRequire = brokerVerify === total;

  return (
    <div className={clsx(
      "rounded-xl border px-5 py-4 text-sm",
      allRequire
        ? "border-yellow-600/30 bg-yellow-950/20"
        : "border-yellow-700/20 bg-zinc-900/40",
    )}>
      <div className="flex items-start gap-3">
        <span className="text-yellow-500 text-base shrink-0 mt-0.5">⚠</span>
        <div className="space-y-1.5">
          <p className="font-semibold text-yellow-300">
            {allRequire
              ? `No live option quotes — all ${total} result${total !== 1 ? "s" : ""} use Polygon model IV`
              : `${brokerVerify} of ${total} result${total !== 1 ? "s" : ""} use Polygon model IV (no live quote)`}
          </p>
          <p className="text-zinc-400 text-xs leading-relaxed">
            Polygon&apos;s snapshot API returns model-computed IV without bid/ask prices when
            markets are closed or liquidity is thin.{" "}
            <strong className="text-zinc-300">The IV values shown may not reflect actual tradeable prices.</strong>
          </p>
          <ul className="text-zinc-500 text-xs space-y-0.5 list-none mt-1">
            <li>→ Verify each ticker&apos;s ATM option spread in your broker platform before entering a position.</li>
            <li>→ Check that the actual bid/ask mid aligns with the model IV shown here.</li>
            <li>→ Wide spreads or stale quotes can make the VRP appear larger than it is.</li>
            <li>→ Re-run the scan during market hours (9:30–16:00 ET) for live quoted IV.</li>
          </ul>
        </div>
      </div>
    </div>
  );
}

function StatPill({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-zinc-800/60 border border-zinc-700/50 rounded-lg px-4 py-3 text-center min-w-[90px]">
      <p className="text-[11px] text-zinc-500 uppercase tracking-wide">{label}</p>
      <p className="text-xl font-bold text-zinc-100 tabular-nums">{value}</p>
      {sub && <p className="text-[10px] text-zinc-500">{sub}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter panel
// ---------------------------------------------------------------------------

interface FilterState {
  maxTickers:               number;
  minRatio:                 number;
  minValidTenors:           number;
  maxSpreadPct:             number;
  minOpenInterest:          number;
  includeSuspicious:        boolean;
  excludeEarningsWithinDays: number;  // 0 = show all with warning
}

const DEFAULT_FILTERS: FilterState = {
  maxTickers:                50,
  minRatio:                  1.30,
  minValidTenors:            2,
  maxSpreadPct:              0.25,
  minOpenInterest:           100,
  includeSuspicious:         true,
  excludeEarningsWithinDays: 0,
};

function FilterPanel({
  filters, setFilters, onScan, scanning,
}: {
  filters: FilterState;
  setFilters: (f: FilterState) => void;
  onScan: () => void;
  scanning: boolean;
}) {
  const update = (key: keyof FilterState, val: number | boolean) =>
    setFilters({ ...filters, [key]: val });

  return (
    <div className="bg-zinc-800/40 border border-zinc-700/50 rounded-xl p-5">
      <div className="flex flex-wrap gap-5 items-end">

        {/* Max tickers */}
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-zinc-400 uppercase tracking-wide">Tickers to scan</span>
          <select
            className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm text-zinc-200"
            value={filters.maxTickers}
            onChange={(e) => update("maxTickers", Number(e.target.value))}
          >
            {[25, 50, 100, 200, 500].map((n) => (
              <option key={n} value={n}>Top {n} by mkt cap</option>
            ))}
          </select>
        </label>

        {/* Min IV/RV ratio */}
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-zinc-400 uppercase tracking-wide">Min IV/RV ratio</span>
          <select
            className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm text-zinc-200"
            value={filters.minRatio}
            onChange={(e) => update("minRatio", Number(e.target.value))}
          >
            {[1.10, 1.20, 1.30, 1.40, 1.50, 1.75, 2.00].map((r) => (
              <option key={r} value={r}>{r.toFixed(2)}×</option>
            ))}
          </select>
        </label>

        {/* Min valid tenors */}
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-zinc-400 uppercase tracking-wide">Min tenors</span>
          <select
            className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm text-zinc-200"
            value={filters.minValidTenors}
            onChange={(e) => update("minValidTenors", Number(e.target.value))}
          >
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>{n}+ tenor{n > 1 ? "s" : ""}</option>
            ))}
          </select>
        </label>

        {/* Max spread pct */}
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-zinc-400 uppercase tracking-wide">Max spread</span>
          <select
            className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm text-zinc-200"
            value={filters.maxSpreadPct}
            onChange={(e) => update("maxSpreadPct", Number(e.target.value))}
          >
            {[0.10, 0.15, 0.20, 0.25, 0.35, 0.50].map((p) => (
              <option key={p} value={p}>{(p * 100).toFixed(0)}%</option>
            ))}
          </select>
        </label>

        {/* Min OI */}
        <label className="flex flex-col gap-1">
          <span className="text-[11px] text-zinc-400 uppercase tracking-wide">Min OI</span>
          <select
            className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-sm text-zinc-200"
            value={filters.minOpenInterest}
            onChange={(e) => update("minOpenInterest", Number(e.target.value))}
          >
            {[0, 50, 100, 250, 500, 1000].map((n) => (
              <option key={n} value={n}>{n === 0 ? "None" : n.toLocaleString()}</option>
            ))}
          </select>
        </label>

        {/* Exclude earnings within */}
        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-wide text-zinc-500 font-medium">
            Excl. Earnings
          </span>
          <select
            className="bg-zinc-800 border border-zinc-700 rounded-md px-2 py-1.5 text-sm text-zinc-200 focus:outline-none focus:border-blue-500"
            value={filters.excludeEarningsWithinDays}
            onChange={(e) => update("excludeEarningsWithinDays", Number(e.target.value))}
          >
            {[
              { label: "Off (warn only)", value: 0  },
              { label: "Within 7d",       value: 7  },
              { label: "Within 14d",      value: 14 },
              { label: "Within 30d",      value: 30 },
            ].map(({ label, value }) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>

        {/* Include suspicious */}
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            className="accent-amber-400 w-4 h-4"
            checked={filters.includeSuspicious}
            onChange={(e) => update("includeSuspicious", e.target.checked)}
          />
          <span className="text-sm text-zinc-300">Include suspicious surfaces</span>
        </label>

        {/* Scan button */}
        <button
          className={clsx(
            "ml-auto px-5 py-2 rounded-lg font-semibold text-sm transition-all",
            scanning
              ? "bg-zinc-700 text-zinc-400 cursor-not-allowed"
              : "bg-blue-600 hover:bg-blue-500 text-white",
          )}
          onClick={onScan}
          disabled={scanning}
        >
          {scanning ? "Scanning…" : "▶ Scan"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Results table
// ---------------------------------------------------------------------------

function ResultsTable({
  results, sortBy, onSort, macroStatus,
}: {
  results:     ScannerTickerResult[];
  sortBy:      ScannerSortField;
  onSort:      (f: ScannerSortField) => void;
  macroStatus: MacroStatus;
}) {
  const col = (key: ScannerSortField, label: string, className = "") => (
    <th
      key={key}
      className={clsx(
        "px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide cursor-pointer select-none whitespace-nowrap",
        sortBy === key ? "text-blue-400" : "text-zinc-500 hover:text-zinc-300",
        className,
      )}
      onClick={() => onSort(key)}
    >
      {label} {sortBy === key ? "▼" : ""}
    </th>
  );

  return (
    <div className="overflow-x-auto rounded-xl border border-zinc-700/50">
      <table className="w-full text-sm">
        <thead className="bg-zinc-800/60 border-b border-zinc-700/50">
          <tr>
            <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-zinc-500 w-8">#</th>
            <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-zinc-500">Ticker</th>
            <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-zinc-500">Price</th>
            {col("iv30",           "IV30",      "text-right")}
            <th className="px-3 py-2.5 text-right text-[11px] font-semibold uppercase tracking-wide text-zinc-500">RV30</th>
            {col("iv_rv_ratio",    "IV/RV",     "text-right")}
            {col("vrp_abs",        "VRP (pts)", "text-right")}
            {col("regime_score",   "Regime",    "text-right")}
            <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-zinc-500">Signal</th>
            <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-zinc-500">Surface</th>
            <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-zinc-500">Tenors</th>
            <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-zinc-500">Spread</th>
            {col("liquidity_score","Liquidity", "")}
            <th className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-zinc-500">Earnings</th>
            {col("score",          "Score",     "text-right")}
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800/60">
          {results.map((r, i) => (
            <ResultRow key={r.ticker} row={r} rank={i + 1} macroStatus={macroStatus} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ResultRow({ row, rank, macroStatus }: { row: ScannerTickerResult; rank: number; macroStatus: MacroStatus }) {
  const [expanded, setExpanded] = useState(false);

  const ratioColor =
    row.iv_rv_ratio >= 1.8 ? "text-red-400" :
    row.iv_rv_ratio >= 1.5 ? "text-orange-400" :
    row.iv_rv_ratio >= 1.3 ? "text-amber-300" :
    "text-zinc-300";

  return (
    <>
      <tr
        className="hover:bg-zinc-800/30 cursor-pointer transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        {/* Rank */}
        <td className="px-3 py-2.5 text-zinc-600 text-xs tabular-nums">{rank}</td>

        {/* Ticker */}
        <td className="px-3 py-2.5">
          <div className="flex items-center gap-1.5">
            <span className="font-bold text-zinc-100">{row.ticker}</span>
            {row.broker_verify_required && (
              <span
                title="Model IV only — verify market quote with your broker before trading"
                className="text-yellow-400 text-[10px] leading-none"
              >
                ⚠
              </span>
            )}
          </div>
        </td>

        {/* Price */}
        <td className="px-3 py-2.5 text-zinc-400 tabular-nums">
          ${row.price.toFixed(2)}
        </td>

        {/* IV30 */}
        <td className="px-3 py-2.5 text-right tabular-nums text-zinc-200">
          {row.iv30.toFixed(1)}%
        </td>

        {/* RV30 */}
        <td className="px-3 py-2.5 text-right tabular-nums text-zinc-400">
          {row.rv30.toFixed(1)}%
        </td>

        {/* IV/RV ratio */}
        <td className={clsx("px-3 py-2.5 text-right tabular-nums font-semibold", ratioColor)}>
          {row.iv_rv_ratio.toFixed(2)}×
        </td>

        {/* VRP */}
        <td className="px-3 py-2.5 text-right tabular-nums text-zinc-300">
          +{row.vrp_abs.toFixed(1)}
        </td>

        {/* Regime score */}
        <td className="px-3 py-2.5 text-right tabular-nums">
          <span className={clsx(
            "font-semibold",
            row.regime_score >= 70 ? "text-red-400" :
            row.regime_score >= 50 ? "text-orange-400" :
            row.regime_score >= 30 ? "text-amber-400" :
            "text-emerald-400",
          )}>
            {row.regime_score.toFixed(0)}
          </span>
        </td>

        {/* Signal */}
        <td className="px-3 py-2.5">
          <SignalBadge signal={row.signal.signal} />
        </td>

        {/* Surface health */}
        <td className="px-3 py-2.5">
          <SurfaceBadge status={row.iv_surface_status} />
        </td>

        {/* Tenor pips */}
        <td className="px-3 py-2.5">
          <TenorPips available={row.available_tenors} missing={row.missing_tenors} />
        </td>

        {/* Spread quality + IV source */}
        <td className="px-3 py-2.5">
          <div className="flex flex-col gap-1">
            <SpreadBadge quality={row.spread_quality} />
            <IVSourceBadge quality={row.iv_source_quality} />
          </div>
        </td>

        {/* Liquidity bar */}
        <td className="px-3 py-2.5">
          <LiquidityBar score={row.liquidity_score} />
        </td>

        {/* Earnings badge */}
        <td className="px-3 py-2.5">
          <EarningsBadge row={row} />
        </td>

        {/* Score */}
        <td className="px-3 py-2.5 text-right tabular-nums">
          <span className="font-bold text-blue-400">{row.score.toFixed(1)}</span>
        </td>
      </tr>

      {/* Expanded detail row */}
      {expanded && (
        <tr className="bg-zinc-900/50">
          <td colSpan={15} className="px-6 py-4">
            <ExpandedDetail row={row} macroStatus={macroStatus} />
          </td>
        </tr>
      )}
    </>
  );
}

function ExpandedDetail({ row, macroStatus }: { row: ScannerTickerResult; macroStatus: MacroStatus }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-6 text-sm">
      {/* IV / RV breakdown */}
      <div>
        <p className="text-zinc-500 text-xs uppercase tracking-wide mb-2">Volatility</p>
        <table className="text-xs w-full">
          <tbody>
            <DetailRow label="IV30" value={`${row.iv30.toFixed(2)}%`} />
            <DetailRow label="RV30" value={`${row.rv30.toFixed(2)}%`} />
            <DetailRow label="IV/RV" value={`${row.iv_rv_ratio.toFixed(3)}×`} emphasis />
            <DetailRow label="VRP (abs)" value={`+${row.vrp_abs.toFixed(2)} vpts`} />
            <DetailRow label="VRP (%)" value={`+${row.vrp_pct.toFixed(2)}%`} />
            <DetailRow label="IV Rank" value={`${row.iv_rank.toFixed(1)}`} />
          </tbody>
        </table>
      </div>

      {/* Options liquidity */}
      <div>
        <p className="text-zinc-500 text-xs uppercase tracking-wide mb-2">Liquidity</p>
        <table className="text-xs w-full">
          <tbody>
            <DetailRow label="ATM OI" value={row.atm_open_interest?.toLocaleString() ?? "—"} />
            <DetailRow label="ATM Vol" value={row.atm_volume?.toLocaleString() ?? "—"} />
            <DetailRow label="Spread %" value={row.spread_pct != null ? `${(row.spread_pct * 100).toFixed(1)}%` : "—"} />
            <DetailRow label="Quality" value={row.spread_quality} />
            <DetailRow label="Contracts" value={row.contract_count.toLocaleString()} />
            <DetailRow label="Liq score" value={row.liquidity_score.toFixed(1)} />
            <DetailRow
              label="IV source"
              value={row.iv_source_quality === "quoted_market_iv"          ? "Mkt Quote"  :
                     row.iv_source_quality === "polygon_model_iv_no_quote" ? "Model IV ⚠" :
                     "Invalid"}
              emphasis={row.broker_verify_required}
            />
          </tbody>
        </table>
      </div>

      {/* Tenors */}
      <div>
        <p className="text-zinc-500 text-xs uppercase tracking-wide mb-2">Tenors</p>
        <div className="space-y-1">
          {["iv7", "iv14", "iv30", "iv60", "iv90"].map((t) => {
            const ok = row.available_tenors.includes(t);
            return (
              <div key={t} className="flex items-center gap-2">
                <span className={clsx("h-2 w-2 rounded-sm shrink-0", ok ? "bg-emerald-400" : "bg-zinc-700")} />
                <span className={ok ? "text-zinc-300" : "text-zinc-600"}>{t.replace("iv", "iv")} </span>
                {!ok && <span className="text-zinc-600 text-[10px]">missing</span>}
              </div>
            );
          })}
        </div>
      </div>

      {/* Macro */}
      <div>
        <p className="text-zinc-500 text-xs uppercase tracking-wide mb-2">Macro</p>
        <table className="text-xs w-full">
          <tbody>
            <DetailRow
              label="Status"
              value={
                macroStatus === "available"   ? "Clear ✓" :
                macroStatus === "partial"     ? "Partial ⚡" :
                                               "Unavailable ⚠"
              }
              emphasis={macroStatus !== "available"}
            />
            <DetailRow
              label="Penalty"
              value={
                macroStatus === "unavailable"
                  ? "N/A"
                  : `−${row.macro_penalty.toFixed(0)} pts`
              }
            />
            <DetailRow label="Regime" value={`${row.regime_score.toFixed(0)}/100`} />
            <DetailRow label="Surface" value={row.iv_surface_status} />
          </tbody>
        </table>
        {row.macro_reasons.length > 0 && (
          <ul className="mt-2 text-[10px] text-amber-400/80 space-y-0.5">
            {row.macro_reasons.map((r, i) => <li key={i}>• {r}</li>)}
          </ul>
        )}
        {macroStatus === "unavailable" && (
          <p className="mt-1.5 text-[10px] text-zinc-600 leading-relaxed">
            Regime filter not applied — this ticker's score does not include macro penalty.
          </p>
        )}
        {macroStatus === "partial" && (
          <p className="mt-1.5 text-[10px] text-amber-600/70 leading-relaxed">
            Score reflects only available macro signals.
          </p>
        )}
      </div>

      {/* Earnings / Event */}
      <div>
        <p className="text-zinc-500 text-xs uppercase tracking-wide mb-2">Earnings</p>
        <table className="text-xs w-full">
          <tbody>
            <DetailRow
              label="Next date"
              value={row.next_earnings_date ?? "Unknown"}
            />
            <DetailRow
              label="Days away"
              value={row.days_to_earnings != null ? `${row.days_to_earnings}d` : "—"}
            />
            <DetailRow
              label="Within 14d"
              value={row.earnings_within_14d ? "Yes ⚠" : "No"}
              emphasis={row.earnings_within_14d}
            />
            <DetailRow
              label="Score −pts"
              value={row.earnings_score_penalty > 0
                ? `−${row.earnings_score_penalty.toFixed(0)}`
                : "None"}
              emphasis={row.earnings_score_penalty > 0}
            />
          </tbody>
        </table>
        {row.event_risk_reason && (
          <p className={clsx(
            "mt-2 text-[10px] leading-relaxed",
            row.earnings_within_14d ? "text-orange-400/90" : "text-amber-400/70",
          )}>
            ⚠ {row.event_risk_reason}
          </p>
        )}
      </div>
    </div>
  );
}

function DetailRow({ label, value, emphasis }: { label: string; value: string; emphasis?: boolean }) {
  return (
    <tr>
      <td className="py-0.5 text-zinc-500 pr-3">{label}</td>
      <td className={clsx("py-0.5 tabular-nums text-right", emphasis ? "text-amber-300 font-semibold" : "text-zinc-300")}>
        {value}
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Errors panel
// ---------------------------------------------------------------------------

function ErrorsPanel({ errors, total }: { errors: Array<{ ticker: string; error: string }>; total: number }) {
  const [open, setOpen] = useState(false);
  if (errors.length === 0) return null;
  return (
    <div className="border border-zinc-700/40 rounded-xl overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-4 py-3 text-sm text-zinc-400 hover:bg-zinc-800/30 transition-colors"
        onClick={() => setOpen((o) => !o)}
      >
        <span>⚠ {total} scanner error{total !== 1 ? "s" : ""} (showing up to 20)</span>
        <span>{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="px-4 pb-4 grid grid-cols-1 md:grid-cols-2 gap-2">
          {errors.map((e) => (
            <div key={e.ticker} className="text-xs bg-zinc-900/60 rounded px-3 py-1.5">
              <span className="font-semibold text-zinc-300 mr-2">{e.ticker}</span>
              <span className="text-zinc-500">{e.error}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function SP500ScannerPage() {
  const [scanResult, setScanResult]   = useState<SP500ScanResult | null>(null);
  const [scanning,   setScanning]     = useState(false);
  const [error,      setError]        = useState<string | null>(null);
  const [sortBy,     setSortBy]       = useState<ScannerSortField>("score");
  const [filters,    setFilters]      = useState<FilterState>(DEFAULT_FILTERS);
  const [elapsedSec, setElapsedSec]   = useState(0);
  const [saving,     setSaving]       = useState(false);
  const [saveResult, setSaveResult]   = useState<JournalSaveResult | null>(null);
  const [saveError,  setSaveError]    = useState<string | null>(null);

  // Derive sorted results
  const sortedResults = (() => {
    if (!scanResult?.results) return [];
    return [...scanResult.results].sort((a, b) => {
      const av = ((a as unknown) as Record<string, unknown>)[sortBy] as number ?? 0;
      const bv = ((b as unknown) as Record<string, unknown>)[sortBy] as number ?? 0;
      return bv - av;
    });
  })();

  // Elapsed counter while scanning
  useEffect(() => {
    if (!scanning) { setElapsedSec(0); return; }
    const id = setInterval(() => setElapsedSec((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [scanning]);

  // Build params from filters + sortBy
  const buildParams = useCallback((forceRefresh = false): SP500ScanParams => ({
    maxTickers:                 filters.maxTickers,
    minRatio:                   filters.minRatio,
    minValidTenors:             filters.minValidTenors,
    maxSpreadPct:               filters.maxSpreadPct,
    minOpenInterest:            filters.minOpenInterest,
    includeSuspicious:          filters.includeSuspicious,
    excludeEarningsWithinDays:  filters.excludeEarningsWithinDays,
    requireIv30:                true,
    excludeInvalid:             true,
    maxResults:                 filters.maxTickers,
    sortBy,
    forceRefresh,
  }), [filters, sortBy]);

  // Load cached result on mount (no force_refresh)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setScanning(true);
      setError(null);
      try {
        const res = await getSP500Scan(buildParams(false));
        if (!cancelled) setScanResult(res);
      } catch (err: unknown) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setScanning(false);
      }
    })();
    return () => { cancelled = true; };
    // Only on mount — deps intentionally empty
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleScan = useCallback(async () => {
    setScanning(true);
    setError(null);
    setSaveResult(null);
    setSaveError(null);
    try {
      const res = await getSP500Scan(buildParams(true));
      setScanResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setScanning(false);
    }
  }, [buildParams]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setSaveResult(null);
    setSaveError(null);
    try {
      const res = await saveScanJournal();
      setSaveResult(res);
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, []);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 p-6 md:p-8">
      {/* Header */}
      <div className="max-w-screen-2xl mx-auto space-y-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Link href="/" className="text-zinc-600 hover:text-zinc-400 text-sm transition-colors">
                ← Dashboard
              </Link>
            </div>
            <h1 className="text-2xl font-bold text-zinc-100">S&amp;P 500 Vol Scanner</h1>
            <p className="text-sm text-zinc-500 mt-1">
              Live IV/RV premium scan · ranked by composite score · filtered for liquidity
            </p>
          </div>

          {scanResult && (
            <div className="flex flex-col items-end gap-2">
              <div className="text-right text-xs text-zinc-500">
                <p>
                  {scanResult.from_cache ? "📦 Cached" : "🔄 Fresh"} ·{" "}
                  {new Date(scanResult.scan_timestamp).toLocaleTimeString()}
                </p>
                <p className="mt-0.5">
                  Scanned {scanResult.scanned} of {scanResult.total_universe} tickers
                  {" "}· {scanResult.elapsed_seconds.toFixed(1)}s
                </p>
              </div>

              {/* Save button + feedback */}
              <div className="flex flex-col items-end gap-1.5">
                <button
                  onClick={handleSave}
                  disabled={saving || scanning}
                  className={clsx(
                    "flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm font-medium transition-all border",
                    saving || scanning
                      ? "bg-zinc-800 border-zinc-700 text-zinc-500 cursor-not-allowed"
                      : "bg-zinc-800 border-zinc-600 text-zinc-300 hover:bg-zinc-700 hover:border-zinc-500 hover:text-zinc-100",
                  )}
                >
                  <span>{saving ? "Saving…" : "💾 Save Today's Scan"}</span>
                </button>

                {/* Save confirmation */}
                {saveResult && (
                  <p className="text-xs text-emerald-400">
                    ✓ Saved {saveResult.rows} signal{saveResult.rows !== 1 ? "s" : ""} for {saveResult.scan_date}
                  </p>
                )}

                {/* Save error */}
                {saveError && (
                  <p className="text-xs text-red-400 max-w-[260px] text-right">
                    ✗ {saveError}
                  </p>
                )}

                {/* Link to history */}
                <Link
                  href="/scanner/history"
                  className="text-[11px] text-zinc-600 hover:text-zinc-400 transition-colors"
                >
                  View signal history →
                </Link>
              </div>
            </div>
          )}
        </div>

        {/* Filter panel */}
        <FilterPanel
          filters={filters}
          setFilters={setFilters}
          onScan={handleScan}
          scanning={scanning}
        />

        {/* Scanning progress */}
        {scanning && (
          <div className="flex items-center gap-3 bg-blue-500/10 border border-blue-500/30 rounded-xl px-5 py-4 text-blue-300">
            <svg className="animate-spin h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            <span className="text-sm font-medium">
              Scanning S&amp;P 500… {elapsedSec}s
              {" "}<span className="text-blue-400/70 font-normal">
                (first scan fetches live options chains — typically 1–10 min; subsequent scans are cached and instant)
              </span>
            </span>
          </div>
        )}

        {/* Error */}
        {error && !scanning && (
          <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-5 py-4 text-red-300 text-sm">
            <span className="font-semibold">Scan error: </span>{error}
          </div>
        )}

        {/* Results */}
        {scanResult && !scanning && (
          <>
            {/* Macro banner */}
            <MacroBanner
              penalty={scanResult.macro_penalty}
              reasons={scanResult.macro_reasons}
              dangerous={scanResult.macro_dangerous}
              status={scanResult.macro_status ?? "unavailable"}
              warning={scanResult.macro_warning ?? null}
            />

            {/* Stats row */}
            <div className="flex flex-wrap gap-3">
              <StatPill label="Passed"   value={scanResult.passed}   sub="qualified" />
              <StatPill label="Scanned"  value={scanResult.scanned}  sub="tickers" />
              <StatPill label="Excluded" value={scanResult.failed}   sub="filtered out" />
              <StatPill label="Skipped"  value={scanResult.skipped}  sub="no data" />
              <StatPill label="Errors"   value={scanResult.error_count} sub="fetch errors" />
              <StatPill
                label="Macro"
                value={
                  scanResult.macro_penalty === null
                    ? "N/A"
                    : scanResult.macro_penalty === 0
                      ? "0 pts"
                      : `−${scanResult.macro_penalty.toFixed(0)} pts`
                }
                sub={
                  (scanResult.macro_status ?? "unavailable") === "unavailable" ? "unavailable" :
                  (scanResult.macro_status ?? "unavailable") === "partial"     ? "partial ⚡" :
                  scanResult.macro_dangerous                                   ? "DANGEROUS" :
                  (scanResult.macro_penalty ?? 0) > 0                          ? "active" :
                                                                                 "clear ✓"
                }
              />
              <StatPill label="Elapsed"  value={`${scanResult.elapsed_seconds.toFixed(1)}s`} sub={scanResult.from_cache ? "cached" : "fresh"} />
            </div>

            {/* Scan quality panel + broker warning */}
            {sortedResults.length > 0 && (() => {
              const brokerVerify = sortedResults.filter(r => r.broker_verify_required).length;
              return (
                <>
                  <ScanQualityPanel
                    results={sortedResults}
                    macroStatus={scanResult.macro_status ?? "unavailable"}
                    macroWarning={scanResult.macro_warning ?? null}
                  />
                  <BrokerVerifyBanner
                    brokerVerify={brokerVerify}
                    total={sortedResults.length}
                  />
                </>
              );
            })()}

            {/* Sort tabs */}
            <div className="flex flex-wrap gap-2">
              <span className="text-xs text-zinc-500 self-center mr-1">Sort:</span>
              {(Object.entries(SORT_LABELS) as [ScannerSortField, string][]).map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setSortBy(key)}
                  className={clsx(
                    "px-3 py-1 rounded-full text-xs font-medium transition-all border",
                    sortBy === key
                      ? "bg-blue-600/30 border-blue-500/50 text-blue-300"
                      : "bg-zinc-800/40 border-zinc-700/50 text-zinc-400 hover:text-zinc-200",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* Results table */}
            {sortedResults.length > 0 ? (
              <ResultsTable
                results={sortedResults}
                sortBy={sortBy}
                onSort={setSortBy}
                macroStatus={scanResult.macro_status ?? "unavailable"}
              />
            ) : (
              <div className="text-center py-16 text-zinc-600">
                <p className="text-lg">No qualifying tickers found</p>
                <p className="text-sm mt-2">
                  Try lowering Min IV/RV ratio, relaxing spread/liquidity filters, or increasing max tickers.
                </p>
              </div>
            )}

            {/* Errors panel */}
            <ErrorsPanel errors={scanResult.errors} total={scanResult.error_count} />

            {/* Column legend */}
            <div className="text-xs text-zinc-600 space-y-1 pt-2 border-t border-zinc-800/50">
              <p>
                <span className="text-zinc-500">Tenor pips: </span>
                iv7D · iv14D · iv30D · iv60D · iv90D — green = available, grey = missing
              </p>
              <p>
                <span className="text-zinc-500">Score: </span>
                composite rank (45% IV/RV + 35% regime + 10% tenors + 10% liquidity) · 0–100
              </p>
              <p>
                <span className="text-zinc-500">VRP: </span>
                IV30 − RV30 in annualised vol points
              </p>
            </div>
          </>
        )}

        {/* Empty state on first load */}
        {!scanResult && !scanning && !error && (
          <div className="text-center py-24 text-zinc-600">
            <p className="text-xl mb-2">S&amp;P 500 Vol Scanner</p>
            <p className="text-sm">Click <span className="text-blue-400">▶ Scan</span> to begin.</p>
          </div>
        )}
      </div>
    </div>
  );
}
