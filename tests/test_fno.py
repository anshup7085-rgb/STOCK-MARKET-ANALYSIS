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
from trading.fno.engine import FeedRefused, FnoEngine, read_bhavcopy, record_manual_settlement
from trading.fno.evaluate import anchors, decide, evaluate, verify_tolerant
from trading.fno.feeds import (FnoFeed, GrowwFnoFeed, KiteFnoFeed, MockFnoFeed, fsp_from_bhavcopy,
                               refuse_secrets_in_env)
from trading.fno.model import ContractMaster, IndexQuote, OptionContract, Quote, from_groww_frame, from_kite_rows
from trading.fno import strategy_v as sv
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


def book_quotes(prices: dict[str, tuple[float, float] | None], **kw):
    def fetch(contracts):
        return {c.symbol: q(c.symbol, *prices[c.symbol], **kw) for c in contracts if prices.get(c.symbol)}
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
    assert stale.code == "stale_snapshot" and "old" in stale.detail       # the ATM pair: a feed check
    assert isinstance(plan(ts=NOW - timedelta(seconds=120)), Plan)
    assert plan(qty=64).code == "touch_size"
    unknown = plan(qty=None)
    assert isinstance(unknown, Plan) and "touch size unknown" in unknown.flags


def test_snapshot_must_be_coherent_and_dated_today():
    assert plan(fetched=NOW + timedelta(seconds=11)).code == "slow_snapshot"
    s = plan(ts=None, index=IndexQuote(25025.0, None, NOW))
    assert s.code == "no_timestamp_today"
    assert isinstance(plan(ts=None), Plan)                # the index carries today's timestamp
    assert plan(index=IndexQuote(25025.0, NOW - timedelta(days=1), NOW)).code == "index_stale"
    assert plan(ts=NOW + timedelta(seconds=6)).code == "clock"         # a quote from the future: clock behind
    assert isinstance(plan(ts=NOW + timedelta(seconds=5)), Plan)
    assert plan(dict(BASE, **{"N24600PE": None})).code == "no_quote"


def test_a_stale_wing_is_the_market_but_a_stale_at_the_money_quote_is_the_feed():
    fresh = book_quotes(BASE)

    def wing_stale(cs):
        out = fresh(cs)
        if "N24600PE" in out:
            out["N24600PE"] = dataclasses.replace(out["N24600PE"], exchange_ts=NOW - timedelta(seconds=300))
        return out
    s = plan_entry(master(), EXP, INDEX, wing_stale, FNO_GROWW)
    assert s.code == "leg_quote" and "old" in s.detail


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
    # NSE's layout (19 Oct 2026): every expiring option row carries the underlying's FSP, equal to UndrlygPric
    v, info = fsp_from_bhavcopy(pd.DataFrame(_bhav_rows()), "NIFTY", EXP)
    assert v == 25012.35 and info["reading"] == "uniform" and info["n"] == 10
    rows = _bhav_rows()
    rows[3]["UndrlygPric"] = "25012.40"                     # the two columns disagree by five paise
    assert fsp_from_bhavcopy(pd.DataFrame(rows), "NIFTY", EXP)[0] is None
    rows = _bhav_rows()
    rows[0]["SttlmPric"] = "25012.36"                       # one row differs: not uniform, and no per-option reading
    assert fsp_from_bhavcopy(pd.DataFrame(rows), "NIFTY", EXP)[0] is None
    v, info = fsp_from_bhavcopy(pd.DataFrame(_bhav_rows()).drop(columns=["UndrlygPric"]), "NIFTY", EXP)
    assert v is None and "lacks ['UndrlygPric']" in info["error"]
    assert fsp_from_bhavcopy(pd.DataFrame(_bhav_rows(strikes=[25000])), "NIFTY", EXP)[0] is None     # two contracts
    assert fsp_from_bhavcopy(pd.DataFrame(_bhav_rows(fsp=0.0)), "NIFTY", EXP)[0] is None
    noise = (_bhav_rows() + [{"TckrSymb": "NIFTY", "FinInstrmTp": "IDF", "XpryDt": "2026-11-17", "StrkPric": "0",
                               "OptnTp": "XX", "SttlmPric": "25013.00", "UndrlygPric": "25012.35"}]
             + _bhav_rows(fsp=70000.0, strikes=[69900, 70100], sym="NIFTYNXT50") + _bhav_rows(fsp=25500.0, xp="2026-11-24"))
    assert fsp_from_bhavcopy(pd.DataFrame(noise), "NIFTY", EXP)[0] == 25012.35

    # each option's own value, the layout the first text assumed: still read if NSE ever uses it
    rows = [{"TckrSymb": "NIFTY", "FinInstrmTp": "IDO", "XpryDt": "2026-11-17", "StrkPric": k, "OptnTp": r,
             "SttlmPric": max(0.0, 25012.35 - k) if r == "CE" else max(0.0, k - 25012.35)}
            for k in (24900, 24950, 25000, 25050, 25100) for r in ("CE", "PE")]
    v, info = fsp_from_bhavcopy(pd.DataFrame(rows), "NIFTY", EXP)
    assert v == 25012.35 and info["n"] == 5 and info["reading"] == "per-option"
    rows[0]["SttlmPric"] = 113.0                         # one contract disagrees by 0.65
    assert fsp_from_bhavcopy(pd.DataFrame(rows), "NIFTY", EXP)[0] is None
    rows = _bhav_rows(per_option=True)
    rows[0]["SttlmPric"] = f"{float(rows[0]['SttlmPric']) + 0.01:.2f}"        # one paisa
    assert fsp_from_bhavcopy(pd.DataFrame(rows), "NIFTY", EXP)[0] is None
    rows = _bhav_rows(per_option=True)
    rows[7]["StrkPric"] = np.nan
    rows[2]["SttlmPric"] = f"{float(rows[2]['SttlmPric']) + 50:.2f}"
    assert fsp_from_bhavcopy(pd.DataFrame(rows), "NIFTY", EXP)[0] is None
    three = _bhav_rows(strikes=[24950, 25000, 25100], per_option=True)
    assert fsp_from_bhavcopy(pd.DataFrame(three), "NIFTY", EXP)[0] is None
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


