"""
The crypto brain: what Bitcoin's next UTC day will do, as probabilities, scored against a base rate.
The same model as the NIFTY brain (brain/model.py), on Binance's daily candles.

Two questions about day d+1, answered the following midnight (UTC):
  up    did BTC close above day d's close?
  big   was the move larger than BTC's usual move (the median absolute daily move of the 365 days to d)?

Inputs on day d use only candles closed by the end of day d. Crypto never closes, so day d+1 begins the
moment day d's candle closes; a forecast counts only if written within 30 minutes of that (it cannot
see those minutes: no input reads them). Later ones are kept on record and never counted.

The reading of the historical record, fixed on 21 Nov 2026 before any crypto price was read: a question
shows skill only if its skill is above 0, z is at least 2.58, and the skill is above 0 in at least 4 of
the 5 years 2020 to 2024. Expected, on record: none on UP; some on BIG, as with NIFTY.
"""

from __future__ import annotations

import math
from datetime import date

from crypto.binance import Candle
from crypto.store import utc_day

FEATURES = ("ret1", "ret3", "ret7", "ret30", "vol7", "vol30", "range", "above200", "volume_chg", "eth_ret1",
            "weekday")
TARGETS = ("up", "big")
WARMUP = 365
RECORD_FROM = date(2020, 1, 1)
Z_NEEDED, YEARS_NEEDED = 2.58, 4


def _mean(xs):
    return sum(xs) / len(xs)


def build(btc: list[Candle], eth: list[Candle]) -> list[dict]:
    """One row per BTC day from the 365th on: {"day", "x", "y", "usual_move"}; y is None for the last day."""
    days = [utc_day(c) for c in btc]
    closes = [c.close for c in btc]
    rets = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(btc))]
    eth_close = {utc_day(c): c.close for c in eth}
    rows = []
    for i in range(WARMUP, len(btc)):
        c, d = btc[i], days[i]
        vols = [b.quote for b in btc[i - 29:i + 1]]
        prev = eth_close.get(days[i - 1])
        x = {
            "ret1": rets[i], "ret3": math.log(closes[i] / closes[i - 3]), "ret7": math.log(closes[i] / closes[i - 7]),
            "ret30": math.log(closes[i] / closes[i - 30]),
            "vol7": math.sqrt(_mean([r * r for r in rets[i - 6:i + 1]])),
            "vol30": math.sqrt(_mean([r * r for r in rets[i - 29:i + 1]])),
            "range": math.log(c.high / c.low) if c.low > 0 else None,
            "above200": math.log(closes[i] / _mean(closes[i - 199:i + 1])),
            "volume_chg": math.log(c.quote / _mean(vols)) if c.quote > 0 and _mean(vols) > 0 else None,
            "eth_ret1": math.log(eth_close[d] / prev) if d in eth_close and prev else None,
            "weekday": float(d.weekday()),
        }
        usual = sorted(abs(r) for r in rets[i - 364:i + 1])[182]
        y = {"up": None, "big": None}
        if i + 1 < len(btc):
            y = {"up": int(rets[i + 1] > 0), "big": int(abs(rets[i + 1]) > usual)}
        rows.append({"day": d, "x": x, "y": y, "usual_move": usual})
    return rows
