"""
EXPLORATION, NOT EVIDENCE: selling the at-the-money straddle on NIFTY's weekly expiry day, in a
small grid of variants fixed before it was run, on the exploration years only (Oct 2021 to Oct 2024).

    PYTHONPATH=. .venv/bin/python research/explore_expiry.py

Explorations 2 and 3 found the explorer's 09:20 straddle positive on expiry day alone (+Rs62,461 over
161 sessions) and nowhere else. Before any of it is tested on the held-back years, this asks whether
a different entry time, stop or a protected version holds up as well, or better. The grid, all 12
variants reported, none dropped:

    entry      09:20, 11:00, 13:00   (decided on the minute bar that closes then)
    stop       a net loss of 30% or 60% of the credit, every cost included; otherwise 15:15
    structure  the straddle; or an iron fly: the same straddle with a call bought 300 points above and
               a put 300 points below (defined risk, far less margin)

One lot of 65, today's Zerodha fees, a 0.2% half-spread (at least a tick) on every fill: the spread
our paper trades actually met (tools/fill_quality.py). Risk as the explorer sizes it: a defined
structure's worst loss plus costs; the straddle's stop x 1.5 plus costs. Expiry days come from NSE's
own F&O files. A variant that only works on one entry time and one stop, with its neighbours weak,
is noise; that is the reading to make first.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from market.charges import FNO_ZERODHA, option_order_costs
from research.explore_straddle import LAST_BAR, half_spread, minutes, tick
from trading.fno import dhan_data as dd
from trading.fno import vh

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "state" / "dhan" / "explore" / "expiry.json"
LOT = 65
WING = 300.0
ENTRIES = {"09:20": "09:19", "11:00": "10:59", "13:00": "12:59"}
STOPS = (0.30, 0.60)
STRUCTURES = ("straddle", "iron fly +/-300")
SLIP = 1.5


def _cost(px: float, side: str, lot: int = LOT) -> float:
    return option_order_costs(px, lot, side, FNO_ZERODHA).total


def replay(day_bars: dict, entry_bar: str, stop_frac: float, structure: str, h, lot: int = LOT,
           wing: float = WING) -> dict | None:
    first = day_bars.get(entry_bar, {})
    ce0 = first.get((0, "CALL"))
    if ce0 is None:
        return None
    k = ce0.strike
    legs = [("CALL", k, "SELL"), ("PUT", k, "SELL")]
    if structure != "straddle":
        legs += [("CALL", k + wing, "BUY"), ("PUT", k - wing, "BUY")]
    fills, last = {}, {}
    for right, strike, side in legs:
        b = dd.at_strike(day_bars, entry_bar, strike, right)
        if b is None or b.close <= 0:
            return None
        px = tick(b.close - h(b.close), False) if side == "SELL" else tick(b.close + h(b.close), True)
        if side == "SELL" and px <= 0:
            return None
        fills[(right, strike)] = (side, px)
        last[(right, strike)] = b.close
    credit_pts = sum(px if side == "SELL" else -px for side, px in fills.values())
    credit = credit_pts * lot
    if credit <= 0:
        return None
    cin = sum(_cost(px, side, lot) for side, px in fills.values())
    cout_est = sum(_cost(px, "BUY" if side == "SELL" else "SELL", lot) for side, px in fills.values())
    stop = stop_frac * credit
    risk = ((wing - credit_pts) * lot + cin + cout_est) if structure != "straddle" else (stop * SLIP + cin + cout_est)
    start = datetime.strptime(entry_bar, "%H:%M")
    path = [m for m in minutes("09:15", LAST_BAR) if datetime.strptime(m, "%H:%M") > start]
    net, exit_at, reason, carried = 0.0, LAST_BAR, "time", 0
    for hm in path:
        for right, strike, _ in legs:
            b = dd.at_strike(day_bars, hm, strike, right)
            if b is None:
                carried += 1
            else:
                last[(right, strike)] = b.close
        gross, cout = 0.0, 0.0
        for (right, strike), (side, px) in fills.items():
            x = last[(right, strike)]
            close_px = tick(x + h(x), True) if side == "SELL" else max(0.0, tick(x - h(x), False))
            gross += (px - close_px) if side == "SELL" else (close_px - px)
            if close_px > 0:
                cout += _cost(close_px, "BUY" if side == "SELL" else "SELL", lot)
        net = gross * lot - cin - cout
        if net <= -stop:
            exit_at, reason = hm, "stop"
            break
    return {"net": round(net, 2), "R": round(net / risk, 4), "risk": round(risk, 2), "credit": round(credit, 2),
            "reason": reason, "exit": exit_at, "carried": carried}


def line(trades: list[dict]) -> str:
    if not trades:
        return "no days"
    nets = [t["net"] for t in trades]
    cum = peak = worst = 0.0
    for n in nets:
        cum += n
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    years: dict[str, float] = defaultdict(float)
    for t in trades:
        years[t["day"][:4]] += t["net"]
    wins = sum(n > 0 for n in nets)
    return (f"{len(trades)} days, won {wins / len(trades):3.0%}, mean R {sum(t['R'] for t in trades) / len(trades):+.3f}, "
            f"net Rs{sum(nets):+9,.0f} (Rs{sum(nets) / len(nets):+5,.0f} a day), worst stretch Rs{worst:,.0f} | "
            + " ".join(f"{y} {v:+,.0f}" for y, v in sorted(years.items())))


def main() -> int:
    archive = vh.Archive(vh.RAW)
    chain = set(vh.weekly_chain(archive.expiries()))
    days = [d for d in dd.sessions() if dd.EXPLORE_FROM <= d <= dd.EXPLORE_TO and d in chain]
    if not days:
        print("no expiry days with Dhan data: run tools/dhan_download.py first")
        return 2
    h = half_spread(0.002)
    results: dict[str, list[dict]] = defaultdict(list)
    for d in days:
        bars = dd.load_day(d)
        if not bars:
            continue
        for entry, bar in ENTRIES.items():
            for stop in STOPS:
                for structure in STRUCTURES:
                    t = replay(bars, bar, stop, structure, h)
                    if t:
                        t["day"] = d.isoformat()
                        results[f"{structure:<16} entry {entry} stop {int(stop * 100)}%"].append(t)
    print(f"EXPLORATION, NOT EVIDENCE: expiry-day straddles, {len(days)} expiry days, {days[0]} to {days[-1]}, "
          f"0.2% half-spread; all 12 variants, fixed before the run")
    for name in sorted(results):
        print(f"  {name}: {line(results[name])}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"written": datetime.now().isoformat(timespec="seconds"), "trades": results}, indent=1))
    print(f"saved: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
