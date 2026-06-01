import numpy as np
import pandas as pd
import pytest
from app.calculations import (
    realized_vol,
    iv_rv_ratio,
    vol_risk_premium,
    iv_rank,
    iv_rv_zscore,
    term_structure_slopes,
    vol_regime_score,
    trade_signal,
    skew_metrics,
)


def make_prices(n=400, seed=42):
    np.random.seed(seed)
    returns = np.random.normal(0.0003, 0.012, n)
    prices = 100 * np.exp(np.cumsum(returns))
    idx = pd.bdate_range(end="2024-12-31", periods=n)
    return pd.Series(prices, index=idx)


def make_iv_series(n=400, base=20.0, seed=7):
    np.random.seed(seed)
    idx = pd.bdate_range(end="2024-12-31", periods=n)
    return pd.Series(base + np.random.normal(0, 3, n), index=idx)


# --- realized_vol ---

def test_realized_vol_positive():
    prices = make_prices()
    rv = realized_vol(prices, 30)
    assert rv > 0

def test_realized_vol_annualized_range():
    prices = make_prices()
    rv = realized_vol(prices, 30)
    assert 5 < rv < 100  # reasonable annualized vol range

def test_realized_vol_insufficient_data():
    prices = make_prices(10)
    rv = realized_vol(prices, 30)
    assert np.isnan(rv)

def test_realized_vol_windows():
    prices = make_prices()
    for w in [10, 20, 30, 60]:
        rv = realized_vol(prices, w)
        assert not np.isnan(rv), f"window {w} returned NaN"


# --- iv_rv_ratio ---

def test_iv_rv_ratio_normal():
    assert abs(iv_rv_ratio(20.0, 15.0) - 20/15) < 1e-9

def test_iv_rv_ratio_zero_rv():
    assert iv_rv_ratio(20.0, 0.0) is None

def test_iv_rv_ratio_nan_rv():
    assert iv_rv_ratio(20.0, float("nan")) is None


# --- vol_risk_premium ---

def test_vrp_positive_when_iv_above_rv():
    vrp = vol_risk_premium(20.0, 15.0)
    assert vrp > 0

def test_vrp_negative_when_iv_below_rv():
    vrp = vol_risk_premium(14.0, 18.0)
    assert vrp < 0

def test_vrp_zero_rv():
    assert vol_risk_premium(20.0, 0.0) is None


# --- iv_rank ---

def test_iv_rank_bounds():
    iv_series = make_iv_series(300, base=20.0)
    ivr = iv_rank(20.0, iv_series)
    assert 0 <= ivr <= 100

def test_iv_rank_at_max():
    iv_series = pd.Series([10.0, 15.0, 20.0, 25.0, 30.0])
    assert iv_rank(30.0, iv_series) == pytest.approx(100.0)

def test_iv_rank_at_min():
    iv_series = pd.Series([10.0, 15.0, 20.0, 25.0, 30.0])
    assert iv_rank(10.0, iv_series) == pytest.approx(0.0)

def test_iv_rank_flat_series():
    iv_series = pd.Series([20.0] * 50)
    assert iv_rank(20.0, iv_series) == pytest.approx(50.0)


# --- iv_rv_zscore ---

def test_iv_rv_zscore_returns_float():
    prices = make_prices()
    iv_series = make_iv_series()
    from app.calculations import realized_vol_series
    rv_series = realized_vol_series(prices, 30)
    common = iv_series.index.intersection(rv_series.index)
    z = iv_rv_zscore(iv_series.loc[common], rv_series.loc[common])
    assert isinstance(z, float)

def test_iv_rv_zscore_insufficient_data():
    iv = pd.Series([20.0] * 10)
    rv = pd.Series([15.0] * 10)
    assert iv_rv_zscore(iv, rv) is None


# --- term_structure_slopes ---

def test_term_structure_normal_contango():
    slopes = term_structure_slopes(iv7=14.0, iv30=16.0, iv60=18.0)
    assert slopes["front_slope"] > 0
    assert slopes["back_slope"] > 0
    assert slopes["total_slope"] > 0

def test_term_structure_inverted():
    slopes = term_structure_slopes(iv7=22.0, iv30=18.0, iv60=15.0)
    assert slopes["front_slope"] < 0
    assert slopes["back_slope"] < 0

def test_term_structure_values():
    slopes = term_structure_slopes(10.0, 15.0, 20.0)
    assert slopes["front_slope"] == 5.0
    assert slopes["back_slope"] == 5.0
    assert slopes["total_slope"] == 10.0


# --- vol_regime_score ---

def test_regime_score_bounds():
    score = vol_regime_score(iv_rank_val=80, vrp=0.3, iv_rv_ratio_val=1.4, iv_rv_z=1.5)
    assert 0 <= score <= 100

def test_regime_score_high_iv():
    score = vol_regime_score(iv_rank_val=90, vrp=0.5, iv_rv_ratio_val=1.8, iv_rv_z=2.5)
    assert score > 65

def test_regime_score_low_iv():
    score = vol_regime_score(iv_rank_val=5, vrp=-0.2, iv_rv_ratio_val=0.7, iv_rv_z=-2.0)
    assert score < 35


# --- trade_signal ---

def test_signal_buy_premium():
    sig = trade_signal(20.0)
    assert sig["signal"] == "BUY_PREMIUM"

def test_signal_neutral():
    sig = trade_signal(40.0)
    assert sig["signal"] == "NEUTRAL"

def test_signal_sell_selective():
    sig = trade_signal(60.0)
    assert sig["signal"] == "SELL_SELECTIVE"

def test_signal_sell_aggressive():
    sig = trade_signal(80.0)
    assert sig["signal"] == "SELL_AGGRESSIVE"

def test_signal_boundaries():
    assert trade_signal(0.0)["signal"] == "BUY_PREMIUM"
    assert trade_signal(30.0)["signal"] == "NEUTRAL"
    assert trade_signal(50.0)["signal"] == "SELL_SELECTIVE"
    assert trade_signal(70.0)["signal"] == "SELL_AGGRESSIVE"
    assert trade_signal(100.0)["signal"] == "SELL_AGGRESSIVE"


# --- skew_metrics ---

def test_skew_put_skew_positive():
    result = skew_metrics(puts_iv={25: 20.0, 50: 17.0}, calls_iv={25: 15.0, 50: 17.0})
    assert result["skew_25d"] == pytest.approx(5.0)

def test_skew_atm():
    result = skew_metrics(puts_iv={25: 20.0, 50: 18.0}, calls_iv={25: 15.0, 50: 18.0})
    assert result["atm_iv"] == pytest.approx(18.0)
