"""
backtesting/engine.py
---------------------
Core backtest runner for option volatility signals.

P&L model
---------
Short premium (SELL_PREMIUM):
    pnl = (IV_entry − RV_holding) / IV_entry

Long premium (BUY_PREMIUM):
    pnl = (RV_holding − IV_entry) / IV_entry

Interpretation: fraction of IV premium captured (positive) or lost (negative).
A pnl of +0.20 means you captured 20% of the implied vol premium.
A pnl of −0.50 means realised vol exceeded implied by 50% of the entry IV.

Trade management
----------------
Non-overlapping: a new trade only opens after the previous one exits.
This avoids double-counting and keeps the equity curve straightforward.

Phase 3 swap
------------
Replace get_price_history() / get_iv_data() with Polygon + ORATS adapters.
The rest of the engine is data-source agnostic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from app.backtesting.signals import (
    Signal,
    iv_rv_ratio_signals,
    iv_rank_signals,
    term_structure_signals,
    vix_regime_signals,
)
from app.backtesting.metrics import (
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    calmar_ratio,
    win_rate,
    expectancy,
    profit_factor,
)
from app.data.mock_provider import get_price_history, get_iv_data

SignalType = Literal[
    "iv_rv_ratio", "iv_rank", "term_structure", "vix_regime", "combined"
]

_TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    """All parameters needed to fully specify one backtest run."""
    ticker:            str        = "SPY"
    signal_type:       SignalType = "iv_rv_ratio"
    holding_period:    int        = 30    # trading days per position
    lookback:          int        = 252   # rolling window for IV Rank
    # Thresholds
    iv_rv_threshold:   float      = 1.20  # IV/RV sell threshold
    iv_rank_threshold: float      = 50.0  # IV Rank sell threshold (0-100)
    vix_low:           float      = 15.0  # below → BUY_PREMIUM
    vix_elevated:      float      = 20.0  # above → SELL_PREMIUM
    vix_spike:         float      = 35.0  # above → BUY_PREMIUM (fade spike)
    ts_contango_min:   float      = 0.0   # minimum slope for SELL signal
    data_days:         int        = 400   # historical rows to load
    require_real_iv:   bool       = False # when True, only use rows with real IV data


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    entry_date:              pd.Timestamp
    exit_date:               pd.Timestamp
    direction:               Signal
    iv_at_entry:             float   # IV30 (annualised %)
    rv_realized:             float   # RV over holding period (annualised %)
    pnl:                     float   # normalised P&L (fraction of entry IV)
    regime_score_at_entry:   float   # composite score 0-100
    metadata:                dict    = field(default_factory=dict)

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0

    @property
    def regime_label(self) -> str:
        return _regime_label(self.regime_score_at_entry)

    def to_dict(self) -> dict:
        return {
            "entry":         str(self.entry_date.date()),
            "exit":          str(self.exit_date.date()),
            "direction":     self.direction.value,
            "iv_entry":      round(self.iv_at_entry, 2),
            "rv_hold":       round(self.rv_realized, 2),
            "pnl":           round(self.pnl, 4),
            "regime_score":  round(self.regime_score_at_entry, 1),
            "regime_label":  self.regime_label,
            **{k: (round(v, 4) if isinstance(v, float) else v)
               for k, v in self.metadata.items()},
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _regime_label(score: float) -> str:
    if score < 30:  return "buy_premium"
    if score < 50:  return "neutral"
    if score < 70:  return "sell_selective"
    return "sell_aggressive"


def _rolling_rv(prices: pd.Series, window: int = 30) -> pd.Series:
    """
    Vectorised close-to-close realised vol (annualised %).
    Avoids O(n×w) Python loop — runs in pandas C backend.
    """
    log_rets = np.log(prices / prices.shift(1))
    rv = log_rets.rolling(window, min_periods=window).std(ddof=1) * math.sqrt(_TRADING_DAYS) * 100
    return rv


def _rolling_iv_rank(iv30: pd.Series, lookback: int) -> pd.Series:
    """Vectorised rolling IV Rank (0–100)."""
    min_p = max(lookback // 4, 10)
    lo = iv30.rolling(lookback, min_periods=min_p).min()
    hi = iv30.rolling(lookback, min_periods=min_p).max()
    rng = hi - lo
    ivr = np.where(rng > 0, (iv30 - lo) / rng * 100, 50.0)
    return pd.Series(ivr, index=iv30.index)


def _approx_regime_score(
    iv_rank: float, iv: float, rv: float
) -> float:
    """
    Lightweight regime score without full z-score history.
    Mirrors the weights in engine.py regime_score() but uses simplified inputs.
    """
    vrp = (iv - rv) / rv if rv > 0 else 0.0
    ratio = iv / rv if rv > 0 else 1.0

    rank_s  = float(np.clip(iv_rank, 0, 100))
    vrp_s   = float(np.clip((vrp  + 0.5) / 1.0  * 100, 0, 100))
    ratio_s = float(np.clip((ratio - 0.5) / 1.5  * 100, 0, 100))
    z_s     = 50.0   # neutral — full z-score needs a series

    return round(0.35 * rank_s + 0.25 * vrp_s + 0.25 * ratio_s + 0.15 * z_s, 2)


# ---------------------------------------------------------------------------
# Signal dispatch
# ---------------------------------------------------------------------------

def _build_signals(
    config: BacktestConfig,
    iv_df: pd.DataFrame,
    rv30: pd.Series,
) -> pd.Series:
    """Return a pd.Series[Signal] aligned with iv_df.index."""
    if config.signal_type == "iv_rv_ratio":
        return iv_rv_ratio_signals(
            iv_df["iv30"], rv30,
            sell_threshold=config.iv_rv_threshold,
        )

    if config.signal_type == "iv_rank":
        return iv_rank_signals(
            iv_df["iv30"],
            sell_threshold=config.iv_rank_threshold,
            lookback=config.lookback,
        )

    if config.signal_type == "term_structure":
        return term_structure_signals(
            iv_df["iv7"], iv_df["iv30"],
            contango_min=config.ts_contango_min,
        )

    if config.signal_type == "vix_regime":
        # For non-SPY tickers use SPY IV30 as VIX proxy
        if config.ticker == "SPY":
            proxy = iv_df["iv30"]
        else:
            spy_iv = get_iv_data("SPY", days=config.data_days)
            proxy  = spy_iv["iv30"].reindex(iv_df.index).ffill()
        return vix_regime_signals(
            proxy,
            low_threshold=config.vix_low,
            elevated_threshold=config.vix_elevated,
            spike_threshold=config.vix_spike,
        )

    if config.signal_type == "combined":
        s1 = iv_rv_ratio_signals(iv_df["iv30"], rv30,
                                  sell_threshold=config.iv_rv_threshold)
        s2 = iv_rank_signals(iv_df["iv30"],
                              sell_threshold=config.iv_rank_threshold,
                              lookback=config.lookback)
        s3 = term_structure_signals(iv_df["iv7"], iv_df["iv30"])

        sell = ((s1 == Signal.SELL_PREMIUM).astype(int)
              + (s2 == Signal.SELL_PREMIUM).astype(int)
              + (s3 == Signal.SELL_PREMIUM).astype(int))
        buy  = ((s1 == Signal.BUY_PREMIUM).astype(int)
              + (s2 == Signal.BUY_PREMIUM).astype(int)
              + (s3 == Signal.BUY_PREMIUM).astype(int))

        # Use pandas boolean-index assignment (same pattern as other signal
        # generators) to preserve Signal enum instances.  np.where coerces
        # enum members to plain str, which breaks Trade.to_dict().
        combined = pd.Series(Signal.NEUTRAL, index=iv_df.index, name="signal")
        combined[buy  >= 2] = Signal.BUY_PREMIUM
        combined[sell >= 2] = Signal.SELL_PREMIUM   # sell takes priority
        return combined

    raise ValueError(f"Unknown signal_type: {config.signal_type!r}")


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def _simulate_trades(
    config: BacktestConfig,
    prices: pd.Series,
    iv_df: pd.DataFrame,
    rv30: pd.Series,
    ivr_series: pd.Series,
    signals: pd.Series,
) -> list[Trade]:
    """
    Non-overlapping trade simulator.

    Iterates through signal dates, opens a position when signal != NEUTRAL,
    holds for `holding_period` trading days, then closes.
    Only one position open at a time.
    """
    idx = prices.index
    n   = len(idx)

    # Warmup: skip first `lookback/4` rows so rolling series are stable
    warmup = max(config.lookback // 4, config.holding_period + 1)

    trades: list[Trade] = []
    last_exit_i = -1   # index of the last closed position's exit bar

    for i in range(warmup, n):
        # Non-overlapping: skip if still in a position
        if i <= last_exit_i:
            continue

        sig = signals.iloc[i]
        if sig == Signal.NEUTRAL:
            continue

        exit_i = i + config.holding_period
        if exit_i >= n:
            break   # not enough data to complete holding period

        iv_entry = float(iv_df["iv30"].iloc[i])
        rv_entry = float(rv30.iloc[i])
        if not math.isfinite(iv_entry) or iv_entry <= 0:
            continue
        if not math.isfinite(rv_entry) or rv_entry <= 0:
            continue

        # Realised vol OVER the holding period (use the slice of prices)
        hold_log_rets = np.log(
            prices.values[i + 1 : exit_i + 1]
            / prices.values[i : exit_i]
        )
        if len(hold_log_rets) < 2:
            continue
        rv_hold = float(hold_log_rets.std(ddof=1) * math.sqrt(_TRADING_DAYS) * 100)
        if not math.isfinite(rv_hold):
            continue

        # Normalised P&L as fraction of entry IV
        vrp_frac = (iv_entry - rv_hold) / iv_entry
        pnl = vrp_frac if sig == Signal.SELL_PREMIUM else -vrp_frac

        # Regime score at entry
        iv_rank_val = float(ivr_series.iloc[i])
        rscore = _approx_regime_score(iv_rank_val, iv_entry, rv_entry)

        trades.append(Trade(
            entry_date            = idx[i],
            exit_date             = idx[exit_i],
            direction             = sig,
            iv_at_entry           = iv_entry,
            rv_realized           = rv_hold,
            pnl                   = pnl,
            regime_score_at_entry = rscore,
            metadata={
                "iv_rank":     round(iv_rank_val, 1),
                "iv_rv_ratio": round(iv_entry / rv_entry, 3) if rv_entry > 0 else None,
                "vrp_pct":     round((iv_entry - rv_entry) / rv_entry * 100, 2)
                               if rv_entry > 0 else None,
            },
        ))
        last_exit_i = exit_i

    return trades


# ---------------------------------------------------------------------------
# Metrics assembly
# ---------------------------------------------------------------------------

def _compute_result(
    config: BacktestConfig,
    trades: list[Trade],
    all_dates: pd.DatetimeIndex,
    real_iv_filter: dict | None = None,
) -> dict:
    if not trades:
        return _empty_result(config, real_iv_filter=real_iv_filter)

    pnls = [t.pnl for t in trades]
    n    = len(trades)

    # Trade-level return series (one return per completed trade)
    trade_returns = np.array(pnls)

    # Date-indexed equity curve (daily granularity, step at each exit)
    daily_returns = pd.Series(0.0, index=all_dates)
    for t in trades:
        if t.exit_date in daily_returns.index:
            # Average if multiple trades exit on the same day
            prev = daily_returns.loc[t.exit_date]
            daily_returns.loc[t.exit_date] = prev + t.pnl   # will normalise below

    # Normalise days with multiple exits
    exits_per_day = pd.Series(0, index=all_dates)
    for t in trades:
        if t.exit_date in exits_per_day.index:
            exits_per_day.loc[t.exit_date] += 1
    mask = exits_per_day > 1
    daily_returns[mask] = daily_returns[mask] / exits_per_day[mask]

    equity = (1.0 + daily_returns).cumprod()

    # --- Scalar metrics ---
    sh  = sharpe_ratio(trade_returns, periods_per_year=1)  # per-trade Sharpe
    so  = sortino_ratio(trade_returns, periods_per_year=1)
    mdd = max_drawdown(equity.values)
    cal = calmar_ratio(trade_returns, periods_per_year=1)
    wr  = win_rate(pnls)
    exp = expectancy(pnls)
    pf  = profit_factor(pnls)

    # --- Regime breakdown ---
    regime_buckets: dict[str, list[float]] = {
        "buy_premium": [], "neutral": [],
        "sell_selective": [], "sell_aggressive": [],
    }
    for t in trades:
        regime_buckets[t.regime_label].append(t.pnl)

    regime_breakdown = {}
    for label, bucket_pnls in regime_buckets.items():
        if bucket_pnls:
            regime_breakdown[label] = {
                "n":        len(bucket_pnls),
                "avg_pnl":  round(float(np.mean(bucket_pnls)), 4),
                "win_rate": round(float(win_rate(bucket_pnls)), 3),
                "total":    round(float(sum(bucket_pnls)), 4),
            }
        else:
            regime_breakdown[label] = {"n": 0, "avg_pnl": None,
                                       "win_rate": None, "total": 0.0}

    # --- Direction breakdown ---
    sell_trades = [t for t in trades if t.direction == Signal.SELL_PREMIUM]
    buy_trades  = [t for t in trades if t.direction == Signal.BUY_PREMIUM]

    def _dir_stats(ts: list[Trade]) -> dict:
        ps = [t.pnl for t in ts]
        return {
            "n":        len(ts),
            "win_rate": round(float(win_rate(ps)), 3) if ps else None,
            "avg_pnl":  round(float(np.mean(ps)), 4)  if ps else None,
        }

    def _safe(v: float) -> float | None:
        return round(v, 4) if math.isfinite(v) else None

    return {
        "ticker":      config.ticker,
        "signal_type": config.signal_type,
        "config": {
            "holding_period":    config.holding_period,
            "lookback":          config.lookback,
            "iv_rv_threshold":   config.iv_rv_threshold,
            "iv_rank_threshold": config.iv_rank_threshold,
            "vix_low":           config.vix_low,
            "vix_elevated":      config.vix_elevated,
            "ts_contango_min":   config.ts_contango_min,
        },
        # Summary metrics
        "n_trades":     n,
        "sharpe":       _safe(sh),
        "sortino":      _safe(so),
        "max_drawdown": _safe(mdd),
        "calmar":       _safe(cal),
        "win_rate":     round(wr, 3)  if math.isfinite(wr)  else None,
        "expectancy":   _safe(exp),
        "profit_factor": _safe(pf),
        "total_pnl":    round(float(sum(pnls)), 4),
        "avg_pnl":      round(float(np.mean(pnls)), 4),
        # Direction stats
        "sell_stats":   _dir_stats(sell_trades),
        "buy_stats":    _dir_stats(buy_trades),
        # Regime breakdown
        "regime_breakdown": regime_breakdown,
        # Equity curve (daily, indexed by exit events)
        "equity_curve": [
            {"date": str(d.date()), "equity": round(float(v), 4)}
            for d, v in equity.items()
            if math.isfinite(v)
        ],
        # Individual trades
        "trades": [t.to_dict() for t in trades],
        "data_source": "mock",
        # Real-IV filter provenance
        "real_iv_filter": real_iv_filter if real_iv_filter is not None else {"enabled": False},
        "sample_size_warning": (n > 0 and n < 20) if (real_iv_filter or {}).get("enabled") else False,
    }


def _empty_result(config: BacktestConfig, real_iv_filter: dict | None = None) -> dict:
    return {
        "ticker":       config.ticker,
        "signal_type":  config.signal_type,
        "config": {
            "holding_period":    config.holding_period,
            "lookback":          config.lookback,
            "iv_rv_threshold":   config.iv_rv_threshold,
            "iv_rank_threshold": config.iv_rank_threshold,
            "vix_low":           config.vix_low,
            "vix_elevated":      config.vix_elevated,
            "ts_contango_min":   config.ts_contango_min,
        },
        "n_trades":     0,
        "sharpe":       None,
        "sortino":      None,
        "max_drawdown": None,
        "calmar":       None,
        "win_rate":     None,
        "expectancy":   None,
        "profit_factor": None,
        "total_pnl":    0.0,
        "avg_pnl":      0.0,
        "sell_stats":   {"n": 0, "win_rate": None, "avg_pnl": None},
        "buy_stats":    {"n": 0, "win_rate": None, "avg_pnl": None},
        "regime_breakdown": {
            k: {"n": 0, "avg_pnl": None, "win_rate": None, "total": 0.0}
            for k in ("buy_premium", "neutral", "sell_selective", "sell_aggressive")
        },
        "equity_curve": [],
        "trades":       [],
        "data_source":  "mock",
        "real_iv_filter": real_iv_filter if real_iv_filter is not None else {"enabled": False},
        "sample_size_warning": False,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_backtest(config: BacktestConfig) -> dict:
    """
    Execute one backtest and return a fully serialisable result dict.

    Parameters
    ----------
    config : BacktestConfig
        Full specification of the backtest (ticker, signal, thresholds, …).

    Returns
    -------
    dict
        Keys: n_trades, sharpe, sortino, max_drawdown, calmar, win_rate,
              expectancy, profit_factor, total_pnl, regime_breakdown,
              equity_curve, trades, data_source.
    """
    prices = get_price_history(config.ticker, days=config.data_days)
    iv_df  = get_iv_data(config.ticker, days=config.data_days,
                         require_real_iv=config.require_real_iv)

    # Extract filter summary from DataFrame attrs
    _filter = iv_df.attrs.get("real_iv_filter", {"enabled": False})

    # Align on trading-day intersection
    idx    = prices.index.intersection(iv_df.index)
    prices = prices.loc[idx]
    iv_df  = iv_df.loc[idx]

    # Pre-compute rolling series (vectorised)
    rv30       = _rolling_rv(prices, window=30)
    ivr_series = _rolling_iv_rank(iv_df["iv30"], lookback=config.lookback)

    # Generate signals
    signals = _build_signals(config, iv_df, rv30)

    # Simulate trades
    trades = _simulate_trades(config, prices, iv_df, rv30, ivr_series, signals)

    return _compute_result(config, trades, idx, real_iv_filter=_filter)


def run_comparison(
    ticker: str,
    holding_period: int = 30,
    require_real_iv: bool = False,
    **threshold_overrides,
) -> dict:
    """
    Run all five signal types on the same ticker and return a comparison dict.

    Useful for the /api/backtest/compare endpoint.
    """
    results = {}
    for stype in ("iv_rv_ratio", "iv_rank", "term_structure", "vix_regime", "combined"):
        cfg = BacktestConfig(
            ticker=ticker,
            signal_type=stype,  # type: ignore[arg-type]
            holding_period=holding_period,
            require_real_iv=require_real_iv,
            **threshold_overrides,
        )
        results[stype] = run_backtest(cfg)

    # Summary table for quick comparison
    summary = [
        {
            "signal":       stype,
            "n_trades":     results[stype]["n_trades"],
            "sharpe":       results[stype]["sharpe"],
            "max_drawdown": results[stype]["max_drawdown"],
            "win_rate":     results[stype]["win_rate"],
            "expectancy":   results[stype]["expectancy"],
            "total_pnl":    results[stype]["total_pnl"],
        }
        for stype in results
    ]

    return {
        "ticker":          ticker,
        "holding_period":  holding_period,
        "summary":         summary,
        "details":         results,
        "data_source":     "mock",
    }
