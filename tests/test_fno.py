"""
Offline tests for the F&O paper engine and strategy V (research/PREREGISTRATION_3.md).
No market data: quotes here are hand-written or synthetic (MockFnoFeed).

    PYTHONPATH=. python tests/test_fno.py
"""

from __future__ import annotations

import dataclasses
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
from trading.fno.evaluate import evaluate, verdict_from
from trading.fno.feeds import FnoFeed, KiteFnoFeed, MockFnoFeed, fsp_from_bhavcopy, refuse_secrets_in_env
from trading.fno.model import ContractMaster, IndexQuote, OptionContract, Quote, from_groww_frame, from_kite_rows
from trading.fno.state import State, contiguous_run
from trading.fno.strategy_v import (REGISTERED, Plan, Skip, VParams, eligible_sessions, max_loss, plan_entry,
                                    sessions_after, settle, slot_of, slot_times)
from trading.journal import Journal, verify_file

CAL = TradingCalendar(holidays={"2026-01-26": "x", "2027-01-26": "x"})   # weekends + one holiday per year
EXP = date(2026, 11, 17)                                 # a Tuesday
NOW = datetime(2026, 11, 11, 10, 0, tzinfo=IST)          # the Wednesday before it


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="fno_test_"))


def master(lot: int = 65, lo: int = 24000, hi: int = 26000, freeze: int | None = None) -> ContractMaster:
    cs = [OptionContract(f"N{k}{r}", "NIFTY", EXP, float(k), r, lot, 0.05, freeze_qty=freeze)
          for k in range(lo, hi + 1, 50) for r in ("CE", "PE")]
    return ContractMaster(cs, "test", NOW)


def q(sym: str, bid, ask, qty=10_000, ts=NOW, fetched=NOW) -> Quote:
    return Quote(sym, bid, ask, qty, qty, None, ts, fetched)


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


def plan(prices=BASE, m=None, p: VParams = REGISTERED, index=INDEX, **kw) -> Plan | Skip:
    return plan_entry(m or master(), EXP, index, book_quotes(prices, **kw), FNO_GROWW, p)


# -- fee card -------------------------------------------------------------------

def test_option_costs_match_the_registered_card_rounded_half_up():
    c = option_order_costs(30.0, 65, "SELL", FNO_GROWW)
    t = 30.0 * 65
    assert c.stt == 2.93                                 # 2.925 rounds half-up
    assert c.exchange_txn == round(t * 0.0003553, 2) and c.stamp_duty == 0
    assert c.gst == round((20 + t * 0.0003553 + t * 0.000001) * 0.18, 2)
    b = option_order_costs(10.0, 65, "BUY", FNO_GROWW)
    assert b.stt == 0 and b.stamp_duty == 0.02


def test_settlement_costs_only_in_the_money_stt_only_for_the_holder_sebi_on_notional():
    assert option_settlement_costs(0.0, 65, True, FNO_GROWW).total == 0
    long_ = option_settlement_costs(100.0, 65, True, FNO_GROWW, notional_price=25550)
    short = option_settlement_costs(100.0, 65, False, FNO_GROWW, notional_price=25550)
    assert long_.stt == 9.75 and short.stt == 0
    assert long_.brokerage == 20 and short.brokerage == 20       # the conservative reading
    assert long_.sebi == round(25550 * 65 * 0.000001, 2)


def test_zerodha_futures_brokerage_is_the_lower_of_pct_or_flat():
    assert futures_order_costs(25000, 65, "SELL", FNO_ZERODHA).brokerage == 20
    assert math.isclose(futures_order_costs(100, 10, "BUY", FNO_ZERODHA).brokerage, 0.3)
    assert futures_order_costs(25000, 65, "BUY", FNO_GROWW).stt == 0


# -- strikes, checks, size --------------------------------------------------------

