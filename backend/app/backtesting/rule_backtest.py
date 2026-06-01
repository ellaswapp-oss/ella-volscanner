"""
backtesting/rule_backtest.py
-----------------------------
Rule-based backtest with explicit 3-condition entry filter.

Entry rule
----------
All three conditions must hold simultaneously:

    1. IV/RV ratio  > 1.30   — IV commands a ≥30% premium over recent realised vol
    2. IV Rank      > 50     — IV is above its 1-year median (elevated)
    3. Regime score in [40, 75] — moderate-to-high sell pressure (avoid extremes)

Direction: SELL_PREMIUM (short volatility).

Exit: 10 trading days after entry (non-overlapping positions).

P&L model
---------
    pnl = (IV_entry − RV_hold) / IV_entry

    +ve → realised vol came in below implied  (seller wins)
    −ve → realised vol exceeded implied       (seller loses)

Outputs
-------
    n_trades, win_rate, avg_return, max_drawdown, sharpe, sortino,
    best_trade, worst_trade, performance_by_regime, equity_curve, trades
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.backtesting.metrics import (
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    win_rate,
    expectancy,
    profit_factor,
)
from app.data.mock_provider import get_price_history, get_iv_data, get_iv_quality_summary

_TDAYS    = 252
_HOLDING  = 10     # trading days

# ---------------------------------------------------------------------------
# Entry thresholds (matches user spec; can be overridden via query params)
# ---------------------------------------------------------------------------

DEFAULT_IV_RV_MIN    = 1.30
DEFAULT_IV_RANK_MIN  = 50.0
DEFAULT_REGIME_LO    = 40.0
DEFAULT_REGIME_HI    = 75.0


# ---------------------------------------------------------------------------
# Helpers (kept local — avoid circular import with engine.py)
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


def _rolling_iv_rank(iv30: pd.Series, lookback: int = 252) -> pd.Series:
    min_p = max(lookback // 4, 10)
    lo    = iv30.rolling(lookback, min_periods=min_p).min()
    hi    = iv30.rolling(lookback, min_periods=min_p).max()
    rng   = hi - lo
    raw   = np.where(rng > 0, (iv30 - lo) / rng * 100, 50.0)
    return pd.Series(raw, index=iv30.index)


def _approx_regime_score(iv_rank: float, iv: float, rv: float) -> float:
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
# Core engine
# ---------------------------------------------------------------------------

def run_rule_backtest(
    ticker: str,
    data_days:       int   = 400,
    lookback:        int   = 252,
    iv_rv_min:       float = DEFAULT_IV_RV_MIN,
    iv_rank_min:     float = DEFAULT_IV_RANK_MIN,
    regime_lo:       float = DEFAULT_REGIME_LO,
    regime_hi:       float = DEFAULT_REGIME_HI,
    holding_period:  int   = _HOLDING,
    require_real_iv: bool  = False,
) -> dict:
    """
    Run the rule-based backtest and return a fully serialisable result dict.

    Parameters
    ----------
    ticker        : uppercase ticker symbol
    data_days     : number of historical trading days to load
    lookback      : rolling window (days) for IV Rank calculation
    iv_rv_min     : minimum IV/RV ratio for entry  (default 1.30)
    iv_rank_min   : minimum IV Rank for entry       (default 50)
    regime_lo     : lower bound of regime score     (default 40)
    regime_hi     : upper bound of regime score     (default 75)
    holding_period: trading days until exit          (default 10)
    """
    prices = get_price_history(ticker, days=data_days)
    iv_df  = get_iv_data(ticker, days=data_days, require_real_iv=require_real_iv)

    _filter = iv_df.attrs.get("real_iv_filter", {"enabled": False})

    idx    = prices.index.intersection(iv_df.index)
    prices = prices.loc[idx]
    iv_df  = iv_df.loc[idx]

    rv30       = _rolling_rv(prices, window=30)
    ivr_series = _rolling_iv_rank(iv_df["iv30"], lookback=lookback)

    n = len(idx)
    warmup = max(lookback // 4, holding_period + 1, 30)

    trades: list[dict] = []
    last_exit_i = -1

    for i in range(warmup, n):
        if i <= last_exit_i:
            continue

        iv  = float(iv_df["iv30"].iloc[i])
        rv  = float(rv30.iloc[i])
        ivr = float(ivr_series.iloc[i])

        if not (math.isfinite(iv) and math.isfinite(rv) and math.isfinite(ivr)):
            continue
        if rv <= 0:
            continue

        ratio  = iv / rv
        regime = _approx_regime_score(ivr, iv, rv)

        # ── 3-condition entry gate ─────────────────────────────────────
        if ratio  <= iv_rv_min:             continue
        if ivr    <= iv_rank_min:           continue
        if regime <  regime_lo or regime > regime_hi: continue

        exit_i = i + holding_period
        if exit_i >= n:
            break

        # Realised vol over the holding window
        hold_log = np.log(
            prices.values[i + 1 : exit_i + 1] /
            prices.values[i     : exit_i]
        )
        if len(hold_log) < 2:
            continue
        rv_hold = float(hold_log.std(ddof=1) * math.sqrt(_TDAYS) * 100)
        if not math.isfinite(rv_hold):
            continue

        pnl = (iv - rv_hold) / iv   # sell premium PnL as fraction of entry IV

        trades.append({
            "entry":        str(idx[i].date()),
            "exit":         str(idx[exit_i].date()),
            "iv_entry":     round(iv,     2),
            "rv_hold":      round(rv_hold, 2),
            "iv_rv_ratio":  round(ratio,  3),
            "iv_rank":      round(ivr,    1),
            "regime_score": round(regime, 1),
            "regime_label": _regime_label(regime),
            "pnl":          round(pnl,    4),
            "is_winner":    bool(pnl > 0),
        })
        last_exit_i = exit_i

    # ── Metrics ───────────────────────────────────────────────────────────
    if not trades:
        return _empty_result(ticker, iv_rv_min, iv_rank_min, regime_lo, regime_hi, holding_period,
                             real_iv_filter=_filter)

    pnls = [t["pnl"] for t in trades]
    pnl_arr = np.array(pnls)

    # Equity curve (daily granularity, step at each exit)
    daily_ret = pd.Series(0.0, index=idx)
    exit_counts = pd.Series(0, index=idx)
    for t in trades:
        exit_date = pd.Timestamp(t["exit"])
        if exit_date in daily_ret.index:
            daily_ret.loc[exit_date]    += t["pnl"]
            exit_counts.loc[exit_date]  += 1

    mask = exit_counts > 1
    daily_ret[mask] /= exit_counts[mask]
    equity = (1.0 + daily_ret).cumprod()

    # Regime breakdown
    buckets: dict[str, list[float]] = {
        "buy_premium": [], "neutral": [],
        "sell_selective": [], "sell_aggressive": [],
    }
    for t in trades:
        buckets[t["regime_label"]].append(t["pnl"])

    regime_breakdown = {}
    for label, bpnls in buckets.items():
        if bpnls:
            regime_breakdown[label] = {
                "n":        len(bpnls),
                "win_rate": round(float(win_rate(bpnls)), 3),
                "avg_pnl":  round(float(np.mean(bpnls)), 4),
                "total":    round(float(sum(bpnls)), 4),
            }
        else:
            regime_breakdown[label] = {"n": 0, "win_rate": None,
                                       "avg_pnl": None, "total": 0.0}

    def _safe(v: float) -> float | None:
        return round(float(v), 4) if math.isfinite(float(v)) else None

    best  = max(trades, key=lambda t: t["pnl"])
    worst = min(trades, key=lambda t: t["pnl"])

    return {
        "ticker":         ticker,
        "rule": {
            "iv_rv_min":      iv_rv_min,
            "iv_rank_min":    iv_rank_min,
            "regime_lo":      regime_lo,
            "regime_hi":      regime_hi,
            "holding_period": holding_period,
        },
        # ── Aggregate metrics ─────────────────────────────────────────
        "n_trades":      len(trades),
        "win_rate":      round(float(win_rate(pnls)), 3),
        "avg_return":    _safe(float(np.mean(pnl_arr))),
        "total_return":  _safe(float(np.sum(pnl_arr))),
        "max_drawdown":  _safe(max_drawdown(equity.values)),
        "sharpe":        _safe(sharpe_ratio(pnl_arr, periods_per_year=1)),
        "sortino":       _safe(sortino_ratio(pnl_arr, periods_per_year=1)),
        "expectancy":    _safe(expectancy(pnls)),
        "profit_factor": _safe(profit_factor(pnls)),
        # ── Highlights ───────────────────────────────────────────────
        "best_trade":    best,
        "worst_trade":   worst,
        # ── Breakdown by regime at entry ─────────────────────────────
        "performance_by_regime": regime_breakdown,
        # ── Daily equity curve ───────────────────────────────────────
        "equity_curve": [
            {"date": str(d.date()), "equity": round(float(v), 4)}
            for d, v in equity.items()
            if math.isfinite(float(v))
        ],
        # ── Individual trades ────────────────────────────────────────
        "trades":        trades,
        "data_source":   "mock",
        # ── IV data quality provenance ────────────────────────────────
        "iv_data_quality": get_iv_quality_summary(ticker, data_days),
        # ── Real-IV filter ────────────────────────────────────────────
        "real_iv_filter": _filter,
        "sample_size_warning": (len(trades) > 0 and len(trades) < 20) if require_real_iv else False,
    }


def _empty_result(
    ticker: str,
    iv_rv_min: float,
    iv_rank_min: float,
    regime_lo: float,
    regime_hi: float,
    holding_period: int,
    real_iv_filter: dict | None = None,
) -> dict:
    return {
        "ticker": ticker,
        "rule": {
            "iv_rv_min":      iv_rv_min,
            "iv_rank_min":    iv_rank_min,
            "regime_lo":      regime_lo,
            "regime_hi":      regime_hi,
            "holding_period": holding_period,
        },
        "n_trades":      0,
        "win_rate":      None,
        "avg_return":    None,
        "total_return":  0.0,
        "max_drawdown":  None,
        "sharpe":        None,
        "sortino":       None,
        "expectancy":    None,
        "profit_factor": None,
        "best_trade":    None,
        "worst_trade":   None,
        "performance_by_regime": {
            k: {"n": 0, "win_rate": None, "avg_pnl": None, "total": 0.0}
            for k in ("buy_premium", "neutral", "sell_selective", "sell_aggressive")
        },
        "equity_curve":  [],
        "trades":        [],
        "data_source":   "mock",
        "iv_data_quality": {"real": 0, "synthetic_fallback": 0, "interpolated": 0, "total": 0},
        "real_iv_filter": real_iv_filter if real_iv_filter is not None else {"enabled": False},
        "sample_size_warning": False,
    }
