"""Core volatility calculations. All functions are pure — no I/O."""

import numpy as np
import pandas as pd
from typing import Optional


def realized_vol(prices: pd.Series, window: int = 30, annualize: bool = True) -> float:
    """Close-to-close realized volatility."""
    log_returns = np.log(prices / prices.shift(1)).dropna()
    if len(log_returns) < window:
        return float("nan")
    rv = log_returns.iloc[-window:].std()
    return float(rv * np.sqrt(252) * 100) if annualize else float(rv)


def realized_vol_series(prices: pd.Series, window: int = 30) -> pd.Series:
    """Rolling realized vol series (annualized %)."""
    log_returns = np.log(prices / prices.shift(1))
    return log_returns.rolling(window).std() * np.sqrt(252) * 100


def iv_rv_ratio(iv30: float, rv30: float) -> Optional[float]:
    if rv30 == 0 or np.isnan(rv30):
        return None
    return iv30 / rv30


def vol_risk_premium(iv30: float, rv30: float) -> Optional[float]:
    """VRP = (IV30 - RV30) / RV30 expressed as a fraction."""
    if rv30 == 0 or np.isnan(rv30):
        return None
    return (iv30 - rv30) / rv30


def iv_rank(current_iv: float, iv_series: pd.Series) -> Optional[float]:
    """IVR over trailing 252 trading days (0–100)."""
    year = iv_series.dropna().iloc[-252:] if len(iv_series) >= 252 else iv_series.dropna()
    if year.empty:
        return None
    low, high = year.min(), year.max()
    if high == low:
        return 50.0
    return float((current_iv - low) / (high - low) * 100)


def iv_rv_zscore(iv_series: pd.Series, rv_series: pd.Series, lookback: int = 252) -> Optional[float]:
    """Z-score of current IV-RV spread vs trailing history."""
    spread = (iv_series - rv_series).dropna()
    if len(spread) < 30:
        return None
    history = spread.iloc[-lookback:]
    current = spread.iloc[-1]
    return float((current - history.mean()) / history.std()) if history.std() != 0 else 0.0


def term_structure_slopes(iv7: float, iv30: float, iv60: float) -> dict:
    return {
        "front_slope": round(iv30 - iv7, 2),   # positive = normal contango
        "back_slope": round(iv60 - iv30, 2),
        "total_slope": round(iv60 - iv7, 2),
    }


def vol_regime_score(
    iv_rank_val: float,
    vrp: float,
    iv_rv_ratio_val: float,
    iv_rv_z: float,
) -> float:
    """Composite regime score 0–100. Higher = more elevated IV / better selling environment."""
    # Normalize each component to 0-100, then weight
    rank_score = np.clip(iv_rank_val, 0, 100)

    # VRP: -1 to +1 maps to 0-100
    vrp_score = np.clip((vrp + 0.5) / 1.0 * 100, 0, 100)

    # IV/RV ratio: 0.5 to 2.0 maps to 0-100
    ratio_score = np.clip((iv_rv_ratio_val - 0.5) / 1.5 * 100, 0, 100)

    # Z-score: -3 to +3 maps to 0-100
    z_score = np.clip((iv_rv_z + 3) / 6 * 100, 0, 100)

    score = 0.35 * rank_score + 0.25 * vrp_score + 0.25 * ratio_score + 0.15 * z_score
    return round(float(np.clip(score, 0, 100)), 1)


def trade_signal(regime_score: float) -> dict:
    if regime_score < 30:
        return {"signal": "BUY_PREMIUM", "label": "Buy Premium", "color": "green"}
    if regime_score < 50:
        return {"signal": "NEUTRAL", "label": "Neutral", "color": "yellow"}
    if regime_score < 70:
        return {"signal": "SELL_SELECTIVE", "label": "Sell Premium Selectively", "color": "orange"}
    return {"signal": "SELL_AGGRESSIVE", "label": "Aggressive Premium Selling", "color": "red"}


def skew_metrics(puts_iv: dict, calls_iv: dict) -> dict:
    """
    puts_iv / calls_iv: {delta: iv_value} e.g. {25: 18.5, 50: 16.0}
    Returns skew metrics.
    """
    skew_25d = puts_iv.get(25, 0) - calls_iv.get(25, 0)
    atm = (puts_iv.get(50, 0) + calls_iv.get(50, 0)) / 2
    return {
        "skew_25d": round(skew_25d, 2),
        "atm_iv": round(atm, 2),
        "put_25d": puts_iv.get(25),
        "call_25d": calls_iv.get(25),
    }
