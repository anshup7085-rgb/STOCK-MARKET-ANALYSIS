"""
EXPLORATION, NOT EVIDENCE: does the expiry-day straddle repeat on indexes it was not found on? SENSEX,
BANKNIFTY, FINNIFTY and MIDCPNIFTY weekly options, exploration years only (to 31 Oct 2024), from Dhan's
minutes (tools/dhan_download_more.py).

    PYTHONPATH=. .venv/bin/python research/explore_expiry_more.py

Exploration 4 found, on NIFTY, that selling the at-the-money straddle on expiry day with a stop at a
net loss of 30% of the credit was positive at all three entry times tried (09:20, 11:00, 13:00). That
was read off one index after looking at several groupings. This runs the same three variants, with the
same code (research/explore_expiry.replay), on four indexes whose data nobody has read. Everything
below was fixed on 10 Nov 2026, while the download was still running:

    variants   straddle, stop 30% of the credit, entry 09:20, 11:00, 13:00; exit 15:15
    fills      a half-spread of 0.2% of the price (NIFTY's measured figure) and, because these indexes
               traded thinner than NIFTY and we have no measurement of our own, 0.5% beside it
    lots       today's: SENSEX 20, BANKNIFTY 30, FINNIFTY 60, MIDCPNIFTY 120 (from memory, unchecked;
               mean R barely depends on the lot), today's Zerodha fees
    expiry     Dhan's series names no expiry date, so a session is an expiry day when the at-the-money
               straddle at 15:25 is worth less than half of the next session's at 09:20, as a share of
               the index: a contract in its last minutes against a fresh week. The rule is checked
               first on NIFTY against NSE's own expiry dates, and the count of misses is printed.

The reading, fixed in advance. The effect REPEATS on an index if all three entry times are net
positive there at 0.2% and at least two stay positive at 0.5%. Repeats on three or four indexes: the
NIFTY finding is probably real. On two: unclear. On one or none: the NIFTY finding was most likely
luck. Whatever the answer, selling a straddle needs far more than Rs20,000; the use of this for a
small account is the other side of it: if sellers are paid on expiry day, buyers of expiry-day
options are the ones paying.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from research.explore_expiry import ENTRIES, line, replay
from research.explore_straddle import half_spread
from trading.fno import dhan_data as dd

REPO = Path(__file__).resolve().parents[1]
MORE = REPO / "state" / "dhan" / "rolling_more"
OUT = REPO / "state" / "dhan" / "explore" / "expiry_more.json"
LOTS = {"SENSEX": 20, "BANKNIFTY": 30, "FINNIFTY": 60, "MIDCPNIFTY": 120}
STOP = 0.30
SPREADS = (0.002, 0.005)
LAST, FIRST = "15:24", "09:19"            # the bars that close at 15:25 and 09:20


def time_value(day_bars: dict, hm: str) -> float | None:
    """The at-the-money straddle at `hm` as a share of the index."""
    m = day_bars.get(hm, {})
    c, p = m.get((0, "CALL")), m.get((0, "PUT"))
    if c is None or p is None or not c.spot or c.close <= 0 or p.close <= 0:
        return None
    return (c.close + p.close) / c.spot


def expiry_days(days: list[date], load) -> list[date]:
    """Sessions whose nearest contract is in its last minutes: see the module's note. `load(day)` gives
    the at-the-money bars of a session."""
    out, prev_day, prev_tv = [], None, None
    for d in days:
        bars = load(d)
        if not bars:
            continue
        open_tv = time_value(bars, FIRST)
        if prev_day is not None and prev_tv is not None and open_tv is not None and prev_tv < 0.5 * open_tv:
            out.append(prev_day)
        prev_day, prev_tv = d, time_value(bars, LAST)
    return out


def sessions(root: Path) -> list[date]:
    return [d for d in dd.sessions(root) if dd.EXPLORE_FROM <= d <= dd.EXPLORE_TO]


def check_on_nifty() -> str:
    """How well the rule recovers NIFTY's expiry days, which NSE's own files give."""
    try:
        from trading.fno import vh
        archive = vh.Archive(vh.RAW)
        chain = set(vh.weekly_chain(archive.expiries()))
        days = [d for d in sessions(dd.ROOT) if d in set(archive.days)]
        truth = {d for d in days if d in chain}
        found = set(expiry_days(days, lambda d: dd.load_day(d, offsets=(0,))))
    except Exception as exc:                                    # noqa: BLE001 - reported; the run goes on
        return f"the check on NIFTY could not run ({type(exc).__name__}: {str(exc)[:80]})"
    return (f"the expiry rule on NIFTY: found {len(found & truth)} of NSE's {len(truth)} expiry days, "
            f"missed {len(truth - found)}, wrongly marked {len(found - truth)}")


def verdict(nets: dict[float, list[float]]) -> bool:
    """nets[spread] = the three entry times' net totals."""
    return all(n > 0 for n in nets[SPREADS[0]]) and sum(n > 0 for n in nets[SPREADS[1]]) >= 2


