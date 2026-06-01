"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { clsx } from "clsx";
import { getResearchReadiness } from "@/lib/api";
import type {
  ResearchReadinessResponse,
  TickerReadiness,
  ResearchModule,
  UniverseReadiness,
  ReadinessLevel,
  ModuleStatus,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TICKER_COLORS: Record<string, string> = {
  SPY:  "#60a5fa",   // blue
  QQQ:  "#34d399",   // emerald
  IWM:  "#fb923c",   // orange
  AAPL: "#a78bfa",   // violet
  NVDA: "#f87171",   // red
  TSLA: "#fbbf24",   // amber
};

const READINESS_META: Record<ReadinessLevel, {
  label: string; color: string; bg: string; border: string; desc: string;
}> = {
  NOT_READY: {
    label: "Not Ready",
    color: "text-red-400",
    bg:    "bg-red-500/10",
    border:"border-red-500/30",
    desc:  "< 63 valid IV30 rows",
  },
  EARLY: {
    label: "Early",
    color: "text-amber-400",
    bg:    "bg-amber-500/10",
    border:"border-amber-500/30",
    desc:  "63–99 valid IV30 rows",
  },
  USABLE: {
    label: "Usable",
    color: "text-blue-400",
    bg:    "bg-blue-500/10",
    border:"border-blue-500/30",
    desc:  "100–251 valid IV30 rows",
  },
  STRONG: {
    label: "Strong",
    color: "text-emerald-400",
    bg:    "bg-emerald-500/10",
    border:"border-emerald-500/30",
    desc:  "252+ valid IV30 rows",
  },
};

const MODULE_STATUS_META: Record<ModuleStatus, {
  label: string; color: string; dot: string;
}> = {
  AVAILABLE:  { label: "Available",  color: "text-emerald-400", dot: "bg-emerald-400" },
  PARTIAL:    { label: "Partial",    color: "text-amber-400",   dot: "bg-amber-400"   },
  NOT_READY:  { label: "Not Ready",  color: "text-red-400/70",  dot: "bg-red-500/60"  },
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pbar(current: number, target: number): number {
  if (target === 0) return 100;
  return Math.min(100, Math.round((current / target) * 100));
}

function pluralDays(n: number, unit: "trading" | "calendar"): string {
  if (n === 0) return "met ✓";
  return `${n.toLocaleString()} ${unit} day${n !== 1 ? "s" : ""}`;
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded bg-surface-3", className)} />;
}

function PageSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-10 w-80" />
      <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-56" />
        ))}
      </div>
      <Skeleton className="h-48 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// ReadinessBadge
// ---------------------------------------------------------------------------

