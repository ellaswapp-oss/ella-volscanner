"""
calculations/engine.py
----------------------
Core volatility calculations. All functions are pure (no I/O, no side effects).

Inputs are array-like (list, np.ndarray, pd.Series) unless noted.
All vol figures are annualized percentages unless otherwise stated.
"""

from __future__ import annotations

import math
from typing import Union

import numpy as np
import pandas as pd

ArrayLike = Union[list, np.ndarray, pd.Series]

# Default trading days per year. Override for crypto (365) or weekly data (52).
_TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_array(x: ArrayLike) -> np.ndarray:
    return np.asarray(x, dtype=float)


def _log_returns(prices: np.ndarray) -> np.ndarray:
    """Close-to-close log returns, dropping non-finite values."""
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = np.log(prices[1:] / prices[:-1])
    return ret[np.isfinite(ret)]


# ---------------------------------------------------------------------------
# realized_volatility
# ---------------------------------------------------------------------------

def realized_volatility(
    prices: ArrayLike,
    window: int,
    *,
    annualization_factor: int = _TRADING_DAYS,
) -> float:
    """
    Annualized close-to-close realized volatility (%).

    Parameters
    ----------
    prices : array-like
        Closing prices, length >= window + 1.
    window : int
        Look-back period in trading days (>= 1).
    annualization_factor : int, default 252
        Trading periods per year. Use 365 for crypto, 52 for weekly bars.

    Returns
    -------
    float
        Annualized RV in percent, or nan if insufficient data.

    Notes
    -----
    Uses sample std (ddof=1) for window > 1. Falls back to population std
    (ddof=0) for window=1 because a single observation has no sample variance.
    Non-finite prices (NaN, inf) are silently dropped before windowing.
    """
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")
    if annualization_factor < 1:
        raise ValueError(f"annualization_factor must be >= 1, got {annualization_factor}")

    arr = _to_array(prices)
    rets = _log_returns(arr)

    if len(rets) < window:
        return float("nan")

    window_rets = rets[-window:]
    ddof = 1 if len(window_rets) > 1 else 0
    rv = window_rets.std(ddof=ddof)
    return float(rv * math.sqrt(annualization_factor) * 100)


# ---------------------------------------------------------------------------
# iv_rv_ratio
# ---------------------------------------------------------------------------

def iv_rv_ratio(iv: float, rv: float) -> float:
    """
    IV / RV ratio.

    Returns nan when rv is zero or non-finite, or iv is non-finite.
    A ratio > 1 means IV is elevated above realised vol.
    """
    if not math.isfinite(rv) or rv == 0.0:
        return float("nan")
    if not math.isfinite(iv):
        return float("nan")
    return iv / rv


# ---------------------------------------------------------------------------
# iv_rank
# ---------------------------------------------------------------------------

def iv_rank(
    current_iv: float,
    iv_history: ArrayLike,
    *,
    lookback: int | None = None,
) -> float:
    """
    IV Rank (0–100) of current_iv relative to its history.

    iv_rank = (current_iv - period_low) / (period_high - period_low) × 100

    Parameters
    ----------
    current_iv : float
        The implied vol level to rank.
    iv_history : array-like
        Historical IV series. Typically 252 trading days (1 year).
    lookback : int | None, default None
        If given, only the most-recent `lookback` finite values are used
        as the comparison window. Useful when iv_history is longer than
        the desired ranking period.

    Returns
    -------
    float
        IV Rank 0–100, clamped so out-of-range inputs return 0 or 100.
        Returns 50.0 when the history is perfectly flat (no range).
        Returns nan when current_iv is non-finite or history has < 2 finite values.
    """
    if not math.isfinite(current_iv):
        return float("nan")

    hist = _to_array(iv_history)
    finite = hist[np.isfinite(hist)]

    if lookback is not None and lookback > 0:
        finite = finite[-lookback:]

    if len(finite) < 2:
        return float("nan")

    lo, hi = finite.min(), finite.max()
    if hi == lo:
        return 50.0

    rank = (current_iv - lo) / (hi - lo) * 100
    return float(np.clip(rank, 0.0, 100.0))


# ---------------------------------------------------------------------------
# volatility_risk_premium
# ---------------------------------------------------------------------------

def volatility_risk_premium(
    iv: float,
    rv: float,
    *,
    absolute: bool = False,
) -> float:
    """
    Volatility risk premium — the compensation sellers receive for bearing vol risk.

    Parameters
    ----------
    iv : float
        Implied volatility (%).
    rv : float
        Realized volatility (%).
    absolute : bool, default False
        False → relative VRP = (IV - RV) / RV   [unit-free, comparable across levels]
        True  → absolute VRP = IV - RV           [in vol points, easier to interpret]

    Returns
    -------
    float
        VRP value. Positive = IV elevated (premium selling favoured).
        Negative = IV compressed (premium buying favoured).
        Returns nan when rv is zero or non-finite, or iv is non-finite.
    """
    if not math.isfinite(rv) or rv == 0.0:
        return float("nan")
    if not math.isfinite(iv):
        return float("nan")

    if absolute:
        return iv - rv
    return (iv - rv) / rv


