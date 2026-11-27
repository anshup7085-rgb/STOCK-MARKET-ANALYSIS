#!/usr/bin/env python3
"""
Save NIFTY 50 and INDIA VIX one-minute candles from Kite's historical API, 2015 to yesterday. Read-only.

    `morning` runs it every day in the background, on the day's login (a second login could end the
    runners' one). The first run fetches about eleven years, some 150 requests, in a few minutes;
    later runs fetch only the current year again, about a dozen requests.
    PYTHONPATH=. python tools/index_minutes_download.py      # by hand, with the day's login in the env

Files: state/index_minutes/{NIFTY_50,INDIA_VIX}/YEAR.csv.gz (time, open, high, low, close) and
manifest.json beside them (candles, sessions, full sessions, first and last minute, SHA-256). A past
year saved complete is never fetched again; a year whose fetch failed keeps its older file. Prints
counts and dates, never a price.

Reading these back goes through trading/fno/index_minutes.py, which keeps the same held-back years
as Dhan's option minutes: exploration reads sessions up to 31 Oct 2024 only.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import sys
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "state" / "index_minutes"
SYMBOLS = ("NIFTY 50", "INDIA VIX")
START_YEAR = 2015
WINDOW_DAYS = 59                 # Kite serves at most 60 days of minute candles a request
GAP_S = 0.4                      # Kite allows about 3 historical requests a second


def folder(symbol: str) -> str:
    return symbol.replace(" ", "_")


def to_csv_gz(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time", "open", "high", "low", "close"])
    for r in rows:
        w.writerow([r["date"].isoformat(), r["open"], r["high"], r["low"], r["close"]])
    return gzip.compress(buf.getvalue().encode(), mtime=0)


def fetch_year(kite, token: int, year: int, today: date, sleep) -> list[dict]:
    """Every minute candle of `year` up to yesterday, in windows of 59 days; one retry per window."""
    last = min(date(year, 12, 31), today - timedelta(days=1))
    s, out = date(year, 1, 1), []
    while s <= last:
        e = min(s + timedelta(days=WINDOW_DAYS - 1), last)
        sleep(GAP_S)
        try:
            rows = kite.historical_data(token, f"{s} 09:00:00", f"{e} 15:30:00", "minute")
        except Exception:                                       # noqa: BLE001 - one retry: Kite's gateway blips
            sleep(5)
            rows = kite.historical_data(token, f"{s} 09:00:00", f"{e} 15:30:00", "minute")
        out += [r for r in rows or [] if r["date"].year == year]
        s = e + timedelta(days=1)
    seen, uniq = set(), []
    for r in sorted(out, key=lambda r: r["date"]):
        if r["date"] not in seen:
            seen.add(r["date"])
            uniq.append(r)
    return uniq


def save(kite, today: date, root: Path = ROOT, sleep=_time.sleep, now_fn=datetime.now) -> dict:
    nse = {r["tradingsymbol"]: r for r in kite.instruments("NSE") if r.get("segment") == "INDICES"}
    summary = {"fetched": 0, "skipped": 0, "failed": 0, "errors": [], "lines": []}
    for sym in SYMBOLS:
        tok = nse.get(sym, {}).get("instrument_token")
        if not tok:
            summary["failed"] += 1
            summary["errors"].append(f"{sym}: not in Kite's instrument list")
            continue
        d = root / folder(sym)
        mpath = d / "manifest.json"
        manifest = json.loads(mpath.read_text()) if mpath.exists() else {"years": {}}
        for year in range(START_YEAR, today.year + 1):
            old = manifest["years"].get(str(year))
            if old and old.get("complete") and (old.get("candles") == 0 or (d / f"{year}.csv.gz").exists()):
                summary["skipped"] += 1
                continue
            try:
                rows = fetch_year(kite, int(tok), year, today, sleep)
            except Exception as exc:                            # noqa: BLE001 - counted; the older file stays
                summary["failed"] += 1
                summary["errors"].append(f"{sym} {year}: {type(exc).__name__}: {str(exc)[:80]}")
                continue
            summary["fetched"] += 1
            complete = date(year, 12, 31) < today
            per_day: dict[date, int] = {}
            for r in rows:
                per_day[r["date"].date()] = per_day.get(r["date"].date(), 0) + 1
            entry = {"candles": len(rows), "sessions": len(per_day),
                     "full_sessions": sum(1 for n in per_day.values() if n >= 375), "complete": complete,
                     "fetched_at": now_fn().isoformat(timespec="seconds")}
            if rows:
                raw = to_csv_gz(rows)
                d.mkdir(parents=True, exist_ok=True)
                tmp = d / f"{year}.tmp"
                tmp.write_bytes(raw)
                os.replace(tmp, d / f"{year}.csv.gz")
                entry.update(first=rows[0]["date"].isoformat(), last=rows[-1]["date"].isoformat(),
                             sha256=hashlib.sha256(raw).hexdigest())
            manifest["years"][str(year)] = entry
            summary["lines"].append(f"  {sym} {year}: {entry['sessions']} sessions ({entry['full_sessions']} full), "
                                    f"{entry['candles']:,} candles"
                                    + (f", {entry['first'][:10]} to {entry['last'][:10]}" if rows else ""))
            d.mkdir(parents=True, exist_ok=True)
            manifest.update(symbol=sym, source="Kite historical API, minute")
            mpath.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return summary


def main() -> int:
    from kiteconnect import KiteConnect

    from trading.fno.feeds import refuse_secrets_in_env
    refuse_secrets_in_env()
    key, token = os.getenv("KITE_API_KEY"), os.getenv("KITE_ACCESS_TOKEN")
    if not key or not token:
        print("needs the day's login (KITE_API_KEY and KITE_ACCESS_TOKEN): run from `morning`")
        return 2
    kite = KiteConnect(api_key=key)
    kite.set_access_token(token)
    print(f"index minutes, {datetime.now():%Y-%m-%d %H:%M}: NIFTY 50 and INDIA VIX from {START_YEAR}. "
          "Counts and dates only.", flush=True)
    s = save(kite, date.today())
    print("\n".join(s["lines"]))
    for e in s["errors"]:
        print(f"  failed: {e}")
    print(f"index minutes: {s['fetched']} year(s) fetched, {s['skipped']} already complete, {s['failed']} failed")
    return 1 if s["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
