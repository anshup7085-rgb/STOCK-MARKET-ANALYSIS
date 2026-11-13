#!/usr/bin/env python3
"""
The market brain's command line. It trades nothing; it forecasts and keeps score.

    PYTHONPATH=. .venv/bin/python run_brain.py backfill     # its paper record on the exploration years
    PYTHONPATH=. .venv/bin/python run_brain.py today        # score yesterday, forecast the next session
    PYTHONPATH=. .venv/bin/python run_brain.py check        # what history is on disk (counts only)

`today` writes each forecast to a hash-chained journal (state/brain/journal.jsonl) before the session
it is about, and scores it once that session's file is on disk. It fits on every answered day except
the held-back ones. deploy/evening.sh runs it after recording the day's files.

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
from datetime import date, datetime, time, timedelta
from pathlib import Path

from brain import data, features, model
from market.config import IST

NOW = lambda: datetime.now(IST)                                 # noqa: E731 - replaced in tests

REPO = Path(__file__).resolve().parent
OUT = REPO / "state" / "brain"
EXPLORE_TO = date(2024, 10, 31)          # the same line as trading/fno/dhan_data.py
HOLDOUT_TO = date(2026, 10, 18)          # held back to here; later days are the forward record
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


def may_train(d: date) -> bool:
    """The held-back years are never fitted on before a registration (research/BRAIN.md)."""
    return d <= EXPLORE_TO or d > HOLDOUT_TO


def cmd_check(args) -> int:
    """What is on disk, by year: files, and how many of them hold a NIFTY 50 close. Counts only."""
    files = data._files("index_close", data.ARCHIVE, data.DAILY)
    if not files:
        print("no index files on disk")
        return 2
    by_year: dict[int, list] = defaultdict(list)
    for d, path in files.items():
        by_year[d.year].append(path)
    first_bad = None
    for y in sorted(by_year):
        ok = 0
        for path in by_year[y]:
            if features.NIFTY in data.parse_index_file(path.read_bytes()):
                ok += 1
            elif first_bad is None:
                first_bad = path
        print(f"  {y}: {len(by_year[y])} index files, NIFTY 50 read from {ok}")
    if first_bad is not None:
        head = first_bad.read_bytes()[:600].decode("utf-8-sig", errors="replace").splitlines()
        names = sorted({ln.split(",")[0].strip() for ln in head[1:6]})
        print(f"  the first file NIFTY 50 was not read from: {first_bad.name}")
        print(f"    its columns: {head[0][:300] if head else '(empty)'}")
        print(f"    its first index names: {names}")
    fii = data.load_fii_long_share()
    if fii:
        print(f"  FII positions: {len(fii)} days, {min(fii)} to {max(fii)}")
    return 0


def next_session_opens(day: date, cal) -> datetime:
    d = day + timedelta(days=1)
    while not cal.is_trading_day(d):
        d += timedelta(days=1)
    return datetime.combine(d, time(9, 15), IST)


def fetch_recent(days: int = 25) -> None:
    """NSE's two small files for the last few weeks, where they are missing. A failure is said, not raised."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("backfill_context", REPO / "tools" / "backfill_context.py")
    bf = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(bf)
        today = datetime.now(IST).date()
        bf.run(today - timedelta(days=days), today, retry_missing=True)
    except Exception as exc:                                    # noqa: BLE001
        print(f"(could not fetch the latest NSE files: {type(exc).__name__}: {str(exc)[:80]})")


