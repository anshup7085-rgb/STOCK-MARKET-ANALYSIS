"""
NSE trading calendar.

Weekends are arithmetic. Holidays are data, and DATA_SOURCES.md governs data:
the list lives in `data/nse_holidays.json` with a named source and a
`verified_on` date. Nothing is guessed: dates enter it only with a named source,
an unverified list reports itself as UNVERIFIED, and an empty one is reported as
such everywhere it is used, so "no holiday known" is never mistaken for "no
holiday".

Fill it from the exchange's own circular (nseindia.com -> Circulars -> "Trading
holidays for the calendar year"), then set `verified_on`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "nse_holidays.json"


@dataclass
class TradingCalendar:
    holidays: dict[str, str] = field(default_factory=dict)   # "YYYY-MM-DD" -> name
    source: str | None = None
    verified_on: str | None = None

    @property
    def loaded(self) -> bool:
        return bool(self.holidays)

    @property
    def verified(self) -> bool:
        return self.loaded and self.verified_on is not None

    def is_holiday(self, d: date) -> bool:
        return d.isoformat() in self.holidays

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and not self.is_holiday(d)

    def status(self) -> str:
        if not self.loaded:
            return (
                "NOT LOADED — data/nse_holidays.json has no entries; holidays are not "
                "checked. Fill it from the NSE circular."
            )
        tag = f"verified {self.verified_on}" if self.verified_on else "UNVERIFIED"
        return f"{len(self.holidays)} holidays from {self.source or 'unnamed source'} ({tag})"


def load_calendar(path: str | Path | None = None) -> TradingCalendar:
    """Read the holiday file. A missing or unreadable file is an empty calendar."""
    p = Path(path or os.getenv("NSE_HOLIDAYS_FILE") or DEFAULT_PATH)
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return TradingCalendar()
    hol = raw.get("holidays") or {}
    if not isinstance(hol, dict):
        return TradingCalendar()
    return TradingCalendar(
        holidays={str(k): str(v) for k, v in hol.items()},
        source=raw.get("source"),
        verified_on=raw.get("verified_on"),
    )