def _st(recs):
    return State.from_records([dict(r, seq=i + 1, mode="fno-paper") for i, r in enumerate(recs)])


def _trade(e: str, r: float = 0.0, net: float = 0.0):
    return {"id": f"V-{e}", "expiry": e, "credit": 30.0, "qty": 65, "lots": 1, "entry_costs_total": 100.0,
            "max_loss": 4700.0, "flags": [], "legs": [{"price": 30.0, "mid": 30.25}]}


def _book(items, start=date(2026, 11, 2)):
    """A synthetic journal: items are ("trade", R, net), ("missed",) or ("none",), one per weekly expiry."""
    exps = [date(2026, 11, 3) + timedelta(days=7 * i) for i in range(len(items) + 2)]
    m = lambda d, run: {"type": "FNO_MASTER", "asof": d.isoformat(),
                        "payload": {"date": d.isoformat(), "weekly_run": [x.isoformat() for x in run]}}
    recs = [{"type": "FNO_INIT", "asof": start.isoformat(), "payload": {"feed": "kite"}}, m(start, exps[:3])]
    for i, it in enumerate(items):
        e = exps[i + 1]
        recs.append(m(e - timedelta(days=6), [e, exps[i + 2]]))
        if it[0] == "trade":
            recs.append({"type": "FNO_ENTRY", "asof": (e - timedelta(days=6)).isoformat(),
                         "payload": {"trade": _trade(e.isoformat())}})
            recs.append({"type": "FNO_SETTLED", "asof": e.isoformat(),
                         "payload": {"trade_id": f"V-{e.isoformat()}", "net": it[2], "R": it[1]}})
        else:
            recs.append({"type": "FNO_CYCLE", "asof": e.isoformat(),
                         "payload": {"expiry": e.isoformat(), "outcome": "missed" if it[0] == "missed" else "no trade"}})
    return _st(recs), exps[len(items)] + timedelta(days=1)


def _decide(items, p=REGISTERED):
    st, d = _book(items)
    return decide(st, CAL, datetime.combine(d, time(17, 0), IST), p)


def T(r, net=None):
    return ("trade", r, r * 5000 if net is None else net)


def test_outcomes_in_order():
    v, k = _decide([T(0.4)] * 5 + [T(-1.0)] * 1 + ([T(0.4)] * 5 + [T(-1.0)]) * 39)
    assert v.startswith("PASS") and k is not None, v
    v, k = _decide([T(0.3), T(0.3), T(-1.0)] * 20)
    assert v.startswith("FAIL — futility") and k == 60, v
    v, k = _decide([T(-1.0)] * 11)
    assert "harm" in v and k == 10, v
    v, k = _decide([T(x) for x in np.tile([0.36, -0.30, 0.36, -0.36, 0.0], 50)])
    assert v.startswith("FAIL — no evidence") and k == 250, v
    v, _ = _decide([("missed",)] * 11)
    assert "harm" not in v, v                             # MISSED cycles carry no rupees
    v, k = _decide([("none",)] * 3, dataclasses.replace(REGISTERED, max_cycles=3))
    assert v.startswith("FAIL — no evidence: 3 counted cycles"), v


def test_harm_is_decided_before_pass_and_pass_before_futility_on_one_item():
    v, k = _decide([T(1.0, -25_000.0)] * 2, dataclasses.replace(REGISTERED, threshold=1.4))
    assert v.startswith("FAIL — harm") and k == 2, v
    v, k = _decide([("missed",)], dataclasses.replace(REGISTERED, threshold=1.0, futility_n=1))
    assert v.startswith("PASS") and k == 1, v
    v, k = _decide([T(0.5), T(0.5)], dataclasses.replace(REGISTERED, threshold=1.25))
    assert v.startswith("PASS") and k == 2, v             # wealth exactly at the threshold passes


def test_the_cycle_cap_and_a_broken_bound_decide_in_order():
    p = dataclasses.replace(REGISTERED, max_cycles=2, threshold=1.2)
    v, _ = _decide([T(0.1), ("none",), T(0.5), T(0.5)], p)
    assert v.startswith("FAIL — no evidence: 2"), v       # the cap comes before a later PASS
    v, _ = _decide([T(0.5)] * 3 + [T(-2.5)], dataclasses.replace(REGISTERED, threshold=1.2))
    assert v.startswith("PASS"), v                        # a PASS before the broken bound stands
    v, _ = _decide([T(-2.5)])
    assert v.startswith("FAIL — bound broken"), v


