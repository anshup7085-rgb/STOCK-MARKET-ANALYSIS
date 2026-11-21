"""
Binance's public market data: candles ("klines"). No account, no key. Reachable from the server
(checked 21 Nov 2026). Only CLOSED candles are kept: a candle still forming is never stored.
"""

from __future__ import annotations

import json
import time as _time
import urllib.request
from dataclasses import dataclass

URL = "https://api.binance.com/api/v3/klines"
STEP_MS = {"1m": 60_000, "1h": 3_600_000, "1d": 86_400_000}
GAP_S = 0.2                          # far below Binance's public limit
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")


@dataclass(frozen=True)
class Candle:
    t: int                           # open time, milliseconds since 1970, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float                    # in the coin
    quote: float                     # in USDT
    trades: int


def get_json(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"User-Agent": "market-lab/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def klines(symbol: str, interval: str, start_ms: int, now_ms: int | None = None, get=get_json,
           sleep=_time.sleep) -> list[Candle]:
    """Every closed candle from start_ms on, 1,000 a request."""
    now_ms = int(_time.time() * 1000) if now_ms is None else now_ms
    out, s = [], start_ms
    while s < now_ms:
        sleep(GAP_S)
        rows = get(f"{URL}?symbol={symbol}&interval={interval}&startTime={s}&limit=1000") or []
        for r in rows:
            if int(r[6]) < now_ms:                       # its close time has passed: the candle is final
                out.append(Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]),
                                  float(r[7]), int(r[8])))
        if len(rows) < 1000:
            break
        s = int(rows[-1][0]) + STEP_MS[interval]
    return out
