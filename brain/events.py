"""
The events calendar: days that move markets, so every strategy and forecast can see them coming and
research can later ask whether they behave differently on them.

Two kinds of event:

  dated      data/events.csv: central-bank decisions, inflation releases, the budget, rule changes, and the
             project's own deadlines. Every row names its source and a status: confirmed (an official
             schedule), expected (a custom or a schedule that may be revised), to-check (reported, not yet
             read in the official circular), lab (our own reminder). Times are kept in the event's own
             time zone and shown in IST, so summer time abroad is handled.
  computed   NSE's index F&O expiries (NIFTY weekly on Tuesday, the monthly on the last Tuesday; BSE's
             SENSEX weekly on Thursday: the rule since 1 Sep 2025), moved to the previous trading day when
             the day is a holiday; and NSE's trading holidays, from data/nse_holidays.json (2026 verified
             against NSE's circulars on 27 Nov 2026). BSE's holidays are taken to be NSE's.

Nothing here is guessed: a date not yet published (NSE's 2027 holidays, the Budget's exact day) is either
absent or marked expected.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
EVENTS = REPO / "data" / "events.csv"
IST = ZoneInfo("Asia/Kolkata")
NSE_EXPIRY, BSE_EXPIRY = 1, 3                  # weekday numbers: Tuesday, Thursday


@dataclass(frozen=True)
class Event:
    at: datetime                               # IST; for an all-day event, 00:00 IST of its day
    all_day: bool
    market: str                                # IN, US, LAB, ...
    event: str
    importance: str                            # high, medium, low
    status: str                                # confirmed, expected, to-check, lab, computed
    source: str

    @property
    def day(self) -> date:
        return self.at.date()


def load(path: Path | None = None) -> list[Event]:
    path = path or EVENTS
    if not path.exists():
        return []
    out = []
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            tz = ZoneInfo(r["tz"] or "Asia/Kolkata")
            when = r["when"].strip()
            if " " in when:
                local = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
                at, all_day = local.astimezone(IST), False
            else:
                at, all_day = datetime.combine(date.fromisoformat(when), time(0, 0), IST), True
            out.append(Event(at, all_day, r["market"], r["event"], r["importance"], r["status"], r["source"]))
    return out


def _trading(d: date, cal) -> bool:
    return cal.is_trading_day(d) if cal is not None else d.weekday() < 5


def _expiry_on(d: date, cal) -> date:
    """The rule's day, or the trading day before it when that day is a holiday."""
    while not _trading(d, cal):
        d -= timedelta(days=1)
    return d


def expiries(start: date, end: date, cal=None) -> list[Event]:
    out = []
    d = start - timedelta(days=7)
    while d <= end + timedelta(days=7):
        if d.weekday() == NSE_EXPIRY:
            nxt = d + timedelta(days=7)
            monthly = nxt.month != d.month
            e = _expiry_on(d, cal)
            if start <= e <= end:
                what = "NIFTY monthly expiry (and the weekly)" if monthly else "NIFTY weekly expiry"
                if e != d:
                    what += f", moved from {d:%a %d %b} (a holiday)"
                out.append(Event(datetime.combine(e, time(15, 30), IST), False, "IN", what, "medium" if monthly else "low",
                                 "computed", "NSE: index options expire on Tuesday since 1 Sep 2025 (SEBI)"))
        if d.weekday() == BSE_EXPIRY:
            e = _expiry_on(d, cal)
            if start <= e <= end:
                what = "SENSEX weekly expiry (BSE)" + (f", moved from {d:%a %d %b} (a holiday)" if e != d else "")
                out.append(Event(datetime.combine(e, time(15, 30), IST), False, "IN", what, "low", "computed",
                                 "BSE: index options expire on Thursday since 1 Sep 2025 (SEBI); BSE holidays taken as NSE's"))
        d += timedelta(days=1)
    return out


def holidays(start: date, end: date, cal=None) -> list[Event]:
    if cal is None:
        return []
    out = []
    for iso, name in sorted(cal.holidays.items()):
        d = date.fromisoformat(iso)
        if start <= d <= end:
            out.append(Event(datetime.combine(d, time(0, 0), IST), True, "IN", f"NSE closed: {name}", "medium",
                             "confirmed", f"data/nse_holidays.json ({cal.source})"))
    return out


def upcoming(now: datetime | None = None, days: int = 21, cal=None, path: Path | None = None) -> list[Event]:
    """Every event from now (today's all-day events included) to `days` ahead, in time order."""
    now = (now or datetime.now(IST)).astimezone(IST)
    start, end = now.date(), now.date() + timedelta(days=days)
    every = load(path) + expiries(start, end, cal) + holidays(start, end, cal)
    keep = [e for e in every if start <= e.day <= end and (e.all_day or e.at >= now - timedelta(hours=2))]
    return sorted(keep, key=lambda e: (e.at, e.market, e.event))


def on(day: date, cal=None, path: Path | None = None) -> list[Event]:
    """The events of one day, for research: was this session a policy day, an expiry, a data release?"""
    every = load(path) + expiries(day, day, cal) + holidays(day, day, cal)
    return [e for e in every if e.day == day]
