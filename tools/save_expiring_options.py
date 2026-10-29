#!/usr/bin/env python3
"""
Save the minute-by-minute history of every NIFTY option (and future) about to expire.

Kite serves an option's minute candles, with open interest, only while the option is listed;
once it expires its history is gone from Kite. So every trading day after the close, this
saves the whole history (from listing) of each NIFTY contract expiring in the next 7 days.
The run on expiry day itself captures the complete life of that week's contracts. Over time
this becomes our own minute-level option history, and the yardstick for any bought data.

    `morning` starts it in the background with the day's login; it waits until 15:40 IST.
    PYTHONPATH=. python tools/save_expiring_options.py --now     # run at once (needs the login)

Read-only. It never touches an order endpoint. Files: state/option_minutes/EXPIRY/SYMBOL.csv.gz
(time, open, high, low, close, volume, oi), with manifest.json beside them. A later run
replaces a contract's file only with a history at least as long. It keeps clear of 16:00 to
17:15 IST, when V's settlement pass reads Kite's closing price. Prints counts, never prices.
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
from datetime import time as dtime
from pathlib import Path

from market.config import IST

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "state" / "option_minutes"
START, PAUSE_FROM, RESUME_AT = dtime(15, 40), dtime(15, 58), dtime(17, 20)
GAP_S = 0.5                      # about 2 requests a second; Kite allows 3 on the historical API
BAND = 0.15                      # strikes within 15% of NIFTY
WINDOW_DAYS, MAX_BACK_DAYS = 59, 400


def to_csv_gz(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time", "open", "high", "low", "close", "volume", "oi"])
    for r in rows:
        w.writerow([r["date"].isoformat(), r["open"], r["high"], r["low"], r["close"], r.get("volume", ""), r.get("oi", "")])
    return gzip.compress(buf.getvalue().encode(), mtime=0)


def rows_in(path: Path) -> int:
    if not path.exists():
        return 0
    with gzip.open(path, "rt") as fh:
        return max(0, sum(1 for _ in fh) - 1)


def history(kite, token: int, today: date, sleep) -> list[dict]:
    """All minute candles from listing to today, fetched in windows of under 60 days, newest first."""
    out: list[dict] = []
    end = today
    while (today - end).days < MAX_BACK_DAYS:
        start = end - timedelta(days=WINDOW_DAYS)
        sleep(GAP_S)
        rows = kite.historical_data(token, f"{start} 09:00:00", f"{end} 15:30:00", "minute", oi=True)
        if not rows:
            break
        out = list(rows) + out
        end = start - timedelta(days=1)
    seen, uniq = set(), []
    for r in out:
        if r["date"] not in seen:
            seen.add(r["date"])
            uniq.append(r)
    return sorted(uniq, key=lambda r: r["date"])


def keep_clear_of_v(now_fn, sleep) -> None:
    """V reads Kite's close from 16:00 on expiry day: pause from 15:58 to 17:20."""
    now = now_fn()
    if PAUSE_FROM <= now.time() < RESUME_AT:
        sleep((datetime.combine(now.date(), RESUME_AT, now.tzinfo) - now).total_seconds())


def save(kite, today: date, root: Path = ROOT, now_fn=lambda: datetime.now(IST), sleep=_time.sleep,
         underlying: str = "NIFTY", spot: float | None = None) -> dict:
    nfo = [r for r in kite.instruments("NFO") if str(r.get("name")) == underlying]
    if spot is None:
        idx = [r for r in kite.instruments("NSE") if r.get("tradingsymbol") == "NIFTY 50" and r.get("segment") == "INDICES"]
        sleep(GAP_S)
        days = kite.historical_data(idx[0]["instrument_token"], f"{today - timedelta(days=10)} 09:00:00",
                                    f"{today} 15:30:00", "day") if idx else []
        spot = days[-1]["close"] if days else None
    if not spot:
        return {"error": "no NIFTY level to centre the strikes on"}
    horizon = today + timedelta(days=7)
    picked = [r for r in nfo if today <= r["expiry"] <= horizon and (
        r.get("instrument_type") == "FUT"
        or (r.get("instrument_type") in ("CE", "PE") and abs(r["strike"] / spot - 1) <= BAND))]
    summary: dict = {"expiries": {}, "contracts": 0, "candles": 0, "failed": 0}
    for r in sorted(picked, key=lambda r: (r["expiry"], r.get("strike", 0), r.get("instrument_type"))):
        keep_clear_of_v(now_fn, sleep)
        exp = r["expiry"].isoformat()
        folder = root / exp
        path = folder / f"{r['tradingsymbol']}.csv.gz"
        try:
            rows = history(kite, int(r["instrument_token"]), today, sleep)
        except Exception as exc:                                # noqa: BLE001 - counted, the run goes on
            summary["failed"] += 1
            summary.setdefault("errors", []).append(f"{r['tradingsymbol']}: {type(exc).__name__}: {str(exc)[:80]}")
            continue
        if not rows:
            continue
        e = summary["expiries"].setdefault(exp, {"contracts": 0, "candles": 0, "files": {}})
        if len(rows) >= rows_in(path):                          # never replace a longer history with a shorter one
            raw = to_csv_gz(rows)
            folder.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(raw)
            os.replace(tmp, path)
            e["files"][path.name] = {"candles": len(rows), "first": rows[0]["date"].isoformat(),
                                     "last": rows[-1]["date"].isoformat(), "sha256": hashlib.sha256(raw).hexdigest()}
        e["contracts"] += 1
        e["candles"] += len(rows)
        summary["contracts"] += 1
        summary["candles"] += len(rows)
    for exp, e in summary["expiries"].items():
        mpath = root / exp / "manifest.json"
        old = json.loads(mpath.read_text()) if mpath.exists() else {"files": {}}
        old["files"].update(e.pop("files"))
        old.update(expiry=exp, updated_at=now_fn().isoformat(timespec="seconds"), source="Kite historical API, minute, oi")
        mpath.parent.mkdir(parents=True, exist_ok=True)
        mpath.write_text(json.dumps(old, indent=1, sort_keys=True))
    return summary


def main() -> int:
    from kiteconnect import KiteConnect

    from trading.fno.feeds import refuse_secrets_in_env
    refuse_secrets_in_env()
    key, token = os.getenv("KITE_API_KEY"), os.getenv("KITE_ACCESS_TOKEN")
    if not key or not token:
        print("needs the day's login (KITE_API_KEY and KITE_ACCESS_TOKEN): `morning` starts this")
        return 2
    now = datetime.now(IST)
    if "--now" not in sys.argv and now.time() < START:
        wait = (datetime.combine(now.date(), START, IST) - now).total_seconds()
        print(f"waiting until {START:%H:%M} to save the expiring contracts ({wait / 3600:.1f} h)", flush=True)
        _time.sleep(wait)
    kite = KiteConnect(api_key=key)
    kite.set_access_token(token)
    res = save(kite, datetime.now(IST).date())
    exps = ", ".join(f"{k}: {v['contracts']} contracts, {v['candles']:,} candles" for k, v in res.get("expiries", {}).items())
    print(f"option minutes saved {datetime.now(IST):%H:%M}: " + (exps or res.get("error", "nothing expiring within 7 days"))
          + (f"; {res['failed']} failed" if res.get("failed") else ""), flush=True)
    for line in res.get("errors", [])[:5]:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
