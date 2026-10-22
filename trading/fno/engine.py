"""
The F&O paper engine for strategy V (research/PREREGISTRATION_3.md).

One pass does two things, in this order:

  1. settlement: a trade whose expiry has closed waits for the official index
     close, cross-checked against a second source, and is settled at it (3, 10);
  2. entry: in the entry window of an eligible session, with no V trade open
     and none yet for this expiry, it plans the condor from live quotes and, if
     every check passes, books a paper fill at the touch (4-8).

Every decision is journaled to a hash-chained journal of its own, with the feed
named. Synthetic data never reaches it: a mock feed is refused unless the state
directory is a mock one. The engine places no order anywhere.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from market.calendar import TradingCalendar, load_calendar
from market.charges import FNO_SCHEDULES, FNO_ZERODHA, FnoSchedule
from market.config import IST
from market.providers.base import DataUnavailable
from trading.engine import CycleReport
from trading.fno.book import FnoBook, FnoBookStore, exclusive
from trading.fno.feeds import FnoFeed
from trading.fno.model import OptionContract
from trading.fno.strategy_v import (REGISTERED, Skip, VParams, flatten_fill, in_entry_window, ineligible,
                                    plan_entry, sessions_after, settle)
from trading.journal import Journal
from trading.risk import KillSwitch

MOCK_DIR_SUFFIX = "_mock"


class FeedRefused(RuntimeError):
    pass


def yahoo_close(underlying: str, day: date) -> float | None:
    """The second, independent source for the settlement value: Yahoo's ^NSEI close (EOD, delayed)."""
    if underlying != "NIFTY":
        return None
    try:
        import yfinance as yf
        raw = yf.Ticker("^NSEI").history(start=day.isoformat(), end=(day + timedelta(days=1)).isoformat(),
                                         interval="1d", auto_adjust=False)
    except Exception:
        return None
    if raw is None or raw.empty:
        return None
    rows = raw[[ts.date() == day for ts in raw.index]]
    return float(rows["Close"].iloc[-1]) if len(rows) else None


