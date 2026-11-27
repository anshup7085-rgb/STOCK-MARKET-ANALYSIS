#!/usr/bin/env python3
"""
Download Dhan's expired NIFTY options, minute by minute, for the last five years: once, while the
one-month Data API plan is paid.

    cd ~/STOCK-MARKET-ANALYSIS && git pull -q && PYTHONPATH=. .venv/bin/python tools/dhan_download.py

It asks for the Dhan access token (hidden; never saved, never printed, never in the environment or
the process list), checks it with one request, then carries on in the background, so the window can
be closed. Progress: `tail -5 logs/dhan-download.log`. Running it again resumes where it stopped: a
window already saved is never fetched again. A token lasts 24 hours; when it expires the download
stops cleanly and asks for a new one.

What: NIFTY (securityId 13) weekly options, nearest expiry (expiryCode 1) at ATM-10 ... ATM+10, and
next expiry (expiryCode 2) at ATM-3 ... ATM+3, calls and puts, one-minute candles with open interest,
IV, strike and spot. Dhan's docs give both one day and 30 days per call, so a first request finds out:
30-day windows if a month comes back whole, otherwise one weekday per call. Where a 30-day window
comes back short, its missing weekdays are fetched one at a time. Each reply is kept as Dhan sent it, compressed, under
state/dhan/rolling/, with its SHA-256 in manifest.jsonl. Prints counts, never a price.

Checked on 4 Nov 2026 (tools/dhan_check.py) against our own Zerodha copy of the 3 Nov week: open
interest equal on 99.7% of minutes, Dhan's close inside Zerodha's high-low for the minute on 97.5%,
typical difference 0.05 points, no lean.
"""

from __future__ import annotations

import getpass
import gzip
import hashlib
import json
import os
import sys
import time as _time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from market.config import IST

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "state" / "dhan" / "rolling"
LOG = REPO / "logs" / "dhan-download.log"
URL = "https://api.dhan.co/v2/charts/rollingoption"
NIFTY_ID = 13
FIELDS = ["open", "high", "low", "close", "volume", "oi", "iv", "strike", "spot"]
START = date(2021, 10, 1)              # Dhan keeps five years; windows before its first day come back empty
WINDOW = 30
GAP_S = 0.25                           # Dhan allows 5 data requests a second
PLAN = [(1, o) for o in range(-10, 11)] + [(2, o) for o in range(-3, 4)]   # (expiry code, ATM offset)


class TokenExpired(Exception):
    pass


def strike_name(offset: int) -> str:
    return "ATM" if offset == 0 else f"ATM{offset:+d}"


def request(token: str, body: dict, sleep=_time.sleep) -> dict | None:
    """Dhan's reply, or None for a window with no data. Raises TokenExpired on 401/403."""
    for attempt in range(5):
        sleep(GAP_S)
        req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json",
                                              "access-token": token})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            text = exc.read().decode(errors="replace")[:300]
            if exc.code in (401, 403):
                raise TokenExpired(f"HTTP {exc.code}: {text[:120]}") from exc
            if exc.code == 429 or "RATE_LIMIT" in text.upper():
                sleep(2 ** attempt)
                continue
            if exc.code == 400 and ("no data" in text.lower() or "DH-905" in text or "DH-907" in text):
                return None
            if attempt == 4:
                raise RuntimeError(f"HTTP {exc.code}: {text[:160]}") from exc
            sleep(2 ** attempt)
        except Exception as exc:                                # noqa: BLE001 - network blips: retry
            if attempt == 4:
                raise RuntimeError(f"{type(exc).__name__}: {str(exc)[:160]}") from exc
            sleep(2 ** attempt)
    raise RuntimeError("rate limited five times")


def body(code: int, offset: int, right: str, a: date, b: date) -> dict:
    return {"exchangeSegment": "NSE_FNO", "interval": "1", "securityId": NIFTY_ID, "instrument": "OPTIDX",
            "expiryFlag": "WEEK", "expiryCode": code, "strike": strike_name(offset), "drvOptionType": right,
            "requiredData": FIELDS, "fromDate": a.isoformat(), "toDate": b.isoformat()}


def days_in(reply: dict | None, right: str) -> set[str]:
    s = ((reply or {}).get("data") or {}).get("ce" if right == "CALL" else "pe") or {}
    return {datetime.fromtimestamp(int(t), IST).strftime("%Y-%m-%d") for t in s.get("timestamp") or []}


def save(path: Path, reply: dict | None, manifest, meta: dict, root: Path = ROOT) -> None:
    raw = json.dumps(reply if reply is not None else {"data": None}).encode()
    blob = gzip.compress(raw, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, path)
    manifest.write(json.dumps({**meta, "file": str(path.relative_to(root)), "sha256": hashlib.sha256(raw).hexdigest(),
                               "empty": reply is None}) + "\n")
    manifest.flush()


