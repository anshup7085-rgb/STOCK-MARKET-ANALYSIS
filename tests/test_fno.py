"""
Offline tests for the F&O paper engine and strategy V (research/PREREGISTRATION_3.md).
No market data: quotes here are hand-written or synthetic (MockFnoFeed).

    PYTHONPATH=. python tests/test_fno.py
"""

from __future__ import annotations

import math
import os
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from market.calendar import TradingCalendar
from market.charges import (FNO_GROWW, FNO_ZERODHA, futures_order_costs, option_order_costs,
                            option_settlement_costs)
from market.config import IST
from market.providers.base import DataUnavailable
from research.fno_stats import bet_sizes, betting_path
from trading.fno.book import RunnerBusy, exclusive
from trading.fno.engine import FeedRefused, FnoEngine, record_manual_settlement
from trading.fno.evaluate import _outcome, evaluate
from trading.fno.feeds import FnoFeed, KiteFnoFeed, MockFnoFeed, refuse_secrets_in_env
from trading.fno.model import (ContractMaster, IndexQuote, OptionContract, Quote, from_groww_frame,
                               from_kite_rows)
from trading.fno.strategy_v import (REGISTERED, Plan, Skip, ineligible, max_loss, plan_entry,
                                    sessions_after, settle)
from trading.journal import verify_file

CAL = TradingCalendar()                                  # weekends only, unless a test adds holidays
EXP = date(2026, 11, 17)                                 # a Tuesday
NOW = datetime(2026, 11, 11, 10, 0, tzinfo=IST)          # the Wednesday before it


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="fno_test_"))


def master(lot: int = 65, lo: int = 24000, hi: int = 26000, freeze: int | None = None) -> ContractMaster:
    cs = [OptionContract(f"N{k}{r}", "NIFTY", EXP, float(k), r, lot, 0.05, freeze_qty=freeze)
          for k in range(lo, hi + 1, 50) for r in ("CE", "PE")]
    return ContractMaster(cs, "test", NOW)


def q(sym: str, bid, ask, qty=10_000, ts=NOW) -> Quote:
    return Quote(sym, bid, ask, qty, qty, None, ts, NOW)


def book_quotes(prices: dict[str, tuple[float, float]], **kw):
    def fetch(contracts):
        return {c.symbol: q(c.symbol, *prices[c.symbol], **kw) for c in contracts if c.symbol in prices}
    return fetch


# S = 25025 sits midway between 25000 and 25050: the lower strike is at the money.
# Straddle mid 150 + 140 = 290, so shorts at >= 25315 (25350 CE) and <= 24735 (24700 PE).
BASE = {"N25000CE": (149.5, 150.5), "N25000PE": (139.5, 140.5),
        "N25350CE": (30.0, 30.5), "N24700PE": (34.0, 34.5),
        "N25450CE": (15.0, 15.5), "N24600PE": (18.0, 18.5)}
INDEX = IndexQuote(25025.0, NOW, NOW)


def plan(prices=BASE, equity=500_000.0, m=None, **kw) -> Plan | Skip:
    return plan_entry(m or master(), EXP, INDEX, book_quotes(prices, **kw), NOW, equity, FNO_GROWW)


# -- fee card -------------------------------------------------------------------

def test_option_costs_match_the_registered_card():
    c = option_order_costs(30.0, 65, "SELL", FNO_GROWW)
    t = 30.0 * 65
    assert math.isclose(c.stt, t * 0.0015)
    assert math.isclose(c.exchange_txn, t * 0.0003553)
    assert c.stamp_duty == 0
    assert math.isclose(c.gst, (20 + t * 0.0003553 + t * 0.000001) * 0.18)
    b = option_order_costs(10.0, 65, "BUY", FNO_GROWW)
    assert b.stt == 0 and math.isclose(b.stamp_duty, 650 * 0.00003)


def test_settlement_costs_only_in_the_money_and_stt_only_for_the_holder():
    assert option_settlement_costs(0.0, 65, True, FNO_GROWW).total == 0
    long_ = option_settlement_costs(100.0, 65, True, FNO_GROWW)
    short = option_settlement_costs(100.0, 65, False, FNO_GROWW)
    assert math.isclose(long_.stt, 6500 * 0.0015) and short.stt == 0
    assert long_.brokerage == 20 and short.brokerage == 20       # the conservative reading