def test_abandonment_decides_at_its_own_date_before_a_later_pass():
    recs = [{"type": "FNO_INIT", "asof": "2026-11-02", "payload": {"feed": "kite"}},
            {"type": "FNO_MASTER", "asof": "2026-11-02", "payload": {
                "date": "2026-11-02", "weekly_run": ["2026-11-03", "2026-11-10", "2026-11-17"]}},
            {"type": "FNO_ENTRY", "asof": "2026-11-04", "payload": {"trade": _trade("2026-11-10")}},
            {"type": "FNO_SETTLED", "asof": "2026-11-10", "payload": {"trade_id": "V-2026-11-10", "net": 2000.0, "R": 0.5}},
            {"type": "FNO_ENTRY", "asof": "2026-11-11", "payload": {"trade": _trade("2026-11-17")}},
            {"type": "FNO_MASTER", "asof": "2026-12-10", "payload": {"date": "2026-12-10", "weekly_run": []}},
            {"type": "FNO_SETTLED", "asof": "2026-12-10", "payload": {"trade_id": "V-2026-11-17", "net": 2000.0, "R": 0.5}}]
    v, _ = decide(_st(recs), CAL, datetime(2026, 12, 10, 17, 0, tzinfo=IST), dataclasses.replace(REGISTERED, threshold=1.2))
    assert "abandoned" in v, v
    st, d = _book([T(0.1)])
    v, _ = decide(st, CAL, datetime.combine(d + timedelta(days=13), time(17), IST))
    assert "abandoned" not in v                           # 21 days without a master is not yet abandoned


def test_a_later_cycle_waits_for_an_earlier_unsettled_trade():
    st = _st([{"type": "FNO_INIT", "asof": "2026-11-02", "payload": {"feed": "kite"}},
              {"type": "FNO_MASTER", "asof": "2026-11-02",
               "payload": {"date": "2026-11-02", "weekly_run": ["2026-11-03", "2026-11-10", "2026-11-17"]}},
              {"type": "FNO_ENTRY", "asof": "2026-11-04", "payload": {"trade": _trade("2026-11-10")}},
              {"type": "FNO_CYCLE", "asof": "2026-11-17", "payload": {"expiry": "2026-11-17", "outcome": "missed"}}])
    v, k = decide(st, CAL, datetime(2026, 11, 18, 17, 0, tzinfo=IST))
    assert v.startswith("UNDECIDED — 0 item"), v


def test_counted_cycles_need_e_prev_on_or_after_start_and_unlisted_expiries_drop():
    st = _st([{"type": "FNO_INIT", "asof": "2026-11-04", "payload": {"feed": "kite"}},
              {"type": "FNO_MASTER", "asof": "2026-11-04",
               "payload": {"date": "2026-11-04", "weekly_run": ["2026-11-03", "2026-11-10", "2026-11-17"]}}])
    assert st.counted_cycles() == [(date(2026, 11, 17), date(2026, 11, 10))]
    moved = _st([{"type": "FNO_INIT", "asof": "2026-11-02", "payload": {"feed": "kite"}},
                 {"type": "FNO_MASTER", "asof": "2026-11-02",
                  "payload": {"date": "2026-11-02", "weekly_run": ["2026-11-03", "2026-11-10", "2026-11-17"]}},
                 {"type": "FNO_MASTER", "asof": "2026-11-11",
                  "payload": {"date": "2026-11-11", "weekly_run": ["2026-11-16", "2026-11-24"]}}])
    assert date(2026, 11, 17) not in moved.known and date(2026, 11, 16) in moved.known


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
    feed._kite, feed._last_quote = FakeKite(), None
    a = feed.quotes([OptionContract("A", "NIFTY", EXP, 25000.0, "CE", 65, 0.05)])["A"]
    assert (a.bid, a.bid_qty, a.ask, a.ask_qty) == (30.0, 650, 30.5, 975)
    assert a.exchange_ts == datetime(2026, 11, 11, 10, 0, tzinfo=IST)


def test_secrets_in_the_environment_are_refused_and_harmless_names_are_not():
    for name in ("GROWW_TOTP_SECRET", "KITE_API_SECRET", "ZERODHA_API_SECRET", "API_SECRET", "KITE_PIN"):
        os.environ[name] = "x"
        try:
            refuse_secrets_in_env()
            raise AssertionError(f"{name} must be refused")
        except DataUnavailable as exc:
            assert name in str(exc)
        finally:
            del os.environ[name]
    os.environ["COREPACK_ENABLE_AUTO_PIN"] = "0"
    os.environ["TWILIO_API_SECRET"] = "x"
    try:
        refuse_secrets_in_env()
    finally:
        del os.environ["TWILIO_API_SECRET"]


def test_kite_quote_calls_are_spaced_a_second_apart():
    import time as _t

    class FakeKite:
        last = None

        def quote(self, keys):
            now = _t.monotonic()
            if FakeKite.last is not None and now - FakeKite.last < 1.0:
                raise RuntimeError("429 Too many requests")
            FakeKite.last = now
            return {k: {"last_price": 1.0, "timestamp": datetime(2026, 11, 11, 10, 0, 0)} for k in keys}
    feed = object.__new__(KiteFnoFeed)
    feed._kite, feed._last_quote = FakeKite(), None
    feed.index_quote("NIFTY")
    feed.quotes([OptionContract("A", "NIFTY", EXP, 25000.0, "CE", 65, 0.05)])


def test_groww_daily_close_parses_six_or_seven_columns_and_ist_dates():
    day_ = date(2026, 11, 17)
    midnight_ist = int(datetime(2026, 11, 17, 0, 0, tzinfo=IST).timestamp())
    assert GrowwFnoFeed.daily_close({"candles": [[midnight_ist, 1, 2, 0.5, 25012.35, None, None]]}, day_) == 25012.35
    assert GrowwFnoFeed.daily_close({"candles": [["2026-11-17T00:00:00+05:30", 1, 2, 0.5, 25012.35, None]]}, day_) == 25012.35
    assert GrowwFnoFeed.daily_close({"candles": [[midnight_ist - 86400, 1, 2, 0.5, 25000.0, 0]]}, day_) is None


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


