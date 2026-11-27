#!/usr/bin/env python3
"""
Download Dhan's expired weekly options for the other indexes, minute by minute, while the Data API
plan is paid (it ends on 4 Dec 2026): SENSEX, BANKNIFTY, FINNIFTY, MIDCPNIFTY.

    cd ~/STOCK-MARKET-ANALYSIS && git pull -q && PYTHONPATH=. .venv/bin/python tools/dhan_download_more.py

Why: the one effect the explorations found (selling the straddle on expiry day) was found on NIFTY.
From 2021 to 2024 these indexes had weekly expiries on other weekdays, so the same rule can be tried
on several times as many expiry days, on indexes it was not found on; SENSEX still has a weekly expiry.

It asks for the Dhan access token (hidden; never saved, printed or put in the environment), then
checks each index with one request for 15 Oct 2024 before downloading anything: the reply must hold
that day, its index level must sit in the range that index really traded in, and it must not be
NIFTY's own level. An index that fails the check is skipped and named. Then it carries on in the
background; progress: `tail -5 logs/dhan-download-more.log`. Running it again resumes. It stops by
itself before Dhan's 100,000 requests a day; run it again the next day with a new token.

What: nearest weekly expiry (expiryCode 1), ATM-10 to ATM+10, calls and puts, one weekday a request,
1 Oct 2021 to yesterday. The at-the-money call is asked first; a day it comes back empty for (a
holiday, or a month with no weekly contract) is not asked for again in the other 41 series. Replies
are kept as sent, compressed, under state/dhan/rolling_more/INDEX/, each with its SHA-256 in that
folder's manifest.jsonl, laid out as trading/fno/dhan_data.py reads NIFTY's (pass root=). The same
held-back years apply. Prints counts, never a price.
"""

from __future__ import annotations

import getpass
import gzip
import importlib.util
import json
import shutil
import sys
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

from market.config import IST

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("dhan_download", Path(__file__).with_name("dhan_download.py"))
dl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dl)

ROOT = REPO / "state" / "dhan" / "rolling_more"
LOG = REPO / "logs" / "dhan-download-more.log"
START = date(2021, 10, 1)
PROBE_DAY = date(2024, 10, 15)            # a Tuesday on which all four had a weekly contract listed
DAILY_CAP = 90_000                        # Dhan allows 100,000 data requests a day
OFFSETS = [0] + [o for k in range(1, 11) for o in (k, -k)]         # ATM first, then outwards
# security id, segment, and the range the index traded in between Oct 2021 and Nov 2026 (a sanity check)
UNDERLYINGS = {
    "SENSEX":     {"id": 51,  "segment": "BSE_FNO", "low": 48_000, "high": 110_000},
    "BANKNIFTY":  {"id": 25,  "segment": "NSE_FNO", "low": 30_000, "high": 75_000},
    "FINNIFTY":   {"id": 27,  "segment": "NSE_FNO", "low": 14_000, "high": 36_000},
    "MIDCPNIFTY": {"id": 442, "segment": "NSE_FNO", "low": 5_500,  "high": 19_000},
}


def body(u: dict, offset: int, right: str, a: date, b: date) -> dict:
    return {"exchangeSegment": u["segment"], "interval": "1", "securityId": u["id"], "instrument": "OPTIDX",
            "expiryFlag": "WEEK", "expiryCode": 1, "strike": dl.strike_name(offset), "drvOptionType": right,
            "requiredData": dl.FIELDS, "fromDate": a.isoformat(), "toDate": b.isoformat()}


def spots_on(reply: dict | None, right: str, day: date) -> list[float]:
    s = ((reply or {}).get("data") or {}).get("ce" if right == "CALL" else "pe") or {}
    ts, sp = s.get("timestamp") or [], s.get("spot") or []
    return [float(v) for t, v in zip(ts, sp)
            if v and datetime.fromtimestamp(int(t), IST).date() == day]


