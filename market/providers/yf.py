"""
yfinance provider — free, no signup, EOD NSE data via the .NS suffix.

Good enough for daily-timeframe swing work, which is the horizon PROMPT.md
specifies (a few days to several weeks). It cannot give you live ticks, real
open interest, or the option chain. Those gaps are reported honestly rather
than filled in.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from market.config import IST
from market.providers.base import Bars, DataProvider, DataUnavailable


class YFinanceProvider(DataProvider):
    name = "yfinance (Yahoo, EOD, delayed)"
    supports_intraday = False
    supports_options = False

    _TF = {"1d": "1d", "1wk": "1wk", "1h": "1h", "15m": "15m"}

    def __init__(self) -> None:
        try:
            import yfinance  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise DataUnavailable(
                "yfinance not installed. Run: pip install yfinance"
            ) from exc

    @staticmethod
    def to_yahoo(symbol: str) -> str:
        """RELIANCE -> RELIANCE.NS ; ^NSEI passes through unchanged."""
        s = symbol.strip().upper()
        if s.startswith("^") or "." in s:
            return s
        return f"{s}.NS"

    def get_bars(self, symbol: str, timeframe: str = "1d", lookback: int = 250) -> Bars:
        import yfinance as yf

        interval = self._TF.get(timeframe)
        if interval is None:
            raise DataUnavailable(f"Unsupported timeframe for yfinance: {timeframe}")

        # Pad the window generously; holidays and halts eat trading days.
        period_days = max(lookback * 2, 400) if timeframe == "1d" else 60
        ticker = self.to_yahoo(symbol)

        try:
            raw = yf.Ticker(ticker).history(
                period=f"{period_days}d", interval=interval, auto_adjust=False
            )
        except Exception as exc:
            raise DataUnavailable(f"yfinance fetch failed for {ticker}: {exc}") from exc

        if raw is None or raw.empty:
            raise DataUnavailable(f"No data returned for {ticker}")

        df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        df = df.dropna().tail(lookback)

        if df.empty:
            raise DataUnavailable(f"All rows dropped as NaN for {ticker}")

        return Bars(
            symbol=symbol,
            df=df,
            timeframe=timeframe,
            freshness="delayed" if timeframe != "1d" else "historical",
            source=self.name,
            fetched_at=datetime.now(IST),
        )
