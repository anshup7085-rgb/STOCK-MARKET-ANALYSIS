"""
Offline tests for the multi-leg structures (trading/fno/structures.py) and the
intraday explorer (trading/fno/explore.py). No market data: hand-written quotes
or the synthetic MockIntradayFeed.

    PYTHONPATH=. python tests/test_fno_explore.py
"""

from __future__ import annotations

import math
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from market.calendar import TradingCalendar
from market.charges import FNO_ZERODHA, futures_order_costs, option_order_costs
from market.config import IST
from trading.fno.explore import (STOP_SLIP, Explorer, Refused, Trigger, build, fire, load_config, quote_problem,
                                 resolve, scoreboard, size, spec_defined)
from trading.fno.feeds import FnoFeed, MockIntradayFeed
from trading.fno.model import (ContractMaster, IndexQuote, OptionContract, Quote, futures_from_groww_frame,
                               futures_from_kite_rows)
from trading.fno.structures import (Leg, breakevens, close_prices, entry_costs, mark, net_premium,
                                    parse_legs, payoff, pick_expiry, pick_strike, slopes, structural_risk)
from trading.journal import Journal, verify_file

CAL = TradingCalendar(holidays={"2026-01-26": "x"})
WK, WK2, MON = date(2026, 11, 10), date(2026, 11, 17), date(2026, 11, 24)     # Tuesdays; the last is the monthly
DAY = date(2026, 11, 4)                                                         # a Wednesday
NOW = datetime.combine(DAY, time(10, 0), IST)
LOT = 65


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="fno_explore_test_"))


def master(lot: int = LOT, fut_lot: int | None = None) -> ContractMaster:
    cs = [OptionContract(f"N{e:%m%d}{k}{r}", "NIFTY", e, float(k), r, lot, 0.05, freeze_qty=1800)
          for e in (WK, WK2, MON) for k in range(24000, 26001, 50) for r in ("CE", "PE")]
    cs.append(OptionContract("NFUT", "NIFTY", MON, 0.0, "FUT", fut_lot or lot, 0.10, freeze_qty=1800))
    return ContractMaster(cs, "test", NOW)


def leg(kind, side, strike, price, lots=1, expiry=WK, lot=LOT, sym=None) -> Leg:
    return Leg(kind, side, sym or f"{kind}{strike}", "NFO", expiry.isoformat(), float(strike), lots, lot, price, price)


def q(sym, bid, ask, qty=10_000, ts=NOW) -> Quote:
    return Quote(sym, bid, ask, qty, qty, None, ts, ts)


# -- notation ---------------------------------------------------------------------------

