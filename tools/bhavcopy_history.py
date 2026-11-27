#!/usr/bin/env python3
"""
How far back NSE's free daily F&O file goes, and whether it holds NIFTY option prices.

    PYTHONPATH=. python tools/bhavcopy_history.py

Probes one trading-looking weekday near the 20th of a month, every 6 months back to 2018,
in both NSE layouts (the new UDiFF file and the older "fo...bhav" file). For each date it
prints whether the file downloaded, and how many NIFTY option rows and which columns it has.
Public market data only; nothing here needs a login. Read-only: writes nothing.
"""

from __future__ import annotations

import io
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta

import pandas as pd

NEW = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
OLD = "https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{d:%Y}/{m}/fo{d:%d}{m}{d:%Y}bhav.csv.zip"
HEAD = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}


def fetch(url: str) -> pd.DataFrame | str:
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=HEAD), timeout=30).read()
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except Exception as exc:   # noqa: BLE001 - a probe: report anything
        return type(exc).__name__
    if raw[:2] != b"PK":
        return "not a zip"
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        return pd.read_csv(z.open(name), dtype=str)


def nifty_options(df: pd.DataFrame, new: bool) -> pd.DataFrame:
    if new:
        return df[(df["TckrSymb"].str.upper() == "NIFTY") & (df["FinInstrmTp"].str.upper() == "IDO")]
    return df[(df["SYMBOL"].str.upper() == "NIFTY") & (df["INSTRUMENT"].str.upper() == "OPTIDX")]


def weekday_near(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def main() -> int:
    today = date.today()
    rows = []
    d = today.replace(day=20)
    while d.year >= 2018:
        day = weekday_near(d)
        got = None
        for back in range(4):                       # a holiday: try the days before it
            probe = weekday_near(day - timedelta(days=back))
            for new, url in ((True, NEW.format(d=probe)),
                             (False, OLD.format(d=probe, m=probe.strftime("%b").upper()))):
                res = fetch(url)
                if isinstance(res, pd.DataFrame):
                    got = (probe, new, res)
                    break
            if got:
                break
        if got:
            probe, new, df = got
            o = nifty_options(df, new)
            cols = "UDiFF" if new else "legacy"
            print(f"{probe}  OK  {cols:6s} NIFTY option rows {len(o):6d}  columns {len(df.columns)}")
            if not o.empty and not rows:
                rows.append(list(df.columns))
        else:
            print(f"{day}  none found (tried both layouts, 4 days)")
        for _ in range(6):                          # six months back
            d = (d.replace(day=1) - timedelta(days=1)).replace(day=20)
    if rows:
        print("\nfirst columns seen:", rows[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
