"""
scripts/save_snapshot.py
------------------------
Seed the local SQLite database with synthetic historical data for every
supported ticker.

Run from the backend directory with:

    python -m scripts.save_snapshot

On the first run this inserts ~400 trading days of mock history per ticker
(~252 business days ≈ 1 year of data before today, configurable via DATA_DAYS).

Subsequent runs are idempotent — existing rows are updated in-place (UPSERT).

Phase 3: swap get_price_history / get_iv_data for live Polygon + ORATS calls.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

# ── Allow running as `python -m scripts.save_snapshot` from backend/ ─────────
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

from app.db.schema import init_db
from app.db.repository import (
    upsert_ticker,
    bulk_upsert_daily_prices,
    bulk_upsert_vol_snapshots,
    bulk_upsert_macro_snapshots,
    bulk_upsert_trade_signals,
)
from app.data.mock_provider import (
    TICKERS,
    get_price_history,
    get_iv_data,
    get_macro_data,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_DAYS = 400      # number of trading days to seed
LOOKBACK  = 252      # rolling window for IV Rank and z-score
_TDAYS    = 252      # annualisation constant

# ---------------------------------------------------------------------------
# Rolling helpers (same as backtesting engine — kept local to avoid import)
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


def _rolling_iv_rank(iv30: pd.Series, lookback: int) -> pd.Series:
    min_p = max(lookback // 4, 10)
    lo = iv30.rolling(lookback, min_periods=min_p).min()
    hi = iv30.rolling(lookback, min_periods=min_p).max()
    rng = hi - lo
    raw = np.where(rng > 0, (iv30 - lo) / rng * 100, 50.0)
    return pd.Series(raw, index=iv30.index)


def _rolling_zscore(iv30: pd.Series, rv30: pd.Series, window: int = 60) -> pd.Series:
    spread = iv30 - rv30
    mean   = spread.rolling(window, min_periods=max(window // 4, 10)).mean()
    std    = spread.rolling(window, min_periods=max(window // 4, 10)).std(ddof=1)
    z      = (spread - mean) / std.replace(0, np.nan)
    return z.fillna(0.0)


def _regime_score(iv_rank: float, iv: float, rv: float) -> float:
    vrp   = (iv - rv) / rv  if rv > 0 else 0.0
    ratio = iv / rv          if rv > 0 else 1.0
    rank_s  = float(np.clip(iv_rank, 0, 100))
    vrp_s   = float(np.clip((vrp  + 0.5) / 1.0 * 100, 0, 100))
    ratio_s = float(np.clip((ratio - 0.5) / 1.5 * 100, 0, 100))
    return round(0.35 * rank_s + 0.25 * vrp_s + 0.25 * ratio_s + 0.15 * 50.0, 2)


def _signal_label(score: float) -> str:
    if score < 30: return "BUY_PREMIUM"
    if score < 50: return "NEUTRAL"
    if score < 70: return "SELL_SELECTIVE"
    return "SELL_AGGRESSIVE"


# ---------------------------------------------------------------------------
# Seed one ticker
# ---------------------------------------------------------------------------

def _seed_ticker(ticker: str, ticker_id: int) -> None:
    print(f"  Seeding {ticker} …", end=" ", flush=True)

    prices = get_price_history(ticker, days=DATA_DAYS)
    iv_df  = get_iv_data(ticker,       days=DATA_DAYS)

    # Align on common trading-day index
    idx    = prices.index.intersection(iv_df.index)
    prices = prices.loc[idx]
    iv_df  = iv_df.loc[idx]

    # Rolling series (vectorised)
    rv10 = _rolling_rv(prices, 10)
    rv20 = _rolling_rv(prices, 20)
    rv30 = _rolling_rv(prices, 30)
    rv60 = _rolling_rv(prices, 60)
    ivr  = _rolling_iv_rank(iv_df["iv30"], LOOKBACK)
    zscore = _rolling_zscore(iv_df["iv30"], rv30, window=60)

    # Build row lists
    price_rows   : list[dict] = []
    vol_rows     : list[dict] = []
    signal_rows  : list[dict] = []

    for dt, price in prices.items():
        date_str = str(dt.date())
        iv30_val = float(iv_df.loc[dt, "iv30"])
        rv30_val = float(rv30.loc[dt]) if math.isfinite(float(rv30.loc[dt])) else None
        ivr_val  = float(ivr.loc[dt])  if math.isfinite(float(ivr.loc[dt]))  else 50.0
        z_val    = float(zscore.loc[dt])

        vrp_val  = ((iv30_val - rv30_val) / iv30_val
                    if rv30_val and rv30_val > 0 else None)
        ratio_val = (iv30_val / rv30_val
                     if rv30_val and rv30_val > 0 else None)
        rscore   = _regime_score(ivr_val, iv30_val, rv30_val or iv30_val)
        sig      = _signal_label(rscore)

        def _rv(s: pd.Series) -> float | None:
            v = float(s.loc[dt])
            return round(v, 4) if math.isfinite(v) else None

        price_rows.append({
            "ticker_id": ticker_id,
            "date":      date_str,
            "open":      round(float(price), 4),
            "high":      round(float(price) * 1.005, 4),  # synthetic ±0.5%
            "low":       round(float(price) * 0.995, 4),
            "close":     round(float(price), 4),
            "volume":    None,
        })

        vol_rows.append({
            "ticker_id":    ticker_id,
            "date":         date_str,
            "iv7":          round(float(iv_df.loc[dt, "iv7"]),  4),
            "iv30":         round(iv30_val, 4),
            "iv60":         round(float(iv_df.loc[dt, "iv60"]), 4),
            "iv90":         round(float(iv_df.loc[dt, "iv90"]), 4),
            "rv10":         _rv(rv10),
            "rv20":         _rv(rv20),
            "rv30":         round(rv30_val, 4) if rv30_val else None,
            "rv60":         _rv(rv60),
            "iv_rank":      round(ivr_val, 4),
            "iv_rv_ratio":  round(ratio_val, 4) if ratio_val else None,
            "vrp":          round(vrp_val,   4) if vrp_val   else None,
            "iv_rv_zscore": round(z_val,     4),
            "regime_score": rscore,
            "signal":       sig,
        })

        signal_rows.append({
            "ticker_id":    ticker_id,
            "date":         date_str,
            "signal":       sig,
            "regime_score": rscore,
            "iv_rank":      round(ivr_val, 4),
            "iv_rv_ratio":  round(ratio_val, 4) if ratio_val else None,
            "iv_rv_zscore": round(z_val,     4),
            "vrp":          round(vrp_val,   4) if vrp_val   else None,
        })

    bulk_upsert_daily_prices(price_rows)
    bulk_upsert_vol_snapshots(vol_rows)
    bulk_upsert_trade_signals(signal_rows)
    print(f"{len(price_rows)} rows")


# ---------------------------------------------------------------------------
# Seed macro snapshots (one row per trading day, market-wide)
# ---------------------------------------------------------------------------

def _seed_macro() -> None:
    print("  Seeding macro …", end=" ", flush=True)

    # Use SPY IV as VIX proxy for historical macro rows
    spy_prices = get_price_history("SPY", days=DATA_DAYS)
    spy_iv     = get_iv_data("SPY",       days=DATA_DAYS)
    idx        = spy_prices.index.intersection(spy_iv.index)

    # VIX ~ SPY IV30 * 1.05 + small noise (purely synthetic)
    rng = np.random.default_rng(seed=42)
    iv30 = spy_iv.loc[idx, "iv30"].values
    vix_series = iv30 * 1.05 + rng.normal(0, 0.4, len(idx))
    vix_series = np.clip(vix_series, 8.0, 80.0)

    def _vix_regime(vix: float) -> str:
        if vix < 15:  return "Low"
        if vix < 20:  return "Normal"
        if vix < 30:  return "Elevated"
        return "Spike"

    # Simple market regime: IV rank of SPY IV30
    rv30_spy = _rolling_rv(spy_prices.loc[idx], 30)
    ivr_spy  = _rolling_iv_rank(spy_iv.loc[idx, "iv30"], LOOKBACK)

    macro_rows: list[dict] = []
    for i, (dt, vix) in enumerate(zip(idx, vix_series)):
        iv30_v = float(spy_iv.loc[dt, "iv30"])
        rv30_v = float(rv30_spy.loc[dt])
        ivr_v  = float(ivr_spy.loc[dt]) if math.isfinite(float(ivr_spy.loc[dt])) else 50.0
        rscore = _regime_score(ivr_v, iv30_v, rv30_v if math.isfinite(rv30_v) else iv30_v)
        macro_rows.append({
            "date":          str(dt.date()),
            "vix":           round(float(vix), 2),
            "vix_regime":    _vix_regime(float(vix)),
            "market_regime": rscore,
            "market_signal": _signal_label(rscore),
        })

    bulk_upsert_macro_snapshots(macro_rows)
    print(f"{len(macro_rows)} rows")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("Vol Dashboard — snapshot seeder")
    print(f"  DB path : {Path(__file__).parent.parent / 'data' / 'vol_dashboard.db'}")
    print(f"  Tickers : {TICKERS}")
    print(f"  History : {DATA_DAYS} trading days\n")

    # Init schema (idempotent)
    init_db()
    print("Schema ready.\n")

    # Seed each ticker
    for ticker in TICKERS:
        tid = upsert_ticker(ticker)
        _seed_ticker(ticker, tid)

    # Seed macro
    _seed_macro()

    print("\nDone.")


if __name__ == "__main__":
    main()