def test_zerodha_futures_brokerage_is_the_lower_of_pct_or_flat():
    assert futures_order_costs(25000, 65, "SELL", FNO_ZERODHA).brokerage == 20
    assert math.isclose(futures_order_costs(100, 10, "BUY", FNO_ZERODHA).brokerage, 1000 * 0.0003)
    assert futures_order_costs(25000, 65, "BUY", FNO_GROWW).stt == 0


# -- strikes, checks, size --------------------------------------------------------

def test_strikes_follow_the_straddle_and_the_lower_strike_wins_a_tie():
    p = plan()
    assert isinstance(p, Plan), p
    assert p.atm_strike == 25000 and math.isclose(p.expected_move, 290.0)
    got = {g["role"]: g["strike"] for g in p.legs}
    assert got == {"short_call": 25350, "short_put": 24700, "long_call": 25450, "long_put": 24600}


def test_fills_are_at_the_touch_never_the_mid():
    p = plan()
    px = {g["role"]: g["price"] for g in p.legs}
    assert px == {"short_call": 30.0, "short_put": 34.0, "long_call": 15.5, "long_put": 18.5}
    assert math.isclose(p.credit, 30.0 + 34.0 - 15.5 - 18.5)


def test_maximum_loss_and_lots():
    p = plan()
    assert p.lots == 1 and p.qty == 65
    # Index 100 beyond the long call: short 200 and long 100 in the money.
    edge = round(20 + 200 * 65 * 1e-6 + (20 + 200 * 65 * 1e-6) * 0.18, 2) \
        + round(20 + 100 * 65 * 1e-6 + (20 + 100 * 65 * 1e-6) * 0.18 + 100 * 65 * 0.0015, 2)
    assert math.isclose(p.settlement_allowance, edge, abs_tol=0.02)
    assert math.isclose(p.max_loss, (100 - p.credit) * 65 + p.entry_costs_total + p.settlement_allowance)
    assert p.max_loss <= 0.01 * 500_000 < max_loss(p.credit, p.legs, 65, 2, p.entry_costs_total * 2,
                                                   REGISTERED, FNO_GROWW)
    assert plan(equity=1_000_000).lots == 2


def test_zero_lots_is_no_trade_with_the_arithmetic():
    s = plan(equity=300_000)
    assert isinstance(s, Skip) and s.code == "zero_lots"
    assert s.data["max_loss_1_lot"] > s.data["budget"] == 3000


def test_freeze_quantity_caps_lots():
    p = plan(equity=10_000_000, m=master(freeze=130))
    assert isinstance(p, Plan) and p.lots == 2


def test_credit_floor_and_bad_data():
    thin = dict(BASE, **{"N25350CE": (20.0, 20.5), "N24700PE": (22.0, 22.5)})
    s = plan(thin)
    assert isinstance(s, Skip) and s.code == "credit_below_floor"
    crossed = dict(BASE, **{"N25450CE": (0.05, 0.10), "N24600PE": (0.05, 0.10),
                            "N25350CE": (60.0, 60.5), "N24700PE": (45.0, 45.5)})
    s = plan(crossed)
    assert isinstance(s, Skip) and s.code == "credit_not_below_wing"


def test_quote_checks():
    wide = dict(BASE, **{"N25350CE": (28.0, 31.5)})
    assert plan(wide).code == "leg_quote"
    one_sided = dict(BASE, **{"N24600PE": (0.0, 18.5)})
    assert plan(one_sided).code == "leg_quote"
    stale = plan(ts=NOW - timedelta(seconds=121))
    assert isinstance(stale, Skip) and stale.code == "atm_quote" and "old" in stale.detail
    assert plan(qty=64).code == "touch_size"
    unknown = plan(qty=None)
    assert isinstance(unknown, Plan) and "touch size unknown" in unknown.flags


def test_missing_leg_or_lot_mismatch_is_no_trade():
    m = master(hi=25400)                                 # no 25450 call
    assert plan(m=m).code == "leg_not_listed"
    cs = [c if c.symbol != "N24600PE" else OptionContract(c.symbol, "NIFTY", EXP, c.strike, "PE", 75, 0.05)
          for c in master().contracts]
    assert plan(m=ContractMaster(cs, "t", NOW)).code == "lot_size_mismatch"


