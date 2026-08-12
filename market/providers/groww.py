"""
Groww provider — daily and intraday candles, plus a real option chain.

Written against the published `growwapi` SDK (v1.5.0), reading its actual method
signatures rather than guessing at them.

AUTH MODEL — read this before setting anything up
-------------------------------------------------
The SDK offers `GrowwAPI.get_access_token(api_key, totp=..., secret=...)`, which
can mint a token from an API key plus a TOTP code. **This module deliberately
does not use it.** Handing a project your TOTP seed means automating a 2FA login,
and SETUP.md is explicit about that: "Automating the daily token means automating
a 2FA login. Don't. Type it."

So the contract here is the same as the Kite provider's:

  1. You mint an access token yourself, in your own script or shell.
  2. You export it as GROWW_ACCESS_TOKEN.
  3. This module reads that token and nothing else.

Your API secret and TOTP seed never enter this project, are never written to
disk by it, and are never logged.

READ-ONLY BY DESIGN
-------------------
`GrowwAPI` exposes place_order, modify_order, cancel_order, create_smart_order
and friends. This wrapper reaches for none of them, and `__getattr__` below
raises on any attempt to tunnel through to them. CLAUDE.md: no module here
places, modifies or cancels an order, and adding one requires the confirmation
and logging controls in PROMPT.md first.

RESPONSE SHAPES
---------------
Candle payloads are normalised defensively — the SDK documents a "V2 response
format" but not its exact keys, so `_to_frame` accepts the common shapes and
raises DataUnavailable rather than guessing if it recognises none of them.
Fabricating a bar because a key moved is precisely the failure this project is
built to avoid. If it raises on your account, print the raw payload and widen
`_CANDLE_KEYS` — do not paper over it.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pandas as pd

from market.config import IST
from market.providers.base import (
    Bars,
    DataProvider,
    DataUnavailable,
    OptionSnapshot,
)

_BLOCKED = {
    "place_order", "modify_order", "cancel_order",
    "create_smart_order", "modify_smart_order", "cancel_smart_order",
}

_CANDLE_KEYS = ("candles", "candleList", "data", "historicalCandles")


class GrowwProvider(DataProvider):
    name = "Groww (read-only)"
    supports_intraday = True
    supports_options = True

    _TF = {
        "1d": "1day", "1wk": "1week",
        "1h": "1hour", "30m": "30minute", "15m": "15minute", "5m": "5minute",
    }

    def __init__(self) -> None:
        token = os.getenv("GROWW_ACCESS_TOKEN")
        if not token:
            raise DataUnavailable(
                "GROWW_ACCESS_TOKEN not set. Mint a token yourself (see the auth "
                "note in market/providers/groww.py) and export it. This module "
                "will not generate one from a stored TOTP seed."
            )
        if os.getenv("GROWW_TOTP_SECRET"):
            raise DataUnavailable(
                "GROWW_TOTP_SECRET is set in the environment. Remove it. Storing a "
                "TOTP seed defeats two-factor authentication, and nothing here will "
                "read it. Export only GROWW_ACCESS_TOKEN."
            )
        try:
            from growwapi import GrowwAPI
        except ImportError as exc:  # pragma: no cover
            raise DataUnavailable("Run: pip install growwapi") from exc

        self._api = GrowwAPI(token)
        self._const = GrowwAPI

    def __getattr__(self, item: str):
        """Refuse to tunnel through to the SDK's order-placing surface."""
        if item in _BLOCKED:
            raise AttributeError(
                f"{item}() is deliberately not exposed. This project is read-only; "
                "see the execution rules in PROMPT.md."
            )
        raise AttributeError(item)

    # -- symbol lookup -----------------------------------------------------

    def _groww_symbol(self, symbol: str) -> str:
        """
        Map a plain NSE ticker to the Groww symbol the candles endpoint wants.

        Indices arrive here as yfinance-style tickers (^NSEI); Groww uses its own
        names, so those are mapped explicitly rather than mangled.
        """
        s = symbol.strip().upper()
        index_map = {"^NSEI": "NIFTY", "^NSEBANK": "BANKNIFTY"}
        if s in index_map:
            s = index_map[s]
        try:
            inst = self._api.get_instrument_by_exchange_and_trading_symbol(
                self._const.EXCHANGE_NSE, s
            )
        except Exception as exc:
            raise DataUnavailable(f"{symbol} not found on Groww NSE: {exc}") from exc
        sym = inst.get("groww_symbol")
        if not sym:
            raise DataUnavailable(f"{symbol} resolved without a groww_symbol")
        return str(sym)

    # -- bars --------------------------------------------------------------

    @staticmethod
    def _to_frame(payload: dict, symbol: str) -> pd.DataFrame:
        rows = None
        for k in _CANDLE_KEYS:
            if isinstance(payload, dict) and payload.get(k):
                rows = payload[k]
                break
        if rows is None:
            raise DataUnavailable(
                f"Groww returned no recognisable candle array for {symbol}; "
                f"keys were {list(payload)[:8] if isinstance(payload, dict) else type(payload)}"
            )

        # Rows are either [ts, o, h, l, c, v] or dicts with named fields.
        if isinstance(rows[0], dict):
            df = pd.DataFrame(rows)
            ren = {
                "timestamp": "ts", "time": "ts", "startTime": "ts",
                "openPrice": "open", "highPrice": "high",
                "lowPrice": "low", "closePrice": "close", "volume": "volume",
            }
            df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
        else:
            df = pd.DataFrame(
                rows, columns=["ts", "open", "high", "low", "close", "volume"]
            )

        missing = {"ts", "open", "high", "low", "close", "volume"} - set(df.columns)
        if missing:
            raise DataUnavailable(f"Groww candles for {symbol} missing {sorted(missing)}")

        ts = df["ts"]
        # Epoch seconds, epoch millis, or an ISO string — decide, don't guess twice.
        if pd.api.types.is_numeric_dtype(ts):
            unit = "ms" if float(ts.iloc[-1]) > 1e11 else "s"
            idx = pd.to_datetime(ts, unit=unit)
        else:
            idx = pd.to_datetime(ts)

        df = df.set_index(idx)[["open", "high", "low", "close", "volume"]]
        return df.astype(float).sort_index().dropna()

    def get_bars(self, symbol: str, timeframe: str = "1d", lookback: int = 250) -> Bars:
        interval = self._TF.get(timeframe)
        if interval is None:
            raise DataUnavailable(f"Unsupported timeframe for Groww: {timeframe}")

        to_dt = datetime.now(IST)
        span = lookback * 2 if timeframe in ("1d", "1wk") else 90
        from_dt = to_dt - timedelta(days=span)
        fmt = "%Y-%m-%d %H:%M:%S"

        try:
            payload = self._api.get_historical_candles(
                exchange=self._const.EXCHANGE_NSE,
                segment=self._const.SEGMENT_CASH,
                groww_symbol=self._groww_symbol(symbol),
                start_time=from_dt.strftime(fmt),
                end_time=to_dt.strftime(fmt),
                candle_interval=interval,
                timeout=30,
            )
        except DataUnavailable:
            raise
        except Exception as exc:
            raise DataUnavailable(f"Groww history failed for {symbol}: {exc}") from exc

        df = self._to_frame(payload, symbol).tail(lookback)
        if df.empty:
            raise DataUnavailable(f"Groww returned an empty series for {symbol}")

        return Bars(
            symbol=symbol,
            df=df,
            timeframe=timeframe,
            freshness="live" if timeframe != "1d" else "delayed",
            source=self.name,
            fetched_at=datetime.now(IST),
        )

    # -- derivatives -------------------------------------------------------

    def get_options(self, symbol: str) -> OptionSnapshot:
        """
        Aggregate call and put OI across the nearest expiry.

        PROMPT.md: 'Do not infer institutional intent from one metric alone.'
        This returns raw aggregates and a ratio. It labels nothing bullish.
        """
        now = datetime.now(IST)
        blank = OptionSnapshot(symbol, now, self.name, available=False)
        try:
            expiries = self._api.get_expiries(
                exchange=self._const.EXCHANGE_NSE,
                underlying_symbol=symbol.strip().upper(),
                timeout=20,
            )
            dates = expiries.get("expiries") or expiries.get("expiryDates") or []
            if not dates:
                return blank

            chain = self._api.get_option_chain(
                exchange=self._const.EXCHANGE_NSE,
                underlying=symbol.strip().upper(),
                expiry_date=str(sorted(str(d) for d in dates)[0]),
                timeout=30,
            )
            strikes = chain.get("optionChain") or chain.get("strikes") or []
            if not strikes:
                return blank

            call_oi = put_oi = 0
            for row in strikes:
                c = row.get("call") or {}
                p = row.get("put") or {}
                call_oi += int(c.get("openInterest") or c.get("oi") or 0)
                put_oi += int(p.get("openInterest") or p.get("oi") or 0)

            if call_oi == 0 and put_oi == 0:
                return blank

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
            return blank