def test_strikes_follow_the_straddle_and_the_lower_strike_wins_a_tie():
    p = plan()
    assert isinstance(p, Plan), p
    assert p.atm_strike == 25000 and p.expected_move == 290.0
    got = {g["role"]: g["strike"] for g in p.legs}
    assert got == {"short_call": 25350, "short_put": 24700, "long_call": 25450, "long_put": 24600}


def test_fills_are_at_the_touch_never_the_mid():
    p = plan()
    px = {g["role"]: g["price"] for g in p.legs}
    assert px == {"short_call": 30.0, "short_put": 34.0, "long_call": 15.5, "long_put": 18.5}
    assert p.credit == 30.0


def test_credit_exactly_on_a_boundary_is_decided_exactly():
    at_floor = dict(BASE, **{"N25350CE": (25.05, 25.55), "N24700PE": (28.95, 29.45)})   # 25.05 + 28.95 - 34 = 20.00
    assert isinstance(plan(at_floor, p=dataclasses.replace(REGISTERED, risk_budget=10_000)), Plan)
    below = dict(BASE, **{"N25350CE": (25.0, 25.5), "N24700PE": (28.95, 29.45)})
    assert plan(below).code == "credit_below_floor"


def test_maximum_loss_is_the_fee_card_at_the_zone_edge():
    p = plan()
    assert p.lots == 1 and p.qty == 65
    t = p.as_dict()
    edge = settle(t, 25550.0, FNO_GROWW)                 # W beyond the long call
    assert math.isclose(edge["R"], -1.0, abs_tol=1e-12)
    assert p.settlement_allowance == edge["settlement_costs_total"]
    assert p.max_loss <= REGISTERED.risk_budget < max_loss(p.credit, p.legs, 65, 2, p.entry_costs_total * 2,
                                                             REGISTERED, FNO_GROWW)


def test_budget_is_fixed_and_zero_lots_is_no_trade_with_the_arithmetic():
    thin = dict(BASE, **{"N25350CE": (24.0, 24.5), "N24700PE": (28.0, 28.5)})   # credit 18 -> below floor
    assert plan(thin).code == "credit_below_floor"
    low = dict(BASE, **{"N25350CE": (21.0, 21.5), "N24700PE": (37.0, 37.5)})    # credit 24: M(1) above Rs5,000
    s = plan(low)
    assert isinstance(s, Skip) and s.code == "zero_lots"
    assert s.data["max_loss_1_lot"] > s.data["budget"] == 5000


def test_freeze_quantity_caps_lots():
    big = dataclasses.replace(REGISTERED, risk_budget=100_000)
    assert plan(p=big).lots == 10
    assert plan(p=big, m=master(freeze=130)).lots == 2


def test_credit_cap_and_bad_data():
    crossed = dict(BASE, **{"N25450CE": (0.05, 0.10), "N24600PE": (0.05, 0.10),
                            "N25350CE": (45.0, 45.5), "N24700PE": (45.0, 45.5)})
    s = plan(crossed)
    assert isinstance(s, Skip) and s.code == "credit_above_cap"


def test_quote_checks():
    assert plan(dict(BASE, **{"N25350CE": (28.0, 31.5)})).code == "leg_quote"
    assert plan(dict(BASE, **{"N24600PE": (0.0, 18.5)})).code == "leg_quote"
    stale = plan(ts=NOW - timedelta(seconds=121))
    assert stale.code == "atm_quote" and "old" in stale.detail
    assert isinstance(plan(ts=NOW - timedelta(seconds=120)), Plan)
    assert plan(qty=64).code == "touch_size"
    unknown = plan(qty=None)
    assert isinstance(unknown, Plan) and "touch size unknown" in unknown.flags


def test_snapshot_must_be_coherent_and_dated_today():
    assert plan(fetched=NOW + timedelta(seconds=11)).code == "slow_snapshot"
    yesterday = NOW - timedelta(days=1)
    old_index = IndexQuote(25025.0, None, NOW)
    s = plan(ts=None, index=old_index)
    assert s.code == "no_timestamp_today"
    assert isinstance(plan(ts=None), Plan)                # the index carries today's timestamp
    assert yesterday < NOW


