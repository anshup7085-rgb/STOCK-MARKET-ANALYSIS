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
SMALL = REPO / "state" / "fno_small"
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
    out.append(money(recs, day))
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


def entry_margin(p: dict) -> tuple[float, bool]:
    """(margin, is it Zerodha's own figure). Zerodha's, where the entry recorded it; else the explorer's
    estimate, which for spreads is only their worst loss and far below what Zerodha blocks."""
    b = p.get("broker_margin") or {}
    if isinstance(b.get("final"), (int, float)) and b["final"] > 0:
        return float(b["final"]), True
    return float(p.get("margin_est") or 0), False


def peak_margin(recs: list[dict], day: str) -> float:
    """The most margin the day's open positions needed at one time, from the entries' own estimates."""
    events = []
    for r in recs:
        if r.get("asof") != day:
            continue
        p = r["payload"]
        if r["type"] == "XP_ENTRY":
            events.append((p["t"], 1, p["id"], entry_margin(p)[0]))
        elif r["type"] == "XP_EXIT":
            events.append((p["t"], 0, p["id"], 0.0))
    held, used, peak = {}, 0.0, 0.0
    for _, kind, pid, m in sorted(events):
        if kind == 1:
            held[pid] = m
            used += m
            peak = max(peak, used)
        else:
            used -= held.pop(pid, 0.0)
    return peak


def money(recs: list[dict], day: str) -> str:
    """The rupees behind the result: the paper capital, today's and the total net as a share of it, and
    the most margin the trades needed at once (Zerodha would block about that much)."""
    init = next((r for r in recs if r["type"] == "XP_INIT"), None)
    if init is None:
        return "money: no explorer book"
    cap = float(init["payload"]["capital"])
    nets = [r["payload"]["net"] for r in recs if r["type"] == "XP_EXIT"]
    today = sum(r["payload"]["net"] for r in recs if r["type"] == "XP_EXIT" and r.get("asof") == day)
    entries = [r["payload"] for r in recs if r["type"] == "XP_ENTRY" and r.get("asof") == day]
    real = sum(entry_margin(p)[1] for p in entries)
    basis = ("Zerodha's figures" if entries and real == len(entries) else
             "estimates, below Zerodha's for spreads" if not real else f"Zerodha's for {real} of {len(entries)}")
    return (f"money: paper capital Rs{cap:,.0f} | today Rs{today:+,.0f} ({today / cap:+.2%}) | since {init.get('asof')} "
            f"Rs{sum(nets):+,.0f} ({sum(nets) / cap:+.2%}) | most margin in use today ~Rs{peak_margin(recs, day):,.0f} "
            f"({basis})")


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
    out.append(f"  taking every trade would have needed ~Rs{peak_margin(today, day):,.0f} of margin at the busiest moment")
    return out


def small(day: str) -> list[str]:
    """The small book: the holder's real capital, one bought option at a time. Paper."""
    path = SMALL / "journal.jsonl"
    if not path.exists():
        return ["no small book yet (it starts with the next `morning`)"]
    recs = Journal(path, "fno-explore").records()
    today = [r for r in recs if r.get("asof") == day]
    out = []
    for r in today:
        p = r["payload"]
        if r["type"] == "XP_ENTRY":
            legs = " ".join(f"{'+' if g['side'] == 'BUY' else '-'}{g['lots']}x{g['symbol']}@{g['price']:g}"
                            for g in p["legs"])
            paid = sum(g["price"] * g["qty"] for g in p["legs"] if g["side"] == "BUY")
            out.append(f"  {_hm(p['t'])} BUY   {p['strategy']} ({p['direction']}): {legs}; paid Rs{paid:,.0f}")
        elif r["type"] == "XP_EXIT":
            out.append(f"  {_hm(p['t'])} CLOSE {p['id']} ({p['reason']}): net Rs{p['net']:+,.0f}")
        elif r["type"] == "XP_HALT":
            out.append(f"  STOPPED FOR THE DAY: the loss reached Rs{-p['pnl']:,.0f}")
    init = next((r for r in recs if r["type"] == "XP_INIT"), None)
    cap = float((init or {}).get("payload", {}).get("capital") or 20_000)
    nets = [r["payload"]["net"] for r in today if r["type"] == "XP_EXIT"]
    total = sum(r["payload"]["net"] for r in recs if r["type"] == "XP_EXIT")
    count = sum(1 for r in recs if r["type"] == "XP_EXIT")
    out.append(f"today: {len(nets)} trade(s), net Rs{sum(nets):+,.0f} ({sum(nets) / cap:+.1%} of Rs{cap:,.0f})")
    out.append(f"since {(init or {}).get('asof', '?')}: {count} trade(s), net Rs{total:+,.0f}; the Rs{cap:,.0f} "
               f"would now be Rs{cap + total:,.0f}")
    skips = sorted({f"{r['payload']['strategy']}: {r['payload']['detail'][:70]}" for r in today
                    if r["type"] == "XP_SKIP"})
    if skips and not nets:
        out.append("why nothing traded: " + " | ".join(skips[:3]))
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
    settled = [r["payload"]["net"] for r in recs if r["type"] == "FNO_SETTLED"]
    margins = [(r["payload"]["trade"].get("margin") or {}).get("final") for r in recs if r["type"] == "FNO_ENTRY"]
    margins = [m for m in margins if isinstance(m, (int, float))]
    eq = 500_000.0                                        # V's fixed paper equity (PREREGISTRATION_3)
    out.append(f"money: V's paper equity Rs{eq:,.0f}, at most Rs5,000 at risk a week | settled so far "
               f"Rs{sum(settled):+,.0f} ({sum(settled) / eq:+.2%}) over {len(settled)} week(s)"
               + (f" | Zerodha's margin for its last trade Rs{margins[-1]:,.0f}" if margins else ""))
    return out


def main() -> int:
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(IST).date().isoformat()
    date.fromisoformat(day)
    print(f"=== F&O paper report for {day} (written {datetime.now(IST):%d %b %H:%M} IST) — paper only ===")
    print("\n--- intraday explorer ---")
    print("\n".join(explorer(day)))
    print("\n--- what-if (every signal, 1 lot, no budget or position limits; not the test) ---")
    print("\n".join(whatif(day)))
    print("\n--- the small book: Rs20,000, bought options only (paper; the size of the real account) ---")
    print("\n".join(small(day)))
    print("\n--- market context (recorded for later tests; not used to trade) ---")
    print("\n".join(summary(read_day(REPO, day))))
    print("\n--- strategy V ---")
    print("\n".join(strategy_v(day)))
    saved = REPO / "logs" / f"options-save-{day}.log"
    if saved.exists():
        lines = [x for x in saved.read_text(errors="replace").splitlines() if x.strip()]
        print("\n--- option minutes saved for our own history ---")
        print("\n".join(lines[-4:]) if lines else "nothing logged yet")
    idx = REPO / "logs" / f"index-minutes-{day}.log"
    if idx.exists():
        lines = [x for x in idx.read_text(errors="replace").splitlines() if x.startswith("index minutes")
                 or "failed" in x]
        print("\n--- NIFTY and VIX minutes saved (counts only) ---")
        print("\n".join(lines[-4:]) if lines else "still running, or nothing logged yet")
    check = REPO / "logs" / f"kite-history-check-{day}.log"
    if check.exists():
        print("\n--- Kite historical data check (one time; counts and dates only) ---")
        print(check.read_text(errors="replace").strip()[:4000])
    print("\n--- journals ---")
    for name, d in (("explorer", EXPLORE), ("what-if", WHATIF), ("small book", SMALL), ("V", V)):
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
