"""
Perpetual-futures minutes for the futures lab (research/perps_lab.py): Binance USD-M perpetuals' one-minute
candles and their funding rates, from Binance's free bulk files (data.binance.vision, reachable from the
server and from the research workspace, checked 27 Nov 2026). No account, no key.

Stored as Binance publishes them, checksum-verified and never changed:

    state/crypto/perp_minutes/SYMBOL/SYMBOL-1m-YYYY-MM.zip
    state/crypto/perp_minutes/SYMBOL/SYMBOL-fundingRate-YYYY-MM.zip

THE HOLDOUT, as for every crypto test (crypto/store.py): months up to Oct 2024 are exploration. Months from
Nov 2024 to Oct 2026 are held back: they are neither downloaded nor read here without the SHA-256 of a bound
registration, and every such read is logged with its caller. Later months are the forward record.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
import time as _time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from crypto.store import EXPLORE_TO, HOLDOUT_TO

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "state" / "crypto" / "perp_minutes"
BASE = "https://data.binance.vision/data/futures/um/monthly"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")
FIRST = date(2021, 1, 1)
COLS = ("t", "open", "high", "low", "close", "volume", "trades", "taker_buy")


class Holdout(Exception):
    pass


def months(start: date, end: date) -> list[date]:
    out, y, m = [], start.year, start.month
    while date(y, m, 1) <= end:
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _guard(month: date, registration_sha256: str | None, what: str, root: Path) -> None:
    if month > HOLDOUT_TO:
        raise Holdout(f"{month:%Y-%m} is after {HOLDOUT_TO}: forward data, not for historical tests")
    if month > EXPLORE_TO:
        if not registration_sha256:
            raise Holdout(f"{month:%Y-%m} is held back (after {EXPLORE_TO}) for registered tests")
        caller = inspect.stack()[2]
        root.mkdir(parents=True, exist_ok=True)
        with (root / "holdout_reads.log").open("a") as fh:
            fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "what": what,
                                 "month": f"{month:%Y-%m}", "registration_sha256": registration_sha256,
                                 "caller": f"{Path(caller.filename).name}:{caller.lineno}"}) + "\n")


def name(symbol: str, kind: str, month: date) -> str:
    return f"{symbol}-{'1m' if kind == 'klines' else 'fundingRate'}-{month:%Y-%m}.zip"


def url(symbol: str, kind: str, month: date) -> str:
    middle = f"klines/{symbol}/1m" if kind == "klines" else f"fundingRate/{symbol}"
    return f"{BASE}/{middle}/{name(symbol, kind, month)}"


def fetch(u: str, timeout: float = 60.0) -> bytes | None:
    """The file's bytes, or None when Binance has no such file (404)."""
    req = urllib.request.Request(u, headers={"User-Agent": "market-lab/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def download(symbol: str, kind: str, month: date, root: Path | None = None, get=fetch,
             registration_sha256: str | None = None) -> str:
    """Fetch one monthly file and check it against Binance's published SHA-256. Returns 'have', 'new',
    'none' (Binance has no file: the coin was not listed yet) or raises on a checksum mismatch."""
    root = root or ROOT
    _guard(month, registration_sha256, f"download {kind}", root)
    path = root / symbol / name(symbol, kind, month)
    if path.exists():
        return "have"
    body = get(url(symbol, kind, month))
    if body is None:
        return "none"
    check = get(url(symbol, kind, month) + ".CHECKSUM")
    want = check.decode().split()[0] if check else None
    got = hashlib.sha256(body).hexdigest()
    if want and want != got:
        raise ValueError(f"{path.name}: checksum {got[:12]} is not Binance's {want[:12]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(body)
    os.replace(tmp, path)
    return "new"


def download_all(symbols=SYMBOLS, start: date = FIRST, end: date = EXPLORE_TO, root: Path | None = None,
                 get=fetch, sleep=_time.sleep, log=print) -> dict[str, int]:
    count = {"have": 0, "new": 0, "none": 0, "failed": 0}
    for sym in symbols:
        for kind in ("klines", "funding"):
            for m in months(start, end):
                try:
                    what = download(sym, kind, m, root=root, get=get)
                except Holdout:
                    raise
                except Exception as exc:                        # noqa: BLE001 - reported; the rest go on
                    log(f"  {sym} {kind} {m:%Y-%m}: failed ({type(exc).__name__}: {str(exc)[:80]})")
                    count["failed"] += 1
                    continue
                count[what] += 1
                if what == "new":
                    sleep(0.1)
        log(f"  {sym}: done")
    return count


def _csv_rows(body: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        text = z.read(z.namelist()[0]).decode()
    lines = text.splitlines()
    if lines and not lines[0][:1].isdigit():                # some months carry a header row, some do not
        lines = lines[1:]
    return [x for x in lines if x]


def load(symbol: str, start: date, end: date, root: Path | None = None,
         registration_sha256: str | None = None) -> dict[str, np.ndarray]:
    """One-minute candles from the first of `start`'s month to the end of `end`'s month, as arrays named
    by COLS (t in ms, UTC open time). Missing months are skipped; the caller sees the gaps in t."""
    root = root or ROOT
    parts = []
    for m in months(date(start.year, start.month, 1), end):
        _guard(m, registration_sha256, "read klines", root)
        path = root / symbol / name(symbol, "klines", m)
        if not path.exists():
            continue
        rows = _csv_rows(path.read_bytes())
        a = np.loadtxt(rows, delimiter=",", usecols=(0, 1, 2, 3, 4, 5, 8, 9), dtype=np.float64, ndmin=2)
        parts.append(a)
    if not parts:
        return {c: np.empty(0) for c in COLS}
    a = np.concatenate(parts)
    a = a[np.argsort(a[:, 0], kind="stable")]
    keep = np.concatenate([[True], np.diff(a[:, 0]) > 0])
    a = a[keep]
    return {c: a[:, i] for i, c in enumerate(COLS)}


def load_funding(symbol: str, start: date, end: date, root: Path | None = None,
                 registration_sha256: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(settlement times in ms, rates as fractions), sorted."""
    root = root or ROOT
    ts, rs = [], []
    for m in months(date(start.year, start.month, 1), end):
        _guard(m, registration_sha256, "read funding", root)
        path = root / symbol / name(symbol, "funding", m)
        if not path.exists():
            continue
        for line in _csv_rows(path.read_bytes()):
            f = line.split(",")
            ts.append(int(f[0]))
            rs.append(float(f[-1]))
    t, r = np.asarray(ts, dtype=np.int64), np.asarray(rs, dtype=np.float64)
    order = np.argsort(t, kind="stable")
    return t[order], r[order]


def have(symbol: str, root: Path | None = None) -> list[str]:
    root = root or ROOT
    return sorted(p.name for p in (root / symbol).glob("*-1m-*.zip"))