def test_missing_leg_or_lot_mismatch_is_no_trade():
    assert plan(m=master(hi=25400)).code == "leg_not_listed"
    cs = [c if c.symbol != "N24600PE" else OptionContract(c.symbol, "NIFTY", EXP, c.strike, "PE", 75, 0.05)
          for c in master().contracts]
    assert plan(m=ContractMaster(cs, "t", NOW)).code == "lot_size_mismatch"


def test_stale_index_is_no_trade():
    old = IndexQuote(25025.0, NOW - timedelta(minutes=5), NOW)
    assert plan(index=old).code == "index_stale"


# -- calendar and slots -----------------------------------------------------------

def test_eligibility_counts_sessions_to_expiry():
    prev = date(2026, 11, 10)
    assert sessions_after(date(2026, 11, 11), EXP, CAL) == 4
    assert eligible_sessions(EXP, prev, CAL) == [date(2026, 11, 11), date(2026, 11, 12)]
    hol = TradingCalendar(holidays={"2026-11-16": "x"})
    assert eligible_sessions(EXP, prev, hol) == [date(2026, 11, 11)]


def test_seventeen_slots_with_a_two_minute_window():
    ts = slot_times()
    assert len(ts) == 17 and ts[0] == time(10, 0) and ts[-1] == time(14, 0)
    d = date(2026, 11, 11)
    assert slot_of(datetime.combine(d, time(14, 0, 0, 1), IST)) == time(14, 0)
    assert slot_of(datetime.combine(d, time(14, 1, 59), IST)) == time(14, 0)
    assert slot_of(datetime.combine(d, time(14, 2, 0), IST)) is None
    assert slot_of(datetime.combine(d, time(9, 59, 59), IST)) is None


def test_the_weekly_run_stops_before_the_monthlies():
    xs = [date(2026, 11, 17), date(2026, 11, 24), date(2026, 12, 1), date(2026, 12, 8), date(2026, 12, 29),
          date(2027, 3, 30)]
    assert contiguous_run(xs) == xs[:4]


# -- settlement -----------------------------------------------------------------

def test_settlement_pnl_in_each_zone():
    t = plan().as_dict()
    c, qty, ec = t["credit"], t["qty"], t["entry_costs_total"]
    inside = settle(t, 25100.0, FNO_GROWW)
    assert math.isclose(inside["net"], round(c * qty - ec, 2)) and inside["settlement_costs_total"] == 0
    between = settle(t, 25400.0, FNO_GROWW)
    assert math.isclose(between["gross_points"], c - 50)
    beyond = settle(t, 25900.0, FNO_GROWW)
    assert -1.02 < beyond["R"] < -1.0


def test_r_bounds_across_settlement_values():
    t = plan().as_dict()
    for v in np.linspace(20000, 30000, 401):
        r = settle(t, float(v), FNO_GROWW)
        assert r["R"] > -2 and r["net"] <= t["credit"] * t["qty"]
        if 24500 <= v <= 25550:
            assert r["R"] >= -1 - 1e-12


def test_fsp_is_derived_from_nse_settlement_prices_or_refused():
    rows = [{"TckrSymb": "NIFTY", "FinInstrmTp": "IDO", "XpryDt": "2026-11-17", "StrkPric": k, "OptnTp": r,
             "SttlmPric": max(0.0, 25012.35 - k) if r == "CE" else max(0.0, k - 25012.35)}
            for k in (24900, 24950, 25000, 25050, 25100) for r in ("CE", "PE")]
    v, info = fsp_from_bhavcopy(pd.DataFrame(rows), "NIFTY", EXP)
    assert v == 25012.35 and info["n"] == 5
    rows[0]["SttlmPric"] = 113.0                         # one contract disagrees by 0.65
    assert fsp_from_bhavcopy(pd.DataFrame(rows), "NIFTY", EXP)[0] is None
    v, info = fsp_from_bhavcopy(pd.DataFrame([{"TckrSymb": "NIFTY"}]), "NIFTY", EXP)
    assert v is None and "SttlmPric" in info["error"]


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