def test_mock_feed_is_refused_outside_a_mock_directory_and_an_unbound_text_refuses_real_feeds():
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
    try:
        FnoEngine(RealLooking(), tmpdir() / "x_mock", calendar=CAL)
        raise AssertionError("a real feed must be refused in a mock directory")
    except FeedRefused:
        pass
    bound = sv.REGISTRATION_SHA256
    try:
        sv.REGISTRATION_SHA256 = "0" * 64                  # the text on disk no longer matches
        FnoEngine(RealLooking(), tmpdir() / "paper_fno", calendar=CAL)
        raise AssertionError("a text that differs from the registered hash must refuse real feeds")
    except FeedRefused as exc:
        assert "registered hash" in str(exc)
    finally:
        sv.REGISTRATION_SHA256 = bound


def test_the_registration_is_bound_to_the_text_on_disk():
    import hashlib
    from trading.fno.engine import DRAFT_MARK, PREREG
    assert sv.REGISTRATION_SHA256 == hashlib.sha256(PREREG.read_bytes()).hexdigest(), \
        "research/PREREGISTRATION_3.md changed after it bound (section 17: a change is a new registration)"
    assert DRAFT_MARK not in PREREG.read_text()


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
    assert out["cycles"][0]["kind"] == "missed" and out["cycles"][0]["R"] == -1.0


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
    assert all(c["kind"] == "no trade" for c in out["cycles"] if c["resolved"])
    assert out["descriptive"]["passes_without_trade_by_check"]["credit_below_floor"] >= 34


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


def _bhav_rows(fsp=25012.35, strikes=None, sym="NIFTY", tp="IDO", xp="2026-11-17", per_option=False):
    """NSE's layout by default (every expiring row carries the FSP); per_option: each option's own value."""
    strikes = strikes or [round(fsp / 50) * 50 + d for d in (-100, -50, 0, 50, 100)]
    out = []
    for k in strikes:
        for r in ("CE", "PE"):
            v = (max(0.0, fsp - k) if r == "CE" else max(0.0, k - fsp)) if per_option else fsp
            out.append({"TckrSymb": sym, "FinInstrmTp": tp, "XpryDt": xp, "StrkPric": f"{k:.2f}", "OptnTp": r,
                        "SttlmPric": f"{v:.2f}", "UndrlygPric": f"{fsp:.2f}"})
    return out


def _bhav_file(fsp: float, e: date) -> Path:
    path = tmpdir() / f"BhavCopy_NSE_FO_0_0_0_{e:%Y%m%d}_F_0000.csv"
    pd.DataFrame(_bhav_rows(fsp, xp=e.isoformat())).to_csv(path, index=False)
    return path


def test_a_hand_supplied_file_needs_a_failed_later_pass_and_still_passes_the_checks():
    eng, clock, feed = mock_engine(fsp=False, second=lambda u, d: None)
    t, e = _open_trade(eng, clock)
    close = round(feed.close_on(e), 2)
    at(clock, e, 16, 0)
    eng.run_pass()
    try:
        record_manual_settlement(eng.state_dir, t["expiry"], _bhav_file(close, e))
        raise AssertionError("too early: no failed pass on a later trading day yet")
    except ValueError:
        pass
    nxt = e + timedelta(days=1)
    while not CAL.is_trading_day(nxt):
        nxt += timedelta(days=1)
    at(clock, nxt, 10, 0)
    eng.run_pass()
    try:
        record_manual_settlement(eng.state_dir, t["expiry"], _bhav_file(close, e + timedelta(days=7)))
        raise AssertionError("a file for another expiry gives no price")
    except ValueError:
        pass
    assert record_manual_settlement(eng.state_dir, t["expiry"], _bhav_file(close, e)) == round(close, 2)
    at(clock, nxt, 10, 15)
    eng.run_pass()
    t2 = eng.state().trades[t["id"]]
    assert t2["status"] == "SETTLED" and "supplied by hand" in t2["settlement"]["source"]


def test_a_hand_supplied_file_off_the_close_waits():
    eng, clock, feed = mock_engine(fsp=False, second=lambda u, d: None)
    t, e = _open_trade(eng, clock)
    close = round(feed.close_on(e), 2)
    at(clock, e, 16, 0)
    eng.run_pass()
    nxt = e + timedelta(days=1)
    while not CAL.is_trading_day(nxt):
        nxt += timedelta(days=1)
    at(clock, nxt, 10, 0)
    eng.run_pass()
    record_manual_settlement(eng.state_dir, t["expiry"], _bhav_file(close + 1.0, e))
    at(clock, nxt, 10, 15)
    eng.run_pass()
    assert eng.state().trades[t["id"]]["status"] == "AWAITING_SETTLEMENT"


def test_no_price_by_the_tenth_trading_day_settles_at_minus_m():
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
    done = [c for c in out["cycles"] if c["resolved"]]
    assert out["journal_verifies"] and len(done) >= 5
    assert all(c["kind"] == "trade" for c in done)
    assert out["verdict"].startswith("NOT EVALUABLE")          # mock records are never evidence


# -- the review's findings: what may never decide a week ------------------------------

def _wed(eng, clock):
    for d in (date(2026, 11, 2), date(2026, 11, 3)):
        day(eng, clock, d)
    return date(2026, 11, 4)


def test_a_quote_the_feed_drops_makes_the_week_missed_not_excluded():
    eng, clock, feed = mock_engine()
    _wed(eng, clock)
    real = feed.quotes
    feed.quotes = lambda cs: {k: v for k, v in real(cs).items() if not k.endswith("PE")}
    for d in (date(2026, 11, 4), date(2026, 11, 5)):
        day(eng, clock, d)
    feed.quotes = real
    for d in (date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 10)):
        day(eng, clock, d)
    assert eng.state().cycles[date(2026, 11, 10)]["outcome"] == "missed"