def test_leg_notation_reads_kind_side_ratio_rule_and_expiry():
    a, b, c = parse_legs("FUT+ CE-2@ATM+2 PE+@EM-1.0/next_weekly")
    assert (a.kind, a.side, a.expiry) == ("FUT", "BUY", "monthly")
    assert (b.kind, b.side, b.ratio, b.strike, b.expiry) == ("CE", "SELL", 2, "ATM+2", "weekly")
    assert (c.strike, c.expiry) == ("EM-1.0", "next_weekly")
    assert b.label == "-2CE@ATM+2" and a.label == "+FUT"
    for bad in ("FUT+@ATM", "CE*@ATM", "XX+", "CE-@OTM", "FUT-/weekly", "CE-@PREM:"):
        try:
            parse_legs(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


# -- risk shape --------------------------------------------------------------------------

def test_defined_and_undefined_structures():
    cases = {
        "FUT+ CE-@ATM+2": False,          # covered call: the future's downside is open
        "CE+@ATM FUT-": True,             # synthetic put: the call caps the upside loss
        "FUT+ PE+@ATM": True,             # protective put
        "FUT- PE-@ATM-2": False,          # covered put
        "CE-@ATM PE-@ATM": False,         # short straddle
        "CE-@ATM PE-@ATM CE+@ATM+4 PE+@ATM-4": True,
        "CE+@ATM CE-2@ATM+4": False,      # ratio spread: short one extra call
        "CE+@ATM CE-@ATM+2": True,
        "FUT+": False, "FUT-": False,
        "CE+@ATM CE-@ATM/next_weekly": False,     # a calendar's value is not its payoff
    }
    for text, want in cases.items():
        assert spec_defined(parse_legs(text)) is want, text


def test_vertical_and_condor_worst_losses_are_exact():
    spread = [leg("CE", "BUY", 25000, 120.0), leg("CE", "SELL", 25100, 70.0)]
    loss, defined = structural_risk(spread, 25000.0)
    assert defined and math.isclose(loss, 50 * LOT)
    assert breakevens(spread, 25000.0) == [25050.0]
    condor = [leg("CE", "SELL", 25200, 40.0), leg("CE", "BUY", 25300, 15.0),
              leg("PE", "SELL", 24800, 45.0), leg("PE", "BUY", 24700, 18.0)]
    loss, defined = structural_risk(condor, 25000.0)
    assert defined and math.isclose(loss, (100 - 52) * LOT)
    assert math.isclose(net_premium(condor), 52 * LOT)


def test_a_future_moves_with_the_index_from_its_own_fill_so_its_basis_is_not_a_loss():
    fut = [leg("FUT", "BUY", 0, 25120.0, expiry=MON)]          # 120 points of basis over the index
    assert payoff(fut, 25000.0, 25000.0) == 0.0
    assert payoff(fut, 24900.0, 25000.0) == -100 * LOT
    synth = [leg("CE", "BUY", 25000, 150.0), leg("FUT", "SELL", 0, 25120.0, expiry=MON)]
    loss, defined = structural_risk(synth, 25000.0)
    assert defined and math.isclose(loss, 150 * LOT)            # the call's premium, not the basis
    cal = [leg("CE", "SELL", 25000, 100.0), leg("CE", "BUY", 25000, 160.0, expiry=WK2)]
    assert structural_risk(cal, 25000.0) == (math.inf, False)   # a calendar: no expiry payoff bounds it
    cc = [leg("FUT", "BUY", 0, 25120.0, expiry=MON), leg("CE", "SELL", 25100, 60.0)]
    up, down = slopes(cc)
    assert up == 0 and down == -LOT and structural_risk(cc, 25000.0)[1] is False


# -- marks -------------------------------------------------------------------------------

def test_closing_at_the_touch_and_a_long_option_without_a_bid():
    legs = [leg("CE", "BUY", 25000, 120.0, sym="L"), leg("CE", "SELL", 25100, 70.0, sym="S")]
    got = close_prices(legs, {"L": q("L", 110.0, 111.0), "S": q("S", 64.0, 65.0)})
    assert got == {"L": 110.0, "S": 65.0}
    assert close_prices(legs, {"L": q("L", None, 0.05), "S": q("S", 1.0, 1.1)}) == {"L": 0.0, "S": 1.1}
    assert close_prices(legs, {"L": q("L", 110.0, 111.0), "S": q("S", 64.0, None)}) is None
    cin = entry_costs(legs, FNO_ZERODHA)
    m = mark(legs, {"L": 0.0, "S": 1.1}, FNO_ZERODHA, cin)
    want_exit = option_order_costs(1.1, LOT, "BUY", FNO_ZERODHA).total       # no order for the worthless long
    assert math.isclose(m["exit_costs"], round(want_exit, 2))
    assert math.isclose(m["net"], round((0 - 120) * LOT + (70 - 1.1) * LOT - cin - want_exit, 2))
    f = [leg("FUT", "SELL", 0, 25120.0, expiry=MON, sym="F")]
    m = mark(f, {"F": 25100.0}, FNO_ZERODHA, entry_costs(f, FNO_ZERODHA))
    assert math.isclose(m["exit_costs"], round(futures_order_costs(25100.0, LOT, "BUY", FNO_ZERODHA).total, 2))


# -- selection -----------------------------------------------------------------------------

def test_expiries_and_strikes_by_rule():
    m = master()
    s = parse_legs("CE-@ATM+2 PE+@ATM/next_weekly CE+@ATM/monthly FUT+")
    assert [pick_expiry(m, x, DAY) for x in s] == [WK, WK2, MON, MON]
    assert pick_strike(m, s[0], WK, 25010.0, None) == 25100.0
    assert pick_strike(m, s[0], WK, 25030.0, None) == 25150.0          # ATM 25050 for an index at 25030
    em_c, em_p = parse_legs("CE-@EM+0.5 PE-@EM-0.5")
    assert pick_strike(m, em_c, WK, 25000.0, 230.0) == 25150.0         # lowest call at or above 25115
    assert pick_strike(m, em_p, WK, 25000.0, 230.0) == 24850.0         # highest put at or below 24885
    prem = parse_legs("CE-@PREM:20")[0]
    quotes = {f"N{WK:%m%d}{k}CE": q(f"N{WK:%m%d}{k}CE", p - 0.5, p + 0.5)
              for k, p in ((25200, 30.0), (25250, 21.0), (25300, 19.0), (25350, 12.0))}
    assert pick_strike(m, prem, WK, 25000.0, None, quotes) == 25300.0


def test_rules_that_read_prices_name_their_quotes_first():
    m = master()
    specs = parse_legs("CE-@EM+0.5 PE-@EM-0.5 CE+@EM+1.0 PE+@EM-1.0")
    pairs, needs, why = resolve(m, specs, 25000.0, DAY, {}, 15)
    assert pairs is None and {c.symbol for c in needs} == {f"N{WK:%m%d}25000CE", f"N{WK:%m%d}25000PE"}
    quotes = {c.symbol: q(c.symbol, 114.5, 115.5) for c in needs}
    pairs, _, why = resolve(m, specs, 25000.0, DAY, quotes, 15)
    assert [c.strike for _, c in pairs] == [25150.0, 24850.0, 25250.0, 24750.0], why


# -- configuration ---------------------------------------------------------------------------

def _cfg(strategies, **kw):
    return load_config({"capital": 500000, "strategies": strategies, **kw})


def test_an_undefined_structure_needs_a_stop_that_is_not_in_r():
    for stop in (None, {"R": 1.0}):
        try:
            _cfg([{"name": "x", "legs": "CE-@ATM PE-@ATM", "window": ["09:20", "09:30"],
                   "exit": {"stop": stop} if stop else {}}])
            raise AssertionError(stop)
        except ValueError as exc:
            assert "undefined risk" in str(exc)
    _cfg([{"name": "x", "legs": "CE-@ATM PE-@ATM", "window": ["09:20", "09:30"], "exit": {"stop": {"credit_mult": 0.3}}}])
    _cfg([{"name": "x", "legs": "CE+@ATM CE-@ATM+2", "window": ["09:20", "09:30"], "exit": {}}])   # defined: none needed


def test_configuration_refusals():
    bad = [
        [{"template": "nope"}],
        [{"template": "short_straddle"}, {"template": "short_straddle"}],
        [{"template": "future_breakout", "window": ["09:20", "13:00"]}],          # before the opening range ends
        [{"template": "debit_spread", "exit": {"target": {"credit_mult": 1}}}],  # a stop's unit as a target
        [{"template": "debit_spread", "exit": {"stop": {"R": -1}}}],
        [{"template": "debit_spread", "exit": {"trail": {"after_R": 0.2, "lock_R": 0.5}}}],
        [{"template": "iron_fly", "trigger": {"type": "range"}}],
        [{"template": "debit_spread", "legs": {"up": "CE+@ATM"}}],
        [{"template": "short_straddle", "enabled": False}],
    ]
    for strategies in bad:
        try:
            _cfg(strategies)
            raise AssertionError(strategies)
        except ValueError:
            pass
    repo_cfg = load_config(Path(__file__).resolve().parents[1] / "config" / "fno_strategies.example.json")
    assert len(repo_cfg.strategies) >= 5 and repo_cfg.sha == load_config(repo_cfg.raw).sha


# -- triggers --------------------------------------------------------------------------------------

def _ticks(pairs):
    return [{"t": datetime.combine(DAY, time.fromisoformat(t), IST).isoformat(), "spot": v} for t, v in pairs]


def test_opening_range_breakout_reads_only_readings_already_taken():
    trig = Trigger.parse({"type": "orb", "range_end": "09:30", "buffer_pct": 0.05})
    ticks = _ticks([("09:15", 25000), ("09:20", 25040), ("09:25", 24980), ("09:29", 25010)])
    at = datetime.combine(DAY, time(9, 40), IST)
    assert fire(trig, ticks, 25060.0, at)[0] == "long"                  # above 25040 x 1.0005 = 25052.5
    assert fire(trig, ticks, 25050.0, at)[0] is None
    assert fire(trig, ticks, 24960.0, at)[0] == "short"
    assert fire(trig, ticks, 25060.0, datetime.combine(DAY, time(9, 25), IST))[0] is None
    late = _ticks([("09:22", 25000), ("09:25", 25040), ("09:29", 24980)])
    assert "open was not observed" in fire(trig, late, 25100.0, at)[1]
    gap = _ticks([("09:15", 25000), ("09:16", 25040), ("09:17", 24980)])
    assert "not observed through its end" in fire(trig, gap, 25100.0, at)[1]


def test_move_and_range_triggers():
    ticks = _ticks([("09:15", 25000)])
    at = datetime.combine(DAY, time(11, 0), IST)
    mv, rg = Trigger.parse({"type": "move", "pct": 0.5}), Trigger.parse({"type": "range", "pct": 0.4})
    assert fire(mv, ticks, 25130.0, at)[0] == "long" and fire(mv, ticks, 24870.0, at)[0] == "short"
    assert fire(mv, ticks, 25100.0, at)[0] is None
    assert fire(rg, ticks, 25090.0, at)[0] == "any" and fire(rg, ticks, 25110.0, at)[0] is None


# -- entry checks and sizing -----------------------------------------------------------------------------

def test_quote_problems():
    cfg = _cfg([{"template": "debit_spread"}])
    ok = q("X", 100.0, 101.0)
    assert quote_problem(ok, NOW, "BUY", 65, False, cfg) == ""
    assert quote_problem(None, NOW, "BUY", 65, False, cfg) == "no quote"
    assert quote_problem(q("X", None, 1.0), NOW, "BUY", 65, False, cfg) == "not two-sided"
    assert "spread" in quote_problem(q("X", 100.0, 111.0), NOW, "BUY", 65, False, cfg)
    assert quote_problem(q("X", 100.0, 110.0), NOW, "BUY", 65, False, cfg) == ""          # 10% of the mid, allowed
    assert "spread" in quote_problem(q("F", 25000.0, 25010.0), NOW, "BUY", 65, True, cfg)  # futures: 0.02%
    assert "old" in quote_problem(q("X", 100.0, 101.0, ts=NOW - timedelta(seconds=61)), NOW, "BUY", 65, False, cfg)
    assert "clock" in quote_problem(q("X", 100.0, 101.0, ts=NOW + timedelta(seconds=6)), NOW, "BUY", 65, False, cfg)
    assert "top of book" in quote_problem(q("X", 100.0, 101.0, qty=50), NOW, "SELL", 65, False, cfg)
    assert quote_problem(Quote("X", 100.0, 101.0, None, None, None, NOW, NOW), NOW, "BUY", 65, False, cfg) == ""


def _strategy(text, budget=10_000.0, stop=None, target=None, max_units=5):
    cfg = _cfg([{"name": "s", "legs": text, "window": ["09:20", "14:00"], "risk_budget": budget,
                 "max_units": max_units, "exit": {k: v for k, v in (("stop", stop), ("target", target)) if v}}])
    return cfg.strategies[0]


def _pairs(m, text, spot=25000.0, quotes=None):
    pairs, needs, why = resolve(m, parse_legs(text), spot, DAY, quotes or {}, 15)
    assert pairs is not None, why
    return pairs


def test_defined_risk_is_the_band_worst_plus_round_trip_costs_and_sets_the_units():
    m = master()
    pairs = _pairs(m, "CE+@ATM CE-@ATM+2")
    quotes = {f"N{WK:%m%d}25000CE": q("a", 119.5, 120.0), f"N{WK:%m%d}25100CE": q("b", 70.0, 70.5)}
    s = _strategy("CE+@ATM CE-@ATM+2", budget=10_000.0, stop={"R": 0.5})
    got = size(s, pairs, quotes, 25000.0, FNO_ZERODHA)
    one = build(pairs, quotes, 1)
    unit = 50 * LOT + entry_costs(one, FNO_ZERODHA) + sum(
        option_order_costs(g.price, g.qty, "SELL" if g.side == "BUY" else "BUY", FNO_ZERODHA).total for g in one)
    assert got.units == int(10_000 // unit) == 2 and got.defined
    assert math.isclose(got.structural, 50 * LOT * 2) and got.risk <= 10_000
    assert math.isclose(got.stop, round(0.5 * got.risk, 2))
    assert "over the" in size(_strategy("CE+@ATM CE-@ATM+2", budget=3000.0), pairs, quotes, 25000.0, FNO_ZERODHA)
    assert "debit" in size(_strategy("CE+@ATM CE-@ATM+2", stop={"credit_mult": 1.0}), pairs, quotes, 25000.0,
                           FNO_ZERODHA)


def test_undefined_risk_is_the_stop_with_slippage_and_margin_counts_one_side():
    m = master()
    pairs = _pairs(m, "CE-@ATM PE-@ATM")
    quotes = {f"N{WK:%m%d}25000CE": q("c", 120.0, 120.5), f"N{WK:%m%d}25000PE": q("p", 110.0, 110.5)}
    s = _strategy("CE-@ATM PE-@ATM", budget=20_000.0, stop={"credit_mult": 0.3}, max_units=1)
    got = size(s, pairs, quotes, 25000.0, FNO_ZERODHA)
    credit = 230.0 * LOT
    assert not got.defined and math.isclose(got.credit, credit) and math.isclose(got.stop, round(0.3 * credit, 2))
    assert math.isclose(got.risk, round(0.3 * credit * STOP_SLIP + got.costs_in + got.costs_out_est, 2))
    assert math.isclose(got.margin, round(0.12 * 25000.0 * LOT, 2))            # one lot of exposure, not two


def test_legs_of_different_lot_sizes_are_not_combined_and_freeze_caps_units():
    m = master(fut_lot=75)
    pairs = _pairs(m, "FUT+ CE-@ATM+2")
    quotes = {"NFUT": q("NFUT", 25100.0, 25100.5), f"N{WK:%m%d}25100CE": q("c", 70.0, 70.5)}
    assert "lot sizes differ" in size(_strategy("FUT+ CE-@ATM+2", stop={"points": 40}), pairs, quotes, 25000.0,
                                      FNO_ZERODHA)
    cs = [OptionContract(c.symbol, c.underlying, c.expiry, c.strike, c.right, c.lot_size, c.tick_size,
                         freeze_qty=130 if c.right == "CE" else None) for c in master().contracts]
    pairs = _pairs(ContractMaster(cs, "t", NOW), "CE+@ATM CE-@ATM+2")
    quotes = {f"N{WK:%m%d}25000CE": q("a", 119.5, 120.0), f"N{WK:%m%d}25100CE": q("b", 70.0, 70.5)}
    assert size(_strategy("CE+@ATM CE-@ATM+2", budget=100_000.0, max_units=10), pairs, quotes, 25000.0,
                FNO_ZERODHA).units == 2


# -- the engine on a scripted feed ------------------------------------------------------------------------------

class Scripted(FnoFeed):
    """A feed whose index and quotes the test sets. Synthetic, so it runs only in a mock directory."""
    key, name, is_mock = "scripted", "SCRIPTED (test)", True

    def __init__(self, clock):
        self.clock, self.spot, self.prices, self.down = clock, 25000.0, {}, set()
        self.m = master()

    def contract_master(self, u):
        return self.m

    def fno_master(self, u):
        return self.m

    def index_quote(self, u):
        return IndexQuote(self.spot, self.clock.now, self.clock.now)

    def quotes(self, cs):
        return {c.symbol: q(c.symbol, *self.prices[c.symbol], ts=self.clock.now)
                for c in cs if c.symbol in self.prices and c.symbol not in self.down}

    def index_close(self, u, d):
        return None


class Clock:
    def __init__(self, at):
        self.now = at

    def __call__(self):
        return self.now


SPREAD = {"name": "bull", "legs": "CE+@ATM CE-@ATM+2", "window": ["09:20", "14:00"], "trigger": {"type": "at"},
          "risk_budget": 10_000, "max_units": 1, "exit": {"stop": {"R": 0.5}, "target": {"R": 0.5}}}
A, B = f"N{WK:%m%d}25000CE", f"N{WK:%m%d}25100CE"


def scripted(strategies=(SPREAD,), **kw):
    clock = Clock(datetime.combine(DAY, time(9, 20), IST))
    feed = Scripted(clock)
    feed.prices = {A: (119.5, 120.0), B: (70.0, 70.5)}
    ex = Explorer(feed, tmpdir() / "x_mock", _cfg(list(strategies), **kw), calendar=CAL, now_fn=clock)
    ex.init()
    return ex, clock, feed


def at(ex, clock, hh, mm, d=DAY):
    clock.now = datetime.combine(d, time(hh, mm), IST)
    return ex.run_pass()


def kinds(ex):
    return [r["type"] for r in ex.journal.records()]


def test_entry_fills_at_the_touch_and_the_target_closes_it():
    ex, clock, feed = scripted()
    at(ex, clock, 9, 20)
    e = next(iter(ex.state().open.values()))
    assert [(g["side"], g["price"]) for g in e["legs"]] == [("BUY", 120.0), ("SELL", 70.0)]
    assert e["defined"] and math.isclose(e["structural_loss"], 50 * LOT) and e["units"] == 1
    feed.prices = {A: (180.0, 180.5), B: (100.0, 100.5)}     # long +60 at the bid, short -30.5 at the ask
    at(ex, clock, 9, 21)
    st = ex.state()
    x = st.closed[0]["exit"]
    assert not st.open and x["reason"] == "target" and math.isclose(x["R"], round(x["net"] / e["risk"], 4))
    assert math.isclose(x["gross"], (60.0 - 30.5) * LOT)
    at(ex, clock, 9, 22)
    assert len(ex.state().closed) == 1                       # max_entries 1 a day


def test_stop_trail_and_square_off():
    ex, clock, feed = scripted([dict(SPREAD, exit={"stop": {"R": 0.3}})])
    at(ex, clock, 9, 20)
    feed.prices = {A: (90.0, 90.5), B: (55.0, 55.5)}         # -30 and +14.5: -15.5 x 65 = -1007.5 gross
    at(ex, clock, 9, 25)
    assert ex.state().closed[0]["exit"]["reason"] == "stop"

    ex, clock, feed = scripted([dict(SPREAD, exit={"trail": {"after_R": 0.4, "lock_R": 0.1}})])
    at(ex, clock, 9, 20)
    feed.prices = {A: (170.0, 170.5), B: (95.0, 95.5)}
    at(ex, clock, 9, 25)                                     # +0.5R: the trail arms
    assert ex.state().open
    feed.prices = {A: (125.0, 125.5), B: (72.0, 72.5)}
    at(ex, clock, 9, 30)                                     # back under +0.1R
    assert ex.state().closed[0]["exit"]["reason"] == "trail"

    ex, clock, feed = scripted([dict(SPREAD, exit={})])
    at(ex, clock, 9, 20)
    at(ex, clock, 15, 14)
    assert ex.state().open
    at(ex, clock, 15, 15)
    assert ex.state().closed[0]["exit"]["reason"] == "time"


def test_the_day_loss_limit_closes_everything_and_stops_entries():
    other = dict(SPREAD, name="later", window=["10:00", "14:00"])
    ex, clock, feed = scripted([SPREAD, other], daily_loss_limit=500)
    at(ex, clock, 9, 20)
    feed.prices = {A: (100.0, 100.5), B: (65.0, 65.5)}
    at(ex, clock, 9, 30)
    st = ex.state()
    assert "XP_HALT" in kinds(ex) and not st.open and st.closed[0]["exit"]["reason"] == "daily loss limit"
    at(ex, clock, 10, 0)
    assert not ex.state().open and any(s[2] == "halted" for s in ex.state().skips)


def test_a_position_without_a_quote_at_the_halt_closes_on_the_next_pass():
    ex, clock, feed = scripted([SPREAD], daily_loss_limit=5000)
    at(ex, clock, 9, 20)
    feed.down = {A}
    ex.journal.append("XP_HALT", asof=DAY.isoformat(), feed="scripted", day=DAY.isoformat(), pnl=-5000.0,
                      limit=5000.0)                          # the day halted while this leg had no quote
    at(ex, clock, 9, 26)
    assert ex.state().open                                   # no quote: it cannot close yet
    feed.down = set()
    at(ex, clock, 9, 27)
    st = ex.state()
    assert not st.open and st.closed[0]["exit"]["reason"] == "daily loss limit"


def test_the_kill_switch_closes_and_blocks():
    ex, clock, feed = scripted()
    at(ex, clock, 9, 20)
    (ex.state_dir / "KILL").write_text("test")
    at(ex, clock, 9, 21)
    st = ex.state()
    assert not st.open and st.closed[0]["exit"]["reason"] == "kill switch"


def test_no_quote_at_the_last_call_books_the_last_recent_mark_or_minus_one_r():
    ex, clock, feed = scripted([dict(SPREAD, exit={})])
    at(ex, clock, 9, 20)
    at(ex, clock, 15, 14)
    feed.down = {A}
    at(ex, clock, 15, 15)
    assert ex.state().open                                   # due, no quote: retried
    at(ex, clock, 15, 29)
    x = ex.state().closed[0]["exit"]
    assert x["reason"] == "unobserved" and "last mark at 15:14" in x["flags"][1]

    ex, clock, feed = scripted([dict(SPREAD, exit={})])
    at(ex, clock, 9, 20)
    at(ex, clock, 9, 21)                                     # a mark, six hours before the last call
    feed.down = {A}
    at(ex, clock, 15, 29)
    x = ex.state().closed[0]["exit"]
    assert x["R"] == -1.0 and "-1 R" in x["flags"][1]


def test_a_position_left_open_overnight_is_closed_on_the_next_pass():
    ex, clock, feed = scripted([dict(SPREAD, exit={})])
    at(ex, clock, 9, 20)
    at(ex, clock, 9, 30, d=DAY + timedelta(days=1))
    x = ex.state().closed[0]["exit"]
    assert x["reason"] == "unobserved" and "runner was down" in x["flags"][0] and x["R"] == -1.0


def test_skips_are_journaled_once_a_day_per_reason_and_entries_respect_max_open():
    s2 = dict(SPREAD, name="second")
    ex, clock, feed = scripted([SPREAD, s2], max_open=1)
    for mm in range(20, 26):
        at(ex, clock, 9, mm)
    recs = [r["payload"] for r in ex.journal.records() if r["type"] == "XP_SKIP"]
    assert len(ex.state().open) == 1
    assert [(p["strategy"], p["code"]) for p in recs] == [("second", "max_open")]


def test_quote_failures_block_an_entry_and_say_why():
    ex, clock, feed = scripted()
    feed.prices = {A: (119.5, 120.0), B: (60.0, 75.0)}       # a 15-point spread on a 67.5 mid
    rep = at(ex, clock, 9, 20)
    assert not ex.state().open and any("spread" in s for s in rep.skips)


def test_margin_room_limits_entries():
    straddle = {"name": "strad", "legs": "CE-@ATM PE-@ATM", "window": ["09:20", "14:00"], "trigger": {"type": "at"},
                "risk_budget": 50_000, "max_units": 1, "exit": {"stop": {"credit_mult": 0.3}}}
    ex, clock, feed = scripted([straddle], capital=200_000)
    feed.prices.update({f"N{WK:%m%d}25000PE": (110.0, 110.5)})
    rep = at(ex, clock, 9, 20)                                # needs ~1.95 lakh against 1.6 lakh of room
    assert not ex.state().open and any("margin" in s for s in rep.skips)


def test_a_changed_configuration_starts_a_new_generation_and_the_screen_restarts():
    ex, clock, feed = scripted()
    at(ex, clock, 9, 20)
    feed.prices = {A: (180.0, 180.5), B: (100.0, 100.5)}
    at(ex, clock, 9, 21)
    ex.cfg = _cfg([dict(SPREAD, risk_budget=12_000)])
    rep = at(ex, clock, 9, 22, d=DAY + timedelta(days=1))
    assert kinds(ex).count("XP_CONFIG") == 2 and any("new generation" in a for a in rep.alerts)
    sb = scoreboard(ex.journal.records())
    assert sb["by_strategy"]["bull"]["trades"] == 1 and sb["by_strategy"]["bull"]["screen"]["trades"] == 0


def test_the_screen_is_the_betting_statistic_at_alpha_over_m_and_a_loss_past_minus_two_voids_it():
    path = tmpdir() / "j.jsonl"
    j = Journal(path, "fno-explore")
    cfg = {"strategies": [{"name": "a"}, {"name": "b"}], "screen_alpha": 0.05}
    j.append("XP_INIT", asof="2026-11-02")
    j.append("XP_CONFIG", asof="2026-11-02", sha="g1", config=cfg)
    rs = {"a": [0.4] * 12, "b": [0.3, -2.5]}
    for name, seq in rs.items():
        for i, r in enumerate(seq):
            pid = f"{name}-{i}"
            j.append("XP_ENTRY", asof="2026-11-02", id=pid, strategy=name, day="2026-11-02", config_sha="g1",
                     entry_costs=10.0, risk=1000.0)
            j.append("XP_EXIT", asof="2026-11-02", id=pid, strategy=name, day="2026-11-02", net=r * 1000, R=r,
                     reason="time", exit_costs=10.0)
    sb = scoreboard(j.records())
    assert sb["screen_threshold"] == 40.0
    from research.fno_stats import betting_path
    assert math.isclose(sb["by_strategy"]["a"]["screen"]["e_value"], round(float(betting_path([0.4] * 12)[-1]), 4))
    assert sb["by_strategy"]["b"]["screen"]["void"] and not sb["by_strategy"]["b"]["screen"]["passed"]


def test_synthetic_data_stays_out_of_the_paper_book_and_init_is_once():
    clock = Clock(NOW)
    cfg = _cfg([SPREAD])
    try:
        Explorer(Scripted(clock), tmpdir() / "fno_explore", cfg, calendar=CAL)
        raise AssertionError("a mock feed must not write a real explorer book")
    except Refused:
        pass

    class Real(Scripted):
        is_mock = False
    try:
        Explorer(Real(clock), tmpdir() / "x_mock", cfg, calendar=CAL)
        raise AssertionError("a real feed must not write into a mock directory")
    except Refused:
        pass
    ex, clock, feed = scripted()
    try:
        ex.init()
        raise AssertionError("init is once")
    except Refused:
        pass


def test_a_tampered_master_file_stops_the_explorer():
    ex, clock, feed = scripted()
    at(ex, clock, 9, 20)
    path = ex.state_dir / "master" / f"{DAY.isoformat()}.json"
    path.write_text(path.read_text().replace('"lot_size": 65', '"lot_size": 75', 1))
    try:
        at(ex, clock, 9, 21)
        raise AssertionError("a changed master must stop the explorer")
    except Refused:
        pass


def test_days_on_the_synthetic_feed_square_off_every_position_and_the_journal_verifies():
    root = tmpdir()
    clock = Clock(datetime(2026, 11, 2, 9, 0, tzinfo=IST))
    feed = MockIntradayFeed(clock, seed=3)
    cfg = load_config(Path(__file__).resolve().parents[1] / "config" / "fno_strategies.example.json")
    ex = Explorer(feed, root / "e_mock", cfg, now_fn=clock)
    ex.init()
    d = date(2026, 11, 2)
    for _ in range(3):
        while not ex.cal.is_trading_day(d):
            d += timedelta(days=1)
        t = datetime.combine(d, time(9, 10), IST)
        while t.time() <= time(15, 30):
            clock.now = t
            ex.run_pass(t)
            t += timedelta(minutes=5) if time(10, 0) < t.time() < time(15, 0) else timedelta(minutes=1)
        assert not ex.state().open, f"positions left open after {d}"
        d += timedelta(days=1)
    st = ex.state()
    assert verify_file(ex.journal.path).ok and st.closed and not st.void
    for c in st.closed:
        x = c["exit"]
        assert c["t"] < x["t"] and math.isclose(x["R"], round(x["net"] / c["risk"], 4))
        assert x["t"][11:16] <= "15:29" and c["t"][11:16] >= "09:20"
        s = next(s for s in cfg.strategies if s.name == c["strategy"])
        assert c["risk"] <= s.risk_budget + 1e-6 and c["units"] <= s.max_units
    for (day, name), n in st.entries.items():
        assert n <= next(s for s in cfg.strategies if s.name == name).max_entries


# -- futures in the masters -----------------------------------------------------------------------------------------

def test_futures_rows_from_kite_and_groww():
    rows = [{"name": "NIFTY", "instrument_type": "FUT", "segment": "NFO-FUT", "expiry": date(2026, 11, 24),
             "lot_size": 65, "tick_size": 0.1, "tradingsymbol": "NIFTY26NOVFUT", "instrument_token": 1, "exchange": "NFO"},
            {"name": "BANKNIFTY", "instrument_type": "FUT", "segment": "NFO-FUT", "expiry": date(2026, 11, 24),
             "lot_size": 30, "tick_size": 0.1, "tradingsymbol": "BANKNIFTY26NOVFUT"},
            {"name": "NIFTY", "instrument_type": "CE", "segment": "NFO-OPT", "expiry": date(2026, 11, 24),
             "strike": 25000, "lot_size": 65, "tick_size": 0.05, "tradingsymbol": "NIFTY26NOV25000CE"}]
    got = futures_from_kite_rows(rows, "NIFTY")
    assert [(c.symbol, c.right, c.strike, c.lot_size) for c in got] == [("NIFTY26NOVFUT", "FUT", 0.0, 65)]
    df = pd.DataFrame([{"trading_symbol": "NIFTY26NOVFUT", "underlying_symbol": "NIFTY", "instrument_type": "FUT",
                        "expiry_date": "2026-11-24", "strike_price": "", "lot_size": "65", "tick_size": "0.1",
                        "segment": "FNO", "exchange": "NSE", "groww_symbol": "NSE-NIFTY-24Nov26-FUT"}])
    got = futures_from_groww_frame(df, "NIFTY")
    assert len(got) == 1 and got[0].expiry == date(2026, 11, 24) and got[0].right == "FUT"


def test_the_mock_lists_monthly_futures_priced_with_carry_and_a_tight_spread():
    clock = Clock(datetime(2026, 11, 4, 10, 0, tzinfo=IST))
    f = MockIntradayFeed(clock)
    m = f.fno_master("NIFTY")
    futs = sorted((c for c in m.contracts if c.right == "FUT"), key=lambda c: c.expiry)
    assert len(futs) == 3 and all(c.expiry in set(m.expiries()) for c in futs)
    qf = f.quotes(futs[:1])[futs[0].symbol]
    spot = f.index_quote("NIFTY").value
    assert qf.bid > spot and qf.ask - qf.bid < 2.0


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
