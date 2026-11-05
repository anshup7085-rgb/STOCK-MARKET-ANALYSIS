"""
EXPLORATION, NOT EVIDENCE: the explorer's 09:20 short straddle replayed on Dhan's minute data,
October 2021 to October 2024 only (trading/fno/dhan_data.py holds the later years back).

    PYTHONPATH=. .venv/bin/python research/explore_straddle.py

The rule is the explorer's own template (trading/fno/library.py, "short_straddle"): sell the
at-the-money call and put of the nearest weekly expiry at 09:20; close both when the position's net
loss, every cost included, reaches 30% of the credit; otherwise close at 15:15. Risk as the explorer
sizes it: the stop x 1.5 plus entry and exit costs. One lot of 65 (today's lot) and today's Zerodha
fee card for every day, as V-H did.

Prices: the minute bar ending at the decision time (09:19 for the 09:20 entry, and so on). There is
no bid or ask in the data, so every fill is a half-spread worse than the close: 0.2% of the price,
at least 0.05 (one tick), sells rounded down and buys up to the tick. The same replay with a 1%
half-spread is reported beside it.

Prints a summary and writes every trade to state/dhan/explore/straddle.json. Nothing here is a
registered test, and nothing it finds is evidence: it chooses what to register.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

from market.charges import FNO_ZERODHA, option_order_costs
from trading.fno import dhan_data as dd

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "state" / "dhan" / "explore" / "straddle.json"
LOT = 65
STOP_FRAC = 0.30            # the template's stop: a loss of 0.3 x the credit
STOP_SLIP = 1.5             # the explorer's risk: stop x 1.5 + costs
BUDGET = 9000.0             # config/fno_strategies.example.json, short_straddle
ENTRY_BAR, LAST_BAR = "09:19", "15:14"


def half_spread(frac: float):
    return lambda x: max(0.05, frac * x)


def tick(x: float, up: bool) -> float:
    n = (Decimal(repr(x)) / Decimal("0.05")).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return float(n * Decimal("0.05"))


def minutes(start: str, end: str) -> list[str]:
    t, out = datetime.combine(date(2000, 1, 1), time.fromisoformat(start)), []
    stop = datetime.combine(date(2000, 1, 1), time.fromisoformat(end))
    while t <= stop:
        out.append(t.strftime("%H:%M"))
        t += timedelta(minutes=1)
    return out


def replay_day(day_bars: dict, h) -> dict | None:
    """One day's straddle, or None if the 09:19 bar or the at-the-money pair is missing."""
    first = day_bars.get(ENTRY_BAR, {})
    ce0, pe0 = first.get((0, "CALL")), first.get((0, "PUT"))
    if ce0 is None:
        return None
    k = ce0.strike
    pe = pe0 if pe0 is not None and pe0.strike == k else dd.at_strike(day_bars, ENTRY_BAR, k, "PUT")
    if pe is None or ce0.close <= 0 or pe.close <= 0:
        return None
    sell_ce, sell_pe = tick(ce0.close - h(ce0.close), False), tick(pe.close - h(pe.close), False)
    if sell_ce <= 0 or sell_pe <= 0:
        return None
    credit = (sell_ce + sell_pe) * LOT
    cin = option_order_costs(sell_ce, LOT, "SELL", FNO_ZERODHA).total + option_order_costs(sell_pe, LOT, "SELL", FNO_ZERODHA).total
    cout_est = (option_order_costs(sell_ce, LOT, "BUY", FNO_ZERODHA).total
                + option_order_costs(sell_pe, LOT, "BUY", FNO_ZERODHA).total)
    stop = STOP_FRAC * credit
    risk = stop * STOP_SLIP + cin + cout_est
    last = {"CALL": ce0.close, "PUT": pe.close}
    carried = 0
    exit_at, reason, net, worst = LAST_BAR, "time", 0.0, 0.0
    for hm in minutes("09:20", LAST_BAR):
        for right in ("CALL", "PUT"):
            b = dd.at_strike(day_bars, hm, k, right)
            if b is None:
                carried += 1
            else:
                last[right] = b.close
        buy_ce, buy_pe = tick(last["CALL"] + h(last["CALL"]), True), tick(last["PUT"] + h(last["PUT"]), True)
        cout = (option_order_costs(buy_ce, LOT, "BUY", FNO_ZERODHA).total
                + option_order_costs(buy_pe, LOT, "BUY", FNO_ZERODHA).total)
        net = (sell_ce + sell_pe - buy_ce - buy_pe) * LOT - cin - cout
        worst = min(worst, net)
        if net <= -stop:
            exit_at, reason = hm, "stop"
            break
    return {"strike": k, "credit": round(credit, 2), "stop": round(stop, 2), "risk": round(risk, 2),
            "within_budget": risk <= BUDGET, "net": round(net, 2), "R": round(net / risk, 4), "exit": exit_at,
            "reason": reason, "worst_net": round(worst, 2), "carried_minutes": carried,
            "spot": first[(0, "CALL")].spot}


