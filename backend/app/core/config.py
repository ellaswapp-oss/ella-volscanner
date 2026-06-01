"""Application-wide configuration. Single source of truth for constants."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    tickers: tuple[str, ...] = ("SPY", "QQQ", "IWM", "AAPL", "NVDA", "TSLA")
    index_tickers: tuple[str, ...] = ("SPY", "QQQ", "IWM")

    cors_origins: tuple[str, ...] = ("http://localhost:3000",)

    # Data provider
    price_history_days: int = 400
    cache_ttl_seconds: int = 300

    # Data source selection (mock | real)
    data_provider: str = field(
        default_factory=lambda: os.environ.get("DATA_PROVIDER", "mock").strip().lower()
    )

    # API keys (loaded from .env — never hard-code values here)
    polygon_api_key: str = field(
        default_factory=lambda: os.environ.get("POLYGON_API_KEY", "")
    )
    orats_api_key: str = field(
        default_factory=lambda: os.environ.get("ORATS_API_KEY", "")
    )
    fred_api_key: str = field(
        default_factory=lambda: os.environ.get("FRED_API_KEY", "")
    )

    # Regime score weights (must sum to 1.0)
    weight_iv_rank: float = 0.35
    weight_vrp: float = 0.25
    weight_iv_rv_ratio: float = 0.25
    weight_z_score: float = 0.15

    # Trade signal thresholds
    threshold_buy: float = 30.0
    threshold_neutral: float = 50.0
    threshold_sell_selective: float = 70.0


settings = Settings()
