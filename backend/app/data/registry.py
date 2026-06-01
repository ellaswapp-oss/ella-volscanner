"""
data/registry.py
----------------
Provider factory — selects and caches the active VolDataProvider based on the
DATA_PROVIDER environment variable (default: ``"mock"``).

Usage
-----
    from app.data.registry import get_provider

    provider = get_provider()
    prices   = provider.get_prices_days("SPY", days=400)

The provider instance is a module-level singleton created on first call.
To swap providers at runtime (tests), call ``_reset_provider()`` then set
the env var before the next ``get_provider()`` call.

Supported values of DATA_PROVIDER
----------------------------------
    mock   — MockDataProvider (default, no network calls)
    real   — RealDataProvider (Polygon/ORATS/FRED; raises NotImplementedError
              until individual integrations are wired in)
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from app.data.base_provider import VolDataProvider

# Load .env from the backend root so DATA_PROVIDER is available at import time.
load_dotenv()

# ---------------------------------------------------------------------------
# Singleton cache
# ---------------------------------------------------------------------------

_provider: VolDataProvider | None = None


def get_provider() -> VolDataProvider:
    """
    Return the active provider singleton.

    Provider selection (checked once on first call):
        DATA_PROVIDER=mock  → MockDataProvider (default)
        DATA_PROVIDER=real  → RealDataProvider
    """
    global _provider
    if _provider is None:
        _provider = _create_provider()
    return _provider


def _create_provider() -> VolDataProvider:
    mode = os.environ.get("DATA_PROVIDER", "mock").strip().lower()

    if mode == "real":
        from app.data.real_provider import RealDataProvider
        return RealDataProvider()

    # Default: mock (also handles any unrecognised value)
    from app.data._mock_impl import MockDataProvider
    return MockDataProvider()


def _reset_provider() -> None:
    """
    Discard the cached provider so the next ``get_provider()`` call re-reads
    the env var.  Intended for tests only.
    """
    global _provider
    _provider = None


# ---------------------------------------------------------------------------
# Convenience re-exports (mirrors the old module-level functions)
# Used by the backward-compat facades in mock_provider.py / macro_provider.py
# ---------------------------------------------------------------------------

def get_tickers() -> list[str]:
    """Canonical ticker list — always from the provider's TICKERS constant."""
    from app.data._mock_impl import TICKERS as _MOCK_TICKERS
    # For real provider, tickers come from settings; mock list is the default.
    provider = get_provider()
    if hasattr(provider, "tickers"):
        return list(provider.tickers)            # type: ignore[attr-defined]
    return list(_MOCK_TICKERS)
