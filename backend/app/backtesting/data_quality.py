"""
backtesting/data_quality.py
----------------------------
Shared IV data-quality gate for all backtest endpoints.

Usage
-----
    from app.backtesting.data_quality import iv_quality_gate

    gate = iv_quality_gate(list(TICKERS), data_days=400)
    result["iv_quality"] = gate

Gate schema
-----------
{
  "total":                  int,     # total IV observations across all tickers
  "real":                   int,     # rows sourced from live options chain
  "interpolated":           int,     # rows that were interpolated
  "synthetic_fallback":     int,     # rows from OU synthetic model
  "real_iv_pct":            float,   # real / total  (0.0 – 1.0)
  "synthetic_fallback_pct": float,   # synthetic_fallback / total
  "status":                 str,     # "GOOD" | "MIXED" | "POOR"
  "warning":                str | None,
}

Thresholds
----------
  GOOD  : real_iv_pct >= 0.80
  MIXED : 0.40 <= real_iv_pct < 0.80
  POOR  : real_iv_pct < 0.40
"""

from __future__ import annotations

_WARNING_TEXT = (
    "Backtest contains synthetic IV fallback and should not be used "
    "for production decisions."
)


def iv_quality_gate(tickers: list[str], data_days: int) -> dict:
    """
    Aggregate IV data quality across *tickers* for the last *data_days* trading days.

    Returns a gate dict that can be embedded directly in any backtest response
    under the key ``"iv_quality"``.
    """
    from app.data.mock_provider import get_iv_quality_summary

    totals = {"real": 0, "interpolated": 0, "synthetic_fallback": 0, "total": 0}

    for ticker in tickers:
        try:
            summary = get_iv_quality_summary(ticker, data_days)
            for key in ("real", "interpolated", "synthetic_fallback", "total"):
                totals[key] += int(summary.get(key, 0))
        except Exception:
            # Never let quality checks crash a backtest
            totals["synthetic_fallback"] += data_days
            totals["total"]              += data_days

    total = totals["total"]
    real  = totals["real"]

    real_pct      = real / total if total > 0 else 0.0
    synthetic_pct = totals["synthetic_fallback"] / total if total > 0 else 1.0

    if real_pct >= 0.80:
        status  = "GOOD"
        warning = None
    elif real_pct >= 0.40:
        status  = "MIXED"
        warning = _WARNING_TEXT
    else:
        status  = "POOR"
        warning = _WARNING_TEXT

    return {
        "total":                  total,
        "real":                   real,
        "interpolated":           totals["interpolated"],
        "synthetic_fallback":     totals["synthetic_fallback"],
        "real_iv_pct":            round(real_pct,      4),
        "synthetic_fallback_pct": round(synthetic_pct, 4),
        "status":                 status,
        "warning":                warning,
    }
