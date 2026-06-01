"""Assembles mock data + calculations into validated API payloads."""

import math
import numpy as np
import pandas as pd

from app.calculations.engine import (
    realized_volatility,
    iv_rv_ratio,
    volatility_risk_premium,
    iv_rank,
    z_score,
    term_structure,
    regime_score,
    trade_recommendation,
)
from app.core.config import settings
from app.data.mock_provider import get_price_history, get_iv_data, get_macro_data


def _rolling_rv_series(prices: pd.Series, window: int = 30) -> pd.Series:
    log_ret = np.log(prices / prices.shift(1))
    return log_ret.rolling(window).std() * np.sqrt(252) * 100


def _safe(val: float, fallback: float) -> float:
    return fallback if (val is None or not math.isfinite(val)) else val


def build_ticker_snapshot(ticker: str) -> dict:
    days = settings.price_history_days
    prices = get_price_history(ticker, days=days)
    iv_df  = get_iv_data(ticker, days=days)

    rv10 = realized_volatility(prices, 10)
    rv20 = realized_volatility(prices, 20)
    rv30 = realized_volatility(prices, 30)
    rv60 = realized_volatility(prices, 60)

    current_iv7  = float(iv_df["iv7"].iloc[-1])
    current_iv30 = float(iv_df["iv30"].iloc[-1])
    current_iv60 = float(iv_df["iv60"].iloc[-1])
    current_iv90 = float(iv_df["iv90"].iloc[-1])

    ratio = _safe(iv_rv_ratio(current_iv30, rv30), 1.0)
    vrp   = _safe(volatility_risk_premium(current_iv30, rv30), 0.0)
    ivr   = _safe(iv_rank(current_iv30, iv_df["iv30"]), 50.0)

    rv30_series = _rolling_rv_series(prices, 30)
    common  = iv_df["iv30"].index.intersection(rv30_series.index)
    spread  = iv_df["iv30"].loc[common] - rv30_series.loc[common]
    current_z = _safe(float(z_score(spread.dropna(), lookback=252)[-1]), 0.0)

    ts    = term_structure(current_iv7, current_iv30, current_iv60, current_iv90)
    score = regime_score({
        "iv_rank":      ivr,
        "vrp":          vrp,
        "iv_rv_ratio":  ratio,
        "z_score":      current_z,
    })
    rec = trade_recommendation(score)

    rv_spark = rv30_series.dropna().iloc[-60:]
    iv_spark = iv_df["iv30"].dropna().iloc[-60:]
    idx = rv_spark.index.intersection(iv_spark.index)

    return {
        "ticker": ticker,
        "price":  round(float(prices.iloc[-1]), 2),
        "rv": {
            "rv10": round(_safe(rv10, 0.0), 2),
            "rv20": round(_safe(rv20, 0.0), 2),
            "rv30": round(_safe(rv30, 0.0), 2),
            "rv60": round(_safe(rv60, 0.0), 2),
        },
        "iv": {
            "iv7":  round(current_iv7,  2),
            "iv30": round(current_iv30, 2),
            "iv60": round(current_iv60, 2),
            "iv90": round(current_iv90, 2),
        },
        "iv_rank":      round(ivr, 1),
        "iv_rv_ratio":  round(ratio, 3),
        "vrp":          round(vrp, 4),
        "iv_rv_zscore": round(current_z, 2),
        "term_structure": ts,
        "skew": {
            "skew_25d": round(current_iv30 * 0.08, 2),
            "atm_iv":   round(current_iv30, 2),
            "put_25d":  round(current_iv30 * 1.08, 2),
            "call_25d": round(current_iv30 * 0.94, 2),
        },
        "regime_score": score,
        "signal":       rec,
        "sparklines": {
            "dates": [d.strftime("%Y-%m-%d") for d in idx],
            "rv30":  [round(float(v), 2) for v in rv_spark.loc[idx]],
            "iv30":  [round(float(v), 2) for v in iv_spark.loc[idx]],
        },
    }


def build_dashboard() -> dict:
    snapshots: dict = {}
    for t in settings.tickers:
        try:
            snapshots[t] = build_ticker_snapshot(t)
        except Exception as exc:
            snapshots[t] = {"ticker": t, "error": str(exc)}

    macro = get_macro_data()

    index_scores = [
        snapshots[t]["regime_score"]
        for t in settings.index_tickers
        if "regime_score" in snapshots.get(t, {})
    ]
    market_regime = round(float(np.mean(index_scores)), 1) if index_scores else 50.0

    return {
        "tickers":       snapshots,
        "macro":         macro,
        "market_regime": market_regime,
        "market_signal": trade_recommendation(market_regime),
    }