class FnoEngine:
    def __init__(self, feed: FnoFeed, state_dir: Path, *, params: VParams = REGISTERED,
                 calendar: TradingCalendar | None = None, sched: FnoSchedule | None = None,
                 second_close: Callable[[str, date], float | None] | None = None,
                 now_fn: Callable[[], datetime] | None = None, notify: Callable[[str], None] | None = None):
        self.state_dir = Path(state_dir)
        is_mock_dir = self.state_dir.name.endswith(MOCK_DIR_SUFFIX)
        if feed.is_mock != is_mock_dir:
            raise FeedRefused(
                "synthetic data never enters the paper journal: a mock feed runs only in a "
                f"'*{MOCK_DIR_SUFFIX}' state directory, and a real feed never runs in one")
        self.feed = feed
        self.p = params
        self.cal = calendar or load_calendar()
        self.sched = sched or FNO_SCHEDULES.get(feed.key, FNO_ZERODHA)
        self.second_close = second_close or yahoo_close
        self.now_fn = now_fn or (lambda: datetime.now(IST))
        self.notify = notify or (lambda msg: None)
        self.journal = Journal(self.state_dir / "journal.jsonl", "fno-mock" if feed.is_mock else "fno-paper")
        self.store = FnoBookStore(self.state_dir / "book.json")
        self.kill = KillSwitch([self.state_dir / "KILL", self.state_dir.parent / "KILL"])
        self._master = None          # (date, ContractMaster): one dump per trading day

    # -- setup ---------------------------------------------------------------

    def init(self, equity: float) -> FnoBook:
        now = self.now_fn()
        book = self.store.create(equity, self.feed.key, now.isoformat())
        self.journal.append("FNO_INIT", asof=now.date().isoformat(), starting_equity=book.starting_equity,
                            feed=self.feed.key, feed_name=self.feed.name, params=self._params(),
                            fee_card=self.sched.name, fee_card_verified_on=self.sched.verified_on,
                            calendar=self.cal.status())
        return book

    def _params(self) -> dict:
        return {k: (v.isoformat() if isinstance(v, time) else v) for k, v in self.p.__dict__.items()}

    def _report(self, now: datetime) -> CycleReport:
        return CycleReport(mode=self.journal.mode, started=now.isoformat(timespec="seconds"),
                           asof=now.date().isoformat())

    def _bound(self, book: FnoBook, now: datetime, switch_reason: str | None) -> None:
        """A book stays with the feed it started on unless the operator says why it moves."""
        if book.feed == self.feed.key:
            return
        if not switch_reason:
            raise FeedRefused(f"this book reads {book.feed}; pass a reason to switch it to {self.feed.key}")
        self.journal.append("FNO_FEED_SWITCH", asof=now.date().isoformat(), old=book.feed,
                            new=self.feed.key, reason=switch_reason)
        book.feed = self.feed.key

    # -- one pass -----------------------------------------------------------

    def run_pass(self, now: datetime | None = None, switch_reason: str | None = None) -> CycleReport:
        now = now or self.now_fn()
        rep = self._report(now)
        with exclusive(self.state_dir):
            book = self.store.load()
            self._bound(book, now, switch_reason)
            self._settle_all(book, now, rep)
            self._close_cycles(book, now)
            self._entry(book, now, rep)
            self.store.save(book)
        rep.equity = book.starting_equity + book.realised()
        rep.kill = self.kill.engaged()
        if not rep.decision:
            rep.decision = "nothing to do at this pass"
        return rep

    # -- settlement (3, 10) -------------------------------------------------

    def _settle_all(self, book: FnoBook, now: datetime, rep: CycleReport) -> None:
        today = now.date()
        for t in book.trades:
            if t["status"] == "OPEN":
                exp = date.fromisoformat(t["expiry"])
                if today > exp or (today == exp and now.time().replace(tzinfo=None) >= self.p.settle_after):
                    t["status"] = "AWAITING_SETTLEMENT"
                    self.journal.append("FNO_EXPIRED", asof=today.isoformat(), trade_id=t["id"], expiry=t["expiry"])
            if t["status"] == "AWAITING_SETTLEMENT":
                self._try_settle(book, t, now, rep)

    def _settlement_value(self, book: FnoBook, t: dict, waited: int) -> tuple[float | None, str, list[str]]:
        exp = date.fromisoformat(t["expiry"])
        manual = book.manual_settlements.get(t["expiry"])
        if manual:
            return float(manual["value"]), f"manual: {manual['source']}", []
        try:
            a = self.feed.index_close(self.p.underlying, exp)
        except Exception:
            a = None
        try:
            b = self.second_close(self.p.underlying, exp)
        except Exception:
            b = None
        if a and b:
            if abs(a - b) / b <= self.p.cross_check_tol:
                return a, f"{self.feed.key} close, cross-checked against Yahoo ^NSEI ({b})", []
            return None, f"{self.feed.key} close {a} and Yahoo ^NSEI {b} differ by more than 0.05%", []
        if a and not b:
            if waited >= self.p.second_source_wait:
                return a, f"{self.feed.key} close", [f"second source missing after {waited} trading days"]
            return None, f"{self.feed.key} close {a}; waiting for the second source", []
        if b and not a:
            return None, f"only the second source has a close ({b}); enter NSE's value by hand", []
        return None, "no close from either source yet", []

    def _try_settle(self, book: FnoBook, t: dict, now: datetime, rep: CycleReport) -> None:
        today = now.date()
        exp = date.fromisoformat(t["expiry"])
        waited = sessions_after(exp, today, self.cal)
        value, source, flags = self._settlement_value(book, t, waited)
        if value is None:
            if waited >= self.p.worst_case_after:
                net, m = -t["max_loss"], t["max_loss"]
                t.update(status="SETTLED", net=net, R=net / m, closed_at=now.isoformat(),
                         settlement={"value": None, "source": f"worst case: no settlement value after {waited} "
                                     f"trading days ({source})", "net": net, "R": net / m})
                self._record_close(book, t, now, rep, "FNO_SETTLED")
                return
            if t.get("pending_noted") != today.isoformat():
                t["pending_noted"] = today.isoformat()
                self.journal.append("FNO_SETTLEMENT_PENDING", asof=today.isoformat(), trade_id=t["id"],
                                    expiry=t["expiry"], why=source, trading_days_waited=waited)
                rep.alerts.append(f"V {t['expiry']}: settlement waiting — {source}")
            return
        result = settle(t, value, self.sched)
        result.update(source=source, flags=flags, settled_at=now.isoformat())
        t.update(status="SETTLED", net=result["net"], R=result["R"], closed_at=now.isoformat(), settlement=result)
        self._record_close(book, t, now, rep, "FNO_SETTLED")

    def _record_close(self, book: FnoBook, t: dict, now: datetime, rep: CycleReport, kind: str) -> None:
        today = now.date().isoformat()
        self.journal.append(kind, asof=today, trade_id=t["id"], expiry=t["expiry"], net=t["net"], R=t["R"],
                            max_loss=t["max_loss"], detail=t.get("settlement") or t.get("flatten"))
        msg = (f"V {t['expiry']} {'settled' if kind == 'FNO_SETTLED' else 'flattened'}: net Rs{t['net']:,.0f} "
               f"({t['R']:+.2f}R); paper equity Rs{book.starting_equity + book.realised():,.0f}")
        rep.actions.append(msg)
        rep.decision = msg
        self.notify(f"[F&O PAPER] {msg}")
        floor = -self.p.harm_stop_frac * book.starting_equity
        if book.halted is None and book.realised() <= floor:
            book.halted = f"harm stop: realised Rs{book.realised():,.0f} <= Rs{floor:,.0f}"
            self.journal.append("FNO_HARM_STOP", asof=today, realised=book.realised(), floor=floor)
            rep.urgent.append(f"V FAILED — {book.halted}. No further entries.")
            rep.alerts.append(rep.urgent[-1])

    # -- cycles: what happened to each expiry --------------------------------

    def _close_cycles(self, book: FnoBook, now: datetime) -> None:
        today = now.date()
        for e, c in book.cycles.items():
            if c.get("outcome") is None and date.fromisoformat(e) <= today and not book.trade_for(e):
                c["outcome"] = "no trade"
                self.journal.append("FNO_CYCLE_NO_TRADE", asof=today.isoformat(), expiry=e,
                                    passes=c.get("passes", 0), last_reason=c.get("last_reason")
                                    or "the runner made no entry pass on an eligible session")

    def _cycle(self, book: FnoBook, e: date, today: date) -> dict:
        c = book.cycles.setdefault(e.isoformat(), {"first_seen": today.isoformat(), "outcome": None, "passes": 0})
        return c

    # -- entry (4-8) ----------------------------------------------------------

    def _master_for(self, today: date):
        if self._master is None or self._master[0] != today:
            self._master = (today, self.feed.contract_master(self.p.underlying))
        return self._master[1]

    def _entry(self, book: FnoBook, now: datetime, rep: CycleReport) -> None:
        today = now.date()
        if book.halted or not self.cal.is_trading_day(today) or not in_entry_window(now, self.p):
            return
        if book.open_trades():
            return
        try:
            master = self._master_for(today)
        except DataUnavailable as exc:
            self._data_problem(rep, today, "contract master", exc)
            return
        e = master.nearest_expiry(today)
        if e is None:
            self.journal.append("FNO_NO_TRADE", asof=today.isoformat(), code="no_expiry",
                                detail="the contract master lists no expiry on or after today")
            return
        if book.trade_for(e.isoformat()) or ineligible(today, e, self.cal, self.p):
            return
        cycle = self._cycle(book, e, today)
        cycle["passes"] += 1
        kill = self.kill.engaged()
        if kill:
            cycle["last_reason"] = f"kill switch: {kill}"
            self.journal.append("FNO_ENTRY_BLOCKED", asof=today.isoformat(), expiry=e.isoformat(), kill=kill)
            rep.decision = f"no entry: kill switch engaged ({kill})"
            return
        try:
            index = self.feed.index_quote(self.p.underlying)
            plan = plan_entry(master, e, index, self.feed.quotes, now, book.equity_for_sizing(), self.sched, self.p)
        except DataUnavailable as exc:
            cycle["last_reason"] = f"data unavailable: {exc}"
            self._data_problem(rep, today, "quotes", exc)
            return
        if isinstance(plan, Skip):
            cycle["last_reason"] = f"{plan.code}: {plan.detail}"
            self.journal.append("FNO_NO_TRADE", asof=today.isoformat(), expiry=e.isoformat(), code=plan.code,
                                detail=plan.detail, data=plan.data, pass_at=now.isoformat())
            rep.decision = f"no trade at this pass ({plan.code}): {plan.detail}"
            return
        trade = plan.as_dict()
        trade.update(id=f"V-{e.isoformat()}", status="OPEN", opened_at=now.isoformat(), feed=self.feed.key,
                     fee_card=self.sched.name, calendar=self.cal.status(), master_source=master.source,
                     margin=self.feed.basket_margin(plan.legs, plan.qty),
                     broker_charges=self.feed.broker_charges(plan.legs, plan.qty), net=None, R=None)
        self.journal.append("FNO_ENTRY", asof=today.isoformat(), trade=trade)
        book.trades.append(trade)
        cycle["outcome"] = "traded"
        legs = ", ".join(f"{'-' if g['side'] == 'SELL' else '+'}{g['strike']:g}{g['right']}@{g['price']:.2f}"
                         for g in trade["legs"])
        msg = (f"V {e} opened: {plan.lots} lot(s) x {plan.lot_size}, credit {plan.credit:.2f} pts, "
               f"max loss Rs{plan.max_loss:,.0f} [{legs}]")
        rep.actions.append(msg)
        rep.decision = msg
        self.notify(f"[F&O PAPER] {msg}")

    def _data_problem(self, rep: CycleReport, today: date, stage: str, exc: Exception) -> None:
        self.journal.append("FNO_DATA_UNAVAILABLE", asof=today.isoformat(), stage=stage, error=str(exc)[:500])
        rep.alerts.append(f"F&O {stage}: data unavailable — {str(exc)[:200]}")
        rep.decision = f"no trade: {stage} unavailable"

    # -- operator actions -----------------------------------------------------

    def flatten(self, reason: str, now: datetime | None = None) -> CycleReport:
        """Off-rule exit at the touch. The trade still counts (section 10)."""
        now = now or self.now_fn()
        rep = self._report(now)
        with exclusive(self.state_dir):
            book = self.store.load()
            opened = book.open_trades()
            if not opened:
                rep.decision = "nothing open to flatten"
                return rep
            t = opened[0]
            contracts = [OptionContract(g["symbol"], self.p.underlying, date.fromisoformat(t["expiry"]),
                                        g["strike"], g["right"], t["lot_size"], 0.05,
                                        exchange=g.get("exchange", "NFO")) for g in t["legs"]]
            try:
                fill = flatten_fill(t, self.feed.quotes(contracts), now, self.sched, self.p)
            except DataUnavailable as exc:
                fill = Skip("flatten_data", str(exc))
            if isinstance(fill, Skip):
                self.journal.append("FNO_FLATTEN_REFUSED", asof=now.date().isoformat(), trade_id=t["id"],
                                    reason=reason, why=fill.detail)
                rep.decision = f"flatten refused: {fill.detail}"
                return rep
            fill.update(reason=reason, off_rule=True, at=now.isoformat())
            t.update(status="FLATTENED", net=fill["net"], R=fill["R"], closed_at=now.isoformat(), flatten=fill)
            self._record_close(book, t, now, rep, "FNO_FLATTENED")
            self.store.save(book)
        return rep

    def set_manual_settlement(self, expiry: str, value: float, source: str) -> None:
        record_manual_settlement(self.state_dir, expiry, value, source, self.now_fn())


def record_manual_settlement(state_dir: Path, expiry: str, value: float, source: str,
                             now: datetime | None = None) -> None:
    """The closing value NSE published, entered by hand with its source (section 3). Needs no feed."""
    now = now or datetime.now(IST)
    state_dir = Path(state_dir)
    store = FnoBookStore(state_dir / "book.json")
    journal = Journal(state_dir / "journal.jsonl",
                      "fno-mock" if state_dir.name.endswith(MOCK_DIR_SUFFIX) else "fno-paper")
    with exclusive(state_dir):
        book = store.load()
        t = book.trade_for(expiry)
        if t is None or t["status"] != "AWAITING_SETTLEMENT":
            raise ValueError(f"no trade for {expiry} is waiting for a settlement value")
        if not (value > 0) or not source.strip():
            raise ValueError("a positive value and its source are both required")
        book.manual_settlements[expiry] = {"value": value, "source": source, "entered_at": now.isoformat()}
        journal.append("FNO_MANUAL_SETTLEMENT_VALUE", asof=now.date().isoformat(), expiry=expiry,
                       value=value, source=source)
        store.save(book)
