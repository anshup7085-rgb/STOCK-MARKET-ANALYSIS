"""
Offline tests for the pre-registered research code. The timing tests matter
most: an off-by-one in execution quietly turns a backtest into a forecast.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.metrics import perf
from research.strategy_a import daily_returns
from research.strategy_b import run, schedule


def _series(values, start="2020-01-01"):
    return pd.Series(values, index=pd.bdate_range(start=start, periods=len(values)), dtype=float)


def test_a_executes_a_day_after_the_signal_and_charges_that_day():
    # Flat at 100 for 6 days (SMA = 100), then 110: close > SMA from day 6.
    close = _series([100.0] * 6 + [110.0, 121.0, 121.0, 121.0])
    df = daily_returns(close, sma=3, cost=0.001)
    sig_day = close.index[6]
    assert df.loc[sig_day, "target"] == 1.0
    assert df.loc[close.index[7], "position"] == 0.0, "the day after the signal still earns the old position"
    assert df.loc[close.index[7], "switch_cost"] == 0.001, "the switch executes (and pays) at close t+1"
    assert df.loc[close.index[8], "position"] == 1.0
    # The +10% jump on the signal bar and the +10% on the execution day are NOT captured.
    assert abs(df.loc[close.index[7], "strategy"] - (-0.001)) < 1e-12


def test_a_is_causal():
    rng = np.random.default_rng(4)
    close = _series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 600))))
    full = daily_returns(close)["strategy"]
    part = daily_returns(close.iloc[:450])["strategy"]
    # The last row of a truncated run cannot know whether tomorrow switches, so compare before it.
    common = part.index[:-1]
    assert np.allclose(full.loc[common], part.loc[common]), "future bars changed past returns"


def test_b_skips_the_last_month_and_trades_after_month_end():
    days = pd.bdate_range("2019-01-01", "2020-06-30")
    rng = np.random.default_rng(5)
    px = pd.DataFrame({f"S{i:02d}": 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.004, len(days))))
                       for i in range(12)}, index=days)
    sch = schedule(px)
    k = 12
    M, E, m1 = sch.loc[k, "M"], sch.loc[k, "E"], sch.loc[k - 1, "M"]
    # S00: the worst name over months M-12..M-1, then triples in the skipped month M-1..M.
    # 12-1 momentum must not pick it; momentum measured to M (no skip) would.
    px["S00"] = np.where(px.index <= m1, np.linspace(100, 60, len(days)), 0.0)
    px.loc[px.index > m1, "S00"] = 60.0 * 3.0
    res = run(px, pd.Series(1000.0, index=days))
    first = res.iloc[0]
    assert first["E"] == E and E > M, "execution is the first trading day after month-end"
    assert "S00" not in first["holdings"], "a jump inside the skipped month drove selection"
    assert abs(first["turnover"] - 1.0) < 1e-9, "the first rebalance buys from cash"


def test_b_trend_filter_holds_cash_below_the_sma():
    days = pd.bdate_range("2019-01-01", "2020-06-30")
    px = pd.DataFrame({f"S{i:02d}": np.linspace(100, 150, len(days)) * (1 + i / 100) for i in range(12)}, index=days)
    falling = pd.Series(np.linspace(2000, 1000, len(days)), index=days)   # always below its SMA200
    res = run(px, falling, trend_filter=True)
    assert all(not h for h in res["holdings"]) and (res["net"] == 0).all()


def test_perf_on_a_known_series():
    r = _series([0.10, -0.50, 0.20])
    m = perf(r, 1)
    assert abs(m["max_dd"] - (-0.5)) < 1e-12
    assert abs(m["cagr"] - ((1.1 * 0.5 * 1.2) ** (1 / 3) - 1)) < 1e-12


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