def test_a_clock_behind_the_exchange_is_a_data_failure():
    eng, clock, feed = mock_engine()
    wed = _wed(eng, clock)
    real = feed.quotes
    feed.quotes = lambda cs: {k: dataclasses.replace(v, exchange_ts=v.exchange_ts + timedelta(hours=3))
                              for k, v in real(cs).items()}
    at(clock, wed, 10, 0)
    eng.run_pass(slot=time(10, 0))
    rec = [r for r in eng.journal.records() if r["type"] == "FNO_PASS"][-1]
    assert rec["payload"]["outcome"] == "data_unavailable" and "clock" in rec["payload"]["detail"]


def test_a_week_the_runner_never_ran_is_missed():
    eng, clock, _ = mock_engine()
    _wed(eng, clock)
    for d in (date(2026, 11, 6), date(2026, 11, 9), date(2026, 11, 10)):
        day(eng, clock, d)
    assert eng.state().cycles[date(2026, 11, 10)]["outcome"] == "missed"
    assert evaluate(eng.journal.path, now=clock.now, cal=CAL)["cycles"][0]["R"] == -1.0


def test_a_slot_that_ends_in_a_data_failure_makes_the_cycle_missed():
    eng, clock, feed = mock_engine()
    wed = _wed(eng, clock)
    real = feed.index_quote
    feed.index_quote = lambda u: (_ for _ in ()).throw(DataUnavailable("feed down"))
    at(clock, wed, 10, 0)
    eng.run_pass(slot=time(10, 0), final=True)
    feed.index_quote = real
    day(eng, clock, wed, slots=slot_times()[1:])
    st = eng.state()
    assert not st.trades and st.cycles[date(2026, 11, 10)]["outcome"] == "missed"


def test_a_slot_is_decided_once():
    eng, clock, _ = mock_engine(p=dataclasses.replace(REGISTERED, min_credit=60.0, max_credit=80.0))
    wed = _wed(eng, clock)
    at(clock, wed, 10, 0)
    eng.run_pass(slot=time(10, 0))
    n = kinds(eng).count("FNO_PASS")
    at(clock, wed, 10, 1)
    eng.run_pass(slot=time(10, 0))
    assert kinds(eng).count("FNO_PASS") == n


def test_the_entry_record_comes_first_and_marks_its_slot():
    eng, clock, _ = mock_engine()
    wed = _wed(eng, clock)
    at(clock, wed, 10, 0)
    eng.run_pass(slot=time(10, 0))
    ks = kinds(eng)
    assert ks.index("FNO_ENTRY") + 1 == ks.index("FNO_PASS", ks.index("FNO_ENTRY"))
    recs = eng.journal.records()
    cut = [r for r in recs if r["seq"] <= next(r["seq"] for r in recs if r["type"] == "FNO_ENTRY")]
    st = State.from_records(cut)                          # a crash right after FNO_ENTRY
    t = next(iter(st.trades.values()))
    assert t["slot"] in st.observed


def test_late_or_stale_entry_records_are_void():
    eng, clock, _ = mock_engine(p=dataclasses.replace(REGISTERED, min_credit=60.0, max_credit=80.0))
    for d in (date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 10)):
        day(eng, clock, d)
    assert eng.state().cycles[date(2026, 11, 10)]["outcome"] == "no trade"
    fake = _trade("2026-11-10")
    fake["slot"] = "2026-11-04T10:00:00+05:30"
    eng.journal.append("FNO_ENTRY", asof="2026-11-11", feed="mock", trade=fake)
    st = eng.state()
    assert not st.trades and any("already closed" in v for v in st.void)
    late = [{"type": "FNO_INIT", "asof": "2026-11-02", "mode": "fno-paper", "payload": {"feed": "kite"}},
            {"type": "FNO_ENTRY", "asof": "2026-11-04", "mode": "fno-paper", "ts": "2026-11-04T10:06:00+05:30",
             "payload": {"trade": dict(_trade("2026-11-10"), slot="2026-11-04T10:00:00+05:30")}}]
    st = State.from_records([dict(r, seq=i + 1) for i, r in enumerate(late)])
    assert not st.trades and "written 360s after" in st.void[0]


def test_a_second_close_and_a_second_cycle_record_are_void():
    eng, clock, feed = mock_engine()
    t, e = _open_trade(eng, clock)
    at(clock, e, 16, 0)
    eng.run_pass()
    first = eng.state().trades[t["id"]]["R"]
    eng.journal.append("FNO_SETTLED", asof=e.isoformat(), feed="mock", trade_id=t["id"], expiry=t["expiry"],
                       net=9999.0, R=2.0, max_loss=t["max_loss"], detail={})
    eng.journal.append("FNO_CYCLE", asof=e.isoformat(), feed="mock", expiry=t["expiry"], outcome="no trade")
    st = eng.state()
    assert len(st.void) == 1 and st.trades[t["id"]]["R"] == first
    eng2, clock2, _ = mock_engine()
    _wed(eng2, clock2)
    day(eng2, clock2, date(2026, 11, 4), slots=slot_times()[1:])
    eng2.journal.append("FNO_CYCLE", asof="2026-11-04", feed="mock", expiry="2026-11-10", outcome="no trade")
    st2 = eng2.state()
    assert st2.void and st2.cycles[date(2026, 11, 10)]["outcome"] == "missed"