def test_stale_index_is_no_trade():
    old = IndexQuote(25025.0, NOW - timedelta(minutes=5), NOW)
    s = plan_entry(master(), EXP, old, book_quotes(BASE), NOW, 500_000, FNO_GROWW)
    assert isinstance(s, Skip) and s.code == "index_stale"


# -- calendar -------------------------------------------------------------------

def test_eligibility_counts_sessions_to_expiry():
    wed, thu, fri = date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)
    assert sessions_after(wed, EXP, CAL) == 4
    assert ineligible(wed, EXP, CAL) is None and ineligible(thu, EXP, CAL) is None
    assert ineligible(fri, EXP, CAL) and ineligible(EXP, EXP, CAL) == "expiry day"
    hol = TradingCalendar(holidays={"2026-11-16": "x"})
    assert ineligible(thu, EXP, hol)                     # Fri, Tue: two sessions


# -- settlement -----------------------------------------------------------------

def _trade(p: Plan) -> dict:
    return p.as_dict()


def test_settlement_pnl_in_each_zone():
    t = _trade(plan())
    c, qty, ec = t["credit"], t["qty"], t["entry_costs_total"]
    inside = settle(t, 25100.0, FNO_GROWW)
    assert math.isclose(inside["net"], c * qty - ec) and inside["settlement_costs_total"] == 0
    between = settle(t, 25400.0, FNO_GROWW)            # short call 50 in the money, long call out
    assert math.isclose(between["gross_points"], c - 50)
    edge = settle(t, 25550.0, FNO_GROWW)                # W beyond the long call: the edge of M's zone
    assert math.isclose(edge["gross_points"], c - 100) and math.isclose(edge["R"], -1.0, abs_tol=1e-12)
    beyond = settle(t, 25900.0, FNO_GROWW)              # further out only the long leg's STT grows
    assert -1.01 < beyond["R"] < -1.0


def test_r_never_below_minus_two_and_never_above_the_credit():
    t = _trade(plan())
    for v in np.linspace(20000, 30000, 401):
        r = settle(t, float(v), FNO_GROWW)
        assert r["R"] > -2 and r["net"] <= t["credit"] * t["qty"]
        if 24500 <= v <= 25550:
            assert r["R"] >= -1 - 1e-12


# -- the statistic ----------------------------------------------------------------

def test_bet_sizes_use_only_earlier_trades():
    r = np.array([0.4, -1.0, 0.3, 0.5, -0.2])
    lam = bet_sizes(r)
    r2 = r.copy()
    r2[3] = -1.0
    assert (bet_sizes(r2)[:4] == lam[:4]).all() and lam[0] == 0.0
    assert (lam >= 0).all() and (lam <= 0.5).all()


def test_a_ruinous_factor_stops_the_wealth_for_good():
    path = betting_path([0.5] * 10 + [-2.5] + [0.5] * 10)
    assert path[10] == 0 and (path[10:] == 0).all()


def test_outcomes_in_order():
    start = 500_000.0
    strong = np.array([0.4, 0.4, 0.4, 0.4, 0.4, -1.0] * 40)
    v, k = _outcome(strong, strong * 5000, start, REGISTERED)
    assert v.startswith("PASS") and k is not None
    weak = np.array([0.3, 0.3, -1.0] * 20)
    v, k = _outcome(weak, weak * 5000, start, REGISTERED)
    assert v.startswith("FAIL — futility") and k == 60
    harm = np.array([-1.0] * 11)
    v, k = _outcome(harm, harm * 5000, start, REGISTERED)
    assert "harm" in v and k == 10
    flat = np.tile([0.36, -0.30, 0.36, -0.36, 0.0], 50) * 1.0
    v, k = _outcome(flat, flat * 5000, start, REGISTERED)
    assert v.startswith("FAIL — no evidence") and k == 250


# -- feeds and model ----------------------------------------------------------------

