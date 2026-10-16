"""
Offline tests for the execution layer. No network, no broker, no API key.

The ones that matter most:
  - test_paper_never_fills_on_the_signal_bar      causality, the backtest's first law
  - test_paper_gap_below_stop_limit_leaves_position_unprotected
  - test_engine_replay_invariants                 the whole loop, end to end
  - test_kite_* with a fake client                exactly what would be sent live
  - test_live_* refusals                          what stands between a bug and money
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd

from market.backtest import net_r
from market.calendar import load_calendar
from market.config import IST
from market.corporate_actions import suspect_corporate_actions
from market.providers.mock import MockProvider
from market.scan import completed_daily_bars
from trading.book import Book, BookStore
from trading.brokers.base import BrokerError, ProtectionImpossible, Throttle
from trading.brokers.kite import KiteBroker
from trading.brokers.paper import PaperBroker
from trading.config import TradingConfig
from trading.confirm import SimulatedConfirmer, TerminalConfirmer
from trading.engine import CycleReport, TradingEngine
from trading.gates import gate2_paper
from trading.journal import Journal, verify_file
from trading.live import LIVE_ACK_PHRASE, LiveRefused, authorise_live
from trading.models import Trade, TradeIntent, round_to_tick
from trading.replay import ReplayProvider, run_replay
from trading.risk import KillSwitch, RiskContext, RiskEngine
from trading.strategy import intents_from_scan


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="trading-test-"))


def frame(rows, start="2026-08-03"):
    idx = pd.bdate_range(start=start, periods=len(rows))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1_000_000
    return df


def make_trade(df, **kw) -> Trade:
    base = dict(
        trade_id="t1", symbol="TEST", direction="long", status="PENDING_ENTRY", qty=10,
        entry_limit=100.0, stop=95.0, stop_limit=94.0, target=110.0, reference_price=99.0,
        signal_bar=df.index[0].date().isoformat(), score=70.0, reason="test",
        created_at="2026-08-03T16:00:00", max_hold_bars=20, tag="AA260803001TEST",
    )
    base.update(kw)
    return Trade(**base)


def paper_fill(rows, **kw):
    """Run one trade through the paper broker over `rows`; first row is the signal bar."""
    df = frame(rows)
    b = PaperBroker(tmpdir() / "pb.json", slippage_pct=0.05)
    d0 = df.index[0].date().isoformat()
    b.sync({"TEST": df.iloc[:1]}, d0)
    t = make_trade(df, **kw)
    t.entry_order_id = b.submit_entry(t, session_open=False)
    b.sync({"TEST": df}, df.index[-1].date().isoformat())
    return b, t


# -- paper broker fill rules ------------------------------------------------

def test_paper_never_fills_on_the_signal_bar():
    # Signal bar trades far below the limit; it must not count.
    b, t = paper_fill([(100, 101, 80, 100), (102, 103, 101.5, 102)])
    u = b.entry_status(t)
    assert u.state == "EXPIRED", u


def test_paper_entry_fills_at_open_when_open_below_limit():
    b, t = paper_fill([(100, 101, 99, 100), (99.5, 101, 99, 100.5)])
    u = b.entry_status(t)
    assert u.state == "COMPLETE" and abs(u.avg_price - round(99.5 * 1.0005, 2)) < 1e-9


def test_paper_entry_needs_trade_through_not_touch():
    b, t = paper_fill([(100, 101, 99, 100), (101, 102, 100.0, 101)])   # low == limit
    assert b.entry_status(t).state == "EXPIRED"
    b, t = paper_fill([(100, 101, 99, 100), (101, 102, 99.9, 101)])
    u = b.entry_status(t)
    assert u.state == "COMPLETE" and u.avg_price == 100.0


def _protected(rows):
    b, t = paper_fill(rows)
    assert b.entry_status(t).state == "COMPLETE"
    t.status, t.filled_qty = "OPEN", 10
    t.protection_id = b.protect(t)
    return b.protection_status(t)


def test_paper_stop_taken_when_bar_spans_both():
    p = _protected([(100, 101, 99, 100), (99.5, 101, 98, 100), (100, 112, 94.5, 105)])
    assert p.state == "STOP_FILLED" and abs(p.fill_price - round(95 * 0.9995, 2)) < 1e-9


def test_paper_target_fills_at_target():
    p = _protected([(100, 101, 99, 100), (99.5, 101, 98, 100), (101, 111, 100, 110)])
    assert p.state == "TARGET_FILLED" and p.fill_price == 110.0


def test_paper_gap_below_stop_fills_at_open_above_stop_limit():
    p = _protected([(100, 101, 99, 100), (99.5, 101, 98, 100), (94.5, 96, 94.2, 95)])
    assert p.state == "STOP_FILLED" and abs(p.fill_price - round(94.5 * 0.9995, 2)) < 1e-9


def test_paper_gap_below_stop_limit_leaves_position_unprotected():
    p = _protected([(100, 101, 99, 100), (99.5, 101, 98, 100), (90, 92, 89, 91)])
    assert p.state == "TRIGGERED_UNFILLED" and p.fill_price is None
    assert "UNPROTECTED" in p.message


def test_paper_fill_bar_checks_stop_but_never_credits_target():
    p = _protected([(100, 101, 99, 100), (99.5, 115, 99, 112)])
    assert p.state == "ACTIVE", "target must not be credited on the day of entry"
    p = _protected([(100, 101, 99, 100), (99.5, 101, 94.5, 96)])
    assert p.state == "STOP_FILLED", "the stop can always hit on the day of entry"


# -- journal ---------------------------------------------------------------

def test_journal_chain_verifies_and_detects_edits():
    path = tmpdir() / "j.jsonl"
    j = Journal(path, "paper")
    for i in range(4):
        j.append("EVENT", asof="2026-08-03", n=i)
    assert verify_file(path).ok
    assert Journal(path, "paper").append("EVENT", n=99)["seq"] == 5, "reopen must continue the chain"

    lines = path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["payload"]["n"] = 1000
    edited = lines[:1] + [json.dumps(rec, sort_keys=True)] + lines[2:]
    path.write_text("\n".join(edited) + "\n")
    assert not verify_file(path).ok, "an edited record must break verification"

    path.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
    assert not verify_file(path).ok, "a deleted record must break verification"


# -- risk engine ------------------------------------------------------------

def intent(**kw) -> TradeIntent:
    base = dict(
        symbol="TCS", qty=20, entry_limit=1000.0, stop=960.0, stop_limit=950.0, target=1100.0,
        reference_price=995.0, score=70.0, coverage=0.7, gross_rr=2.5, net_rr=2.2,
        breakeven_win_rate_pct=31.0, est_max_loss=880.0, est_charges=60.0,
        target_basis="structure", signal_bar="2026-08-03", data_source="test",
        data_freshness="historical", regime="uptrend", reason="test",
    )
    base.update(kw)
    return TradeIntent(**base)


def ctx(**kw) -> RiskContext:
    base = dict(today="2026-08-03", equity=500_000.0, available_capital=500_000.0,
                risk_pct=1.0, last_close=995.0)
    base.update(kw)
    return RiskContext(**base)


def fresh_book(equity=500_000.0) -> Book:
    return Book(mode="paper", starting_equity=equity, created_at="2026-08-01T00:00:00",
                peak_equity=equity)


def test_risk_approves_a_clean_intent():
    d = RiskEngine(TradingConfig()).check_entry(intent(), fresh_book(), ctx())
    assert d.approved, d.summary()


def test_risk_rejections_name_their_reason():
    eng, book = RiskEngine(TradingConfig()), fresh_book()
    cases = {
        "kill_switch": (intent(), ctx(kill_reason="test")),
        "per_trade_risk": (intent(est_max_loss=6_000.0), ctx()),
        "net_reward_risk": (intent(net_rr=1.6), ctx()),
        "coverage_floor": (intent(coverage=0.5), ctx()),
        "long_only_cash": (intent(direction="short"), ctx()),
        "price_band": (intent(), ctx(last_close=800.0)),
        "not_restricted": (intent(), ctx(restricted={"TCS": "ASM stage 2"})),
        "live_price_above_stop": (intent(), ctx(live_price=955.0)),
        "live_price_above_stop ": (intent(), ctx(require_live_price=True)),
        "position_concentration": (intent(qty=150, est_max_loss=4_000.0), ctx()),
        "broker_cash": (intent(), ctx(broker_cash=1_000.0)),
    }
    for name, (it, c) in cases.items():
        d = eng.check_entry(it, book, c)
        assert not d.approved and name.strip() in [f.name for f in d.failures], (name, d.summary())


def test_risk_blocks_duplicates_and_correlated_bets():
    eng, book = RiskEngine(TradingConfig()), fresh_book()
    df = frame([(100, 101, 99, 100)] * 2)
    book.trades["x"] = make_trade(df, symbol="INFY", status="OPEN", filled_qty=10, entry_price=100.0)
    assert "no_duplicate" in [f.name for f in eng.check_entry(
        intent(symbol="INFY"), book, ctx()).failures]

    rng = np.random.default_rng(1)
    base = pd.Series(rng.normal(0, 0.01, 80))
    twin = base + rng.normal(0, 0.001, 80)
    d = eng.check_entry(intent(), book, ctx(returns={"TCS": twin, "INFY": base}))
    assert "correlation" in [f.name for f in d.failures], d.summary()
    other = pd.Series(rng.normal(0, 0.01, 80))
    d = eng.check_entry(intent(), book, ctx(returns={"TCS": other, "INFY": base}))
    assert d.approved, d.summary()


def test_kill_switch_and_circuit_breakers():
    root = tmpdir()
    ks = KillSwitch([root / "paper" / "KILL", root / "KILL"])
    assert ks.engaged() is None
    ks.engage("test")
    assert "test" in ks.engaged()
    ks.release()
    assert ks.engaged() is None

    eng = RiskEngine(TradingConfig(daily_loss_limit_pct=2.0, max_drawdown_pct=10.0))
    book = fresh_book()
    book.equity_marks["2026-08-03"] = 500_000.0
    book.realised_net = -15_000.0                      # -3% today
    trips = eng.circuit_breakers(book, "2026-08-04")
    assert any("daily loss" in t for t in trips)
    book.peak_equity = 600_000.0                       # 485k vs 600k peak = -19%
    assert any("drawdown" in t for t in eng.circuit_breakers(book, "2026-08-04"))


# -- strategy sizing ----------------------------------------------------------

def _report_with(levels: dict, score=72.0) -> dict:
    return {
        "regime": {"classification": "uptrend"},
        "shortlist": [{
            "symbol": "TCS", "score": score, "coverage": 0.71, "confidence": "moderate",
            "levels": levels, "data": {"last_bar": "2026-08-03 00:00:00", "source": "t",
                                       "freshness": "historical"},
        }],
    }


def test_strategy_sizes_on_net_loss_and_snaps_to_tick():
    lv = {"direction": "long", "entry_low": 990.1, "entry_high": 1003.37, "reference_price": 997.0,
          "stop": 961.23, "target2": 1101.9, "target_basis": "structure"}
    cfg = TradingConfig()
    intents, skipped = intents_from_scan(_report_with(lv), cfg, 500_000.0, 1.0)
    assert not skipped and len(intents) == 1
    it = intents[0]
    assert it.est_max_loss <= 5_000.0, "net loss at the stop must fit the 1% budget"
    for px in (it.entry_limit, it.stop, it.stop_limit, it.target):
        assert abs(px / 0.05 - round(px / 0.05)) < 1e-6, f"{px} is off the tick"
    assert it.stop_limit < it.stop < it.entry_limit < it.target
    assert it.notional <= 500_000 * cfg.max_single_position_pct / 100 + 1

    # At 0.25% the risk budget, not the concentration cap, is what binds — and
    # it must bind on the NET loss: gross sizing would overshoot by the charges.
    budget = 500_000 * 0.25 / 100
    it = intents_from_scan(_report_with(lv), cfg, 500_000.0, 0.25)[0][0]
    assert it.est_max_loss <= budget, f"net loss {it.est_max_loss} exceeds budget {budget}"
    assert it.est_max_loss > budget - 2 * it.risk_per_share, "sized far below the budget"


def test_round_to_tick_directions():
    assert round_to_tick(100.03, 0.05, "down") == 100.0
    assert round_to_tick(100.03, 0.05, "up") == 100.05
    assert round_to_tick(100.03, 0.05) == 100.05


# -- confirmation -----------------------------------------------------------

def test_terminal_confirmation_requires_the_exact_phrase():
    quiet = lambda *_: None  # noqa: E731
    ok, _ = TerminalConfirmer(lambda _: "BUY 20 TCS", quiet, lambda: True).confirm({"a": 1}, "BUY 20 TCS")
    assert ok
    ok, _ = TerminalConfirmer(lambda _: "y", quiet, lambda: True).confirm({"a": 1}, "BUY 20 TCS")
    assert not ok, "a bare 'y' must not place a live order"
    ok, how = TerminalConfirmer(lambda _: "BUY 20 TCS", quiet, lambda: False).confirm({"a": 1}, "BUY 20 TCS")
    assert not ok and "interactive" in how, "no terminal, no live order"


# -- Kite adapter, against a fake client ------------------------------------

class FakeKite:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.book: list[dict] = []
        self.gtts: dict[int, dict] = {}
        self.px = 1000.0
        self._id = 1000

    def instruments(self, ex):
        return [{"tradingsymbol": "TCS", "tick_size": 0.1}]

    def orders(self):
        return list(self.book)

    def place_order(self, **kw):
        self.calls.append(("place_order", kw))
        self._id += 1
        self.book.append({"order_id": str(self._id), "status": "OPEN", "filled_quantity": 0, **kw})
        return str(self._id)

    def order_history(self, oid):
        return [o for o in self.book if o["order_id"] == oid]

    def cancel_order(self, variety, oid):
        self.calls.append(("cancel_order", {"variety": variety, "order_id": oid}))

    def ltp(self, key):
        return {key: {"last_price": self.px}}

    def place_gtt(self, **kw):
        self.calls.append(("place_gtt", kw))
        return {"trigger_id": 77}

    def get_gtt(self, tid):
        return self.gtts[tid]

    def delete_gtt(self, tid):
        self.calls.append(("delete_gtt", {"id": tid}))

    def holdings(self):
        return []

    def positions(self):
        return {"day": [], "net": []}

    def margins(self, seg):
        return {"net": 1_000_000.0}


def kite(fake=None) -> KiteBroker:
    return KiteBroker(live_ack=True, max_orders_per_second=1000, client=fake or FakeKite())


def test_kite_refuses_without_ack_and_with_stored_2fa():
    try:
        KiteBroker(live_ack=False, max_orders_per_second=5, client=FakeKite())
        raise AssertionError("built a live broker without the ack")
    except BrokerError:
        pass
    os.environ["KITE_TOTP_SECRET"] = "x"
    try:
        kite()
        raise AssertionError("accepted a stored TOTP seed")
    except BrokerError as exc:
        assert "TOTP" in str(exc)
    finally:
        os.environ.pop("KITE_TOTP_SECRET", None)


def test_kite_entry_is_a_tagged_cnc_limit_and_idempotent():
    fake = FakeKite()
    b = kite(fake)
    df = frame([(100, 101, 99, 100)])
    t = make_trade(df, symbol="TCS", entry_limit=1003.37)
    oid = b.submit_entry(t, session_open=True)
    name, kw = fake.calls[-1]
    assert name == "place_order"
    assert kw["order_type"] == "LIMIT" and kw["product"] == "CNC" and kw["variety"] == "regular"
    assert kw["tag"] == t.tag and kw["transaction_type"] == "BUY" and kw["validity"] == "DAY"
    assert kw["price"] == 1003.3, f"price must snap DOWN to the 0.1 tick: {kw['price']}"
    n = len(fake.calls)
    assert b.submit_entry(t, session_open=True) == oid, "same tag must return the same order"
    assert len(fake.calls) == n, "a resubmit after a crash must not send a second order"
    t2 = make_trade(df, symbol="TCS", tag="OTHERTAG")
    b.submit_entry(t2, session_open=False)
    assert fake.calls[-1][1]["variety"] == "amo"


def test_kite_protection_is_a_two_leg_gtt_and_refuses_when_price_is_through():
    fake = FakeKite()
    b = kite(fake)
    df = frame([(100, 101, 99, 100)])
    t = make_trade(df, symbol="TCS", status="OPEN", filled_qty=10, entry_price=1000.0,
                   entry_limit=1000.0, stop=960.0, stop_limit=950.4, target=1100.0)
    assert b.protect(t) == "77"
    name, kw = fake.calls[-1]
    assert name == "place_gtt" and kw["trigger_type"] == "two-leg"
    assert kw["trigger_values"] == [960.0, 1100.0]
    legs = kw["orders"]
    assert [l["transaction_type"] for l in legs] == ["SELL", "SELL"]
    assert [l["price"] for l in legs] == [950.4, 1100.0] and all(l["quantity"] == 10 for l in legs)
    fake.px = 955.0
    try:
        b.protect(t)
        raise AssertionError("placed a GTT with price already through the stop")
    except ProtectionImpossible as exc:
        assert exc.side == "stop"


def test_kite_reads_a_triggered_gtt():
    fake = FakeKite()
    b = kite(fake)
    fake.book.append({"order_id": "555", "status": "COMPLETE", "filled_quantity": 10, "average_price": 958.5})
    fake.gtts[77] = {"status": "triggered", "orders": [
        {"result": {"order_result": {"order_id": "555", "status": "success"}}},
        {"result": None},
    ]}
    df = frame([(100, 101, 99, 100)])
    t = make_trade(df, symbol="TCS", status="OPEN", filled_qty=10, protection_id="77")
    p = b.protection_status(t)
    assert p.state == "STOP_FILLED" and p.fill_price == 958.5 and p.filled_qty == 10
    fake.book[-1].update(status="CANCELLED", filled_quantity=0, average_price=0)
    assert b.protection_status(t).state == "TRIGGERED_UNFILLED"


def test_kite_never_sends_a_market_order():
    fake = FakeKite()
    b = kite(fake)
    df = frame([(100, 101, 99, 100)])
    t = make_trade(df, symbol="TCS", status="OPEN", filled_qty=10, entry_price=1000.0)
    b.submit_entry(make_trade(df, symbol="TCS", tag="A1"), True)
    b.submit_exit(t, 990.0, True, "A1X1")
    assert all(kw["order_type"] == "LIMIT" for name, kw in fake.calls if name == "place_order")


def test_throttle_spaces_calls():
    clock = [0.0]
    slept = []
    th = Throttle(5.0, clock=lambda: clock[0], sleep=lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)))
    for _ in range(3):
        th.wait()
    assert len(slept) == 2 and all(abs(s - 0.2) < 1e-9 for s in slept)


# -- live opt-in ------------------------------------------------------------

def _live_cfg() -> TradingConfig:
    return TradingConfig(mode="live", state_root=tmpdir())


def test_live_refuses_without_the_ack_phrase():
    cfg = _live_cfg()
    try:
        authorise_live(cfg, Journal(cfg.journal_path, "live"), backtest_json=None,
                       autonomous=False, override_reason=None, env={})
        raise AssertionError("live authorised without the ack")
    except LiveRefused as exc:
        assert "LIVE_TRADING_ACK" in str(exc)


def test_live_gate_override_is_journaled_and_autonomous_has_none():
    cfg = _live_cfg()
    j = Journal(cfg.journal_path, "live")
    env = {"LIVE_TRADING_ACK": LIVE_ACK_PHRASE}
    for reason in (None, "because"):
        try:
            authorise_live(cfg, j, backtest_json=None, autonomous=False, override_reason=reason, env=env)
            raise AssertionError("gates failed but live was authorised without a real reason")
        except LiveRefused:
            pass
    auth = authorise_live(cfg, j, backtest_json=None, autonomous=False, env=env,
                          override_reason="Testing per-order confirmed trading at 0.25% risk")
    assert auth.overridden and isinstance(auth.confirmer, TerminalConfirmer)
    assert [r["type"] for r in j.records()][-2:] == ["GATE_OVERRIDE", "LIVE_AUTHORISED"]
    try:
        authorise_live(cfg, j, backtest_json=None, autonomous=True, env=env,
                       override_reason="Testing per-order confirmed trading at 0.25% risk")
        raise AssertionError("autonomous live authorised with failing gates")
    except LiveRefused as exc:
        assert "no override" in str(exc)


def test_live_risk_is_capped():
    cfg = TradingConfig(mode="live", risk_per_trade_pct=2.0, live_max_risk_per_trade_pct=0.25)
    assert cfg.effective_risk_pct() == 0.25
    assert TradingConfig(mode="paper", risk_per_trade_pct=2.0).effective_risk_pct() == 2.0


# -- gates --------------------------------------------------------------------

def test_gate2_counts_only_untampered_real_time_paper_trades():
    root = tmpdir()
    hol = root / "hol.json"
    hol.write_text(json.dumps({"source": "test", "verified_on": "2026-01-01",
                               "holidays": {"2026-01-26": "Republic Day"}}))
    os.environ["NSE_HOLIDAYS_FILE"] = str(hol)
    try:
        path = root / "paper" / "journal.jsonl"
        j = Journal(path, "paper")
        for i in range(60):
            j.append("TRADE_CLOSED", asof="2026-08-03", net_r_multiple=0.3, net_pnl=100.0)
        assert gate2_paper(path).passed, gate2_paper(path).text()

        rj = Journal(root / "replay.jsonl", "replay")
        for i in range(60):
            rj.append("TRADE_CLOSED", asof="2026-08-03", net_r_multiple=0.3, net_pnl=100.0)
        assert not gate2_paper(root / "replay.jsonl").passed, "replay trades are not paper evidence"

        lines = path.read_text().splitlines()
        rec = json.loads(lines[5])
        rec["payload"]["net_r_multiple"] = 9.0
        path.write_text("\n".join(lines[:5] + [json.dumps(rec, sort_keys=True)] + lines[6:]) + "\n")
        assert not gate2_paper(path).passed, "a tampered journal must fail the gate"
    finally:
        os.environ.pop("NSE_HOLIDAYS_FILE", None)


# -- market-side additions ----------------------------------------------------

def test_completed_bars_drop_a_forming_daily_bar():
    df = frame([(100, 101, 99, 100)] * 3, start="2026-08-04")
    last = df.index[-1].date()
    assert len(completed_daily_bars(df, datetime.combine(last, time(10, 0), IST))) == 2
    assert len(completed_daily_bars(df, datetime.combine(last, time(16, 30), IST))) == 3


def test_corporate_action_detector_flags_an_unadjusted_split():
    rows = [(1000, 1010, 990, 1000)] * 30 + [(500, 505, 495, 500)] * 5
    flags = suspect_corporate_actions(frame(rows))
    assert len(flags) == 1 and "1:2" in flags[0].likely
    assert suspect_corporate_actions(frame([(1000, 1010, 990, 1000)] * 30)) == []


def test_calendar_reads_holidays_and_reports_empty():
    p = tmpdir() / "h.json"
    p.write_text(json.dumps({"source": "s", "verified_on": None, "holidays": {"2026-01-26": "RD"}}))
    cal = load_calendar(p)
    assert not cal.is_trading_day(date(2026, 1, 26)) and cal.is_trading_day(date(2026, 1, 27))
    assert not cal.is_trading_day(date(2026, 1, 25)), "Sunday"
    assert "UNVERIFIED" in cal.status()
    assert not load_calendar(tmpdir() / "missing.json").loaded


def test_backtest_net_r_is_below_gross():
    assert net_r(1000.0, 960.0, 2.0, "long") < 2.0
    assert net_r(1000.0, 960.0, -1.0, "long") < -1.0


# -- engine, end to end -------------------------------------------------------

SYMS = ["TCS", "INFY", "CIPLA", "NESTLEIND", "SBIN", "HAL"]


def _engine(root: Path, provider, broker=None, **cfg_kw) -> TradingEngine:
    cfg = TradingConfig(mode="paper", state_root=root, min_net_rr=1.2, min_score=55, **cfg_kw)
    store = BookStore(cfg.book_path)
    if not store.exists():
        store.create("paper", 500_000.0)
    return TradingEngine(
        cfg, provider, broker or PaperBroker(cfg.state_dir / "pb.json", cfg.slippage_pct),
        Journal(cfg.journal_path, "paper"), store, SimulatedConfirmer(), symbols=SYMS,
    )


def test_engine_replay_invariants():
    root = tmpdir()
    prov = ReplayProvider(MockProvider(), SYMS, lookback=300)
    eng = _engine(root, prov)
    dates = prov.session_dates()
    run_replay(lambda: eng, prov, dates[-90], None)

    path = eng.cfg.journal_path
    v = verify_file(path)
    assert v.ok, v.problems
    book = eng.store.load()
    recs = [json.loads(l) for l in path.read_text().splitlines()]
    submitted = [r for r in recs if r["type"] == "ORDER_SUBMIT" and r["payload"]["side"] == "BUY"]
    assert submitted, "the replay never traded — loosen the test floors"
    assert book.closed(), "nothing closed in 90 sessions"

    for t in book.trades.values():
        if t.entry_at:
            assert t.entry_at > t.signal_bar, f"{t.trade_id} filled on/before its signal bar"
    placed = {r["payload"]["trade_id"] for r in recs if r["type"] == "PROTECTION_PLACED"}
    assert {t.trade_id for t in book.trades.values() if t.filled_qty} <= placed
    assert abs(book.realised_net - sum(t.net_pnl for t in book.closed())) < 0.05
    for t in book.closed():
        assert t.charges > 0 and t.net_pnl < t.gross_pnl
    assert len({t.symbol for t in book.active()}) == len(book.active()), "two live trades in one name"


def test_engine_rerun_on_the_same_bar_enters_nothing_new():
    root = tmpdir()
    prov = ReplayProvider(MockProvider(), SYMS, lookback=300)
    eng = _engine(root, prov)
    d = prov.session_dates()[-1]
    prov.set_asof(d)
    now = datetime.combine(d, time(16, 15), IST)
    eng.run_cycle(now)
    n = sum(1 for r in eng.journal.records(("ORDER_SUBMIT",)))
    rep = eng.run_cycle(now)
    assert sum(1 for r in eng.journal.records(("ORDER_SUBMIT",))) == n
    assert "already acted on" in rep.decision


def test_engine_kill_switch_blocks_entries_and_cancels_pending():
    root = tmpdir()
    prov = ReplayProvider(MockProvider(), SYMS, lookback=300)
    eng = _engine(root, prov)
    dates = prov.session_dates()
    for d in dates[-60:-1]:
        prov.set_asof(d)
        rep = eng.run_cycle(datetime.combine(d, time(16, 15), IST))
        if rep.submitted:
            break
    assert rep.submitted, "no entry in 60 sessions — loosen the test floors"
    pending = [t.trade_id for t in eng.store.load().active() if t.status == "PENDING_ENTRY"]
    assert pending

    # Kill, then rerun before the next bar: pending entries are cancelled.
    eng.kill.engage("test")
    book = eng.store.load()
    book.last_signal_bar = None              # would otherwise skip entries anyway
    eng.store.save(book)
    n_buys = len([r for r in eng.journal.records(("ORDER_SUBMIT",)) if r["payload"]["side"] == "BUY"])
    rep = eng.run_cycle(datetime.combine(d, time(16, 30), IST))
    assert rep.kill and not rep.submitted and "kill switch" in rep.decision
    cancelled = {r["payload"]["trade_id"] for r in eng.journal.records(("ENTRY_CANCEL_REQUESTED",))}
    assert set(pending) <= cancelled
    assert len([r for r in eng.journal.records(("ORDER_SUBMIT",))
                if r["payload"]["side"] == "BUY"]) == n_buys, "an entry went out with the switch on"

    # Next bar: the cancelled orders resolve as dead, nothing new is entered.
    nxt = dates[dates.index(d) + 1]
    prov.set_asof(nxt)
    rep = eng.run_cycle(datetime.combine(nxt, time(16, 15), IST))
    book = eng.store.load()
    assert all(book.trades[tid].status == "CANCELLED" for tid in pending)
    assert not rep.submitted


def test_engine_adopts_an_order_sent_before_a_crash():
    root = tmpdir()
    fake = FakeKite()
    broker = kite(fake)
    eng = _engine(root, MockProvider(), broker=broker)
    book = eng.store.load()
    df = frame([(100, 101, 99, 100)])
    sent = make_trade(df, trade_id="sent", symbol="TCS", tag="TAGSENT")
    lost = make_trade(df, trade_id="lost", symbol="INFY", tag="TAGLOST")
    book.trades = {"sent": sent, "lost": lost}
    fake.book.append({"order_id": "9001", "tag": "TAGSENT", "status": "OPEN", "filled_quantity": 0})
    rep = CycleReport(mode="paper", started="x")
    eng._reconcile(book, {}, "2026-08-04", True, rep, manage_time=False)
    assert sent.entry_order_id == "9001" and sent.status == "PENDING_ENTRY"
    assert lost.status == "CANCELLED"
    kinds = [r["type"] for r in eng.journal.records()]
    assert "ORDER_ADOPTED" in kinds and "ENTRY_NEVER_SENT" in kinds


if __name__ == "__main__":
    import sys
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
