#!/usr/bin/env python3
"""
How wide were the real bid-ask spreads our paper trades met? A calibration for every replay.

    PYTHONPATH=. python tools/fill_quality.py

Every paper entry since 26 Oct 2026 stored Zerodha's bid and ask for each leg (the explorer's and
the what-if book's journals). This measures the half-spread, (ask - bid) / 2, as a share of the mid
and in points, by kind of contract and distance from the money, and at the open against later in the
day. The same leg seen by both books at the same minute counts once. It then says how often the real
half-spread was within the replay's assumption (0.2% of the price, at least one tick) and the harsh
one (1%). Read-only; prints no trade's price, only spreads.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

from trading.journal import Journal

REPO = Path(__file__).resolve().parents[1]
BOOKS = (REPO / "state" / "fno_explore" / "journal.jsonl", REPO / "state" / "fno_whatif" / "journal.jsonl")


def legs(paths=None) -> list[dict]:
    seen, out = set(), []
    for path in paths or BOOKS:
        if not path.exists():
            continue
        for r in Journal(path, "fno-explore").records():
            if r["type"] != "XP_ENTRY":
                continue
            p = r["payload"]
            spot = float(p.get("spot") or 0)
            for g in p.get("legs", []):
                q = g.get("quote") or {}
                bid, ask = q.get("bid"), q.get("ask")
                key = (g["symbol"], p["t"][:16])
                if key in seen or not bid or not ask or ask <= bid:
                    continue
                seen.add(key)
                mid = (bid + ask) / 2
                if g["kind"] == "FUT":
                    where = "future"
                else:
                    away = abs(g["strike"] - spot) / spot if spot else 0
                    where = "option, at the money" if away <= 0.0025 else (
                        "option, near (0.25-1%)" if away <= 0.01 else "option, far (over 1%)")
                out.append({"where": where, "half": (ask - bid) / 2, "rel": (ask - bid) / 2 / mid, "mid": mid,
                            "open": p["t"][11:16] < "09:36"})
    return out


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def main() -> int:
    rows = legs()
    if not rows:
        print("no paper entries with quotes yet")
        return 1
    print(f"real half-spreads met by {len(rows)} paper legs (both books, each leg once)")
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["where"]].append(r)
        groups[("at the open (to 09:35)" if r["open"] else "later in the day")].append(r)
    for name in ("option, at the money", "option, near (0.25-1%)", "option, far (over 1%)", "future",
                 "at the open (to 09:35)", "later in the day"):
        g = groups.get(name)
        if not g:
            continue
        rel = [r["rel"] for r in g]
        pts = [r["half"] for r in g]
        within = sum(r["half"] <= max(0.05, 0.002 * r["mid"]) + 1e-9 for r in g)
        harsh = sum(r["half"] <= max(0.05, 0.01 * r["mid"]) + 1e-9 for r in g)
        print(f"  {name:<24} {len(g):4d} legs: half-spread median {pct(rel, 0.5):.2%} ({pct(pts, 0.5):.2f} pts), "
              f"90% within {pct(rel, 0.9):.2%} ({pct(pts, 0.9):.2f} pts); within the replay's 0.2% on "
              f"{within / len(g):.0%}, within 1% on {harsh / len(g):.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