def test_a_tampered_master_file_stops_the_engine():
    eng, clock, _ = mock_engine()
    at(clock, date(2026, 11, 2), 9, 30)
    eng.run_pass()
    path = eng.state_dir / "master" / "2026-11-02.json"
    path.write_text(path.read_text().replace('"lot_size": 65', '"lot_size": 75', 1))
    at(clock, date(2026, 11, 2), 10, 0)
    try:
        eng.run_pass()
        raise AssertionError("a master file that differs from its journaled hash must stop the engine")
    except FeedRefused:
        pass


def test_the_holiday_list_may_change_only_for_future_dates():
    eng, clock, _ = mock_engine()
    day(eng, clock, date(2026, 11, 2))
    eng.cal = TradingCalendar(holidays={**CAL.holidays, "2026-12-25": "x"})
    at(clock, date(2026, 11, 3), 9, 30)
    eng.run_pass()
    assert "FNO_CALENDAR" in kinds(eng)
    eng.cal = TradingCalendar(holidays={**eng.cal.holidays, "2026-11-02": "x"})
    try:
        eng.run_pass()
        raise AssertionError("re-labelling a past session must be refused")
    except FeedRefused:
        pass


def test_the_parameters_cannot_change_under_a_book():
    eng, clock, _ = mock_engine()
    eng.p = dataclasses.replace(REGISTERED, risk_budget=6000.0)
    try:
        eng.run_pass()
        raise AssertionError("a different budget must be refused")
    except FeedRefused:
        pass


def test_a_feed_switch_needs_two_adjacent_missed_cycles_and_happens_once():
    eng, clock, feed = mock_engine()
    eng.feed.key = "kite"                                  # pretend the book reads kite
    eng2 = FnoEngine(feed, eng.state_dir, calendar=CAL, now_fn=clock, fsp_source=Fsp(feed))
    feed.key = "groww"
    try:
        eng2.switch_feed("test", ["2026-11-10", "2026-11-17"])
        raise AssertionError("a switch without missed cycles must be refused")
    except FeedRefused:
        pass
    feed.key = "mock"


def test_the_probe_needs_a_quote_timestamp():
    class NoTs(MockFnoFeed):
        def quotes(self, cs):
            return {k: dataclasses.replace(v, exchange_ts=None) for k, v in super().quotes(cs).items()}

        def index_quote(self, u):
            return dataclasses.replace(super().index_quote(u), exchange_ts=None)
    clock = Clock(datetime(2026, 11, 2, 9, 0, tzinfo=IST))
    f = NoTs(clock, calendar=CAL)
    eng = FnoEngine(f, tmpdir() / "x_mock", calendar=CAL, now_fn=clock, fsp_source=Fsp(f))
    try:
        eng.init()
        raise AssertionError("a feed whose quotes carry no timestamp can never trade")
    except FeedRefused as exc:
        assert "timestamp" in str(exc)


def _post_change_trade(fsp, closes=True):
    eng, clock, feed = mock_engine(start=datetime(2026, 12, 7, 9, 0, tzinfo=IST))
    d = date(2026, 12, 7)
    while not eng.state().trades:
        if CAL.is_trading_day(d):
            day(eng, clock, d, settle_at=None)
        d += timedelta(days=1)
    t = next(iter(eng.state().trades.values()))
    e = date.fromisoformat(t["expiry"])
    real = feed.index_close
    eng.fsp_source = lambda u, x: (fsp(real(u, x)), {"url": "https://nsearchives.nseindia.com/x"})
    if not closes:                                       # nothing to cross-check NSE's value against
        feed.index_close = lambda u, x: None
        eng.second_close = lambda u, x: None
    at(clock, e, 16, 0)
    eng.run_pass()
    return eng.state().trades[t["id"]], real("NIFTY", e)


def test_nse_value_governs_and_its_checks_hold():
    t, close = _post_change_trade(lambda c: round(c + 7.5, 2))
    assert t["status"] == "SETTLED" and abs(t["settlement"]["value"] - (close + 7.5)) < 1e-9
    t, _ = _post_change_trade(lambda c: round(c * 1.04, 2))
    assert t["status"] == "AWAITING_SETTLEMENT"
    t, _ = _post_change_trade(lambda c: float("nan"))
    assert t["status"] == "AWAITING_SETTLEMENT"
    eng, clock, feed = mock_engine()
    t, e = _open_trade(eng, clock)
    eng.fsp_source = lambda u, x: (round(feed.index_close(u, x) + 0.5, 2), {"url": "x"})
    at(clock, e, 16, 0)
    eng.run_pass()
    assert eng.state().trades[t["id"]]["status"] == "AWAITING_SETTLEMENT"     # before 7 Dec: within 0.10


def test_the_close_rule_needs_yahoo_within_ten_paise():
    holder = {}
    eng, clock, feed = mock_engine(fsp=False, second=lambda u, d: holder["f"].index_close(u, d) + 0.5)
    holder["f"] = eng.feed
    t, e = _open_trade(eng, clock)
    at(clock, e, 16, 0)
    eng.run_pass()
    assert eng.state().trades[t["id"]]["status"] == "AWAITING_SETTLEMENT"


def _journal(tmp: Path, init_extra=None, mode="fno-paper") -> Path:
    path = tmp / "journal.jsonl"
    j = Journal(path, mode)
    j.append("FNO_INIT", asof="2026-11-02", **{"feed": "kite", "prereg_sha256": "h", "registered_sha256": "h",
                                               **(init_extra or {})})
    j.append("FNO_MASTER", asof="2026-11-02", feed="kite", date="2026-11-02", sha256="m",
             weekly_run=["2026-11-03", "2026-11-10"], summary={})
    return path


