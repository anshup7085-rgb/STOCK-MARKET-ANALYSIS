"""
Zerodha Kite Connect provider — intraday bars plus a real option chain.

Auth model, per CLAUDE.md's rule about never bypassing broker authentication:
  1. You log in through Zerodha's own browser flow and obtain a request_token.
  2. You exchange it for an access_token ONCE per day, yourself (see SETUP.md).
  3. You export KITE_API_KEY and KITE_ACCESS_TOKEN into the environment.

This module never sees your password, PIN, TOTP or API secret. It never
initiates a login and it never places an order — it is read-only by design.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from market.config import IST, settings
from market.providers.base import (
    Bars,
    DataProvider,
    DataUnavailable,
    OptionSnapshot,
)


class KiteProvider(DataProvider):
    name = "Zerodha Kite Connect"
    supports_intraday = True
    supports_options = True

    _TF = {"1d": "day", "1h": "60minute", "15m": "15minute", "5m": "5minute"}

    def __init__(self) -> None:
        if not settings.kite_api_key or not settings.kite_access_token:
            raise DataUnavailable(
                "Kite credentials absent. Export KITE_API_KEY and KITE_ACCESS_TOKEN. "
                "See SETUP.md for the daily token flow."
            )
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:  # pragma: no cover
            raise DataUnavailable("Run: pip install kiteconnect") from exc

        self._kite = KiteConnect(api_key=settings.kite_api_key)
        self._kite.set_access_token(settings.kite_access_token)
        self._instruments: pd.DataFrame | None = None

    # -- instrument lookup -------------------------------------------------

    def _load_instruments(self, exchange: str = "NSE") -> pd.DataFrame:
        if self._instruments is None:
            try:
                self._instruments = pd.DataFrame(self._kite.instruments(exchange))
            except Exception as exc:
                raise DataUnavailable(f"Instrument dump failed: {exc}") from exc
        return self._instruments

    def token_for(self, symbol: str, exchange: str = "NSE") -> int:
        inst = self._load_instruments(exchange)
        hit = inst.loc[inst["tradingsymbol"] == symbol.strip().upper()]
        if hit.empty:
            raise DataUnavailable(f"{symbol} not found on {exchange}")
        return int(hit.iloc[0]["instrument_token"])

    # -- bars --------------------------------------------------------------

    def get_bars(self, symbol: str, timeframe: str = "1d", lookback: int = 250) -> Bars:
        interval = self._TF.get(timeframe)
        if interval is None:
            raise DataUnavailable(f"Unsupported timeframe for Kite: {timeframe}")

        token = self.token_for(symbol)
        span = lookback * 2 if timeframe == "1d" else 90
        to_dt = datetime.now(IST)
        from_dt = to_dt - timedelta(days=span)

        try:
            candles = self._kite.historical_data(token, from_dt, to_dt, interval)
        except Exception as exc:
            raise DataUnavailable(f"Kite history failed for {symbol}: {exc}") from exc

        if not candles:
            raise DataUnavailable(f"Kite returned no candles for {symbol}")

        df = pd.DataFrame(candles).set_index("date")
        df = df[["open", "high", "low", "close", "volume"]].dropna().tail(lookback)

        return Bars(
            symbol=symbol,
            df=df,
            timeframe=timeframe,
            freshness="live",
            source=self.name,
            fetched_at=datetime.now(IST),
        )

    # -- derivatives -------------------------------------------------------

    def get_options(self, symbol: str) -> OptionSnapshot:
        """
        Aggregate OI across the nearest expiry.

        PROMPT.md: 'Do not infer institutional intent from one metric alone.'
        So this returns raw aggregates only. It does not label anything bullish.
        """
        now = datetime.now(IST)
        try:
            nfo = pd.DataFrame(self._kite.instruments("NFO"))
            chain = nfo.loc[
                (nfo["name"] == symbol.strip().upper())
                & (nfo["instrument_type"].isin(["CE", "PE"]))
            ]
            if chain.empty:
                return OptionSnapshot(symbol, now, self.name, available=False)

            nearest = chain["expiry"].min()
            chain = chain.loc[chain["expiry"] == nearest]

            quotes = self._kite.quote(
                [f"NFO:{ts}" for ts in chain["tradingsymbol"].tolist()]
            )

            call_oi = put_oi = 0
            for _, row in chain.iterrows():
                q = quotes.get(f"NFO:{row['tradingsymbol']}")
                if not q:
                    continue
                oi = int(q.get("oi", 0) or 0)
                if row["instrument_type"] == "CE":
                    call_oi += oi
                else:
                    put_oi += oi

            if call_oi == 0 and put_oi == 0:
                return OptionSnapshot(symbol, now, self.name, available=False)

            return OptionSnapshot(
                symbol=symbol,
                fetched_at=now,
                source=self.name,
                put_call_ratio=(put_oi / call_oi) if call_oi else None,
                total_call_oi=call_oi,
                total_put_oi=put_oi,
                available=True,
            )
        except Exception:
            # Never fabricate derivatives data. Absent beats wrong.
            return OptionSnapshot(symbol, now, self.name, available=False)
