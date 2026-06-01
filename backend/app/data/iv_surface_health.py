"""
data/iv_surface_health.py
--------------------------
Sanity-check rules for ATM IV surfaces.

Rules
-----
1. IV range              — each tenor IV must be 1% ≤ IV ≤ 300%.  Violations → invalid.
2. Tenor jump            — adjacent tenor jump > JUMP_THRESHOLD (10 vol pts) → warning.
3. Duplicate IV          — tenors ≥2 steps apart with nearly identical IVs → warning
                           (indicates the same expiration was reused for multiple tenors).
4. Missing tenor         — tenor could not be computed (None/NaN value, no expirations
                           in chain at all) → warning.
5. Tenor out-of-tolerance — nearest expiration exceeded the per-tenor DTE tolerance;
                           IV was set to null rather than using a stale expiration.
6. Duplicate expiration  — two within-tolerance tenors anchored to the same expiration
                           (surface has a flat segment) → warning.

Spread quality thresholds
--------------------------
Expressed as (ask − bid) / mid:
  TIGHT     < 5%
  FAIR      5% – 15%
  WIDE      15% – 30%
  VERY_WIDE > 30%
  NO_QUOTE  bid or ask is None / zero mid
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IV_MIN           = 1.0    # percent — below this is almost certainly bad data
IV_MAX           = 300.0  # percent — above this is almost certainly bad data
JUMP_THRESHOLD   = 10.0   # vol points — max allowable adjacent-tenor jump
DUPLICATE_THRESH = 0.1    # vol points — difference < this → considered identical

TENORS: list[str] = ["iv7", "iv14", "iv30", "iv60", "iv90"]
TENOR_DAYS: dict[str, int] = {
    "iv7": 7, "iv14": 14, "iv30": 30, "iv60": 60, "iv90": 90,
}

# Mirror of options_utils.TENOR_TOLERANCE — kept in sync manually.
TENOR_TOLERANCE: dict[str, int] = {
    "iv7":  4,
    "iv14": 7,
    "iv30": 10,
    "iv60": 14,
    "iv90": 21,
}

# Spread quality buckets: (upper_threshold_fraction, label)
_SPREAD_LEVELS: list[tuple[float, str]] = [
    (0.05, "TIGHT"),
    (0.15, "FAIR"),
    (0.30, "WIDE"),
    (1e9,  "VERY_WIDE"),
]


# ---------------------------------------------------------------------------
# Spread quality
# ---------------------------------------------------------------------------

def spread_quality(bid: float | None, ask: float | None) -> str:
    """
    Categorise a bid/ask pair by spread quality.

    Returns
    -------
    "TIGHT" | "FAIR" | "WIDE" | "VERY_WIDE" | "NO_QUOTE"
    """
    if bid is None or ask is None:
        return "NO_QUOTE"
    mid = (bid + ask) / 2.0
    if mid <= 0.0:
        return "NO_QUOTE"
    ratio = (ask - bid) / mid
    for threshold, label in _SPREAD_LEVELS:
        if ratio <= threshold:
            return label
    return "VERY_WIDE"


def spread_quality_score(quality: str) -> int:
    """Numeric rank for sorting (lower = better)."""
    return {"TIGHT": 0, "FAIR": 1, "WIDE": 2, "VERY_WIDE": 3, "NO_QUOTE": 4}.get(quality, 4)


# ---------------------------------------------------------------------------
# IV surface validation
# ---------------------------------------------------------------------------

def validate_iv_surface(
    iv_surface: dict[str, float | None],
    tenor_meta: dict[str, dict] | None = None,
) -> tuple[str, list[dict]]:
    """
    Run all sanity checks on an IV surface dict.

    Parameters
    ----------
    iv_surface : dict
        Keys ``"iv7", "iv14", "iv30", "iv60", "iv90"`` with values in **percent**
        (e.g. ``15.8`` means 15.8 %). ``None`` values are treated as missing.
    tenor_meta : dict, optional
        Per-tenor metadata from ``calculate_atm_iv_by_tenor`` (the
        ``_tenor_meta`` key).  When supplied, enables Rules 5 and 6 and
        improves the Rule 1 (missing tenor) message.

    Returns
    -------
    (status, warnings)
        status   : ``"healthy"`` | ``"suspicious"`` | ``"invalid"``
        warnings : list of dicts with at minimum ``severity``, ``code``, ``message``.
    """
    warnings: list[dict] = []
    has_invalid = False

    # ── Rule 1 & 5: None tenors — distinguish missing vs. out-of-tolerance ──
    for k in TENORS:
        v = iv_surface.get(k)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            continue  # valid IV — skip

        m = (tenor_meta or {}).get(k, {})
        within      = m.get("within_tolerance")
        actual_dte  = m.get("actual_dte")

        if within is False and actual_dte is not None:
            # Rule 5: expiration existed but was outside tolerance
            tol = TENOR_TOLERANCE[k]
            warnings.append({
                "severity":   "warning",
                "code":       "tenor_out_of_tolerance",
                "tenor":      k,
                "target_dte": m["target_dte"],
                "actual_dte": actual_dte,
                "dte_diff":   m["dte_diff"],
                "tolerance":  tol,
                "expiration": m.get("expiration"),
                "message": (
                    f"{k} (target {m['target_dte']}DTE): nearest expiration is "
                    f"{actual_dte}DTE (diff={m['dte_diff']}d, tolerance=±{tol}d) — "
                    f"IV set to null rather than reusing a stale expiration."
                ),
            })
        else:
            # Rule 1: no usable expiration found at all
            warnings.append({
                "severity": "warning",
                "code":     "missing_tenor",
                "tenor":    k,
                "message": (
                    f"{k} could not be computed — no expiration found near "
                    f"{TENOR_DAYS[k]} DTE in the options chain."
                ),
            })

    # Build dict of valid (non-None, non-NaN) values for subsequent checks
    valid: dict[str, float] = {}
    for k in TENORS:
        v = iv_surface.get(k)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            valid[k] = float(v)

    # ── Rule 2: IV out of range ───────────────────────────────────────────
    for k, v in valid.items():
        if v < IV_MIN or v > IV_MAX:
            has_invalid = True
            warnings.append({
                "severity": "invalid",
                "code":     "iv_out_of_range",
                "tenor":    k,
                "value":    round(v, 2),
                "message": (
                    f"{k} = {v:.2f}% is outside the valid range "
                    f"[{IV_MIN:.0f}%, {IV_MAX:.0f}%]."
                ),
            })

    # ── Rule 3: adjacent tenor jumps ─────────────────────────────────────
    for i in range(len(TENORS) - 1):
        k1, k2 = TENORS[i], TENORS[i + 1]
        v1, v2 = valid.get(k1), valid.get(k2)
        if v1 is not None and v2 is not None:
            jump = abs(v2 - v1)
            if jump > JUMP_THRESHOLD:
                warnings.append({
                    "severity": "warning",
                    "code":     "tenor_jump",
                    "tenors":   [k1, k2],
                    "jump":     round(jump, 2),
                    "message": (
                        f"{k1} → {k2} jump of {jump:.1f} vol pts exceeds "
                        f"the {JUMP_THRESHOLD:.0f} vol-pt threshold."
                    ),
                })

    # ── Rule 4: duplicate IVs across distant tenors (≥2 steps apart) ─────
    for i in range(len(TENORS)):
        for j in range(i + 2, len(TENORS)):
            k1, k2 = TENORS[i], TENORS[j]
            v1, v2 = valid.get(k1), valid.get(k2)
            if v1 is not None and v2 is not None:
                diff = abs(v2 - v1)
                if diff < DUPLICATE_THRESH:
                    warnings.append({
                        "severity": "warning",
                        "code":     "duplicate_iv",
                        "tenors":   [k1, k2],
                        "diff":     round(diff, 4),
                        "message": (
                            f"{k1} ({v1:.2f}%) and {k2} ({v2:.2f}%) have nearly "
                            "identical IVs — they may be anchored to the same expiration."
                        ),
                    })

    # ── Rule 6: duplicate expiration across within-tolerance tenors ───────
    if tenor_meta:
        exp_to_tenors: dict[str, list[str]] = {}
        for k in TENORS:
            m = tenor_meta.get(k, {})
            if m.get("within_tolerance") and m.get("expiration"):
                exp_to_tenors.setdefault(m["expiration"], []).append(k)
        for exp, tenors in exp_to_tenors.items():
            if len(tenors) > 1:
                warnings.append({
                    "severity":   "warning",
                    "code":       "duplicate_expiration",
                    "tenors":     tenors,
                    "expiration": exp,
                    "message": (
                        f"Expiration {exp} is used for {len(tenors)} tenors "
                        f"({', '.join(tenors)}) — the IV surface has a flat segment."
                    ),
                })

    # ── Status ────────────────────────────────────────────────────────────
    if has_invalid:
        status = "invalid"
    elif warnings:
        status = "suspicious"
    else:
        status = "healthy"

    return status, warnings