def _ev(path):
    return evaluate(path, now=datetime(2026, 11, 3, 17, 0, tzinfo=IST), cal=CAL, anchor_log=tmpdir() / "none.md")["verdict"]


def test_evaluable_baseline_and_each_condition_alone():
    assert _ev(_journal(tmpdir())).startswith("UNDECIDED")
    two = _journal(tmpdir())
    Journal(two, "fno-paper").append("FNO_INIT", asof="2026-11-03", feed="kite", prereg_sha256="h", registered_sha256="h")
    assert "FNO_INIT" in _ev(two) and _ev(two).startswith("NOT EVALUABLE")
    mixed = _journal(tmpdir())
    Journal(mixed, "fno-mock").append("FNO_RUNNER_START", asof="2026-11-03", feed="kite")
    assert "modes" in _ev(mixed)
    assert "registered hash" in _ev(_journal(tmpdir(), {"registered_sha256": None}))
    tampered = _journal(tmpdir())
    tampered.write_text(tampered.read_text().replace('"weekly_run": ["2026-11-03"', '"weekly_run": ["2026-11-04"'))
    assert "verify" in _ev(tampered)
    stray = _journal(tmpdir())
    Journal(stray, "fno-paper").append("FNO_PASS", asof="2026-11-02", feed="kite", expiry="2026-11-03",
                                       slot="2026-11-02T10:00:00+05:30", outcome="no_trade", master_sha256="other")
    assert "contract master not journaled" in _ev(stray)


def test_a_torn_line_is_tolerated_when_the_chain_continues():
    path = _journal(tmpdir())
    with path.open("a") as fh:
        fh.write('{"seq": 3, "torn')
    Journal(path, "fno-paper").append("FNO_RUNNER_START", asof="2026-11-03", feed="kite")
    vr = verify_tolerant(path)
    assert vr["ok"] and vr["torn"] and _ev(path).startswith("UNDECIDED")


def test_anchors_must_match_the_journal():
    path = _journal(tmpdir())
    recs = Journal(path, "fno-paper").records()
    log = tmpdir() / "log.md"
    log.write_text(f"anchor: seq 2 sha256 {recs[1]['hash']}\nanchor: seq 1 sha256 {'0' * 64}\n")
    a = anchors(log, recs, repo=log.parent)
    assert a["mismatched"] == ["anchor seq 1 does not match the journal"] and len(a["anchors"]) == 1


def test_the_recorded_verdict_stands_over_a_later_recomputation():
    p = dataclasses.replace(REGISTERED, max_cycles=3, threshold=1.2)
    path = tmpdir() / "journal.jsonl"
    j = Journal(path, "fno-paper")
    a = lambda k, d, **kw: j.append(k, asof=d, feed="kite", **kw)
    a("FNO_INIT", "2026-11-02", prereg_sha256="h", registered_sha256="h")
    a("FNO_MASTER", "2026-11-02", date="2026-11-02", sha256="m", summary={},
      weekly_run=["2026-11-03", "2026-11-10", "2026-11-17", "2026-11-24", "2026-12-01"])
    a("FNO_MASTER", "2026-11-20", date="2026-11-20", sha256="m2", summary={},
      weekly_run=["2026-11-24", "2026-12-01"])
    a("FNO_ENTRY", "2026-11-04", trade=_trade("2026-11-10"))
    a("FNO_SETTLED", "2026-11-10", trade_id="V-2026-11-10", net=2000.0, R=0.5, detail={})
    a("FNO_ENTRY", "2026-11-11", trade=_trade("2026-11-17"))
    a("FNO_CYCLE", "2026-11-24", expiry="2026-11-24", outcome="no trade")
    a("FNO_ENTRY", "2026-11-25", trade=_trade("2026-12-01"))
    a("FNO_SETTLED", "2026-11-27", trade_id="V-2026-11-17", net=0.0, R=0.0, detail={})
    v, k = decide(State.from_records(j.records()), CAL, datetime(2026, 11, 27, 17, 0, tzinfo=IST), p)
    assert v.startswith("FAIL — no evidence"), v
    a("FNO_VERDICT", "2026-11-27", verdict=v, decided_at=k)
    a("FNO_SETTLED", "2026-12-01", trade_id="V-2026-12-01", net=2000.0, R=0.5, detail={})
    out = evaluate(path, now=datetime(2026, 12, 1, 17, 0, tzinfo=IST), p=p, cal=CAL, anchor_log=tmpdir() / "n.md")
    assert out["verdict"].startswith("FAIL")


def test_bet_sizes_match_the_registered_formula():
    assert np.allclose(bet_sizes([0.1, -0.05, 0.3]), [0.0, 0.1 / 0.26, 0.05 / 0.2625])


def test_boundaries_from_the_review():
    at_cap = dict(BASE, **{"N25350CE": (50.0, 50.5), "N24700PE": (50.0, 50.5),
                           "N25450CE": (9.5, 10.0), "N24600PE": (9.5, 10.0)})
    assert plan(at_cap, p=dataclasses.replace(REGISTERED, risk_budget=10_000)).credit == 80.0
    exact = dict(BASE, **{"N25000CE": (162.0, 163.0), "N25000PE": (162.0, 163.0)})   # S +- EM on a strike
    assert {g["role"]: g["strike"] for g in plan(exact).legs}["short_call"] == 25350
    assert isinstance(plan(qty=65), Plan)
    m1 = plan().max_loss
    assert plan(p=dataclasses.replace(REGISTERED, risk_budget=m1)).lots == 1
    assert isinstance(plan(fetched=NOW + timedelta(seconds=10)), Plan)
    assert isinstance(plan(dict(BASE, **{"N24600PE": (8.0, 9.0)})), Plan)              # spread exactly 1.00
    cs = [c for c in master().contracts if c.symbol != "N25000PE"]
    prices = dict(BASE, **{"N25050CE": (125.0, 126.0), "N25050PE": (165.0, 166.0)})
    assert plan(prices, m=ContractMaster(cs, "t", NOW)).atm_strike == 25050
    r = settle(plan().as_dict(), 25600.0, FNO_GROWW)
    assert r["settlement_costs"]["long_call"]["sebi"] == 1.66




