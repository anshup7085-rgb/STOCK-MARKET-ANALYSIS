"""
The F&O paper engine for strategy V (research/PREREGISTRATION_3.md).

Each pass rebuilds V's state from the journal (trading/fno/state.py), then:

  1. reads the day's contract master once (the first successful read of the day
     governs every pass that day, in any process), learns the weekly expiries and
     follows any expiry the exchange moves;
  2. settles every trade whose expiry has closed, at NSE's final settlement price;
  3. closes each cycle whose entry window is over: no trade, or MISSED;
  4. at an entry slot, and only from the runner, plans the condor from live quotes
     and books a paper fill at the touch if every check passes.

Every decision is journaled to a hash-chained journal of its own, with the feed
named on every record. The journal is the record: nothing here keeps a copy of a
trade that could disagree with it. book.json is a snapshot for reading only.
The engine places no order anywhere, and synthetic data never reaches the paper
journal: a mock feed runs only in a '*_mock' state directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from market.calendar import DEFAULT_PATH as HOLIDAYS_PATH
from market.calendar import TradingCalendar, load_calendar
from market.charges import FNO_SCHEDULES, FNO_ZERODHA, FnoSchedule
from market.config import IST
from market.providers.base import DataUnavailable
from trading.engine import CycleReport
from trading.fno import strategy_v as sv
from trading.fno.book import exclusive
from trading.fno.feeds import FnoFeed, nse_fsp
from trading.fno.model import ContractMaster, OptionContract
from trading.fno.state import State, contiguous_run
from trading.fno.strategy_v import REGISTERED, Skip, VParams, plan_entry, sessions_after, settle
from trading.journal import Journal
from trading.risk import KillSwitch

MOCK_DIR_SUFFIX = "_mock"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PREREG = REPO_ROOT / "research" / "PREREGISTRATION_3.md"
DRAFT_MARK = "DRAFT — not yet binding"
BOUND_PATHS = ("trading/fno", "research/fno_stats.py", "research/PREREGISTRATION_3.md",
               "market/charges.py", "market/calendar.py", "data/nse_holidays.json")


class FeedRefused(RuntimeError):
    pass


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def registration() -> dict:
    """What a real-feed record carries: the text's hash, the code's commit, and whether either is unbound."""
    out = {"prereg_sha256": _sha256(PREREG), "calendar_sha256": _sha256(HOLIDAYS_PATH),
           "registered_sha256": sv.REGISTRATION_SHA256}
    try:
        out["git_commit"] = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                                           capture_output=True, text=True, timeout=10).stdout.strip() or None
        dirty = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--", *BOUND_PATHS],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        out["uncommitted"] = dirty.splitlines()[:10]
    except (OSError, subprocess.SubprocessError):
        out["git_commit"], out["uncommitted"] = None, ["git unavailable"]
    try:
        out["draft"] = DRAFT_MARK in PREREG.read_text()
    except OSError:
        out["draft"] = True
    return out


def require_binding() -> dict:
    """Section 17: no real-feed record before the registration binds, and none from code that differs from a commit."""
    reg = registration()
    why = []
    if reg["draft"]:
        why.append("research/PREREGISTRATION_3.md is still a draft")
    if not reg["registered_sha256"] or reg["prereg_sha256"] != reg["registered_sha256"]:
        why.append("research/PREREGISTRATION_3.md does not match the registered hash")
    if not reg["git_commit"]:
        why.append("the code is not in a git checkout")
    if reg["uncommitted"]:
        why.append(f"uncommitted changes in {', '.join(reg['uncommitted'])}")
    if os.getenv("NSE_HOLIDAYS_FILE"):
        why.append("NSE_HOLIDAYS_FILE is set; V reads only the committed data/nse_holidays.json")
    if why:
        raise FeedRefused("V's paper test cannot run: " + "; ".join(why))
    return reg


def yahoo_close(underlying: str, day: date) -> float | None:
    """Yahoo's ^NSEI close for `day` (EOD, delayed): a cross-check, never the settlement price on its own."""
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


def _master_to_json(m: ContractMaster) -> str:
    return json.dumps({"source": m.source, "fetched_at": m.fetched_at.isoformat(),
                       "contracts": [c.as_dict() for c in m.contracts]}, sort_keys=True)