def _items(rs, net=5000.0):
    return [{"R": r, "net": r * net} for r in rs]


def test_outcomes_in_order():
    v, k = verdict_from(_items([0.4] * 5 + [-1.0]) * 40, 0)
    assert v.startswith("PASS") and k is not None
    v, k = verdict_from(_items([0.3, 0.3, -1.0] * 20), 60)
    assert v.startswith("FAIL — futility") and k == 60
    v, k = verdict_from(_items([-1.0] * 11), 11)
    assert "harm" in v and k == 10
    v, k = verdict_from(_items(list(np.tile([0.36, -0.30, 0.36, -0.36, 0.0], 50))), 250)
    assert v.startswith("FAIL — no evidence") and k == 250
    missed = [{"R": -1.0, "net": None}] * 11                    # MISSED cycles carry no rupees
    v, k = verdict_from(missed, 11)
    assert "harm" not in v
    v, _ = verdict_from(_items([0.1]), 300)
    assert v.startswith("FAIL — no evidence: 300")


# -- feeds and model ----------------------------------------------------------------

def test_kite_rows_are_filtered_and_typed():
    rows = [{"name": "NIFTY", "instrument_type": "CE", "segment": "NFO-OPT", "expiry": EXP, "strike": 25000.0,
             "lot_size": 65, "tick_size": 0.05, "tradingsymbol": "NIFTY26N1725000CE", "exchange": "NFO",
             "instrument_token": 1},
            {"name": "NIFTY", "instrument_type": "FUT", "segment": "NFO-FUT", "expiry": EXP, "strike": 0.0,
             "lot_size": 65, "tick_size": 0.1, "tradingsymbol": "NIFTY26NOVFUT", "exchange": "NFO"}]
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


def test_kite_quotes_take_the_best_level_of_depth_and_the_quote_timestamp():
    class FakeKite:
        def quote(self, keys):
            return {"NFO:A": {"last_price": 30.1, "timestamp": datetime(2026, 11, 11, 10, 0, 0),
                              "last_trade_time": datetime(2026, 11, 11, 9, 50, 0),
                              "buy_quantity": 99999, "sell_quantity": 99999,
                              "depth": {"buy": [{"price": 30.0, "quantity": 650, "orders": 3}],
                                        "sell": [{"price": 0, "quantity": 0}, {"price": 30.5, "quantity": 975}]}}}
    feed = object.__new__(KiteFnoFeed)
    feed._kite = FakeKite()
    a = feed.quotes([OptionContract("A", "NIFTY", EXP, 25000.0, "CE", 65, 0.05)])["A"]
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


class Fsp:
    """NSE's settlement file, simulated: the mock's close, or nothing."""
    def __init__(self, feed, available=True):
        self.feed, self.available = feed, available

    def __call__(self, u, d):
        return (self.feed.index_close(u, d), {"url": "mock"}) if self.available else (None, {"error": "unreachable"})


def mock_engine(start=datetime(2026, 11, 2, 9, 0, tzinfo=IST), second=None, fsp=True, p=REGISTERED, **kw):
    root = tmpdir()
    clock = Clock(start)
    feed = MockFnoFeed(clock, calendar=CAL, **kw)
    eng = FnoEngine(feed, root / "t_mock", calendar=CAL, now_fn=clock, params=p,
                    second_close=second or (lambda u, d: feed.index_close(u, d)),
                    fsp_source=Fsp(feed, fsp))
    eng.init()
    return eng, clock, feed


def at(clock: Clock, d: date, hh: int, mm: int = 0) -> datetime:
    clock.now = datetime.combine(d, time(hh, mm), IST)
    return clock.now


def day(eng, clock, d: date, slots=None, settle_at=(16, 0)):
    """Run a trading day: a morning pass, the given entry slots (default all), and a settlement pass."""
    at(clock, d, 9, 30)
    eng.run_pass()
    for t in (slot_times() if slots is None else slots):
        at(clock, d, t.hour, t.minute)
        eng.run_pass(slot=t)
    if settle_at:
        at(clock, d, *settle_at)
        eng.run_pass()