# -- from the planted-bug run: each test below fails if its one check is removed --------------

def test_an_index_read_outside_its_slot_is_a_data_failure():
    eng, clock, feed = mock_engine()
    for d in (date(2026, 11, 2), date(2026, 11, 3)):
        day(eng, clock, d)
    real = feed.index_quote
    feed.index_quote = lambda u: dataclasses.replace(real(u), fetched_at=clock.now + timedelta(seconds=150))
    at(clock, date(2026, 11, 4), 10, 0)
    eng.run_pass(slot=time(10, 0))
    p = [r["payload"] for r in eng.journal.records() if r["type"] == "FNO_PASS" and r["payload"].get("slot")][-1]
    assert p["outcome"] == "data_unavailable" and "outside the 10:00 slot" in p["detail"]
    assert not eng.state().trades


def test_an_unusable_nse_value_waits_even_with_no_close_to_check_it_against():
    for bad in (float("nan"), 0.0, -5.0, float("inf")):
        t, _ = _post_change_trade(lambda c, b=bad: b, closes=False)
        assert t["status"] == "AWAITING_SETTLEMENT", bad
    t, close = _post_change_trade(lambda c: round(c, 2), closes=False)
    assert t["status"] == "SETTLED" and t["settlement"]["checks"].get("flag") == "no feed close to cross-check"


def _manual_ready():
    """A trade whose NSE value is missing, with a failed pass on a later trading day and a file on record."""
    eng, clock, feed = mock_engine(fsp=False, second=lambda u, d: None)
    t, e = _open_trade(eng, clock)
    close = round(feed.close_on(e), 2)
    at(clock, e, 16, 0)
    eng.run_pass()
    nxt = e + timedelta(days=1)
    while not CAL.is_trading_day(nxt):
        nxt += timedelta(days=1)
    at(clock, nxt, 10, 0)
    eng.run_pass()
    record_manual_settlement(eng.state_dir, t["expiry"], _bhav_file(close, e))
    at(clock, nxt, 10, 15)
    return eng, clock, feed, t, close


def test_nse_governs_a_hand_supplied_file_once_it_appears():
    eng, clock, feed, t, close = _manual_ready()
    eng.fsp_source = lambda u, x: (round(close + 0.05, 2), {"url": "https://nsearchives.nseindia.com/x"})
    eng.run_pass()
    s = eng.state().trades[t["id"]]["settlement"]
    assert s["value"] == round(close + 0.05, 2) and s["source"].startswith("NSE bhavcopy (")


def test_a_hand_supplied_file_with_no_close_at_all_waits():
    eng, clock, feed, t, close = _manual_ready()
    feed.index_close = lambda u, d: None
    eng.run_pass()
    assert eng.state().trades[t["id"]]["status"] == "AWAITING_SETTLEMENT"


def test_the_kill_switch_marks_the_cycle_missed_at_once():
    eng, clock, _ = mock_engine()
    for d in (date(2026, 11, 2), date(2026, 11, 3)):
        day(eng, clock, d)
    (eng.state_dir / "KILL").write_text("test")
    at(clock, date(2026, 11, 4), 10, 0)
    eng.run_pass(slot=time(10, 0))
    cyc = list(eng.state().cycles.values())
    assert len(cyc) == 1 and cyc[0]["outcome"] == "missed" and cyc[0]["first_unobserved"].endswith("10:00:00+05:30")
    (eng.state_dir / "KILL").unlink()
    at(clock, date(2026, 11, 4), 10, 15)
    eng.run_pass(slot=time(10, 15))
    assert not eng.state().trades


def test_there_is_no_second_feed_switch():
    eng, clock, feed = mock_engine()
    eng.journal.append("FNO_FEED_SWITCH", asof="2026-11-02", feed="mock", old="kite", new="mock",
                       reason="test", cycles=["2026-11-10", "2026-11-17"])
    feed.key = "groww"
    try:
        eng.switch_feed("again", ["2026-11-24", "2026-12-01"])
        raise AssertionError("a second switch must be refused")
    except FeedRefused as exc:
        assert "already switched" in str(exc)
    finally:
        feed.key = "mock"


def test_a_recorded_verdict_is_the_verdict_and_a_different_recomputation_is_shown_beside_it():
    path = tmpdir() / "journal.jsonl"
    j = Journal(path, "fno-paper")
    a = lambda k, d, **kw: j.append(k, asof=d, feed="kite", **kw)
    a("FNO_INIT", "2026-11-02", prereg_sha256="h", registered_sha256="h")
    a("FNO_MASTER", "2026-11-02", date="2026-11-02", sha256="m", summary={}, weekly_run=["2026-11-03", "2026-11-10"])
    a("FNO_VERDICT", "2026-11-04", verdict="FAIL — futility (recorded)", decided_at=0)
    out = evaluate(path, now=datetime(2026, 11, 5, 17, 0, tzinfo=IST), cal=CAL, anchor_log=tmpdir() / "n.md")
    assert out["verdict"] == "FAIL — futility (recorded)"
    assert out["recomputed_verdict"] != out["verdict"] and "discrepancy" in out


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
