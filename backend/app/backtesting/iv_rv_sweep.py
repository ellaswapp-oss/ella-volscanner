"""
backtesting/iv_rv_sweep.py
--------------------------
Test the IV/RV > 1.30 rule across 5 filter variations and all 6 tickers.

Base rule: sell premium when IV30/RV30 > 1.30, hold 10 trading days.
A winning trade = realized vol over the next 10 days < implied vol at entry.

Variations
----------
1  IV/RV > 1.30 only              — base rule, no secondary filters
2  IV/RV > 1.30 + IVR > 50       — IV must be above 1-year median
3  IV/RV > 1.30 + regime 40–75   — avoid extremes (panic / complacency)
4  IV/RV > 1.30 + TS not inverted — only enter in contango/flat term structure
5  IV/RV > 1.30 + IVR>50 + regime 40–75 — all three filters combined

All runs: SELL_PREMIUM direction, 10-day hold, no overlapping positions per ticker.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd

from app.data.mock_provider import TICKERS, get_price_history, get_iv_data
from app.backtesting.metrics import (
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    win_rate,
    profit_factor,
    expectancy,
)

_TDAYS     = 252
_HOLDING   = 10
_LOOKBACK  = 252
_DATA_DAYS = 400

# ---------------------------------------------------------------------------
# Variation catalogue
# ---------------------------------------------------------------------------

VARIATIONS: list[dict] = [
    {
        "id": 1,
        "name": "IV/RV > 1.30 only",
        "short": "Base",
        "description": "Sell premium whenever IV30 ≥ 30% above RV30. No secondary filters.",
        "rules": {"iv_rv_min": 1.30},
    },
    {
        "id": 2,
        "name": "IV/RV > 1.30  ·  IVR > 50",
        "short": "+ IV Rank",
        "description": "Base rule + IV Rank > 50 — IV must be above its 1-year median.",
        "rules": {"iv_rv_min": 1.30, "iv_rank_min": 50.0},
    },
    {
        "id": 3,
        "name": "IV/RV > 1.30  ·  Regime 40–75",
        "short": "+ Regime",
        "description": "Base rule + regime score 40–75 — avoid panic spikes and complacency.",
        "rules": {"iv_rv_min": 1.30, "regime_lo": 40.0, "regime_hi": 75.0},
    },
    {
        "id": 4,
        "name": "IV/RV > 1.30  ·  TS not inverted",
        "short": "+ Contango",
        "description": "Base rule + term structure not inverted (IV30 ≥ IV7). Skip stress inversions.",
        "rules": {"iv_rv_min": 1.30, "require_contango": True},
    },
    {
        "id": 5,
        "name": "IV/RV > 1.30  ·  IVR > 50  ·  Regime 40–75",
        "short": "Full rule",
        "description": "All three secondary filters — tightest entry criteria, highest selectivity.",
        "rules": {"iv_rv_min": 1.30, "iv_rank_min": 50.0, "regime_lo": 40.0, "regime_hi": 75.0},
    },
]

# ---------------------------------------------------------------------------
# Rolling helpers  (kept local — no circular import with engine.py)
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


def _regime_score(iv_rank: float, iv: float, rv: float) -> float:
    vrp     = (iv - rv) / rv  if rv > 0 else 0.0
    ratio   = iv / rv          if rv > 0 else 1.0
    rank_s  = float(np.clip(iv_rank, 0, 100))
    vrp_s   = float(np.clip((vrp  + 0.5) / 1.0 * 100, 0, 100))
    ratio_s = float(np.clip((ratio - 0.5) / 1.5 * 100, 0, 100))
    return round(0.35 * rank_s + 0.25 * vrp_s + 0.25 * ratio_s + 0.15 * 50.0, 2)


def _regime_label(score: float) -> str:
    if score < 30: return "buy_premium"
    if score < 50: return "neutral"
    if score < 70: return "sell_selective"
    return "sell_aggressive"


# ---------------------------------------------------------------------------
# Trade simulator  (one ticker × one variation)
# ---------------------------------------------------------------------------

def _simulate(
    ticker: str,
    prices: pd.Series,
    iv_df: pd.DataFrame,
    rv30: pd.Series,
    ivr: pd.Series,
    rules: dict,
    holding: int = _HOLDING,
) -> list[dict]:
    """
    Non-overlapping SELL_PREMIUM trades for one ticker under one rule set.

    P&L model: pnl = (IV_entry − RV_hold) / IV_entry
        positive → realised vol came in below implied (seller wins)
        negative → realised vol exceeded implied     (seller loses)
    """
    iv_rv_min       = float(rules.get("iv_rv_min", 1.30))
    iv_rank_min     = rules.get("iv_rank_min", None)
    regime_lo       = rules.get("regime_lo", None)
    regime_hi       = rules.get("regime_hi", None)
    require_contango = bool(rules.get("require_contango", False))

    idx       = prices.index
    n         = len(idx)
    warmup    = max(_LOOKBACK // 4, holding + 1, 30)
    trades: list[dict] = []
    last_exit = -1

    for i in range(warmup, n):
        if i <= last_exit:
            continue

        iv   = float(iv_df["iv30"].iloc[i])
        iv7  = float(iv_df["iv7"].iloc[i])
        rv   = float(rv30.iloc[i])
        ivr_v = float(ivr.iloc[i])

        if not (math.isfinite(iv) and math.isfinite(rv) and math.isfinite(ivr_v)):
            continue
        if rv <= 0:
            continue

        ratio  = iv / rv
        regime = _regime_score(ivr_v, iv, rv)

        # ── Entry gate ─────────────────────────────────────────────────
        if ratio <= iv_rv_min:
            continue
        if iv_rank_min is not None and ivr_v <= iv_rank_min:
            continue
        if regime_lo is not None and regime < regime_lo:
            continue
        if regime_hi is not None and regime > regime_hi:
            continue
        if require_contango and iv < iv7:          # inverted: front above atm
            continue

        exit_i = i + holding
        if exit_i >= n:
            break

        # Realised vol computed over the actual holding window
        hold_log = np.log(
            prices.values[i + 1 : exit_i + 1] /
            prices.values[i     : exit_i]
        )
        if len(hold_log) < 2:
            continue
        rv_hold = float(hold_log.std(ddof=1) * math.sqrt(_TDAYS) * 100)
        if not math.isfinite(rv_hold):
            continue

        pnl = (iv - rv_hold) / iv   # SELL_PREMIUM — capture IV premium

        trades.append({
            "ticker":       ticker,
            "entry":        str(idx[i].date()),
            "exit":         str(idx[exit_i].date()),
            "iv_entry":     round(iv, 2),
            "rv_hold":      round(rv_hold, 2),
            "iv_rv_ratio":  round(ratio, 3),
            "iv_rank":      round(ivr_v, 1),
            "regime_score": round(regime, 1),
            "regime_label": _regime_label(regime),
            "pnl":          round(pnl, 4),
            "is_winner":    bool(pnl > 0),
        })
        last_exit = exit_i

    return trades


# ---------------------------------------------------------------------------
# Aggregate metrics  (all trades pooled from all tickers)
# ---------------------------------------------------------------------------

def _aggregate(all_trades: list[dict]) -> dict:
    """Build the full metrics dict for one variation's trade pool."""
    _empty = {
        "n_trades":      0,
        "win_rate":      None,
        "avg_return":    None,
        "median_return": None,
        "max_drawdown":  None,
        "sharpe":        None,
        "sortino":       None,
        "profit_factor": None,
        "expectancy":    None,
        "best_trade":    None,
        "worst_trade":   None,
        "by_ticker":     {t: {"n_trades": 0, "win_rate": None,
                              "avg_return": None, "median_return": None}
                          for t in TICKERS},
        "by_regime":     {r: {"n": 0, "win_rate": None,
                              "avg_pnl": None, "total": 0.0}
                          for r in ("buy_premium", "neutral",
                                    "sell_selective", "sell_aggressive")},
        "equity_points": [],
    }
    if not all_trades:
        return _empty

    pnls    = [t["pnl"] for t in all_trades]
    pnl_arr = np.array(pnls)

    def _safe(v: float) -> float | None:
        return round(float(v), 4) if math.isfinite(float(v)) else None

    # Sequential equity curve (sorted by exit date across all tickers)
    sorted_trades = sorted(all_trades, key=lambda t: (t["exit"], t["ticker"]))
    equity = 1.0
    equity_pts = []
    for j, t in enumerate(sorted_trades):
        equity *= (1.0 + t["pnl"])
        equity_pts.append({"trade_n": j + 1, "equity": round(equity, 4)})

    eq_arr = np.array([1.0] + [p["equity"] for p in equity_pts])

    # Per-ticker breakdown
    by_ticker_pnls: dict[str, list[float]] = defaultdict(list)
    for t in all_trades:
        by_ticker_pnls[t["ticker"]].append(t["pnl"])

    by_ticker = {}
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
            by_ticker[tk] = {"n_trades": 0, "win_rate": None,
                             "avg_return": None, "median_return": None}

    # Per-regime breakdown
    by_regime_pnls: dict[str, list[float]] = defaultdict(list)
    for t in all_trades:
        by_regime_pnls[t["regime_label"]].append(t["pnl"])

    by_regime = {}
    for label in ("buy_premium", "neutral", "sell_selective", "sell_aggressive"):
        rp = by_regime_pnls.get(label, [])
        if rp:
            by_regime[label] = {
                "n":        len(rp),
                "win_rate": round(float(win_rate(rp)), 3),
                "avg_pnl":  round(float(np.mean(rp)), 4),
                "total":    round(float(sum(rp)), 4),
            }
        else:
            by_regime[label] = {"n": 0, "win_rate": None,
                                 "avg_pnl": None, "total": 0.0}

    best  = max(all_trades, key=lambda t: t["pnl"])
    worst = min(all_trades, key=lambda t: t["pnl"])

    return {
        "n_trades":      len(all_trades),
        "win_rate":      round(float(win_rate(pnls)), 3),
        "avg_return":    _safe(float(np.mean(pnl_arr))),
        "median_return": _safe(float(np.median(pnl_arr))),
        "max_drawdown":  _safe(max_drawdown(eq_arr)),
        "sharpe":        _safe(sharpe_ratio(pnl_arr, periods_per_year=1)),
        "sortino":       _safe(sortino_ratio(pnl_arr, periods_per_year=1)),
        "profit_factor": _safe(profit_factor(pnls)),
        "expectancy":    _safe(expectancy(pnls)),
        "best_trade":    best,
        "worst_trade":   worst,
        "by_ticker":     by_ticker,
        "by_regime":     by_regime,
        "equity_points": equity_pts,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_iv_rv_sweep(data_days: int = _DATA_DAYS, require_real_iv: bool = False) -> dict:
    """
    Run all 5 filter variations across all supported tickers.

    Preloads each ticker's price + IV data once, then applies each variation's
    filter conditions — efficient single-pass data loading.

    Returns
    -------
    dict
        variations        — list[dict], one entry per variation
        best_variation_id — id of variation with highest Sharpe (≥ 2 trades)
        tickers           — list of tickers tested
        data_source       — "mock"
        real_iv_filter    — aggregated filter summary across all tickers
    """
    # ── Load all ticker data once (shared across all variations) ──────────
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
    _before  = sum(f.get("rows_before_filter", 0) for f in filter_summaries)
    _excl    = sum(f.get("rows_excluded",      0) for f in filter_summaries)
    _remain  = sum(f.get("rows_remaining",     0) for f in filter_summaries)
    _starts  = [f["usable_date_start"] for f in filter_summaries if f.get("usable_date_start")]
    _ends    = [f["usable_date_end"]   for f in filter_summaries if f.get("usable_date_end")]
    agg_filter: dict = {
        "enabled":            require_real_iv,
        "rows_before_filter": _before,
        "rows_excluded":      _excl,
        "rows_remaining":     _remain,
        "pct_remaining":      round(_remain / _before, 4) if _before > 0 else 0.0,
        "usable_date_start":  min(_starts) if _starts else None,
        "usable_date_end":    max(_ends)   if _ends   else None,
    }

    # ── Simulate each variation ───────────────────────────────────────────
    variation_results: list[dict] = []

    for var in VARIATIONS:
        all_trades: list[dict] = []
        per_ticker_trades: dict[str, list[dict]] = {}

        for ticker in TICKERS:
            prices, iv_df, rv30, ivr = ticker_data[ticker]
            trades = _simulate(ticker, prices, iv_df, rv30, ivr, var["rules"])
            all_trades.extend(trades)
            per_ticker_trades[ticker] = trades

        agg = _aggregate(all_trades)

        n_var = agg["n_trades"]
        variation_results.append({
            "id":          var["id"],
            "name":        var["name"],
            "short":       var["short"],
            "description": var["description"],
            "rules":       var["rules"],
            # ── Flat aggregate metrics ─────────────────────────────────
            "n_trades":      n_var,
            "win_rate":      agg["win_rate"],
            "avg_return":    agg["avg_return"],
            "median_return": agg["median_return"],
            "max_drawdown":  agg["max_drawdown"],
            "sharpe":        agg["sharpe"],
            "sortino":       agg["sortino"],
            "profit_factor": agg["profit_factor"],
            "expectancy":    agg["expectancy"],
            "best_trade":    agg["best_trade"],
            "worst_trade":   agg["worst_trade"],
            # ── Breakdowns ────────────────────────────────────────────
            "by_ticker":     agg["by_ticker"],
            "by_regime":     agg["by_regime"],
            "equity_points": agg["equity_points"],
            # ── Real-IV filter ─────────────────────────────────────────
            "sample_size_warning": require_real_iv and 0 < n_var < 20,
        })

    # ── Pick best variation (highest Sharpe, ≥ 2 trades) ─────────────────
    best_id    = 1
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
    }
