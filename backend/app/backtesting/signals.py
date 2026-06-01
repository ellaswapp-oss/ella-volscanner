"""
backtesting/signals.py
----------------------
Signal generators for option volatility strategies.

Each function accepts aligned pd.Series inputs and returns a pd.Series of
Signal enum values with the same index.

Signals
-------
SELL_PREMIUM  → environment favours short vol (collect theta/vega)
BUY_PREMIUM   → environment favours long vol (own gamma)
NEUTRAL       → no clear edge — stay flat

Signal generators
-----------------
iv_rv_ratio_signals     IV/RV ratio threshold
iv_rank_signals         rolling IV Rank threshold
term_structure_signals  contango vs inversion of the vol surface
vix_regime_signals      absolute IV level (SPY IV30 ≈ VIX)
"""

from __future__ import annotations

from enum import Enum

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Signal enum
# ---------------------------------------------------------------------------

class Signal(str, Enum):
    SELL_PREMIUM = "SELL_PREMIUM"
    BUY_PREMIUM  = "BUY_PREMIUM"
    NEUTRAL      = "NEUTRAL"


# ---------------------------------------------------------------------------
# IV / RV Ratio signal
# ---------------------------------------------------------------------------

def iv_rv_ratio_signals(
    iv30: pd.Series,
    rv30: pd.Series,
    *,
    sell_threshold: float = 1.2,
    buy_threshold: float | None = None,
) -> pd.Series:
    """
    Generate signals based on IV30 / RV30.

    Parameters
    ----------
    iv30, rv30 : pd.Series
        Implied and realised vol series (same index, annualised %).
    sell_threshold : float, default 1.2
        IV/RV > sell_threshold → SELL_PREMIUM (IV expensive vs realised).
    buy_threshold : float | None, default 1 / sell_threshold
        IV/RV < buy_threshold → BUY_PREMIUM (IV cheap vs realised).

    Notes
    -----
    A ratio of 1.0 means IV = RV exactly (break-even on premium).
    Historical VRP research suggests IV trades at ~1.1–1.3× RV on average.
    """
    if buy_threshold is None:
        buy_threshold = 1.0 / sell_threshold

    ratio = iv30 / rv30.replace(0.0, np.nan)

    signal = pd.Series(Signal.NEUTRAL, index=iv30.index, name="signal")
    signal[ratio > sell_threshold]  = Signal.SELL_PREMIUM
    signal[ratio < buy_threshold]   = Signal.BUY_PREMIUM
    return signal


# ---------------------------------------------------------------------------
# IV Rank signal
# ---------------------------------------------------------------------------

def iv_rank_signals(
    iv30: pd.Series,
    *,
    sell_threshold: float = 50.0,
    buy_threshold: float | None = None,
    lookback: int = 252,
) -> pd.Series:
    """
    Generate signals based on rolling IV Rank (0–100).

    Parameters
    ----------
    iv30 : pd.Series
        Implied vol series (annualised %).
    sell_threshold : float, default 50
        IV Rank > sell_threshold → SELL_PREMIUM.
    buy_threshold : float | None, default 100 - sell_threshold
        IV Rank < buy_threshold → BUY_PREMIUM.
    lookback : int, default 252
        Rolling window for IV Rank computation.

    Notes
    -----
    IV Rank = (current − period_low) / (period_high − period_low) × 100
    Above 50 means IV is in the upper half of its 1-year range.
    """
    if buy_threshold is None:
        buy_threshold = 100.0 - sell_threshold

    min_periods = max(lookback // 4, 10)

    roll_lo = iv30.rolling(lookback, min_periods=min_periods).min()
    roll_hi = iv30.rolling(lookback, min_periods=min_periods).max()
    range_  = roll_hi - roll_lo

    # Avoid divide-by-zero on flat series → default 50
    ivr = np.where(range_ > 0, (iv30 - roll_lo) / range_ * 100, 50.0)
    ivr_series = pd.Series(ivr, index=iv30.index)

    signal = pd.Series(Signal.NEUTRAL, index=iv30.index, name="signal")
    signal[ivr_series > sell_threshold] = Signal.SELL_PREMIUM
    signal[ivr_series < buy_threshold]  = Signal.BUY_PREMIUM
    return signal


# ---------------------------------------------------------------------------
# Term structure signal
# ---------------------------------------------------------------------------

def term_structure_signals(
    iv7: pd.Series,
    iv30: pd.Series,
    *,
    contango_min: float = 0.0,
    inversion_min: float = 0.0,
) -> pd.Series:
    """
    Generate signals from the front of the vol term structure (IV7 vs IV30).

    Parameters
    ----------
    iv7, iv30 : pd.Series
        7-day and 30-day implied vols (same index, annualised %).
    contango_min : float, default 0.0
        Front slope (IV30 − IV7) must exceed this to trigger SELL_PREMIUM.
    inversion_min : float, default 0.0
        Front slope must be below −inversion_min to trigger BUY_PREMIUM.

    Notes
    -----
    Normal market: IV30 > IV7 (contango — upward sloping).
    Stressed market: IV7 > IV30 (inversion — front vol spiked).

    Contango → premium selling favoured (carry is positive: you sell time value).
    Inversion → premium buying favoured (short-dated vol is elevated, may fall).
    """
    slope = iv30 - iv7   # positive = contango

    signal = pd.Series(Signal.NEUTRAL, index=iv7.index, name="signal")
    signal[slope >  contango_min]   = Signal.SELL_PREMIUM
    signal[slope < -inversion_min]  = Signal.BUY_PREMIUM
    return signal


# ---------------------------------------------------------------------------
# VIX / Absolute-IV-level signal
# ---------------------------------------------------------------------------

def vix_regime_signals(
    iv30_spy: pd.Series,
    *,
    low_threshold: float = 15.0,
    elevated_threshold: float = 20.0,
    spike_threshold: float = 35.0,
) -> pd.Series:
    """
    Generate signals based on absolute SPY IV30 level (≈ VIX).

    Parameters
    ----------
    iv30_spy : pd.Series
        SPY 30-day implied vol as VIX proxy (annualised %).
    low_threshold : float, default 15
        IV30 < low_threshold → BUY_PREMIUM (vol historically cheap).
    elevated_threshold : float, default 20
        IV30 > elevated_threshold → SELL_PREMIUM (vol historically expensive).
    spike_threshold : float, default 35
        IV30 > spike_threshold → BUY_PREMIUM override (fade the spike;
        extreme vol events tend to mean-revert — buying calls is cheap).

    Notes
    -----
    Zones (VIX equivalent):
        < 15  Low vol     → BUY  (IV may rise back toward normal)
        15–20 Normal      → NEUTRAL
        20–35 Elevated    → SELL (collect premium above historical mean)
        > 35  Spike       → BUY  (fade the spike — vol mean-reverts)

    Phase 3: replace SPY IV30 with actual CBOE VIX time series via FRED.
    """
    signal = pd.Series(Signal.NEUTRAL, index=iv30_spy.index, name="signal")

    # Order matters — spike check overrides elevated check
    signal[iv30_spy > elevated_threshold] = Signal.SELL_PREMIUM
    signal[iv30_spy > spike_threshold]    = Signal.BUY_PREMIUM   # override: fade spike
    signal[iv30_spy < low_threshold]      = Signal.BUY_PREMIUM
    return signal
