"""
EXPLORATION, NOT EVIDENCE: the simple crypto rules of crypto/strategies.py on BTC and ETH, daily, on the
exploration years only (to 31 Oct 2024), AFTER India's crypto tax.

    PYTHONPATH=. .venv/bin/python research/explore_crypto.py

Fixed on 26 Nov 2026, before any rule was run on any price:
  money     a paper account of Rs20,000 per rule and coin, all in or all out, from 1 Jun 2018 (the rules
            need 200 days of history; BTC's data begins Aug 2017) to 31 Oct 2024
  fills     at the day's close, the moment the rule decides (crypto trades around the clock, so the next
            moment's price is that close); each buy and each sell pays 0.25% (an exchange fee of 0.2%
            and a half-spread of 0.05%, assumed for an Indian exchange, not checked)
  tax       India's tax on crypto (Budget 2026, unchanged): 30% plus 4% cess = 31.2% of every sale's gain
            over its cost of buying; a loss cannot be set against any gain; fees are not deductible. The
            1% TDS on each sale is credited against that tax, so it moves cash in time but is left out
  benchmark buying on 1 Jun 2018 and selling on 31 Oct 2024, taxed once on the whole gain

The reading, fixed in advance. On each coin, a rule is labelled:
  BEATS HOLDING   it ends above buy-and-hold after tax
  SAFER           it ends below buy-and-hold but above its own Rs20,000, and its worst fall is less than
                  half of buy-and-hold's
  NO              otherwise
A rule is worth following forward on paper only if it earns the same label, other than NO, on BTC and on
ETH: two coins, so one lucky coin is not enough.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from crypto import store
from crypto.strategies import RULES, positions

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "state" / "crypto" / "explore_rules.json"
START, END = date(2018, 6, 1), store.EXPLORE_TO
CAPITAL, FEE, TAX = 20_000.0, 0.0025, 0.312


def run(days: list[date], closes: list[float], rule: str) -> dict:
    """The paper account through START..END; taxes each sale's gain; returns the record."""
    pos = positions(closes, rule)
    cash, units, cost, tax_paid, trades = CAPITAL, 0.0, 0.0, 0.0, 0
    equity, by_year = [], defaultdict(float)
    first = next(i for i, d in enumerate(days) if d >= START)
    last = max(i for i, d in enumerate(days) if d <= END)
    year_start = {}
    for i in range(first, last + 1):
        c = closes[i]
        want = pos[i] if i < last else False                   # everything is sold on the last day
        if want and units == 0:
            spend = cash
            units = spend * (1 - FEE) / c
            cost, cash = spend, 0.0                            # the cost of buying: what left the account
            trades += 1
        elif not want and units > 0:
            value = units * c * (1 - FEE)
            gain = units * c - cost                           # sale price over cost; fees not deductible
            t = TAX * gain if gain > 0 else 0.0
            tax_paid += t
            cash, units = value - t, 0.0
        eq = cash + units * c
        equity.append(eq)
        y = days[i].year
        year_start.setdefault(y, equity[-2] if len(equity) > 1 else CAPITAL)
        by_year[y] = eq / year_start[y] - 1
    peak, worst = CAPITAL, 0.0
    for e in equity:
        peak = max(peak, e)
        worst = min(worst, e / peak - 1)
    held = sum(pos[first:last]) / max(1, last - first)
    return {"end": round(cash, 2), "tax": round(tax_paid, 2), "trades": trades, "worst_fall": round(worst, 4),
            "time_held": round(held, 3), "by_year": {str(k): round(v, 4) for k, v in by_year.items()}}


def label(r: dict, hold: dict) -> str:
    if r["end"] > hold["end"]:
        return "BEATS HOLDING"
    if r["end"] > CAPITAL and abs(r["worst_fall"]) < 0.5 * abs(hold["worst_fall"]):
        return "SAFER"
    return "NO"


def main() -> int:
    results: dict[str, dict] = {}
    labels: dict[str, dict[str, str]] = defaultdict(dict)
    for sym in ("BTCUSDT", "ETHUSDT"):
        candles = [c for c in store.load(sym, "1d") if store.utc_day(c) <= END]
        if len(candles) < 400:
            print("too little history: run `run_crypto.py download` first")
            return 2
        days, closes = [store.utc_day(c) for c in candles], [c.close for c in candles]
        hold = run(days, closes, "hold")
        print(f"\n{sym[:-4]}, {START} to {END}, Rs{CAPITAL:,.0f} each, after a 0.25% cost a side and 31.2% tax on each gain:")
        for rule in RULES:
            r = hold if rule == "hold" else run(days, closes, rule)
            results[f"{sym} {rule}"] = r
            lab = "benchmark" if rule == "hold" else label(r, hold)
            if rule != "hold":
                labels[rule][sym] = lab
            years = " ".join(f"{y} {v:+.0%}" for y, v in r["by_year"].items())
            print(f"  {rule:<9} ends Rs{r['end']:>10,.0f} (tax Rs{r['tax']:,.0f}), {r['trades']:3d} buys, held "
                  f"{r['time_held']:4.0%} of days, worst fall {r['worst_fall']:+.0%}  [{lab}]  | {years}")
    print("\nby the reading fixed in advance (the same label on both coins):")
    for rule, by in labels.items():
        same = len(set(by.values())) == 1 and "NO" not in by.values()
        print(f"  {rule:<9} BTC {by.get('BTCUSDT')}, ETH {by.get('ETHUSDT')}: "
              f"{'FOLLOW FORWARD on paper' if same else 'not followed'}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"written": datetime.now().isoformat(timespec="seconds"), "results": results,
                               "labels": labels}, indent=1))
    print(f"saved: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
