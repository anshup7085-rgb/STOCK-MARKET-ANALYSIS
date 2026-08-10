"""
Data provider interface.

DATA_SOURCES.md requires that price/volume data record a timestamp, a timeframe,
and whether it is live, delayed or historical. That contract is enforced here:
every provider returns a Bars object carrying those fields, so downstream code
can never silently treat stale data as current.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd

from market.config import IST

Freshness = Literal["live", "delayed", "historical", "unknown"]


@dataclass
class Bars:
    """OHLCV history for one symbol, with provenance attached."""

    symbol: str
    df: pd.DataFrame              # index: DatetimeIndex; cols: open high low close volume
    timeframe: str                # "1d", "15m", ...
    freshness: Freshness
    source: str                   # provider name, for the DATA QUALITY section
    fetched_at: datetime

    @property
    def last_bar_time(self) -> datetime | None:
        if self.df.empty:
            return None
        ts = self.df.index[-1]
        return ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts

    @property
    def last_close(self) -> float | None:
        if self.df.empty:
            return None
        return float(self.df["close"].iloc[-1])

    def staleness_days(self, now: datetime | None = None) -> float | None:
        """Calendar days between the last bar and now. None if no data."""
        last = self.last_bar_time
        if last is None:
            return None
        now = now or datetime.now(IST)
        if last.tzinfo is None:
            last = last.replace(tzinfo=IST)
        return (now - last).total_seconds() / 86400.0


@dataclass
class OptionSnapshot:
    """Derivatives context. Optional — most free providers cannot supply it."""

    symbol: str
    fetched_at: datetime
    source: str
    put_call_ratio: float | None = None
    total_call_oi: int | None = None
    total_put_oi: int | None = None
    implied_vol: float | None = None
    available: bool = False       # False => scoring must skip the derivatives block


class DataProvider(ABC):
    """Minimum surface an agent run needs."""

    name: str = "abstract"
    supports_intraday: bool = False
    supports_options: bool = False

    @abstractmethod
    def get_bars(self, symbol: str, timeframe: str = "1d", lookback: int = 250) -> Bars:
        """Return OHLCV history. Raise DataUnavailable rather than returning junk."""

    def get_options(self, symbol: str) -> OptionSnapshot:
        """Default: not available. Providers with an option chain override this."""
        return OptionSnapshot(
            symbol=symbol,
            fetched_at=datetime.now(IST),
            source=self.name,
            available=False,
        )

    def capabilities(self) -> dict[str, bool]:
        return {
            "intraday": self.supports_intraday,
            "options": self.supports_options,
        }


class DataUnavailable(RuntimeError):
    """Raised when a provider cannot supply usable data. Never substitute a guess."""
