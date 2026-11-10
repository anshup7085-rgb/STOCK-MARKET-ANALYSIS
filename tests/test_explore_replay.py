"""
research/explore_replay.py: the explorer itself, run through one synthetic session served from
Dhan-style minutes. SYNTHETIC prices (Black-76 on a seeded index path): this checks the wiring, it
says nothing about NIFTY. Run: PYTHONPATH=. python tests/test_explore_replay.py
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

from market.config import IST
from research import explore_replay as er
from trading.fno import dhan_data as dd
from trading.fno.explore import load_config
from trading.fno.explore_cli import DEFAULT_CONFIG, whatif_config

DAY, EXPIRY, FUT_EXPIRY = date(2022, 3, 2), date(2022, 3, 3), date(2022, 3, 31)


def _bars():
    rng = random.Random(3)
    spot, out = 16600.0, {}
    t0 = datetime.combine(DAY, time(9, 15), IST)
    for i in range(375):
        spot *= math.exp(rng.gauss(0, 0.0007))
        hm = (t0 + timedelta(minutes=i)).strftime("%H:%M")
        atm = round(spot / 50) * 50
        tau = max((datetime.combine(EXPIRY, time(15, 30), IST) - (t0 + timedelta(minutes=i))).total_seconds(), 60) / (365 * 86400)
        m = {}
        for off in dd.OFFSETS:
            k = atm + 50 * off
            sd = 0.14 * math.sqrt(tau)
            d1 = (math.log(spot / k) + sd * sd / 2) / sd
            n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))           # noqa: E731
            call = spot * n(d1) - k * n(d1 - sd)
            for right, px in (("CALL", call), ("PUT", call - spot + k)):
                px = max(0.05, round(px / 0.05) * 0.05)
                m[(off, right)] = dd.Bar(float(k), px, px, px, px, 100.0, 1000.0, 14.0, round(spot, 2))
        out[hm] = m
    return out


def test_the_explorer_runs_a_whole_session_on_dhan_minutes():
    bars = _bars()
    raw = json.loads(Path(DEFAULT_CONFIG).read_text())
    trades = er.run_day(DAY, bars, EXPIRY, FUT_EXPIRY, load_config(whatif_config(raw)), 0.002)
    names = {t["strategy"] for t in trades}
    assert {"short_straddle", "iron_condor"} <= names, names                       # time-triggered: always fire
    straddle = next(t for t in trades if t["strategy"] == "short_straddle")
    assert straddle["opened"] == "09:20" and straddle["closed"] <= "15:15"
    assert all(t["closed"] <= "15:15" or t["reason"] != "time" for t in trades)
    assert all(math.isclose(t["R"], round(t["net"] / t["risk"], 4), abs_tol=1e-3) for t in trades)
    limited = er.run_day(DAY, bars, EXPIRY, FUT_EXPIRY, load_config(raw), 0.002)
    assert len(limited) <= len(trades)                                              # limits only remove trades


def test_monthly_expiry_and_the_feed_serves_the_bar_that_just_closed():
    chain = [date(2022, 3, 3), date(2022, 3, 10), date(2022, 3, 17), date(2022, 3, 24), date(2022, 3, 31),
             date(2022, 4, 7)]
    assert er.monthly_expiry(chain, date(2022, 3, 2)) == date(2022, 3, 31)
    assert er.monthly_expiry(chain, date(2022, 4, 1)) is None or er.monthly_expiry(chain, date(2022, 4, 1)) == date(2022, 4, 7)
    bars = _bars()
    clock = er.Clock(datetime.combine(DAY, time(9, 20), IST))
    feed = er.DhanReplayFeed(DAY, bars, EXPIRY, FUT_EXPIRY, clock, 0.002)
    q = feed.index_quote("NIFTY")
    assert q.value == next(iter(bars["09:19"].values())).spot and q.day_open == next(iter(bars["09:15"].values())).spot
    c = next(c for c in feed.master.contracts if c.right == "CE" and c.strike == bars["09:19"][(0, "CALL")].strike)
    quote = feed.quotes([c])[c.symbol]
    assert quote.bid < bars["09:19"][(0, "CALL")].close < quote.ask
    fq = feed.quotes([feed.fut])[feed.fut.symbol]                      # passes the explorer's 0.02% futures check
    assert (fq.ask - fq.bid) / ((fq.ask + fq.bid) / 2) <= 0.0002



def test_a_chosen_set_of_strategies_and_gross_before_fees():
    raw = json.loads(Path(DEFAULT_CONFIG).read_text())
    picked = er.pick_strategies(raw, ["future_breakout", "long_option"])
    assert [d["name"] for d in picked["strategies"]] == ["future_breakout", "long_option"]
    assert picked["strategies"][1] == {"name": "long_option", "template": "long_option", "risk_budget": 6000,
                                       "max_units": 1}
    assert [d["name"] for d in raw["strategies"]][0] == "future_breakout" and len(raw["strategies"]) > 2
    try:
        er.pick_strategies(raw, ["no_such_thing"])
        raise AssertionError("an unknown strategy was accepted")
    except SystemExit:
        pass
    trades = er.run_day(DAY, _bars(), EXPIRY, FUT_EXPIRY, load_config(whatif_config(picked)), 0.002)
    assert {t["strategy"] for t in trades} == {"future_breakout", "long_option"}
    for t in trades:
        assert t["fees"] > 0 and math.isclose(t["net"], t["gross"] - t["fees"], abs_tol=0.05), t


def test_the_small_book_replays_within_its_capital():
    raw = json.loads((Path(DEFAULT_CONFIG).parent / "fno_small.example.json").read_text())
    trades = er.run_day(DAY, _bars(), EXPIRY, FUT_EXPIRY, load_config(raw), 0.002)
    assert trades and {t["strategy"] for t in trades} <= {"long_cheap", "late_momentum", "long_option"}
    for t in trades:                                           # one bought lot each: the premium is the risk
        assert t["risk"] <= 12_000 and t["margin_est"] <= 0.8 * 20_000, t
    assert sum(t["net"] for t in trades) >= -2_000 - 500       # the day's loss limit, give or take one fill
    line = er.book_line([{"day": "2022-03-02", "closed": "10:00", "net": -3000.0},
                         {"day": "2022-03-03", "closed": "10:00", "net": 5000.0}], 20_000)
    assert "ends at Rs22,000 after 2 trades" in line and "lowest point Rs17,000 on 2022-03-02" in line, line
    assert "2022 Rs+2,000" in line


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
