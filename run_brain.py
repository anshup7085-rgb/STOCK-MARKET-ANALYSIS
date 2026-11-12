#!/usr/bin/env python3
"""
The market brain's command line. It trades nothing; it forecasts and keeps score.

    PYTHONPATH=. .venv/bin/python run_brain.py backfill     # its paper record on the exploration years

`backfill` walks through history one month at a time: at each month's start it learns from every
answered day before it, then forecasts that month's days, which it has never seen. Two questions a
day about the next session (brain/features.py): will NIFTY close UP, and will its move be BIG, larger
than its usual move. The record runs to 31 Oct 2024 only: the two years after are held back, as for
every other test here, so that a fixed version of this model can be judged once on days nobody tuned
it on.

The reading, fixed on 12 Nov 2026 before the first run on real data. A question shows skill in
exploration only if all three hold: its skill is above 0; z is at least 2.58; and the skill is above
0 in at least 7 of the 9 calendar years 2016 to 2024. What I expect, written down so it can be
checked: no skill on UP (direction is close to a coin toss); clear skill on BIG (quiet and stormy
days come in runs). Skill on BIG is not yet money: it only becomes a trade if it knows more than
option prices already do, which is the next test.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from brain import data, features, model

REPO = Path(__file__).resolve().parent
OUT = REPO / "state" / "brain"
EXPLORE_TO = date(2024, 10, 31)          # the same line as trading/fno/dhan_data.py
RECORD_FROM = date(2016, 1, 1)           # nine calendar years of record: 2016 to 2024
Z_NEEDED, YEARS_NEEDED = 2.58, 7


def line(target: str, preds: list[dict]) -> list[str]:
    s = model.score(preds)
    if s.get("n", 0) < 2:
        return [f"  {target.upper()}: too few forecasts"]
    by_year: dict[str, list[dict]] = defaultdict(list)
    for q in preds:
        by_year[q["day"][:4]].append(q)
    skills = {y: model.score(v).get("skill", 0.0) for y, v in sorted(by_year.items()) if len(v) >= 50}
    good = sum(v > 0 for v in skills.values())
    shows = s["skill"] > 0 and s["z"] >= Z_NEEDED and good >= YEARS_NEEDED
    return [f"  {target.upper()}: {s['n']} forecasts, {preds[0]['day']} to {preds[-1]['day']}; the answer was yes on "
            f"{s['yes_rate']:.1%} of days; the brain was right on {s['right']:.1%}; skill {s['skill']:+.2%} "
            f"(z {s['z']:+.2f}); skill above 0 in {good} of {len(skills)} years",
            "      skill by year: " + "; ".join(f"{y} {v:+.1%}" for y, v in skills.items()),
            f"      by the rule fixed in advance: {'SHOWS SKILL in exploration' if shows else 'no skill shown'}"]


def cmd_backfill(args) -> int:
    indexes, fii = data.load_indexes(), data.load_fii_long_share()
    rows = features.build(indexes, fii)
    rows = [r for r in rows if r["day"] <= EXPLORE_TO]          # nothing later is read into the record
    if len(rows) < model.MIN_TRAIN + 250:
        print("too little history on disk: run deploy/backfill.sh first (NSE's index files since 2012)")
        return 2
    print(f"EXPLORATION, NOT EVIDENCE: the brain's walk-forward record, {rows[0]['day']} to {rows[-1]['day']} "
          f"({len(rows)} sessions, {len(features.FEATURES)} inputs); the reading was fixed before this ran")
    have = {f: sum(r["x"].get(f) is not None for r in rows) for f in features.FEATURES}
    thin = [f"{f} {n / len(rows):.0%}" for f, n in have.items() if n < 0.9 * len(rows)]
    if thin:
        print("  inputs present on under 90% of days: " + ", ".join(thin))
    saved = {}
    for target in features.TARGETS:
        preds = model.walk_forward(rows, target, first=RECORD_FROM, last=EXPLORE_TO)
        saved[target] = preds
        print("\n".join(line(target, preds)))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "backfill.json").write_text(json.dumps({"written": datetime.now().isoformat(timespec="seconds"),
                                                   "forecasts": saved}, indent=1))
    print(f"saved: {OUT / 'backfill.json'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="the market brain: forecasts and keeps score; trades nothing")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backfill", help="its walk-forward record on the exploration years").set_defaults(fn=cmd_backfill)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