def test_kite_rows_are_filtered_and_typed():
    rows = [{"name": "NIFTY", "instrument_type": "CE", "segment": "NFO-OPT", "expiry": EXP, "strike": 25000.0,
             "lot_size": 65, "tick_size": 0.05, "tradingsymbol": "NIFTY26N1725000CE", "exchange": "NFO",
             "instrument_token": 1},
            {"name": "NIFTY", "instrument_type": "FUT", "segment": "NFO-FUT", "expiry": EXP, "strike": 0.0,
             "lot_size": 65, "tick_size": 0.1, "tradingsymbol": "NIFTY26NOVFUT", "exchange": "NFO"},
            {"name": "BANKNIFTY", "instrument_type": "PE", "segment": "NFO-OPT", "expiry": EXP, "strike": 50000.0,
             "lot_size": 30, "tick_size": 0.05, "tradingsymbol": "BANKNIFTY26NOV50000PE", "exchange": "NFO"}]
    out = from_kite_rows(rows, "NIFTY")
    assert len(out) == 1 and out[0].lot_size == 65 and out[0].expiry == EXP
    try:
        from_kite_rows(rows[1:], "NIFTY")
        raise AssertionError("an empty master must be refused")
    except DataUnavailable:
        pass


def test_groww_csv_without_fno_columns_is_refused_by_name():
    try:
        from_groww_frame(pd.DataFrame({"exchange": ["NSE"], "trading_symbol": ["X"]}), "NIFTY")
        raise AssertionError("missing columns must be refused")
    except DataUnavailable as exc:
        assert "lot_size" in str(exc)


def test_kite_quotes_take_the_best_level_of_depth():
    class FakeKite:
        def quote(self, keys):
            return {"NFO:A": {"last_price": 30.1, "timestamp": datetime(2026, 11, 11, 10, 0, 0),
                              "depth": {"buy": [{"price": 30.0, "quantity": 650, "orders": 3}],
                                        "sell": [{"price": 0, "quantity": 0}, {"price": 30.5, "quantity": 975}]}}}
    feed = object.__new__(KiteFnoFeed)
    feed._kite = FakeKite()
    out = feed.quotes([OptionContract("A", "NIFTY", EXP, 25000.0, "CE", 65, 0.05)])
    a = out["A"]
    assert (a.bid, a.bid_qty, a.ask, a.ask_qty) == (30.0, 650, 30.5, 975)
    assert a.exchange_ts == datetime(2026, 11, 11, 10, 0, tzinfo=IST)


def test_secrets_in_the_environment_are_refused():
    os.environ["GROWW_TOTP_SECRET"] = "x"
    try:
        refuse_secrets_in_env()
        raise AssertionError("a TOTP seed in the environment must be refused")
    except DataUnavailable as exc:
        assert "GROWW_TOTP_SECRET" in str(exc)
    finally:
        del os.environ["GROWW_TOTP_SECRET"]


# -- the engine, on synthetic data ------------------------------------------------------

class Clock:
    def __init__(self, at: datetime):
        self.now = at

    def __call__(self):
        return self.now


def mock_engine(root: Path | None = None, start=datetime(2026, 11, 9, 9, 0, tzinfo=IST), second=None, **kw):
    root = root or tmpdir()
    clock = Clock(start)
    feed = MockFnoFeed(clock, calendar=CAL, **kw)
    eng = FnoEngine(feed, root / "t_mock", calendar=CAL, now_fn=clock,
                    second_close=second or (lambda u, d: feed.index_close(u, d)))
    eng.init(500_000)
    return eng, clock, feed


def at(clock: Clock, d: date, hh: int, mm: int = 0) -> None:
    clock.now = datetime.combine(d, time(hh, mm), IST)


def test_mock_feed_is_refused_outside_a_mock_directory_and_vice_versa():
    clock = Clock(NOW)
    try:
        FnoEngine(MockFnoFeed(clock, calendar=CAL), tmpdir() / "paper_fno", calendar=CAL)
        raise AssertionError("synthetic data must never reach the paper journal")
    except FeedRefused:
        pass

    class RealLooking(FnoFeed):
        key = "kite"
        def contract_master(self, u): ...
        def index_quote(self, u): ...
        def quotes(self, c): ...
        def index_close(self, u, d): ...
    try:
        FnoEngine(RealLooking(), tmpdir() / "x_mock", calendar=CAL)
        raise AssertionError("a real feed must not write into a mock directory")
    except FeedRefused:
        pass


