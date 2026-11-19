"""
Why does MIDCPNIFTY's expiry-day straddle at 09:20 earn MORE with the wider half-spread (0.5%) than with
the narrower one (0.2%)? A cost alone cannot do that, so the two runs must have closed differently on
some days. This reads the saved trades of research/explore_expiry_more.py (no market data, no new
result) and lists, day by day, where the two differ.

    PYTHONPATH=. .venv/bin/python research/trace_expiry_more.py [INDEX] [ENTRY]   # default MIDCPNIFTY 09:20

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


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "MIDCPNIFTY"
    entry = sys.argv[2] if len(sys.argv) > 2 else "09:20"
    if not SAVED.exists():
        print("run research/explore_expiry_more.py first")
        return 2
    trades = json.loads(SAVED.read_text())["trades"]
    rows = compare(trades, name, entry)
    if not rows:
        print(f"no saved trades for {name} at {entry}")
        return 2
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