def probe(name: str, token: str, nifty_level: float | None, fetch=dl.request, sleep=_time.sleep) -> str:
    """'' if Dhan's reply for PROBE_DAY is this index, else why not."""
    u = UNDERLYINGS[name]
    try:
        reply = fetch(token, body(u, 0, "CALL", PROBE_DAY, PROBE_DAY + timedelta(days=1)), sleep)
    except RuntimeError as exc:
        return f"the request failed ({exc})"
    levels = spots_on(reply, "CALL", PROBE_DAY)
    if len(levels) < 300:
        return f"only {len(levels)} minutes came back for {PROBE_DAY}"
    mid = sorted(levels)[len(levels) // 2]
    if not (u["low"] <= mid <= u["high"]):
        return f"its index level is outside {u['low']:,} to {u['high']:,}: not {name}"
    if nifty_level and abs(mid / nifty_level - 1) < 0.005:
        return "its index level is NIFTY's own"
    return ""


def empty_day(path: Path, day: date) -> bool:
    try:
        reply = json.loads(gzip.decompress(path.read_bytes()))
    except Exception:                                           # noqa: BLE001 - unreadable: ask again
        return False
    return day.isoformat() not in dl.days_in(reply, "CALL")


def run(token: str, name: str, end: date, root: Path = ROOT, fetch=dl.request, sleep=_time.sleep, log=print,
        budget: int = DAILY_CAP) -> dict:
    """One index. Returns its counts; "stopped" is True if the request budget ran out first."""
    u, base = UNDERLYINGS[name], root / name
    base.mkdir(parents=True, exist_ok=True)
    stats = {"requests": 0, "saved": 0, "skipped": 0, "empty_days": 0, "failed": 0, "stopped": False}
    with (base / "manifest.jsonl").open("a") as manifest:
        for a, b in dl.windows(START, end, 1):
            first = base / "code1" / f"{dl.strike_name(0)}_CALL" / f"{a.isoformat()}.json.gz"
            no_contract = first.exists() and empty_day(first, a)       # asked before: nothing listed that day
            for offset in OFFSETS:
                for right in ("CALL", "PUT"):
                    path = base / "code1" / f"{dl.strike_name(offset)}_{right}" / f"{a.isoformat()}.json.gz"
                    if path.exists():
                        stats["skipped"] += 1
                        continue
                    if no_contract:
                        continue
                    if stats["requests"] >= budget:
                        stats["stopped"] = True
                        return stats
                    stats["requests"] += 1
                    try:
                        reply = fetch(token, body(u, offset, right, a, b), sleep)
                    except dl.TokenExpired:
                        raise
                    except RuntimeError as exc:
                        stats["failed"] += 1
                        log(f"  {name} {path.parent.name} {a}: failed ({exc}); tried again on the next run")
                        continue
                    dl.save(path, reply, manifest, {"underlying": name, "expiry_code": 1,
                                                    "strike": dl.strike_name(offset), "right": right,
                                                    "from": a.isoformat(), "to": b.isoformat()}, base)
                    stats["saved"] += 1
                    if path == first and a.isoformat() not in dl.days_in(reply, "CALL"):
                        stats["empty_days"] += 1
                        no_contract = True
                    if stats["requests"] % 500 == 0:
                        log(f"  {datetime.now():%H:%M} {name}: {stats['requests']} requests, {stats['saved']} saved, "
                            f"{stats['empty_days']} days with no weekly contract, {stats['failed']} failed; now {a}")
    return stats


def nifty_level_on(day: date) -> float | None:
    try:
        from trading.fno import dhan_data as dd
        bars = dd.load_day(day, offsets=(0,))
        levels = sorted(b.spot for m in bars.values() for b in m.values() if b.spot)
        return levels[len(levels) // 2] if levels else None
    except Exception:                                           # noqa: BLE001 - the check is then skipped
        return None


def main() -> int:
    end = date.today() - timedelta(days=1)
    free_gb = shutil.disk_usage(REPO).free / 1e9
    if free_gb < 8:
        print(f"only {free_gb:.1f} GB free on this disk; the download needs room (about 1 GB an index). Stopping.")
        return 2
    token = getpass.getpass("Dhan access token (hidden; paste once, then Enter): ").strip()
    if not token:
        print("nothing entered; stopping")
        return 2
    nifty = nifty_level_on(PROBE_DAY)
    good = []
    for name in UNDERLYINGS:
        try:
            why = probe(name, token, nifty)
        except dl.TokenExpired as exc:
            print(f"Dhan refused the token ({exc}). Generate a new one on web.dhan.co and run this again.")
            return 2
        print(f"  {name}: {'ok' if not why else 'SKIPPED: ' + why}")
        if not why:
            good.append(name)
    if not good:
        print("no index passed the check: nothing downloaded. Send this to Claude.")
        return 2
    print(f"{free_gb:.0f} GB free. Downloading {', '.join(good)} in the background, at most {DAILY_CAP:,} requests "
          f"today (about 11 hours); run it again tomorrow to finish.")
    print("You can close this window. Progress:  tail -5 logs/dhan-download-more.log")
    sys.stdout.flush()
    dl.background(LOG)
    print(f"{datetime.now():%Y-%m-%d %H:%M} download started: {', '.join(good)}, {START} to {end}", flush=True)
    left = DAILY_CAP
    for name in good:
        try:
            s = run(token, name, end, log=lambda m: print(m, flush=True), budget=left)
        except dl.TokenExpired as exc:
            print(f"{datetime.now():%H:%M} STOPPED: the token expired ({exc}). Generate a new one and run "
                  f"tools/dhan_download_more.py again; it resumes.", flush=True)
            return 3
        left -= s["requests"]
        print(f"{datetime.now():%H:%M} {name}: {s}", flush=True)
        if s["stopped"]:
            print(f"{datetime.now():%H:%M} STOPPED for today at {DAILY_CAP:,} requests. Run it again tomorrow "
                  f"with a new token; it resumes.", flush=True)
            return 0
    print(f"{datetime.now():%H:%M} FINISHED: every index is complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
