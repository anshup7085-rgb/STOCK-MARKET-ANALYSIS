"""
Crypto candles on disk: state/crypto/klines/SYMBOL/INTERVAL/YEAR.csv.gz (UTC years), one row per closed
candle, never shortened. The same held-back years as everything else apply to tests and records
(EXPLORE_TO, HOLDOUT_TO below); recent days may be read as the market's recent past.
"""

from __future__ import annotations

import csv
import gzip
import io
import os
from datetime import date, datetime, timezone
from pathlib import Path

from crypto.binance import Candle

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "state" / "crypto" / "klines"
EXPLORE_TO, HOLDOUT_TO = date(2024, 10, 31), date(2026, 10, 18)
COLS = ("t", "open", "high", "low", "close", "volume", "quote", "trades")


def utc_day(c: Candle) -> date:
    return datetime.fromtimestamp(c.t / 1000, timezone.utc).date()


def _path(root: Path, symbol: str, interval: str, year: int) -> Path:
    return root / symbol / interval / f"{year}.csv.gz"


def _read(path: Path) -> dict[int, Candle]:
    if not path.exists():
        return {}
    with gzip.open(path, "rt") as fh:
        return {int(r["t"]): Candle(int(r["t"]), float(r["open"]), float(r["high"]), float(r["low"]),
                                    float(r["close"]), float(r["volume"]), float(r["quote"]), int(r["trades"]))
                for r in csv.DictReader(fh)}


def save(symbol: str, interval: str, candles: list[Candle], root: Path | None = None) -> int:
    """Merge into the year files; returns how many candles were new."""
    root = root or ROOT
    by_year: dict[int, list[Candle]] = {}
    for c in candles:
        by_year.setdefault(utc_day(c).year, []).append(c)
    new = 0
    for year, cs in by_year.items():
        path = _path(root, symbol, interval, year)
        have = _read(path)
        before = len(have)
        for c in cs:
            have[c.t] = c
        new += len(have) - before
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(COLS)
        for t in sorted(have):
            c = have[t]
            w.writerow([c.t, c.open, c.high, c.low, c.close, c.volume, c.quote, c.trades])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(buf.getvalue().encode(), mtime=0))
        os.replace(tmp, path)
    return new


def load(symbol: str, interval: str, root: Path | None = None) -> list[Candle]:
    root = root or ROOT
    out: dict[int, Candle] = {}
    for path in sorted((root / symbol / interval).glob("*.csv.gz")):
        out.update(_read(path))
    return [out[t] for t in sorted(out)]


def last_t(symbol: str, interval: str, root: Path | None = None) -> int | None:
    root = root or ROOT
    years = sorted((root / symbol / interval).glob("*.csv.gz"))
    if not years:
        return None
    have = _read(years[-1])
    return max(have) if have else None
