"""
The brain's inputs, all from public NSE files already on disk (no login, no paid feed):

  state/nse_archive/index_close/YEAR/ind_close_all_DDMMYYYY.csv        every NSE index, each day since 2012
  state/nse_archive/participant_oi/YEAR/fao_participant_oi_DDMMYYYY.csv  FII, DII, pro and client positions
  state/market_context/raw/DAY/index_close.csv, participant_oi.csv     the same two, as recorded each day

Read into plain dictionaries: index name (lower case) -> {day: Bar}, and {day: FII long share}.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "state" / "nse_archive"
DAILY = REPO / "state" / "market_context" / "raw"


@dataclass(frozen=True)
class Bar:
    open: float | None
    high: float | None
    low: float | None
    close: float
    pe: float | None


def _num(text) -> float | None:
    try:
        v = float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def parse_index_file(raw: bytes) -> dict[str, Bar]:
    """One all-index file: lower-case index name -> Bar. Rows without a close are dropped."""
    out = {}
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))):
        r = {(k or "").strip().lower(): v for k, v in row.items()}
        name = (r.get("index name") or "").strip().lower()
        close = _num(r.get("closing index value"))
        if not name or close is None or close <= 0:
            continue
        out[name] = Bar(_num(r.get("open index value")), _num(r.get("high index value")),
                        _num(r.get("low index value")), close, _num(r.get("p/e")))
    return out


def _files(kind: str, archive: Path, daily: Path) -> dict[date, Path]:
    """day -> file, the archive first and the daily recorder's copy for days the archive lacks."""
    out: dict[date, Path] = {}
    for p in sorted((archive / kind).glob("*/*.csv")):
        try:
            out[datetime.strptime(p.stem.rsplit("_", 1)[1], "%d%m%Y").date()] = p
        except ValueError:
            continue
    for p in sorted(daily.glob(f"*/{kind}.csv")):
        try:
            out.setdefault(date.fromisoformat(p.parent.name), p)
        except ValueError:
            continue
    return dict(sorted(out.items()))


def load_indexes(archive: Path = ARCHIVE, daily: Path = DAILY) -> dict[str, dict[date, Bar]]:
    out: dict[str, dict[date, Bar]] = {}
    for d, p in _files("index_close", archive, daily).items():
        for name, bar in parse_index_file(p.read_bytes()).items():
            out.setdefault(name, {})[d] = bar
    return out


def load_fii_long_share(archive: Path = ARCHIVE, daily: Path = DAILY) -> dict[date, float]:
    """FII long contracts in index futures as a share of their long plus short."""
    from trading.fno.context import Unavailable, parse_participant_oi
    out = {}
    for d, p in _files("participant_oi", archive, daily).items():
        try:
            f = parse_participant_oi(p.read_bytes(), d).get("FII", {})
        except Unavailable:
            continue
        lo, sh = f.get("Future Index Long"), f.get("Future Index Short")
        if lo is not None and sh is not None and lo + sh > 0:
            out[d] = lo / (lo + sh)
    return out
