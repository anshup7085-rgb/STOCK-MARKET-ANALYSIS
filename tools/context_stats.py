#!/usr/bin/env python3
"""
Where today's FII index-futures positioning sits in NSE's archive since 2012.

    PYTHONPATH=. python tools/context_stats.py

Reads the files deploy/backfill.sh saved (state/nse_archive/participant_oi) and the daily
recorder's days after them (state/market_context). For each day: the FII long share in index
futures, long / (long + short). Prints the latest day, its rank among all days, the extremes
with their dates, and the last ten days.

Descriptive only: it reads positions, never prices or returns, so no test is judged here.
Whether positioning predicts anything is a hypothesis to write down before any return is read.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

from trading.fno.context import Unavailable, parse_participant_oi

REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "state" / "nse_archive" / "participant_oi"
DAILY = REPO / "state" / "market_context"


def long_share(oi: dict) -> float | None:
    f = oi.get("FII", {})
    lo, sh = f.get("Future Index Long"), f.get("Future Index Short")
    return lo / (lo + sh) if lo is not None and sh is not None and lo + sh > 0 else None


def series() -> dict[date, float]:
    out: dict[date, float] = {}
    for p in sorted(ARCHIVE.glob("*/*.csv")):
        d = datetime.strptime(p.stem.rsplit("_", 1)[1], "%d%m%Y").date()
        try:
            v = long_share(parse_participant_oi(p.read_bytes(), d))
        except Unavailable:
            continue
        if v is not None:
            out[d] = v
    for p in sorted(DAILY.glob("*.json")):
        rec = json.loads(p.read_text())
        data = rec.get("sources", {}).get("participant_oi", {}).get("data")
        v = long_share(data) if data else None
        if v is not None:
            out.setdefault(date.fromisoformat(rec["day"]), v)
    return dict(sorted(out.items()))


def main() -> int:
    s = series()
    if not s:
        print("no participant files yet: run deploy/backfill.sh first")
        return 1
    days, vals = list(s), list(s.values())
    last_d, last_v = days[-1], vals[-1]
    below = sum(v < last_v for v in vals)
    lo_d = min(s, key=s.get)
    hi_d = max(s, key=s.get)
    print(f"FII long share in index futures, {len(s)} days, {days[0]} to {last_d}")
    print(f"  latest {last_d}: {last_v:.1%}; lower on {below} days ({below / len(s):.1%} of all days)")
    print(f"  lowest {s[lo_d]:.1%} on {lo_d}; highest {s[hi_d]:.1%} on {hi_d}; median {sorted(vals)[len(vals) // 2]:.1%}")
    for y in sorted({d.year for d in days}):
        ys = [v for d, v in s.items() if d.year == y]
        print(f"  {y}: median {sorted(ys)[len(ys) // 2]:.1%}, range {min(ys):.1%} to {max(ys):.1%} ({len(ys)} days)")
    print("  last ten days: " + ", ".join(f"{d:%d %b} {v:.0%}" for d, v in list(s.items())[-10:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
