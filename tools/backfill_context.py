#!/usr/bin/env python3
"""
Download NSE's archived market-context files for every weekday since 2012, once.

    deploy/backfill.sh                                          # in the background, on the server
    PYTHONPATH=. python tools/backfill_context.py [--since 2012-01-01] [--until YYYY-MM-DD]
    PYTHONPATH=. python tools/backfill_context.py --summary     # what is on disk, no network

Files: participant-wise open interest and volume (FII, DII, pro, client; from 2012) and
the all-index close file (India VIX in it from October 2014). Each file is saved as NSE
served it, under state/nse_archive/SOURCE/YEAR/, with its SHA-256 in manifest.jsonl.
No option price is fetched: these files hold none.

Polite by design, because the same machine fetches NSE's settlement file for V: about
one request a second, a pause after any refusal, and a stop after five refusals in a
row. A run can be stopped and started again; it skips what it already has. A file NSE
does not have (a holiday, or a day before the series began) is listed in missing.json
and not asked for again unless --retry-missing is given. Prints counts, never a figure
from a file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time as _time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from market.config import IST

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "state" / "nse_archive"
HOSTS = ("https://nsearchives.nseindia.com", "https://archives.nseindia.com")
SOURCES = {
    "participant_oi": "/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv",
    "participant_vol": "/content/nsccl/fao_participant_vol_{d:%d%m%Y}.csv",
    "index_close": "/content/indices/ind_close_all_{d:%d%m%Y}.csv",
}
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
GAP_S, PAUSE_S, MAX_REFUSALS = 1.0, 60.0, 5


class NotThere(Exception):
    """NSE answered that the file does not exist (404)."""


class Refused(Exception):
    """Anything else: a block, a timeout, a dropped connection."""


def fetch(path: str, timeout: float = 20.0) -> bytes:
    last: Exception | None = None
    for host in HOSTS:
        try:
            return urllib.request.urlopen(urllib.request.Request(host + path, headers=HEADERS),
                                          timeout=timeout).read()
        except urllib.error.HTTPError as exc:
            last = NotThere(f"HTTP {exc.code}") if exc.code == 404 else Refused(f"HTTP {exc.code}")
        except Exception as exc:                                # noqa: BLE001 - a refusal of any kind
            last = Refused(f"{type(exc).__name__}: {str(exc)[:80]}")
    raise last


def looks_right(source: str, raw: bytes) -> bool:
    """A cheap check that NSE sent the file and not an error page."""
    head = raw[:400].decode("utf-8-sig", errors="replace").lower()
    return ("client type" in head) if source.startswith("participant") else ("index name" in head)


def file_path(source: str, d: date) -> Path:
    return ROOT / source / str(d.year) / Path(SOURCES[source].format(d=d)).name


def load_missing() -> dict[str, list[str]]:
    p = ROOT / "missing.json"
    return json.loads(p.read_text()) if p.exists() else {s: [] for s in SOURCES}


def save_missing(missing: dict[str, list[str]]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = ROOT / "missing.json.tmp"
    tmp.write_text(json.dumps({k: sorted(set(v)) for k, v in missing.items()}, indent=0))
    tmp.replace(ROOT / "missing.json")


def weekdays(since: date, until: date) -> list[date]:
    out, d = [], since
    while d <= until:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def quiet_hours(sleep, now_fn) -> None:
    """Stay off NSE from 15:25 to 17:30 IST, when V's runner fetches the day's settlement file."""
    now = now_fn()
    if dtime(15, 25) <= now.time() < dtime(17, 30):
        wait = (datetime.combine(now.date(), dtime(17, 30), now.tzinfo) - now).total_seconds()
        print(f"  pausing until 17:30 (V's settlement window), {wait / 60:.0f} min", flush=True)
        sleep(wait)


def run(since: date, until: date, retry_missing: bool, fetcher=fetch, sleep=_time.sleep,
        now_fn=lambda: datetime.now(IST)) -> dict:
    missing = load_missing()
    for s in SOURCES:
        missing.setdefault(s, [])
    skip = {s: set() if retry_missing else set(missing[s]) for s in SOURCES}
    got = {s: 0 for s in SOURCES}
    refusals, days = 0, weekdays(since, until)
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / "manifest.jsonl").open("a") as manifest:
        for i, d in enumerate(days):
            for source in SOURCES:
                path = file_path(source, d)
                if path.exists() or d.isoformat() in skip[source]:
                    continue
                while True:
                    quiet_hours(sleep, now_fn)
                    sleep(GAP_S)
                    try:
                        raw = fetcher(SOURCES[source].format(d=d))
                        refusals = 0
                        break
                    except NotThere:
                        raw = None
                        refusals = 0
                        break
                    except Refused as exc:
                        refusals += 1
                        print(f"  {d} {source}: refused ({exc}) [{refusals}/{MAX_REFUSALS}]"
                              + (f"; pause {PAUSE_S:.0f} s" if refusals < MAX_REFUSALS else ""), flush=True)
                        if refusals >= MAX_REFUSALS:
                            save_missing(missing)
                            print("stopping: NSE kept refusing. Run it again later; it resumes where it stopped.")
                            return {"stopped": True, "got": got}
                        sleep(PAUSE_S)
                if raw is None or not looks_right(source, raw):
                    missing[source].append(d.isoformat())
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                manifest.write(json.dumps({"source": source, "day": d.isoformat(), "file": str(path.relative_to(ROOT)),
                                           "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                                           "fetched_at": datetime.now().isoformat(timespec="seconds")}) + "\n")
                manifest.flush()
                got[source] += 1
            if i % 100 == 99:
                save_missing(missing)
                print(f"  {d}: {i + 1}/{len(days)} weekdays done; new files {got}", flush=True)
    save_missing(missing)
    return {"stopped": False, "got": got}


def summary() -> list[str]:
    out = []
    missing = load_missing() if (ROOT / "missing.json").exists() else {}
    for source in SOURCES:
        files = sorted((ROOT / source).glob("*/*")) if (ROOT / source).exists() else []
        if not files:
            out.append(f"{source}: no files yet")
            continue
        days = sorted(f.name.rsplit("_", 1)[1][:8] for f in files)
        as_dates = sorted(datetime.strptime(x, "%d%m%Y").date() for x in days)
        out.append(f"{source}: {len(files)} days, {as_dates[0]} to {as_dates[-1]}; "
                   f"not on NSE (holidays and gaps): {len(missing.get(source, []))}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="NSE market-context archive backfill (counts only)")
    ap.add_argument("--since", default="2012-01-01")
    ap.add_argument("--until", default=None, help="default: yesterday")
    ap.add_argument("--retry-missing", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    if args.summary:
        print("\n".join(summary()))
        return 0
    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until) if args.until else date.today() - timedelta(days=1)
    print(f"backfill {since} to {until}: {len(weekdays(since, until))} weekdays x {len(SOURCES)} files, "
          f"about one request a second. Safe to leave running.", flush=True)
    res = run(since, until, args.retry_missing)
    print("finished." if not res["stopped"] else "paused.", "New files:", res["got"])
    print("\n".join(summary()))
    return 0 if not res["stopped"] else 3


if __name__ == "__main__":
    sys.exit(main())
