"""
Market endpoints — focused, single-concern responses.

Data source: dispatched via snapshot_builder → mock or live (Polygon options).
Each handler pulls from snapshot_builder and reshapes into the endpoint's schema.

Phase 2 changes
---------------
- _get_snapshot() now calls build_snapshot() (provider-agnostic dispatcher)
- VolatilityResponse includes provider/iv_source/iv_rank_is_proxy/as_of_date
- TermStructureResponse includes provider/iv_source; adds 14d curve point when available
- TradeSignalResponse includes all metadata + macro_penalties
"""

from fastapi import APIRouter, HTTPException
from app.core.config import settings
from app.models.schemas import (
    IVSurfaceMetadata,
    MacroPenalty,
    RegimeResponse,
    RegimeTicker,
    VolatilityResponse,
    TermStructureResponse,
    CurvePoint,
    TradeSignalResponse,
    SignalFactor,
)
from app.services.snapshot_builder import build_snapshot, build_dashboard_snapshot
from app.data.mock_provider import get_macro_data

router = APIRouter(prefix="/api", tags=["market"])

_PLAYBOOK: dict[str, list[str]] = {
    "BUY_PREMIUM": [
        "Long straddles / strangles before known catalysts",
        "Buy ATM options — own gamma, not theta",
        "Calendar spreads: buy front month, sell back month",
        "Debit spreads for directional exposure with defined risk",
    ],
    "NEUTRAL": [
        "Reduce overall position size until signal clarifies",
        "Iron condors with wider-than-normal wings",
        "Ratio spreads with net-zero or slight long premium bias",
        "Avoid large naked short positions",
    ],
    "SELL_SELECTIVE": [
        "Cash-secured puts on quality names at technical support",
        "Covered calls at near-term resistance levels",
        "Iron condors on low-beta, range-bound underlyings",
        "Short strangles with 30–45 DTE, strict delta limits",
    ],
    "SELL_AGGRESSIVE": [
        "Short strangles across the portfolio — maximize theta",
        "Large iron condors / iron butterflies on liquid names",
        "Sell premium across the full term structure",
        "Maintain delta-neutral book and strict position limits",
    ],
}

_CONTRIBUTION_THRESHOLDS = [
    (25, "low"),
    (50, "moderate"),
    (75, "high"),
    (100, "very_high"),
]


def _contribution(normalized_score: float) -> str:
    """Map a 0–100 normalized component score to a contribution label."""
    for threshold, label in _CONTRIBUTION_THRESHOLDS:
        if normalized_score <= threshold:
            return label
    return "very_high"


def _normalize_iv_rank(v: float) -> float:
    return round(float(v), 1)  # already 0–100


def _normalize_vrp(v: float) -> float:
    return round(float((v + 0.5) / 1.0 * 100), 1)


def _normalize_ratio(v: float) -> float:
    return round(float((v - 0.5) / 1.5 * 100), 1)


def _normalize_z(v: float) -> float:
    return round(float((v + 3.0) / 6.0 * 100), 1)


def _get_snapshot(ticker: str) -> dict:
    sym = ticker.upper()
    if sym not in settings.tickers:
        raise HTTPException(
            status_code=404,
            detail=f"{sym} not supported. Available: {list(settings.tickers)}",
        )
    return build_snapshot(sym)


def _iv_surface_meta(snap: dict) -> IVSurfaceMetadata | None:
    """Convert the ``iv_surface_metadata`` dict from a snapshot to a Pydantic model."""
    raw = snap.get("iv_surface_metadata")
    return IVSurfaceMetadata(**raw) if raw is not None else None


# ---------------------------------------------------------------------------
# GET /api/regime
# ---------------------------------------------------------------------------

@router.get("/regime", response_model=RegimeResponse)
def get_regime():
    """
    Market-wide volatility regime.

    Returns the composite regime score across index ETFs (SPY, QQQ, IWM)
    and a per-ticker breakdown for all supported names.

    Phase 2 note: always uses mock data regardless of DATA_PROVIDER.
    Full live regime support is Phase 3.
    """
    dashboard = build_dashboard_snapshot()
    macro = get_macro_data()

    ticker_rows = [
        RegimeTicker(
            ticker=snap["ticker"],
            regime_score=snap["regime_score"],
            signal=snap["signal"],
            iv_rank=snap["iv_rank"],
            iv_rv_ratio=snap["iv_rv_ratio"],
            vrp=snap["vrp"],
            iv_rv_zscore=snap["iv_rv_zscore"],
        )
        for snap in dashboard["tickers"].values()
        if "error" not in snap
    ]
    ticker_rows.sort(key=lambda r: r.regime_score, reverse=True)

    return RegimeResponse(
        market_regime=dashboard["market_regime"],
        market_signal=dashboard["market_signal"],
        tickers=ticker_rows,
        vix=macro["vix"],
        vix_regime=macro["vix_regime"],
    )


# ---------------------------------------------------------------------------
# GET /api/volatility/{ticker}
# ---------------------------------------------------------------------------

