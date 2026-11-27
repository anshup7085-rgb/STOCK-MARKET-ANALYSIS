"""
Simple, long-only crypto rules on daily closes (UTC days), each a yes/no answer every evening: hold the
coin tomorrow, or hold cash. Fixed on 26 Nov 2026 before any of them was run on any price.

    hold        buy and hold: the benchmark every rule must be set against
    ma50        hold while the close is above its 50-day average
    ma200       hold while the close is above its 200-day average
    mom30       hold while the last 30 days' return is positive
    donchian    buy when the close passes the highest close of the 20 days before; sell when it falls below
                the lowest close of the 10 days before
    dip         buy after a fall of 10% or more in 3 days; sell 5 days later

`positions(closes, rule)` gives, for each day, whether the rule holds the coin AFTER that day's close: it
reads only closes up to that day. Long only: Indian spot exchanges do not let an individual sell short.
"""

from __future__ import annotations

RULES = ("hold", "ma50", "ma200", "mom30", "donchian", "dip")
WARMUP = 200


def _avg(xs):
    return sum(xs) / len(xs)


def positions(closes: list[float], rule: str) -> list[bool]:
    out, inside, held_for = [], False, 0
    for i, c in enumerate(closes):
        if i < WARMUP:
            out.append(False)
            continue
        if rule == "hold":
            inside = True
        elif rule == "ma50":
            inside = c > _avg(closes[i - 49:i + 1])
        elif rule == "ma200":
            inside = c > _avg(closes[i - 199:i + 1])
        elif rule == "mom30":
            inside = c > closes[i - 30]
        elif rule == "donchian":
            if not inside and c > max(closes[i - 20:i]):
                inside = True
            elif inside and c < min(closes[i - 10:i]):
                inside = False
        elif rule == "dip":
            if inside:
                held_for += 1
                if held_for >= 5:
                    inside, held_for = False, 0
            elif c <= 0.9 * closes[i - 3]:
                inside, held_for = True, 0
        else:
            raise ValueError(f"unknown rule {rule!r}")
        out.append(inside)
    return out
