"""
backtesting/signal_grades.py
----------------------------
Convert the short-vol setup into a continuous 0–100 score, bucket each trade
into a letter grade, then compare four portfolio strategies built on those grades.

Scoring (base = 0):
    +25   IV/RV > 1.30             — core condition
    +25   IV Rank > 50             — core condition
    +15   VIX TS in contango       — VIX3M > VIX1M (non-stress term structure)
    +10   Breadth > 55 %           — majority of SPX above 50-DMA (healthy tape)
    +10   SPY above 200-DMA        — broad index uptrend
    −15   10Y yield up > 40 bps/20d — rate shock headwind
    −15   Crude oil up > 10 %/20d  — energy/inflation shock
    −20   VIX TS inverted          — mutually exclusive with +15; acute front stress
    −20   VIX ≥ 32                 — spike regime, gap risk elevated

    Maximum achievable: +85   (all positive, no negatives)
    V2 base only:        +50   (IV/RV + IV Rank; neutral macro)
    Score floor:        −70   (all negatives, no positives; not useful)

Grade thresholds:
    A   ≥ 80   — all macro conditions aligned
    B   65–79  — contango confirmed, breadth OK
    C   50–64  — base V2 conditions; minor or no macro headwind
    F    < 50  — macro stress outweighs positives; skip

Portfolio strategies compared (4 total):
    1  A Only   — score ≥ 80
    2  A + B    — score ≥ 65
    3  A+B+C    — score ≥ 50  (V2 + no severe macro stress)
    4  V2 Base  — all V2 trades regardless of score

Outputs per portfolio:
    n_trades, win_rate, premium_capture_rate, avg_return, median_return,
    max_drawdown, sharpe, sortino, profit_factor, expectancy,
    by_ticker, by_vix_regime, equity_points

Outputs grade_stats:
    Same metrics computed per grade bucket (A/B/C/F) from the V2 baseline
    universe — answers "did higher scores predict better outcomes?"
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd

from app.data.mock_provider import TICKERS, get_price_history, get_iv_data
from app.data.macro_provider import get_macro_series
from app.backtesting.metrics import (
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    win_rate,
    profit_factor,
    expectancy,
)

_TDAYS       = 252
_HOLDING     = 10
_LOOKBACK    = 252
_IV_RV_MIN   = 1.30
_IV_RANK_MIN = 50.0


# ---------------------------------------------------------------------------
# Public catalogues (exposed to the API layer for documentation)
# ---------------------------------------------------------------------------

SCORE_COMPONENTS: list[dict] = [
    {"key": "iv_rv",         "label": "IV/RV > 1.30",              "value": +25, "type": "positive"},
    {"key": "iv_rank",       "label": "IV Rank > 50",               "value": +25, "type": "positive"},
    {"key": "vix_ts",        "label": "VIX3M > VIX1M (contango)",  "value": +15, "type": "positive"},
    {"key": "breadth",       "label": "Breadth > 55 %",            "value": +10, "type": "positive"},
    {"key": "spy_200dma",    "label": "SPY above 200-DMA",         "value": +10, "type": "positive"},
    {"key": "yield_stress",  "label": "10Y yield up > 40 bps / 20d","value": -15, "type": "negative"},
    {"key": "oil_shock",     "label": "Crude up > 10 % / 20d",     "value": -15, "type": "negative"},
    {"key": "vix_inversion", "label": "VIX TS inverted",           "value": -20, "type": "negative"},
    {"key": "vix_spike",     "label": "VIX ≥ 32",                  "value": -20, "type": "negative"},
]

GRADE_META: dict[str, dict] = {
    "A": {"label": "A",  "min": 80,  "max": 100, "desc": "All macro conditions aligned"},
    "B": {"label": "B",  "min": 65,  "max": 79,  "desc": "Contango confirmed, breadth OK"},
    "C": {"label": "C",  "min": 50,  "max": 64,  "desc": "Base V2; minor macro headwind"},
    "F": {"label": "F",  "min": -99, "max": 49,  "desc": "Macro stress — skip"},
}

PORTFOLIOS: list[dict] = [
    {
        "id": 1,
        "name": "Grade A Only",
        "short": "A Only",
        "description": "Score ≥ 80: all macro conditions aligned — tightest entry criteria",
        "threshold": 80,
        "color": "#34d399",   # emerald
        "grades_included": ["A"],
    },
    {
        "id": 2,
        "name": "Grade A + B",
        "short": "A + B",
        "description": "Score ≥ 65: VIX contango confirmed; breadth optional",
        "threshold": 65,
        "color": "#60a5fa",   # blue
        "grades_included": ["A", "B"],
    },
    {
        "id": 3,
        "name": "Grade A + B + C",
        "short": "A+B+C",
        "description": "Score ≥ 50: base V2 with no severe macro headwinds (inversion or stress blocks)",
        "threshold": 50,
        "color": "#fbbf24",   # amber
        "grades_included": ["A", "B", "C"],
    },
    {
        "id": 4,
        "name": "V2 Baseline",
        "short": "V2 Base",
        "description": "All IV/RV > 1.30 + IVR > 50 trades regardless of macro score",
        "threshold": -999,
        "color": "#94a3b8",   # slate
        "grades_included": ["A", "B", "C", "F"],
    },
]


# ---------------------------------------------------------------------------
# Rolling helpers
# ---------------------------------------------------------------------------

def _rolling_rv(prices: pd.Series, window: int) -> pd.Series:
    return (
        np.log(prices / prices.shift(1))
        .rolling(window, min_periods=max(window // 4, 5))
        .std(ddof=1)
        * math.sqrt(_TDAYS)
        * 100
    )


def _rolling_iv_rank(iv30: pd.Series, lookback: int = _LOOKBACK) -> pd.Series:
    min_p = max(lookback // 4, 10)
    lo    = iv30.rolling(lookback, min_periods=min_p).min()
    hi    = iv30.rolling(lookback, min_periods=min_p).max()
    raw   = np.where((hi - lo) > 0, (iv30 - lo) / (hi - lo) * 100, 50.0)
    return pd.Series(raw, index=iv30.index)


def _vix_bucket(vix: float) -> str:
    if vix < 18:  return "calm"
    if vix < 25:  return "normal"
    if vix < 32:  return "elevated"
    return "spike"


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

def _compute_score(
    iv_rv_ratio:   float,
    iv_rank:       float,
    vix3m:         float,
    vix1m:         float,
    breadth:       float,
    spy_above_200: bool,
    yield_rise_20d: float,   # change in percentage points (pp)
    crude_pct_20d:  float,   # fractional change (e.g. 0.12 = 12 %)
    vix:           float,
) -> tuple[int, dict[str, int]]:
    """
    Compute a scalar score and a component breakdown for one potential entry.

    Returns
    -------
    (score, components)
        score      : integer score (no clamping — can be negative)
        components : dict mapping each component key to its signed contribution
    """
    comp: dict[str, int] = {}

    comp["iv_rv"]   = 25 if iv_rv_ratio > _IV_RV_MIN   else 0
    comp["iv_rank"] = 25 if iv_rank     > _IV_RANK_MIN  else 0

    # VIX term structure — contango and inversion are mutually exclusive
    if vix3m > vix1m:
        comp["vix_ts"]        = +15
        comp["vix_inversion"] = 0
    else:
        comp["vix_ts"]        = 0
        comp["vix_inversion"] = -20

    comp["breadth"]    = 10 if breadth > 55.0    else 0
    comp["spy_200dma"] = 10 if spy_above_200      else 0

    comp["yield_stress"] = -15 if (math.isfinite(yield_rise_20d) and yield_rise_20d > 0.40)  else 0
    comp["oil_shock"]    = -15 if (math.isfinite(crude_pct_20d)  and crude_pct_20d  > 0.10)  else 0
    comp["vix_spike"]    = -20 if vix >= 32.0                                                 else 0

    return sum(comp.values()), comp


def _score_to_grade(score: int) -> str:
    if score >= 80: return "A"
    if score >= 65: return "B"
    if score >= 50: return "C"
    return "F"


# ---------------------------------------------------------------------------
# Premium capture rate helper
# ---------------------------------------------------------------------------

def _premium_capture_rate(trades: list[dict]) -> float | None:
    """sum(IV_entry − RV_hold) / sum(IV_entry) — value-weighted by IV at entry."""
    if not trades:
        return None
    total_iv  = sum(t["iv_entry"] for t in trades)
    total_cap = sum(t["iv_entry"] - t["rv_hold"] for t in trades)
    return round(total_cap / total_iv, 4) if total_iv > 0 else None


# ---------------------------------------------------------------------------
# Trade simulator  (one ticker, one score threshold)
# ---------------------------------------------------------------------------

def _simulate(
    ticker: str,
    prices: pd.Series,
    iv_df:  pd.DataFrame,
    rv30:   pd.Series,
    ivr:    pd.Series,
    macro:  pd.DataFrame,
    threshold: int,
    holding:   int = _HOLDING,
) -> list[dict]:
    """
    Non-overlapping SELL_PREMIUM trades for one ticker at one score threshold.

    Entry conditions:
        1. IV/RV > 1.30  (V2 core)
        2. IV Rank > 50  (V2 core)
        3. score >= threshold (grade filter; -999 = accept all)

    P&L: pnl = (IV_entry - RV_hold) / IV_entry
    """
    common_idx = prices.index.intersection(macro.index)
    prices_a   = prices.loc[common_idx]
    iv_df_a    = iv_df.loc[common_idx]
    rv30_a     = rv30.loc[common_idx]
    ivr_a      = ivr.loc[common_idx]
    macro_a    = macro.loc[common_idx]

    n      = len(common_idx)
    warmup = max(_LOOKBACK // 4, holding + 1, 30)

    spy_arr  = macro_a["spy_price"].values
    spy_200  = pd.Series(spy_arr).rolling(200, min_periods=100).mean().values
    vix1m    = macro_a["vix1m"].values
    vix3m    = macro_a["vix3m"].values
    br_arr   = macro_a["breadth"].values
    y_arr    = macro_a["us10y"].values
    c_arr    = macro_a["crude_oil"].values
    y_lag20  = pd.Series(y_arr).shift(20).values
    c_lag20  = pd.Series(c_arr).shift(20).values

    trades: list[dict] = []
    last_exit = -1

    for i in range(warmup, n):
        if i <= last_exit:
            continue

        iv    = float(iv_df_a["iv30"].iloc[i])
        rv    = float(rv30_a.iloc[i])
        ivr_v = float(ivr_a.iloc[i])

        if not (math.isfinite(iv) and math.isfinite(rv) and math.isfinite(ivr_v)):
            continue
        if rv <= 0:
            continue

        ratio = iv / rv

        # V2 base conditions — always required
        if ratio <= _IV_RV_MIN:
            continue
        if ivr_v <= _IV_RANK_MIN:
            continue

        # Score this potential entry
        vix   = float(vix1m[i])
        vix3m_v = float(vix3m[i])
        br    = float(br_arr[i])
        dma200 = float(spy_200[i])
        spy_above_200 = math.isfinite(dma200) and float(spy_arr[i]) > dma200

        y_lag = float(y_lag20[i])
        y_rise_20 = (float(y_arr[i]) - y_lag) if math.isfinite(y_lag) else 0.0

        c_lag = float(c_lag20[i])
        c_pct_20 = ((float(c_arr[i]) - c_lag) / c_lag) if (math.isfinite(c_lag) and c_lag > 0) else 0.0

        score, comp = _compute_score(
            ratio, ivr_v, vix3m_v, vix, br,
            spy_above_200, y_rise_20, c_pct_20, vix,
        )
        grade = _score_to_grade(score)

        # Apply grade threshold
        if score < threshold:
            continue

        exit_i = i + holding
        if exit_i >= n:
            break

        hold_log = np.log(
            prices_a.values[i + 1 : exit_i + 1] /
            prices_a.values[i     : exit_i]
        )
        if len(hold_log) < 2:
            continue
        rv_hold = float(hold_log.std(ddof=1) * math.sqrt(_TDAYS) * 100)
        if not math.isfinite(rv_hold):
            continue

        pnl = (iv - rv_hold) / iv

        trades.append({
            "ticker":           ticker,
            "entry":            str(common_idx[i].date()),
            "exit":             str(common_idx[exit_i].date()),
            "iv_entry":         round(iv, 2),
            "rv_hold":          round(rv_hold, 2),
            "iv_rv_ratio":      round(ratio, 3),
            "iv_rank":          round(ivr_v, 1),
            "vix_entry":        round(vix, 2),
            "vix_bucket":       _vix_bucket(vix),
            "score":            score,
            "grade":            grade,
            "score_components": comp,
            "pnl":              round(pnl, 4),
            "is_winner":        bool(pnl > 0),
        })
        last_exit = exit_i

    return trades


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------

def _aggregate(all_trades: list[dict]) -> dict:
    """Full metrics dict for a list of trades."""
    _empty: dict = {
        "n_trades":             0,
        "win_rate":             None,
        "premium_capture_rate": None,
        "avg_return":           None,
        "median_return":        None,
        "max_drawdown":         None,
        "sharpe":               None,
        "sortino":              None,
        "profit_factor":        None,
        "expectancy":           None,
        "best_trade":           None,
        "worst_trade":          None,
        "by_ticker": {
            t: {"n_trades": 0, "win_rate": None, "avg_return": None, "median_return": None}
            for t in TICKERS
        },
        "by_vix_regime": {
            b: {"n": 0, "win_rate": None, "avg_pnl": None, "total": 0.0}
            for b in ("calm", "normal", "elevated", "spike")
        },
        "equity_points": [],
    }
    if not all_trades:
        return _empty

    pnls    = [t["pnl"] for t in all_trades]
    pnl_arr = np.array(pnls)

    def _safe(v: float) -> float | None:
        return round(float(v), 4) if math.isfinite(float(v)) else None

    # Sequential equity curve (sort by exit date, ticker for tie-break)
    sorted_trades = sorted(all_trades, key=lambda t: (t["exit"], t["ticker"]))
    equity = 1.0
    equity_pts: list[dict] = []
    for j, t in enumerate(sorted_trades):
        equity *= 1.0 + t["pnl"]
        equity_pts.append({"trade_n": j + 1, "equity": round(equity, 4)})

    eq_arr = np.array([1.0] + [p["equity"] for p in equity_pts])

    # Per-ticker
    by_ticker_pnls: dict[str, list[float]] = defaultdict(list)
    for t in all_trades:
        by_ticker_pnls[t["ticker"]].append(t["pnl"])

    by_ticker: dict[str, dict] = {}
    for tk in TICKERS:
        tp = by_ticker_pnls.get(tk, [])
        if tp:
            by_ticker[tk] = {
                "n_trades":      len(tp),
                "win_rate":      round(float(win_rate(tp)), 3),
                "avg_return":    round(float(np.mean(tp)), 4),
                "median_return": round(float(np.median(tp)), 4),
            }
        else:
            by_ticker[tk] = {
                "n_trades": 0, "win_rate": None,
                "avg_return": None, "median_return": None,
            }

    # Per VIX bucket
    by_vix_pnls: dict[str, list[float]] = defaultdict(list)
    for t in all_trades:
        by_vix_pnls[t["vix_bucket"]].append(t["pnl"])

    by_vix_regime: dict[str, dict] = {}
    for bkt in ("calm", "normal", "elevated", "spike"):
        bp = by_vix_pnls.get(bkt, [])
        if bp:
            by_vix_regime[bkt] = {
                "n":        len(bp),
                "win_rate": round(float(win_rate(bp)), 3),
                "avg_pnl":  round(float(np.mean(bp)), 4),
                "total":    round(float(sum(bp)), 4),
            }
        else:
            by_vix_regime[bkt] = {"n": 0, "win_rate": None, "avg_pnl": None, "total": 0.0}

    best  = max(all_trades, key=lambda t: t["pnl"])
    worst = min(all_trades, key=lambda t: t["pnl"])

    return {
        "n_trades":             len(all_trades),
        "win_rate":             round(float(win_rate(pnls)), 3),
        "premium_capture_rate": _premium_capture_rate(all_trades),
        "avg_return":           _safe(float(np.mean(pnl_arr))),
        "median_return":        _safe(float(np.median(pnl_arr))),
        "max_drawdown":         _safe(max_drawdown(eq_arr)),
        "sharpe":               _safe(sharpe_ratio(pnl_arr, periods_per_year=1)),
        "sortino":              _safe(sortino_ratio(pnl_arr, periods_per_year=1)),
        "profit_factor":        _safe(profit_factor(pnls)),
        "expectancy":           _safe(expectancy(pnls)),
        "best_trade":           best,
        "worst_trade":          worst,
        "by_ticker":            by_ticker,
        "by_vix_regime":        by_vix_regime,
        "equity_points":        equity_pts,
    }


# ---------------------------------------------------------------------------
# Grade stats  (retrospective, from V2 baseline universe)
# ---------------------------------------------------------------------------

def _compute_grade_stats(v2_trades: list[dict]) -> dict:
    """
    Group V2 baseline trades by grade and compute metrics per group.

    This answers: "Did higher scores predict better trade outcomes?"
    Unlike the portfolio comparison, this uses the same V2 non-overlapping
    universe for all grades — purely retrospective segmentation.
    """
    by_grade: dict[str, list[dict]] = defaultdict(list)
    for t in v2_trades:
        by_grade[t["grade"]].append(t)

    result: dict[str, dict] = {}
    for grade in ("A", "B", "C", "F"):
        trades = by_grade.get(grade, [])
        if not trades:
            result[grade] = {
                "n": 0, "win_rate": None, "premium_capture_rate": None,
                "avg_return": None, "median_return": None,
                "max_drawdown": None, "sharpe": None,
                "profit_factor": None, "expectancy": None,
            }
            continue

        pnls    = [t["pnl"] for t in trades]
        pnl_arr = np.array(pnls)

        # Equity curve from this grade's trades in date order
        sorted_t = sorted(trades, key=lambda t: (t["exit"], t["ticker"]))
        eq = 1.0
        eq_pts = [1.0]
        for t in sorted_t:
            eq *= 1.0 + t["pnl"]
            eq_pts.append(eq)
        eq_arr = np.array(eq_pts)

        def _safe(v: float) -> float | None:
            return round(float(v), 4) if math.isfinite(float(v)) else None

        result[grade] = {
            "n":                    len(trades),
            "win_rate":             round(float(win_rate(pnls)), 3),
            "premium_capture_rate": _premium_capture_rate(trades),
            "avg_return":           _safe(float(np.mean(pnl_arr))),
            "median_return":        _safe(float(np.median(pnl_arr))),
            "max_drawdown":         _safe(max_drawdown(eq_arr)),
            "sharpe":               _safe(sharpe_ratio(pnl_arr, periods_per_year=1)),
            "profit_factor":        _safe(profit_factor(pnls)),
            "expectancy":           _safe(expectancy(pnls)),
        }

    return result


# ---------------------------------------------------------------------------
# Score distribution  (histogram from V2 baseline)
# ---------------------------------------------------------------------------

def _score_distribution(trades: list[dict]) -> list[dict]:
    """
    5-point score buckets from the V2 baseline trade universe.

    5-pt buckets are used so every bucket falls entirely within one grade band
    (grade boundaries at 50, 65, 80 are all multiples of 5).
    """
    counts: dict[int, int] = {}
    for t in trades:
        bucket = (t["score"] // 5) * 5
        counts[bucket] = counts.get(bucket, 0) + 1
    return [
        {"score_min": k, "score_max": k + 4, "count": v, "grade": _score_to_grade(k)}
        for k, v in sorted(counts.items())
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_signal_grades(data_days: int = 400, require_real_iv: bool = False) -> dict:
    """
    Score every potential V2 entry, grade it A/B/C/F, and compare four
    portfolio strategies that apply progressively looser grade cutoffs.

    Returns
    -------
    dict
        portfolios         — 4 portfolio result dicts (A-only → V2 baseline)
        grade_stats        — retrospective metrics per grade from V2 baseline
        score_distribution — histogram of scores from V2 baseline trades
        score_components   — scoring rubric (for frontend documentation)
        grade_meta         — grade threshold definitions
        tickers            — tickers tested
        data_source        — "mock"
    """
    macro = get_macro_series(days=data_days)

    # Pre-load all ticker data once
    ticker_data: dict[str, tuple] = {}
    filter_summaries: list[dict] = []
    for ticker in TICKERS:
        prices = get_price_history(ticker, days=data_days)
        iv_df  = get_iv_data(ticker, days=data_days, require_real_iv=require_real_iv)
        filter_summaries.append(iv_df.attrs.get("real_iv_filter", {"enabled": False,
                                                                    "rows_before_filter": len(iv_df),
                                                                    "rows_excluded": 0,
                                                                    "rows_remaining": len(iv_df)}))
        idx    = prices.index.intersection(iv_df.index)
        prices = prices.loc[idx]
        iv_df  = iv_df.loc[idx]
        rv30   = _rolling_rv(prices, 30)
        ivr    = _rolling_iv_rank(iv_df["iv30"], _LOOKBACK)
        ticker_data[ticker] = (prices, iv_df, rv30, ivr)

    # Aggregate filter summary across all tickers
    _before = sum(f.get("rows_before_filter", 0) for f in filter_summaries)
    _excl   = sum(f.get("rows_excluded",      0) for f in filter_summaries)
    _remain = sum(f.get("rows_remaining",     0) for f in filter_summaries)
    _starts = [f["usable_date_start"] for f in filter_summaries if f.get("usable_date_start")]
    _ends   = [f["usable_date_end"]   for f in filter_summaries if f.get("usable_date_end")]
    agg_filter: dict = {
        "enabled":            require_real_iv,
        "rows_before_filter": _before,
        "rows_excluded":      _excl,
        "rows_remaining":     _remain,
        "pct_remaining":      round(_remain / _before, 4) if _before > 0 else 0.0,
        "usable_date_start":  min(_starts) if _starts else None,
        "usable_date_end":    max(_ends)   if _ends   else None,
    }

    # Simulate V2 baseline (threshold=-999) for grade_stats and score distribution
    v2_baseline_trades: list[dict] = []
    for ticker in TICKERS:
        prices, iv_df, rv30, ivr = ticker_data[ticker]
        trades = _simulate(ticker, prices, iv_df, rv30, ivr, macro, threshold=-999)
        v2_baseline_trades.extend(trades)

    grade_stats   = _compute_grade_stats(v2_baseline_trades)
    score_dist    = _score_distribution(v2_baseline_trades)

    # Simulate each portfolio strategy
    portfolio_results: list[dict] = []
    for p in PORTFOLIOS:
        if p["threshold"] == -999:
            # V2 baseline — reuse already-simulated trades
            all_trades = v2_baseline_trades
        else:
            # Re-simulate with the grade threshold (non-overlapping applied per ticker)
            all_trades = []
            for ticker in TICKERS:
                prices, iv_df, rv30, ivr = ticker_data[ticker]
                trades = _simulate(
                    ticker, prices, iv_df, rv30, ivr, macro, threshold=p["threshold"]
                )
                all_trades.extend(trades)

        agg = _aggregate(all_trades)

        n_portfolio = agg["n_trades"]
        portfolio_results.append({
            "id":          p["id"],
            "name":        p["name"],
            "short":       p["short"],
            "description": p["description"],
            "threshold":   p["threshold"],
            "color":       p["color"],
            "grades_included": p["grades_included"],
            # Metrics
            "n_trades":             n_portfolio,
            "win_rate":             agg["win_rate"],
            "premium_capture_rate": agg["premium_capture_rate"],
            "avg_return":           agg["avg_return"],
            "median_return":        agg["median_return"],
            "max_drawdown":         agg["max_drawdown"],
            "sharpe":               agg["sharpe"],
            "sortino":              agg["sortino"],
            "profit_factor":        agg["profit_factor"],
            "expectancy":           agg["expectancy"],
            "best_trade":           agg["best_trade"],
            "worst_trade":          agg["worst_trade"],
            "by_ticker":            agg["by_ticker"],
            "by_vix_regime":        agg["by_vix_regime"],
            "equity_points":        agg["equity_points"],
            # Real-IV filter
            "sample_size_warning":  require_real_iv and 0 < n_portfolio < 20,
        })

    return {
        "portfolios":        portfolio_results,
        "grade_stats":       grade_stats,
        "score_distribution": score_dist,
        "score_components":  SCORE_COMPONENTS,
        "grade_meta":        GRADE_META,
        "tickers":           list(TICKERS),
        "data_source":       "mock",
        "real_iv_filter":    agg_filter,
    }