def test_one_entry_per_expiry_inside_the_window_only():
    eng, clock, _ = mock_engine()
    wed = date(2026, 11, 11)
    at(clock, wed, 9, 45)
    eng.run_pass()
    assert not eng.store.load().trades                   # before 10:00
    at(clock, wed, 10, 0)
    rep = eng.run_pass()
    book = eng.store.load()
    assert len(book.trades) == 1 and book.trades[0]["expiry"] == "2026-11-17", rep.text()
    for hh in (10, 11, 13):
        at(clock, wed, hh, 30)
        eng.run_pass()
    at(clock, date(2026, 11, 12), 10, 0)
    eng.run_pass()
    assert len(eng.store.load().trades) == 1
    kinds = [r["type"] for r in eng.journal.records()]
    assert kinds.count("FNO_ENTRY") == 1


def test_settles_after_the_close_with_a_cross_checked_value():
    eng, clock, feed = mock_engine()
    at(clock, date(2026, 11, 11), 10, 0)
    eng.run_pass()
    at(clock, EXP, 15, 45)
    eng.run_pass()
    assert eng.store.load().trades[0]["status"] == "OPEN"     # before 16:00
    at(clock, EXP, 16, 0)
    eng.run_pass()
    t = eng.store.load().trades[0]
    assert t["status"] == "SETTLED" and "cross-checked" in t["settlement"]["source"]
    assert math.isclose(t["settlement"]["value"], feed.index_close("NIFTY", EXP))
    assert verify_file(eng.journal.path).ok


def test_settlement_waits_then_uses_the_feed_alone_after_three_days():
    eng, clock, _ = mock_engine(second=lambda u, d: None)
    at(clock, date(2026, 11, 11), 10, 0)
    eng.run_pass()
    at(clock, EXP, 16, 0)
    eng.run_pass()
    assert eng.store.load().trades[0]["status"] == "AWAITING_SETTLEMENT"
    at(clock, date(2026, 11, 19), 16, 0)                 # 2 trading days after
    eng.run_pass()
    assert eng.store.load().trades[0]["status"] == "AWAITING_SETTLEMENT"
    at(clock, date(2026, 11, 20), 9, 0)                  # 3 trading days after
    eng.run_pass()
    t = eng.store.load().trades[0]
    assert t["status"] == "SETTLED" and t["settlement"]["flags"]


def test_a_mismatch_waits_for_a_manual_value_then_worst_case_after_ten_days():
    eng, clock, feed = mock_engine(second=lambda u, d: 1.01 * feed.index_close(u, d))
    at(clock, date(2026, 11, 11), 10, 0)
    eng.run_pass()
    at(clock, EXP, 16, 0)
    eng.run_pass()
    assert eng.store.load().trades[0]["status"] == "AWAITING_SETTLEMENT"
    record_manual_settlement(eng.state_dir, "2026-11-17", 25010.0, "NSE closing value, test")
    at(clock, date(2026, 11, 18), 9, 0)
    eng.run_pass()
    t = eng.store.load().trades[0]
    assert t["status"] == "SETTLED" and t["settlement"]["value"] == 25010.0
    assert t["settlement"]["source"].startswith("manual")

    eng2, clock2, feed2 = mock_engine(second=lambda u, d: None)
    feed2.index_close = lambda u, d: None
    at(clock2, date(2026, 11, 11), 10, 0)
    eng2.run_pass()
    at(clock2, date(2026, 12, 1), 9, 0)                  # 10 trading days after expiry
    eng2.run_pass()
    t2 = eng2.store.load().trades[0]
    assert t2["status"] == "SETTLED" and t2["R"] == -1.0 and "worst case" in t2["settlement"]["source"]


def test_a_pending_trade_does_not_block_the_next_entry_but_its_m_is_held_back():
    eng, clock, _ = mock_engine(second=lambda u, d: None)
    at(clock, date(2026, 11, 11), 10, 0)
    eng.run_pass()
    m = eng.store.load().trades[0]["max_loss"]
    at(clock, date(2026, 11, 18), 10, 0)
    eng.run_pass()
    book = eng.store.load()
    assert len(book.trades) == 2
    assert math.isclose(book.trades[1]["equity_for_sizing"], 500_000 - m)


