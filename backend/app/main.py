from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import vol, market, backtest, history, data, research, scanner
from app.core.config import settings
from app.models.schemas import HealthResponse

app = FastAPI(
    title="Vol Dashboard API",
    version="0.2.0",
    description="Volatility trading dashboard — IV, RV, VRP, term structure, skew, macro, regime scoring",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(vol.router)
app.include_router(market.router)
app.include_router(backtest.router)
app.include_router(history.router)
app.include_router(data.router)
app.include_router(research.router)
app.include_router(scanner.router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health():
    return {"status": "ok"}
