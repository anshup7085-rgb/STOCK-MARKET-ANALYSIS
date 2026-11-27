"""
EXPLORATION, NOT EVIDENCE: after a big overnight gap, does NIFTY give part of it back, or run on? On the
exploration years only (Jan 2015 to Oct 2024), from NIFTY 50 minute candles (tools/index_minutes_download.py).

    PYTHONPATH=. .venv/bin/python research/explore_gap.py

The gap is today's 09:15 open (set by the pre-open auction) against yesterday's last minute. On days
when it is at least the threshold, take one NIFTY futures lot against the gap (the "fade") at the
entry minute and close it at the exit minute. The grid, all 8 variants reported, fixed before any of
the data was fetched:

    gap      0.5% or more; 1% or more
    entry    09:20; 09:30           (the close of the minute that ends then)
    exit     10:15; 15:15           (the first hour; the explorer's square-off)

A positive result means fading paid; a negative one means following the gap paid, and the same
numbers read with the sign turned are that trade (before costs). Costs as in research/explore_momentum.py:
the index as the future, a 0.007% half-spread, one lot of 65, today's Zerodha fees. The reading fixed
in advance: a pattern counts only if its sign holds in most years, at both thresholds and both exits;
a gross effect smaller than the futures cost goes on to an options test, not to a registration.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

from research.explore_momentum import FROM, LOT, fut_round_trip, line
from trading.fno import index_minutes as im
from trading.fno.dhan_data import EXPLORE_TO

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "state" / "index_minutes" / "explore_gap.json"
GAPS = (0.005, 0.01)
ENTRIES = {"09:20": "09:19", "09:30": "09:29"}
EXITS = {"10:15": "10:14", "15:15": "15:14"}


def days_of(sessions: dict) -> list[dict]:
    rows, prev = [], None
    need = ["09:15"] + list(ENTRIES.values()) + list(EXITS.values())
    for d, m in sessions.items():
        if prev is not None and "15:29" in prev and all(k in m for k in need):
            rows.append({"day": d, "gap": m["09:15"].open / prev["15:29"].close - 1,
                         **{k: m[k].close for k in need[1:]}})
        prev = m
    return rows


def trades(rows: list[dict], gap: float, entry: str, exit_: str) -> list[dict]:
    a, b = ENTRIES[entry], EXITS[exit_]
    out = []
    for r in rows:
        if abs(r["gap"]) < gap:
            continue
        long = r["gap"] < 0                                   # fade: buy a gap down, sell a gap up
        move = r[b] / r[a] - 1
        signed = move if long else -move
        out.append({"day": r["day"].isoformat(), "gap": round(r["gap"], 6), "entry": round(r[a], 2),
                    "gross_bps": round(signed * 1e4, 3),
                    "net": round(signed * r[a] * LOT - fut_round_trip(r[a], long), 2)})
    return out


def main() -> int:
    sessions = im.load(im.NIFTY, FROM, EXPLORE_TO)
    rows = days_of(sessions)
    if len(rows) < 100:
        print("too few NIFTY minute sessions: let `morning` run tools/index_minutes_download.py first")
        return 2
    print(f"EXPLORATION, NOT EVIDENCE: fading NIFTY's opening gap, {len(rows)} sessions, {rows[0]['day']} to "
          f"{rows[-1]['day']}; all 8 variants, fixed before the run (positive: fading paid; negative: following did)")
    for g in GAPS:
        print(f"  gaps of {g:.1%} or more: {sum(abs(r['gap']) >= g for r in rows)} sessions")
    results = {}
    for g in GAPS:
        for entry in ENTRIES:
            for exit_ in EXITS:
                name = f"gap {g:.1%}+ entry {entry} exit {exit_}"
                results[name] = trades(rows, g, entry, exit_)
                print(f"  {name}: {line(results[name])}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"written": datetime.now().isoformat(timespec="seconds"), "trades": results}, indent=1))
    print(f"saved: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
