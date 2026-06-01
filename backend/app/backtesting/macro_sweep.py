"""
backtesting/macro_sweep.py
--------------------------
Test the V2 rule (IV/RV > 1.30 + IVR > 50) against five orthogonal macro filters,
individually and combined, across all six tickers.

Base rule (always applied — V2 from the IV/RV sweep):
    Entry  when IV30/RV30 > 1.30  AND  IV Rank > 50
    Exit   after 10 trading days (non-overlapping per ticker)
    Side   SELL_PREMIUM

Macro filters (all sourced from macro_provider.get_macro_series):
  Filter 1 — VIX level         : only enter when VIX between 18 and 32
  Filter 2 — VIX term structure: only enter when VIX3M > VIX1M (contango)
  Filter 3 — Breadth           : SPY above 200-DMA  AND  >55 % of SPX above 50-DMA
  Filter 4 — Yield stress      : block when US 10Y yield has risen > 40 bps in 20 days
  Filter 5 — Oil shock         : block when WTI crude is up > 10 % in 20 days

Variations tested:
  1  V2 Baseline     — no macro overlay
  2  V2 + VIX Level
  3  V2 + VIX TS
  4  V2 + Breadth
  5  V2 + Yield Stress
  6  V2 + Oil Shock
  7  V2 + All Macro  — all five filters simultaneously

Output metrics (per variation, trades pooled across all tickers):
  n_trades, win_rate, premium_capture_rate, avg_return, median_return,
  max_drawdown, sharpe, sortino, profit_factor, expectancy,
  best_trade, worst_trade, by_ticker, by_vix_regime, equity_points

Premium capture rate = sum(IV_entry − RV_hold) / sum(IV_entry)
  — value-weighted by IV level at entry; distinct from avg_return.

by_vix_regime segments trades into four buckets by VIX at entry:
  calm (< 18), normal (18–25), elevated (25–32), spike (> 32)
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd

from app.data.mock_provider import TICKERS, get_price_history, get_iv_data
from app.data.macro_provider import get_macro_series
from app.backtesting.metrics import (
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    win_rate,
    profit_factor,
    expectancy,
)

_TDAYS       = 252
_HOLDING     = 10
_LOOKBACK    = 252
_IV_RV_MIN   = 1.30
_IV_RANK_MIN = 50.0


# ---------------------------------------------------------------------------
# Variation catalogue
# ---------------------------------------------------------------------------

MACRO_VARIATIONS: list[dict] = [
    {
        "id": 1,
        "name": "V2 Baseline",
        "short": "V2 Base",
        "description": (
            "IV/RV > 1.30 + IV Rank > 50. No macro overlay. "
            "Replicates the best variation from the IV/RV sweep."
        ),
        "filters": {},
    },
    {
        "id": 2,
        "name": "V2 + VIX 18–32",
        "short": "+ VIX Level",
        "description": (
            "Enter only when VIX is between 18 and 32. "
            "Below 18 = complacency; above 32 = panic / gap risk."
        ),
        "filters": {"vix_lo": 18.0, "vix_hi": 32.0},
    },
    {
        "id": 3,
        "name": "V2 + VIX Contango",
        "short": "+ VIX TS",
        "description": (
            "Enter only when VIX3M > VIX1M (term structure in contango). "
            "Inverted VIX term structure signals acute short-term stress."
        ),
        "filters": {"require_vix_contango": True},
    },
    {
        "id": 4,
        "name": "V2 + Breadth",
        "short": "+ Breadth",
        "description": (
            "Enter only when SPY is above its 200-DMA and "
            ">55 % of SPX stocks are above their 50-DMA. "
            "Avoids entering short-vol in structurally weak markets."
        ),
        "filters": {"require_spy_above_200dma": True, "breadth_min": 55.0},
    },
    {
        "id": 5,
        "name": "V2 + Yield Stress",
        "short": "+ Yield",
        "description": (
            "Block entry when the US 10Y yield has risen >40 bps "
            "over the prior 20 trading days. Rate shocks compress "
            "equity multiples and lift cross-asset vol."
        ),
        "filters": {"yield_rise_max_20d": 0.40},
    },
    {
        "id": 6,
        "name": "V2 + Oil Shock",
        "short": "+ Oil",
        "description": (
            "Block entry when WTI crude is up >10 % over the prior "
            "20 trading days. Oil shocks lift energy-sector vol and "
            "often spill into broad-market implied vol."
        ),
        "filters": {"crude_rise_max_20d": 0.10},
    },
    {
        "id": 7,
        "name": "V2 + All Macro",
        "short": "Full Rule",
        "description": (
            "All five macro filters simultaneously: "
            "VIX level, VIX contango, breadth, yield stress, oil shock."
        ),
        "filters": {
            "vix_lo": 18.0,
            "vix_hi": 32.0,
            "require_vix_contango": True,
            "require_spy_above_200dma": True,
            "breadth_min": 55.0,
            "yield_rise_max_20d": 0.40,
            "crude_rise_max_20d": 0.10,
        },
    },
]


# ---------------------------------------------------------------------------
# Rolling helpers
# ---------------------------------------------------------------------------

def _rolling_rv(prices: pd.Series, window: int) -> pd.Series:
    log_rets = np.log(prices / prices.shift(1))
    return (
        log_rets
        .rolling(window, min_periods=max(window // 4, 5))
        .std(ddof=1)
        * math.sqrt(_TDAYS)
        * 100
    )


def _rolling_iv_rank(iv30: pd.Series, lookback: int = _LOOKBACK) -> pd.Series:
    min_p = max(lookback // 4, 10)
    lo    = iv30.rolling(lookback, min_periods=min_p).min()
    hi    = iv30.rolling(lookback, min_periods=min_p).max()
    rng   = hi - lo
    raw   = np.where(rng > 0, (iv30 - lo) / rng * 100, 50.0)
    return pd.Series(raw, index=iv30.index)


def _vix_bucket(vix: float) -> str:
    if vix < 18:  return "calm"
    if vix < 25:  return "normal"
    if vix < 32:  return "elevated"
    return "spike"


# ---------------------------------------------------------------------------
# Premium capture rate
# ---------------------------------------------------------------------------

def _premium_capture_rate(trades: list[dict]) -> float | None:
    """
    Aggregate premium capture rate = sum(IV_entry - RV_hold) / sum(IV_entry).

    Value-weighted by IV level at entry.  Distinct from avg_return because
    higher-IV trades contribute more to the numerator and denominator.
    Positive = net premium captured; negative = net loss to realised vol.
    """
    if not trades:
        return None
    total_iv   = sum(t["iv_entry"] for t in trades)
    total_cap  = sum(t["iv_entry"] - t["rv_hold"] for t in trades)
    if total_iv <= 0:
        return None
    return round(total_cap / total_iv, 4)


# ---------------------------------------------------------------------------
# Trade simulator  (one ticker × one variation)
# ---------------------------------------------------------------------------

def _simulate(
    ticker: str,
    prices: pd.Series,
    iv_df: pd.DataFrame,
    rv30: pd.Series,
    ivr: pd.Series,
    macro: pd.DataFrame,
    filters: dict,
    holding: int = _HOLDING,
) -> list[dict]:
    """
    Non-overlapping SELL_PREMIUM trades for one ticker under one filter set.

    Base gate (always applied):
        IV30/RV30 > 1.30   AND   IV Rank > 50

    Macro gates (derived from ``filters`` dict keys):
        vix_lo / vix_hi            — VIX1M in [lo, hi]
        require_vix_contango       — VIX3M > VIX1M
        require_spy_above_200dma   — SPY close > 200-day SMA (min_periods=100)
        breadth_min                — breadth > threshold
        yield_rise_max_20d         — 20-day yield change <= threshold (pp)
        crude_rise_max_20d         — 20-day crude % change <= threshold

    P&L model: pnl = (IV_entry - RV_hold) / IV_entry
    """
    vix_lo        = filters.get("vix_lo",            None)
    vix_hi        = filters.get("vix_hi",            None)
    req_vix_c     = bool(filters.get("require_vix_contango",    False))
    req_spy_200   = bool(filters.get("require_spy_above_200dma", False))
    breadth_min   = filters.get("breadth_min",       None)
    yield_max     = filters.get("yield_rise_max_20d", None)
    crude_max     = filters.get("crude_rise_max_20d", None)

    # Align all series to the common calendar
    common_idx = prices.index.intersection(macro.index)
    prices_a   = prices.loc[common_idx]
    iv_df_a    = iv_df.loc[common_idx]
    rv30_a     = rv30.loc[common_idx]
    ivr_a      = ivr.loc[common_idx]
    macro_a    = macro.loc[common_idx]

    n      = len(common_idx)
    warmup = max(_LOOKBACK // 4, holding + 1, 30)

    # Pre-compute macro arrays for fast row access
    spy_arr   = macro_a["spy_price"].values
    spy_200   = pd.Series(spy_arr).rolling(200, min_periods=100).mean().values
    vix1m     = macro_a["vix1m"].values
    vix3m     = macro_a["vix3m"].values
    br_arr    = macro_a["breadth"].values
    y_arr     = macro_a["us10y"].values
    c_arr     = macro_a["crude_oil"].values
    y_lag20   = pd.Series(y_arr).shift(20).values
    c_lag20   = pd.Series(c_arr).shift(20).values

    trades: list[dict] = []
    last_exit = -1

    for i in range(warmup, n):
        if i <= last_exit:
            continue

        iv    = float(iv_df_a["iv30"].iloc[i])
        rv    = float(rv30_a.iloc[i])
        ivr_v = float(ivr_a.iloc[i])

        if not (math.isfinite(iv) and math.isfinite(rv) and math.isfinite(ivr_v)):
            continue
        if rv <= 0:
            continue

        ratio = iv / rv

        # ── V2 base gate ─────────────────────────────────────────────────
        if ratio <= _IV_RV_MIN:
            continue
        if ivr_v <= _IV_RANK_MIN:
            continue

        # ── Macro gates ──────────────────────────────────────────────────
        vix = vix1m[i]

        if vix_lo is not None and vix < vix_lo:
            continue
        if vix_hi is not None and vix > vix_hi:
            continue

        if req_vix_c and vix3m[i] <= vix1m[i]:
            continue

        if req_spy_200:
            dma = spy_200[i]
            if not math.isfinite(dma) or spy_arr[i] <= dma:
                continue

        if breadth_min is not None and br_arr[i] <= breadth_min:
            continue

        if yield_max is not None:
            y20 = y_lag20[i]
            if math.isfinite(y20):
                if (y_arr[i] - y20) > yield_max:
                    continue

        if crude_max is not None:
            c20 = c_lag20[i]
            if math.isfinite(c20) and c20 > 0:
                if (c_arr[i] - c20) / c20 > crude_max:
                    continue

        exit_i = i + holding
        if exit_i >= n:
            break

        hold_log = np.log(
            prices_a.values[i + 1 : exit_i + 1] /
            prices_a.values[i     : exit_i]
        )
        if len(hold_log) < 2:
            continue
        rv_hold = float(hold_log.std(ddof=1) * math.sqrt(_TDAYS) * 100)
        if not math.isfinite(rv_hold):
            continue

        pnl = (iv - rv_hold) / iv

        trades.append({
            "ticker":      ticker,
            "entry":       str(common_idx[i].date()),
            "exit":        str(common_idx[exit_i].date()),
            "iv_entry":    round(iv, 2),
            "rv_hold":     round(rv_hold, 2),
            "iv_rv_ratio": round(ratio, 3),
            "iv_rank":     round(ivr_v, 1),
            "vix_entry":   round(float(vix), 2),
            "vix_bucket":  _vix_bucket(float(vix)),
            "pnl":         round(pnl, 4),
            "is_winner":   bool(pnl > 0),
        })
        last_exit = exit_i

    return trades


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------

def _aggregate(all_trades: list[dict]) -> dict:
    """Build full metrics dict for one variation's pooled trade list."""
    _empty: dict = {
        "n_trades":             0,
        "win_rate":             None,
        "premium_capture_rate": None,
        "avg_return":           None,
        "median_return":        None,
        "max_drawdown":         None,
        "sharpe":               None,
        "sortino":              None,
        "profit_factor":        None,
        "expectancy":           None,
        "best_trade":           None,
        "worst_trade":          None,
        "by_ticker": {
            t: {"n_trades": 0, "win_rate": None, "avg_return": None, "median_return": None}
            for t in TICKERS
        },
        "by_vix_regime": {
            b: {"n": 0, "win_rate": None, "avg_pnl": None, "total": 0.0}
            for b in ("calm", "normal", "elevated", "spike")
        },
        "equity_points": [],
    }
    if not all_trades:
        return _empty

    pnls    = [t["pnl"] for t in all_trades]
    pnl_arr = np.array(pnls)

    def _safe(v: float) -> float | None:
        return round(float(v), 4) if math.isfinite(float(v)) else None

    # Sequential equity curve (sort by exit date, then ticker for ties)
    sorted_trades = sorted(all_trades, key=lambda t: (t["exit"], t["ticker"]))
    equity     = 1.0
    equity_pts = []
    for j, t in enumerate(sorted_trades):
        equity *= 1.0 + t["pnl"]
        equity_pts.append({"trade_n": j + 1, "equity": round(equity, 4)})

    eq_arr = np.array([1.0] + [p["equity"] for p in equity_pts])

    # Per-ticker breakdown
    by_ticker_pnls: dict[str, list[float]] = defaultdict(list)
    for t in all_trades:
        by_ticker_pnls[t["ticker"]].append(t["pnl"])

    by_ticker: dict[str, dict] = {}
    for tk in TICKERS:
        tp = by_ticker_pnls.get(tk, [])
        if tp:
            by_ticker[tk] = {
                "n_trades":      len(tp),
                "win_rate":      round(float(win_rate(tp)), 3),
                "avg_return":    round(float(np.mean(tp)), 4),
                "median_return": round(float(np.median(tp)), 4),
            }
        else:
            by_ticker[tk] = {
                "n_trades": 0, "win_rate": None,
                "avg_return": None, "median_return": None,
            }

    # Per-VIX-bucket breakdown
    by_vix_pnls: dict[str, list[float]] = defaultdict(list)
    for t in all_trades:
        by_vix_pnls[t["vix_bucket"]].append(t["pnl"])

    by_vix_regime: dict[str, dict] = {}
    for bkt in ("calm", "normal", "elevated", "spike"):
        bp = by_vix_pnls.get(bkt, [])
        if bp:
            by_vix_regime[bkt] = {
                "n":        len(bp),
                "win_rate": round(float(win_rate(bp)), 3),
                "avg_pnl":  round(float(np.mean(bp)), 4),
                "total":    round(float(sum(bp)), 4),
            }
        else:
            by_vix_regime[bkt] = {"n": 0, "win_rate": None, "avg_pnl": None, "total": 0.0}

    best  = max(all_trades, key=lambda t: t["pnl"])
    worst = min(all_trades, key=lambda t: t["pnl"])

    return {
        "n_trades":             len(all_trades),
        "win_rate":             round(float(win_rate(pnls)), 3),
        "premium_capture_rate": _premium_capture_rate(all_trades),
        "avg_return":           _safe(float(np.mean(pnl_arr))),
        "median_return":        _safe(float(np.median(pnl_arr))),
        "max_drawdown":         _safe(max_drawdown(eq_arr)),
        "sharpe":               _safe(sharpe_ratio(pnl_arr, periods_per_year=1)),
        "sortino":              _safe(sortino_ratio(pnl_arr, periods_per_year=1)),
        "profit_factor":        _safe(profit_factor(pnls)),
        "expectancy":           _safe(expectancy(pnls)),
        "best_trade":           best,
        "worst_trade":          worst,
        "by_ticker":            by_ticker,
        "by_vix_regime":        by_vix_regime,
        "equity_points":        equity_pts,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_macro_sweep(data_days: int = 400, require_real_iv: bool = False) -> dict:
    """
    Run all 7 macro-filter variations across all supported tickers.

    Preloads macro series and ticker data once; applies each variation's
    gate conditions in a single pass per variation.

    Returns
    -------
    dict
        variations        — list[dict], one entry per variation (7 total)
        best_variation_id — id with highest Sharpe ratio (>= 2 trades required)
        tickers           — list of tickers tested
        data_source       — "mock"
        macro_filters     — human-readable descriptions of each macro gate
    """
    # Load macro series (shared calendar anchor for all tickers)
    macro = get_macro_series(days=data_days)

    # Load all ticker data once
    ticker_data: dict[str, tuple] = {}
    filter_summaries: list[dict] = []
    for ticker in TICKERS:
        prices = get_price_history(ticker, days=data_days)
        iv_df  = get_iv_data(ticker, days=data_days, require_real_iv=require_real_iv)
        filter_summaries.append(iv_df.attrs.get("real_iv_filter", {"enabled": False,
                                                                    "rows_before_filter": len(iv_df),
                                                                    "rows_excluded": 0,
                                                                    "rows_remaining": len(iv_df)}))
        idx    = prices.index.intersection(iv_df.index)
        prices = prices.loc[idx]
        iv_df  = iv_df.loc[idx]
        rv30   = _rolling_rv(prices, 30)
        ivr    = _rolling_iv_rank(iv_df["iv30"], _LOOKBACK)
        ticker_data[ticker] = (prices, iv_df, rv30, ivr)

    # Aggregate filter summary across all tickers
    _before = sum(f.get("rows_before_filter", 0) for f in filter_summaries)
    _excl   = sum(f.get("rows_excluded",      0) for f in filter_summaries)
    _remain = sum(f.get("rows_remaining",     0) for f in filter_summaries)
    _starts = [f["usable_date_start"] for f in filter_summaries if f.get("usable_date_start")]
    _ends   = [f["usable_date_end"]   for f in filter_summaries if f.get("usable_date_end")]
    agg_filter: dict = {
        "enabled":            require_real_iv,
        "rows_before_filter": _before,
        "rows_excluded":      _excl,
        "rows_remaining":     _remain,
        "pct_remaining":      round(_remain / _before, 4) if _before > 0 else 0.0,
        "usable_date_start":  min(_starts) if _starts else None,
        "usable_date_end":    max(_ends)   if _ends   else None,
    }

    # Run each variation
    variation_results: list[dict] = []

    for var in MACRO_VARIATIONS:
        all_trades: list[dict] = []
        for ticker in TICKERS:
            prices, iv_df, rv30, ivr = ticker_data[ticker]
            trades = _simulate(ticker, prices, iv_df, rv30, ivr, macro, var["filters"])
            all_trades.extend(trades)

        agg = _aggregate(all_trades)

        n_var = agg["n_trades"]
        variation_results.append({
            "id":          var["id"],
            "name":        var["name"],
            "short":       var["short"],
            "description": var["description"],
            "filters":     var["filters"],
            # Aggregate metrics
            "n_trades":             n_var,
            "win_rate":             agg["win_rate"],
            "premium_capture_rate": agg["premium_capture_rate"],
            "avg_return":           agg["avg_return"],
            "median_return":        agg["median_return"],
            "max_drawdown":         agg["max_drawdown"],
            "sharpe":               agg["sharpe"],
            "sortino":              agg["sortino"],
            "profit_factor":        agg["profit_factor"],
            "expectancy":           agg["expectancy"],
            "best_trade":           agg["best_trade"],
            "worst_trade":          agg["worst_trade"],
            # Breakdowns
            "by_ticker":     agg["by_ticker"],
            "by_vix_regime": agg["by_vix_regime"],
            "equity_points": agg["equity_points"],
            # Real-IV filter
            "sample_size_warning": require_real_iv and 0 < n_var < 20,
        })

    # Pick best variation by Sharpe (>= 2 trades)
    best_id     = 1
    best_sharpe = -999.0
    for r in variation_results:
        sh = r.get("sharpe")
        if sh is not None and r["n_trades"] >= 2 and float(sh) > best_sharpe:
            best_sharpe = float(sh)
            best_id = r["id"]

    return {
        "variations":        variation_results,
        "best_variation_id": best_id,
        "tickers":           list(TICKERS),
        "data_source":       "mock",
        "real_iv_filter":    agg_filter,
        "macro_filters": {
            "vix_level":    "VIX between 18 and 32 at entry",
            "vix_ts":       "VIX3M > VIX1M (term structure in contango)",
            "breadth":      "SPY above 200-DMA and >55% of SPX above 50-DMA",
            "yield_stress": "US 10Y yield not risen >40 bps in prior 20 days",
            "oil_shock":    "WTI crude not up >10% in prior 20 days",
        },
    }
