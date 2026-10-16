"""
The trading engine: one cycle, run once per trading day (and, live, a `watch`
loop during the session).

    1. scan            the same market/scan.py pipeline run_scan.py prints
    2. sync            paper: simulate the new bar; live: nothing to do
    3. reconcile       every active trade — filled? protected? stopped? due out?
    4. breakers        daily loss and drawdown limits; a trip engages the kill switch
    5. entries         intents -> risk engine -> confirmation -> broker
    6. persist         book saved, cycle summary journaled

Ordering rules that matter:
  * A trade is written to the book BEFORE its order goes to the broker, keyed
    by a client tag. If the process dies in between, the next cycle asks the
    broker for that tag instead of sending a second order.
  * Positions are protected before anything else happens to them, and the
    kill switch never removes a protective order.
  * Signals for a given completed bar are acted on once. Rerunning a cycle on
    the same bar manages positions but enters nothing new.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

import pandas as pd

from market.charges import SCHEDULES, round_trip_costs
from market.config import IST
from market.calendar import load_calendar
from market.providers.base import DataProvider, DataUnavailable
from market.scan import bar_date, completed_daily_bars, market_status, scan
from trading.book import Book, BookStore
from trading.brokers.base import Broker, BrokerError, ProtectionImpossible
from trading.config import TradingConfig, load_restricted
from trading.confirm import Confirmer, build_ticket
from trading.journal import Journal
from trading.models import ExitLeg, Trade, round_to_tick
from trading.risk import KillSwitch, RiskContext, RiskEngine
from trading.strategy import intents_from_scan


@dataclass
class CycleReport:
    mode: str
    started: str
    asof: str | None = None
    actions: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    submitted: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    equity: float | None = None
    kill: str | None = None
    decision: str = ""

    def text(self) -> str:
        out = [f"[{self.mode}] cycle {self.started} — market data as of {self.asof or 'NONE'}"]
        if self.equity is not None:
            out.append(f"equity Rs{self.equity:,.0f}")
        if self.kill:
            out.append(f"KILL SWITCH: {self.kill}")
        out += [f"  - {a}" for a in self.actions]
        out += [f"  ! {a}" for a in self.alerts]
        for r in self.rejected:
            out.append(f"  x {r['symbol']}: {r['reason']}")
        out.append(f"decision: {self.decision}")
        return "\n".join(out)


class TradingEngine:
    def __init__(
        self,
        cfg: TradingConfig,
        provider: DataProvider,
        broker: Broker,
        journal: Journal,
        store: BookStore,
        confirmer: Confirmer,
        *,
        symbols: list[str] | None = None,
        catalysts: dict | None = None,
        risk_pct: float | None = None,
    ):
        self.cfg = cfg
        self.provider = provider
        self.broker = broker
        self.journal = journal
        self.store = store
        self.confirmer = confirmer
        self.symbols = symbols
        self.catalysts = catalysts or {}
        self.risk_pct = risk_pct if risk_pct is not None else cfg.effective_risk_pct()
        self.risk = RiskEngine(cfg)
        self.kill = KillSwitch(cfg.kill_paths)
        self.calendar = load_calendar()
        self.restricted = load_restricted(cfg.restricted_file)

    # ------------------------------------------------------------------
    # cycle
    # ------------------------------------------------------------------

    def run_cycle(self, now: datetime | None = None) -> CycleReport:
        now = now or datetime.now(IST)
        cfg = self.cfg
        book = self.store.load()
        rep = CycleReport(mode=cfg.mode, started=now.isoformat(timespec="seconds"))
        session_open = market_status(now)["session_open"]

        self.journal.append(
            "CYCLE_START", broker=self.broker.name, provider=self.provider.name,
            session_open=session_open, wall=now.isoformat(), risk_pct=self.risk_pct,
            confirmer=self.confirmer.name,
        )

        # 1. scan -----------------------------------------------------
        sr = scan(
            self.provider, self.symbols, catalysts=self.catalysts,
            equity=book.equity(), risk_pct=self.risk_pct, segment=cfg.segment,
            slippage=cfg.slippage_pct, min_score=cfg.min_score, now=now,
        )
        report = sr.report
        bars: dict[str, pd.DataFrame] = {s: b.df for s, b in sr.bars.items()}
        for t in book.active():
            if t.symbol not in bars:
                try:
                    b = self.provider.get_bars(t.symbol, "1d", 300)
                    bars[t.symbol] = completed_daily_bars(b.df, now)
                except DataUnavailable as exc:
                    self._alert(rep, None, f"{t.symbol}: held but no data — {exc}")

        asof = self._asof(sr, bars)
        rep.asof = asof
        self.journal.append(
            "SCAN", asof=asof,
            regime=(report.get("regime") or {}).get("classification"),
            analysed=len(report["candidates"]),
            shortlist=[[c["symbol"], c["score"]] for c in report["shortlist"]],
            rejected=report["rejected"], errors=report["errors"],
            data_limitations=report["data_limitations"],
            decision_hint=report["decision_hint"],
        )
        if asof is None:
            self._alert(rep, None, "no market data at all — nothing can be managed or entered")
            rep.decision = "NO TRADE — no data"
            self.journal.append("CYCLE_END", decision=rep.decision, alerts=rep.alerts)
            return rep

        # 2-3. sync and reconcile ---------------------------------------
        self.broker.sync(bars, asof)
        for s, df in bars.items():
            if not df.empty:
                book.marks[s] = round(float(df["close"].iloc[-1]), 2)
        self._reconcile(book, bars, asof, session_open, rep, manage_time=True)
        self.store.save(book)
        if self.broker.is_live:
            self._check_holdings(book, asof, rep)

        # 4. breakers ----------------------------------------------------
        for why in self.risk.circuit_breakers(book, asof):
            p = self.kill.engage(why)
            self._alert(rep, asof, f"KILL SWITCH ENGAGED ({p}): {why}")
            self.journal.append("KILL_SWITCH_ENGAGED", asof=asof, reason=why, path=str(p))
        rep.kill = self.kill.engaged()
        if rep.kill:
            self._cancel_pending(book, asof, rep, f"kill switch: {rep.kill}")

        # 5. entries -----------------------------------------------------
        if rep.kill:
            rep.decision = "NO NEW ENTRIES — kill switch engaged (protective orders untouched)"
        elif book.last_signal_bar == asof:
            rep.decision = f"signals for {asof} were already acted on — managing positions only"
        elif cfg.is_live and cfg.live_entries_in_session_only and not session_open:
            rep.decision = (
                "live entries wait for the session, so the price can be checked against "
                "the stop just before each order goes out"
            )
        else:
            self._entries(book, report, bars, asof, session_open, now, rep)
            book.last_signal_bar = asof

        # 6. persist -----------------------------------------------------
        eq = book.equity()
        book.equity_marks[asof] = round(eq, 2)
        book.peak_equity = max(book.peak_equity, eq)
        rep.equity = eq
        self.store.save(book)
        self.journal.append(
            "CYCLE_END", asof=asof, equity=round(eq, 2), realised_net=round(book.realised_net, 2),
            unrealised=round(book.unrealised(), 2), open_risk=round(book.open_risk(), 2),
            active=[t.trade_id for t in book.active()], submitted=rep.submitted,
            alerts=rep.alerts, decision=rep.decision,
        )
        return rep

    def watch_once(self, now: datetime | None = None) -> CycleReport:
        """
        Live, during the session: reconcile only. Protects entries the moment
        they fill and books exits as they happen. Never enters anything.
        """
        now = now or datetime.now(IST)
        book = self.store.load()
        rep = CycleReport(mode=self.cfg.mode, started=now.isoformat(timespec="seconds"))
        today = now.date().isoformat()
        rep.asof = today
        session_open = market_status(now)["session_open"]
        self._reconcile(book, {}, today, session_open, rep, manage_time=False)
        self.store.save(book)
        rep.equity = book.equity()
        rep.decision = "watch: reconcile only"
        if rep.actions or rep.alerts:
            self.journal.append("WATCH", asof=today, actions=rep.actions, alerts=rep.alerts)
        return rep

    def flatten(self, reason: str, now: datetime | None = None) -> CycleReport:
        """Cancel every pending entry and exit every open position. Caller confirms first."""
        now = now or datetime.now(IST)
        book = self.store.load()
        rep = CycleReport(mode=self.cfg.mode, started=now.isoformat(timespec="seconds"))
        today = now.date().isoformat()
        session_open = market_status(now)["session_open"]
        self.journal.append("FLATTEN", asof=today, reason=reason)
        self._cancel_pending(book, today, rep, f"flatten: {reason}")
        for t in book.active():
            if t.status != "OPEN":
                continue
            if t.protection_id:
                try:
                    self.broker.cancel_protection(t)
                except BrokerError as exc:
                    self._alert(rep, today, f"{t.symbol}: could not cancel protection — {exc}")
                    continue
                t.protection_id = None
            self._exit(t, book, today, session_open, rep, "manual")
        self.store.save(book)
        rep.decision = "flatten submitted"
        return rep

    # ------------------------------------------------------------------
    # reconcile
    # ------------------------------------------------------------------

    def _reconcile(self, book: Book, bars, asof: str, session_open: bool,
                   rep: CycleReport, manage_time: bool) -> None:
        for t in list(book.active()):
            try:
                if t.status == "PENDING_ENTRY":
                    self._reconcile_entry(t, book, asof, rep)
                if t.status == "OPEN":
                    self._reconcile_open(t, book, bars, asof, session_open, rep, manage_time)
                elif t.status == "EXITING":
                    self._reconcile_exit(t, book, asof, session_open, rep)
            except BrokerError as exc:
                self._alert(rep, asof, f"{t.symbol}: broker error while reconciling — {exc}")
                self.journal.append("BROKER_ERROR", asof=asof, trade_id=t.trade_id, error=str(exc))

    def _reconcile_entry(self, t: Trade, book: Book, asof: str, rep: CycleReport) -> None:
        if t.entry_order_id is None:
            oid = self.broker.find_order_by_tag(t.tag)
            if not oid:
                t.status = "CANCELLED"
                self.journal.append("ENTRY_NEVER_SENT", asof=asof, trade_id=t.trade_id, tag=t.tag)
                rep.actions.append(f"{t.symbol}: entry was recorded but never reached the broker — dropped")
                return
            t.entry_order_id = oid
            self.journal.append("ORDER_ADOPTED", asof=asof, trade_id=t.trade_id, tag=t.tag, order_id=oid)

        u = self.broker.entry_status(t)
        dead = u.state in ("CANCELLED", "EXPIRED", "REJECTED")
        if u.state == "COMPLETE" or (dead and u.filled_qty > 0):
            t.status = "OPEN"
            t.filled_qty = u.filled_qty or t.qty
            t.entry_price = u.avg_price if u.avg_price is not None else t.entry_limit
            t.entry_at = (u.at or asof)[:10]
            self.journal.append(
                "FILL", asof=asof, trade_id=t.trade_id, symbol=t.symbol, side="BUY",
                qty=t.filled_qty, price=t.entry_price, at=u.at, planned_qty=t.qty,
                planned_limit=t.entry_limit,
            )
            rep.actions.append(f"{t.symbol}: bought {t.filled_qty} @ {t.entry_price}")
        elif dead:
            t.status = "CANCELLED"
            self.journal.append("ENTRY_DEAD", asof=asof, trade_id=t.trade_id, state=u.state, message=u.message)
            rep.actions.append(f"{t.symbol}: entry {u.state.lower()} unfilled ({u.message})")

    def _reconcile_open(self, t: Trade, book: Book, bars, asof: str, session_open: bool,
                        rep: CycleReport, manage_time: bool) -> None:
        df = bars.get(t.symbol)
        if df is not None and t.entry_at:
            t.bars_held = sum(1 for ts in df.index if bar_date(ts).isoformat() > t.entry_at[:10])

        if t.protection_id is None:
            self._protect(t, book, asof, session_open, rep)
            if t.status != "OPEN" or t.protection_id is None:
                return

        p = self.broker.protection_status(t)
        t.protection_state = p.state
        if p.state in ("STOP_FILLED", "TARGET_FILLED"):
            reason = "stop" if p.state == "STOP_FILLED" else "target"
            self._book_exit(t, book, p.filled_qty or t.remaining_qty, p.fill_price, p.at or asof, reason, rep)
            return
        if p.state == "TRIGGERED_UNFILLED":
            if p.filled_qty:
                self._book_exit(t, book, p.filled_qty, p.fill_price, p.at or asof, "stop", rep)
            if t.remaining_qty > 0:
                self._alert(rep, asof, f"{t.symbol}: UNPROTECTED — {p.message}. Sending an emergency exit.")
                self.journal.append("PROTECTION_FAILED", asof=asof, trade_id=t.trade_id, message=p.message)
                t.protection_id = None
                self._exit(t, book, asof, session_open, rep, "emergency")
            return
        if p.state == "CANCELLED":
            self._alert(rep, asof, f"{t.symbol}: protective order gone ({p.message}) — re-placing it")
            self.journal.append("PROTECTION_LOST", asof=asof, trade_id=t.trade_id, message=p.message)
            t.protection_id = None
            self._protect(t, book, asof, session_open, rep)
            return
        if p.state == "UNKNOWN":
            self._alert(rep, asof, f"{t.symbol}: protection status unreadable ({p.message}) — check by hand")
            return

        if manage_time and p.state == "ACTIVE" and t.bars_held >= t.max_hold_bars:
            self.journal.append("TIME_STOP", asof=asof, trade_id=t.trade_id, bars_held=t.bars_held)
            self.broker.cancel_protection(t)
            t.protection_id = None
            self._exit(t, book, asof, session_open, rep, "time")

    def _protect(self, t: Trade, book: Book, asof: str, session_open: bool, rep: CycleReport) -> None:
        try:
            pid = self.broker.protect(t)
        except ProtectionImpossible as exc:
            self.journal.append("PROTECTION_IMPOSSIBLE", asof=asof, trade_id=t.trade_id,
                                side=exc.side, price=exc.price, message=str(exc))
            self._alert(rep, asof, f"{t.symbol}: cannot place stop/target — {exc}. Exiting.")
            self._exit(t, book, asof, session_open, rep, exc.side, ref_price=exc.price)
            return
        except BrokerError as exc:
            self._alert(rep, asof, f"{t.symbol}: POSITION OPEN WITHOUT PROTECTION — {exc}")
            self.journal.append("PROTECTION_FAILED", asof=asof, trade_id=t.trade_id, message=str(exc))
            return
        t.protection_id = pid
        t.protection_state = "ACTIVE"
        self.journal.append(
            "PROTECTION_PLACED", asof=asof, trade_id=t.trade_id, protection_id=pid,
            qty=t.remaining_qty, stop=t.stop, stop_limit=t.stop_limit, target=t.target,
        )

    def _reconcile_exit(self, t: Trade, book: Book, asof: str, session_open: bool, rep: CycleReport) -> None:
        u = self.broker.exit_status(t)
        dead = u.state in ("CANCELLED", "EXPIRED", "REJECTED")
        if u.state == "COMPLETE" or (dead and u.filled_qty > 0):
            self._book_exit(t, book, u.filled_qty or t.remaining_qty, u.avg_price, u.at or asof,
                            t.exit_reason_pending or "exit", rep)
            if t.status == "CLOSED":
                return
            t.status, t.exit_order_id = "OPEN", None
            self._alert(rep, asof, f"{t.symbol}: exit part-filled, {t.remaining_qty} left — retrying")
            self._exit(t, book, asof, session_open, rep, t.exit_reason_pending or "exit")
        elif dead:
            self._alert(rep, asof, f"{t.symbol}: exit order {u.state.lower()} ({u.message}) — retrying")
            self.journal.append("EXIT_DEAD", asof=asof, trade_id=t.trade_id, state=u.state, message=u.message)
            t.status, t.exit_order_id = "OPEN", None
            self._exit(t, book, asof, session_open, rep, t.exit_reason_pending or "exit")

    # ------------------------------------------------------------------
    # exits and booking
    # ------------------------------------------------------------------

    def _exit(self, t: Trade, book: Book, asof: str, session_open: bool, rep: CycleReport,
              reason: str, ref_price: float | None = None) -> None:
        ref = ref_price
        if ref is None and self.broker.is_live:
            try:
                ref = self.broker.last_price(t.symbol)
            except BrokerError:
                ref = None
        ref = ref or book.marks.get(t.symbol) or t.entry_price or t.entry_limit
        limit = round_to_tick(ref * (1 - self.cfg.exit_limit_buffer_pct / 100.0),
                              self.broker.tick_size(t.symbol), "down")
        t.exit_attempts += 1
        tag = f"{t.tag[:18]}X{t.exit_attempts % 10}"
        self.journal.append(
            "ORDER_SUBMIT", asof=asof, trade_id=t.trade_id, tag=tag, symbol=t.symbol,
            side="SELL", qty=t.remaining_qty, order_type="LIMIT", price=limit,
            product="CNC", reason=reason, session_open=session_open,
        )
        try:
            oid = self.broker.submit_exit(t, limit, session_open, tag)
        except BrokerError as exc:
            self._alert(rep, asof, f"{t.symbol}: EXIT ORDER FAILED — {exc}")
            self.journal.append("ORDER_REJECTED", asof=asof, trade_id=t.trade_id, tag=tag, error=str(exc))
            if reason == "time":
                self._protect(t, book, asof, session_open, rep)
            return
        t.exit_order_id, t.status, t.exit_reason_pending = oid, "EXITING", reason
        book.count_order(asof)
        self.journal.append("ORDER_ACK", asof=asof, trade_id=t.trade_id, tag=tag, broker_order_id=oid)
        rep.actions.append(f"{t.symbol}: {reason} exit sent — SELL {t.remaining_qty} LIMIT {limit}")

    def _book_exit(self, t: Trade, book: Book, qty: int, price: float | None, at: str,
                   reason: str, rep: CycleReport) -> None:
        if price is None:
            self._alert(rep, at, f"{t.symbol}: exit reported without a price — book it by hand")
            return
        qty = min(int(qty), t.remaining_qty)
        t.exits.append(ExitLeg(qty=qty, price=float(price), at=str(at)[:10], reason=reason))
        self.journal.append("FILL", asof=str(at)[:10], trade_id=t.trade_id, symbol=t.symbol,
                            side="SELL", qty=qty, price=price, reason=reason)
        if t.remaining_qty <= 0:
            self._close(t, book, rep)

    def _close(self, t: Trade, book: Book, rep: CycleReport) -> None:
        entry = float(t.entry_price)
        sched = SCHEDULES[self.cfg.segment]
        gross = sum(leg.qty * (leg.price - entry) for leg in t.exits)
        charges = sum(round_trip_costs(entry, leg.price, leg.qty, sched).total for leg in t.exits)
        net = gross - charges
        risk_ps = entry - t.stop if entry > t.stop else t.entry_limit - t.stop
        t.gross_pnl, t.charges, t.net_pnl = round(gross, 2), round(charges, 2), round(net, 2)
        t.r_multiple = round((t.exit_price - entry) / risk_ps, 3)
        t.net_r_multiple = round(net / (t.filled_qty * risk_ps), 3)
        t.status = "CLOSED"
        t.closed_at = t.exits[-1].at
        book.realised_net += net
        reasons = "+".join(sorted({leg.reason for leg in t.exits}))
        self.journal.append(
            "TRADE_CLOSED", asof=t.closed_at, trade_id=t.trade_id, symbol=t.symbol,
            qty=t.filled_qty, entry_price=entry, exit_price=round(t.exit_price, 2),
            exit_reason=reasons, bars_held=t.bars_held, gross_pnl=t.gross_pnl,
            charges=t.charges, net_pnl=t.net_pnl, r_multiple=t.r_multiple,
            net_r_multiple=t.net_r_multiple, planned_entry=t.entry_limit,
            reference_price=t.reference_price, stop=t.stop, target=t.target,
            signal_bar=t.signal_bar, score=t.score,
        )
        rep.actions.append(
            f"{t.symbol}: CLOSED ({reasons}) {t.filled_qty} @ {entry} -> {t.exit_price:.2f}, "
            f"net Rs{net:,.0f} ({t.net_r_multiple:+.2f}R after Rs{charges:,.0f} charges)"
        )

    def _cancel_pending(self, book: Book, asof: str, rep: CycleReport, why: str) -> None:
        for t in book.active():
            if t.status != "PENDING_ENTRY" or not t.entry_order_id:
                continue
            try:
                self.broker.cancel_entry(t)
            except BrokerError as exc:
                self._alert(rep, asof, f"{t.symbol}: could not cancel pending entry — {exc}")
                continue
            self.journal.append("ENTRY_CANCEL_REQUESTED", asof=asof, trade_id=t.trade_id, reason=why)
            rep.actions.append(f"{t.symbol}: pending entry cancel requested ({why})")

    def _check_holdings(self, book: Book, asof: str, rep: CycleReport) -> None:
        try:
            held = self.broker.holdings()
        except BrokerError as exc:
            self._alert(rep, asof, f"could not read holdings to reconcile — {exc}")
            return
        if held is None:
            return
        for sym in sorted({t.symbol for t in book.active() if t.status in ("OPEN", "EXITING")}):
            want, have = book.held_qty(sym), held.get(sym, 0)
            if have < want:
                msg = (f"{sym}: broker shows {have} shares, the book expects {want}. Sold or "
                       "moved outside this system? Resolve by hand before trusting the book.")
                self._alert(rep, asof, msg)
                self.journal.append("HOLDINGS_MISMATCH", asof=asof, symbol=sym, broker=have, book=want)

    # ------------------------------------------------------------------
    # entries
    # ------------------------------------------------------------------

    def _entries(self, book: Book, report: dict, bars, asof: str, session_open: bool,
                 now: datetime, rep: CycleReport) -> None:
        cfg = self.cfg
        equity = book.equity()
        intents, skipped = intents_from_scan(report, cfg, equity, self.risk_pct)
        for s in skipped:
            self.journal.append("SIGNAL_SKIPPED", asof=asof, **s)
            rep.rejected.append(s)
        if not intents:
            rep.decision = "NO TRADE — " + report.get("decision_hint", "nothing shortlisted")
            self.journal.append("DECISION", asof=asof, decision=rep.decision)
            return

        returns = {
            s: df["close"].pct_change().dropna()
            for s, df in bars.items() if df is not None and len(df) > 1
        }
        broker_cash = None
        if self.broker.is_live:
            try:
                broker_cash = self.broker.available_cash()
            except BrokerError as exc:
                self._alert(rep, asof, f"cannot read broker cash — {exc}")
            if broker_cash is None:
                rep.decision = "NO TRADE — broker cash unreadable, so no live order can be sized safely"
                self.journal.append("DECISION", asof=asof, decision=rep.decision)
                return
        trading_day = self.calendar.is_trading_day(now.date()) if cfg.is_live else True

        approved = 0
        for it in intents:
            live_px = None
            if self.broker.is_live:
                try:
                    live_px = self.broker.last_price(it.symbol)
                except BrokerError as exc:
                    self._alert(rep, asof, f"{it.symbol}: no live price — {exc}")
            ctx = RiskContext(
                today=asof, equity=equity, available_capital=book.available_capital(),
                risk_pct=self.risk_pct, broker_cash=broker_cash,
                kill_reason=self.kill.engaged(), is_trading_day=trading_day,
                last_close=book.marks.get(it.symbol), live_price=live_px,
                returns=returns, restricted=self.restricted, approved_this_cycle=approved,
                require_live_price=self.broker.is_live,
            )
            dec = self.risk.check_entry(it, book, ctx)
            self.journal.append(
                "RISK_DECISION", asof=asof, symbol=it.symbol, approved=dec.approved,
                checks=[asdict(c) for c in dec.checks], intent=asdict(it),
            )
            if not dec.approved:
                rep.rejected.append({"symbol": it.symbol, "reason": dec.summary()})
                continue

            ticket = build_ticket(it, cfg, equity)
            ok, how = self.confirmer.confirm(ticket, f"BUY {it.qty} {it.symbol}")
            self.journal.append("CONFIRMATION", asof=asof, symbol=it.symbol, approved=ok,
                                how=how, confirmer=self.confirmer.name, ticket=ticket)
            if not ok:
                rep.rejected.append({"symbol": it.symbol, "reason": f"not confirmed: {how}"})
                continue

            trade_id, tag = book.next_ids(it.symbol, it.signal_bar)
            t = Trade(
                trade_id=trade_id, symbol=it.symbol, direction="long", status="PENDING_ENTRY",
                qty=it.qty, entry_limit=it.entry_limit, stop=it.stop, stop_limit=it.stop_limit,
                target=it.target, reference_price=it.reference_price, signal_bar=it.signal_bar,
                score=it.score, reason=it.reason, created_at=now.isoformat(timespec="seconds"),
                max_hold_bars=cfg.max_hold_bars, tag=tag,
            )
            book.trades[trade_id] = t
            self.store.save(book)          # on disk BEFORE the broker sees it
            self.journal.append(
                "ORDER_SUBMIT", asof=asof, trade_id=trade_id, tag=tag, symbol=it.symbol,
                side="BUY", qty=it.qty, order_type="LIMIT", price=it.entry_limit,
                product="CNC", validity="DAY", session_open=session_open,
            )
            try:
                oid = self.broker.submit_entry(t, session_open)
            except BrokerError as exc:
                t.status = "CANCELLED"
                t.alerts.append(str(exc))
                self._alert(rep, asof, f"{it.symbol}: entry REJECTED by broker — {exc}")
                self.journal.append("ORDER_REJECTED", asof=asof, trade_id=trade_id, tag=tag, error=str(exc))
                self.store.save(book)
                continue
            t.entry_order_id = oid
            book.count_order(asof)
            approved += 1
            # An order id means the broker accepted it into its book. It does
            # not mean filled, and an exchange can still reject it — the next
            # reconcile reads the real state.
            self.journal.append("ORDER_ACK", asof=asof, trade_id=trade_id, tag=tag, broker_order_id=oid)
            rep.submitted.append(f"BUY {it.qty} {it.symbol} LIMIT {it.entry_limit} (order {oid})")
            self.store.save(book)

        if approved:
            rep.decision = f"{approved} entr{'y' if approved == 1 else 'ies'} submitted"
        else:
            rep.decision = "NO TRADE — no candidate passed every risk check and confirmation"
        self.journal.append("DECISION", asof=asof, decision=rep.decision)

    # ------------------------------------------------------------------

    def _asof(self, sr, bars) -> str | None:
        if sr.benchmark is not None and not sr.benchmark.df.empty:
            return bar_date(sr.benchmark.df.index[-1]).isoformat()
        dates = [bar_date(df.index[-1]) for df in bars.values() if df is not None and not df.empty]
        return max(dates).isoformat() if dates else None

    def _alert(self, rep: CycleReport, asof: str | None, msg: str) -> None:
        rep.alerts.append(msg)
        self.journal.append("ALERT", asof=asof, message=msg)
