"""Provider registry."""

from __future__ import annotations

from market.config import settings
from market.providers.base import (
    Bars,
    DataProvider,
    DataUnavailable,
    OptionSnapshot,
)


def get_provider(name: str | None = None) -> DataProvider:
    """Resolve a provider by name, defaulting to MARKET_PROVIDER in the env."""
    choice = (name or settings.provider or "yfinance").strip().lower()

    if choice in ("yfinance", "yf", "yahoo"):
        from market.providers.yf import YFinanceProvider

        return YFinanceProvider()

    if choice in ("mock", "synthetic"):
        from market.providers.mock import MockProvider

        return MockProvider()

    if choice in ("kite", "zerodha"):
        from market.providers.kite import KiteProvider

        return KiteProvider()

    raise DataUnavailable(
        f"Unknown provider '{choice}'. Supported: yfinance, kite, mock"
    )


__all__ = [
    "Bars",
    "DataProvider",
    "DataUnavailable",
    "OptionSnapshot",
    "get_provider",
]