def _master_from_json(raw: str) -> ContractMaster:
    d = json.loads(raw)
    cs = [OptionContract(**{**c, "expiry": date.fromisoformat(c["expiry"])}) for c in d["contracts"]]
    return ContractMaster(cs, d["source"], datetime.fromisoformat(d["fetched_at"]))


class FnoEngine:
    def __init__(self, feed: FnoFeed, state_dir: Path, *, params: VParams = REGISTERED,
                 calendar: TradingCalendar | None = None, sched: FnoSchedule | None = None,
                 fsp_source: Callable[[str, date], tuple[float | None, dict]] | None = None,
                 second_close: Callable[[str, date], float | None] | None = None,
                 now_fn: Callable[[], datetime] | None = None, notify: Callable[[str], None] | None = None):
        self.state_dir = Path(state_dir)
        is_mock_dir = self.state_dir.name.endswith(MOCK_DIR_SUFFIX)
        if feed.is_mock != is_mock_dir:
            raise FeedRefused(
                "synthetic data never enters the paper journal: a mock feed runs only in a "
                f"'*{MOCK_DIR_SUFFIX}' state directory, and a real feed never runs in one")
        self.reg = {} if feed.is_mock else require_binding()
        self.feed = feed
        self.p = params
        self.cal = calendar or load_calendar(HOLIDAYS_PATH)
        self.sched = sched or FNO_SCHEDULES.get(feed.key, FNO_ZERODHA)
        self.fsp_source = fsp_source or nse_fsp
        self.second_close = second_close or yahoo_close
        self.now_fn = now_fn or (lambda: datetime.now(IST))
        self.notify = notify or (lambda msg: None)
        self.mode = "fno-mock" if feed.is_mock else "fno-paper"
        self.journal = Journal(self.state_dir / "journal.jsonl", self.mode)
        self.kill = KillSwitch([self.state_dir / "KILL", self.state_dir.parent / "KILL"])

    # -- journal ------------------------------------------------------------

    def _log(self, kind: str, at: date, **payload) -> dict:
        return self.journal.append(kind, asof=at.isoformat(), feed=self.feed.key, **payload)

    def state(self) -> State:
        return State.from_records(self.journal.records())

    def _head(self) -> str:
        recs = self.journal.records()
        return f"seq {recs[-1]['seq']} {recs[-1]['hash'][:16]}" if recs else "empty"

    # -- setup ---------------------------------------------------------------

    def init(self) -> dict:
        """Start the one paper book: probe the feed first; a feed without two-sided quotes is not used (3)."""
        now = self.now_fn()
        with exclusive(self.state_dir):
            if self.journal.path.exists() and self.journal.records():
                raise FeedRefused(f"{self.journal.path} already holds records; V's paper book is started once")
            probe = self.probe_quotes()
            if not probe["ok"]:
                raise FeedRefused(f"the feed failed the quote probe: {probe['why']}")
            self._log("FNO_INIT", now.date(), starting_equity=self.p.starting_equity, feed_name=self.feed.name,
                      params=self._params(), fee_card=self.sched.name, fee_card_verified_on=self.sched.verified_on,
                      calendar=self.cal.status(), state_dir=str(self.state_dir.resolve()),
                      host=socket.gethostname(), **self.reg)
            self._log("FNO_PROBE", now.date(), **probe)
            self._write_snapshot()
            return probe

    def probe_quotes(self) -> dict:
        """Section 3: best bid, best ask and their quantities, for NIFTY options, before any entry pass."""
        try:
            m = self.feed.contract_master(self.p.underlying)
            e = m.nearest_expiry(self.now_fn().date())
            ks = m.strikes(e, "CE")
            mid = ks[len(ks) // 2]
            cs = [c for c in (m.get(e, mid, "CE"), m.get(e, mid, "PE"), m.get(e, ks[len(ks) // 2 + 4], "CE")) if c]
            qs = self.feed.quotes(cs)
        except Exception as exc:
            return {"ok": False, "why": f"{type(exc).__name__}: {str(exc)[:200]}"}
        rows = [{"symbol": c.symbol, "bid": q.bid is not None, "ask": q.ask is not None,
                 "bid_qty": q.bid_qty is not None, "ask_qty": q.ask_qty is not None,
                 "exchange_ts": q.exchange_ts is not None} for c in cs for q in [qs.get(c.symbol)] if q]
        ok = bool(rows) and all(r["bid"] and r["ask"] for r in rows)
        return {"ok": ok, "why": "" if ok else "no two-sided quote", "contracts": rows,
                "lot_sizes": sorted({c.lot_size for c in m.contracts if c.expiry == e}),
                "nearest_expiry": e.isoformat()}

    def _params(self) -> dict:
        return {k: (v.isoformat() if isinstance(v, (time, date)) else v) for k, v in self.p.__dict__.items()}

    def _report(self, now: datetime) -> CycleReport:
        return CycleReport(mode=self.mode, started=now.isoformat(timespec="seconds"), asof=now.date().isoformat())

    def switch_feed(self, reason: str, failed_sessions: list[str]) -> None:
        """
        Section 3: only after the bound feed failed every entry slot of two
        eligible sessions: each named day has a failed entry pass and no observed one.
        """
        now = self.now_fn()
        with exclusive(self.state_dir):
            st = self.state()
            if st.feed == self.feed.key:
                return
            days = sorted(set(failed_sessions))
            passes = [r for r in self.journal.records() if r["type"] == "FNO_PASS"]
            for d in days:
                on = [r["payload"].get("outcome") for r in passes if r["asof"] == d]
                if not on or "data_unavailable" not in on or {"entry", "no_trade"} & set(on):
                    raise FeedRefused(f"{d}: the journal does not show every entry slot failing for want of data")
            if len(days) != 2:
                raise FeedRefused("name the two eligible sessions on which the bound feed failed")
            self._log("FNO_FEED_SWITCH", now.date(), old=st.feed, new=self.feed.key, reason=reason,
                      failed_sessions=days)

    # -- one pass -----------------------------------------------------------

    def run_pass(self, now: datetime | None = None, slot: time | None = None, final: bool = True) -> tuple[CycleReport, str]:
        """
        `slot` is given only by the runner, for an entry pass. Returns the report and
        a status: "done", or "retry" when an entry pass hit unavailable data and
        `final` is False (the runner tries again inside the slot's window).
        """
        now = now or self.now_fn()
        rep = self._report(now)
        status = "done"
        with exclusive(self.state_dir):
            st = self.state()
            if st.init is None:
                raise FeedRefused("no F&O paper book here: run `fno init` first")
            if st.feed != self.feed.key:
                raise FeedRefused(f"this book reads {st.feed}; see `fno switch-feed`")
            master = self._master_today(st, now, rep)
            st = self.state()
            if master is not None:
                self._follow_moves(st, master, now)
                st = self.state()
            self._settle_all(st, now, rep)
            st = self.state()
            self._close_cycles(st, now)
            st = self.state()
            self._decide(st, now, rep)
            st = self.state()
            if slot is not None and st.verdict is None:
                status = self._entry(st, now, slot, final, master, rep)
            self._write_snapshot()
        if not rep.decision:
            rep.decision = "nothing to do at this pass"
        return rep, status

    # -- the day's contract master (3) ----------------------------------------

    def _master_today(self, st: State, now: datetime, rep: CycleReport) -> ContractMaster | None:
        today = now.date()
        if not self.cal.is_trading_day(today):
            return None
        path = self.state_dir / "master" / f"{today.isoformat()}.json"
        if path.exists():
            return _master_from_json(path.read_text())
        try:
            m = self.feed.contract_master(self.p.underlying)
        except DataUnavailable as exc:
            rep.alerts.append(f"contract master unavailable: {str(exc)[:200]}")
            return None
        raw = _master_to_json(m)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(raw)
        os.replace(tmp, path)
        run = contiguous_run([e for e in m.expiries() if e >= today])
        self._log("FNO_MASTER", today, date=today.isoformat(), sha256=hashlib.sha256(raw.encode()).hexdigest(),
                  weekly_run=[e.isoformat() for e in run], summary=m.summary())
        return m

    def _follow_moves(self, st: State, master: ContractMaster, now: datetime) -> None:
        """Section 4: if the exchange moves an open trade's expiry, the trade follows its contracts."""
        by_symbol = {c.symbol: c for c in master.contracts}
        for t in st.trades.values():
            if t["status"] != "OPEN":
                continue
            moved = {by_symbol[g["symbol"]].expiry for g in t["legs"] if g["symbol"] in by_symbol}
            if len(moved) == 1 and (new := moved.pop()).isoformat() != t["expiry"]:
                self._log("FNO_EXPIRY_MOVED", now.date(), trade_id=t["id"], old=t["expiry"], new=new.isoformat())

    # -- settlement (3, 10) -------------------------------------------------

    def _settle_all(self, st: State, now: datetime, rep: CycleReport) -> None:
        today = now.date()
        for t in list(st.trades.values()):
            exp = date.fromisoformat(t["expiry"])
            if t["status"] == "OPEN" and (today > exp or (today == exp and now.timetz().replace(tzinfo=None) >= self.p.settle_after)):
                self._log("FNO_EXPIRED", today, trade_id=t["id"], expiry=t["expiry"])
                t["status"] = "AWAITING_SETTLEMENT"
            if t["status"] == "AWAITING_SETTLEMENT":
                self._try_settle(st, t, now, rep)

    def _fsp(self, st: State, t: dict, exp: date) -> tuple[float | None, str, dict]:
        """Section 3: NSE's final settlement price, checked against the index close."""
        def safe(fn, *a):
            try:
                return fn(*a)
            except Exception:
                return None
        feed_close = safe(self.feed.index_close, self.p.underlying, exp)
        yahoo = safe(self.second_close, self.p.underlying, exp)
        info = {"feed_close": feed_close, "yahoo_close": yahoo}
        manual = st.manual.get(exp)
        if manual:
            value, source = float(manual["value"]), f"entered by the operator from {manual['source']}"
        else:
            got = safe(self.fsp_source, self.p.underlying, exp) or (None, {"error": "source raised"})
            value, nse_info = got
            info["nse"] = nse_info
            source = f"NSE bhavcopy ({nse_info.get('url', '')})"
            if value is None and exp < self.p.close_rule_until and feed_close and yahoo \
                    and abs(feed_close - yahoo) <= self.p.close_match_points:
                value, source = feed_close, f"{self.feed.key} close, matching Yahoo ^NSEI within 0.10 points"
        if value is None:
            return None, "no final settlement price yet", info
        if feed_close:
            if exp < self.p.close_rule_until and abs(value - feed_close) > self.p.close_match_points:
                return None, f"{source}: {value} differs from the {self.feed.key} close {feed_close} by more than 0.10 points", info
            if abs(value - feed_close) > self.p.fsp_sanity_frac * feed_close:
                return None, f"{source}: {value} is more than 3% from the {self.feed.key} close {feed_close}", info
        else:
            info["flag"] = "no feed close to cross-check"
        return value, source, info

    def _try_settle(self, st: State, t: dict, now: datetime, rep: CycleReport) -> None:
        today = now.date()
        exp = date.fromisoformat(t["expiry"])
        waited = sessions_after(exp, today, self.cal)
        value, source, info = self._fsp(st, t, exp)
        if value is None:
            if waited >= self.p.worst_case_after:
                net = -t["max_loss"]
                self._close(t, now, rep, net, -1.0, {"value": None, "source": f"no final settlement price after "
                                                     f"{waited} trading days ({source})", "net": net, "R": -1.0, **info})
                return
            if today not in st.pending_days.get(t["id"], set()):
                self._log("FNO_SETTLEMENT_PENDING", today, trade_id=t["id"], expiry=t["expiry"], why=source,
                          trading_days_waited=waited, **info)
                rep.alerts.append(f"V {t['expiry']}: settlement waiting — {source}")
            return
        result = settle(t, value, self.sched)
        result.update(source=source, checks=info, settled_at=now.isoformat())
        self._close(t, now, rep, result["net"], result["R"], result)

    def _close(self, t: dict, now: datetime, rep: CycleReport, net: float, r: float, detail: dict) -> None:
        self._log("FNO_SETTLED", now.date(), trade_id=t["id"], expiry=t["expiry"], net=net, R=r,
                  max_loss=t["max_loss"], detail=detail, **self.reg)
        st = self.state()
        realised = sum(x["net"] for x in st.trades.values() if x["status"] == "SETTLED")
        msg = (f"V {t['expiry']} settled: net Rs{net:,.0f} ({r:+.2f}R); paper P&L Rs{realised:+,.0f}; "
               f"journal {self._head()}")
        rep.actions.append(msg)
        rep.decision = msg
        self.notify(f"[F&O PAPER] {msg}")

    # -- cycles (4, 5, 12) ----------------------------------------------------

    def _close_cycles(self, st: State, now: datetime) -> None:
        today = now.date()
        for e, prev in st.counted_cycles():
            if e > today or e in st.cycles or st.trade_for(e):
                continue
            missing = st.unobserved(e, prev, self.cal, IST, p=self.p)
            outcome = "missed" if missing else "no trade"
            self._log("FNO_CYCLE", today, expiry=e.isoformat(), outcome=outcome,
                      first_unobserved=missing[0].isoformat() if missing else None)

    def _decide(self, st: State, now: datetime, rep: CycleReport) -> None:
        from trading.fno.evaluate import decide
        if st.verdict is not None:
            return
        verdict, at = decide(st, self.cal, now, self.p)
        if at is not None:
            self._log("FNO_VERDICT", now.date(), verdict=verdict, decided_at=at, journal=self._head())
            rep.urgent.append(f"V: {verdict}. No further entries.")
            rep.alerts.append(rep.urgent[-1])
            self.notify(f"[F&O PAPER] V: {verdict}")

    # -- entry (4-8) ----------------------------------------------------------

    def _active_cycle(self, st: State, today: date) -> tuple[date, date] | None:
        for e, prev in st.counted_cycles():
            if today in sv.eligible_sessions(e, prev, self.cal, self.p):
                return e, prev
        return None

    def _entry(self, st: State, now: datetime, slot: time, final: bool, master: ContractMaster | None,
               rep: CycleReport) -> str:
        today = now.date()
        if not self.cal.is_trading_day(today):
            return "done"
        if not any(h.startswith(f"{today.year}-") for h in self.cal.holidays):
            self._log("FNO_PASS", today, expiry=None, slot=None, outcome="calendar_missing",
                      detail=f"data/nse_holidays.json holds no {today.year} holidays")
            rep.urgent.append(f"No entry: the {today.year} NSE holiday list is missing from data/nse_holidays.json")
            rep.alerts.append(rep.urgent[-1])
            return "done"
        if sv.slot_of(now, self.p) != slot:
            return "done"                                   # a pass outside its slot's window enters nothing (5)
        cyc = self._active_cycle(st, today)
        if cyc is None:
            return "done"
        e, prev = cyc
        if e in st.cycles or st.trade_for(e):
            return "done"
        slot_dt = datetime.combine(today, slot, IST)
        earlier = st.unobserved(e, prev, self.cal, IST, before=slot_dt, p=self.p)
        if earlier:
            self._log("FNO_CYCLE", today, expiry=e.isoformat(), outcome="missed", first_unobserved=earlier[0].isoformat())
            rep.alerts.append(f"V {e}: cycle MISSED — slot {earlier[0]:%a %H:%M} had no pass")
            return "done"
        mpath = self.state_dir / "master" / f"{today.isoformat()}.json"
        base = {"expiry": e.isoformat(), "slot": slot_dt.isoformat(),
                "master_sha256": _sha256(mpath) if mpath.exists() else None}
        kill = self.kill.engaged()
        if kill:
            self._log("FNO_PASS", today, outcome="blocked", detail=f"kill switch: {kill}", **base)
            self._log("FNO_CYCLE", today, expiry=e.isoformat(), outcome="missed", first_unobserved=slot_dt.isoformat())
            rep.decision = f"no entry: kill switch engaged ({kill}); cycle {e} is MISSED"
            return "done"
        snap: dict = {}
        try:
            if master is None:
                raise DataUnavailable("no contract master today")
            if master.nearest_expiry(today) != e:
                raise DataUnavailable(f"the contract master's nearest expiry is {master.nearest_expiry(today)}, not {e}")
            index = self.feed.index_quote(self.p.underlying)
            plan = plan_entry(master, e, index, self.feed.quotes, self.sched, self.p, snapshot=snap)
        except DataUnavailable as exc:
            if not final:
                return "retry"
            self._log("FNO_PASS", today, outcome="data_unavailable", detail=str(exc)[:500], snapshot=snap, **base)
            rep.alerts.append(f"V {e}: slot {slot:%H:%M} — data unavailable: {str(exc)[:160]}")
            rep.decision = f"no entry at {slot:%H:%M}: data unavailable"
            return "done"
        if isinstance(plan, Skip):
            self._log("FNO_PASS", today, outcome="no_trade", code=plan.code, detail=plan.detail, data=plan.data,
                      snapshot=snap, **base)
            rep.decision = f"no trade at {slot:%H:%M} ({plan.code}): {plan.detail}"
            return "done"
        trade = plan.as_dict()
        trade.update(id=f"V-{e.isoformat()}", opened_at=now.isoformat(), slot=slot_dt.isoformat(),
                     feed=self.feed.key, fee_card=self.sched.name, calendar=self.cal.status(),
                     master_source=master.source,
                     margin=self.feed.basket_margin(plan.legs, plan.qty),
                     broker_charges=self.feed.broker_charges(plan.legs, plan.qty), **self.reg)
        self._log("FNO_PASS", today, outcome="entry", snapshot=snap, **base)
        self._log("FNO_ENTRY", today, trade=trade)
        legs = ", ".join(f"{'-' if g['side'] == 'SELL' else '+'}{g['strike']:g}{g['right']}@{g['price']:.2f}"
                         for g in trade["legs"])
        msg = (f"V {e} opened: {plan.lots} lot(s) x {plan.lot_size}, credit {plan.credit:.2f} pts, "
               f"max loss Rs{plan.max_loss:,.0f} [{legs}]; journal {self._head()}")
        rep.actions.append(msg)
        rep.decision = msg
        self.notify(f"[F&O PAPER] {msg}")
        return "done"

    # -- snapshot ----------------------------------------------------------------

    def _write_snapshot(self) -> None:
        """book.json: a readable summary, rebuilt from the journal. Not the record."""
        st = self.state()
        snap = {"note": "snapshot rebuilt from journal.jsonl after each pass; the journal is the record",
                "feed": st.feed, "start": st.start.isoformat() if st.start else None,
                "verdict": st.verdict, "trades": list(st.trades.values()),
                "cycles": {e.isoformat(): c for e, c in sorted(st.cycles.items())}, "void": st.void}
        path = self.state_dir / "book.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snap, indent=1, sort_keys=True, default=str))
        os.replace(tmp, path)


def record_manual_settlement(state_dir: Path, expiry: str, value: float, source: str,
                             now: datetime | None = None, feed_key: str = "operator") -> None:
    """
    Section 3: NSE's published final settlement price, entered by hand, only after
    a settlement pass on or after the first trading day after the expiry has
    failed to find it, and only with the URL of NSE's file.
    """
    now = now or datetime.now(IST)
    state_dir = Path(state_dir)
    journal = Journal(state_dir / "journal.jsonl",
                      "fno-mock" if state_dir.name.endswith(MOCK_DIR_SUFFIX) else "fno-paper")
    with exclusive(state_dir):
        st = State.from_records(journal.records())
        t = next((x for x in st.trades.values() if x["expiry"] == expiry), None)
        if t is None or t["status"] != "AWAITING_SETTLEMENT":
            raise ValueError(f"no trade for {expiry} is waiting for a settlement value")
        exp = date.fromisoformat(expiry)
        cal = load_calendar(HOLIDAYS_PATH)
        if not any(d > exp and cal.is_trading_day(d) for d in st.pending_days.get(t["id"], set())):
            raise ValueError("a manual value is accepted only after a settlement pass on a later trading day "
                             "has failed to find NSE's final settlement price")
        if not (value > 0) or "nseindia.com" not in source:
            raise ValueError("a positive value and the URL of NSE's file (nseindia.com) are both required")
        if exp in st.manual:
            raise ValueError(f"a manual value for {expiry} was already entered")
        journal.append("FNO_MANUAL_SETTLEMENT_VALUE", asof=now.date().isoformat(), feed=feed_key,
                       expiry=expiry, value=value, source=source)
