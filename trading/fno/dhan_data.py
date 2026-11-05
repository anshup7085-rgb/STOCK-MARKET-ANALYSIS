"""
Read Dhan's expired NIFTY option minutes (downloaded by tools/dhan_download.py) back for testing.

Layout on disk: state/dhan/rolling/code{1,2}/{ATM, ATM+k, ATM-k}_{CALL,PUT}/{DATE}.json.gz, one
Dhan reply per weekday asked (Dhan's toDate came back inclusive, so a file also holds the next day).
Code 1 is the nearest weekly expiry, code 2 the next; each minute names its own strike, so a fixed
strike is followed across the ATM-relative series.

THE HOLDOUT. Exploration may read sessions up to EXPLORE_TO only. Later sessions, up to 18 Oct 2026,
are kept for tests whose rules are written and bound first; after that date the data is the forward
family's. load_day() refuses a later session unless the caller passes the SHA-256 of a bound
registration, and every such read is appended to state/dhan/holdout_reads.log with its caller, so a
look at the held-back years cannot happen quietly.
"""

from __future__ import annotations

import gzip
import inspect
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from market.config import IST

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "state" / "dhan" / "rolling"
AUDIT = REPO / "state" / "dhan" / "holdout_reads.log"
EXPLORE_FROM, EXPLORE_TO = date(2021, 10, 1), date(2024, 10, 31)     # before SEBI's November 2024 changes
HOLDOUT_TO = date(2026, 10, 18)                                      # the historical family's last day
OFFSETS = tuple(range(-10, 11))


class Holdout(RuntimeError):
    """A held-back session was asked for without a bound registration."""


@dataclass(frozen=True)
class Bar:
    strike: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    oi: float
    iv: float | None
    spot: float | None


def series_name(offset: int, right: str) -> str:
    return f"{'ATM' if offset == 0 else f'ATM{offset:+d}'}_{right}"


def _bars(path: Path, right: str, day: date) -> dict[str, Bar]:
    """HH:MM -> Bar for `day`, inside 09:15 to 15:29, from one stored reply."""
    if not path.exists():
        return {}
    reply = json.loads(gzip.decompress(path.read_bytes()))
    s = ((reply or {}).get("data") or {}).get("ce" if right == "CALL" else "pe") or {}
    ts = s.get("timestamp") or []
    col = {k: s.get(k) or [None] * len(ts) for k in ("strike", "open", "high", "low", "close", "volume", "oi",
                                                      "iv", "spot")}
    out = {}
    for i, t in enumerate(ts):
        when = datetime.fromtimestamp(int(t), IST)
        hm = when.strftime("%H:%M")
        if when.date() != day or not ("09:15" <= hm <= "15:29") or col["strike"][i] is None or col["close"][i] is None:
            continue
        out[hm] = Bar(float(col["strike"][i]), float(col["open"][i] or 0), float(col["high"][i] or 0),
                      float(col["low"][i] or 0), float(col["close"][i]), float(col["volume"][i] or 0),
                      float(col["oi"][i] or 0), None if col["iv"][i] is None else float(col["iv"][i]),
                      None if col["spot"][i] is None else float(col["spot"][i]))
    return out


def load_day(day: date, code: int = 1, offsets=OFFSETS, root: Path = ROOT,
             registration_sha256: str | None = None) -> dict[str, dict[tuple[int, str], Bar]]:
    """HH:MM -> {(offset, CALL/PUT): Bar} for one session. Held-back sessions need a bound registration."""
    if day > HOLDOUT_TO:
        raise Holdout(f"{day} is after {HOLDOUT_TO}: forward data, not for historical tests")
    if day > EXPLORE_TO:
        if not registration_sha256:
            raise Holdout(f"{day} is held back for registered tests (exploration ends {EXPLORE_TO})")
        caller = inspect.stack()[1]
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT.open("a") as fh:
            fh.write(json.dumps({"at": datetime.now(IST).isoformat(timespec="seconds"), "day": day.isoformat(),
                                 "registration_sha256": registration_sha256,
                                 "caller": f"{Path(caller.filename).name}:{caller.lineno}"}) + "\n")
    out: dict[str, dict[tuple[int, str], Bar]] = {}
    for off in offsets:
        for right in ("CALL", "PUT"):
            folder = root / f"code{code}" / series_name(off, right)
            bars = _bars(folder / f"{day.isoformat()}.json.gz", right, day)
            if not bars:                                 # the day before's reply also holds this day
                prev = day - timedelta(days=1)
                while prev.weekday() >= 5:
                    prev -= timedelta(days=1)
                bars = _bars(folder / f"{prev.isoformat()}.json.gz", right, day)
            for hm, b in bars.items():
                out.setdefault(hm, {})[(off, right)] = b
    return dict(sorted(out.items()))


def at_strike(day_bars: dict[str, dict[tuple[int, str], Bar]], hm: str, strike: float, right: str) -> Bar | None:
    """The bar of the fixed strike at minute hm, whichever ATM offset carried it then."""
    for (off, r), b in day_bars.get(hm, {}).items():
        if r == right and b.strike == strike:
            return b
    return None


def sessions(root: Path = ROOT, code: int = 1) -> list[date]:
    """Weekdays with a stored ATM call reply, in order (holidays come back empty and are dropped later)."""
    folder = root / f"code{code}" / series_name(0, "CALL")
    return sorted(date.fromisoformat(p.name[:10]) for p in folder.glob("*.json.gz"))