def kinds(eng):
    return [r["type"] for r in eng.journal.records()]


def test_mock_feed_is_refused_outside_a_mock_directory_and_a_draft_registration_refuses_real_feeds():
    try:
        FnoEngine(MockFnoFeed(Clock(NOW), calendar=CAL), tmpdir() / "paper_fno", calendar=CAL)
        raise AssertionError("synthetic data must never reach the paper journal")
    except FeedRefused:
        pass

    class RealLooking(FnoFeed):
        key = "kite"
        def contract_master(self, u): ...
        def index_quote(self, u): ...
        def quotes(self, c): ...
        def index_close(self, u, d): ...
    for name in ("x_mock", "paper_fno"):
        try:
            FnoEngine(RealLooking(), tmpdir() / name, calendar=CAL)
            raise AssertionError(f"a real feed must be refused in {name}")
        except FeedRefused as exc:
            assert name == "x_mock" or "registered hash" in str(exc) or "draft" in str(exc)


def test_the_first_counted_cycle_is_the_one_after_the_current_one():
    eng, clock, _ = mock_engine()                        # init Mon 2026-11-02
    for d in (date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)):
        day(eng, clock, d)
    st = eng.state()
    counted = [e for e, _ in st.counted_cycles()]
    assert counted and counted[0] > date(2026, 11, 3)
    assert all(date.fromisoformat(t["expiry"]) in counted for t in st.trades.values())


def test_one_entry_per_cycle_only_at_a_slot_from_the_runner():
    eng, clock, _ = mock_engine()
    for d in (date(2026, 11, 2), date(2026, 11, 3)):
        day(eng, clock, d)
    wed = date(2026, 11, 4)
    at(clock, wed, 10, 0)
    eng.run_pass()                                       # no slot: settlement only
    assert not eng.state().trades
    at(clock, wed, 10, 3)
    eng.run_pass(slot=time(10, 0))                       # outside the slot's window
    assert not eng.state().trades
    day(eng, clock, wed)
    day(eng, clock, date(2026, 11, 5))
    st = eng.state()
    assert len(st.trades) == 1 and kinds(eng).count("FNO_ENTRY") == 1
    assert next(iter(st.trades.values()))["slot"].endswith("10:00:00+05:30")


def test_a_missed_first_slot_makes_the_cycle_missed_and_it_counts_minus_one():
    eng, clock, _ = mock_engine()
    for d in (date(2026, 11, 2), date(2026, 11, 3)):
        day(eng, clock, d)
    day(eng, clock, date(2026, 11, 4), slots=slot_times()[1:])        # 10:00 not run
    st = eng.state()
    assert not st.trades
    cyc = [c for c in st.cycles.values() if c["outcome"] == "missed"]
    assert cyc and cyc[0]["first_unobserved"].endswith("10:00:00+05:30")
    for d in (date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 10)):
        day(eng, clock, d)
    out = evaluate(eng.journal.path, now=clock.now, cal=CAL)
    assert out["items"][0]["kind"] == "missed" and out["items"][0]["R"] == -1.0


def test_rule_skips_at_every_slot_are_a_no_trade_cycle_outside_the_sequence():
    eng, clock, _ = mock_engine(p=dataclasses.replace(REGISTERED, min_credit=60.0, max_credit=80.0))
    d = date(2026, 11, 2)
    while d <= date(2026, 11, 18):
        if CAL.is_trading_day(d):
            day(eng, clock, d)
        d += timedelta(days=1)
    st = eng.state()
    assert not st.trades and st.cycles
    assert all(c["outcome"] == "no trade" for c in st.cycles.values()), st.cycles
    out = evaluate(eng.journal.path, now=clock.now, cal=CAL)
    assert out["items"] == [] and out["descriptive"]["passes_without_trade_by_check"]["credit_below_floor"] >= 34


