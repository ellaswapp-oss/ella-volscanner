"""
api/backtest.py
---------------
FastAPI routes for the backtesting engine.

Endpoints
---------
GET /api/backtest
    Run a single signal backtest and return full results + equity curve.

GET /api/backtest/compare
    Run all five signal types on the same ticker and return a side-by-side
    comparison table plus full per-signal detail.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.backtesting.engine import BacktestConfig, run_backtest, run_comparison
from app.backtesting.rule_backtest import run_rule_backtest
from app.backtesting.iv_rv_sweep import run_iv_rv_sweep
from app.backtesting.macro_sweep import run_macro_sweep
from app.backtesting.signal_grades import run_signal_grades
from app.backtesting.score_optimization import run_score_optimization
from app.backtesting.walk_forward import run_walk_forward
from app.backtesting.data_quality import iv_quality_gate
from app.core.config import settings

router = APIRouter(prefix="/api/backtest", tags=["backtesting"])

_VALID_SIGNALS = {"iv_rv_ratio", "iv_rank", "term_structure", "vix_regime", "combined"}


# ---------------------------------------------------------------------------
# Single-signal backtest
# ---------------------------------------------------------------------------

@router.get("")
def backtest(
    ticker: str = Query("SPY", description="Ticker symbol"),
    signal_type: str = Query(
        "iv_rv_ratio",
        description="Signal generator: iv_rv_ratio | iv_rank | term_structure | vix_regime | combined",
    ),
    holding_period: int = Query(30, ge=1, le=252, description="Position holding period in trading days"),
    lookback: int = Query(252, ge=20, le=1260, description="Rolling lookback window for IV Rank (trading days)"),
    iv_rv_threshold: float = Query(1.20, ge=0.5, le=5.0, description="IV/RV ratio sell threshold"),
    iv_rank_threshold: float = Query(50.0, ge=0.0, le=100.0, description="IV Rank sell threshold (0-100)"),
    vix_low: float = Query(15.0, ge=0.0, le=100.0, description="VIX level below which → BUY_PREMIUM"),
    vix_elevated: float = Query(20.0, ge=0.0, le=100.0, description="VIX level above which → SELL_PREMIUM"),
    vix_spike: float = Query(35.0, ge=0.0, le=100.0, description="VIX level above which → BUY_PREMIUM (fade spike)"),
    ts_contango_min: float = Query(0.0, ge=0.0, le=20.0, description="Minimum slope (IV30−IV7) to trigger SELL"),
    data_days: int = Query(400, ge=100, le=2520, description="Historical trading days to load"),
    require_real_iv: bool = Query(False, description="When true, only use rows with real IV data (polygon_historical_options etc.). Reduces sample size significantly."),
) -> dict:
    """
    Run a single signal backtest.

    Returns full metrics (Sharpe, Sortino, max drawdown, Calmar, win rate,
    expectancy, profit factor), direction stats, regime breakdown by vol regime,
    the daily equity curve, and the list of individual trades.
    """
    sym = ticker.upper()
    if sym not in settings.tickers:
        raise HTTPException(
            status_code=404,
            detail=f"{sym!r} not in supported tickers: {sorted(settings.tickers)}",
        )

    if signal_type not in _VALID_SIGNALS:
        raise HTTPException(
            status_code=422,
            detail=f"signal_type {signal_type!r} invalid. Choose from: {sorted(_VALID_SIGNALS)}",
        )

    if vix_elevated <= vix_low:
        raise HTTPException(
            status_code=422,
            detail=f"vix_elevated ({vix_elevated}) must be greater than vix_low ({vix_low})",
        )

    if vix_spike <= vix_elevated:
        raise HTTPException(
            status_code=422,
            detail=f"vix_spike ({vix_spike}) must be greater than vix_elevated ({vix_elevated})",
        )

    config = BacktestConfig(
        ticker=sym,
        signal_type=signal_type,  # type: ignore[arg-type]
        holding_period=holding_period,
        lookback=lookback,
        iv_rv_threshold=iv_rv_threshold,
        iv_rank_threshold=iv_rank_threshold,
        vix_low=vix_low,
        vix_elevated=vix_elevated,
        vix_spike=vix_spike,
        ts_contango_min=ts_contango_min,
        data_days=data_days,
        require_real_iv=require_real_iv,
    )

    return run_backtest(config)


# ---------------------------------------------------------------------------
# Multi-signal comparison
# ---------------------------------------------------------------------------

@router.get("/compare")
def compare(
    ticker: str = Query("SPY", description="Ticker symbol"),
    holding_period: int = Query(30, ge=1, le=252, description="Position holding period in trading days"),
    iv_rv_threshold: float = Query(1.20, ge=0.5, le=5.0, description="IV/RV ratio sell threshold"),
    iv_rank_threshold: float = Query(50.0, ge=0.0, le=100.0, description="IV Rank sell threshold (0-100)"),
    data_days: int = Query(400, ge=100, le=2520, description="Historical trading days to load"),
    require_real_iv: bool = Query(False, description="When true, only use rows with real IV data (polygon_historical_options etc.). Reduces sample size significantly."),
) -> dict:
    """
    Run all five signal types on the same ticker and return a comparison table.

    The response includes:
    - ``summary``: flat list — one row per signal with key metrics
    - ``details``: full backtest result per signal (same schema as GET /api/backtest)
    """
    sym = ticker.upper()
    if sym not in settings.tickers:
        raise HTTPException(
            status_code=404,
            detail=f"{sym!r} not in supported tickers: {sorted(settings.tickers)}",
        )

    return run_comparison(
        ticker=sym,
        holding_period=holding_period,
        iv_rv_threshold=iv_rv_threshold,
        iv_rank_threshold=iv_rank_threshold,
        data_days=data_days,
        require_real_iv=require_real_iv,
    )


# ---------------------------------------------------------------------------
# IV/RV ratio sweep — 5 variations × all tickers
# GET /api/backtest/iv-rv-ratio
# IMPORTANT: must be defined BEFORE /{ticker} so FastAPI matches it first
# ---------------------------------------------------------------------------

@router.get("/iv-rv-ratio")
def iv_rv_ratio_sweep(
    data_days: int = Query(
        400, ge=100, le=2520,
        description="Historical trading days to load (default 400)",
    ),
    require_real_iv: bool = Query(False, description="When true, only use rows with real IV data (polygon_historical_options etc.). Reduces sample size significantly."),
) -> dict:
    """
    Run the IV/RV > 1.30 rule across 5 filter variations and all 6 tickers.

    All variations sell premium (SELL_PREMIUM) when IV30/RV30 > 1.30, hold for
    10 trading days, and use non-overlapping positions per ticker.

    Variations tested
    -----------------
    1. IV/RV > 1.30 only                           — base rule
    2. IV/RV > 1.30 + IV Rank > 50                — add rank gate
    3. IV/RV > 1.30 + regime score 40–75          — add regime gate
    4. IV/RV > 1.30 + term structure not inverted  — add contango gate
    5. IV/RV > 1.30 + IV Rank > 50 + regime 40–75 — all three filters

    Metrics (per variation, pooled across all tickers)
    ---------------------------------------------------
    n_trades, win_rate, avg_return, median_return, max_drawdown,
    sharpe, sortino, profit_factor, expectancy, best_trade, worst_trade,
    by_ticker breakdown, by_regime breakdown, sequential equity_points.

    The ``best_variation_id`` field identifies the variation with the highest
    Sharpe ratio (minimum 2 trades required for consideration).
    """
    result = run_iv_rv_sweep(data_days=data_days, require_real_iv=require_real_iv)
    result["iv_quality"] = iv_quality_gate(list(settings.tickers), data_days)
    return result


# ---------------------------------------------------------------------------
# Macro filter sweep  GET /api/backtest/macro-sweep
# IMPORTANT: must be defined BEFORE /{ticker} so FastAPI matches it first
# ---------------------------------------------------------------------------

@router.get("/macro-sweep")
def macro_sweep(
    data_days: int = Query(
        400, ge=100, le=2520,
        description="Historical trading days to load (default 400)",
    ),
    require_real_iv: bool = Query(False, description="When true, only use rows with real IV data (polygon_historical_options etc.). Reduces sample size significantly."),
) -> dict:
    """
    Run V2 baseline + 5 orthogonal macro filters + all-combined across all tickers.

    Macro filters tested (each independently, then all together):
    1. VIX level         — only enter when VIX in [18, 32]
    2. VIX term structure— only enter when VIX3M > VIX1M (contango)
    3. Breadth           — SPY above 200-DMA AND >55% SPX above 50-DMA
    4. Yield stress      — block when 10Y yield rose >40 bps in 20 days
    5. Oil shock         — block when WTI crude up >10% in 20 days

    Returns 7 variation results with:
    n_trades, win_rate, premium_capture_rate, avg_return, median_return,
    max_drawdown, sharpe, sortino, profit_factor, expectancy,
    by_ticker, by_vix_regime, equity_points.
    """
    result = run_macro_sweep(data_days=data_days, require_real_iv=require_real_iv)
    result["iv_quality"] = iv_quality_gate(list(settings.tickers), data_days)
    return result


# ---------------------------------------------------------------------------
# Signal grades  GET /api/backtest/signal-grades
# IMPORTANT: defined BEFORE /{ticker}
# ---------------------------------------------------------------------------

@router.get("/signal-grades")
def signal_grades(
    data_days: int = Query(
        400, ge=100, le=2520,
        description="Historical trading days to load (default 400)",
    ),
    require_real_iv: bool = Query(False, description="When true, only use rows with real IV data (polygon_historical_options etc.). Reduces sample size significantly."),
) -> dict:
    """
    Score every V2 entry candidate 0–100 using macro conditions, grade it A/B/C/F,
    then compare four portfolio strategies built on progressively looser grade cutoffs.

    Scoring rubric:
      +25  IV/RV > 1.30          +25  IV Rank > 50
      +15  VIX TS contango       +10  Breadth > 55 %     +10  SPY above 200-DMA
      −15  Yield rise > 40 bp/20d  −15  Crude up > 10%/20d
      −20  VIX TS inverted         −20  VIX ≥ 32

    Grade thresholds: A ≥ 80 · B 65–79 · C 50–64 · F < 50

    Portfolios compared: A-only · A+B · A+B+C · V2 baseline

    Returns portfolios, grade_stats, score_distribution, score_components,
    grade_meta, tickers, data_source.
    """
    result = run_signal_grades(data_days=data_days, require_real_iv=require_real_iv)
    result["iv_quality"] = iv_quality_gate(list(settings.tickers), data_days)
    return result


# ---------------------------------------------------------------------------
# Score-weight optimisation  GET /api/backtest/score-optimization
# IMPORTANT: defined BEFORE /{ticker}
# ---------------------------------------------------------------------------

@router.get("/score-optimization")
def score_optimization(
    data_days: int = Query(
        400, ge=100, le=2520,
        description="Historical trading days to load (default 400)",
    ),
    require_real_iv: bool = Query(False, description="When true, only use rows with real IV data (polygon_historical_options etc.). Reduces sample size significantly."),
) -> dict:
    """
    Grid-search over scoring component weights to find the optimal configuration.

    Evaluates 19 683 weight combinations × 3 thresholds (59 049 total) and
    returns the top 10 models ranked by a composite objective:

        obj = 0.40 × PCR_norm + 0.35 × PF_norm + 0.25 × MDD_norm − trade_penalty

    Response keys
    -------------
    top_models          — top 10 weight configs with full metrics
    current_model       — hand-built baseline (same response schema)
    comparison          — delta between best model and current
    search_stats        — timing, candidate count, eval count
    weight_sensitivity  — per-component avg objective at each grid value
    weight_grid         — search space definition
    objective_definition— objective formula specification
    """
    result = run_score_optimization(data_days=data_days, require_real_iv=require_real_iv)
    result["iv_quality"] = iv_quality_gate(list(settings.tickers), data_days)
    return result


# ---------------------------------------------------------------------------
# Walk-forward validation  GET /api/backtest/walk-forward
# IMPORTANT: defined BEFORE /{ticker}
# ---------------------------------------------------------------------------

@router.get("/walk-forward")
def walk_forward(
    data_days:       int  = Query(756, ge=315, le=2520, description="Trading days to load (≥ 315 required for at least one window)"),
    train_days:      int  = Query(252, ge=63,  le=756,  description="Training window length in trading days"),
    test_days:       int  = Query(63,  ge=21,  le=252,  description="Test window length in trading days"),
    step_days:       int  = Query(63,  ge=21,  le=252,  description="Step size between windows in trading days"),
    objective_mode:  str  = Query("balanced", description="Objective mode: balanced | pcr_stability | conservative"),
    require_real_iv: bool = Query(False, description="When true, only use rows with real IV data (polygon_historical_options etc.). Reduces sample size significantly."),
) -> dict:
    """
    Walk-forward validation of the score-weight optimisation with three objective modes.

    For each rolling window ONE grid search evaluates all three modes simultaneously:
      1. balanced      — 0.40 PCR + 0.35 PF + 0.25 MDD
      2. pcr_stability — 0.60 PCR + 0.20 PF + 0.20 MDD + dispersion penalty
      3. conservative  — 0.40 PCR + 0.20 PF + 0.40 MDD

    The ``objective_mode`` parameter selects which mode drives the primary windows /
    summary / equity outputs.  All three modes always appear in ``mode_comparison``.

    Response keys
    -------------
    windows              — per-fold detail with weight_turnover per window
    summary              — pooled IS vs OOS vs current-OOS + avg degradation + avg_weight_turnover
    oos_equity           — sequential equity across all OOS test windows (active mode)
    is_equity            — sequential equity across all in-sample train windows
    current_oos_equity   — current hand-built model on all test windows
    mode_comparison      — all 3 modes: IS/OOS obj, avg degradation, OOS key metrics, avg turnover
    objective_mode       — active mode id
    mode_definitions     — labels and descriptions for all modes
    params               — window configuration (days, n_windows, etc.)
    """
    result = run_walk_forward(
        data_days=data_days,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        objective_mode=objective_mode,
        require_real_iv=require_real_iv,
    )
    result["iv_quality"] = iv_quality_gate(list(settings.tickers), data_days)
    return result


# ---------------------------------------------------------------------------
# Rule-based backtest  GET /api/backtest/{ticker}
# ---------------------------------------------------------------------------

@router.get("/{ticker}")
def rule_backtest(
    ticker: str,
    iv_rv_min: float = Query(
        1.30, ge=0.5, le=5.0,
        description="Minimum IV/RV ratio for entry (default 1.30)",
    ),
    iv_rank_min: float = Query(
        50.0, ge=0.0, le=100.0,
        description="Minimum IV Rank for entry (default 50)",
    ),
    regime_lo: float = Query(
        40.0, ge=0.0, le=100.0,
        description="Regime score lower bound (default 40)",
    ),
    regime_hi: float = Query(
        75.0, ge=0.0, le=100.0,
        description="Regime score upper bound (default 75)",
    ),
    holding_period: int = Query(
        10, ge=1, le=252,
        description="Trading days to hold each position (default 10)",
    ),
    data_days: int = Query(
        400, ge=100, le=2520,
        description="Historical trading days to load (default 400)",
    ),
    require_real_iv: bool = Query(False, description="When true, only use rows with real IV data (polygon_historical_options etc.). Reduces sample size significantly."),
) -> dict:
    """
    Rule-based backtest for one ticker.

    Entry conditions (all must be true simultaneously):
    - ``iv_rv_min``  — IV/RV ratio above threshold (default > 1.30)
    - ``iv_rank_min`` — IV Rank above threshold    (default > 50)
    - ``regime_lo`` ≤ regime score ≤ ``regime_hi`` (default 40–75)

    Direction: SELL_PREMIUM.  Exit after ``holding_period`` trading days.

    Returns n_trades, win_rate, avg_return, max_drawdown, sharpe, sortino,
    best/worst trade highlights, performance_by_regime breakdown,
    daily equity_curve, and full trade list.
    """
    sym = ticker.upper()
    if sym not in settings.tickers:
        raise HTTPException(
            status_code=404,
            detail=f"{sym!r} not in supported tickers: {sorted(settings.tickers)}",
        )
    if regime_lo >= regime_hi:
        raise HTTPException(
            status_code=422,
            detail=f"regime_lo ({regime_lo}) must be less than regime_hi ({regime_hi})",
        )

    return run_rule_backtest(
        ticker=sym,
        data_days=data_days,
        iv_rv_min=iv_rv_min,
        iv_rank_min=iv_rank_min,
        regime_lo=regime_lo,
        regime_hi=regime_hi,
        holding_period=holding_period,
        require_real_iv=require_real_iv,
    )