# ---------------------------------------------------------------------------
# z_score
# ---------------------------------------------------------------------------

def z_score(
    series: ArrayLike,
    *,
    lookback: int | None = None,
) -> np.ndarray:
    """
    Element-wise z-score: (x - mean) / std, using sample std (ddof=1).

    Parameters
    ----------
    series : array-like
        Input series. Non-finite values are excluded from mean/std calculation
        but their positions are preserved as nan in the output.
    lookback : int | None, default None
        If given, mean and std are estimated using only the most-recent
        `lookback` finite observations. Earlier values are still z-scored
        against this window's statistics, giving a "rolling anchor" effect.
        Typical use: lookback=252 to z-score a spread vs its 1-year history.

    Returns
    -------
    np.ndarray
        Z-scores, same length as input. nan where input is non-finite or
        where std cannot be computed (< 2 observations).

    Examples
    --------
    # Current IV/RV spread z-score vs trailing 1 year
    spread = iv30_series - rv30_series
    current_z = z_score(spread, lookback=252)[-1]
    """
    arr = _to_array(series)
    finite_mask = np.isfinite(arr)
    finite_vals = arr[finite_mask]

    result = np.full_like(arr, fill_value=float("nan"))

    if len(finite_vals) < 2:
        return result

    # Estimate statistics from the lookback window only
    if lookback is not None and lookback > 0:
        anchor = finite_vals[-lookback:]
    else:
        anchor = finite_vals

    if len(anchor) < 2:
        return result

    mu = anchor.mean()
    sigma = anchor.std(ddof=1)

    if sigma == 0.0:
        result[finite_mask] = 0.0
        return result

    result[finite_mask] = (finite_vals - mu) / sigma
    return result


# ---------------------------------------------------------------------------
# term_structure
# ---------------------------------------------------------------------------

def term_structure(
    iv7:  float | None,
    iv30: float,
    iv60: float | None,
    iv90: float | None,
    *,
    flat_threshold_pct: float = 3.0,
) -> dict:
    """
    IV term structure across four tenors.

    Parameters
    ----------
    iv7 : float | None
        7-day implied vol (%).  ``None`` when not available from the chain.
    iv30 : float
        30-day implied vol (%).  Always required (caller passes a fallback).
    iv60 : float | None
        60-day implied vol (%).  ``None`` when not available from the chain.
    iv90 : float | None
        90-day implied vol (%).  ``None`` when not available from the chain.
    flat_threshold_pct : float, default 3.0
        Total slope is "flat" when |total_slope| < flat_threshold_pct % of
        the ATM (iv30) level. Default 3% means a 15-vol ATM curve is flat
        if its full slope is < 0.45 pts; a 50-vol ATM curve is flat if < 1.5 pts.
        This scales with the underlying vol level, unlike an absolute threshold.

    Returns
    -------
    dict
        front_slope  : iv30 - iv7         (None when iv7 is None)
        mid_slope    : iv60 - iv30        (None when iv60 is None)
        back_slope   : iv90 - iv60        (None when iv60 or iv90 is None)
        total_slope  : iv90 - iv7         (None when iv7 or iv90 is None)
        curvature    : front_slope - back_slope  (None when either is None)
                       Positive = front steeper than back (decelerating contango).
                       Negative = back steeper than front (accelerating).
        shape        : "contango" | "inverted" | "humped" | "flat" | "partial"
                       "partial" when any segment slope cannot be computed.
    """
    # None-safe arithmetic — a None input propagates to a None slope.
    front: float | None = round(iv30 - iv7,  4) if iv7  is not None else None
    mid:   float | None = round(iv60 - iv30, 4) if iv60 is not None else None
    back:  float | None = (
        round(iv90 - iv60, 4) if (iv60 is not None and iv90 is not None) else None
    )
    total: float | None = (
        round(iv90 - iv7,  4) if (iv7  is not None and iv90 is not None) else None
    )
    curvature: float | None = (
        round(front - back, 4) if (front is not None and back is not None) else None
    )

    # Shape classification requires all three segment slopes.
    # When any is missing the curve is "partial" — no full-shape verdict possible.
    if front is None or mid is None or back is None:
        shape = "partial"
    else:
        # All three slopes are available; total is also non-None (guaranteed
        # because front non-None → iv7 non-None, back non-None → iv90 non-None).
        atm = iv30 if iv30 > 0 else 1.0
        flat_cutoff = atm * flat_threshold_pct / 100
        slopes = [front, mid, back]
        if abs(total) < flat_cutoff:  # type: ignore[arg-type]
            shape = "flat"
        elif all(s >= 0 for s in slopes):
            shape = "contango"
        elif all(s <= 0 for s in slopes):
            shape = "inverted"
        else:
            shape = "humped"

    return {
        "front_slope": front,
        "mid_slope":   mid,
        "back_slope":  back,
        "total_slope": total,
        "curvature":   curvature,
        "shape":       shape,
    }


