"""
Offline tests for the pre-registered research code. The timing tests matter
most: an off-by-one in execution quietly turns a backtest into a forecast.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import inspect
import tempfile
from pathlib import Path

import research.data as data
import research.strategy_a as strategy_a
import research.strategy_b as strategy_b
from research.metrics import perf, split
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


DAYS = pd.bdate_range("2019-01-01", "2020-06-30")
M, E, E_NEXT = pd.Timestamp("2020-01-31"), pd.Timestamp("2020-02-03"), pd.Timestamp("2020-03-02")


def _step(day, factor, base=100.0):
    """Flat at `base`, multiplied by `factor` from `day` on."""
    return pd.Series(np.where(DAYS >= pd.Timestamp(day), base * factor, base), index=DAYS)


def _momentum_panel():
    """
    Ten fillers that drift up smoothly, plus four names whose only move sits on
    one side of a window edge, so each edge of 12-1 momentum is pinned:
      IN_START  +30% just after M-12 (2019-01-31)  -> inside the window
      IN_END    +30% just before M-1 (2019-12-31)  -> inside the window
      OUT_EARLY +30% just before M-12              -> outside
      OUT_SKIP  +30% inside the skipped month      -> outside
    and two moves that pin the holding window E -> E_next:
      IN_START  +10% on the close of E itself      -> already in the entry price
      IN_END    +20% on E+1, +50% on E_next+1      -> first counts, second does not
    """
    px = pd.DataFrame({f"F{i:02d}": np.linspace(100, 110, len(DAYS)) for i in range(10)}, index=DAYS)
    px["IN_START"] = _step("2019-02-15", 1.3) * np.where(DAYS >= E, 1.1, 1.0)
    px["IN_END"] = (_step("2019-12-16", 1.3) * np.where(DAYS >= pd.Timestamp("2020-02-04"), 1.2, 1.0)
                    * np.where(DAYS >= pd.Timestamp("2020-03-03"), 1.5, 1.0))
    px["OUT_EARLY"] = _step("2019-01-15", 1.3)
    px["OUT_SKIP"] = _step("2020-01-15", 1.3)
    return px


def test_b_schedule_and_12_1_window_on_fixed_dates():
    px = _momentum_panel()
    sch = schedule(px)
    assert (sch.loc[12, "M"], sch.loc[12, "E"], sch.loc[13, "E"]) == (M, E, E_NEXT)
    first = run(px, pd.Series(1000.0, index=DAYS)).iloc[0]
    held = set(first["holdings"])
    assert {"IN_START", "IN_END"} <= held, "a move inside M-12..M-1 was missed"
    assert not held & {"OUT_EARLY", "OUT_SKIP"}, "a move outside M-12..M-1 counted"
    assert held == {"IN_START", "IN_END"} | {f"F{i:02d}" for i in range(8)}, "ties are not alphabetical"


def test_b_holds_from_close_of_e_to_close_of_next_e():
    px = _momentum_panel()
    first = run(px, pd.Series(1000.0, index=DAYS)).iloc[0]
    # By hand, from the series built above, not from the code under test:
    expect = {s: px.loc[E_NEXT, s] / px.loc[E, s] - 1 for s in first["holdings"]}
    assert expect["IN_START"] == 0.0 and abs(expect["IN_END"] - 0.2) < 1e-12
    assert abs(first["gross"] - sum(expect.values()) / 10) < 1e-12


def test_b_net_return_drift_and_turnover_by_hand():
    a = pd.Series(np.where(DAYS > E, 200.0, 100.0), index=DAYS)       # doubles in month one, then flat
    px = pd.DataFrame({"A": a, "B": 100.0}, index=DAYS)
    res = run(px, pd.Series(1000.0, index=DAYS))
    one, two = res.iloc[0], res.iloc[1]
    assert one["turnover"] == 1.0 and one["gross"] == 0.5
    assert abs(one["net"] - ((1 - 0.0025) * 1.5 - 1)) < 1e-12
    # Drifted to 2/3 : 1/3, rebalanced back to 1/2 : 1/2 -> one third of the book trades.
    assert abs(two["turnover"] - 1 / 3) < 1e-12 and two["gross"] == 0.0
    assert abs(two["net"] - (-0.0025 / 3)) < 1e-12


def test_b_trend_filter_reads_the_close_on_m():
    px = _momentum_panel()
    nifty = pd.Series(1000.0, index=DAYS)
    nifty[M] = 900.0                     # below its SMA200 on M only; back above by E
    res = run(px, nifty, trend_filter=True)
    assert res.iloc[0]["holdings"] == [] and res.iloc[0]["net"] == 0.0, "the filter did not read M"
    assert res.iloc[1]["holdings"], "a dip on the previous M kept the next month in cash"
    late = nifty[nifty.index >= pd.Timestamp("2019-09-02")]   # SMA200 not yet defined on M
    assert run(px, late, trend_filter=True).iloc[0]["holdings"] == [], "no SMA must mean cash"


def test_b_months_are_indexed_and_split_by_execution_date():
    res = run(_momentum_panel(), pd.Series(1000.0, index=DAYS))
    assert (res.index == pd.DatetimeIndex(res["E"])).all()
    r = pd.Series([0.01, 0.02], index=pd.DatetimeIndex(["2022-12-01", "2023-01-02"]))
    parts = split(r)
    assert list(parts["development"].index) == [pd.Timestamp("2022-12-01")]
    assert list(parts["holdout"].index) == [pd.Timestamp("2023-01-02")]


def test_perf_vol_and_sharpe_on_a_known_series():
    r = _series([0.01, -0.02, 0.03, 0.00])
    m = perf(r, 12)
    sd = float(np.std(r.values, ddof=1))
    assert abs(m["vol"] - sd * np.sqrt(12)) < 1e-12
    assert abs(m["sharpe"] - r.mean() / sd * np.sqrt(12)) < 1e-12


def test_pre_registered_constants_are_what_ran():
    assert strategy_a.COST_PER_SWITCH == 0.0010
    sig = inspect.signature(strategy_a.daily_returns)
    assert sig.parameters["sma"].default == 200 and sig.parameters["cash_rate"].default == 0.0
    assert strategy_b.COST_PER_SIDE == 0.0025 and strategy_b.TOP_N == 10
    assert inspect.signature(split).parameters["dev_end"].default == "2022-12-31"


def test_load_back_adjusts_an_exact_bonus_factor_only():
    days = pd.bdate_range("2024-01-01", periods=40)
    close = np.where(days >= days[20], 200.0, 300.0)          # 2/3 overnight: a 1:2 bonus left unadjusted
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1000.0},
                      index=pd.Index(days, name="date"))
    with tempfile.TemporaryDirectory() as tmp:
        df.to_csv(Path(tmp) / "TEST.csv")
        adj = data.load("TEST", cache=Path(tmp))
    assert np.allclose(adj["close"].iloc[:20], 200.0) and np.allclose(adj["close"].iloc[20:], 200.0)
    assert np.allclose(adj["volume"].iloc[:20], 1500.0) and np.allclose(adj["volume"].iloc[20:], 1000.0)
    crash = df.copy()
    crash.loc[crash.index >= days[20], ["open", "high", "low", "close"]] = 180.0   # -40%: on no factor
    assert data.corporate_action_fixes(crash) == [], "a real move was 'fixed'"


def test_placeholder_rows_are_found():
    days = pd.bdate_range("2024-01-01", periods=5)
    df = pd.DataFrame({"open": [10, 11, 12, 12, 13.0], "high": [10, 11, 12, 12, 13.0],
                       "low": [10, 11, 12, 12, 13.0], "close": [10, 11, 12, 12, 13.0],
                       "volume": [5, 5, 5, 0, 5.0]}, index=days)
    assert list(data.placeholder_rows(df)) == [days[3]]


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
