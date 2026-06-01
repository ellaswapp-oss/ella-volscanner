"""
backtesting/metrics.py
----------------------
Pure performance metrics — no I/O, no side effects.

All functions accept array-like inputs and return floats (or nan on bad input).

Metrics:
    sharpe_ratio    — annualised risk-adjusted return
    sortino_ratio   — like Sharpe but penalises only downside deviation
    max_drawdown    — worst peak-to-trough decline
    calmar_ratio    — annualised return / |max drawdown|
    win_rate        — fraction of positive-P&L trades
    expectancy      — average P&L per trade (accounting for win/loss split)
    profit_factor   — gross wins / |gross losses|
"""

from __future__ import annotations

import math
from typing import Union

import numpy as np
import pandas as pd

ArrayLike = Union[list, np.ndarray, pd.Series]

_TRADING_DAYS = 252


def _to_array(x: ArrayLike) -> np.ndarray:
    return np.asarray(x, dtype=float)


# ---------------------------------------------------------------------------
# Sharpe ratio
# ---------------------------------------------------------------------------

def sharpe_ratio(
    returns: ArrayLike,
    *,
    risk_free_annual: float = 0.05,
    periods_per_year: int = _TRADING_DAYS,
) -> float:
    """
    Annualised Sharpe ratio.

    Parameters
    ----------
    returns : array-like
        Periodic returns (e.g. daily, per-trade).
    risk_free_annual : float
        Annual risk-free rate to subtract. Default 5%.
    periods_per_year : int
        Number of periods in a year. Use 1 for per-trade returns where
        each "period" is one completed trade.

    Returns
    -------
    float
        Annualised Sharpe, or nan if std is 0 or < 2 observations.
    """
    r = _to_array(returns)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return float("nan")

    rf_per_period = risk_free_annual / periods_per_year
    excess = r - rf_per_period
    std = excess.std(ddof=1)
    if std == 0.0:
        return float("nan")

    return float((excess.mean() / std) * math.sqrt(periods_per_year))


# ---------------------------------------------------------------------------
# Sortino ratio
# ---------------------------------------------------------------------------

def sortino_ratio(
    returns: ArrayLike,
    *,
    risk_free_annual: float = 0.05,
    periods_per_year: int = _TRADING_DAYS,
) -> float:
    """
    Annualised Sortino ratio — penalises only downside deviation.

    Returns nan when there are fewer than 2 negative-excess returns.
    """
    r = _to_array(returns)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return float("nan")

    rf_per_period = risk_free_annual / periods_per_year
    excess = r - rf_per_period
    downside = excess[excess < 0.0]

    if len(downside) < 2:
        return float("nan")

    downside_std = downside.std(ddof=1)
    if downside_std == 0.0:
        return float("nan")

    return float((excess.mean() / downside_std) * math.sqrt(periods_per_year))


# ---------------------------------------------------------------------------
# Max drawdown
# ---------------------------------------------------------------------------

def max_drawdown(equity_curve: ArrayLike) -> float:
    """
    Maximum peak-to-trough drawdown of an equity curve.

    Parameters
    ----------
    equity_curve : array-like
        Portfolio value series (not returns). Typically starts at 1.0.

    Returns
    -------
    float
        Worst drawdown in [−1, 0]. 0 means no drawdown; −1 means total loss.
        Returns 0.0 for fewer than 2 finite observations.
    """
    eq = _to_array(equity_curve)
    eq = eq[np.isfinite(eq)]
    if len(eq) < 2:
        return 0.0

    running_max = np.maximum.accumulate(eq)
    # Avoid divide-by-zero on zero-valued peaks
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(running_max > 0, (eq - running_max) / running_max, 0.0)
    return float(dd.min())


# ---------------------------------------------------------------------------
# Calmar ratio
# ---------------------------------------------------------------------------

def calmar_ratio(
    returns: ArrayLike,
    *,
    periods_per_year: int = _TRADING_DAYS,
) -> float:
    """
    Calmar ratio = annualised return / |max drawdown|.

    Returns nan when max drawdown is zero or returns are insufficient.
    """
    r = _to_array(returns)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return float("nan")

    equity = np.cumprod(1.0 + r)
    mdd = abs(max_drawdown(equity))
    if mdd == 0.0:
        return float("nan")

    ann_return = float((1.0 + r.mean()) ** periods_per_year - 1.0)
    return ann_return / mdd


# ---------------------------------------------------------------------------
# Win rate
# ---------------------------------------------------------------------------

def win_rate(pnls: ArrayLike) -> float:
    """
    Fraction of trades with strictly positive P&L.

    Returns nan for empty or all-nan input.
    """
    arr = _to_array(pnls)
    finite = arr[np.isfinite(arr)]
    if len(finite) == 0:
        return float("nan")
    return float((finite > 0).sum() / len(finite))


# ---------------------------------------------------------------------------
# Expectancy
# ---------------------------------------------------------------------------

def expectancy(pnls: ArrayLike) -> float:
    """
    Expectancy = (win_rate × avg_win) + (loss_rate × avg_loss).

    Represents the average P&L per trade across all trade outcomes.
    A positive expectancy means the strategy has an edge.

    Returns nan for empty input.
    """
    arr = _to_array(pnls)
    finite = arr[np.isfinite(arr)]
    if len(finite) == 0:
        return float("nan")

    wins   = finite[finite > 0]
    losses = finite[finite <= 0]

    wr = len(wins)   / len(finite)
    lr = len(losses) / len(finite)

    avg_win  = float(wins.mean())   if len(wins)   > 0 else 0.0
    avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0

    return float(wr * avg_win + lr * avg_loss)


# ---------------------------------------------------------------------------
# Profit factor
# ---------------------------------------------------------------------------

def profit_factor(pnls: ArrayLike) -> float:
    """
    Gross profit / |gross loss|.

    > 1 means winners outweigh losers in aggregate.
    Returns nan when there are no losing trades.
    """
    arr = _to_array(pnls)
    finite = arr[np.isfinite(arr)]
    if len(finite) == 0:
        return float("nan")

    gross_win  = float(finite[finite > 0].sum())
    gross_loss = abs(float(finite[finite <= 0].sum()))

    if gross_loss == 0.0:
        return float("nan")   # no losers — undefined, not infinite edge
    return gross_win / gross_loss
