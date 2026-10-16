"""
Corporate-action detection from price alone.

PROMPT.md: reject or downgrade ideas when 'corporate action data is unresolved'.
Nothing here could see a corporate-action feed, so this does the next best
honest thing: it looks for the fingerprint an UNADJUSTED split or bonus leaves
in a price series — an overnight jump whose ratio sits on a common split/bonus
factor — and flags it for a human to resolve.

It cannot tell a split from a genuine crash, and it does not try. A flag means
"the history may be distorted; verify before trusting any level built on it".
Kite's and Groww's historical candles are not split-adjusted, so on those feeds
this check is load-bearing. Yahoo's are, so there it mostly catches bad ticks.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# open/prev_close ratios an unadjusted action produces: splits 1:2, 1:5, 1:10,
# bonuses 1:1, 1:2, 2:1, 3:1 and so on.
_FACTORS = {
    1 / 2: "1:2 split or 1:1 bonus",
    1 / 3: "1:3 split or 2:1 bonus",
    1 / 4: "1:4 split or 3:1 bonus",
    1 / 5: "1:5 split or 4:1 bonus",
    1 / 10: "1:10 split",
    2 / 3: "1:2 bonus",
    3 / 4: "1:3 bonus",
    4 / 5: "1:4 bonus",
}


@dataclass
class ActionFlag:
    date: str
    ratio: float
    likely: str


def suspect_corporate_actions(
    df: pd.DataFrame,
    lookback: int = 260,
    min_move: float = 0.18,
    factor_tolerance: float = 0.03,
    extreme_move: float = 0.35,
) -> list[ActionFlag]:
    """
    Overnight jumps (open vs previous close) that look like an unadjusted action.

    Flags a move when it lands within `factor_tolerance` of a known factor and is
    at least `min_move`, or when it exceeds `extreme_move` whatever its ratio.
    """
    if df is None or len(df) < 2:
        return []
    tail = df.tail(lookback + 1)
    prev_close = tail["close"].shift(1)
    ratio = (tail["open"] / prev_close).dropna()

    out: list[ActionFlag] = []
    for ts, r in ratio.items():
        r = float(r)
        move = abs(r - 1)
        if move < min_move:
            continue
        likely = None
        for f, label in _FACTORS.items():
            if abs(r / f - 1) <= factor_tolerance:
                likely = label
                break
        if likely is None and move >= extreme_move:
            likely = "extreme gap — split, bonus, demerger or bad tick"
        if likely:
            day = ts.date().isoformat() if hasattr(ts, "date") else str(ts)
            out.append(ActionFlag(day, round(r, 4), likely))
    return out
