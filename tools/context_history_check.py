#!/usr/bin/env python3
"""
How far back NSE's archive keeps the daily market-context files the recorder reads.

    PYTHONPATH=. python tools/context_history_check.py            # every 6 months back to 2012
    PYTHONPATH=. python tools/context_history_check.py --since 2016

For one weekday near the 20th of every sixth month it checks four public files:
participant-wise open interest, participant-wise volume (FII, DII, pro and client
positions), the all-index close file (which carries India VIX), and the FII derivatives
statistics. It prints only whether each file exists and reads correctly, never a figure
from it. Public data; no login; writes nothing.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from trading.fno.context import Unavailable, fetch_url, parse_index_close, parse_participant_oi

HOSTS = ("https://nsearchives.nseindia.com", "https://archives.nseindia.com")
PATHS = {
    "participant OI": "/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv",
    "participant vol": "/content/nsccl/fao_participant_vol_{d:%d%m%Y}.csv",
    "index close": "/content/indices/ind_close_all_{d:%d%m%Y}.csv",
    "FII stats": "/content/fo/fii_stats_{d:%d}-{d:%b}-{d:%Y}.xls",
}


def get(name: str, d: date) -> bytes:
    last = None
    for host in HOSTS:
        try:
            return fetch_url(host + PATHS[name].format(d=d))
        except Unavailable as exc:
            last = exc
    raise last


def check(name: str, d: date) -> str:
    try:
        raw = get(name, d)
    except Unavailable as exc:
        return f"no ({str(exc)[:40]})"
    try:
        if name.startswith("participant"):
            parse_participant_oi(raw, d)
            return "ok"
        if name == "index close":
            return "ok, VIX in it" if "vix" in parse_index_close(raw, d) else "ok, no VIX"
        return "ok" if raw[:4] == b"\xd0\xcf\x11\xe0" or len(raw) > 2000 else f"odd ({len(raw)} bytes)"
    except Unavailable as exc:
        return f"unreadable ({str(exc)[:50]})"


def probe_dates(today: date, since: int) -> list[date]:
    out, d = [], today.replace(day=20)
    while d.year >= since:
        out.append(d)
        for _ in range(6):
            d = (d.replace(day=1) - timedelta(days=1)).replace(day=20)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="How far back NSE keeps the market-context files")
    ap.add_argument("--since", type=int, default=2012)
    args = ap.parse_args()
    print("date        " + " | ".join(PATHS))
    for target in probe_dates(date.today(), args.since):
        found = None
        for back in range(6):                                   # a holiday or weekend: try the days before
            d = target - timedelta(days=back)
            if d.weekday() >= 5:
                continue
            first = check("participant OI", d)
            if not first.startswith("no"):
                found = (d, first)
                break
        if found is None:
            print(f"{target}  participant OI not found on any of the 6 days before")
            continue
        d, first = found
        rest = [check(n, d) for n in list(PATHS)[1:]]
        print(f"{d}  " + " | ".join([first] + rest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
