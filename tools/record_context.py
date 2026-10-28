#!/usr/bin/env python3
"""
Record the day's public market context (FII/DII positions, India VIX, put-call ratio, cash flows).

    PYTHONPATH=. python tools/record_context.py            # the last 5 trading days, today included
    PYTHONPATH=. python tools/record_context.py --days 10

Data only: nothing trades on it. A file that is not published yet, or not fetchable, is
recorded as unavailable and retried on the next run. NSE serves the FII/DII cash flows for its
latest session only, so both `morning` and `evening` run this: the morning run catches the
previous session's flows. Public data; no login, no secrets.
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
    recs = record_recent(REPO, today, args.days)
    print("market context recorded: " + ", ".join(
        f"{r['day'][5:]} {sum(s['status'] == 'ok' for s in r['sources'].values())}/{len(r['sources'])}" for r in recs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
