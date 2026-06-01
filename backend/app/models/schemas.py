"""Pydantic response models — enforces shape at the API boundary and drives OpenAPI docs."""

from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class RVMetrics(BaseModel):
    rv10: float = Field(..., description="10-day annualized realized vol (%)")
    rv20: float = Field(..., description="20-day annualized realized vol (%)")
    rv30: float = Field(..., description="30-day annualized realized vol (%)")
    rv60: float = Field(..., description="60-day annualized realized vol (%)")


class IVMetrics(BaseModel):
    iv7:  Optional[float] = Field(None, description="7-day implied vol (%) — None when not within DTE tolerance")
    iv14: Optional[float] = Field(None, description="14-day implied vol (%) — live options chain only")
    iv30: Optional[float] = Field(None, description="30-day implied vol (%) — None when not available from chain (see iv_surface_metadata)")
    iv60: Optional[float] = Field(None, description="60-day implied vol (%) — None when not within DTE tolerance")
    iv90: Optional[float] = Field(None, description="90-day implied vol (%) — None when not within DTE tolerance")


class IVSurfaceMetadata(BaseModel):
    """
    IV surface quality metadata injected into market endpoint responses.

    iv_surface_status
        "healthy"           — all core tenors (7/30/60/90) available from chain
        "partial"           — iv30 available but some long-tenor data missing
        "unavailable"       — iv30 not available from chain; signal uses RV-based estimate
        "synthetic_fallback"— options chain fetch failed; all IVs are synthetic

    required_tenor_available
        True  — iv30 was computed from a real options chain within tolerance
        False — iv30 is missing or synthetic (see iv_surface_status)

    term_structure_quality
        "full"        — all segment slopes (front/mid/back) are computable
        "partial"     — some slopes are None (missing 60d/90d expirations)
        "unavailable" — iv30 is absent; cannot compute any slope
    """
    iv_surface_status:        str = Field(..., description="healthy | partial | unavailable | synthetic_fallback")
    required_tenor_available: bool = Field(..., description="True when iv30 comes from a real chain within tolerance")
    available_tenors:         list[str] = Field(default_factory=list, description="Tenor keys with valid IV from chain")
    missing_tenors:           list[str] = Field(default_factory=list, description="Tenor keys with no valid IV")
    term_structure_quality:   str = Field(..., description="full | partial | unavailable")


class TermStructure(BaseModel):
    front_slope: Optional[float] = Field(None, description="IV30 - IV7 (None when iv7 unavailable)")
    mid_slope:   Optional[float] = Field(None, description="IV60 - IV30 (None when iv60 unavailable)")
    back_slope:  Optional[float] = Field(None, description="IV90 - IV60 (None when iv60 or iv90 unavailable)")
    total_slope: Optional[float] = Field(None, description="IV90 - IV7 (None when iv7 or iv90 unavailable)")
    curvature:   Optional[float] = Field(None, description="front_slope - back_slope; positive = decelerating contango")
    shape: Literal["contango", "inverted", "humped", "flat", "partial"]


class Skew(BaseModel):
    skew_25d: float = Field(..., description="25-delta put IV minus 25-delta call IV")
    atm_iv:   float = Field(..., description="ATM implied vol (%)")
    put_25d:  float = Field(..., description="25-delta put IV (%)")
    call_25d: float = Field(..., description="25-delta call IV (%)")


class TradeSignal(BaseModel):
    signal: Literal["BUY_PREMIUM", "NEUTRAL", "SELL_SELECTIVE", "SELL_AGGRESSIVE"]
    label:  str
    color:  Literal["green", "yellow", "orange", "red"]
    description: str


class Sparklines(BaseModel):
    dates: list[str]
    rv30:  list[float]
    iv30:  list[float]


class TickerSnapshot(BaseModel):
    ticker:        str
    price:         float
    rv:            RVMetrics
    iv:            IVMetrics
    iv_rank:       float = Field(..., ge=0, le=100, description="IV Rank 0–100")
    iv_rv_ratio:   float = Field(..., description="IV30 / RV30")
    vrp:           float = Field(..., description="(IV30 - RV30) / RV30")
    iv_rv_zscore:  float = Field(..., description="Z-score of IV-RV spread vs 1-yr history")
    term_structure: TermStructure
    skew:          Skew
    regime_score:  float = Field(..., ge=0, le=100, description="Composite vol regime score")
    signal:        TradeSignal
    sparklines:    Sparklines
    error:         Optional[str] = None