@router.get("/volatility/{ticker}", response_model=VolatilityResponse)
def get_volatility(ticker: str):
    """
    Full volatility surface for one ticker.

    Returns IV surface (iv7/14/30/60/90), multi-window realized vol (rv10/20/30/60),
    IV Rank, IV/RV ratio, VRP, IV/RV z-score with 60-day sparklines, and
    metadata about the data source (provider, iv_source, iv_rank_is_proxy, as_of_date).
    """
    snap = _get_snapshot(ticker)
    return VolatilityResponse(
        ticker=snap["ticker"],
        price=snap["price"],
        iv=snap["iv"],
        rv=snap["rv"],
        iv_rank=snap["iv_rank"],
        iv_rv_ratio=snap["iv_rv_ratio"],
        vrp=snap["vrp"],
        iv_rv_zscore=snap["iv_rv_zscore"],
        sparklines=snap["sparklines"],
        provider=snap.get("provider", "mock"),
        iv_source=snap.get("iv_source", "synthetic"),
        iv_rank_is_proxy=snap.get("iv_rank_is_proxy", False),
        as_of_date=snap.get("as_of_date", ""),
        iv_surface_metadata=_iv_surface_meta(snap),
    )


# ---------------------------------------------------------------------------
# GET /api/term-structure/{ticker}
# ---------------------------------------------------------------------------

@router.get("/term-structure/{ticker}", response_model=TermStructureResponse)
def get_term_structure(ticker: str):
    """
    IV term structure curve for one ticker.

    Returns the IV at each tenor as ordered curve points (including 14d when
    available from the live options chain), all four slopes, and a shape
    classification (contango / inverted / humped / flat).
    """
    snap = _get_snapshot(ticker)
    iv   = snap["iv"]
    ts   = snap["term_structure"]

    # Build curve — only include tenors where IV is non-None.
    # iv7/iv60/iv90 are None when outside DTE tolerance; iv14 only from live chain.
    curve: list[CurvePoint] = []
    if iv.get("iv7")  is not None:
        curve.append(CurvePoint(tenor="7d",  days=7,  iv=iv["iv7"]))
    if iv.get("iv14") is not None:
        curve.append(CurvePoint(tenor="14d", days=14, iv=iv["iv14"]))
    if iv.get("iv30") is not None:
        curve.append(CurvePoint(tenor="30d", days=30, iv=iv["iv30"]))
    if iv.get("iv60") is not None:
        curve.append(CurvePoint(tenor="60d", days=60, iv=iv["iv60"]))
    if iv.get("iv90") is not None:
        curve.append(CurvePoint(tenor="90d", days=90, iv=iv["iv90"]))

    return TermStructureResponse(
        ticker=snap["ticker"],
        curve=curve,
        front_slope=ts["front_slope"],
        mid_slope=ts["mid_slope"],
        back_slope=ts["back_slope"],
        total_slope=ts["total_slope"],
        curvature=ts["curvature"],
        shape=ts["shape"],
        provider=snap.get("provider", "mock"),
        iv_source=snap.get("iv_source", "synthetic"),
        iv_surface_metadata=_iv_surface_meta(snap),
    )


# ---------------------------------------------------------------------------
# GET /api/trade-signal/{ticker}
# ---------------------------------------------------------------------------

@router.get("/trade-signal/{ticker}", response_model=TradeSignalResponse)
def get_trade_signal(ticker: str):
    """
    Trade recommendation for one ticker.

    Returns the regime score (post macro-penalty adjustment for real provider),
    directional signal, per-factor breakdown, playbook, and full metadata
    including any macro penalties that were applied.
    """
    snap      = _get_snapshot(ticker)
    signal_key = snap["signal"]["signal"]

    ivr   = snap["iv_rank"]
    vrp   = snap["vrp"]
    ratio = snap["iv_rv_ratio"]
    z     = snap["iv_rv_zscore"]

    factors = {
        "iv_rank": SignalFactor(
            value=round(ivr, 1),
            weight=settings.weight_iv_rank,
            contribution=_contribution(_normalize_iv_rank(ivr)),
        ),
        "vrp": SignalFactor(
            value=round(vrp, 4),
            weight=settings.weight_vrp,
            contribution=_contribution(_normalize_vrp(vrp)),
        ),
        "iv_rv_ratio": SignalFactor(
            value=round(ratio, 3),
            weight=settings.weight_iv_rv_ratio,
            contribution=_contribution(_normalize_ratio(ratio)),
        ),
        "z_score": SignalFactor(
            value=round(z, 2),
            weight=settings.weight_z_score,
            contribution=_contribution(_normalize_z(z)),
        ),
    }

    # Build MacroPenalty model if metadata is present
    macro_pen_dict = snap.get("macro_penalties")
    macro_penalty  = (
        MacroPenalty(**macro_pen_dict) if macro_pen_dict is not None else None
    )

    return TradeSignalResponse(
        ticker=snap["ticker"],
        regime_score=snap["regime_score"],
        signal=snap["signal"],
        factors=factors,
        playbook=_PLAYBOOK[signal_key],
        provider=snap.get("provider", "mock"),
        iv_source=snap.get("iv_source", "synthetic"),
        iv_rank_is_proxy=snap.get("iv_rank_is_proxy", False),
        as_of_date=snap.get("as_of_date", ""),
        macro_penalties=macro_penalty,
        iv_surface_metadata=_iv_surface_meta(snap),
    )
