"""
backtesting/walk_forward.py
----------------------------
Walk-forward validation for score-weight optimisation with three objective modes.

Method
------
1.  Build the full V2 candidate pool once (all tickers, all dates).
2.  Assign each candidate a global date index from the macro calendar.
3.  Define rolling windows:
        train = 252 trading days  (1 year)
        test  =  63 trading days  (1 quarter)
        step  =  63 trading days  (advance one quarter each fold)
4.  For every window, run ONE grid search (19 683 combos × 3 thresholds) that
    simultaneously finds the best weights for all three objective modes:
        balanced      — 0.40 PCR + 0.35 PF + 0.25 MDD
        pcr_stability — 0.60 PCR + 0.20 PF + 0.20 MDD + dispersion penalty
        conservative  — 0.40 PCR + 0.20 PF + 0.40 MDD
    The active ``objective_mode`` determines which mode's weights are used for
    the primary summary / equity-curve output.
5.  For all modes and the current hand-built model, apply weights to the TEST
    set and record out-of-sample (OOS) metrics.
6.  Pool all OOS trades across windows for a sequential equity curve.
7.  Compute weight turnover (L1 weight change between consecutive windows) per mode.
8.  Aggregate mode_comparison: IS obj / OOS obj / avg degradation / key OOS metrics.

Degradation interpretation
--------------------------
    OOS − in-sample per metric.  Negative = OOS worse (expected for any optimised system).
    Large negatives indicate overfitting; values near zero indicate good generalisation.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from itertools import product

import numpy as np

from app.data.mock_provider import TICKERS
from app.data.macro_provider import get_macro_series
from app.backtesting.score_optimization import (
    _build_candidate_pool,
    _greedy_select_full,
    _fast_metrics,
    _objective,
    WEIGHT_GRID,
    THRESHOLDS,
    COMPONENT_KEYS,
    CURRENT_WEIGHTS,
    CURRENT_THRESHOLD,
)
from app.backtesting.objective_modes import (
    OBJECTIVE_MODES,
    MODE_IDS,
    objective_for_metrics,
)
from app.backtesting.signal_grades import _HOLDING

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_TRAIN = 252
_DEFAULT_TEST  = 63
_DEFAULT_STEP  = 63
_DEFAULT_DAYS  = 756

_VALID_MODES = set(MODE_IDS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _subset_ticker_groups(
    candidates: list[dict],
    global_indices: list[int],
) -> list[list[int]]:
    """
    Given a list of global candidate indices, return per-ticker groups of
    LOCAL indices (0 … len(global_indices)-1) in chronological order.

    These local indices map directly into the subset feature matrix passed to
    _greedy_select_full and the inline matmul loops.
    """
    ticker_to_local: dict[str, list[int]] = defaultdict(list)
    for local_i, global_i in enumerate(global_indices):
        ticker_to_local[candidates[global_i]["ticker"]].append(local_i)
    return [ticker_to_local[t] for t in TICKERS if t in ticker_to_local]


def _window_equity(trades: list[dict], window_id: int) -> list[dict]:
    """Sequential equity curve for one window's trades, tagged with window_id."""
    sorted_t = sorted(trades, key=lambda t: (t["exit_date"], t["ticker"]))
    eq = 1.0
    pts: list[dict] = []
    for j, t in enumerate(sorted_t):
        eq *= 1.0 + t["pnl"]
        pts.append({
            "trade_n":   j + 1,
            "equity":    round(eq, 4),
            "window_id": window_id,
            "date":      t["exit_date"],
        })
    return pts


def _pooled_equity(all_trades: list[dict]) -> list[dict]:
    """Sequential equity across all trades sorted by exit date."""
    sorted_t = sorted(all_trades, key=lambda t: (t["exit_date"], t["ticker"]))
    eq = 1.0
    pts: list[dict] = []
    for j, t in enumerate(sorted_t):
        eq *= 1.0 + t["pnl"]
        pts.append({
            "trade_n":   j + 1,
            "equity":    round(eq, 4),
            "window_id": t.get("_window_id"),
            "date":      t["exit_date"],
        })
    return pts


def _safe_metrics(m: dict) -> dict:
    """Strip internal / non-serialisable keys before returning to the caller."""
    return {k: v for k, v in m.items()
            if k not in ("best_trade", "worst_trade", "_obj")}


