"""
What the brain knows at the close of a session, and what it is asked about the next one.

Every feature of day t uses data up to and including day t's close, nothing later. The two questions
about day t+1, each with a yes or no answer the next evening:

  up    did NIFTY close above day t's close?
  big   was NIFTY's move (up or down) larger than its usual move, the median absolute daily move of
        the 250 sessions ending on day t?
"""

from __future__ import annotations

import math
from datetime import date

from brain.data import Bar

NIFTY, VIX = "nifty 50", "india vix"
SECTORS = ("nifty bank", "nifty it", "nifty auto", "nifty pharma", "nifty fmcg", "nifty metal", "nifty realty",
           "nifty energy", "nifty media", "nifty psu bank", "nifty infra", "nifty financial services")
FEATURES = ("ret1", "ret5", "ret20", "vol5", "vol20", "range", "gap", "above200", "vix", "vix_chg", "vix_over_vol",
            "fii_long", "fii_chg5", "breadth", "weekday")
TARGETS = ("up", "big")
WARMUP = 250


def _mean(xs):
    return sum(xs) / len(xs)


def build(indexes: dict[str, dict[date, Bar]], fii: dict[date, float]) -> list[dict]:
    """One row per NIFTY session from the 250th on: {"day", "x": {feature: value or None},
    "y": {target: 0 or 1, or None while the next session has not happened}}."""
    nifty = indexes.get(NIFTY, {})
    days = sorted(nifty)
    closes = [nifty[d].close for d in days]
    rets = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(days))]
    vix = indexes.get(VIX, {})
    rows = []
    for i in range(WARMUP, len(days)):
        d, bar = days[i], nifty[days[i]]
        vol5 = math.sqrt(_mean([r * r for r in rets[i - 4:i + 1]]))
        vol20 = math.sqrt(_mean([r * r for r in rets[i - 19:i + 1]]))
        x: dict[str, float | None] = {
            "ret1": rets[i], "ret5": math.log(closes[i] / closes[i - 5]), "ret20": math.log(closes[i] / closes[i - 20]),
            "vol5": vol5, "vol20": vol20,
            "range": math.log(bar.high / bar.low) if bar.high and bar.low else None,
            "gap": math.log(bar.open / closes[i - 1]) if bar.open else None,
            "above200": math.log(closes[i] / _mean(closes[i - 199:i + 1])),
            "weekday": float(d.weekday()),
        }
        v, v_prev = vix.get(d), vix.get(days[i - 1])
        x["vix"] = math.log(v.close) if v else None
        x["vix_chg"] = math.log(v.close / v_prev.close) if v and v_prev else None
        x["vix_over_vol"] = (math.log(v.close / (vol20 * math.sqrt(252) * 100)) if v and vol20 > 0 else None)
        x["fii_long"] = fii.get(d)
        x["fii_chg5"] = (fii[d] - fii[days[i - 5]]) if d in fii and days[i - 5] in fii else None
        above = []
        for name in SECTORS:
            s = indexes.get(name, {})
            window = [s[k].close for k in days[i - 19:i + 1] if k in s]
            if d in s and len(window) >= 15:
                above.append(1.0 if s[d].close > _mean(window) else 0.0)
        x["breadth"] = _mean(above) if len(above) >= 6 else None
        usual = sorted(abs(r) for r in rets[i - 249:i + 1])[125]
        y: dict[str, int | None] = {"up": None, "big": None}
        if i + 1 < len(days):
            y = {"up": int(rets[i + 1] > 0), "big": int(abs(rets[i + 1]) > usual)}
        rows.append({"day": d, "x": x, "y": y, "usual_move": usual})
    return rows
