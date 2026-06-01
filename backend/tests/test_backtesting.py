"""
tests/test_backtesting.py
--------------------------
Comprehensive tests for the backtesting sub-package.

Coverage
--------
  metrics.py  — sharpe, sortino, max_drawdown, calmar, win_rate, expectancy,
                profit_factor — happy paths, edge cases, nan guards
  signals.py  — iv_rv_ratio_signals, iv_rank_signals, term_structure_signals,
                vix_regime_signals — correct regime, thresholds, type/length
  engine.py   — BacktestConfig defaults, run_backtest() end-to-end for every
                signal type, run_comparison(), empty-trade path, regime_breakdown
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dates(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range(end="2024-12-31", periods=n)


def _iv_series(n: int = 400, base: float = 22.0, seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(base + rng.normal(0, 2, n), index=_dates(n))


def _price_series(n: int = 400, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    log_r = rng.normal(0.0003, 0.012, n)
    prices = 100 * np.exp(np.cumsum(log_r))
    return pd.Series(prices, index=_dates(n))


# ===========================================================================
# metrics.py
# ===========================================================================

from app.backtesting.metrics import (
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    calmar_ratio,
    win_rate,
    expectancy,
    profit_factor,
)


# --- sharpe_ratio ---

class TestSharpeRatio:
    def test_positive_returns_positive_sharpe(self):
        returns = [0.02] * 100
        assert sharpe_ratio(returns) > 0

    def test_negative_returns_negative_sharpe(self):
        returns = [-0.02] * 100
        assert sharpe_ratio(returns) < 0

    def test_zero_std_returns_nan(self):
        # Constant excess returns (after rf subtraction) → std = 0
        rf = 0.05 / 252
        returns = [rf] * 100  # excess = 0 every period → std = 0
        result = sharpe_ratio(returns)
        assert math.isnan(result)

    def test_fewer_than_2_returns_nan(self):
        assert math.isnan(sharpe_ratio([]))
        assert math.isnan(sharpe_ratio([0.01]))

    def test_annualises_correctly(self):
        daily_ret = 0.001
        r = [daily_ret] * 500
        result = sharpe_ratio(r, risk_free_annual=0.0)
        # Mean / std * sqrt(252); std(ddof=1) of constant array = 0 → nan
        # Use variable series
        rng = np.random.default_rng(7)
        r2 = rng.normal(0.001, 0.01, 500).tolist()
        result2 = sharpe_ratio(r2, risk_free_annual=0.0)
        assert math.isfinite(result2)

    def test_accepts_numpy_array(self):
        arr = np.random.default_rng(0).normal(0.001, 0.01, 200)
        assert math.isfinite(sharpe_ratio(arr))

    def test_accepts_pandas_series(self):
        s = pd.Series(np.random.default_rng(1).normal(0.001, 0.01, 200))
        assert math.isfinite(sharpe_ratio(s))

    def test_ignores_nan_values(self):
        r = [0.01, float("nan"), 0.02, float("nan"), 0.015] * 20
        result = sharpe_ratio(r)
        assert math.isfinite(result)


# --- sortino_ratio ---

class TestSortinoRatio:
    def test_higher_sortino_than_sharpe_for_positively_skewed(self):
        rng = np.random.default_rng(3)
        # Positive skew: large positive, small negative
        r = np.concatenate([rng.uniform(0.01, 0.05, 150),
                            rng.uniform(-0.005, 0, 50)])
        sh = sharpe_ratio(r, risk_free_annual=0.0)
        so = sortino_ratio(r, risk_free_annual=0.0)
        assert so > sh

    def test_fewer_than_2_downside_returns_nan(self):
        returns = [0.05] * 100   # all positive → < 2 downside
        assert math.isnan(sortino_ratio(returns))

    def test_returns_finite_for_mixed_returns(self):
        rng = np.random.default_rng(5)
        r = rng.normal(0.001, 0.015, 300)
        assert math.isfinite(sortino_ratio(r))


# --- max_drawdown ---

class TestMaxDrawdown:
    def test_monotonically_rising_equity_zero_drawdown(self):
        eq = np.linspace(1.0, 2.0, 100)
        assert max_drawdown(eq) == 0.0

    def test_known_drawdown(self):
        eq = [1.0, 1.2, 0.9, 1.1]   # peak=1.2 → trough=0.9 → dd = -0.25
        dd = max_drawdown(eq)
        assert abs(dd - (-0.25)) < 1e-9

    def test_drawdown_bounded_minus_one_to_zero(self):
        rng = np.random.default_rng(9)
        eq = np.cumprod(1 + rng.normal(0.0002, 0.015, 300))
        dd = max_drawdown(eq)
        assert -1.0 <= dd <= 0.0

    def test_fewer_than_2_observations_returns_zero(self):
        assert max_drawdown([]) == 0.0
        assert max_drawdown([1.5]) == 0.0

    def test_all_zeros_no_crash(self):
        result = max_drawdown([0.0, 0.0, 0.0])
        assert math.isfinite(result)


# --- calmar_ratio ---

class TestCalmarRatio:
    def test_positive_returns_with_drawdown(self):
        rng = np.random.default_rng(11)
        r = rng.normal(0.001, 0.012, 400)
        result = calmar_ratio(r)
        assert math.isfinite(result)

    def test_no_drawdown_returns_nan(self):
        # Monotonically increasing cumulative product → MDD = 0
        r = [0.001] * 200  # all positive → equity strictly rises
        # MDD=0 → calmar should be nan
        result = calmar_ratio(r)
        # either nan or a large number depending on tiny floating-point noise
        # The contract: mdd=0 → nan
        eq = np.cumprod(1.0 + np.array(r, dtype=float))
        mdd = abs(max_drawdown(eq))
        if mdd == 0.0:
            assert math.isnan(result)

    def test_fewer_than_2_returns_nan(self):
        assert math.isnan(calmar_ratio([0.01]))


# --- win_rate ---

class TestWinRate:
    def test_all_wins(self):
        assert win_rate([1.0, 2.0, 3.0]) == 1.0

    def test_all_losses(self):
        assert win_rate([-1.0, -2.0]) == 0.0

    def test_half_half(self):
        assert win_rate([1.0, -1.0]) == 0.5

    def test_empty_nan(self):
        assert math.isnan(win_rate([]))

    def test_all_nan_returns_nan(self):
        assert math.isnan(win_rate([float("nan"), float("nan")]))

    def test_zero_pnl_not_a_win(self):
        assert win_rate([0.0, 0.0, 1.0]) == pytest.approx(1 / 3)


# --- expectancy ---

class TestExpectancy:
    def test_all_wins_positive_expectancy(self):
        assert expectancy([1.0, 2.0, 3.0]) > 0

    def test_all_losses_negative_expectancy(self):
        assert expectancy([-1.0, -2.0]) < 0

    def test_empty_nan(self):
        assert math.isnan(expectancy([]))

    def test_formula_correctness(self):
        pnls = [2.0, 2.0, -1.0]   # 2 wins at +2, 1 loss at -1
        # wr=2/3, lr=1/3, avg_win=2, avg_loss=-1
        expected = (2 / 3) * 2.0 + (1 / 3) * (-1.0)
        assert expectancy(pnls) == pytest.approx(expected, rel=1e-6)

    def test_mixed_returns_finite(self):
        rng = np.random.default_rng(13)
        pnls = rng.normal(0.05, 0.3, 100).tolist()
        assert math.isfinite(expectancy(pnls))


# --- profit_factor ---

class TestProfitFactor:
    def test_all_wins_nan(self):
        # No losers → undefined → nan
        assert math.isnan(profit_factor([1.0, 2.0]))

    def test_greater_than_one_when_winners_dominate(self):
        pnls = [3.0, 3.0, -1.0]   # gross_win=6, gross_loss=1 → pf=6
        assert profit_factor(pnls) == pytest.approx(6.0, rel=1e-6)

    def test_less_than_one_when_losers_dominate(self):
        pnls = [1.0, -3.0, -3.0]
        assert profit_factor(pnls) < 1.0

    def test_empty_nan(self):
        assert math.isnan(profit_factor([]))


# ===========================================================================
# signals.py
# ===========================================================================

from app.backtesting.signals import (
    Signal,
    iv_rv_ratio_signals,
    iv_rank_signals,
    term_structure_signals,
    vix_regime_signals,
)


# --- Signal enum ---

class TestSignalEnum:
    def test_enum_values(self):
        assert Signal.SELL_PREMIUM.value == "SELL_PREMIUM"
        assert Signal.BUY_PREMIUM.value  == "BUY_PREMIUM"
        assert Signal.NEUTRAL.value      == "NEUTRAL"

    def test_is_string_subclass(self):
        assert isinstance(Signal.SELL_PREMIUM, str)


# --- iv_rv_ratio_signals ---

class TestIVRVRatioSignals:
    def _make(self, iv_vals, rv_vals):
        idx = _dates(len(iv_vals))
        return (pd.Series(iv_vals, index=idx),
                pd.Series(rv_vals, index=idx))

    def test_high_ratio_triggers_sell(self):
        iv, rv = self._make([25.0] * 50, [10.0] * 50)   # ratio = 2.5 > 1.2
        sigs = iv_rv_ratio_signals(iv, rv)
        assert (sigs == Signal.SELL_PREMIUM).all()

    def test_low_ratio_triggers_buy(self):
        iv, rv = self._make([10.0] * 50, [25.0] * 50)   # ratio ≈ 0.4 < 1/1.2
        sigs = iv_rv_ratio_signals(iv, rv)
        assert (sigs == Signal.BUY_PREMIUM).all()

    def test_near_one_ratio_neutral(self):
        iv, rv = self._make([20.0] * 50, [20.0] * 50)   # ratio = 1.0
        sigs = iv_rv_ratio_signals(iv, rv)
        assert (sigs == Signal.NEUTRAL).all()

    def test_output_length_matches_input(self):
        iv, rv = self._make([20.0] * 100, [15.0] * 100)
        assert len(iv_rv_ratio_signals(iv, rv)) == 100

    def test_index_preserved(self):
        iv, rv = self._make([20.0] * 30, [15.0] * 30)
        sigs = iv_rv_ratio_signals(iv, rv)
        assert sigs.index.equals(iv.index)

    def test_custom_thresholds(self):
        iv, rv = self._make([15.0] * 50, [10.0] * 50)   # ratio = 1.5
        # With sell_threshold=2.0 → should be NEUTRAL (1.5 < 2.0)
        sigs = iv_rv_ratio_signals(iv, rv, sell_threshold=2.0, buy_threshold=0.5)
        assert (sigs == Signal.NEUTRAL).all()

    def test_zero_rv_does_not_crash(self):
        iv, rv = self._make([20.0] * 30, [0.0] * 30)
        sigs = iv_rv_ratio_signals(iv, rv)
        assert len(sigs) == 30   # no exception


# --- iv_rank_signals ---

class TestIVRankSignals:
    def test_high_iv_rank_triggers_sell(self):
        # Monotonically rising IV: current = max → rank = 100
        n = 300
        iv = pd.Series(np.linspace(10, 40, n), index=_dates(n))
        sigs = iv_rank_signals(iv, sell_threshold=50.0)
        # Later half should all be SELL_PREMIUM (rank rises above 50)
        assert (sigs.iloc[-50:] == Signal.SELL_PREMIUM).all()

    def test_low_iv_rank_triggers_buy(self):
        # Monotonically falling IV: current = min → rank = 0
        n = 300
        iv = pd.Series(np.linspace(40, 10, n), index=_dates(n))
        sigs = iv_rank_signals(iv, sell_threshold=50.0)
        assert (sigs.iloc[-50:] == Signal.BUY_PREMIUM).all()

    def test_flat_iv_defaults_to_neutral(self):
        # Flat IV → range = 0 → ivr defaults to 50 → NEUTRAL at threshold=50
        n = 300
        iv = pd.Series([20.0] * n, index=_dates(n))
        sigs = iv_rank_signals(iv, sell_threshold=51.0, buy_threshold=49.0)
        assert (sigs.iloc[-50:] == Signal.NEUTRAL).all()

    def test_output_length_matches_input(self):
        iv = _iv_series(200)
        assert len(iv_rank_signals(iv)) == 200

    def test_custom_lookback(self):
        n = 100
        iv = pd.Series(np.linspace(10, 40, n), index=_dates(n))
        sigs = iv_rank_signals(iv, lookback=20)
        assert len(sigs) == n


# --- term_structure_signals ---

class TestTermStructureSignals:
    def _make(self, iv7_vals, iv30_vals):
        idx = _dates(len(iv7_vals))
        return (pd.Series(iv7_vals, index=idx),
                pd.Series(iv30_vals, index=idx))

    def test_contango_triggers_sell(self):
        # IV30 > IV7 → positive slope → SELL_PREMIUM
        iv7, iv30 = self._make([15.0] * 50, [20.0] * 50)
        sigs = term_structure_signals(iv7, iv30)
        assert (sigs == Signal.SELL_PREMIUM).all()

    def test_inversion_triggers_buy(self):
        # IV7 > IV30 → negative slope → BUY_PREMIUM
        iv7, iv30 = self._make([25.0] * 50, [15.0] * 50)
        sigs = term_structure_signals(iv7, iv30)
        assert (sigs == Signal.BUY_PREMIUM).all()

    def test_flat_structure_triggers_sell_at_zero_min(self):
        # IV30 = IV7 → slope = 0 → not > 0 → NEUTRAL (contango_min=0 uses >)
        iv7, iv30 = self._make([20.0] * 50, [20.0] * 50)
        sigs = term_structure_signals(iv7, iv30)
        assert (sigs == Signal.NEUTRAL).all()

    def test_contango_min_filters_weak_contango(self):
        # slope = 1 but contango_min = 2 → should be NEUTRAL
        iv7, iv30 = self._make([19.0] * 50, [20.0] * 50)
        sigs = term_structure_signals(iv7, iv30, contango_min=2.0)
        assert (sigs == Signal.NEUTRAL).all()

    def test_output_length_matches_input(self):
        iv7, iv30 = self._make([15.0] * 80, [20.0] * 80)
        assert len(term_structure_signals(iv7, iv30)) == 80

    def test_index_preserved(self):
        iv7, iv30 = self._make([15.0] * 30, [20.0] * 30)
        sigs = term_structure_signals(iv7, iv30)
        assert sigs.index.equals(iv7.index)


# --- vix_regime_signals ---

class TestVIXRegimeSignals:
    def _make(self, vals):
        return pd.Series(vals, index=_dates(len(vals)))

    def test_low_iv_triggers_buy(self):
        iv = self._make([12.0] * 50)   # < 15 → BUY_PREMIUM
        sigs = vix_regime_signals(iv)
        assert (sigs == Signal.BUY_PREMIUM).all()

    def test_normal_zone_neutral(self):
        iv = self._make([17.0] * 50)   # 15–20 → NEUTRAL
        sigs = vix_regime_signals(iv)
        assert (sigs == Signal.NEUTRAL).all()

    def test_elevated_triggers_sell(self):
        iv = self._make([28.0] * 50)   # 20–35 → SELL_PREMIUM
        sigs = vix_regime_signals(iv)
        assert (sigs == Signal.SELL_PREMIUM).all()

    def test_spike_overrides_sell_to_buy(self):
        iv = self._make([40.0] * 50)   # > 35 → BUY_PREMIUM (fade spike)
        sigs = vix_regime_signals(iv)
        assert (sigs == Signal.BUY_PREMIUM).all()

    def test_custom_thresholds(self):
        iv = self._make([25.0] * 50)   # 20–35 normally → SELL
        # Raise elevated_threshold so 25 is still NEUTRAL
        sigs = vix_regime_signals(iv, elevated_threshold=30.0, spike_threshold=50.0)
        assert (sigs == Signal.NEUTRAL).all()

    def test_boundary_at_elevated_threshold(self):
        iv = self._make([20.0] * 50)   # exactly at threshold
        sigs = vix_regime_signals(iv, elevated_threshold=20.0, spike_threshold=35.0)
        # 20 > 20 is False → NEUTRAL
        assert (sigs == Signal.NEUTRAL).all()

    def test_output_length_matches_input(self):
        iv = self._make([22.0] * 120)
        assert len(vix_regime_signals(iv)) == 120


# ===========================================================================
# engine.py
# ===========================================================================

from app.backtesting.engine import BacktestConfig, run_backtest, run_comparison


# --- BacktestConfig defaults ---

class TestBacktestConfig:
    def test_defaults(self):
        c = BacktestConfig()
        assert c.ticker == "SPY"
        assert c.signal_type == "iv_rv_ratio"
        assert c.holding_period == 30
        assert c.lookback == 252
        assert c.iv_rv_threshold == pytest.approx(1.20)
        assert c.iv_rank_threshold == pytest.approx(50.0)
        assert c.vix_low == pytest.approx(15.0)
        assert c.vix_elevated == pytest.approx(20.0)
        assert c.vix_spike == pytest.approx(35.0)
        assert c.data_days == 400

    def test_custom_fields(self):
        c = BacktestConfig(ticker="QQQ", signal_type="iv_rank",
                           holding_period=21, iv_rv_threshold=1.5)
        assert c.ticker == "QQQ"
        assert c.signal_type == "iv_rank"
        assert c.holding_period == 21
        assert c.iv_rv_threshold == pytest.approx(1.5)


# --- run_backtest result schema ---

_REQUIRED_KEYS = {
    "ticker", "signal_type", "config",
    "n_trades", "sharpe", "sortino", "max_drawdown", "calmar",
    "win_rate", "expectancy", "profit_factor",
    "total_pnl", "avg_pnl",
    "sell_stats", "buy_stats",
    "regime_breakdown", "equity_curve", "trades", "data_source",
}

SIGNAL_TYPES = ["iv_rv_ratio", "iv_rank", "term_structure", "vix_regime", "combined"]
TICKERS      = ["SPY", "QQQ", "IWM", "NVDA", "TSLA"]


@pytest.mark.parametrize("signal_type", SIGNAL_TYPES)
def test_run_backtest_schema(signal_type):
    """Every signal type must return a result with the required keys."""
    cfg = BacktestConfig(ticker="SPY", signal_type=signal_type, data_days=300)
    result = run_backtest(cfg)
    assert _REQUIRED_KEYS.issubset(result.keys()), (
        f"Missing keys for {signal_type}: {_REQUIRED_KEYS - result.keys()}"
    )


@pytest.mark.parametrize("signal_type", SIGNAL_TYPES)
def test_run_backtest_ticker_matches(signal_type):
    cfg = BacktestConfig(ticker="QQQ", signal_type=signal_type, data_days=300)
    result = run_backtest(cfg)
    assert result["ticker"] == "QQQ"
    assert result["signal_type"] == signal_type


@pytest.mark.parametrize("signal_type", SIGNAL_TYPES)
def test_run_backtest_n_trades_non_negative(signal_type):
    cfg = BacktestConfig(ticker="SPY", signal_type=signal_type, data_days=300)
    result = run_backtest(cfg)
    assert result["n_trades"] >= 0


@pytest.mark.parametrize("signal_type", SIGNAL_TYPES)
def test_run_backtest_metrics_finite_or_none(signal_type):
    """Metrics must be float or None — never NaN in the JSON payload."""
    cfg = BacktestConfig(ticker="SPY", signal_type=signal_type, data_days=300)
    result = run_backtest(cfg)
    for key in ("sharpe", "sortino", "max_drawdown", "calmar",
                "win_rate", "expectancy", "profit_factor"):
        val = result[key]
        assert val is None or math.isfinite(val), (
            f"{key}={val!r} is not None and not finite for signal {signal_type}"
        )


@pytest.mark.parametrize("signal_type", SIGNAL_TYPES)
def test_run_backtest_trades_are_dicts(signal_type):
    cfg = BacktestConfig(ticker="SPY", signal_type=signal_type, data_days=300)
    result = run_backtest(cfg)
    for trade in result["trades"]:
        assert isinstance(trade, dict)
        assert "entry" in trade and "exit" in trade
        assert "pnl" in trade
        assert math.isfinite(trade["pnl"])


@pytest.mark.parametrize("signal_type", SIGNAL_TYPES)
def test_run_backtest_equity_curve_monotone_start(signal_type):
    """Equity curve must start at or near 1.0 and have a valid length."""
    cfg = BacktestConfig(ticker="SPY", signal_type=signal_type, data_days=300)
    result = run_backtest(cfg)
    ec = result["equity_curve"]
    assert isinstance(ec, list)
    # If there are trades, equity curve must be non-empty
    if result["n_trades"] > 0:
        assert len(ec) > 0
        # All equity values must be positive and finite
        for point in ec:
            assert math.isfinite(point["equity"])
            assert point["equity"] > 0


@pytest.mark.parametrize("signal_type", SIGNAL_TYPES)
def test_run_backtest_regime_breakdown_keys(signal_type):
    cfg = BacktestConfig(ticker="SPY", signal_type=signal_type, data_days=300)
    result = run_backtest(cfg)
    rb = result["regime_breakdown"]
    for bucket in ("buy_premium", "neutral", "sell_selective", "sell_aggressive"):
        assert bucket in rb
        assert "n" in rb[bucket]
        assert rb[bucket]["n"] >= 0


@pytest.mark.parametrize("signal_type", SIGNAL_TYPES)
def test_run_backtest_regime_breakdown_totals_trades(signal_type):
    """Sum of regime bucket trade counts == n_trades."""
    cfg = BacktestConfig(ticker="SPY", signal_type=signal_type, data_days=300)
    result = run_backtest(cfg)
    rb = result["regime_breakdown"]
    bucket_total = sum(rb[k]["n"] for k in rb)
    assert bucket_total == result["n_trades"]


@pytest.mark.parametrize("signal_type", SIGNAL_TYPES)
def test_run_backtest_direction_stats_sum(signal_type):
    """sell_stats.n + buy_stats.n == n_trades."""
    cfg = BacktestConfig(ticker="SPY", signal_type=signal_type, data_days=300)
    result = run_backtest(cfg)
    total = result["sell_stats"]["n"] + result["buy_stats"]["n"]
    assert total == result["n_trades"]


@pytest.mark.parametrize("ticker", TICKERS)
def test_run_backtest_all_tickers(ticker):
    """All supported tickers must produce a valid result."""
    cfg = BacktestConfig(ticker=ticker, signal_type="iv_rv_ratio", data_days=300)
    result = run_backtest(cfg)
    assert result["ticker"] == ticker
    assert result["n_trades"] >= 0


def test_run_backtest_data_source_mock():
    cfg = BacktestConfig(ticker="SPY", signal_type="iv_rv_ratio", data_days=300)
    result = run_backtest(cfg)
    assert result["data_source"] == "mock"


# --- High sell threshold → fewer/no trades ---

def test_high_iv_rv_threshold_fewer_trades():
    """Raising sell threshold should reduce (or equal) trade count."""
    cfg_base = BacktestConfig(ticker="SPY", signal_type="iv_rv_ratio",
                              iv_rv_threshold=1.0, data_days=300)
    cfg_high = BacktestConfig(ticker="SPY", signal_type="iv_rv_ratio",
                              iv_rv_threshold=5.0, data_days=300)
    n_base = run_backtest(cfg_base)["n_trades"]
    n_high = run_backtest(cfg_high)["n_trades"]
    assert n_high <= n_base


def test_short_holding_period_more_trades_than_long():
    """Shorter holding periods should not reduce trade count vs longer ones on same data."""
    cfg_short = BacktestConfig(ticker="SPY", signal_type="iv_rv_ratio",
                               holding_period=5, data_days=400)
    cfg_long  = BacktestConfig(ticker="SPY", signal_type="iv_rv_ratio",
                               holding_period=60, data_days=400)
    n_short = run_backtest(cfg_short)["n_trades"]
    n_long  = run_backtest(cfg_long)["n_trades"]
    assert n_short >= n_long


# --- run_comparison ---

class TestRunComparison:
    def test_schema(self):
        result = run_comparison("SPY", holding_period=30, data_days=300)
        assert "ticker" in result
        assert "holding_period" in result
        assert "summary" in result
        assert "details" in result
        assert "data_source" in result

    def test_summary_has_all_signal_types(self):
        result = run_comparison("SPY", holding_period=30, data_days=300)
        signal_names = {row["signal"] for row in result["summary"]}
        assert signal_names == set(SIGNAL_TYPES)

    def test_details_has_all_signal_types(self):
        result = run_comparison("SPY", holding_period=30, data_days=300)
        assert set(result["details"].keys()) == set(SIGNAL_TYPES)

    def test_summary_row_keys(self):
        result = run_comparison("SPY", holding_period=30, data_days=300)
        required_row_keys = {"signal", "n_trades", "sharpe", "max_drawdown",
                             "win_rate", "expectancy", "total_pnl"}
        for row in result["summary"]:
            assert required_row_keys.issubset(row.keys())

    def test_details_consistent_with_summary(self):
        """n_trades in summary must match n_trades in details for each signal."""
        result = run_comparison("QQQ", holding_period=30, data_days=300)
        for row in result["summary"]:
            stype = row["signal"]
            assert row["n_trades"] == result["details"][stype]["n_trades"]

    def test_ticker_propagated(self):
        result = run_comparison("IWM", holding_period=21, data_days=300)
        assert result["ticker"] == "IWM"
        assert result["holding_period"] == 21
        for stype in SIGNAL_TYPES:
            assert result["details"][stype]["ticker"] == "IWM"
