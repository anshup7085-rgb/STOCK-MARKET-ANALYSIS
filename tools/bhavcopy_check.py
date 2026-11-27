#!/usr/bin/env python3
"""
What NSE's F&O bhavcopy really holds for an expiring NIFTY option series.

    python tools/bhavcopy_check.py 2026-10-19          # fetched from NSE
    python tools/bhavcopy_check.py 2026-10-19 FILE.zip # a file downloaded by hand

Prints the columns, the NIFTY option expiries in the file, and a sample of the
expiring rows across strikes with every price column, so the meaning of
SttlmPric for expiring options can be read off the data rather than assumed.
Public market data only; nothing here needs a login.
"""

from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
PRICE_COLS = ["XpryDt", "FininstrmActlXpryDt", "StrkPric", "OptnTp", "OpnPric", "ClsPric", "LastPric",
              "PrvsClsgPric", "SttlmPric", "UndrlygPric", "OpnIntrst", "TtlTradgVol"]


def load(d: date, path: str | None) -> pd.DataFrame:
    raw = Path(path).read_bytes() if path else urllib.request.urlopen(
        urllib.request.Request(URL.format(d=d), headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"}),
        timeout=30).read()
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            return pd.read_csv(z.open(name), dtype=str)
    return pd.read_csv(io.BytesIO(raw), dtype=str)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    d = date.fromisoformat(sys.argv[1])
    df = load(d, sys.argv[2] if len(sys.argv) > 2 else None)
    print("columns:", list(df.columns))
    n = df[(df["TckrSymb"].astype(str).str.upper() == "NIFTY")]
    print("NIFTY rows by FinInstrmTp:", n["FinInstrmTp"].value_counts().to_dict())
    o = n[n["FinInstrmTp"].astype(str).str.upper() == "IDO"]
    for c in ("XpryDt", "FininstrmActlXpryDt"):
        if c in o.columns:
            print(f"IDO rows by {c}:", o[c].value_counts().sort_index().head(8).to_dict())
    hit = pd.Series(False, index=o.index)
    for c in ("XpryDt", "FininstrmActlXpryDt"):
        if c in o.columns:
            hit |= pd.to_datetime(o[c], errors="coerce").dt.date == d
    e = o[hit].copy()
    print(f"rows expiring {d}: {len(e)}")
    if e.empty:
        return 1
    e["_k"] = pd.to_numeric(e["StrkPric"], errors="coerce")
    e = e.sort_values(["_k", "OptnTp"])
    cols = [c for c in PRICE_COLS if c in e.columns]
    strikes = sorted(e["_k"].dropna().unique())
    pick = set(strikes[:: max(1, len(strikes) // 12)]) | set(strikes[len(strikes) // 2 - 3: len(strikes) // 2 + 4])
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(e[e["_k"].isin(pick)][cols].to_string(index=False))
    sp = pd.to_numeric(e["SttlmPric"], errors="coerce")
    print("SttlmPric: zero", int((sp == 0).sum()), "| positive", int((sp > 0).sum()),
          "| distinct values", sp.nunique(), "| most common", sp.value_counts().head(3).to_dict())
    fut = n[(n["FinInstrmTp"].astype(str).str.upper() == "IDF")]
    if not fut.empty:
        print("NIFTY futures:")
        print(fut[[c for c in ("XpryDt", "ClsPric", "SttlmPric", "UndrlygPric") if c in fut.columns]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