def summary(trades: list[dict], label: str) -> list[str]:
    if not trades:
        return [f"{label}: no trades"]
    nets = [t["net"] for t in trades]
    rs = [t["R"] for t in trades]
    cum = peak = dd_ = 0.0
    for n in nets:
        cum += n
        peak = max(peak, cum)
        dd_ = min(dd_, cum - peak)
    wins = sum(n > 0 for n in nets)
    avg_win = sum(n for n in nets if n > 0) / max(wins, 1)
    avg_loss = sum(n for n in nets if n <= 0) / max(len(nets) - wins, 1)
    stops = sum(t["reason"] == "stop" for t in trades)
    out = [f"{label}: {len(trades)} days; won {wins / len(trades):.1%}; mean R {sum(rs) / len(rs):+.3f}; "
           f"net Rs{sum(nets):+,.0f} (Rs{sum(nets) / len(nets):+,.0f} a day); worst stretch Rs{dd_:,.0f}",
           f"    average win Rs{avg_win:+,.0f}, average loss Rs{avg_loss:+,.0f}; stopped out on {stops} days "
           f"({stops / len(trades):.0%}); worst day Rs{min(nets):+,.0f}, best Rs{max(nets):+,.0f}"]
    by_year: dict[str, list[float]] = defaultdict(list)
    by_dow: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_year[t["day"][:4]].append(t["net"])
        by_dow[date.fromisoformat(t["day"]).strftime("%a")].append(t["net"])
    out.append("    by year: " + "; ".join(f"{y} {len(v)} days Rs{sum(v):+,.0f}" for y, v in sorted(by_year.items())))
    out.append("    by weekday: " + "; ".join(f"{d} Rs{sum(v):+,.0f}" for d, v in
                                              sorted(by_dow.items(), key=lambda kv: "MonTueWedThuFri".index(kv[0]))))
    return out


def main() -> int:
    days = [d for d in dd.sessions() if dd.EXPLORE_FROM <= d <= dd.EXPLORE_TO]
    if not days:
        print("no Dhan data on disk: run tools/dhan_download.py first")
        return 2
    results = {"0.2%": [], "1%": []}
    skipped = 0
    for d in days:
        bars = dd.load_day(d)
        if not bars:
            skipped += 1
            continue
        for label, frac in (("0.2%", 0.002), ("1%", 0.01)):
            t = replay_day(bars, half_spread(frac))
            if t:
                t["day"] = d.isoformat()
                results[label].append(t)
    base = results["0.2%"]
    lines = [f"EXPLORATION, NOT EVIDENCE: 09:20 short straddle, {days[0]} to {days[-1]}, "
             f"{len(base)} sessions replayed ({skipped} weekdays without data: holidays)"]
    lines += summary(base, "every day, 0.2% half-spread")
    lines += summary([t for t in base if t["within_budget"]], "only days within the Rs9,000 risk budget")
    lines += summary(results["1%"], "every day, 1% half-spread")
    carried = sum(t["carried_minutes"] for t in base)
    lines.append(f"data: {carried} leg-minutes carried forward (strike outside ATM±10 or a missing minute)")
    print("\n".join(lines))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"written": datetime.now().isoformat(timespec="seconds"), "summary": lines,
                               "trades": results}, indent=1))
    print(f"saved: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