def complete(root: Path) -> bool:
    """The download works through the days in order, so one file dated after the exploration years
    means every exploration day of this index is in."""
    folder = root / "code1" / dd.series_name(0, "CALL")
    return any(date.fromisoformat(p.name[:10]) > dd.EXPLORE_TO for p in folder.glob("*.json.gz"))


def main() -> int:
    waiting = [n for n in LOTS if not complete(MORE / n)]
    if waiting:
        print(f"still downloading: {', '.join(waiting)}. This is read once, when all four are in "
              f"(tail -5 logs/dhan-download-more.log); nothing was read.")
        return 2
    print("EXPLORATION, NOT EVIDENCE: the expiry-day straddle (stop 30%) on indexes it was not found on, "
          "to 31 Oct 2024; rules and reading fixed on 10 Nov 2026, before this data was read")
    print(f"  {check_on_nifty()}")
    results: dict[str, list[dict]] = defaultdict(list)
    repeats = {}
    for name in LOTS:
        root = MORE / name
        days = sessions(root)
        exp = expiry_days(days, lambda d: dd.load_day(d, offsets=(0,), root=root))
        if not exp:
            print(f"\n{name}: no expiry day found in {len(days)} sessions")
            repeats[name] = False
            continue
        print(f"\n{name}: {len(exp)} expiry days, {exp[0]} to {exp[-1]}, lot {LOTS[name]}")
        for d in exp:                                           # one day in memory at a time
            bars = dd.load_day(d, root=root)
            for spread in SPREADS:
                for entry, bar in ENTRIES.items():
                    t = replay(bars, bar, STOP, "straddle", half_spread(spread), lot=LOTS[name])
                    if t:
                        t["day"] = d.isoformat()
                        results[f"{name} entry {entry} half-spread {spread:.1%}"].append(t)
        nets: dict[float, list[float]] = {}
        for spread in SPREADS:
            nets[spread] = []
            for entry in ENTRIES:
                ts = results[f"{name} entry {entry} half-spread {spread:.1%}"]
                nets[spread].append(sum(t["net"] for t in ts))
                print(f"  entry {entry}, half-spread {spread:.1%}: {line(ts)}")
        repeats[name] = verdict(nets)
        print(f"  by the rule fixed in advance, the effect {'REPEATS' if repeats[name] else 'does NOT repeat'} on {name}")
    n = sum(repeats.values())
    reading = ("the NIFTY finding is probably real" if n >= 3 else "unclear" if n == 2
               else "the NIFTY finding was most likely luck")
    print(f"\nrepeats on {n} of {len(repeats)} indexes: {reading}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"written": datetime.now().isoformat(timespec="seconds"), "repeats": repeats,
                               "trades": results}, indent=1))
    print(f"saved: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