def test_kill_switch_blocks_entries_and_is_journaled():
    eng, clock, _ = mock_engine()
    (eng.state_dir / "KILL").write_text("test")
    at(clock, date(2026, 11, 11), 10, 0)
    rep = eng.run_pass()
    assert not eng.store.load().trades and "kill" in rep.decision
    assert any(r["type"] == "FNO_ENTRY_BLOCKED" for r in eng.journal.records())


def test_a_missed_cycle_is_recorded():
    eng, clock, _ = mock_engine()
    (eng.state_dir / "KILL").write_text("test")
    at(clock, date(2026, 11, 11), 10, 0)
    eng.run_pass()
    (eng.state_dir / "KILL").unlink()
    at(clock, date(2026, 11, 18), 10, 0)
    eng.run_pass()
    recs = [r for r in eng.journal.records() if r["type"] == "FNO_CYCLE_NO_TRADE"]
    assert recs and recs[0]["payload"]["expiry"] == "2026-11-17" and "kill" in recs[0]["payload"]["last_reason"]


def test_harm_stop_halts_entries():
    eng, clock, feed = mock_engine()
    book = eng.store.load()
    book.trades.append({"id": "V-x", "expiry": "2026-11-10", "status": "AWAITING_SETTLEMENT", "max_loss": 50_001.0,
                        "credit": 30.0, "qty": 65, "entry_costs_total": 0.0, "legs": [], "net": None, "R": None})
    eng.store.save(book)
    feed.index_close = lambda u, d: None
    at(clock, date(2026, 11, 24), 9, 0)                  # 10 trading days after 2026-11-10
    rep = eng.run_pass()
    book = eng.store.load()
    assert book.halted and rep.urgent
    at(clock, date(2026, 11, 25), 10, 0)
    eng.run_pass()
    assert len(eng.store.load().trades) == 1


def test_two_passes_cannot_run_at_once():
    eng, clock, _ = mock_engine()
    with exclusive(eng.state_dir):
        try:
            eng.run_pass()
            raise AssertionError("a second pass must be refused")
        except RunnerBusy:
            pass


def test_flatten_counts_the_trade_at_its_realised_pnl():
    eng, clock, _ = mock_engine()
    at(clock, date(2026, 11, 11), 10, 0)
    eng.run_pass()
    at(clock, date(2026, 11, 12), 11, 0)
    rep = eng.flatten("test")
    t = eng.store.load().trades[0]
    assert t["status"] == "FLATTENED" and t["flatten"]["off_rule"], rep.text()
    out = evaluate(eng.journal.path)
    assert out["settled"] == 1 and out["trades"][0]["how"].startswith("flattened")


def test_evaluate_reads_the_journal_and_stops_at_the_first_unsettled_trade():
    eng, clock, feed = mock_engine(second=lambda u, d: None)
    real = feed.index_close
    feed.index_close = lambda u, d: None if d == EXP else real(u, d)
    at(clock, date(2026, 11, 11), 10, 0)
    eng.run_pass()
    at(clock, date(2026, 11, 18), 10, 0)
    eng.run_pass()
    at(clock, date(2026, 11, 27), 16, 0)                 # the second trade settles, the first cannot
    eng.run_pass()
    book = eng.store.load()
    assert [t["status"] for t in book.trades] == ["AWAITING_SETTLEMENT", "SETTLED"]
    out = evaluate(eng.journal.path)
    assert out["journal_verifies"] and out["settled"] == 0
    assert out["open_or_unsettled"] == ["V-2026-11-17"]


def test_demo_weeks_produce_a_verifying_journal():
    eng, clock, _ = mock_engine()
    d = date(2026, 11, 9)
    while d < date(2026, 12, 31):
        if CAL.is_trading_day(d):
            for hh, mm in ((10, 0), (12, 0), (16, 0)):
                at(clock, d, hh, mm)
                eng.run_pass()
        d += timedelta(days=1)
    out = evaluate(eng.journal.path)
    assert out["journal_verifies"] and out["settled"] >= 6
    assert all(t["lots"] >= 1 for t in eng.store.load().trades)


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
