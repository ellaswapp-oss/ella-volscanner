"""
backtesting/score_optimization.py
----------------------------------
Grid-search over scoring component weights to find the configuration that best
balances premium capture, profit factor, and max drawdown for the short-vol
signal scoring system.

Algorithm
---------
1.  Build a candidate pool: all V2 entries (IV/RV > 1.30 AND IV Rank > 50)
    across all tickers, with every macro feature flag pre-computed and the
    realized-vol outcome fixed.

2.  For each weight combination × threshold:
    a.  Score every candidate with the current weights via a matrix multiply.
    b.  Apply a per-ticker non-overlapping greedy selection at that threshold.
    c.  Compute portfolio metrics and the scalar objective.

3.  Rank all evaluations; return the top 10 plus the current hand-built model.

Objective function (0–1 scale)
-------------------------------
    obj = 0.40 × PCR_norm + 0.35 × PF_norm + 0.25 × MDD_norm − penalty

    PCR_norm  = clip( (pcr + 0.30) / 0.60, 0, 1 )   — range [-30 %, +30 %]
    PF_norm   = clip( pf  / 3.0,           0, 1 )   — profit factor at 3× = 1.0
    MDD_norm  = clip( 1 − mdd / 0.50,      0, 1 )   — 0 % DD=1.0, 50 % DD=0.0
    penalty   = max(0, (10 − n_trades) × 0.05)       — lose 0.05 per trade below 10

Weight search grid  (3 values × 9 components = 19 683 combinations)
--------------------------------------------------------------------
    iv_rv        : [15, 25, 35]
    iv_rank      : [15, 25, 35]
    vix_ts       : [ 0, 15, 25]
    breadth      : [ 0, 10, 20]
    spy_200dma   : [ 0, 10, 20]
    yield_stress : [-20, -10,  0]
    oil_shock    : [-20, -10,  0]
    vix_inversion: [-25, -15,  0]
    vix_spike    : [-25, -15,  0]

Thresholds tested : [50, 65, 80]  → 59 049 total evaluations
"""

from __future__ import annotations

import heapq
import math
import time
from collections import defaultdict
from itertools import product

import numpy as np
import pandas as pd

