"""
EXPLORATION, NOT EVIDENCE: the 09:20 straddle replay (research/explore_straddle.py) split by how many
sessions remain to the nearest weekly expiry, on the exploration years only.

    PYTHONPATH=. .venv/bin/python research/explore_straddle_days.py

The first replay showed Thursdays, the NIFTY weekly expiry day through 2024, far ahead of the other
weekdays. A weekday is a proxy: holidays move expiries. This reads NSE's own expiry dates from the
F&O files downloaded for V-H (state/vh/raw) and counts, for each replayed day, the sessions left to
the nearest weekly expiry (0 = the expiry day itself). It reads the saved replay; it fetches nothing.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from trading.fno import vh

REPO = Path(__file__).resolve().parents[1]
REPLAY = REPO / "state" / "dhan" / "explore" / "straddle.json"


def block(trades: list[dict]) -> str:
    if not trades:
        return "no days"
    nets = [t["net"] for t in trades]
    cum = peak = worst_run = 0.0
    for n in nets:
        cum += n
        peak = max(peak, cum)
        worst_run = min(worst_run, cum - peak)
    wins = sum(n > 0 for n in nets)
    return (f"{len(trades)} days, won {wins / len(trades):.0%}, net Rs{sum(nets):+,.0f} (Rs{sum(nets) / len(nets):+,.0f} a day), "
            f"worst day Rs{min(nets):+,.0f}, worst stretch Rs{worst_run:,.0f}")


def main() -> int:
    if not REPLAY.exists():
        print("run research/explore_straddle.py first")
        return 2
    saved = json.loads(REPLAY.read_text())
    archive = vh.Archive(vh.RAW)
    sessions = archive.days
    chain = vh.weekly_chain(archive.expiries())
    print("EXPLORATION, NOT EVIDENCE: the 09:20 straddle by sessions left to the nearest weekly expiry "
          "(0 = expiry day), Oct 2021 to Oct 2024")
    for label in ("0.2%", "1%"):
        groups: dict[str, list[dict]] = defaultdict(list)
        years: dict[str, list[dict]] = defaultdict(list)
        for t in saved["trades"][label]:
            d = date.fromisoformat(t["day"])
            e = next((x for x in chain if x >= d), None)
            if e is None or d not in sessions:
                continue
            left = sum(1 for s in sessions if d < s <= e)
            key = str(left) if left < 4 else "4+"
            groups[key].append(t)
            if left == 0:
                years[t["day"][:4]].append(t)
        print(f"\nhalf-spread {label}:")
        for key in ("0", "1", "2", "3", "4+"):
            print(f"  {key:>2} left: {block(groups[key])}")
        print("  expiry day by year: " + "; ".join(f"{y} {block(v)}" for y, v in sorted(years.items())))
        within = [t for t in groups["0"] if t["within_budget"]]
        print(f"  expiry day, only within the Rs9,000 budget: {block(within)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
