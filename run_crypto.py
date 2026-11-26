#!/usr/bin/env python3
"""
The crypto head's command line (research/BLUEPRINT.md, phase 2). It trades nothing.

    PYTHONPATH=. .venv/bin/python run_crypto.py download    # daily and hourly candles since 2017, then new ones only
    PYTHONPATH=. .venv/bin/python run_crypto.py backfill    # the crypto brain's record, 2020 to Oct 2024
    PYTHONPATH=. .venv/bin/python run_crypto.py today       # score yesterday, forecast today (UTC)
    PYTHONPATH=. .venv/bin/python run_crypto.py live        # the minute recorder, forever (deploy/crypto.sh starts it)
    PYTHONPATH=. .venv/bin/python run_crypto.py paper       # the crypto paper book (ma50, mom30 on BTC and ETH)

Prices come from Binance's free public data. The live recorder also tops up the history every hour and,
just after midnight UTC (05:30 IST), runs `today` and rebuilds the dashboard, so the crypto head runs by
itself around the clock.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time as _time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from brain import model
from crypto import binance, store
from crypto import brain as cb

REPO = Path(__file__).resolve().parent
OUT = REPO / "state" / "crypto" / "brain"
LIVE = REPO / "state" / "crypto" / "live"
START = int(datetime(2017, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
NOW = lambda: datetime.now(timezone.utc)                         # noqa: E731 - replaced in tests
LATE_AFTER = timedelta(minutes=30)


def may_train(d: date) -> bool:
    return d <= store.EXPLORE_TO or d > store.HOLDOUT_TO


def cmd_download(args=None, get=binance.get_json, sleep=_time.sleep, log=print) -> int:
    for sym in binance.SYMBOLS:
        for interval in ("1d", "1h"):
            last = store.last_t(sym, interval)
            start = START if last is None else last + binance.STEP_MS[interval]
            try:
                got = binance.klines(sym, interval, start, int(NOW().timestamp() * 1000), get=get, sleep=sleep)
            except Exception as exc:                            # noqa: BLE001 - one symbol must not stop the rest
                log(f"  {sym} {interval}: failed ({type(exc).__name__}: {str(exc)[:80]}); tried again next time")
                continue
            new = store.save(sym, interval, got) if got else 0
            if new or getattr(args, "verbose", True):
                log(f"  {sym} {interval}: {new} new candles")
    return 0


def line(target: str, preds: list[dict]) -> list[str]:
    s = model.score(preds)
    if s.get("n", 0) < 2:
        return [f"  {target.upper()}: too few forecasts"]
    by_year: dict[str, list[dict]] = defaultdict(list)
    for q in preds:
        by_year[q["day"][:4]].append(q)
    skills = {y: model.score(v).get("skill", 0.0) for y, v in sorted(by_year.items()) if len(v) >= 50}
    good = sum(v > 0 for v in skills.values())
    shows = s["skill"] > 0 and s["z"] >= cb.Z_NEEDED and good >= cb.YEARS_NEEDED
    return [f"  {target.upper()}: {s['n']} forecasts, {preds[0]['day']} to {preds[-1]['day']}; yes on {s['yes_rate']:.1%} "
            f"of days; right on {s['right']:.1%}; skill {s['skill']:+.2%} (z {s['z']:+.2f}); above 0 in {good} of "
            f"{len(skills)} years",
            "      skill by year: " + "; ".join(f"{y} {v:+.1%}" for y, v in skills.items()),
            f"      by the rule fixed in advance: {'SHOWS SKILL in exploration' if shows else 'no skill shown'}"]


def rows_now() -> list[dict]:
    return cb.build(store.load("BTCUSDT", "1d"), store.load("ETHUSDT", "1d"))


def cmd_backfill(args=None) -> int:
    rows = [r for r in rows_now() if r["day"] <= store.EXPLORE_TO]
    if len(rows) < model.MIN_TRAIN + 200:
        print("too little history: run `run_crypto.py download` first")
        return 2
    print(f"EXPLORATION, NOT EVIDENCE: the crypto brain's walk-forward record (BTC), {cb.RECORD_FROM} to "
          f"{rows[-1]['day']}, {len(cb.FEATURES)} inputs; the reading was fixed before this ran")
    saved = {}
    for q in cb.TARGETS:
        preds = model.walk_forward(rows, q, first=cb.RECORD_FROM, last=store.EXPLORE_TO, names=cb.FEATURES)
        saved[q] = preds
        print("\n".join(line(q, preds)))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "backfill.json").write_text(json.dumps({"written": NOW().isoformat(timespec="seconds"),
                                                   "forecasts": saved}, indent=1))
    return 0


def cmd_today(args=None) -> int:
    from trading.journal import Journal
    rows = rows_now()
    if len(rows) < model.MIN_TRAIN + 50:
        print("too little history: run `run_crypto.py download` first")
        return 2
    now = NOW()
    OUT.mkdir(parents=True, exist_ok=True)
    j = Journal(OUT / "journal.jsonl", "crypto-brain")
    recs = j.records()
    by_day = {r["day"].isoformat(): r for r in rows}
    made = {(r["payload"]["after"], r["payload"]["question"]): r["payload"] for r in recs if r["type"] == "CRYPTO_FORECAST"}
    scored = {(r["payload"]["after"], r["payload"]["question"]) for r in recs if r["type"] == "CRYPTO_SCORED"}
    for key, f in sorted(made.items()):
        row = by_day.get(key[0])
        if key in scored or row is None or row["y"][key[1]] is None:
            continue
        day_begins = datetime.combine(date.fromisoformat(key[0]) + timedelta(days=1), datetime.min.time(), timezone.utc)
        late = datetime.fromisoformat(f["written"]) > day_begins + LATE_AFTER
        j.append("CRYPTO_SCORED", asof=key[0], after=key[0], question=key[1], p=f["p"], base=f["base"],
                 y=row["y"][key[1]], late=late)
    last = rows[-1]
    day = last["day"].isoformat()
    begins = datetime.combine(last["day"] + timedelta(days=1), datetime.min.time(), timezone.utc)
    print(f"=== the crypto brain (BTC), after the UTC day {day} (written {now:%d %b %H:%M} UTC) ===")
    x = last["x"]
    print(f"BTC ${store.load('BTCUSDT', '1d')[-1].close:,.0f}: {x['ret1']:+.2%} that day, {x['ret7']:+.2%} in 7, "
          f"{x['ret30']:+.2%} in 30; a usual day's move lately {last['usual_move']:.2%} either way")
    if now > begins + LATE_AFTER:
        print(f"NO FORECAST: the day after {day} began at 00:00 UTC; a forecast written now would be late. "
              "(The live recorder writes it at 00:07 UTC by itself.)")
    else:
        for q in cb.TARGETS:
            if (day, q) in made:
                f = made[(day, q)]
            else:
                train = [r for r in rows[:-1] if r["y"][q] is not None and may_train(r["day"])]
                m = model.fit(train, q, cb.FEATURES)
                f = {"after": day, "question": q, "p": round(model.predict(m, last), 4), "base": round(m["base"], 4),
                     "trained_on": m["n"], "written": now.isoformat(timespec="seconds")}
                j.append("CRYPTO_FORECAST", asof=day, **f)
            what = "BTC closes higher" if q == "up" else f"BTC moves more than {last['usual_move']:.2%} either way"
            print(f"  {what}: {f['p']:.0%} (it happens on {f['base']:.0%} of days anyway)")
    done = [r["payload"] for r in j.records() if r["type"] == "CRYPTO_SCORED" and not r["payload"].get("late")]
    for q in cb.TARGETS:
        mine = [d for d in done if d["question"] == q]
        if mine:
            right = sum((d["p"] > 0.5) == bool(d["y"]) for d in mine)
            print(f"  live score {q.upper()}: {len(mine)} scored, right on {right}")
    return 0


def record_minutes(get=binance.get_json, sleep=_time.sleep) -> int:
    """The closed one-minute candles since the last recorded, per symbol, into state/crypto/live/SYMBOL/DAY.csv."""
    n = 0
    now_ms = int(NOW().timestamp() * 1000)
    for sym in binance.SYMBOLS:
        folder = LIVE / sym
        folder.mkdir(parents=True, exist_ok=True)
        marker = folder / "last_t"
        last = int(marker.read_text()) if marker.exists() else now_ms - 10 * 60_000
        got = binance.klines(sym, "1m", last + 60_000, now_ms, get=get, sleep=sleep)
        for c in got:
            path = folder / f"{store.utc_day(c).isoformat()}.csv"
            new = not path.exists()
            with path.open("a") as fh:
                if new:
                    fh.write(",".join(store.COLS) + "\n")
                fh.write(f"{c.t},{c.open},{c.high},{c.low},{c.close},{c.volume},{c.quote},{c.trades}\n")
        if got:
            marker.write_text(str(got[-1].t))
            n += len(got)
    return n


def cmd_live(args=None) -> int:
    import subprocess
    print(f"{NOW():%Y-%m-%d %H:%M} UTC: the crypto recorder starts ({', '.join(binance.SYMBOLS)})", flush=True)
    last_hour = last_day = None
    while True:
        now = NOW()
        try:
            record_minutes()
        except Exception as exc:                                # noqa: BLE001 - the recorder must keep going
            print(f"{now:%H:%M} minutes failed: {type(exc).__name__}: {str(exc)[:100]}", flush=True)
        if now.hour != last_hour and now.minute >= 2:
            last_hour = now.hour
            cmd_download(argparse.Namespace(verbose=False), log=lambda m: print(f"{now:%H:%M}{m}", flush=True))
        if now.date() != last_day and now.hour == 0 and 6 <= now.minute < 25:
            last_day = now.date()
            for cmd in (["run_crypto.py", "today"], ["run_crypto.py", "paper"], ["dashboard/build.py"]):
                subprocess.run([sys.executable, *cmd], cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO)}, timeout=600,
                               stdout=sys.stdout, stderr=sys.stdout)
        _time.sleep(60 - NOW().second + 5)


def cmd_paper(args=None) -> int:
    from crypto import paper
    for line in paper.run(NOW()):
        print(line)
    for r in paper.summary():
        print(f"  {r['rule']:<6} {r['coin']}: {'holding' if r['in'] else 'in cash'}; worth Rs{r['free']:,.0f} with no tax, "
              f"Rs{r['india']:,.0f} after India's tax (from Rs{paper.CAPITAL:,.0f} on {r['since']})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="the crypto head: prices, the crypto brain; trades nothing")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, h in (("download", cmd_download, "daily and hourly candles, new ones only"),
                        ("backfill", cmd_backfill, "the crypto brain's record, 2020 to Oct 2024"),
                        ("today", cmd_today, "score yesterday, forecast today (UTC)"),
                        ("live", cmd_live, "the minute recorder, forever"),
                        ("paper", cmd_paper, "the crypto paper book: today's decisions and values")):
        sub.add_parser(name, help=h).set_defaults(fn=fn)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
