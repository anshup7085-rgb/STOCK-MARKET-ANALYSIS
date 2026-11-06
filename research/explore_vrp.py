"""
EXPLORATION, NOT EVIDENCE: is the 09:20 straddle worth selling only when options are dear? On the
exploration years only (Oct 2021 to Oct 2024), from the saved straddle replay and Dhan's minutes.

    PYTHONPATH=. .venv/bin/python research/explore_vrp.py

Option sellers are paid, on average, because implied volatility (what the option price assumes) runs
above the volatility that then happens: the volatility risk premium. If that is where the straddle's
money comes from, the days when the premium is wide should pay and the days when it is thin should
not. For each day the replay sold (research/explore_straddle.py, 0.2% half-spread), this measures,
from what was known at 09:19:

    IV   the at-the-money call's and put's implied volatility at 09:19, averaged (Dhan's field)
    RV   NIFTY's realized volatility over the 5, or the 20, sessions before: every minute's move and
         every overnight gap, annualized
    the premium: IV / RV

and splits the days into thirds by IV / RV (each lookback), and by IV alone (to tell a wide premium
from simply dear options), reporting every group: all days, the days that were not an expiry, and
by year. Fixed before the run. The reading fixed in advance: a usable pattern rises from the low third
to the high third, in most years, and outside expiry day too. The thirds' boundaries come from the
exploration years; a registered test would carry them unchanged to the held-back years.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from trading.fno import dhan_data as dd
from trading.fno import vh

REPO = Path(__file__).resolve().parents[1]
REPLAY = REPO / "state" / "dhan" / "explore" / "straddle.json"
OUT = REPO / "state" / "dhan" / "explore" / "vrp.json"
ENTRY_BAR = "09:19"
LOOKBACKS = (5, 20)


def spots(day_bars: dict) -> list[float]:
    """The index level minute by minute, 09:15 to 15:29, from the ATM series' spot field."""
    out = []
    for hm in sorted(day_bars):
        b = day_bars[hm].get((0, "CALL")) or day_bars[hm].get((0, "PUT"))
        if b is not None and b.spot:
            out.append(b.spot)
    return out


def iv_at(day_bars: dict, hm: str = ENTRY_BAR) -> float | None:
    """The ATM call's and put's implied volatility at `hm`, averaged, in percent."""
    m = day_bars.get(hm, {})
    ivs = [b.iv for b in (m.get((0, "CALL")), m.get((0, "PUT"))) if b is not None and b.iv]
    if not ivs:
        return None
    v = sum(ivs) / len(ivs)
    return v * 100 if v < 3 else v                     # a fraction or a percent, whichever Dhan sends


def realized(prior: list[list[float]]) -> float | None:
    """Annualized realized volatility, percent, over `prior` sessions (oldest first): every minute's log
    move within a session and every overnight gap between them."""
    if not prior or any(len(s) < 300 for s in prior):
        return None
    ss, last = 0.0, None
    for s in prior:
        if last is not None:
            ss += math.log(s[0] / last) ** 2
        ss += sum(math.log(b / a) ** 2 for a, b in zip(s, s[1:]))
        last = s[-1]
    return math.sqrt(ss * 252 / len(prior)) * 100


def thirds(rows: list[dict], key: str) -> list[tuple[str, list[dict]]]:
    have = sorted((r for r in rows if r.get(key) is not None), key=lambda r: r[key])
    n = len(have)
    cuts = [have[:n // 3], have[n // 3: 2 * n // 3], have[2 * n // 3:]]
    out = []
    for label, part in zip(("low", "middle", "high"), cuts):
        if part:
            out.append((f"{label} third ({part[0][key]:.2f} to {part[-1][key]:.2f})", part))
    return out


def block(ts: list[dict]) -> str:
    if not ts:
        return "no days"
    nets = [t["net"] for t in ts]
    wins = sum(n > 0 for n in nets)
    return (f"{len(ts)} days, won {wins / len(ts):.0%}, mean R {sum(t['R'] for t in ts) / len(ts):+.3f}, "
            f"net Rs{sum(nets):+,.0f} (Rs{sum(nets) / len(nets):+,.0f} a day)")


def main() -> int:
    if not REPLAY.exists():
        print("run research/explore_straddle.py first")
        return 2
    trades = {t["day"]: t for t in json.loads(REPLAY.read_text())["trades"]["0.2%"]}
    archive = vh.Archive(vh.RAW)
    chain = set(vh.weekly_chain(archive.expiries()))
    nse = set(archive.days)
    days = [d for d in dd.sessions() if dd.EXPLORE_FROM <= d <= dd.EXPLORE_TO and d in nse]
    series: dict[date, list[float]] = {}
    rows = []
    for i, d in enumerate(days):
        bars = dd.load_day(d, offsets=(0,))
        series[d] = spots(bars)
        t = trades.get(d.isoformat())
        if t is None:
            continue
        row = {**t, "expiry": d in chain, "iv": iv_at(bars)}
        for n in LOOKBACKS:
            prior = [series[x] for x in days[max(0, i - n):i]]
            rv = realized(prior) if len(prior) == n else None
            row[f"rv{n}"] = None if rv is None else round(rv, 2)
            row[f"ratio{n}"] = None if rv is None or not row["iv"] else round(row["iv"] / rv, 3)
        rows.append(row)
    if not rows:
        print("no replayed days matched Dhan's sessions: run research/explore_straddle.py first")
        return 2
    ivs = sorted(r["iv"] for r in rows if r["iv"])
    print(f"EXPLORATION, NOT EVIDENCE: the 09:20 straddle by the volatility premium, {len(rows)} days, "
          f"{rows[0]['day']} to {rows[-1]['day']}, 0.2% half-spread; every group, fixed before the run")
    if ivs:
        print(f"  IV at 09:19: median {ivs[len(ivs) // 2]:.1f}% ({len(ivs)} days with a reading)")
    print(f"  every day: {block(rows)}")
    for key, title in [(f"ratio{n}", f"IV / RV over {n} sessions") for n in LOOKBACKS] + [("iv", "IV alone")]:
        print(f"\n{title}:")
        for label, part in thirds(rows, key):
            years: dict[str, list[dict]] = defaultdict(list)
            for t in part:
                years[t["day"][:4]].append(t)
            print(f"  {label}: {block(part)}")
            print(f"      not expiry day: {block([t for t in part if not t['expiry']])}; "
                  f"expiry day: {block([t for t in part if t['expiry']])}")
            print("      by year: " + "; ".join(f"{y} Rs{sum(t['net'] for t in v):+,.0f}" for y, v in sorted(years.items())))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"written": datetime.now().isoformat(timespec="seconds"), "days": rows}, indent=1))
    print(f"\nsaved: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
