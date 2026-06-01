from fastapi import APIRouter, HTTPException
from app.core.config import settings
from app.models.schemas import DashboardResponse, TickerSnapshot, MacroData, TickerListResponse
from app.services.vol_service import build_dashboard, build_ticker_snapshot
from app.data.mock_provider import get_macro_data

router = APIRouter(prefix="/api/vol", tags=["volatility"])


@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard():
    """Full dashboard payload for all supported tickers."""
    return build_dashboard()


@router.get("/tickers", response_model=TickerListResponse)
def list_tickers():
    return {"tickers": list(settings.tickers)}


@router.get("/ticker/{ticker}", response_model=TickerSnapshot)
def get_ticker(ticker: str):
    sym = ticker.upper()
    if sym not in settings.tickers:
        raise HTTPException(
            status_code=404,
            detail=f"{sym} not supported. Available: {list(settings.tickers)}",
        )
    return build_ticker_snapshot(sym)


@router.get("/macro", response_model=MacroData)
def get_macro():
    return get_macro_data()
