"""
backtesting/objective_modes.py
-------------------------------
Objective function definitions for walk-forward validation.

Three modes are evaluated in parallel during every grid-search window so the
caller can compare their out-of-sample behaviour without running the search
three times.

Modes
-----
balanced      — 0.40 PCR + 0.35 PF + 0.25 MDD  (the original hand-tuned objective)
pcr_stability — 0.60 PCR + 0.20 PF + 0.20 MDD  + dispersion penalty + lower trade threshold
conservative  — 0.40 PCR + 0.20 PF + 0.40 MDD  (strongly penalises drawdown)

Normalisation (shared across all modes)
-----------------------------------------
    PCR_norm  = clip( (pcr + 0.30) / 0.60, 0, 1 )
    PF_norm   = clip( pf  / 3.0,           0, 1 )
    MDD_norm  = clip( 1 − mdd_abs / 0.50,  0, 1 )

Dispersion penalty (pcr_stability only)
-----------------------------------------
    disp_norm = clip( return_std / 0.20, 0, 1 )
    penalty  += dispersion_w × disp_norm
    (return_std = population std of per-trade PnL; 20% std → full penalty)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Mode definitions
# ---------------------------------------------------------------------------

OBJECTIVE_MODES: dict[str, dict] = {
    "balanced": {
        "label":       "Balanced",
        "description": "0.40 PCR + 0.35 PF + 0.25 MDD",
        "pcr_w":       0.40,
        "pf_w":        0.35,
        "mdd_w":       0.25,
        "dispersion_penalty":   False,
        "dispersion_w":         0.00,
        "trade_penalty_thresh": 10,
        "trade_penalty_per":    0.05,
    },
    "pcr_stability": {
        "label":       "PCR Stability",
        "description": "0.60 PCR + 0.20 PF + 0.20 MDD + dispersion penalty",
        "pcr_w":       0.60,
        "pf_w":        0.20,
        "mdd_w":       0.20,
        "dispersion_penalty":   True,
        "dispersion_w":         0.10,
        "trade_penalty_thresh": 5,
        "trade_penalty_per":    0.05,
    },
    "conservative": {
        "label":       "Conservative",
        "description": "0.40 PCR + 0.20 PF + 0.40 MDD",
        "pcr_w":       0.40,
        "pf_w":        0.20,
        "mdd_w":       0.40,
        "dispersion_penalty":   False,
        "dispersion_w":         0.00,
        "trade_penalty_thresh": 10,
        "trade_penalty_per":    0.05,
    },
}

#: Stable iteration order — used for consistent response ordering.
MODE_IDS: list[str] = list(OBJECTIVE_MODES.keys())


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------

def objective_for_metrics(metrics: dict, mode_id: str = "balanced") -> float:
    """
    Compute the objective score for a metrics dict under the given mode.

    Parameters
    ----------
    metrics : dict
        Output from ``_fast_metrics()``.  Required keys: ``n_trades``,
        ``premium_capture_rate``, ``profit_factor``, ``max_drawdown``.
        The ``return_std`` key is additionally consumed by ``pcr_stability``.

    mode_id : str
        One of ``'balanced'``, ``'pcr_stability'``, ``'conservative'``.
        Falls back to ``'balanced'`` for unrecognised values.

    Returns
    -------
    float
        Objective score.  Can be negative for poorly-performing configurations.
    """
    if mode_id not in OBJECTIVE_MODES:
        mode_id = "balanced"
    mode = OBJECTIVE_MODES[mode_id]

    n   = metrics.get("n_trades") or 0
    pcr = metrics.get("premium_capture_rate")
    pf  = metrics.get("profit_factor")
    mdd = metrics.get("max_drawdown")

    if n == 0:
        return 0.0

    mdd_abs  = abs(mdd or 0.50)
    pcr_norm = max(0.0, min(1.0, ((pcr or -0.30) + 0.30) / 0.60))
    pf_norm  = max(0.0, min(1.0, (pf  or 0.0)   / 3.0))
    mdd_norm = max(0.0, min(1.0, 1.0 - mdd_abs  / 0.50))

    penalty = max(0.0, (mode["trade_penalty_thresh"] - n) * mode["trade_penalty_per"])

    raw = (
        mode["pcr_w"] * pcr_norm
        + mode["pf_w"] * pf_norm
        + mode["mdd_w"] * mdd_norm
        - penalty
    )

    if mode["dispersion_penalty"]:
        ret_std = float(metrics.get("return_std") or 0.0)
        disp_n  = max(0.0, min(1.0, ret_std / 0.20))
        raw    -= mode["dispersion_w"] * disp_n

    return round(raw, 4)