# Backward-compatible alias
term_structure_slope = term_structure


# ---------------------------------------------------------------------------
# regime_score
# ---------------------------------------------------------------------------

def regime_score(
    inputs: dict,
    *,
    weights: dict[str, float] | None = None,
) -> float:
    """
    Composite volatility regime score (0–100).

    Higher score → IV more elevated → better environment for selling premium.

    Parameters
    ----------
    inputs : dict
        iv_rank     : float, 0–100
        vrp         : float, e.g. 0.20 = IV is 20% above RV
        iv_rv_ratio : float, e.g. 1.3
        z_score     : float, current IV/RV spread z-score

    weights : dict | None, default None
        Override component weights. Must contain the same keys as inputs.
        Weights are re-normalised to sum to 1, so absolute values don't matter.
        Default: {iv_rank: 0.35, vrp: 0.25, iv_rv_ratio: 0.25, z_score: 0.15}

    Returns
    -------
    float
        Score 0–100, rounded to 2 dp. Missing or non-finite inputs fall back
        to a neutral contribution rather than propagating nan.

    Normalisation ranges
    --------------------
        iv_rank     already 0–100
        vrp         [-0.5, +0.5] → [0, 100]
        iv_rv_ratio [0.5, 2.0]   → [0, 100]
        z_score     [-3, +3]     → [0, 100]
    """
    _DEFAULT_WEIGHTS = {
        "iv_rank":     0.35,
        "vrp":         0.25,
        "iv_rv_ratio": 0.25,
        "z_score":     0.15,
    }

    w = weights if weights is not None else _DEFAULT_WEIGHTS

    # Re-normalise so weights always sum to 1
    total_w = sum(w.values())
    if total_w <= 0:
        raise ValueError("weights must sum to a positive number")
    w = {k: v / total_w for k, v in w.items()}

    def _safe(val, default: float) -> float:
        return default if (val is None or not math.isfinite(float(val))) else float(val)

    iv_rank_val = _safe(inputs.get("iv_rank"),     50.0)
    vrp_val     = _safe(inputs.get("vrp"),          0.0)
    ratio_val   = _safe(inputs.get("iv_rv_ratio"),  1.0)
    z_val       = _safe(inputs.get("z_score"),      0.0)

    # Normalise each component to 0–100
    rank_score  = float(np.clip(iv_rank_val, 0, 100))
    vrp_score   = float(np.clip((vrp_val  + 0.5) / 1.0  * 100, 0, 100))
    ratio_score = float(np.clip((ratio_val - 0.5) / 1.5  * 100, 0, 100))
    z_score_val = float(np.clip((z_val    + 3.0)  / 6.0  * 100, 0, 100))

    component_map = {
        "iv_rank":     rank_score,
        "vrp":         vrp_score,
        "iv_rv_ratio": ratio_score,
        "z_score":     z_score_val,
    }

    score = sum(w.get(k, 0.0) * v for k, v in component_map.items())
    return round(float(np.clip(score, 0.0, 100.0)), 2)


# ---------------------------------------------------------------------------
# trade_recommendation  (unchanged — not a calculation)
# ---------------------------------------------------------------------------

def trade_recommendation(score: float) -> dict:
    """
    Map a regime score to a trade recommendation.

    Thresholds
    ----------
    0  – 30 : Buy Premium
    30 – 50 : Neutral
    50 – 70 : Sell Premium Selectively
    70 – 100: Aggressive Premium Selling
    """
    if not math.isfinite(score):
        raise ValueError(f"score must be finite, got {score!r}")
    if not (0.0 <= score <= 100.0):
        raise ValueError(f"score must be in [0, 100], got {score}")

    if score < 30:
        return {
            "signal":      "BUY_PREMIUM",
            "label":       "Buy Premium",
            "color":       "green",
            "description": "IV is compressed. Long gamma / long straddles / calendar spreads.",
        }
    if score < 50:
        return {
            "signal":      "NEUTRAL",
            "label":       "Neutral",
            "color":       "yellow",
            "description": "Mixed signals. Reduce size, use defined-risk spreads.",
        }
    if score < 70:
        return {
            "signal":      "SELL_SELECTIVE",
            "label":       "Sell Premium Selectively",
            "color":       "orange",
            "description": "IV moderately elevated. Iron condors, covered calls, CSPs on strength.",
        }
    return {
        "signal":      "SELL_AGGRESSIVE",
        "label":       "Aggressive Premium Selling",
        "color":       "red",
        "description": "IV highly elevated. Short strangles, large iron condors, sell across term structure.",
    }
