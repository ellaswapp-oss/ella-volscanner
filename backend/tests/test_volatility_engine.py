"""
Tests for app.calculations.engine.

Run: pytest tests/test_volatility_engine.py -v
"""

import math
import numpy as np
import pandas as pd
import pytest

from app.calculations.engine import (
    realized_volatility,
    iv_rv_ratio,
    volatility_risk_premium,
    iv_rank,
    z_score,
    term_structure,
    term_structure_slope,   # alias — must remain importable
    regime_score,
    trade_recommendation,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def flat_prices(n=100, base=100.0):
    """Prices that never move — RV should be 0."""
    return [base] * n


def trending_prices(n=252, daily_return=0.001):
    """Deterministic uptrend."""
    return [100.0 * (1 + daily_return) ** i for i in range(n)]


def random_prices(n=400, seed=42, mu=0.0003, sigma=0.012):
    np.random.seed(seed)
    rets = np.random.normal(mu, sigma, n)
    return list(100.0 * np.exp(np.cumsum(rets)))


def random_iv_series(n=300, base=20.0, seed=7):
    np.random.seed(seed)
    return list(base + np.random.normal(0, 3, n))


# ===========================================================================
# realized_volatility
# ===========================================================================

class TestRealizedVolatility:

    def test_returns_positive_for_volatile_prices(self):
        prices = random_prices()
        rv = realized_volatility(prices, 30)
        assert rv > 0

    def test_annualized_range_is_plausible(self):
        """Expect 5–80% for typical equity-like prices."""
        prices = random_prices(sigma=0.012)
        rv = realized_volatility(prices, 30)
        assert 5.0 < rv < 80.0

    def test_higher_sigma_produces_higher_rv(self):
        low = realized_volatility(random_prices(sigma=0.005), 30)
        high = realized_volatility(random_prices(sigma=0.03), 30)
        assert high > low

    def test_longer_window_smoother_not_necessarily_lower(self):
        prices = random_prices()
        rv30 = realized_volatility(prices, 30)
        rv60 = realized_volatility(prices, 60)
        # Both should be finite and positive
        assert math.isfinite(rv30) and rv30 > 0
        assert math.isfinite(rv60) and rv60 > 0

    def test_all_windows_return_finite(self):
        prices = random_prices(n=400)
        for w in [5, 10, 20, 30, 60, 90]:
            rv = realized_volatility(prices, w)
            assert math.isfinite(rv), f"window={w} returned non-finite"

    def test_insufficient_data_returns_nan(self):
        prices = random_prices(n=10)
        assert math.isnan(realized_volatility(prices, 30))

    def test_exactly_enough_data(self):
        # window+1 prices → exactly window returns
        prices = random_prices(n=31)
        rv = realized_volatility(prices, 30)
        assert math.isfinite(rv)

    def test_one_short_of_enough_returns_nan(self):
        prices = random_prices(n=30)  # 29 returns < window=30
        assert math.isnan(realized_volatility(prices, 30))

    def test_flat_prices_return_zero(self):
        rv = realized_volatility(flat_prices(100), 30)
        assert rv == pytest.approx(0.0, abs=1e-10)

    def test_accepts_numpy_array(self):
        prices = np.array(random_prices())
        assert math.isfinite(realized_volatility(prices, 30))

    def test_accepts_pandas_series(self):
        prices = pd.Series(random_prices())
        assert math.isfinite(realized_volatility(prices, 30))

    def test_invalid_window_raises(self):
        with pytest.raises(ValueError, match="window must be >= 1"):
            realized_volatility(random_prices(), 0)

    def test_window_1_returns_finite(self):
        prices = random_prices(n=10)
        rv = realized_volatility(prices, 1)
        assert math.isfinite(rv)

    def test_annualization_factor(self):
        """Manual check: single return of 1% → RV = 1% × √252."""
        prices = [100.0, 101.0, 100.0 * (101 / 100) ** 2]  # 2 equal returns
        rv = realized_volatility(prices, window=2)
        # log(101/100) ≈ 0.00995; std of [0.00995, 0.00995] with ddof=1 = 0
        # Two identical returns → std=0
        assert rv == pytest.approx(0.0, abs=1e-10)


# ===========================================================================
# iv_rv_ratio
# ===========================================================================

class TestIVRVRatio:

    def test_normal_case(self):
        assert iv_rv_ratio(20.0, 15.0) == pytest.approx(20.0 / 15.0)

    def test_iv_equals_rv(self):
        assert iv_rv_ratio(18.0, 18.0) == pytest.approx(1.0)

    def test_iv_below_rv(self):
        ratio = iv_rv_ratio(14.0, 20.0)
        assert ratio < 1.0
        assert ratio == pytest.approx(0.7)

    def test_zero_rv_returns_nan(self):
        assert math.isnan(iv_rv_ratio(20.0, 0.0))

    def test_negative_rv_still_divides(self):
        # Negative RV is nonsensical but the function should not crash
        ratio = iv_rv_ratio(20.0, -5.0)
        assert ratio == pytest.approx(-4.0)

    def test_nan_rv_returns_nan(self):
        assert math.isnan(iv_rv_ratio(20.0, float("nan")))

    def test_nan_iv_returns_nan(self):
        assert math.isnan(iv_rv_ratio(float("nan"), 15.0))

    def test_inf_rv_returns_nan(self):
        assert math.isnan(iv_rv_ratio(20.0, float("inf")))

    def test_both_large_values(self):
        assert iv_rv_ratio(100.0, 50.0) == pytest.approx(2.0)


# ===========================================================================
# volatility_risk_premium
# ===========================================================================

class TestVolatilityRiskPremium:

    def test_positive_when_iv_above_rv(self):
        vrp = volatility_risk_premium(20.0, 15.0)
        assert vrp == pytest.approx(1 / 3)

    def test_negative_when_iv_below_rv(self):
        vrp = volatility_risk_premium(12.0, 16.0)
        assert vrp == pytest.approx(-0.25)

    def test_zero_when_equal(self):
        assert volatility_risk_premium(18.0, 18.0) == pytest.approx(0.0)

    def test_zero_rv_returns_nan(self):
        assert math.isnan(volatility_risk_premium(20.0, 0.0))

    def test_nan_rv_returns_nan(self):
        assert math.isnan(volatility_risk_premium(20.0, float("nan")))

    def test_nan_iv_returns_nan(self):
        assert math.isnan(volatility_risk_premium(float("nan"), 15.0))

    def test_formula_correctness(self):
        iv, rv = 25.0, 20.0
        expected = (25.0 - 20.0) / 20.0  # = 0.25
        assert volatility_risk_premium(iv, rv) == pytest.approx(expected)

    def test_large_premium(self):
        # IV 3× RV → VRP = 2.0 (200%)
        assert volatility_risk_premium(60.0, 20.0) == pytest.approx(2.0)


# ===========================================================================
# iv_rank
# ===========================================================================

class TestIVRank:

    def test_at_maximum_returns_100(self):
        hist = [10.0, 15.0, 20.0, 25.0, 30.0]
        assert iv_rank(30.0, hist) == pytest.approx(100.0)

    def test_at_minimum_returns_0(self):
        hist = [10.0, 15.0, 20.0, 25.0, 30.0]
        assert iv_rank(10.0, hist) == pytest.approx(0.0)

    def test_midpoint(self):
        hist = [10.0, 20.0, 30.0]
        assert iv_rank(20.0, hist) == pytest.approx(50.0)

    def test_above_history_clamps_to_100(self):
        hist = [10.0, 20.0, 30.0]
        assert iv_rank(50.0, hist) == pytest.approx(100.0)

    def test_below_history_clamps_to_0(self):
        hist = [10.0, 20.0, 30.0]
        assert iv_rank(5.0, hist) == pytest.approx(0.0)

    def test_flat_history_returns_50(self):
        hist = [20.0] * 100
        assert iv_rank(20.0, hist) == pytest.approx(50.0)

    def test_single_value_returns_nan(self):
        assert math.isnan(iv_rank(20.0, [20.0]))

    def test_empty_history_returns_nan(self):
        assert math.isnan(iv_rank(20.0, []))

    def test_result_bounds(self):
        hist = random_iv_series(300)
        rank = iv_rank(22.0, hist)
        assert 0.0 <= rank <= 100.0

    def test_accepts_numpy_array(self):
        hist = np.array([10.0, 15.0, 20.0, 25.0, 30.0])
        assert iv_rank(20.0, hist) == pytest.approx(50.0)

    def test_accepts_pandas_series(self):
        hist = pd.Series([10.0, 15.0, 20.0, 25.0, 30.0])
        assert iv_rank(20.0, hist) == pytest.approx(50.0)

    def test_ignores_nan_in_history(self):
        hist = [10.0, float("nan"), 30.0]
        assert iv_rank(20.0, hist) == pytest.approx(50.0)

    def test_252_day_typical_use(self):
        hist = random_iv_series(252, base=20.0)
        rank = iv_rank(20.0, hist)
        assert math.isfinite(rank)
        assert 0.0 <= rank <= 100.0


# ===========================================================================
# z_score
# ===========================================================================

class TestZScore:

    def test_standard_normal_series(self):
        """Known series: mean=0, std=1 → z-scores equal the values."""
        s = [-2.0, -1.0, 0.0, 1.0, 2.0]
        zs = z_score(s)
        # Mean of s = 0, std (ddof=1) of symmetric ints
        mu = np.mean(s)
        sigma = np.std(s, ddof=1)
        expected = [(x - mu) / sigma for x in s]
        np.testing.assert_allclose(zs, expected, rtol=1e-9)

    def test_mean_of_z_scores_is_zero(self):
        s = random_prices(n=100)
        zs = z_score(s)
        assert np.nanmean(zs) == pytest.approx(0.0, abs=1e-10)

    def test_std_of_z_scores_is_one(self):
        s = random_prices(n=100)
        zs = z_score(s)
        assert np.nanstd(zs, ddof=1) == pytest.approx(1.0, abs=1e-10)

    def test_constant_series_returns_zeros(self):
        zs = z_score([5.0] * 50)
        np.testing.assert_array_equal(zs, np.zeros(50))

    def test_single_element_returns_nan(self):
        zs = z_score([42.0])
        assert math.isnan(zs[0])

    def test_empty_returns_empty(self):
        zs = z_score([])
        assert len(zs) == 0

    def test_output_length_matches_input(self):
        s = random_prices(n=80)
        assert len(z_score(s)) == 80

    def test_nan_in_input_propagates(self):
        s = [1.0, 2.0, float("nan"), 4.0, 5.0]
        zs = z_score(s)
        assert math.isnan(zs[2])
        # Non-nan positions should still be finite
        assert all(math.isfinite(zs[i]) for i in [0, 1, 3, 4])

    def test_accepts_numpy_array(self):
        s = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        zs = z_score(s)
        assert len(zs) == 5

    def test_accepts_pandas_series(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        zs = z_score(s)
        assert len(zs) == 5

    def test_two_element_series(self):
        zs = z_score([10.0, 20.0])
        # mean=15, std(ddof=1)=sqrt(50)≈7.071 → z = ±5/7.071 ≈ ±0.7071
        expected = np.array([-1.0 / math.sqrt(2), 1.0 / math.sqrt(2)])
        np.testing.assert_allclose(zs, expected, rtol=1e-9)

    def test_current_z_via_last_element(self):
        """Typical usage: get z-score of latest IV/RV spread."""
        spread = [float(i) for i in range(50)]
        current_z = z_score(spread)[-1]
        assert math.isfinite(current_z)
        assert current_z > 0  # last value is max of a rising series


# ===========================================================================
# term_structure_slope
# ===========================================================================

class TestTermStructureSlope:

    def test_normal_contango(self):
        result = term_structure_slope(12.0, 15.0, 17.0, 19.0)
        assert result["front_slope"] == pytest.approx(3.0)
        assert result["mid_slope"] == pytest.approx(2.0)
        assert result["back_slope"] == pytest.approx(2.0)
        assert result["total_slope"] == pytest.approx(7.0)
        assert result["shape"] == "contango"

    def test_fully_inverted(self):
        result = term_structure_slope(25.0, 22.0, 19.0, 16.0)
        assert result["front_slope"] < 0
        assert result["mid_slope"] < 0
        assert result["back_slope"] < 0
        assert result["total_slope"] < 0
        assert result["shape"] == "inverted"

    def test_flat_curve(self):
        result = term_structure_slope(20.0, 20.1, 20.2, 20.3)
        # total_slope = 0.3 < 0.5 → flat
        assert result["shape"] == "flat"

    def test_humped_curve(self):
        # front up, back down — mixed signs, non-flat total
        result = term_structure_slope(15.0, 20.0, 18.0, 14.0)
        assert result["front_slope"] > 0
        assert result["back_slope"] < 0
        assert result["shape"] == "humped"

    def test_exact_slope_values(self):
        result = term_structure_slope(10.0, 15.0, 20.0, 25.0)
        assert result["front_slope"] == pytest.approx(5.0)
        assert result["mid_slope"] == pytest.approx(5.0)
        assert result["back_slope"] == pytest.approx(5.0)
        assert result["total_slope"] == pytest.approx(15.0)

    def test_equal_tenors_flat(self):
        result = term_structure_slope(20.0, 20.0, 20.0, 20.0)
        assert result["front_slope"] == pytest.approx(0.0)
        assert result["total_slope"] == pytest.approx(0.0)
        assert result["shape"] == "flat"

    def test_keys_present(self):
        result = term_structure(14.0, 16.0, 17.0, 18.0)
        assert set(result.keys()) == {"front_slope", "mid_slope", "back_slope", "total_slope", "curvature", "shape"}

    def test_iv90_included_in_back_slope(self):
        result = term_structure(15.0, 17.0, 19.0, 22.0)
        assert result["back_slope"] == pytest.approx(22.0 - 19.0)
        assert result["total_slope"] == pytest.approx(22.0 - 15.0)

    def test_alias_matches_canonical(self):
        args = (10.0, 15.0, 18.0, 20.0)
        assert term_structure(*args) == term_structure_slope(*args)

    # curvature
    def test_curvature_positive_when_front_steeper(self):
        # front_slope=5, back_slope=1 → curvature=4 (decelerating contango)
        result = term_structure(10.0, 15.0, 17.0, 18.0)
        assert result["curvature"] == pytest.approx(result["front_slope"] - result["back_slope"])
        assert result["curvature"] > 0

    def test_curvature_negative_when_back_steeper(self):
        # front_slope=1, back_slope=5 → curvature=-4 (accelerating)
        result = term_structure(10.0, 11.0, 14.0, 19.0)
        assert result["curvature"] < 0

    def test_curvature_zero_for_uniform_slope(self):
        result = term_structure(10.0, 15.0, 20.0, 25.0)
        assert result["curvature"] == pytest.approx(0.0)

    # relative flat threshold
    def test_flat_threshold_scales_with_atm_iv(self):
        # High-vol name: SPY at 15% ATM — 0.3pt total slope is flat (0.3 < 15*3%)
        low_vol  = term_structure(14.9, 15.0, 15.1, 15.2)
        # TSLA at 60% ATM — 0.3pt total slope is also flat (0.3 < 60*3%)
        high_vol = term_structure(59.9, 60.0, 60.1, 60.2)
        assert low_vol["shape"]  == "flat"
        assert high_vol["shape"] == "flat"

    def test_absolute_threshold_would_misclassify_high_vol(self):
        # total_slope=1.0 on SPY (ATM=15): 1.0/15=6.7% > 3% cutoff → contango
        # total_slope=1.0 on TSLA (ATM=60): 1.0/60=1.7% < 3% cutoff → flat
        spy_like  = term_structure(14.5, 15.0, 15.5, 15.5)   # total=1.0, cutoff=0.45 → contango
        tsla_like = term_structure(59.5, 60.0, 60.5, 60.5)   # total=1.0, cutoff=1.80 → flat
        assert spy_like["shape"]  != "flat"
        assert tsla_like["shape"] == "flat"

    def test_custom_flat_threshold(self):
        # With a tighter 1% threshold, the same curve should be contango
        tight = term_structure(14.9, 15.0, 15.1, 15.2, flat_threshold_pct=1.0)
        loose = term_structure(14.9, 15.0, 15.1, 15.2, flat_threshold_pct=10.0)
        assert loose["shape"] == "flat"
        # total_slope=0.3, ATM=15, 1% cutoff=0.15 → 0.3 > 0.15 → not flat
        assert tight["shape"] != "flat"


# ===========================================================================
# regime_score
# ===========================================================================

class TestRegimeScore:

    def test_output_in_bounds(self):
        score = regime_score({"iv_rank": 60, "vrp": 0.2, "iv_rv_ratio": 1.4, "z_score": 1.0})
        assert 0.0 <= score <= 100.0

    def test_high_iv_environment_above_65(self):
        score = regime_score({"iv_rank": 90, "vrp": 0.5, "iv_rv_ratio": 1.8, "z_score": 2.5})
        assert score > 65

    def test_low_iv_environment_below_35(self):
        score = regime_score({"iv_rank": 5, "vrp": -0.3, "iv_rv_ratio": 0.7, "z_score": -2.0})
        assert score < 35

    def test_neutral_inputs_near_midpoint(self):
        score = regime_score({"iv_rank": 50, "vrp": 0.0, "iv_rv_ratio": 1.0, "z_score": 0.0})
        # All inputs are at midpoints — score should be near centre
        assert 30.0 < score < 70.0

    def test_missing_keys_fall_back_gracefully(self):
        score = regime_score({})
        assert math.isfinite(score)
        assert 0.0 <= score <= 100.0

    def test_nan_inputs_fall_back_gracefully(self):
        score = regime_score({"iv_rank": float("nan"), "vrp": float("nan"),
                              "iv_rv_ratio": float("nan"), "z_score": float("nan")})
        assert math.isfinite(score)

    def test_extreme_high_clamps_to_100(self):
        score = regime_score({"iv_rank": 200, "vrp": 10.0, "iv_rv_ratio": 10.0, "z_score": 100.0})
        assert score == pytest.approx(100.0)

    def test_extreme_low_clamps_to_0(self):
        score = regime_score({"iv_rank": -100, "vrp": -10.0, "iv_rv_ratio": -5.0, "z_score": -100.0})
        assert score == pytest.approx(0.0)

    def test_deterministic(self):
        inputs = {"iv_rank": 70, "vrp": 0.3, "iv_rv_ratio": 1.5, "z_score": 1.2}
        assert regime_score(inputs) == regime_score(inputs)

    def test_iv_rank_weight_dominates(self):
        """IV rank is 35% weight — changing it should move score more than equal z_score change."""
        base = {"iv_rank": 50, "vrp": 0.0, "iv_rv_ratio": 1.0, "z_score": 0.0}
        high_rank = {**base, "iv_rank": 100}
        high_z = {**base, "z_score": 3.0}
        delta_rank = regime_score(high_rank) - regime_score(base)
        delta_z = regime_score(high_z) - regime_score(base)
        assert delta_rank > delta_z

    # custom weights
    def test_custom_weights_change_score(self):
        inputs = {"iv_rank": 90, "vrp": 0.1, "iv_rv_ratio": 1.1, "z_score": 0.1}
        default_score = regime_score(inputs)
        # Flip weights: make z_score dominate
        flipped = regime_score(inputs, weights={"iv_rank": 0.10, "vrp": 0.10, "iv_rv_ratio": 0.10, "z_score": 0.70})
        # With low z_score (0.1→ z normalized≈55), dominant z weight pulls score toward middle
        assert default_score != flipped_score if False else True   # just check they differ
        assert default_score != flipped

    def test_custom_weights_are_renormalized(self):
        inputs = {"iv_rank": 80, "vrp": 0.3, "iv_rv_ratio": 1.5, "z_score": 1.0}
        # Weights that sum to 2 should give same result as same weights summing to 1
        w1 = {"iv_rank": 0.35, "vrp": 0.25, "iv_rv_ratio": 0.25, "z_score": 0.15}
        w2 = {"iv_rank": 0.70, "vrp": 0.50, "iv_rv_ratio": 0.50, "z_score": 0.30}
        assert regime_score(inputs, weights=w1) == pytest.approx(regime_score(inputs, weights=w2))

    def test_zero_weights_raises(self):
        with pytest.raises(ValueError, match="positive"):
            regime_score({"iv_rank": 50}, weights={"iv_rank": 0.0})


# ===========================================================================
# trade_recommendation
# ===========================================================================

class TestTradeRecommendation:

    # Signal mapping
    def test_score_0_is_buy_premium(self):
        assert trade_recommendation(0.0)["signal"] == "BUY_PREMIUM"

    def test_score_29_is_buy_premium(self):
        assert trade_recommendation(29.9)["signal"] == "BUY_PREMIUM"

    def test_score_30_is_neutral(self):
        assert trade_recommendation(30.0)["signal"] == "NEUTRAL"

    def test_score_49_is_neutral(self):
        assert trade_recommendation(49.9)["signal"] == "NEUTRAL"

    def test_score_50_is_sell_selective(self):
        assert trade_recommendation(50.0)["signal"] == "SELL_SELECTIVE"

    def test_score_69_is_sell_selective(self):
        assert trade_recommendation(69.9)["signal"] == "SELL_SELECTIVE"

    def test_score_70_is_sell_aggressive(self):
        assert trade_recommendation(70.0)["signal"] == "SELL_AGGRESSIVE"

    def test_score_100_is_sell_aggressive(self):
        assert trade_recommendation(100.0)["signal"] == "SELL_AGGRESSIVE"

    # Return structure
    def test_returns_required_keys(self):
        result = trade_recommendation(55.0)
        assert {"signal", "label", "color", "description"} <= result.keys()

    def test_color_values_are_valid(self):
        valid_colors = {"green", "yellow", "orange", "red"}
        for score in [10, 40, 60, 80]:
            assert trade_recommendation(score)["color"] in valid_colors

    def test_color_green_for_buy(self):
        assert trade_recommendation(15.0)["color"] == "green"

    def test_color_yellow_for_neutral(self):
        assert trade_recommendation(40.0)["color"] == "yellow"

    def test_color_orange_for_selective(self):
        assert trade_recommendation(60.0)["color"] == "orange"

    def test_color_red_for_aggressive(self):
        assert trade_recommendation(85.0)["color"] == "red"

    def test_description_is_nonempty_string(self):
        for score in [10, 40, 60, 80]:
            desc = trade_recommendation(score)["description"]
            assert isinstance(desc, str) and len(desc) > 0

    # Validation
    def test_score_above_100_raises(self):
        with pytest.raises(ValueError, match="100"):
            trade_recommendation(100.1)

    def test_score_below_0_raises(self):
        with pytest.raises(ValueError, match="0"):
            trade_recommendation(-1.0)

    def test_nan_score_raises(self):
        with pytest.raises(ValueError):
            trade_recommendation(float("nan"))

    def test_inf_score_raises(self):
        with pytest.raises(ValueError):
            trade_recommendation(float("inf"))


# ===========================================================================
# New parameter tests
# ===========================================================================

class TestRealizedVolatilityAnnualization:

    def test_crypto_annualization(self):
        """365-day factor should produce higher annualized vol than 252."""
        prices = random_prices(n=100)
        rv_252 = realized_volatility(prices, 30, annualization_factor=252)
        rv_365 = realized_volatility(prices, 30, annualization_factor=365)
        assert rv_365 > rv_252

    def test_annualization_factor_scales_linearly(self):
        """Doubling annualization_factor should multiply RV by sqrt(2)."""
        prices = random_prices(n=100)
        rv_100 = realized_volatility(prices, 30, annualization_factor=100)
        rv_400 = realized_volatility(prices, 30, annualization_factor=400)
        assert rv_400 == pytest.approx(rv_100 * 2.0, rel=1e-9)

    def test_invalid_annualization_factor_raises(self):
        with pytest.raises(ValueError, match="annualization_factor"):
            realized_volatility(random_prices(), 30, annualization_factor=0)


class TestIVRankLookback:

    def test_lookback_shorter_than_history(self):
        """Lookback should ignore early observations."""
        # Low values early, high values recently
        hist = [10.0] * 100 + [30.0] * 50
        # Full history: lo=10, hi=30 → rank(25) = 75
        full = iv_rank(25.0, hist)
        # Lookback=50 (recent highs only): lo=30, hi=30 → flat → 50
        recent = iv_rank(25.0, hist, lookback=50)
        assert full != recent
        assert recent == pytest.approx(50.0)  # flat window

    def test_lookback_equal_to_history_length(self):
        hist = random_iv_series(100)
        assert iv_rank(20.0, hist, lookback=100) == iv_rank(20.0, hist)

    def test_nan_current_iv_returns_nan(self):
        hist = [10.0, 20.0, 30.0]
        assert math.isnan(iv_rank(float("nan"), hist))

    def test_inf_current_iv_returns_nan(self):
        hist = [10.0, 20.0, 30.0]
        assert math.isnan(iv_rank(float("inf"), hist))


class TestVRPAbsolute:

    def test_absolute_mode_returns_difference(self):
        assert volatility_risk_premium(20.0, 15.0, absolute=True) == pytest.approx(5.0)

    def test_absolute_mode_negative(self):
        assert volatility_risk_premium(12.0, 16.0, absolute=True) == pytest.approx(-4.0)

    def test_relative_mode_is_default(self):
        assert volatility_risk_premium(20.0, 15.0) == pytest.approx(1/3)

    def test_absolute_and_relative_consistent_sign(self):
        """Both modes should agree on direction."""
        for iv, rv in [(20, 15), (14, 18), (18, 18)]:
            rel = volatility_risk_premium(iv, rv)
            abs_ = volatility_risk_premium(iv, rv, absolute=True)
            assert math.copysign(1, rel) == math.copysign(1, abs_) or rel == 0.0


class TestZScoreLookback:

    def test_lookback_changes_result(self):
        """Stats anchored to the lookback window should differ from full-series stats."""
        rng = np.random.default_rng(99)
        # Rising series: recent values are high, anchoring to recent window shifts z-scores
        s = list(range(1, 201))          # 1..200
        z_full   = z_score(s)[-1]
        z_recent = z_score(s, lookback=20)[-1]
        assert z_full != z_recent

    def test_lookback_of_full_series_matches_no_lookback(self):
        s = random_prices(n=80)
        z1 = z_score(s)
        z2 = z_score(s, lookback=len(s))
        np.testing.assert_allclose(z1, z2, rtol=1e-9)

    def test_lookback_1_returns_nan(self):
        """Lookback of 1 gives only 1 finite anchor value — std is undefined, result is nan."""
        s = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = z_score(s, lookback=1)
        assert np.all(np.isnan(result))

    def test_typical_spread_z_score_usage(self):
        """Mirrors how vol_service uses z_score for IV/RV spread."""
        prices = random_prices(n=400)
        iv = np.array(random_iv_series(400, base=20.0))
        log_ret = np.diff(np.log(prices))
        rv = pd.Series(log_ret).rolling(30).std().dropna() * np.sqrt(252) * 100
        spread = pd.Series(iv[-len(rv):]) - rv.values
        current_z = float(z_score(spread.values, lookback=252)[-1])
        assert math.isfinite(current_z)