def test_kill_switch_blocks_and_misses_the_cycle():
    eng, clock, _ = mock_engine()
    for d in (date(2026, 11, 2), date(2026, 11, 3)):
        day(eng, clock, d)
    (eng.state_dir / "KILL").write_text("test")
    day(eng, clock, date(2026, 11, 4))
    st = eng.state()
    assert not st.trades and any(c["outcome"] == "missed" for c in st.cycles.values())
    assert "FNO_PASS" in kinds(eng)


def test_data_failures_retry_inside_the_slot_and_only_the_last_attempt_counts():
    eng, clock, feed = mock_engine()
    for d in (date(2026, 11, 2), date(2026, 11, 3)):
        day(eng, clock, d)
    real = feed.index_quote
    feed.index_quote = lambda u: (_ for _ in ()).throw(DataUnavailable("feed down"))
    at(clock, date(2026, 11, 4), 10, 0)
    _, status = eng.run_pass(slot=time(10, 0), final=False)
    assert status == "retry" and "FNO_PASS" not in kinds(eng)
    feed.index_quote = real
    _, status = eng.run_pass(slot=time(10, 0), final=True)
    assert status == "done" and len(eng.state().trades) == 1


def test_settles_at_nse_price_after_the_close():
    eng, clock, feed = mock_engine()
    for d in (date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)):
        day(eng, clock, d)
    t = next(iter(eng.state().trades.values()))
    e = date.fromisoformat(t["expiry"])
    d = date(2026, 11, 5)
    while d < e:
        if CAL.is_trading_day(d):
            day(eng, clock, d, settle_at=None)
        d += timedelta(days=1)
    at(clock, e, 15, 45)
    eng.run_pass()
    assert next(iter(eng.state().trades.values()))["status"] == "OPEN"
    at(clock, e, 16, 0)
    eng.run_pass()
    t = next(iter(eng.state().trades.values()))
    assert t["status"] == "SETTLED" and t["settlement"]["source"].startswith("NSE bhavcopy")
    assert math.isclose(t["settlement"]["value"], feed.index_close("NIFTY", e))
    assert verify_file(eng.journal.path).ok


def _open_trade(eng, clock):
    for d in (date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)):
        day(eng, clock, d)
    t = next(iter(eng.state().trades.values()))
    return t, date.fromisoformat(t["expiry"])


def test_without_nse_the_close_rule_settles_only_before_the_change_date():
    eng, clock, feed = mock_engine(fsp=False)
    t, e = _open_trade(eng, clock)
    at(clock, e, 16, 0)
    eng.run_pass()
    t = eng.state().trades[t["id"]]
    assert t["status"] == "SETTLED" and "matching Yahoo" in t["settlement"]["source"]

    late = mock_engine(start=datetime(2026, 12, 7, 9, 0, tzinfo=IST), fsp=False)
    eng2, clock2, _ = late
    d = date(2026, 12, 7)
    while not eng2.state().trades:
        if CAL.is_trading_day(d):
            day(eng2, clock2, d)
        d += timedelta(days=1)
    t2 = next(iter(eng2.state().trades.values()))
    e2 = date.fromisoformat(t2["expiry"])
    at(clock2, e2, 16, 0)
    eng2.run_pass()
    assert eng2.state().trades[t2["id"]]["status"] == "AWAITING_SETTLEMENT"


