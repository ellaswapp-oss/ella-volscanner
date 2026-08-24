"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import {
  getRegime,
  getVolatility,
  getTermStructure,
  getTradeSignal,
  getHistory,
  getRuleBacktest,
} from "@/lib/api";
import type {
  RegimeResponse,
  VolatilityResponse,
  TermStructureResponse,
  TradeSignalResponse,
  HistoryResponse,
  RuleBacktestResult,
} from "@/lib/api";
import { TickerSelector } from "@/components/dashboard/TickerSelector";
import type { Ticker } from "@/components/dashboard/TickerSelector";
import { RegimeCard } from "@/components/dashboard/RegimeCard";
import {
  IVRVRatioCard,
  VRPCard,
  IVRankCard,
  ZScoreCard,
} from "@/components/dashboard/MetricCard";
import { TradeCard } from "@/components/dashboard/TradeCard";
import { TermStructureTable } from "@/components/dashboard/TermStructureTable";
import { IVRVBarChart } from "@/components/dashboard/IVRVBarChart";
import { ZScoreHistoryChart } from "@/components/dashboard/ZScoreHistoryChart";
import { HistoricalTable } from "@/components/dashboard/HistoricalTable";
import { BacktestResults } from "@/components/dashboard/BacktestResults";

// ---------------------------------------------------------------------------
// State shapes
// ---------------------------------------------------------------------------
interface AsyncState<T> {
  data:    T | null;
  loading: boolean;
  error:   string | null;
}

