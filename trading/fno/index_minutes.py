"""
Read NIFTY 50 and INDIA VIX one-minute candles (saved by tools/index_minutes_download.py) back for testing.

Layout: state/index_minutes/{NIFTY_50,INDIA_VIX}/YEAR.csv.gz with columns time, open, high, low, close.
A Kite minute candle labelled 09:44 spans 09:44:00 to 09:44:59, so its close is the level at 09:45.

THE HOLDOUT, the same as for Dhan's option minutes (trading/fno/dhan_data.py): exploration reads
sessions up to 31 Oct 2024 only. Sessions from 1 Nov 2024 to 18 Oct 2026 need the SHA-256 of a
bound registration, and every such read is logged to state/index_minutes/holdout_reads.log with
its caller. Later sessions belong to the forward family and are never served here.
"""

from __future__ import annotations

import csv
import gzip
import inspect
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from market.config import IST
from trading.fno.dhan_data import EXPLORE_TO, HOLDOUT_TO, Holdout

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "state" / "index_minutes"
AUDIT = ROOT / "holdout_reads.log"
NIFTY, VIX = "NIFTY_50", "INDIA_VIX"


@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float


def load(symbol: str, start: date, end: date, root: Path = ROOT,
         registration_sha256: str | None = None) -> dict[date, dict[str, Candle]]:
    """session -> {HH:MM: Candle}, 09:15 to 15:29, for start <= session <= end."""
    if end > HOLDOUT_TO:
        raise Holdout(f"{end} is after {HOLDOUT_TO}: forward data, not for historical tests")
    if end > EXPLORE_TO:
        if not registration_sha256:
            raise Holdout(f"sessions after {EXPLORE_TO} are held back for registered tests")
        caller = inspect.stack()[1]
        root.mkdir(parents=True, exist_ok=True)
        with (root / AUDIT.name).open("a") as fh:
            fh.write(json.dumps({"at": datetime.now(IST).isoformat(timespec="seconds"), "symbol": symbol,
                                 "from": start.isoformat(), "to": end.isoformat(),
                                 "registration_sha256": registration_sha256,
                                 "caller": f"{Path(caller.filename).name}:{caller.lineno}"}) + "\n")
    out: dict[date, dict[str, Candle]] = {}
    for year in range(start.year, end.year + 1):
        path = root / symbol / f"{year}.csv.gz"
        if not path.exists():
            continue
        with gzip.open(path, "rt") as fh:
            for row in csv.DictReader(fh):
                t = datetime.fromisoformat(row["time"])
                t = t.astimezone(IST) if t.tzinfo else t
                hm = t.strftime("%H:%M")
                if not (start <= t.date() <= end) or not ("09:15" <= hm <= "15:29"):
                    continue
                out.setdefault(t.date(), {})[hm] = Candle(float(row["open"]), float(row["high"]),
                                                          float(row["low"]), float(row["close"]))
    return dict(sorted(out.items()))
