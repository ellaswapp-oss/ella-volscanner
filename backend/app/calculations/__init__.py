# Engine exports — all new code should import from app.calculations.engine directly
# or use these names. Engine functions return nan (not None) for undefined results.
from app.calculations.engine import (
    realized_volatility,
    volatility_risk_premium,
    z_score,
    term_structure,
    term_structure_slope,   # alias for term_structure — kept for compatibility
    regime_score,
    trade_recommendation,
)

# Legacy exports — backward-compatible names and signatures.
# iv_rv_ratio and iv_rank are exported from legacy so existing tests
# that assert `is None` on bad inputs continue to pass.
from app.calculations.legacy import (
    realized_vol,
    realized_vol_series,
    iv_rv_ratio,       # returns None (not nan) on bad input — legacy contract
    vol_risk_premium,
    iv_rank,
    iv_rv_zscore,
    term_structure_slopes,
    vol_regime_score,
    trade_signal,
    skew_metrics,
)

__all__ = [
    # engine
    "realized_volatility",
    "volatility_risk_premium",
    "z_score",
    "term_structure",
    "term_structure_slope",
    "regime_score",
    "trade_recommendation",
    # legacy
    "realized_vol",
    "realized_vol_series",
    "iv_rv_ratio",
    "vol_risk_premium",
    "iv_rank",
    "iv_rv_zscore",
    "term_structure_slopes",
    "vol_regime_score",
    "trade_signal",
    "skew_metrics",
]