class MacroPenalty(BaseModel):
    total_penalty: float = Field(..., description="Points deducted from regime score (0–25)")
    reasons: list[str] = Field(default_factory=list, description="Human-readable penalty reasons")
    available: bool = Field(..., description="False when macro data could not be fetched")


class MacroData(BaseModel):
    vix:                  float
    vix_regime:           str
    credit_spread:        float = Field(..., description="HY OAS spread (%)")
    yield_curve:          float = Field(..., description="10Y-2Y Treasury spread (%)")
    financial_conditions: float = Field(..., description="Financial conditions index")


class DashboardResponse(BaseModel):
    tickers:       dict[str, TickerSnapshot]
    macro:         MacroData
    market_regime: float = Field(..., ge=0, le=100)
    market_signal: TradeSignal


class TickerListResponse(BaseModel):
    tickers: list[str]


class HealthResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# /api/regime
# ---------------------------------------------------------------------------

class RegimeTicker(BaseModel):
    ticker:       str
    regime_score: float = Field(..., ge=0, le=100)
    signal:       TradeSignal
    iv_rank:      float
    iv_rv_ratio:  float
    vrp:          float
    iv_rv_zscore: float


class RegimeResponse(BaseModel):
    market_regime:  float = Field(..., ge=0, le=100, description="Composite index-ETF regime score")
    market_signal:  TradeSignal
    tickers:        list[RegimeTicker]
    vix:            float
    vix_regime:     str
    data_source:    str = "mock"


# ---------------------------------------------------------------------------
# /api/volatility/{ticker}
# ---------------------------------------------------------------------------

class VolatilityResponse(BaseModel):
    ticker:               str
    price:                float
    iv:                   IVMetrics
    rv:                   RVMetrics
    iv_rank:              float = Field(..., ge=0, le=100)
    iv_rv_ratio:          float
    vrp:                  float = Field(..., description="(IV30 - RV30) / RV30")
    iv_rv_zscore:         float
    sparklines:           Sparklines
    data_source:          str = "mock"
    provider:             str = "mock"
    iv_source:            str = "synthetic"
    iv_rank_is_proxy:     bool = False
    as_of_date:           str = ""
    iv_surface_metadata:  Optional[IVSurfaceMetadata] = None


# ---------------------------------------------------------------------------
# /api/term-structure/{ticker}
# ---------------------------------------------------------------------------

class CurvePoint(BaseModel):
    tenor: str   # "7d" | "30d" | "60d" | "90d"
    days:  int
    iv:    float


class TermStructureResponse(BaseModel):
    ticker:              str
    curve:               list[CurvePoint]
    front_slope:         Optional[float] = Field(None, description="IV30 - IV7 (None when iv7 unavailable)")
    mid_slope:           Optional[float] = Field(None, description="IV60 - IV30 (None when iv60 unavailable)")
    back_slope:          Optional[float] = Field(None, description="IV90 - IV60 (None when iv60 or iv90 unavailable)")
    total_slope:         Optional[float] = Field(None, description="IV90 - IV7 (None when iv7 or iv90 unavailable)")
    curvature:           Optional[float] = Field(None, description="front_slope - back_slope")
    shape:               Literal["contango", "inverted", "humped", "flat", "partial"]
    data_source:         str = "mock"
    provider:            str = "mock"
    iv_source:           str = "synthetic"
    iv_surface_metadata: Optional[IVSurfaceMetadata] = None


# ---------------------------------------------------------------------------
# /api/trade-signal/{ticker}
# ---------------------------------------------------------------------------

class SignalFactor(BaseModel):
    value:        float
    weight:       float = Field(..., description="Weight in regime score (0–1)")
    contribution: Literal["low", "moderate", "high", "very_high"]


class TradeSignalResponse(BaseModel):
    ticker:               str
    regime_score:         float = Field(..., ge=0, le=100)
    signal:               TradeSignal
    factors: dict[str, SignalFactor] = Field(
        ..., description="iv_rank | vrp | iv_rv_ratio | z_score — each factor's value and contribution"
    )
    playbook:             list[str]
    data_source:          str = "mock"
    provider:             str = "mock"
    iv_source:            str = "synthetic"
    iv_rank_is_proxy:     bool = False
    as_of_date:           str = ""
    macro_penalties:      Optional[MacroPenalty] = None
    iv_surface_metadata:  Optional[IVSurfaceMetadata] = None