from app.data.mock_provider import TICKERS, get_price_history, get_iv_data
from app.data.macro_provider import get_macro_series
from app.backtesting.signal_grades import (
    _rolling_rv,
    _rolling_iv_rank,
    _vix_bucket,
    _TDAYS,
    _HOLDING,
    _LOOKBACK,
    _IV_RV_MIN,
    _IV_RANK_MIN,
)
from app.backtesting.metrics import (
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    win_rate,
    profit_factor,
    expectancy,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Order must stay in sync with the feature-matrix column order in
#: _build_candidate_pool.
COMPONENT_KEYS: list[str] = [
    "iv_rv",
    "iv_rank",
    "vix_ts",
    "breadth",
    "spy_200dma",
    "yield_stress",
    "oil_shock",
    "vix_inversion",
    "vix_spike",
]

WEIGHT_GRID: dict[str, list[int]] = {
    "iv_rv":         [15, 25, 35],
    "iv_rank":       [15, 25, 35],
    "vix_ts":        [ 0, 15, 25],
    "breadth":       [ 0, 10, 20],
    "spy_200dma":    [ 0, 10, 20],
    "yield_stress":  [-20, -10,  0],
    "oil_shock":     [-20, -10,  0],
    "vix_inversion": [-25, -15,  0],
    "vix_spike":     [-25, -15,  0],
}

THRESHOLDS: list[int] = [50, 65, 80]

CURRENT_WEIGHTS: dict[str, int] = {
    "iv_rv":         25,
    "iv_rank":       25,
    "vix_ts":        15,
    "breadth":       10,
    "spy_200dma":    10,
    "yield_stress":  -15,
    "oil_shock":     -15,
    "vix_inversion": -20,
    "vix_spike":     -20,
}
CURRENT_THRESHOLD: int = 50

OBJECTIVE_DEFINITION: dict = {
    "formula": "0.40 × PCR_norm + 0.35 × PF_norm + 0.25 × MDD_norm − trade_penalty",
    "weights": {
        "premium_capture": 0.40,
        "profit_factor":   0.35,
        "max_drawdown":    0.25,
    },
    "normalization": {
        "premium_capture": {
            "min": -0.30, "max": 0.30,
            "formula": "clip((pcr + 0.30) / 0.60, 0, 1)",
        },
        "profit_factor": {
            "min": 0.0, "max": 3.0,
            "formula": "clip(pf / 3.0, 0, 1)",
        },
        "max_drawdown": {
            "min": 0.0, "max": 0.50,
            "formula": "clip(1 − mdd / 0.50, 0, 1)  [inverted: lower DD = higher score]",
        },
    },
    "trade_count_penalty": {
        "threshold":                10,
        "penalty_per_missing_trade": 0.05,
        "formula": "max(0, (10 − n_trades) × 0.05)",
    },
}

_TOP_N = 10


# ---------------------------------------------------------------------------
# Candidate pool
# ---------------------------------------------------------------------------

def _build_candidate_pool(
    data_days: int,
    require_real_iv: bool = False,
) -> tuple[list[dict], np.ndarray, list[list[int]]]:
    """
    One-pass data loader: collect every V2-qualifying entry across all tickers
    with all macro feature flags and realised-vol outcome pre-computed.

    Returns
    -------
    candidates : list[dict]
        One entry per V2 candidate.  Keys include trade metadata, pnl, and
        nine boolean feature flags (f_<component_key>).

    feature_matrix : ndarray, shape (n_candidates, 9), float32
        Column j corresponds to COMPONENT_KEYS[j].
        Flags for iv_rv and iv_rank are always 1.0 (V2 gate).
        vix_ts and vix_inversion are complements.

    ticker_groups : list[list[int]]
        Candidate indices grouped by ticker in chronological order.
        Used by _greedy_select to enforce the non-overlapping constraint.
    """
    macro = get_macro_series(days=data_days)
    candidates: list[dict] = []
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

        common_idx = prices.index.intersection(macro.index)
        prices_a   = prices.loc[common_idx]
        iv_df_a    = iv_df.loc[common_idx]
        rv30_a     = rv30.loc[common_idx]
        ivr_a      = ivr.loc[common_idx]
        macro_a    = macro.loc[common_idx]

        n      = len(common_idx)
        warmup = max(_LOOKBACK // 4, _HOLDING + 1, 30)

        spy_arr = macro_a["spy_price"].values
        spy_200 = pd.Series(spy_arr).rolling(200, min_periods=100).mean().values
        vix1m   = macro_a["vix1m"].values
        vix3m   = macro_a["vix3m"].values
        br_arr  = macro_a["breadth"].values
        y_arr   = macro_a["us10y"].values
        c_arr   = macro_a["crude_oil"].values
        y_lag20 = pd.Series(y_arr).shift(20).values
        c_lag20 = pd.Series(c_arr).shift(20).values

        for i in range(warmup, n - _HOLDING):
            iv    = float(iv_df_a["iv30"].iloc[i])
            rv    = float(rv30_a.iloc[i])
            ivr_v = float(ivr_a.iloc[i])

            if not (math.isfinite(iv) and math.isfinite(rv) and math.isfinite(ivr_v)):
                continue
            if rv <= 0:
                continue

            ratio = iv / rv
            if ratio <= _IV_RV_MIN:
                continue
            if ivr_v <= _IV_RANK_MIN:
                continue

            exit_i = i + _HOLDING
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
            vix       = float(vix1m[i])
            vix3m_v   = float(vix3m[i])
            br        = float(br_arr[i])
            dma200    = float(spy_200[i])
            spy_above = math.isfinite(dma200) and float(spy_arr[i]) > dma200

            y_lag    = float(y_lag20[i])
            y_rise   = (float(y_arr[i]) - y_lag) if math.isfinite(y_lag) else 0.0
            c_lag    = float(c_lag20[i])
            c_pct    = ((float(c_arr[i]) - c_lag) / c_lag) if (math.isfinite(c_lag) and c_lag > 0) else 0.0

            ts_contango = vix3m_v > vix

            candidates.append({
                "ticker":      ticker,
                "date_idx":    i,
                "entry_date":  str(common_idx[i].date()),
                "exit_date":   str(common_idx[exit_i].date()),
                "iv_entry":    round(iv, 2),
                "rv_hold":     round(rv_hold, 2),
                "pnl":         round(pnl, 4),
                "vix_entry":   round(vix, 2),
                "vix_bucket":  _vix_bucket(vix),
                "is_winner":   bool(pnl > 0),
                # Feature flags — column order MUST match COMPONENT_KEYS
                "f_iv_rv":        True,
                "f_iv_rank":      True,
                "f_ts_contango":  ts_contango,
                "f_breadth":      bool(br > 55.0),
                "f_spy_200dma":   bool(spy_above),
                "f_yield_stress": bool(math.isfinite(y_rise) and y_rise > 0.40),
                "f_oil_shock":    bool(math.isfinite(c_pct)  and c_pct  > 0.10),
                "f_ts_inverted":  not ts_contango,
                "f_vix_spike":    bool(vix >= 32.0),
            })

    def _build_agg(fs: list[dict]) -> dict:
        _before = sum(f.get("rows_before_filter", 0) for f in fs)
        _excl   = sum(f.get("rows_excluded",      0) for f in fs)
        _remain = sum(f.get("rows_remaining",     0) for f in fs)
        _starts = [f["usable_date_start"] for f in fs if f.get("usable_date_start")]
        _ends   = [f["usable_date_end"]   for f in fs if f.get("usable_date_end")]
        return {
            "enabled":            require_real_iv,
            "rows_before_filter": _before,
            "rows_excluded":      _excl,
            "rows_remaining":     _remain,
            "pct_remaining":      round(_remain / _before, 4) if _before > 0 else 0.0,
            "usable_date_start":  min(_starts) if _starts else None,
            "usable_date_end":    max(_ends)   if _ends   else None,
        }

    if not candidates:
        return [], np.zeros((0, 9), dtype=np.float32), [], _build_agg(filter_summaries)

    # Feature matrix — column order matches COMPONENT_KEYS
    _flag_cols = [
        "f_iv_rv", "f_iv_rank", "f_ts_contango", "f_breadth", "f_spy_200dma",
        "f_yield_stress", "f_oil_shock", "f_ts_inverted", "f_vix_spike",
    ]
    feature_matrix = np.array(
        [[float(c[k]) for k in _flag_cols] for c in candidates],
        dtype=np.float32,
    )

    # Ticker groups (already in date_idx order within each ticker)
    ticker_to_idx: dict[str, list[int]] = defaultdict(list)
    for j, c in enumerate(candidates):
        ticker_to_idx[c["ticker"]].append(j)
    ticker_groups = [ticker_to_idx[t] for t in TICKERS if t in ticker_to_idx]

    return candidates, feature_matrix, ticker_groups, _build_agg(filter_summaries)


# ---------------------------------------------------------------------------
# Non-overlapping greedy selection
# ---------------------------------------------------------------------------

def _greedy_select_full(
    scores: list[float],
    candidates: list[dict],
    ticker_groups: list[list[int]],
    threshold: float,
) -> list[dict]:
    """
    Per-ticker non-overlapping greedy trade selection — returns full candidate
    dicts (used when computing metrics for the top-N after the grid search).
    """
    selected: list[dict] = []
    for group in ticker_groups:
        last_exit_idx = -1
        for j in group:
            if scores[j] >= threshold:
                c = candidates[j]
                if c["date_idx"] > last_exit_idx:
                    selected.append(c)
                    last_exit_idx = c["date_idx"] + _HOLDING
    return selected


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _fast_metrics(trades: list[dict]) -> dict:
    """Portfolio metrics used both during the grid search and for final output."""
    _null: dict = {
        "n_trades": 0, "win_rate": None, "premium_capture_rate": None,
        "profit_factor": None, "max_drawdown": None,
        "sharpe": None, "sortino": None,
        "avg_return": None, "median_return": None, "expectancy": None,
        "best_trade": None, "worst_trade": None, "return_std": None,
    }
    if not trades:
        return _null

    pnls    = [t["pnl"] for t in trades]
    pnl_arr = np.array(pnls)

    def _s(v: float) -> float | None:
        return round(float(v), 4) if math.isfinite(float(v)) else None

    sorted_t = sorted(trades, key=lambda t: (t["exit_date"], t["ticker"]))
    eq = 1.0
    eq_pts = [1.0]
    for t in sorted_t:
        eq *= 1.0 + t["pnl"]
        eq_pts.append(eq)
    eq_arr = np.array(eq_pts)

    total_iv  = sum(t["iv_entry"] for t in trades)
    total_cap = sum(t["iv_entry"] - t["rv_hold"] for t in trades)
    pcr = round(total_cap / total_iv, 4) if total_iv > 0 else None

    best  = max(trades, key=lambda t: t["pnl"])
    worst = min(trades, key=lambda t: t["pnl"])

    return {
        "n_trades":             len(trades),
        "win_rate":             round(float(win_rate(pnls)), 3),
        "premium_capture_rate": pcr,
        "profit_factor":        _s(profit_factor(pnls)),
        "max_drawdown":         _s(max_drawdown(eq_arr)),
        "sharpe":               _s(sharpe_ratio(pnl_arr, periods_per_year=1)),
        "sortino":              _s(sortino_ratio(pnl_arr, periods_per_year=1)),
        "avg_return":           _s(float(np.mean(pnl_arr))),
        "median_return":        _s(float(np.median(pnl_arr))),
        "expectancy":           _s(expectancy(pnls)),
        "best_trade":           best,
        "worst_trade":          worst,
        # Population std of per-trade PnL — used by pcr_stability dispersion penalty
        "return_std":           _s(float(np.std(pnl_arr))) if len(pnl_arr) > 0 else None,
    }


def _equity_points(trades: list[dict]) -> list[dict]:
    sorted_t = sorted(trades, key=lambda t: (t["exit_date"], t["ticker"]))
    eq = 1.0
    pts: list[dict] = []
    for j, t in enumerate(sorted_t):
        eq *= 1.0 + t["pnl"]
        pts.append({"trade_n": j + 1, "equity": round(eq, 4)})
    return pts


# ---------------------------------------------------------------------------
# Objective function
# ---------------------------------------------------------------------------

def _objective(metrics: dict) -> tuple[float, dict]:
    """
    Returns (objective_score, breakdown_dict).

    All components are normalised to [0, 1] before weighting so that no single
    metric dominates due to scale differences.
    """
    n   = metrics["n_trades"]
    pcr = metrics["premium_capture_rate"]
    pf  = metrics["profit_factor"]
    mdd = metrics["max_drawdown"]

    if n == 0:
        return 0.0, {
            "pcr_component": 0.0, "pf_component": 0.0,
            "mdd_component": 0.0, "trade_penalty": 0.0,
        }

    # max_drawdown() from metrics.py returns a negative float
    # (e.g. −0.43 for a 43 % peak-to-trough decline).  Take abs() here.
    mdd_abs  = abs(mdd or 0.50)
    pcr_norm = float(np.clip(((pcr or -0.30) + 0.30) / 0.60, 0.0, 1.0))
    pf_norm  = float(np.clip((pf  or 0.0) / 3.0,             0.0, 1.0))
    mdd_norm = float(np.clip(1.0 - mdd_abs / 0.50,           0.0, 1.0))

    penalty  = max(0.0, (10 - n) * 0.05)

    raw = 0.40 * pcr_norm + 0.35 * pf_norm + 0.25 * mdd_norm - penalty

    # No floor: allow negative scores so the breakdown always sums to
    # objective_score and models can be meaningfully compared below zero.
    return round(raw, 4), {
        "pcr_component":  round(0.40 * pcr_norm, 4),
        "pf_component":   round(0.35 * pf_norm,  4),
        "mdd_component":  round(0.25 * mdd_norm, 4),
        "trade_penalty":  round(-penalty, 4),
    }


# ---------------------------------------------------------------------------
# Weight sensitivity aggregation
# ---------------------------------------------------------------------------

def _build_sensitivity(
    sensitivity_raw: dict[str, dict[int, list[float]]],
) -> dict[str, list[dict]]:
    """
    For each component and each grid value, compute the average objective
    score across all combos that used that value.  Useful for identifying
    which weight levels consistently improve the objective.
    """
    result: dict[str, list[dict]] = {}
    for key in COMPONENT_KEYS:
        entries = []
        for w_val in sorted(sensitivity_raw[key]):
            vals = sensitivity_raw[key][w_val]
            entries.append({
                "weight":        w_val,
                "avg_objective": round(sum(vals) / len(vals), 4),
                "count":         len(vals),
            })
        result[key] = entries
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_score_optimization(data_days: int = 400, require_real_iv: bool = False) -> dict:
    """
    Execute the full grid search and return a ranked result set.

    Returns
    -------
    dict with keys:
        top_models          — top 10 weight configurations ranked by objective
        current_model       — evaluation of the hand-built baseline weights
        comparison          — delta metrics between best model and current
        search_stats        — timing, counts, candidate pool size
        weight_sensitivity  — per-component, per-value average objective
        weight_grid         — the search space definition
        objective_definition— objective function specification
        tickers, data_source
    """
    t0 = time.perf_counter()

    # ------------------------------------------------------------------ #
    # 1.  Build candidate pool (one data pass)                            #
    # ------------------------------------------------------------------ #
    candidates, feature_matrix, ticker_groups, agg_filter = _build_candidate_pool(
        data_days, require_real_iv=require_real_iv
    )

    if not candidates:
        return {
            "error": "No V2 candidates found in the specified date range.",
            "top_models": [], "current_model": None, "comparison": None,
            "search_stats": {"total_evaluated": 0},
            "real_iv_filter": agg_filter,
            "sample_size_warning": False,
        }

    # ------------------------------------------------------------------ #
    # 2.  Evaluate current hand-built model                               #
    # ------------------------------------------------------------------ #
    cur_w_vec = np.array(
        [CURRENT_WEIGHTS[k] for k in COMPONENT_KEYS], dtype=np.float32
    )
    cur_scores  = (feature_matrix @ cur_w_vec).tolist()
    cur_trades  = _greedy_select_full(cur_scores, candidates, ticker_groups, CURRENT_THRESHOLD)
    cur_metrics = _fast_metrics(cur_trades)
    cur_obj, cur_breakdown = _objective(cur_metrics)
    current_model: dict = {
        "rank":               None,
        "objective_score":    cur_obj,
        "weights":            CURRENT_WEIGHTS.copy(),
        "threshold":          CURRENT_THRESHOLD,
        "objective_breakdown": cur_breakdown,
        "equity_points":      _equity_points(cur_trades),
        **cur_metrics,
    }

    # ------------------------------------------------------------------ #
    # 3.  Grid search — vectorised scoring, inlined objective             #
    # ------------------------------------------------------------------ #
    grid_values  = [WEIGHT_GRID[k] for k in COMPONENT_KEYS]
    all_combos   = list(product(*grid_values))
    total_combos = len(all_combos)

    # One batched matmul: (n_combos, 9) × (9, n_cand) → (n_combos, n_cand)
    weights_matrix   = np.array(all_combos, dtype=np.float32)        # (n_combos, 9)
    all_scores_matrix = weights_matrix @ feature_matrix.T             # (n_combos, n_cand)

    # Pre-extract parallel arrays for the tight inner loop (avoids dict lookups)
    _cand_date_idx = [c["date_idx"] for c in candidates]
    _cand_pnl      = [c["pnl"]      for c in candidates]
    _cand_iv       = [c["iv_entry"] for c in candidates]
    _cand_rv       = [c["rv_hold"]  for c in candidates]
    _cand_exit     = [c["exit_date"] for c in candidates]
    _holding       = _HOLDING

    sensitivity_raw: dict[str, dict[int, list[float]]] = {
        k: defaultdict(list) for k in COMPONENT_KEYS
    }

    # Collect ALL evaluations so we can deduplicate later.
    # Each entry: (obj, combo_tuple, threshold, metrics_fingerprint)
    all_evals: list[tuple] = []
    eval_count = 0

    for ci, combo in enumerate(all_combos):
        scores_row = all_scores_matrix[ci].tolist()   # Python list — fast indexing

        for threshold in THRESHOLDS:
            # ----- non-overlapping greedy selection (inline) ----------
            sel: list[int] = []
            for group in ticker_groups:
                last_exit = -1
                for j in group:
                    if scores_row[j] >= threshold and _cand_date_idx[j] > last_exit:
                        sel.append(j)
                        last_exit = _cand_date_idx[j] + _holding

            # ----- inline objective (no numpy/function-call overhead) --
            n = len(sel)
            if n == 0:
                obj = 0.0
            else:
                # Sort indices by exit date for correct MDD path
                sel_sorted = sorted(sel, key=lambda j: _cand_exit[j])
                wins = losses = total_iv = total_cap = 0.0
                eq = peak = 1.0
                mdd = 0.0
                for j in sel_sorted:
                    p  = _cand_pnl[j]
                    iv = _cand_iv[j]
                    rv = _cand_rv[j]
                    total_iv  += iv
                    total_cap += iv - rv
                    if p > 0:  wins   += p
                    else:      losses -= p
                    eq *= 1.0 + p
                    if eq > peak:
                        peak = eq
                    dd = (peak - eq) / peak
                    if dd > mdd:
                        mdd = dd

                pcr = (total_cap / total_iv) if total_iv > 0 else -0.30
                pf  = (wins / losses) if losses > 0 else (3.0 if wins > 0 else 0.0)

                pcr_norm = (pcr + 0.30) / 0.60
                pcr_norm = 0.0 if pcr_norm < 0.0 else (1.0 if pcr_norm > 1.0 else pcr_norm)
                pf_norm  = pf / 3.0
                pf_norm  = 0.0 if pf_norm  < 0.0 else (1.0 if pf_norm  > 1.0 else pf_norm)
                mdd_norm = 1.0 - mdd / 0.50
                mdd_norm = 0.0 if mdd_norm < 0.0 else (1.0 if mdd_norm > 1.0 else mdd_norm)
                penalty  = (10 - n) * 0.05
                if penalty < 0.0:
                    penalty = 0.0

                obj = 0.40 * pcr_norm + 0.35 * pf_norm + 0.25 * mdd_norm - penalty

            eval_count += 1

            # Sensitivity accumulation
            for k, v in zip(COMPONENT_KEYS, combo):
                sensitivity_raw[k][v].append(obj)

            # metrics fingerprint for deduplication
            fp = (n, round(obj, 4))
            all_evals.append((obj, combo, threshold, fp))

    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    # ------------------------------------------------------------------ #
    # 4.  Select top-N with weight diversity  (deduplicate by fingerprint,#
    #     prefer configs that differ most from current weights)           #
    # ------------------------------------------------------------------ #
    all_evals.sort(key=lambda x: -x[0])  # descending by objective

    cur_w_arr = np.array([CURRENT_WEIGHTS[k] for k in COMPONENT_KEYS], dtype=float)

    # Minimum quality bar: require at least 3 trades and positive objective.
    # This prevents degenerate n=0 / n=1 models from filling the top-N slots
    # when the candidate pool is small (mock data).  Real data will have many
    # qualifying configurations.
    _MIN_TRADES_FINGERPRINT = 3

    seen_fps: set = set()
    diverse_candidates: list[tuple] = []
    fallback_candidates: list[tuple] = []

    for obj, combo, threshold, fp in all_evals:
        n_fp = fp[0]  # n_trades is first element of fingerprint tuple
        if n_fp < _MIN_TRADES_FINGERPRINT:
            continue
        if fp not in seen_fps:
            seen_fps.add(fp)
            diverse_candidates.append((obj, combo, threshold))
        else:
            fallback_candidates.append((obj, combo, threshold))

    # Fill top-N from unique fingerprints first; pad with diverse weights if needed
    top_pool = diverse_candidates[:_TOP_N]
    if len(top_pool) < _TOP_N:
        # Among fallbacks (same metrics, different weights): sort by
        # (obj DESC, weight_diversity DESC) — prefer equal-quality configs
        # that are most different from the current baseline.
        fallback_candidates.sort(
            key=lambda x: (
                -x[0],
                -float(np.sum(np.abs(np.array(x[1], dtype=float) - cur_w_arr))),
            )
        )
        top_pool += fallback_candidates[: _TOP_N - len(top_pool)]

    top_models: list[dict] = []
    for rank, (obj, combo, threshold) in enumerate(top_pool[:_TOP_N], start=1):
        # Compute full metrics for top-N
        w_vec  = np.array(combo, dtype=np.float32)
        scores = (feature_matrix @ w_vec).tolist()
        trades = _greedy_select_full(scores, candidates, ticker_groups, threshold)
        metrics = _fast_metrics(trades)
        _, breakdown = _objective(metrics)

        # Weight diversity score (vs current baseline) — used by frontend
        w_arr  = np.array(combo, dtype=float)
        w_dist = round(float(np.sum(np.abs(w_arr - cur_w_arr))), 1)

        # Re-derive obj from corrected _objective (abs MDD) to keep score
        # and breakdown consistent.  Grid-search inline obj was used only
        # for ranking; the final displayed score must match the breakdown.
        obj_final, breakdown = _objective(metrics)

        top_models.append({
            "rank":               rank,
            "objective_score":    obj_final,
            "weights":            dict(zip(COMPONENT_KEYS, combo)),
            "threshold":          threshold,
            "objective_breakdown": breakdown,
            "weight_distance_from_current": w_dist,
            "equity_points":      _equity_points(trades),
            **metrics,
        })

    # ------------------------------------------------------------------ #
    # 5.  Comparison: best vs current                                     #
    # ------------------------------------------------------------------ #
    comparison: dict | None = None
    if top_models:
        best = top_models[0]
        comparison = {
            "objective_improvement": round(
                best["objective_score"] - cur_obj, 4
            ),
            "pcr_improvement": round(
                (best["premium_capture_rate"] or 0.0) -
                (cur_metrics["premium_capture_rate"] or 0.0), 4
            ),
            "profit_factor_improvement": round(
                (best["profit_factor"] or 0.0) -
                (cur_metrics["profit_factor"] or 0.0), 4
            ),
            # positive = drawdown got smaller (improvement)
            "drawdown_improvement": round(
                (cur_metrics["max_drawdown"] or 0.0) -
                (best["max_drawdown"] or 0.0), 4
            ),
            "trade_count_change": best["n_trades"] - cur_metrics["n_trades"],
            "sharpe_improvement": round(
                (best["sharpe"] or 0.0) -
                (cur_metrics["sharpe"] or 0.0), 4
            ),
        }

    # ------------------------------------------------------------------ #
    # 6.  Return                                                          #
    # ------------------------------------------------------------------ #
    best_n = top_models[0]["n_trades"] if top_models else 0
    return {
        "top_models":           top_models,
        "current_model":        current_model,
        "comparison":           comparison,
        "search_stats": {
            "total_evaluated":    eval_count,
            "weight_combinations": total_combos,
            "thresholds_tested":  THRESHOLDS,
            "candidates_in_pool": len(candidates),
            "elapsed_ms":         elapsed_ms,
        },
        "weight_sensitivity":   _build_sensitivity(sensitivity_raw),
        "weight_grid":          WEIGHT_GRID,
        "objective_definition": OBJECTIVE_DEFINITION,
        "tickers":              list(TICKERS),
        "data_source":          "mock",
        "real_iv_filter":       agg_filter,
        "sample_size_warning":  require_real_iv and 0 < best_n < 20,
    }