def _degradation(
    train_obj: float | None,
    test_obj:  float | None,
    train_m:   dict,
    test_m:    dict,
) -> dict:
    """
    Per-metric degradation = OOS − in-sample.

    Negative values indicate the metric got worse out-of-sample (expected).
    For max_drawdown we compare absolute magnitudes; a positive value means
    larger drawdown in the test period (worse).
    """
    def _d(a, b):
        if a is None or b is None:
            return None
        return round((b or 0.0) - (a or 0.0), 4)

    mdd_train = abs(train_m.get("max_drawdown") or 0.0)
    mdd_test  = abs(test_m.get("max_drawdown")  or 0.0)

    return {
        "objective":             round((test_obj  or 0.0) - (train_obj or 0.0), 4),
        "win_rate":              _d(train_m.get("win_rate"),             test_m.get("win_rate")),
        "premium_capture_rate":  _d(train_m.get("premium_capture_rate"), test_m.get("premium_capture_rate")),
        "profit_factor":         _d(train_m.get("profit_factor"),        test_m.get("profit_factor")),
        "sharpe":                _d(train_m.get("sharpe"),               test_m.get("sharpe")),
        # positive = more drawdown in test (worse)
        "max_drawdown_abs":      round(mdd_test - mdd_train, 4),
        "avg_return":            _d(train_m.get("avg_return"),           test_m.get("avg_return")),
        "n_trades":              (test_m.get("n_trades") or 0) - (train_m.get("n_trades") or 0),
    }


def _avg_degradation(window_results: list[dict]) -> dict:
    """Average degradation across all windows that have at least one OOS trade."""
    active = [r for r in window_results if (r["test_metrics"].get("n_trades") or 0) > 0]
    if not active:
        return {}
    keys = list(active[0]["degradation"].keys())
    result: dict = {}
    for k in keys:
        vals = [r["degradation"][k] for r in active if r["degradation"].get(k) is not None]
        result[k] = round(sum(vals) / len(vals), 4) if vals else None
    return result


# ---------------------------------------------------------------------------
# Multi-mode per-window grid search
# ---------------------------------------------------------------------------