def test_manual_value_needs_a_failed_later_pass_and_an_nse_url_then_worst_case_after_ten_days():
    eng, clock, feed = mock_engine(fsp=False, second=lambda u, d: None)
    t, e = _open_trade(eng, clock)
    at(clock, e, 16, 0)
    eng.run_pass()
    try:
        record_manual_settlement(eng.state_dir, t["expiry"], 25010.0, "https://nsearchives.nseindia.com/x")
        raise AssertionError("too early: no failed pass on a later trading day yet")
    except ValueError:
        pass
    nxt = e + timedelta(days=1)
    at(clock, nxt, 10, 0)
    eng.run_pass()
    try:
        record_manual_settlement(eng.state_dir, t["expiry"], 25010.0, "my notes")
        raise AssertionError("an NSE URL is required")
    except ValueError:
        pass
    close = feed.index_close("NIFTY", e)
    record_manual_settlement(eng.state_dir, t["expiry"], close, "https://nsearchives.nseindia.com/x")
    at(clock, nxt, 10, 15)
    eng.run_pass()
    t = eng.state().trades[t["id"]]
    assert t["status"] == "SETTLED" and t["settlement"]["source"].startswith("entered by the operator")

    eng2, clock2, feed2 = mock_engine(fsp=False, second=lambda u, d: None)
    t2, e2 = _open_trade(eng2, clock2)
    d, n = e2, 0
    while n < 10:
        d += timedelta(days=1)
        n += CAL.is_trading_day(d)
    at(clock2, d, 9, 0)
    eng2.run_pass()
    t2 = eng2.state().trades[t2["id"]]
    assert t2["status"] == "SETTLED" and t2["R"] == -1.0


def test_a_moved_expiry_is_followed():
    eng, clock, feed = mock_engine()
    t, e = _open_trade(eng, clock)
    feed.expiry_moves[e] = e - timedelta(days=1)
    at(clock, e - timedelta(days=4), 9, 30)               # a day the master is re-read
    eng.run_pass()
    assert "FNO_EXPIRY_MOVED" in kinds(eng)
    assert eng.state().trades[t["id"]]["expiry"] == (e - timedelta(days=1)).isoformat()


def test_duplicate_records_are_void_and_make_the_journal_not_evaluable():
    eng, clock, _ = mock_engine()
    t, _ = _open_trade(eng, clock)
    rec = next(r for r in eng.journal.records() if r["type"] == "FNO_ENTRY")
    eng.journal.append("FNO_ENTRY", asof="2026-11-04", feed="mock", trade=rec["payload"]["trade"])
    st = eng.state()
    assert st.void and len(st.trades) == 1
    out = evaluate(eng.journal.path, now=clock.now, cal=CAL)
    assert out["verdict"].startswith("NOT EVALUABLE") and "void" in out["verdict"]


def test_a_verdict_stops_entries():
    eng, clock, _ = mock_engine()
    for d in (date(2026, 11, 2), date(2026, 11, 3)):
        day(eng, clock, d)
    eng.journal.append("FNO_VERDICT", asof="2026-11-03", feed="mock", verdict="FAIL — test", decided_at=0)
    day(eng, clock, date(2026, 11, 4))
    assert not eng.state().trades


def test_abandonment_after_three_weeks_without_a_master():
    eng, clock, _ = mock_engine()
    day(eng, clock, date(2026, 11, 2))
    at(clock, date(2026, 11, 30), 9, 30)
    rep, _ = eng.run_pass()
    assert eng.state().verdict and "abandoned" in eng.state().verdict["verdict"]


def test_two_passes_cannot_run_at_once():
    eng, clock, _ = mock_engine()
    with exclusive(eng.state_dir):
        try:
            eng.run_pass()
            raise AssertionError("a second pass must be refused")
        except RunnerBusy:
            pass


def test_init_is_once_only():
    eng, clock, _ = mock_engine()
    try:
        eng.init()
        raise AssertionError("a second FNO_INIT must be refused")
    except FeedRefused:
        pass


def test_weeks_of_synthetic_running_give_a_verifying_journal_and_a_sequence():
    eng, clock, _ = mock_engine()
    d = date(2026, 11, 2)
    while d < date(2026, 12, 31):
        if CAL.is_trading_day(d):
            day(eng, clock, d)
        d += timedelta(days=1)
    out = evaluate(eng.journal.path, now=clock.now, cal=CAL)
    assert out["journal_verifies"] and len(out["items"]) >= 5
    assert all(x["kind"] == "trade" for x in out["items"])
    assert out["verdict"].startswith("NOT EVALUABLE")          # mock records are never evidence


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