function ReadinessBadge({ level }: { level: ReadinessLevel }) {
  const m = READINESS_META[level];
  return (
    <span className={clsx(
      "inline-flex items-center rounded-full border px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-widest",
      m.color, m.bg, m.border
    )}>
      {m.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Progress bar
// ---------------------------------------------------------------------------

function MilestoneBar({
  label,
  current,
  target,
  tradingDaysLeft,
  calDaysLeft,
  color,
}: {
  label:           string;
  current:         number;
  target:          number;
  tradingDaysLeft: number;
  calDaysLeft:     number;
  color:           string;
}) {
  const pct     = pbar(current, target);
  const met     = current >= target;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-slate-500 font-medium">{label}</span>
        <span className={met ? "text-emerald-400 font-semibold" : "text-slate-400"}>
          {met ? "✓ met" : `${current} / ${target}`}
        </span>
      </div>
      {/* Track */}
      <div className="h-1.5 w-full rounded-full bg-slate-700/60 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%`, background: met ? "#34d399" : color }}
        />
      </div>
      {!met && (
        <p className="text-[9px] text-slate-600">
          {pluralDays(tradingDaysLeft, "trading")} · ~{pluralDays(calDaysLeft, "calendar")} remaining
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-ticker readiness card
// ---------------------------------------------------------------------------

function TickerCard({ t, thresholds }: {
  t:          TickerReadiness;
  thresholds: ResearchReadinessResponse["thresholds"];
}) {
  const color  = TICKER_COLORS[t.ticker] ?? "#94a3b8";
  const meta   = READINESS_META[t.readiness];

  return (
    <div className={clsx(
      "rounded-xl border bg-surface-2 p-4 flex flex-col gap-3",
      meta.border
    )}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: color }} />
          <span className="text-base font-bold text-white">{t.ticker}</span>
        </div>
        <ReadinessBadge level={t.readiness} />
      </div>

      {/* Key numbers */}
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <div className="rounded-lg bg-surface-3/60 px-2.5 py-2">
          <p className="text-slate-600 text-[9px] uppercase tracking-widest">Real rows</p>
          <p className="text-white font-bold text-sm">{t.real_rows.toLocaleString()}</p>
        </div>
        <div className="rounded-lg bg-surface-3/60 px-2.5 py-2">
          <p className="text-slate-600 text-[9px] uppercase tracking-widest">Valid IV30</p>
          <p className="font-bold text-sm" style={{ color }}>{t.valid_iv30_rows.toLocaleString()}</p>
        </div>
        <div className="rounded-lg bg-surface-3/60 px-2.5 py-2 col-span-2">
          <p className="text-slate-600 text-[9px] uppercase tracking-widest mb-0.5">Real-IV window</p>
          {t.first_real_date ? (
            <p className="text-slate-300 font-mono text-[10px]">
              {t.first_real_date} → {t.latest_real_date}
              <span className="text-slate-500 ml-1">({t.usable_trading_days} trading days)</span>
            </p>
          ) : (
            <p className="text-slate-600 text-[10px] italic">No real data yet</p>
          )}
        </div>
      </div>

      {/* Milestone progress bars */}
      <div className="space-y-2.5 pt-1">
        <MilestoneBar
          label="Warmup (63)"
          current={t.valid_iv30_rows}
          target={thresholds.warmup_63}
          tradingDaysLeft={t.days_until_warmup_63}
          calDaysLeft={t.cal_days_until_warmup_63}
          color={color}
        />
        <MilestoneBar
          label="Usable (100)"
          current={t.valid_iv30_rows}
          target={thresholds.usable_100}
          tradingDaysLeft={t.days_until_100}
          calDaysLeft={t.cal_days_until_100}
          color={color}
        />
        <MilestoneBar
          label="Strong (252)"
          current={t.valid_iv30_rows}
          target={thresholds.strong_252}
          tradingDaysLeft={t.days_until_252}
          calDaysLeft={t.cal_days_until_252}
          color={color}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Universe summary strip
// ---------------------------------------------------------------------------

function UniverseStrip({ universes }: {
  universes: ResearchReadinessResponse["universes"];
}) {
  const entries: [string, UniverseReadiness][] = [
    ["etf",         universes.etf],
    ["single_name", universes.single_name],
    ["all",         universes.all],
  ];

  return (
    <div className="grid gap-3 grid-cols-1 sm:grid-cols-3">
      {entries.map(([key, u]) => {
        const meta = READINESS_META[u.readiness];
        return (
          <div
            key={key}
            className={clsx(
              "rounded-xl border px-4 py-3 flex flex-col gap-1",
              meta.border, meta.bg
            )}
          >
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">
              {u.label}
            </p>
            <div className="flex items-end justify-between mt-1">
              <div>
                <span className="text-2xl font-bold text-white">
                  {u.min_valid_iv30_rows}
                </span>
                <span className="text-[10px] text-slate-500 ml-1">valid IV30 (min)</span>
              </div>
              <ReadinessBadge level={u.readiness} />
            </div>
            <p className="text-[10px] text-slate-500 mt-0.5">
              {u.tickers.join(" · ")}
            </p>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Module trust table
// ---------------------------------------------------------------------------

function ModuleTrustTable({ modules }: { modules: ResearchModule[] }) {
  return (
    <div className="rounded-xl border border-border bg-surface-2 overflow-hidden">
      <div className="px-4 pt-4 pb-2 border-b border-border">
        <h2 className="text-sm font-bold text-slate-100">Research Module Trust</h2>
        <p className="text-[10px] text-slate-500 mt-0.5">
          Which modules can be trusted given current real-IV coverage.
          Status is based on the minimum valid IV30 rows across the binding ticker universe.
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[540px]">
          <thead>
            <tr className="border-b border-border">
              <th className="py-2.5 pl-4 pr-3 text-left text-[9px] font-semibold uppercase tracking-widest text-slate-600 w-48">
                Module
              </th>
              <th className="py-2.5 px-3 text-left text-[9px] font-semibold uppercase tracking-widest text-slate-600">
                Description
              </th>
              <th className="py-2.5 px-3 text-center text-[9px] font-semibold uppercase tracking-widest text-slate-600 w-24">
                Status
              </th>
              <th className="py-2.5 px-3 text-center text-[9px] font-semibold uppercase tracking-widest text-slate-600 w-20">
                Required
              </th>
              <th className="py-2.5 px-3 text-center text-[9px] font-semibold uppercase tracking-widest text-slate-600 w-20">
                Current
              </th>
              <th className="py-2.5 pl-3 pr-4 text-left text-[9px] font-semibold uppercase tracking-widest text-slate-600">
                Notes
              </th>
            </tr>
          </thead>
          <tbody>
            {modules.map((m, i) => {
              const sm     = MODULE_STATUS_META[m.status];
              const pct    = pbar(m.rows_current, m.rows_required);
              const isAvail = m.status === "AVAILABLE";

              return (
                <tr
                  key={m.name}
                  className={clsx(
                    "border-b border-border/40",
                    i % 2 === 0 ? "bg-transparent" : "bg-surface-3/10"
                  )}
                >
                  {/* Name */}
                  <td className="py-3 pl-4 pr-3">
                    <div className="flex items-center gap-2">
                      <span className={clsx("w-2 h-2 rounded-full shrink-0", sm.dot)} />
                      <span className="text-[11px] font-semibold text-slate-200">
                        {m.name}
                      </span>
                    </div>
                  </td>

                  {/* Description */}
                  <td className="py-3 px-3 text-[10px] text-slate-500">
                    {m.description}
                  </td>

                  {/* Status */}
                  <td className="py-3 px-3 text-center">
                    <span className={clsx(
                      "inline-flex items-center rounded-full border px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest",
                      m.status === "AVAILABLE"  && "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
                      m.status === "PARTIAL"    && "border-amber-500/30 bg-amber-500/10 text-amber-400",
                      m.status === "NOT_READY"  && "border-red-500/20 bg-red-500/8 text-red-400/70"
                    )}>
                      {sm.label}
                    </span>
                  </td>

                  {/* Required */}
                  <td className="py-3 px-3 text-center text-[11px] font-mono text-slate-400">
                    {m.rows_required === 0 ? "—" : m.rows_required.toLocaleString()}
                  </td>

                  {/* Current + mini bar */}
                  <td className="py-3 px-3">
                    <div className="flex flex-col items-center gap-1">
                      <span className={clsx(
                        "text-[11px] font-mono font-semibold",
                        isAvail ? "text-emerald-400" : "text-slate-300"
                      )}>
                        {m.rows_current.toLocaleString()}
                      </span>
                      {m.rows_required > 0 && (
                        <div className="w-14 h-1 rounded-full bg-slate-700/60 overflow-hidden">
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${pct}%`,
                              background: isAvail ? "#34d399" : "#f59e0b",
                            }}
                          />
                        </div>
                      )}
                    </div>
                  </td>

                  {/* Notes */}
                  <td className="py-3 pl-3 pr-4 text-[10px] text-slate-500 max-w-xs">
                    {m.notes}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Readiness legend
// ---------------------------------------------------------------------------

function ReadinessLegend({ thresholds }: {
  thresholds: ResearchReadinessResponse["thresholds"];
}) {
  const levels: [ReadinessLevel, string][] = [
    ["NOT_READY", `< ${thresholds.warmup_63} rows — below backtest warmup threshold`],
    ["EARLY",     `${thresholds.warmup_63}–${thresholds.usable_100 - 1} rows — minimal viable for simple backtests`],
    ["USABLE",    `${thresholds.usable_100}–${thresholds.strong_252 - 1} rows — solid for single-signal analysis`],
    ["STRONG",    `${thresholds.strong_252}+ rows — full year, reliable for optimisation & walk-forward`],
  ];

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-3">
        Readiness Level Definitions
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {levels.map(([level, desc]) => {
          const meta = READINESS_META[level];
          return (
            <div key={level} className="flex items-start gap-2.5">
              <span className={clsx(
                "mt-0.5 shrink-0 rounded-full border px-2 py-0.5 text-[9px] font-bold uppercase tracking-widest",
                meta.color, meta.bg, meta.border
              )}>
                {meta.label}
              </span>
              <span className="text-[10px] text-slate-500">{desc}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Countdown summary ribbon
// ---------------------------------------------------------------------------

function CountdownRibbon({ data }: { data: ResearchReadinessResponse }) {
  // Find the binding ticker for each ETF milestone (the one with the most remaining)
  const etfTickers = data.tickers.filter(
    (t) => data.universes.etf.tickers.includes(t.ticker)
  );

  const maxUntil63  = Math.max(...etfTickers.map((t) => t.days_until_warmup_63));
  const maxUntil252 = Math.max(...etfTickers.map((t) => t.days_until_252));
  const calUntil63  = Math.ceil(maxUntil63 * 7 / 5);
  const calUntil252 = Math.ceil(maxUntil252 * 7 / 5);

  type Stat = { label: string; value: string; sub: string; met: boolean };
  const stats: Stat[] = [
    {
      label: "IV/RV Backtest",
      value: maxUntil63 === 0 ? "Ready" : `${maxUntil63} trading days`,
      sub:   maxUntil63 === 0 ? "ETF universe" : `≈ ${calUntil63} calendar days`,
      met:   maxUntil63 === 0,
    },
    {
      label: "Optimisation / Walk-Forward",
      value: maxUntil252 === 0 ? "Ready" : `${maxUntil252} trading days`,
      sub:   maxUntil252 === 0 ? "ETF universe" : `≈ ${calUntil252} calendar days`,
      met:   maxUntil252 === 0,
    },
  ];

  return (
    <div className="grid gap-3 grid-cols-1 sm:grid-cols-2">
      {stats.map((s) => (
        <div
          key={s.label}
          className={clsx(
            "rounded-xl border px-4 py-3",
            s.met
              ? "border-emerald-500/30 bg-emerald-500/8"
              : "border-border bg-surface-2"
          )}
        >
          <p className="text-[9px] font-semibold uppercase tracking-widest text-slate-500 mb-1">
            {s.label}
          </p>
          <p className={clsx(
            "text-lg font-bold",
            s.met ? "text-emerald-400" : "text-white"
          )}>
            {s.value}
          </p>
          <p className="text-[10px] text-slate-500">{s.sub}</p>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ResearchReadinessPage() {
  const [data, setData]       = useState<ResearchReadinessResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    getResearchReadiness()
      .then((d) => setData(d))
      .catch((e) => setError(String(e?.message ?? e)))
      .finally(() => setLoading(false));
  }, []);

  return (
    <main className="min-h-screen bg-surface px-4 py-5 md:px-6 md:py-7">

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <Link href="/" className="text-[10px] text-slate-500 hover:text-slate-300 transition-colors">
              ← Dashboard
            </Link>
          </div>
          <h1 className="text-xl font-bold tracking-tight text-white">
            Real-IV Research Readiness
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            {data
              ? `As of ${data.as_of} · ${data.tickers.length} tickers · milestones at ${data.thresholds.warmup_63} / ${data.thresholds.usable_100} / ${data.thresholds.strong_252} valid IV30 rows`
              : "Loading real-IV coverage metrics…"}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap shrink-0">
          <Link
            href="/backtest"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-[10px] font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
          >
            Backtest →
          </Link>
          <span className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-[10px] font-mono text-slate-500">
            Real IV only
          </span>
        </div>
      </div>

      {/* ── States ──────────────────────────────────────────────────────── */}
      {loading && <PageSkeleton />}

      {error && (
        <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-4">
          <p className="text-xs font-semibold text-red-400 mb-1">Failed to load readiness data</p>
          <p className="font-mono text-[10px] text-red-300/60">{error}</p>
        </div>
      )}

      {data && (
        <div className="space-y-5">

          {/* 1 — Countdown to key milestones */}
          <CountdownRibbon data={data} />

          {/* 2 — Universe summaries */}
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-2">
              Universe Readiness — Binding by Minimum Ticker
            </p>
            <UniverseStrip universes={data.universes} />
          </div>

          {/* 3 — Per-ticker cards */}
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mb-2">
              Per-Ticker Coverage &amp; Milestone Progress
            </p>
            <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
              {data.tickers.map((t) => (
                <TickerCard key={t.ticker} t={t} thresholds={data.thresholds} />
              ))}
            </div>
          </div>

          {/* 4 — Module trust table */}
          <ModuleTrustTable modules={data.modules} />

          {/* 5 — Readiness legend */}
          <ReadinessLegend thresholds={data.thresholds} />

          {/* Footer */}
          <p className="text-center text-[10px] text-slate-700 pt-2">
            Counts only rows with <code className="text-slate-600">iv_data_quality = &quot;real&quot;</code> and a valid (non-NaN) <code className="text-slate-600">iv30</code> value.
            Accumulates ~1 row per trading day from the daily live-snapshot backfiller.
          </p>
        </div>
      )}
    </main>
  );
}