def _mini_grid_search_multi(
    local_candidates: list[dict],
    feature_matrix:   np.ndarray,        # (n_cand, 9)
    ticker_groups:    list[list[int]],
    weights_matrix:   np.ndarray,        # (n_combos, 9)
    all_combos:       list[tuple],
    mode_ids:         list[str],
) -> dict[str, tuple[float, tuple, int]]:
    """
    Full grid search on a window-scoped candidate subset, returning the best
    (inline_obj, combo, threshold) for every requested objective mode in ONE pass.

    Uses the same batch-matmul + inline-objective approach as run_score_optimization:
    the expensive greedy selection is performed once per (combo, threshold) pair and
    the shared portfolio stats (PCR, PF, MDD, return_std) are then fed into each
    mode's objective function independently.

    Returns
    -------
    dict mapping mode_id → (best_inline_obj, best_combo, best_threshold)
    """
    fallback = {m: (float("-inf"), all_combos[0], CURRENT_THRESHOLD) for m in mode_ids}
    if not local_candidates or feature_matrix.shape[0] == 0:
        return fallback

    # (n_combos, n_cand)
    all_scores = weights_matrix @ feature_matrix.T

    # Parallel arrays for tight inner loop
    cand_date_idx = [c["date_idx"] for c in local_candidates]
    cand_pnl      = [c["pnl"]      for c in local_candidates]
    cand_iv       = [c["iv_entry"] for c in local_candidates]
    cand_rv       = [c["rv_hold"]  for c in local_candidates]
    cand_exit     = [c["exit_date"] for c in local_candidates]

    # Pre-extract mode config tuples for inner loop (avoids repeated dict lookups)
    mode_configs: list[tuple] = []
    for m in mode_ids:
        cfg = OBJECTIVE_MODES[m]
        mode_configs.append((
            m,
            cfg["pcr_w"], cfg["pf_w"], cfg["mdd_w"],
            cfg["dispersion_penalty"], cfg["dispersion_w"],
            cfg["trade_penalty_thresh"], cfg["trade_penalty_per"],
        ))

    best_obj:   dict[str, float] = {m: float("-inf") for m in mode_ids}
    best_combo: dict[str, tuple] = {m: all_combos[0] for m in mode_ids}
    best_thr:   dict[str, int]   = {m: CURRENT_THRESHOLD for m in mode_ids}

    for ci, combo in enumerate(all_combos):
        scores = all_scores[ci].tolist()

        for threshold in THRESHOLDS:
            # --- non-overlapping greedy selection (shared across modes) -----
            sel: list[int] = []
            for group in ticker_groups:
                last_exit = -1
                for j in group:
                    if scores[j] >= threshold and cand_date_idx[j] > last_exit:
                        sel.append(j)
                        last_exit = cand_date_idx[j] + _HOLDING

            n = len(sel)
            if n == 0:
                continue

            # --- shared metric computation ----------------------------------
            sel_s = sorted(sel, key=lambda j: cand_exit[j])
            wins = losses = total_iv = total_cap = 0.0
            pnl_sum = pnl_sq_sum = 0.0
            eq = peak = 1.0
            mdd = 0.0
            for j in sel_s:
                p  = cand_pnl[j]
                iv = cand_iv[j]
                rv = cand_rv[j]
                total_iv  += iv
                total_cap += iv - rv
                if p > 0:
                    wins   += p
                else:
                    losses -= p
                pnl_sum    += p
                pnl_sq_sum += p * p
                eq *= 1.0 + p
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak
                if dd > mdd:
                    mdd = dd

            pcr = total_cap / total_iv if total_iv > 0 else -0.30
            pf  = wins / losses if losses > 0 else (3.0 if wins > 0 else 0.0)

            # Shared normalised components (clamped to [0, 1])
            pcr_n = (pcr + 0.30) / 0.60
            pcr_n = 0.0 if pcr_n < 0.0 else (1.0 if pcr_n > 1.0 else pcr_n)
            pf_n  = pf / 3.0
            pf_n  = 0.0 if pf_n  < 0.0 else (1.0 if pf_n  > 1.0 else pf_n)
            mdd_n = 1.0 - mdd / 0.50
            mdd_n = 0.0 if mdd_n < 0.0 else (1.0 if mdd_n > 1.0 else mdd_n)

            # Population std for dispersion penalty
            mean_p  = pnl_sum / n
            var_p   = pnl_sq_sum / n - mean_p * mean_p
            ret_std = var_p ** 0.5 if var_p > 0.0 else 0.0
            disp_n  = ret_std / 0.20
            if disp_n > 1.0:
                disp_n = 1.0

            # --- per-mode objective ----------------------------------------
            for (m_id, pcr_w, pf_w, mdd_w,
                 disp_pen, disp_w, tpt, tpp) in mode_configs:

                pen = (tpt - n) * tpp
                if pen < 0.0:
                    pen = 0.0

                obj = pcr_w * pcr_n + pf_w * pf_n + mdd_w * mdd_n - pen
                if disp_pen:
                    obj -= disp_w * disp_n

                if obj > best_obj[m_id]:
                    best_obj[m_id]   = obj
                    best_combo[m_id] = combo
                    best_thr[m_id]   = threshold

    return {
        m: (best_obj[m], best_combo[m], best_thr[m])
        for m in mode_ids
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_walk_forward(
    data_days:       int  = _DEFAULT_DAYS,
    train_days:      int  = _DEFAULT_TRAIN,
    test_days:       int  = _DEFAULT_TEST,
    step_days:       int  = _DEFAULT_STEP,
    objective_mode:  str  = "balanced",
    require_real_iv: bool = False,
) -> dict:
    """
    Execute walk-forward validation across three objective modes simultaneously.

    The ``objective_mode`` parameter selects which mode's per-window weights are
    used for the primary summary, equity curves, and degradation table.  All three
    modes are always evaluated; their aggregate results appear in ``mode_comparison``.

    Response keys
    -------------
    windows              — per-fold detail (dates, weights, IS/OOS/current metrics,
                           degradation, weight_turnover)
    summary              — pooled IS, OOS, current-OOS + avg degradation + avg turnover
    oos_equity           — sequential equity across all OOS test windows (active mode)
    is_equity            — sequential equity across all in-sample train windows
    current_oos_equity   — current hand-built model equity on all test windows
    mode_comparison      — per-mode IS obj / OOS obj / avg degradation / OOS metrics
    objective_mode       — active mode id
    mode_definitions     — human-readable mode labels and descriptions
    params               — window configuration
    elapsed_ms, tickers, data_source
    """
    t0 = time.perf_counter()

    if objective_mode not in _VALID_MODES:
        objective_mode = "balanced"

    # ------------------------------------------------------------------ #
    # 1.  Build full candidate pool once                                  #
    # ------------------------------------------------------------------ #
    candidates, feature_matrix, _, agg_filter = _build_candidate_pool(
        data_days, require_real_iv=require_real_iv
    )

    if not candidates:
        return {
            "error": "No V2 candidates found. Increase data_days.",
            "windows": [], "summary": None,
            "real_iv_filter": agg_filter,
        }

    # ------------------------------------------------------------------ #
    # 2.  Assign global date indices from the macro calendar              #
    # ------------------------------------------------------------------ #
    macro        = get_macro_series(days=data_days)
    macro_dates  = list(macro.index)
    n_macro      = len(macro_dates)
    date_to_gidx = {str(d.date()): i for i, d in enumerate(macro_dates)}

    for c in candidates:
        c["_gidx"] = date_to_gidx.get(c["entry_date"], -1)

    # ------------------------------------------------------------------ #
    # 3.  Define rolling windows                                          #
    # ------------------------------------------------------------------ #
    windows_def: list[dict] = []
    start = 0
    while start + train_days + test_days <= n_macro:
        te = min(start + train_days + test_days - 1, n_macro - 1)
        windows_def.append({
            "train_start":      start,
            "train_end":        start + train_days,
            "test_start":       start + train_days,
            "test_end":         start + train_days + test_days,
            "train_date_start": str(macro_dates[start].date()),
            "train_date_end":   str(macro_dates[start + train_days - 1].date()),
            "test_date_start":  str(macro_dates[start + train_days].date()),
            "test_date_end":    str(macro_dates[te].date()),
        })
        start += step_days

    if not windows_def:
        return {
            "error": (
                f"Not enough data for walk-forward windows. "
                f"Need ≥ {train_days + test_days} trading days, have {n_macro}. "
                f"Increase data_days to at least {train_days + test_days + 63}."
            ),
            "windows": [], "summary": None,
            "real_iv_filter": agg_filter,
        }

    # ------------------------------------------------------------------ #
    # 4.  Pre-build weights matrix — reused across all windows            #
    # ------------------------------------------------------------------ #
    all_combos     = list(product(*[WEIGHT_GRID[k] for k in COMPONENT_KEYS]))
    weights_matrix = np.array(all_combos, dtype=np.float32)   # (n_combos, 9)
    cur_w_vec      = np.array([CURRENT_WEIGHTS[k] for k in COMPONENT_KEYS], dtype=np.float32)

    # ------------------------------------------------------------------ #
    # 5.  Process each window                                             #
    # ------------------------------------------------------------------ #
    window_results:      list[dict] = []
    all_is_trades:       list[dict] = []
    all_oos_trades:      list[dict] = []
    all_cur_oos_trades:  list[dict] = []

    # Per-mode accumulators for mode_comparison
    all_mode_oos_trades:   dict[str, list[dict]]  = {m: [] for m in MODE_IDS}
    mode_window_is_objs:   dict[str, list[float]] = {m: [] for m in MODE_IDS}
    mode_window_oos_objs:  dict[str, list[float]] = {m: [] for m in MODE_IDS}
    mode_prev_weights:     dict[str, dict | None] = {m: None for m in MODE_IDS}
    mode_turnover_sums:    dict[str, float]       = {m: 0.0 for m in MODE_IDS}
    mode_turnover_counts:  dict[str, int]         = {m: 0   for m in MODE_IDS}

    for wid, wd in enumerate(windows_def):
        # --- filter candidates by global date index --------------------
        train_idx = [i for i, c in enumerate(candidates)
                     if wd["train_start"] <= c["_gidx"] < wd["train_end"]]
        test_idx  = [i for i, c in enumerate(candidates)
                     if wd["test_start"]  <= c["_gidx"] < wd["test_end"]]

        train_cands = [candidates[i] for i in train_idx]
        test_cands  = [candidates[i] for i in test_idx]

        train_fm = feature_matrix[train_idx] if train_idx else np.zeros((0, 9), dtype=np.float32)
        test_fm  = feature_matrix[test_idx]  if test_idx  else np.zeros((0, 9), dtype=np.float32)

        train_tg = _subset_ticker_groups(candidates, train_idx)
        test_tg  = _subset_ticker_groups(candidates, test_idx)

        # --- multi-mode grid search on train set -----------------------
        multi_result = _mini_grid_search_multi(
            train_cands, train_fm, train_tg, weights_matrix, all_combos, MODE_IDS
        )

        # --- current model on test set (unchanged baseline) ------------
        if test_cands:
            cu_scores = (test_fm @ cur_w_vec).tolist()
            cu_trades = _greedy_select_full(cu_scores, test_cands, test_tg, CURRENT_THRESHOLD)
        else:
            cu_trades = []
        for t in cu_trades:
            t["_window_id"] = wid + 1
        cu_metrics = _fast_metrics(cu_trades)
        cu_obj, _  = _objective(cu_metrics)
        all_cur_oos_trades.extend(cu_trades)

        # --- per-mode IS + OOS metrics + weight turnover ---------------
        mode_data: dict[str, dict] = {}

        for m in MODE_IDS:
            _, m_combo, m_thresh = multi_result[m]
            m_w_vec      = np.array(m_combo, dtype=np.float32)
            m_weights_d  = dict(zip(COMPONENT_KEYS, m_combo))

            # IS: apply best weights to train set
            if train_cands:
                m_tr_sc  = (train_fm @ m_w_vec).tolist()
                m_tr_tr  = _greedy_select_full(m_tr_sc, train_cands, train_tg, m_thresh)
            else:
                m_tr_tr  = []
            for t in m_tr_tr:
                t["_window_id"] = wid + 1
            m_tr_met = _fast_metrics(m_tr_tr)
            m_tr_obj = objective_for_metrics(m_tr_met, m)
            mode_window_is_objs[m].append(m_tr_obj)

            # OOS: apply best weights to test set
            if test_cands:
                m_te_sc  = (test_fm @ m_w_vec).tolist()
                m_te_tr  = _greedy_select_full(m_te_sc, test_cands, test_tg, m_thresh)
            else:
                m_te_tr  = []
            for t in m_te_tr:
                t["_window_id"] = wid + 1
            all_mode_oos_trades[m].extend(m_te_tr)
            m_te_met = _fast_metrics(m_te_tr)
            m_te_obj = objective_for_metrics(m_te_met, m)
            mode_window_oos_objs[m].append(m_te_obj)

            # Weight turnover: L1 change from previous window's weights
            if mode_prev_weights[m] is not None:
                l1 = sum(
                    abs(m_weights_d.get(k, 0) - mode_prev_weights[m].get(k, 0))
                    for k in COMPONENT_KEYS
                )
                mode_turnover_sums[m]  += l1
                mode_turnover_counts[m] += 1
                wt = round(l1, 1)
            else:
                wt = 0.0
            mode_prev_weights[m] = m_weights_d

            mode_data[m] = {
                "combo":        m_combo,
                "thresh":       m_thresh,
                "weights_d":    m_weights_d,
                "tr_trades":    m_tr_tr,
                "tr_metrics":   m_tr_met,
                "tr_obj":       m_tr_obj,
                "te_trades":    m_te_tr,
                "te_metrics":   m_te_met,
                "te_obj":       m_te_obj,
                "wt":           wt,
            }

        # --- extract active mode as primary output ---------------------
        active      = mode_data[objective_mode]
        tr_trades   = active["tr_trades"]
        tr_metrics  = active["tr_metrics"]
        tr_obj      = active["tr_obj"]
        te_trades   = active["te_trades"]
        te_metrics  = active["te_metrics"]
        te_obj      = active["te_obj"]
        best_combo  = active["combo"]
        best_thresh = active["thresh"]
        best_w_dict = active["weights_d"]
        active_wt   = active["wt"]

        deg = _degradation(tr_obj, te_obj, tr_metrics, te_metrics)

        all_is_trades.extend(tr_trades)
        all_oos_trades.extend(te_trades)

        window_results.append({
            "window_id":   wid + 1,
            "train_period": {
                "start":          wd["train_date_start"],
                "end":            wd["train_date_end"],
                "n_trading_days": train_days,
            },
            "test_period": {
                "start":          wd["test_date_start"],
                "end":            wd["test_date_end"],
                "n_trading_days": test_days,
            },
            "n_train_candidates":     len(train_cands),
            "n_test_candidates":      len(test_cands),
            "best_weights":           best_w_dict,
            "best_threshold":         best_thresh,
            "train_objective":        round(tr_obj, 4),
            "test_objective":         round(te_obj, 4),
            "current_test_objective": round(cu_obj, 4),
            "train_metrics":          _safe_metrics(tr_metrics),
            "test_metrics":           _safe_metrics(te_metrics),
            "current_test_metrics":   _safe_metrics(cu_metrics),
            "degradation":            deg,
            "weight_turnover":        active_wt,
            # Per-window equity (for the window detail table)
            "train_equity":  _window_equity(tr_trades, wid + 1),
            "test_equity":   _window_equity(te_trades, wid + 1),
        })

    # ------------------------------------------------------------------ #
    # 6.  Pool all OOS / IS trades for summary + equity curves            #
    # ------------------------------------------------------------------ #
    oos_metrics     = _fast_metrics(all_oos_trades)
    oos_obj, _      = objective_for_metrics(oos_metrics, objective_mode), None
    oos_obj         = objective_for_metrics(oos_metrics, objective_mode)
    is_metrics      = _fast_metrics(all_is_trades)
    is_obj          = objective_for_metrics(is_metrics, objective_mode)
    cur_oos_metrics = _fast_metrics(all_cur_oos_trades)
    cur_oos_obj, _  = _objective(cur_oos_metrics)

    avg_deg = _avg_degradation(window_results)

    # Weight turnover summary for active mode
    if mode_turnover_counts[objective_mode] > 0:
        avg_wt = round(
            mode_turnover_sums[objective_mode] / mode_turnover_counts[objective_mode], 1
        )
    else:
        avg_wt = None

    # ------------------------------------------------------------------ #
    # 7.  Mode comparison table                                           #
    # ------------------------------------------------------------------ #
    mode_comparison: dict[str, dict] = {}

    for m in MODE_IDS:
        is_objs  = mode_window_is_objs[m]
        oos_objs = mode_window_oos_objs[m]

        avg_is  = round(sum(is_objs)  / len(is_objs),  4) if is_objs  else None
        avg_oos = round(sum(oos_objs) / len(oos_objs), 4) if oos_objs else None
        n_w     = len(is_objs)
        avg_deg_obj = (
            round(
                sum(oos_objs[i] - is_objs[i] for i in range(n_w)) / n_w, 4
            )
            if n_w > 0 else None
        )

        m_oos_met = _fast_metrics(all_mode_oos_trades[m])
        avg_m_wt  = (
            round(mode_turnover_sums[m] / mode_turnover_counts[m], 1)
            if mode_turnover_counts[m] > 0 else None
        )

        mode_comparison[m] = {
            "label":               OBJECTIVE_MODES[m]["label"],
            "description":         OBJECTIVE_MODES[m]["description"],
            "is_obj_avg":          avg_is,
            "oos_obj_avg":         avg_oos,
            "avg_degradation_obj": avg_deg_obj,
            "oos_n_trades":        m_oos_met.get("n_trades", 0),
            "oos_win_rate":        m_oos_met.get("win_rate"),
            "oos_pcr":             m_oos_met.get("premium_capture_rate"),
            "oos_profit_factor":   m_oos_met.get("profit_factor"),
            "oos_sharpe":          m_oos_met.get("sharpe"),
            "oos_mdd_abs":         round(abs(m_oos_met.get("max_drawdown") or 0.0), 4),
            "avg_weight_turnover": avg_m_wt,
        }

    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    return {
        "windows": window_results,
        "summary": {
            "n_windows":              len(window_results),
            "in_sample_pooled_obj":   round(is_obj,      4),
            "oos_pooled_obj":         round(oos_obj,     4),
            "current_oos_pooled_obj": round(cur_oos_obj, 4),
            "in_sample_pooled":       _safe_metrics(is_metrics),
            "oos_pooled":             _safe_metrics(oos_metrics),
            "current_oos_pooled":     _safe_metrics(cur_oos_metrics),
            "avg_degradation":        avg_deg,
            "avg_weight_turnover":    avg_wt,
        },
        "oos_equity":         _pooled_equity(all_oos_trades),
        "is_equity":          _pooled_equity(all_is_trades),
        "current_oos_equity": _pooled_equity(all_cur_oos_trades),
        "mode_comparison":    mode_comparison,
        "objective_mode":     objective_mode,
        "mode_definitions":   {
            m: {
                "label":       OBJECTIVE_MODES[m]["label"],
                "description": OBJECTIVE_MODES[m]["description"],
            }
            for m in MODE_IDS
        },
        "params": {
            "train_days":    train_days,
            "test_days":     test_days,
            "step_days":     step_days,
            "data_days":     data_days,
            "n_windows":     len(window_results),
            "n_macro_dates": n_macro,
        },
        "component_keys": COMPONENT_KEYS,
        "tickers":        list(TICKERS),
        "data_source":    "mock",
        "elapsed_ms":     elapsed_ms,
        "real_iv_filter": agg_filter,
    }
