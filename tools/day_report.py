#!/usr/bin/env python3
"""
One day of F&O paper running on one screen, to paste into the chat each evening.

    PYTHONPATH=. python tools/day_report.py [YYYY-MM-DD]      # default: today (IST)

From the two journals, the explorer's tick file and the day's logs: whether the
runners were up all day, every paper entry and exit with its net P&L and R,
the first reason each strategy did not trade, V's slot decisions, the running
scoreboard, journal integrity, and any errors. Nothing secret is in any of it.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from market.config import IST
from trading.fno.context import read_day, summary
from trading.fno.explore import Ticks, format_scoreboard, scoreboard
from trading.journal import Journal, verify_file

REPO = Path(os.environ.get("FNO_REPORT_ROOT") or Path(__file__).resolve().parents[1])   # the override is for tests
EXPLORE, V = REPO / "state" / "fno_explore", REPO / "state" / "paper_fno"
WHATIF = REPO / "state" / "fno_whatif"
ON_TIME = "09:10"                                    # the agreed latest start
ERROR_WORDS = ("Traceback", "Error", "[refused]", "failed", "pass failed")


def _hm(ts: str) -> str:
    return ts[11:16] if ts and len(ts) >= 16 else "?"


def explorer(day: str) -> list[str]:
    path = EXPLORE / "journal.jsonl"
    if not path.exists():
        return ["no explorer book yet"]
    recs = Journal(path, "fno-explore").records()
    out = []
    ticks = Ticks(EXPLORE).read(day)
    if ticks:
        out.append(f"runner: {len(ticks)} index readings, {_hm(ticks[0]['t'])} to {_hm(ticks[-1]['t'])}")
        if _hm(ticks[0]["t"]) > ON_TIME:
            out.append(f"  LATE START: first reading {_hm(ticks[0]['t'])}, the target is {ON_TIME}")
    else:
        out.append("runner: no index readings today (not started, or no market data)")
    today = [r for r in recs if r.get("asof") == day]
    for r in today:
        p = r["payload"]
        if r["type"] == "XP_ENTRY":
            legs = " ".join(f"{'+' if g['side'] == 'BUY' else '-'}{g['lots']}x{g['symbol']}@{g['price']:g}"
                            for g in p["legs"])
            out.append(f"  {_hm(p['t'])} OPEN  {p['strategy']} ({p['direction']}): {legs}; risk Rs{p['risk']:,.0f}")
        elif r["type"] == "XP_EXIT":
            out.append(f"  {_hm(p['t'])} CLOSE {p['id']} ({p['reason']}): net Rs{p['net']:+,.0f} = {p['R']:+.2f}R"
                       + (f"  [{'; '.join(p['flags'])}]" if p.get("flags") else ""))
        elif r["type"] == "XP_HALT":
            out.append(f"  HALT: day loss Rs{-p['pnl']:,.0f} reached the limit")
    nets = [r["payload"]["net"] for r in today if r["type"] == "XP_EXIT"]
    out.append(f"today: {sum(r['type'] == 'XP_ENTRY' for r in today)} opened, {len(nets)} closed, "
               f"net Rs{sum(nets):+,.0f}")
    skips = defaultdict(list)
    for r in today:
        if r["type"] == "XP_SKIP":
            skips[r["payload"]["strategy"]].append(f"{r['payload']['code']}: {r['payload']['detail'][:80]}")
    if skips:
        out.append("why strategies did not trade (first time each reason came up today):")
        for name, why in sorted(skips.items()):
            out.append(f"  {name}: " + " | ".join(why[:3]) + (f" (+{len(why) - 3} more)" if len(why) > 3 else ""))
    out.append("")
    out.append(format_scoreboard(scoreboard(recs)))
    return out


def whatif(day: str) -> list[str]:
    """The what-if book: every signal at 1 unit, no limits. Not the test; it answers "what if"."""
    path = WHATIF / "journal.jsonl"
    if not path.exists():
        return ["no what-if book yet"]
    today = [r for r in Journal(path, "fno-explore").records() if r.get("asof") == day]
    out, nets = [], defaultdict(float)
    for r in today:
        p = r["payload"]
        if r["type"] == "XP_EXIT":
            name = p["id"].rsplit("-", 4)[0]
            nets[name] += p["net"]
            out.append(f"  {_hm(p['t'])} {name} ({p['reason']}): net Rs{p['net']:+,.0f}")
    opened = sum(r["type"] == "XP_ENTRY" for r in today)
    out.append(f"if every signal had been traded at 1 lot: {opened} trades, net Rs{sum(nets.values()):+,.0f}"
               + ("  (" + ", ".join(f"{k} {v:+,.0f}" for k, v in sorted(nets.items())) + ")" if nets else ""))
    return out


def strategy_v(day: str) -> list[str]:
    path = V / "journal.jsonl"
    if not path.exists():
        return ["no V book yet"]
    recs = Journal(path, "fno-paper").records()
    out = []
    today = [r for r in recs if r.get("asof") == day]
    passes = [r["payload"] for r in today if r["type"] == "FNO_PASS" and r["payload"].get("slot")]
    if passes:
        counts = defaultdict(int)
        for p in passes:
            counts[p.get("outcome")] += 1
        out.append(f"slots decided today: {dict(counts)}")
        for p in passes:
            if p.get("outcome") != "no_trade" or p is passes[-1]:
                out.append(f"  {_hm(p['slot'])} {p.get('outcome')}: {p.get('code', '')} {str(p.get('detail', ''))[:90]}")
    else:
        out.append("slots decided today: none (not an entry day for the counted cycle, or started after the slots)")
    for r in today:
        p = r["payload"]
        if r["type"] == "FNO_ENTRY":
            t = p["trade"]
            out.append(f"  OPEN {t['id']}: {t['lots']} lot(s), credit {t['credit']:.2f} pts, max loss Rs{t['max_loss']:,.0f}")
        elif r["type"] == "FNO_SETTLED":
            out.append(f"  SETTLED {p['trade_id']}: net Rs{p['net']:+,.0f} = {p['R']:+.2f}R")
        elif r["type"] == "FNO_CYCLE":
            out.append(f"  cycle {p['expiry']}: {p['outcome']}")
        elif r["type"] in ("FNO_PASS_ERROR", "FNO_SETTLEMENT_PENDING"):
            out.append(f"  {r['type']}: {str(p.get('error') or p.get('why'))[:120]}")
    return out


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(IST).date().isoformat()
    date.fromisoformat(day)
    print(f"=== F&O paper report for {day} (written {datetime.now(IST):%d %b %H:%M} IST) — paper only ===")
    print("\n--- intraday explorer ---")
    print("\n".join(explorer(day)))
    print("\n--- what-if (every signal, 1 lot, no budget or position limits; not the test) ---")
    print("\n".join(whatif(day)))
    print("\n--- market context (recorded for later tests; not used to trade) ---")
    print("\n".join(summary(read_day(REPO, day))))
    print("\n--- strategy V ---")
    print("\n".join(strategy_v(day)))
    check = REPO / "logs" / f"kite-history-check-{day}.log"
    if check.exists():
        print("\n--- Kite historical data check (one time; counts and dates only) ---")
        print(check.read_text(errors="replace").strip()[:4000])
    print("\n--- journals ---")
    for name, d in (("explorer", EXPLORE), ("what-if", WHATIF), ("V", V)):
        p = d / "journal.jsonl"
        if p.exists():
            vr = verify_file(p)
            print(f"{name}: {'verifies' if vr.ok else 'DOES NOT VERIFY'} ({vr.records} records)"
                  + (f" {vr.problems[:2]}" if vr.problems else ""))
    errors = []
    for log in sorted((REPO / "logs").glob(f"fno-*-{day}.log")):
        for line in log.read_text(errors="replace").splitlines():
            if any(w in line for w in ERROR_WORDS):
                errors.append(f"{log.name}: {line.strip()[:150]}")
    print("\n--- errors in today's logs ---")
    print("\n".join(errors[-8:]) if errors else "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
