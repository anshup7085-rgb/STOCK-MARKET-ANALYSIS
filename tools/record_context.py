#!/usr/bin/env python3
"""
Record the day's public market context (FII/DII positions, India VIX, put-call ratio, cash flows).

    PYTHONPATH=. python tools/record_context.py            # today and the last 4 trading days
    PYTHONPATH=. python tools/record_context.py --days 10

Data only: nothing trades on it. A source that is not published yet, or not fetchable, is
recorded as unavailable and retried on the next run. Public data; no login, no secrets.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from market.config import IST
from trading.fno.context import record_recent, summary

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--days", type=int, default=5)
    args = ap.parse_args()
    today = datetime.now(IST).date()
    for rec in record_recent(REPO, today, args.days):
        ok = sum(s["status"] == "ok" for s in rec["sources"].values())
        print(f"{rec['day']}: {ok}/{len(rec['sources'])} sources recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