function idle<T>(): AsyncState<T> {
  return { data: null, loading: true, error: null };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function DashboardPage() {
  const [ticker, setTicker] = useState<Ticker>("SPY");

  // Market-wide — fetched once on mount
  const [regime, setRegime] = useState<AsyncState<RegimeResponse>>(idle());

  // Ticker-specific — refetch whenever ticker changes
  const [vol,  setVol]  = useState<AsyncState<VolatilityResponse>>(idle());
  const [ts,   setTS]   = useState<AsyncState<TermStructureResponse>>(idle());
  const [sig,  setSig]  = useState<AsyncState<TradeSignalResponse>>(idle());
  const [hist, setHist] = useState<AsyncState<HistoryResponse>>(idle());
  const [bt,   setBT]   = useState<AsyncState<RuleBacktestResult>>(idle());

  // Fetch market regime once
  useEffect(() => {
    getRegime()
      .then((d) => setRegime({ data: d, loading: false, error: null }))
      .catch((e) =>
        setRegime({ data: null, loading: false, error: String(e.message ?? e) })
      );
  }, []);

  // Fetch ticker-specific data whenever ticker changes
  const fetchTicker = useCallback(async (t: Ticker) => {
    setVol  ({ data: null, loading: true, error: null });
    setTS   ({ data: null, loading: true, error: null });
    setSig  ({ data: null, loading: true, error: null });
    setHist ({ data: null, loading: true, error: null });
    setBT   ({ data: null, loading: true, error: null });

    const [volResult, tsResult, sigResult, histResult, btResult] = await Promise.allSettled([
      getVolatility(t),
      getTermStructure(t),
      getTradeSignal(t),
      getHistory(t, 90),
      getRuleBacktest(t),
    ]);

    setVol(
      volResult.status === "fulfilled"
        ? { data: volResult.value, loading: false, error: null }
        : { data: null, loading: false, error: String((volResult.reason as Error).message) }
    );
    setTS(
      tsResult.status === "fulfilled"
        ? { data: tsResult.value, loading: false, error: null }
        : { data: null, loading: false, error: String((tsResult.reason as Error).message) }
    );
    setSig(
      sigResult.status === "fulfilled"
        ? { data: sigResult.value, loading: false, error: null }
        : { data: null, loading: false, error: String((sigResult.reason as Error).message) }
    );
    setHist(
      histResult.status === "fulfilled"
        ? { data: histResult.value, loading: false, error: null }
        : { data: null, loading: false, error: String((histResult.reason as Error).message) }
    );
    setBT(
      btResult.status === "fulfilled"
        ? { data: btResult.value, loading: false, error: null }
        : { data: null, loading: false, error: String((btResult.reason as Error).message) }
    );
  }, []);

  useEffect(() => {
    fetchTicker(ticker);
  }, [ticker, fetchTicker]);

  const tickerLoading = vol.loading || ts.loading || sig.loading || hist.loading || bt.loading;

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <main className="min-h-screen bg-surface px-4 py-5 md:px-6 md:py-7">

      {/* ------------------------------------------------------------------ */}
      {/* Header                                                               */}
      {/* ------------------------------------------------------------------ */}
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white">
            Vol Dashboard
          </h1>
          <p className="text-xs text-slate-500">
            {new Date().toLocaleDateString("en-US", {
              weekday: "short", month: "short", day: "numeric",
            })}
            {" · "}
            {vol.data ? (
              <span className="text-slate-500">
                {vol.data.provider === "real"
                  ? `live data · IV: ${vol.data.iv_source === "live_options_chain" ? "options chain" : "RV fallback"}`
                  : "synthetic data · mock provider"}
              </span>
            ) : (
              <span className="text-slate-600">loading…</span>
            )}
          </p>
        </div>

        <div className="flex items-center gap-3 flex-wrap">
          <TickerSelector
            selected={ticker}
            onChange={(t) => setTicker(t)}
            disabled={tickerLoading}
          />
          <Link
            href="/scanner/sp500"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-xs font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
          >
            Scanner →
          </Link>
          <Link
            href="/backtest"
            className="shrink-0 rounded-full bg-surface-3 border border-border px-3 py-1 text-xs font-medium text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
          >
            Backtest →
          </Link>
          {vol.data?.provider === "real" ? (
            <span className="shrink-0 rounded-full bg-green-500/10 border border-green-500/20 px-3 py-1 text-xs font-medium text-green-400">
              Live
            </span>
          ) : (
            <span className="shrink-0 rounded-full bg-slate-700/50 border border-border px-3 py-1 text-xs font-medium text-slate-500">
              Mock
            </span>
          )}
        </div>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Proxy IV Rank warning banner (live provider only)                   */}
      {/* ------------------------------------------------------------------ */}
      {vol.data?.iv_rank_is_proxy && !vol.loading && (
        <div className="mb-4 rounded-lg bg-amber-500/10 border border-amber-500/20 px-4 py-2.5 flex items-center gap-3">
          <span className="text-amber-400 shrink-0">⚠</span>
          <p className="text-[11px] text-amber-300 leading-snug">
            <span className="font-semibold">IV Rank is a proxy</span>
            {" — "}current IV30 ranked against 1-year realized vol history.
            True IV Rank requires historical IV data (ORATS or similar).
          </p>
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* IV30 unavailable banner — real chain, but 30d expiry out of range   */}
      {/* ------------------------------------------------------------------ */}
      {vol.data?.iv_surface_metadata?.required_tenor_available === false
        && vol.data?.iv_source === "live_options_chain"
        && !vol.loading && (
        <div className="mb-4 rounded-lg bg-red-500/10 border border-red-500/20 px-4 py-2.5 flex items-center gap-3">
          <span className="text-red-400 shrink-0">⚠</span>
          <p className="text-[11px] text-red-300 leading-snug">
            <span className="font-semibold">IV30 unavailable</span>
            {" — "}no 30-day expiration found within tolerance on the live options chain.
            Regime score uses a realized-vol–based estimate; treat signals with caution.
          </p>
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Partial term structure banner — iv30 ok but iv60/iv90 missing       */}
      {/* ------------------------------------------------------------------ */}
      {vol.data?.iv_surface_metadata?.required_tenor_available === true
        && vol.data?.iv_surface_metadata?.term_structure_quality === "partial"
        && !vol.loading && (
        <div className="mb-4 rounded-lg bg-blue-500/10 border border-blue-500/20 px-4 py-2.5 flex items-center gap-3">
          <span className="text-blue-400 shrink-0">ℹ</span>
          <p className="text-[11px] text-blue-300 leading-snug">
            <span className="font-semibold">Core IV30 signal is available</span>
            {" — "}but long-tenor term structure is incomplete.
            IV60 and IV90 require options expiring 46+ DTE; they are greyed out until that data is available.
          </p>
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Row 1: Regime (market-wide) + Trade Recommendation (ticker)        */}
      {/* ------------------------------------------------------------------ */}
      <div className="grid gap-4 grid-cols-1 lg:grid-cols-2 mb-4">
        <RegimeCard
          data={regime.data}
          loading={regime.loading}
          error={regime.error}
        />
        <TradeCard
          data={sig.data}
          loading={sig.loading}
          error={sig.error}
          ticker={ticker}
        />
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Row 2: Four metric cards                                            */}
      {/* ------------------------------------------------------------------ */}
      <div className="grid gap-4 grid-cols-2 lg:grid-cols-4 mb-4">
        <IVRVRatioCard data={vol.data} loading={vol.loading} error={vol.error} />
        <VRPCard       data={vol.data} loading={vol.loading} error={vol.error} />
        <IVRankCard    data={vol.data} loading={vol.loading} error={vol.error} />
        <ZScoreCard    data={vol.data} loading={vol.loading} error={vol.error} />
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Row 3: Term structure table (full width)                            */}
      {/* ------------------------------------------------------------------ */}
      <TermStructureTable
        data={ts.data}
        loading={ts.loading}
        error={ts.error}
        ticker={ticker}
      />

      {/* ------------------------------------------------------------------ */}
      {/* Row 4: IV vs RV bar chart + Z-score history                        */}
      {/* ------------------------------------------------------------------ */}
      <div className="grid gap-4 grid-cols-1 lg:grid-cols-2 mt-4">
        <IVRVBarChart
          data={vol.data}
          loading={vol.loading}
          error={vol.error}
          ticker={ticker}
        />
        <ZScoreHistoryChart
          ticker={ticker}
          currentZ={vol.data?.iv_rv_zscore ?? 0}
          loading={vol.loading}
          error={vol.error}
        />
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Row 5: Historical signal table (full width)                        */}
      {/* ------------------------------------------------------------------ */}
      <div className="mt-4">
        <HistoricalTable
          data={hist.data}
          loading={hist.loading}
          error={hist.error}
          ticker={ticker}
        />
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Row 6: Rule-based backtest results (full width)                    */}
      {/* ------------------------------------------------------------------ */}
      <div className="mt-4">
        <BacktestResults
          data={bt.data}
          loading={bt.loading}
          error={bt.error}
          ticker={ticker}
        />
      </div>

      <p className="mt-6 text-center text-[10px] text-slate-700">
        Phase 2 · live IV surface via Polygon options chain · IV Rank proxy · macro penalty scoring · ORATS IV history in Phase 3
      </p>
    </main>
  );
}
