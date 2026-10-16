"""
Replay: the paper engine walked through history, one session at a time.

The backtest in market/backtest.py asks "does the score predict?" one trade at
a time, with no portfolio. Replay asks the question that comes after: "what
would THIS SYSTEM have done?" — same scan, same sizing on net loss, same risk
limits and correlation check, same kill switch, same paper fill rules, same
charges — because it is the same code. Only the provider changes: it serves
history truncated at each replay date, so nothing after that date is visible.

Replay results are research, not evidence for Gate 2. They are journaled with
mode "replay" in their own directory, and the gate counts only "paper".
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd

from market.config import IST
from market.providers.base import Bars, DataProvider, DataUnavailable
from market.scan import bar_date
from market.universe import BENCHMARK


class ReplayProvider(DataProvider):
    supports_intraday = False
    supports_options = False

    def __init__(self, inner: DataProvider, symbols: list[str], lookback: int = 800):
        self.name = f"REPLAY of {inner.name} — historical, NOT live"
        self.frames: dict[str, pd.DataFrame] = {}
        self.dates: dict[str, np.ndarray] = {}
        self.errors: dict[str, str] = {}
        self.asof: date | None = None
        for s in list(dict.fromkeys(list(symbols) + [BENCHMARK])):
            try:
                df = inner.get_bars(s, "1d", lookback).df
            except DataUnavailable as exc:
                self.errors[s] = str(exc)
                continue
            self.frames[s] = df
            self.dates[s] = np.array([bar_date(ts) for ts in df.index])

    def session_dates(self) -> list[date]:
        key = BENCHMARK if BENCHMARK in self.dates else next(iter(self.dates), None)
        return list(self.dates[key]) if key else []

    def set_asof(self, d: date) -> None:
        self.asof = d

    def get_bars(self, symbol: str, timeframe: str = "1d", lookback: int = 250) -> Bars:
        if symbol not in self.frames:
            raise DataUnavailable(self.errors.get(symbol, f"{symbol}: not in replay set"))
        if self.asof is None:
            raise DataUnavailable("replay date not set")
        n = int(np.searchsorted(self.dates[symbol], self.asof, side="right"))
        if n == 0:
            raise DataUnavailable(f"{symbol}: no history on or before {self.asof}")
        df = self.frames[symbol].iloc[max(0, n - lookback):n]
        return Bars(
            symbol=symbol, df=df, timeframe="1d", freshness="historical", source=self.name,
            fetched_at=datetime.combine(self.asof, time(16, 0), IST),
        )


def run_replay(engine_factory, provider: ReplayProvider, start: date | None, end: date | None,
               progress=None) -> list:
    """Run one engine cycle per session date in [start, end]. Returns the cycle reports."""
    reports = []
    for d in provider.session_dates():
        if (start and d < start) or (end and d > end):
            continue
        provider.set_asof(d)
        rep = engine_factory().run_cycle(now=datetime.combine(d, time(16, 15), IST))
        reports.append(rep)
        if progress:
            progress(rep)
    return reports


def replay_summary(book, journal_path: Path) -> dict:
    closed = book.closed()
    net = [t.net_r_multiple for t in closed if t.net_r_multiple is not None]
    marks = [book.equity_marks[k] for k in sorted(book.equity_marks)]
    peak, worst = 0.0, 0.0
    for e in marks:
        peak = max(peak, e)
        if peak:
            worst = min(worst, (e / peak - 1) * 100)
    exits: dict[str, int] = {}
    for t in closed:
        for leg in t.exits:
            exits[leg.reason] = exits.get(leg.reason, 0) + 1
    return {
        "sessions": len(marks),
        "closed_trades": len(closed),
        "still_open": len([t for t in book.active() if t.status != "PENDING_ENTRY"]),
        "win_rate_pct": round(sum(1 for x in net if x > 0) / len(net) * 100, 1) if net else None,
        "expectancy_net_r": round(sum(net) / len(net), 3) if net else None,
        "realised_net": round(book.realised_net, 2),
        "charges_paid": round(sum(t.charges or 0 for t in closed), 2),
        "final_equity": round(book.equity(), 2),
        "return_pct": round((book.equity() / book.starting_equity - 1) * 100, 2),
        "max_drawdown_pct": round(worst, 2),
        "exits_by_reason": exits,
        "journal": str(journal_path),
    }
