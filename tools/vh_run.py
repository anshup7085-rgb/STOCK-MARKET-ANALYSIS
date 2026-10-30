#!/usr/bin/env python3
"""
Run V-H (research/PREREGISTRATION_4.md) on the downloaded files: both implementations, the
verdict, and section 7's description. Refuses to run unless the registration is the bound one.

    PYTHONPATH=. python tools/vh_run.py          # after tools/vh_download.py has finished

Writes state/vh/result.json and prints a summary. The verdict is the registered one: PASS only if
implementation A's betting wealth reaches 200 and implementation B agrees on every trade (within
Rs1 of net P&L) and on the outcome; any disagreement records FAIL with its cause.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from market.config import IST
from research import vh_check
from trading.fno import vh

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "state" / "vh" / "result.json"


def compare(a: list[vh.Cycle], b: list[dict]) -> list[str]:
    problems = []
    bmap = {r["expiry"]: r for r in b}
    amap = {c.expiry: c for c in a}
    for e in sorted(set(amap) | set(bmap)):
        ca, cb = amap.get(e), bmap.get(e)
        if ca is None or cb is None:
            problems.append(f"{e}: only in {'A' if cb is None else 'B'}")
            continue
        if ca.outcome != cb["outcome"]:
            problems.append(f"{e}: A {ca.outcome}, B {cb['outcome']}")
            continue
        if ca.outcome == "trade":
            if ca.entry != cb["entry"]:
                problems.append(f"{e}: entry A {ca.entry}, B {cb['entry']}")
            elif abs(ca.net - cb["net"]) > 1.0:
                problems.append(f"{e}: net A {ca.net}, B {cb['net']}")
            elif abs(ca.trade["max_loss"] - cb["M"]) > 1.0:
                problems.append(f"{e}: maximum loss A {ca.trade['max_loss']}, B {cb['M']}")
    return problems


def main() -> int:
    vh.check_registration()
    if not (vh.RAW.parent / "download.complete").exists():
        print("the download has not finished: run tools/vh_download.py until it says finished")
        return 2
    sessions = vh.load_sessions(vh.RAW)
    if not sessions:
        print("no files: run tools/vh_download.py first")
        return 2
    expiries = sessions.expiries()
    a = vh.run(sessions, vh.penalty_registered, vh.INDEX_CLOSE, expiries)
    b = vh_check.compute(vh.RAW, vh.INDEX_CLOSE, vh.penalty_registered)
    problems = compare(a, b)
    va, vb = vh.verdict(a), vh_check.outcome(b)
    if problems or va["outcome"] != vb:
        final = {"outcome": "FAIL", "why": f"the implementations disagree ({len(problems)} cycles; "
                                           f"A {va['outcome']}, B {vb}): the failing outcome is recorded"}
    else:
        final = va
    result = {
        "registration_sha256": vh.REGISTRATION_SHA256, "run_at": datetime.now(IST).isoformat(timespec="seconds"),
        "sessions": len(sessions), "first_session": sessions.days[0].isoformat(),
        "last_session": sessions.days[-1].isoformat(),
        "verdict": final, "implementation_A": va, "implementation_B": vb, "disagreements": problems[:50],
        "described": vh.describe(a),
        "described_no_penalty": vh.describe(vh.run(sessions, vh.penalty_none, vh.INDEX_CLOSE, expiries)),
        "described_harsh_penalty": vh.describe(vh.run(sessions, vh.penalty_harsh, vh.INDEX_CLOSE, expiries)),
        "trades": [{"expiry": c.expiry.isoformat(), "entry": c.entry.isoformat(), "credit": c.trade["credit"],
                    "lots": c.trade["lots"], "M": c.trade["max_loss"], "settle": c.settlement["value"],
                    "net": c.net, "R": round(c.R, 4)} for c in a if c.outcome == "trade"],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1, default=str))
    d = result["described"]
    print(f"V-H, {result['sessions']} sessions {result['first_session']} to {result['last_session']}")
    print(f"VERDICT: {final['outcome']} — {final['why']}")
    print(f"  implementations: A {va['outcome']}, B {vb}, disagreements {len(problems)}")
    print(f"  cycles {d['cycles']}: trades {d['items']}, no trade {d['no_trade']}, left out {d['left_out']}")
    al = d["all"]
    print(f"  win rate {al['win_rate']}, mean R {al['mean_R']}, net Rs{al['net_total']:,.0f}, "
          f"max drawdown Rs{d['max_drawdown']:,.0f}, costs {d['costs_share_of_credit']} of credit")
    print(f"  wealth: final {d['wealth_final']}, peak {d['wealth_peak']}; V's harm stop would have hit at item "
          f"{d['harm_stop_item']}; futility at 60: {d['futility_at_60']}")
    for name, blk in d["periods"].items():
        print(f"  {name}: {blk['items']} trades, win {blk['win_rate']}, mean R {blk['mean_R']}, net Rs{blk['net_total']:,.0f}")
    for label in ("described_no_penalty", "described_harsh_penalty"):
        x = result[label]["all"]
        print(f"  {label.split('_', 1)[1].replace('_', ' ')}: {x['items']} trades, mean R {x['mean_R']}, net Rs{x['net_total']:,.0f}")
    print(f"  no-trade reasons: {d['no_trade_reasons']}")
    print(f"saved: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
