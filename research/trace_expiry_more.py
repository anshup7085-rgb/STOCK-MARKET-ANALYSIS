"""
Why does MIDCPNIFTY's expiry-day straddle at 09:20 earn MORE with the wider half-spread (0.5%) than with
the narrower one (0.2%)? A cost alone cannot do that, so the two runs must have closed differently on
some days. This reads the saved trades of research/explore_expiry_more.py (no market data, no new
result) and lists, day by day, where the two differ.

    PYTHONPATH=. .venv/bin/python research/trace_expiry_more.py [INDEX] [ENTRY]   # default MIDCPNIFTY 09:20
    PYTHONPATH=. .venv/bin/python research/trace_expiry_more.py MIDCPNIFTY 09:20 --day 2024-04-08   # its minutes

Two explanations were written down before this was run (INTRADAY_LOG step 25):
  (a) the stop: the wider spread makes the position look worse from its first minute, so the 30% stop
      is reached earlier, before a later jump in the option's price that the narrower run then pays for;
  (b) the data: a strike that leaves Dhan's ATM +/- 10 window keeps its last price ("carried" minutes),
      so a mark can be stale, and a stale mark can hide a loss or a gain until the window comes back.
The listing shows which: (a) days stop at different minutes with few carried minutes; (b) the days
that differ carry many minutes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SAVED = REPO / "state" / "dhan" / "explore" / "expiry_more.json"


def compare(trades: dict, name: str, entry: str) -> list[dict]:
    narrow = {t["day"]: t for t in trades.get(f"{name} entry {entry} half-spread 0.2%", [])}
    wide = {t["day"]: t for t in trades.get(f"{name} entry {entry} half-spread 0.5%", [])}
    out = []
    for day in sorted(set(narrow) | set(wide)):
        a, b = narrow.get(day), wide.get(day)
        out.append({"day": day, "a": a, "b": b, "diff": (b["net"] if b else 0.0) - (a["net"] if a else 0.0),
                    "same_close": bool(a and b and a["reason"] == b["reason"] and a["exit"] == b["exit"])})
    return out


def side(t: dict | None) -> str:
    if t is None:
        return "no trade"
    return f"{t['reason']:<4} at {t['exit']}, net Rs{t['net']:+9,.0f}, {t.get('carried', 0):3d} carried"


def minutes_around(name: str, entry: str, day: str, rows: list[dict], root: Path | None = None) -> list[str]:
    """The index and the straddle's two legs, minute by minute, around the stops of one day: does the
    index move when the option jumps, or does one option print alone (a bad print in thin trading)?"""
    from datetime import date, datetime, timedelta

    from research.explore_expiry import ENTRIES
    from trading.fno import dhan_data as dd
    r = next((r for r in rows if r["day"] == day), None)
    if r is None:
        return [f"{day} is not one of the saved expiry days"]
    bars = dd.load_day(date.fromisoformat(day), root=root or REPO / "state" / "dhan" / "rolling_more" / name)
    first = bars.get(ENTRIES[entry], {}).get((0, "CALL"))
    if first is None:
        return [f"no at-the-money call at {ENTRIES[entry]} on {day}"]
    k = first.strike
    exits = sorted(t["exit"] for t in (r["a"], r["b"]) if t)
    t0 = datetime.strptime(exits[0], "%H:%M") - timedelta(minutes=8)
    t1 = datetime.strptime(exits[-1], "%H:%M") + timedelta(minutes=4)
    out = [f"{name} {day}, the straddle sold at {entry} at strike {k:g}; the stops: {', '.join(exits)}",
           f"  {'minute':<7} {'index':>10} {'call':>9} {'put':>9} {'straddle':>9}"]
    t = t0
    while t <= t1:
        hm = t.strftime("%H:%M")
        c, q = dd.at_strike(bars, hm, k, "CALL"), dd.at_strike(bars, hm, k, "PUT")
        spot = next((b.spot for b in (c, q) if b and b.spot), None)
        cell = lambda b: f"{b.close:9.2f}" if b else f"{'-':>9}"          # noqa: E731
        both = f"{c.close + q.close:9.2f}" if c and q else f"{'-':>9}"
        out.append(f"  {hm:<7} {spot if spot else '-':>10} {cell(c)} {cell(q)} {both}")
        t += timedelta(minutes=1)
    return out


def main() -> int:
    if "--day" in sys.argv:
        i = sys.argv.index("--day")
        day = sys.argv[i + 1]
        args = sys.argv[1:i] + sys.argv[i + 2:]
    else:
        day, args = None, sys.argv[1:]
    name = args[0] if len(args) > 0 else "MIDCPNIFTY"
    entry = args[1] if len(args) > 1 else "09:20"
    if not SAVED.exists():
        print("run research/explore_expiry_more.py first")
        return 2
    trades = json.loads(SAVED.read_text())["trades"]
    rows = compare(trades, name, entry)
    if not rows:
        print(f"no saved trades for {name} at {entry}")
        return 2
    if day:
        print("\n".join(minutes_around(name, entry, day, rows)))
        return 0
    same = [r for r in rows if r["same_close"]]
    moved = [r for r in rows if not r["same_close"]]
    total = sum(r["diff"] for r in rows)
    print(f"{name}, entry {entry}: {len(rows)} expiry days; the 0.5% run minus the 0.2% run = Rs{total:+,.0f}")
    print(f"  closed the same way (same reason, same minute): {len(same)} days, difference Rs{sum(r['diff'] for r in same):+,.0f}"
          " (the cost of the wider spread)")
    print(f"  closed differently: {len(moved)} days, difference Rs{sum(r['diff'] for r in moved):+,.0f}")
    carried = sum(1 for r in moved if max((r['a'] or {}).get('carried', 0), (r['b'] or {}).get('carried', 0)) > 30)
    print(f"  of those, days with more than 30 carried (stale) leg-minutes: {carried}")
    print("\n  the days that closed differently, largest difference first:")
    print(f"  {'day':<10}  {'0.2% half-spread':<46}  {'0.5% half-spread':<46}  difference")
    for r in sorted(moved, key=lambda r: -abs(r["diff"]))[:12]:
        print(f"  {r['day']:<10}  {side(r['a']):<46}  {side(r['b']):<46}  Rs{r['diff']:+,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