def cmd_today(args) -> int:
    """Score what can now be scored, forecast the next session, and print the brief."""
    from market.calendar import load_calendar
    from trading.journal import Journal
    if getattr(args, "fetch", False):
        fetch_recent()
    cal, now = load_calendar(), NOW()
    indexes, fii = data.load_indexes(), data.load_fii_long_share()
    rows = features.build(indexes, fii)
    if len(rows) < model.MIN_TRAIN + 50:
        print("too little history on disk for a forecast")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    j = Journal(OUT / "journal.jsonl", "brain")
    recs = j.records()
    by_day = {r["day"].isoformat(): r for r in rows}
    made = {(r["payload"]["after"], r["payload"]["question"]): {**r["payload"], "ts": r["ts"]}
            for r in recs if r["type"] == "BRAIN_FORECAST"}
    scored = {(r["payload"]["after"], r["payload"]["question"]) for r in recs if r["type"] == "BRAIN_SCORED"}
    for key, f in sorted(made.items()):
        row = by_day.get(key[0])
        if key in scored or row is None or row["y"][key[1]] is None:
            continue
        late = datetime.fromisoformat(f.get("written") or f["ts"]) >= next_session_opens(row["day"], cal)
        j.append("BRAIN_SCORED", asof=key[0], after=key[0], question=key[1], p=f["p"], base=f["base"],
                 y=row["y"][key[1]], late=late)       # late: written after its day began; never counted
    last = rows[-1]
    day = last["day"].isoformat()
    print(f"=== the market brain, after the close of {day} (written {datetime.now():%d %b %H:%M}) ===")
    print("It trades nothing. Forecasts are probabilities; the score below says whether they are worth anything.")
    x = last["x"]
    nifty = indexes[features.NIFTY][last["day"]]
    vix = indexes.get(features.VIX, {}).get(last["day"])
    print(f"\nthe market: NIFTY {nifty.close:,.2f} ({x['ret1']:+.2%} today, {x['ret5']:+.2%} in 5 sessions, "
          f"{x['ret20']:+.2%} in 20)" + (f"; India VIX {vix.close:.2f}" if vix else "")
          + (f"; FII long share {x['fii_long']:.0%}" if x.get("fii_long") is not None else "")
          + (f"; {x['breadth']:.0%} of sectors above their 20-day average" if x.get("breadth") is not None else ""))
    print(f"  a usual day's move lately: {last['usual_move']:.2%} either way")
    stale = now >= next_session_opens(last["day"], cal)
    if stale:
        print(f"\nNO FORECAST: the files on disk end on {day} and the session after it has already begun. A forecast "
              f"written now would not be a forecast. Run it again once the latest files are in (deploy/evening.sh).")
    else:
        print("\nfor the next session:")
    for q in features.TARGETS:
        if stale:
            break
        if (day, q) in made:
            f = made[(day, q)]
        else:
            train = [r for r in rows[:-1] if r["y"][q] is not None and may_train(r["day"])]
            m = model.fit(train, q)
            f = {"after": day, "question": q, "p": round(model.predict(m, last), 4), "base": round(m["base"], 4),
                 "trained_on": m["n"], "written": now.isoformat(timespec="seconds")}
            j.append("BRAIN_FORECAST", asof=day, **f)
        what = "NIFTY closes higher" if q == "up" else f"NIFTY moves more than {last['usual_move']:.2%} either way"
        print(f"  {what}: {f['p']:.0%} (it happens on {f['base']:.0%} of days anyway)")
    every = [r["payload"] for r in j.records() if r["type"] == "BRAIN_SCORED"]
    done = [d for d in every if not d.get("late")]
    if len(every) > len(done):
        print(f"\n({len(every) - len(done)} forecast(s) were written after their day had begun: kept on record, "
              f"never counted)")
    print("\nits live score so far (forecasts written before the day they are about):")
    if not done:
        print("  nothing scored yet: the first answer comes after the next session")
    for q in features.TARGETS:
        mine = [d for d in done if d["question"] == q]
        if mine:
            sc = model.score(mine) if len(mine) >= 2 else {}
            right = sum((d["p"] > 0.5) == bool(d["y"]) for d in mine)
            print(f"  {q.upper()}: {len(mine)} scored, right on {right}"
                  + (f"; skill {sc['skill']:+.1%} (z {sc['z']:+.2f}); proof needs z of {Z_NEEDED} or more"
                     if sc else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="the market brain: forecasts and keeps score; trades nothing")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backfill", help="its walk-forward record on the exploration years").set_defaults(fn=cmd_backfill)
    sub.add_parser("check", help="what history is on disk, by year (counts only)").set_defaults(fn=cmd_check)
    q = sub.add_parser("today", help="score yesterday, forecast the next session, print the brief")
    q.add_argument("--fetch", action="store_true", help="first fetch NSE's files for the last few weeks, where missing")
    q.set_defaults(fn=cmd_today)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
