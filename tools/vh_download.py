#!/usr/bin/env python3
"""
Download NSE's F&O files for V-H's window (research/PREREGISTRATION_4.md section 2), once.

    deploy/vh.sh                                        # download, then run both implementations
    PYTHONPATH=. python tools/vh_download.py            # download only

Refuses to run unless the registration text is the bound one. For every weekday from
1 Jan 2019 to 18 Oct 2026 it fetches the day's F&O bhavcopy (the UDiFF file, or the older
fo...bhav.csv file), records the zip's SHA-256, and keeps only the NIFTY index-option rows,
verbatim, in state/vh/raw/YEAR/DATE.csv.gz. A day NSE has no file for is listed in
missing.json (a holiday). Polite like deploy/backfill.sh: about one request a second, a pause
after a refusal, a stop after five in a row, and never between 15:25 and 17:30 IST, when V
fetches the day's settlement file. Resumable. Prints counts, never a price.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import sys
import time as _time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from market.config import IST
from trading.fno.vh import WINDOW_FROM, WINDOW_TO, check_registration

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "state" / "vh"
RAW = ROOT / "raw"
UDIFF = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
LEGACY = "https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{d:%Y}/{m}/fo{d:%d}{m}{d:%Y}bhav.csv.zip"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
GAP_S, PAUSE_S, MAX_REFUSALS = 1.0, 60.0, 5


class NotThere(Exception):
    pass


class Refused(Exception):
    pass


def fetch(url: str, timeout: float = 60.0) -> bytes:
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout).read()
    except urllib.error.HTTPError as exc:
        raise (NotThere if exc.code == 404 else Refused)(f"HTTP {exc.code}") from exc
    except Exception as exc:                                    # noqa: BLE001
        raise Refused(f"{type(exc).__name__}: {str(exc)[:80]}") from exc


def urls(d: date) -> list[tuple[str, str]]:
    u = [("udiff", UDIFF.format(d=d)), ("legacy", LEGACY.format(d=d, m=d.strftime("%b").upper()))]
    return u if d >= date(2024, 1, 1) else u[::-1]


def nifty_rows(zip_bytes: bytes) -> tuple[str, int]:
    """The NIFTY index-option rows of the file, verbatim with the header, and their count."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        text = z.read(name).decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    head = [h.strip() for h in rows[0]]
    if "TckrSymb" in head:
        a, b, want = head.index("TckrSymb"), head.index("FinInstrmTp"), ("NIFTY", "IDO")
    elif "SYMBOL" in head:
        a, b, want = head.index("SYMBOL"), head.index("INSTRUMENT"), ("NIFTY", "OPTIDX")
    else:
        raise ValueError("neither NSE layout")
    keep = [r for r in rows[1:] if len(r) > max(a, b) and (r[a].strip().upper(), r[b].strip().upper()) == want]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(head)
    w.writerows(keep)
    return buf.getvalue(), len(keep)


def quiet_hours(sleep, now_fn) -> None:
    now = now_fn()
    if dtime(15, 25) <= now.time() < dtime(17, 30):
        sleep((datetime.combine(now.date(), dtime(17, 30), now.tzinfo) - now).total_seconds())


def run(fetcher=fetch, sleep=_time.sleep, now_fn=lambda: datetime.now(IST), root: Path = ROOT) -> dict:
    check_registration()
    raw = root / "raw"
    missing_path = root / "missing.json"
    missing = set(json.loads(missing_path.read_text())) if missing_path.exists() else set()
    got, refusals, d = 0, 0, WINDOW_FROM
    root.mkdir(parents=True, exist_ok=True)
    with (root / "manifest.jsonl").open("a") as manifest:
        while d <= WINDOW_TO:
            out = raw / str(d.year) / f"{d.isoformat()}.csv.gz"
            if d.weekday() < 5 and not out.exists() and d.isoformat() not in missing:
                found = None
                for layout, url in urls(d):
                    while True:
                        quiet_hours(sleep, now_fn)
                        sleep(GAP_S)
                        try:
                            found = (layout, url, fetcher(url))
                            refusals = 0
                            break
                        except NotThere:
                            refusals = 0
                            break
                        except Refused as exc:
                            refusals += 1
                            print(f"  {d}: refused ({exc}) [{refusals}/{MAX_REFUSALS}]", flush=True)
                            if refusals >= MAX_REFUSALS:
                                missing_path.write_text(json.dumps(sorted(missing)))
                                print("stopping: NSE kept refusing. Run it again later; it resumes.")
                                return {"stopped": True, "got": got}
                            sleep(PAUSE_S)
                    if found:
                        break
                if found is None:
                    missing.add(d.isoformat())
                else:
                    layout, url, blob = found
                    text, n = nifty_rows(blob)
                    data = gzip.compress(text.encode(), mtime=0)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(data)
                    manifest.write(json.dumps({"day": d.isoformat(), "layout": layout, "url": url,
                                               "zip_sha256": hashlib.sha256(blob).hexdigest(), "nifty_rows": n,
                                               "kept_sha256": hashlib.sha256(data).hexdigest(),
                                               "fetched_at": now_fn().isoformat(timespec="seconds")}) + "\n")
                    manifest.flush()
                    got += 1
                    if got % 100 == 0:
                        print(f"  {d}: {got} new days saved", flush=True)
                        missing_path.write_text(json.dumps(sorted(missing)))
            d += timedelta(days=1)
    missing_path.write_text(json.dumps(sorted(missing)))
    (root / "download.complete").write_text(json.dumps({"finished_at": now_fn().isoformat(timespec="seconds"),
                                                         "missing_weekdays": len(missing)}))
    return {"stopped": False, "got": got, "missing": len(missing)}


def main() -> int:
    res = run()
    days = len(list(RAW.glob("*/*.csv.gz")))
    print(f"download {'paused' if res['stopped'] else 'finished'}: {res['got']} new days; {days} days on disk; "
          f"{res.get('missing', '?')} weekdays without an NSE file")
    return 3 if res["stopped"] else 0


if __name__ == "__main__":
    sys.exit(main())