def windows(start: date, end: date, size: int = WINDOW) -> list[tuple[date, date]]:
    """(from, to) pairs. A one-day window asks for d to d + 1 (Dhan's toDate), weekdays only."""
    out, a = [], start
    while a <= end:
        if size == 1:
            if a.weekday() < 5:
                out.append((a, a + timedelta(days=1)))
            a += timedelta(days=1)
            continue
        b = min(a + timedelta(days=size - 1), end)
        out.append((a, b))
        a = b + timedelta(days=1)
    return out


def pick_size(token: str, sleep=_time.sleep) -> int:
    """30 if Dhan returns a whole month for one request (October 2026, ATM call), else 1."""
    try:
        got = days_in(request(token, body(1, 0, "CALL", date(2026, 10, 1), date(2026, 10, 30)), sleep), "CALL")
    except RuntimeError:
        return 1
    return WINDOW if len(got) >= 15 else 1


def run(token: str, end: date, root: Path = ROOT, sleep=_time.sleep, log=print, size: int = WINDOW) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    stats = {"windows": 0, "empty": 0, "day_fills": 0, "failed": 0, "skipped": 0}
    with (root / "manifest.jsonl").open("a") as manifest:
        for code, offset in PLAN:
            for right in ("CALL", "PUT"):
                series = f"code{code}/{strike_name(offset)}_{right}"
                for a, b in windows(START, end, size):
                    path = root / series / f"{a.isoformat()}.json.gz"
                    if path.exists():
                        stats["skipped"] += 1
                        continue
                    meta = {"expiry_code": code, "strike": strike_name(offset), "right": right,
                            "from": a.isoformat(), "to": b.isoformat()}
                    try:
                        reply = request(token, body(code, offset, right, a, b), sleep)
                        got = days_in(reply, right)
                        weekdays = sum(1 for k in range((b - a).days + 1) if (a + timedelta(days=k)).weekday() < 5)
                        if size > 1 and got and len(got) < 0.6 * weekdays:   # short: fill it a day at a time
                            for k in range((b - a).days + 1):
                                d = a + timedelta(days=k)
                                if d.weekday() >= 5 or d.isoformat() in got:
                                    continue
                                day_path = root / series / "days" / f"{d.isoformat()}.json.gz"
                                if day_path.exists():
                                    continue
                                save(day_path, request(token, body(code, offset, right, d, d + timedelta(days=1)), sleep),
                                     manifest, {**meta, "from": d.isoformat(), "to": (d + timedelta(days=1)).isoformat()},
                                     root)
                                stats["day_fills"] += 1
                        save(path, reply, manifest, meta, root)
                        stats["windows"] += 1
                        stats["empty"] += reply is None or not got
                    except TokenExpired:
                        raise
                    except RuntimeError as exc:
                        stats["failed"] += 1
                        log(f"  {series} {a}: failed ({exc}); it is tried again on the next run")
                    done = stats["windows"] + stats["failed"]
                    if done and done % 100 == 0:
                        log(f"  {datetime.now():%H:%M} {done} windows done ({stats['empty']} empty, "
                            f"{stats['day_fills']} single days filled, {stats['failed']} failed); now {series} {a}")
    return stats


def background(log_path: Path) -> None:
    """Carry on after the window closes: the token stays in this process's memory only."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)


def main() -> int:
    end = min(date.today() - timedelta(days=1), date(2026, 11, 4))
    token = getpass.getpass("Dhan access token (hidden; paste once, then Enter): ").strip()
    if not token:
        print("nothing entered; stopping")
        return 2
    try:
        probe = request(token, body(1, 0, "CALL", date(2026, 11, 2), date(2026, 11, 3)))
    except TokenExpired as exc:
        print(f"Dhan refused the token ({exc}). Generate a new one on web.dhan.co and run this again.")
        return 2
    if not days_in(probe, "CALL"):
        print("the token works but a known recent day came back empty: stopping. Send this to Claude.")
        return 2
    size = pick_size(token)
    n_win = len(windows(START, end, size))
    print(f"token accepted. Dhan returns {'a month' if size > 1 else 'a day or two'} per request, so: "
          f"{len(PLAN) * 2} series x {n_win} {'30-day windows' if size > 1 else 'days'} = {len(PLAN) * 2 * n_win} "
          f"requests, about {len(PLAN) * 2 * n_win * 0.45 / 3600:.1f} hours, in the background.")
    print("You can close this window. Progress:  tail -5 logs/dhan-download.log")
    sys.stdout.flush()
    background(LOG)
    print(f"{datetime.now():%Y-%m-%d %H:%M} download started: {START} to {end}, {size}-day requests", flush=True)
    try:
        stats = run(token, end, log=lambda m: print(m, flush=True), size=size)
    except TokenExpired as exc:
        print(f"{datetime.now():%H:%M} STOPPED: the token expired ({exc}). Generate a new one and run "
              f"tools/dhan_download.py again; it resumes.", flush=True)
        return 3
    print(f"{datetime.now():%H:%M} FINISHED: {stats}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
