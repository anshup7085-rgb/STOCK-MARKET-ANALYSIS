"""
Offline tests for round 2 (research/PREREGISTRATION_2.md). No market data: every
panel here is synthetic. The causality tests are the ones section 19 requires.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from research.round2 import ETFS, G, N, N_MONTHS, Panel, NotEvaluable, fy_of, plus_12_months, schedule
from research.strategy_t import (GROWW, ZERODHA, Book, Run, Synthetic, eq_transform, faber_targets,
                                 identity_gap, mix_checks, order_costs, primary, simulate)


def _panel(seed: int = 1) -> Panel:
    return next(Synthetic(seed=seed).panels(1))


def test_schedule_executes_after_the_signal_session():
    p = _panel()
    sch = schedule(p)
    assert len(sch.X) == N_MONTHS + 1 and sch.X[-1] == pd.Timestamp("2026-10-01")
    assert sch.M[0] == pd.Timestamp("2010-01-29") and sch.X[0] == pd.Timestamp("2010-02-01")
    assert all(x > m for x, m in zip(sch.X, sch.M)), "something traded on its signal session"
    assert sch.hold_month[0] == (2010, 2) and sch.hold_month[-1] == (2026, 9)


def test_signal_at_m_uses_no_price_after_m():
    p = _panel()
    sch = schedule(p)
    w = faber_targets(p.Q, sch)
    t = 120
    cut = sch.M[t - 1]
    close = p.close.copy()
    close.loc[close.index > cut] *= np.linspace(0.3, 3.0, int((close.index > cut).sum()))[:, None]
    w2 = faber_targets(p.with_close(close).Q, sch)
    assert (w[:t] == w2[:t]).all(), "a later price changed an earlier signal"


def test_eq_uses_only_its_two_anchor_closes():
    p = _panel()
    sch = schedule(p)
    a = eq_transform(p, sch)
    close = p.close.copy()
    mid = close.index[(close.index > sch.X[0]) & (close.index < sch.X[-1])]
    close.loc[mid[::7], N] *= 1.5
    close.loc[mid[::5], G] *= 0.7
    b = eq_transform(p.with_close(close), sch)
    keep = ~close.index.isin(mid[::5])
    assert np.allclose(a.close[G][keep], b.close[G][keep]), "EQ read a close other than its anchors"
    qa = a.Q
    assert math.isclose(math.log(qa.at[sch.X[-1], G] / qa.at[sch.X[0], G]),
                        math.log(qa.at[sch.X[-1], N] / qa.at[sch.X[0], N]), rel_tol=1e-9)


def test_monthly_returns_are_exactly_additive():
    p = _panel()
    sch = schedule(p)
    w = faber_targets(p.Q, sch)
    run = simulate("monthly", p, sch, targets=w)
    assert identity_gap(w, run) < 1e-12
    wbar = np.tile(w.mean(axis=0), (N_MONTHS, 1))
    assert identity_gap(wbar, simulate("monthly", p, sch, targets=wbar)) < 1e-12


def test_known_drift_and_cash_rate_cannot_move_the_statistic():
    rng = np.random.default_rng(3)
    w = rng.choice([0.0, 0.5], size=(N_MONTHS, 2))
    r = rng.normal(0.01, 0.05, size=(N_MONTHS, 2))
    zero = np.zeros(N_MONTHS)

    def fake(rr):
        V = np.r_[1.0, np.cumprod(1 + (w * rr).sum(axis=1))]
        return Run(V=V, C=zero * V[:-1], Ck=np.zeros((N_MONTHS, 2)), r=rr, book=None)

    wbar = w.mean(axis=0)
    a = primary(w, fake(r), fake(r), wbar)
    b = primary(w, fake(r + np.array([0.02, -0.01])), fake(r + np.array([0.02, -0.01])), wbar)
    assert math.isclose(a["dbar"], b["dbar"], abs_tol=1e-15) and math.isclose(a["t"], b["t"], rel_tol=1e-9)


def test_groww_card_and_doubling():
    cost, stt = order_costs(GROWW, N, "sell", 250_000.0)
    b = 20.0
    ex, se = 250_000 * 0.00297 / 100, 250_000 * 0.0001 / 100
    expect = b + ex + se + 0.18 * (b + ex + se) + 250_000 * 0.0005 + 250_000 * 0.00001
    assert math.isclose(cost, expect) and math.isclose(stt, 2.5)
    assert order_costs(GROWW, G, "sell", 250_000.0)[1] == 0.0, "gold ETFs carry no STT"
    assert order_costs(ZERODHA, N, "buy", 250_000.0)[0] < order_costs(GROWW, N, "buy", 250_000.0)[0]
    d = GROWW.doubled()
    assert d.gst_pct == 36.0 and d.dp_per_sell == 47.2 and d.brokerage_min == 10.0


def test_dp_charge_once_per_etf_per_session():
    book = Book(GROWW, 0.0, taxed=False)
    book.units[N] = 100.0
    day = pd.Timestamp("2020-01-01")
    c1 = book.sell(N, 1000.0, day, 100.0)
    c2 = book.sell(N, 1000.0, day, 100.0)
    assert math.isclose(c1 - c2, GROWW.dp_per_sell)


def test_tax_fifo_terms_setoff_carry_and_no_refunds():
    b = Book(ZERODHA.doubled().__class__(name="flat", brokerage_pct=0, brokerage_min=0, brokerage_cap=0,
                                          exchange_pct=0, sebi_pct=0, gst_pct=0, stamp_buy_pct=0,
                                          stt_sell_n_pct=0, stt_sell_g_pct=0, slippage_pct=0, dp_per_sell=0),
             0.0, taxed=True)
    leap = pd.Timestamp("2020-02-29")
    b.buy(N, 1000.0, leap, 10.0)                          # 100 units at 10
    b.buy(N, 1000.0, pd.Timestamp("2020-06-01"), 20.0)    # 50 units at 20
    b.sell(N, 750.0, pd.Timestamp("2021-02-28"), 15.0)    # FIFO, 50 units: not after 28 Feb -> +250 ST
    b.sell(N, 750.0, pd.Timestamp("2021-03-01"), 15.0)    # the other 50 of the first lot: +250 LT
    assert plus_12_months(leap) == pd.Timestamp("2021-02-28")
    assert math.isclose(b.realised[2020][("EQ", "ST")], 250.0)
    assert math.isclose(b.realised[2020][("EQ", "LT")], 250.0)
    b.sell(N, 50 * 15.0, pd.Timestamp("2021-08-01"), 15.0)  # second lot, 14 months: -250 LT
    b.buy(G, 1000.0, pd.Timestamp("2021-04-10"), 10.0)
    b.sell(G, 1100.0, pd.Timestamp("2021-09-10"), 11.0)    # +100 AU-ST
    assert math.isclose(b.close_fy(2020), 250.0 * 0.208 + 250.0 * 0.13)
    # FY2021: LT loss 250 offsets only LT buckets, so AU-ST +100 is taxed and 250 carries forward.
    assert math.isclose(b.close_fy(2021), 100.0 * 0.312)
    assert b.carry == [[2021, "LT", 250.0]]
    for fy in range(2022, 2030):
        assert b.close_fy(fy) == 0.0, "no refunds"
    assert b.lapsed == 250.0 and b.carry == []


def test_st_loss_offsets_in_the_registered_order():
    b = Book(GROWW, 0.0, taxed=True)
    b.realised[2015][("EQ", "ST")] = -100.0
    b.realised[2015][("AU", "ST")] = 60.0
    b.realised[2015][("EQ", "LT")] = 80.0
    # 100 of ST loss: 60 against AU-ST, then 40 against EQ-LT (AU-LT and EQ-ST are empty).
    assert math.isclose(b.close_fy(2015), 40.0 * 0.13)


def test_mix_checks_are_chained_on_anniversaries():
    p = _panel()
    sch = schedule(p)
    ch = mix_checks(p, sch)
    prev = sch.X[0]
    for K, Y in ch:
        assert K > plus_12_months(prev) and Y > K and Y < sch.X[-1]
        prev = Y
    assert 15 <= len(ch) <= 16


def test_a_month_end_with_no_genuine_row_stops_the_test():
    p = _panel()
    g = p.genuine.copy()
    me = p.sessions[(p.sessions > pd.Timestamp("2015-06-20")) & (p.sessions <= pd.Timestamp("2015-07-08"))]
    g.loc[me, G] = False
    try:
        schedule(Panel(p.sessions, p.close, g))
    except NotEvaluable:
        return
    raise AssertionError("an execution more than five sessions late was accepted")


def _bars(dates, closes, vols=None):
    idx = pd.DatetimeIndex(pd.to_datetime(dates), name="date")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                         "volume": np.ones(len(c)) if vols is None else np.asarray(vols, float)}, index=idx)


def test_calendar_tests_placeholders_on_the_frame_as_fetched():
    from research.round2 import calendar
    # A Saturday special session, then a Monday holiday placeholder copying it.
    n = _bars(["2024-01-18", "2024-01-19", "2024-01-20", "2024-01-22", "2024-01-23"],
              [100.0, 101.0, 100.7, 100.7, 102.0], [5, 5, 5, 0, 5])
    sessions, info = calendar(n)
    assert pd.Timestamp("2024-01-22") not in sessions and pd.Timestamp("2024-01-20") not in sessions
    assert info["dropped_placeholder"] == ["2024-01-22"] and info["dropped_weekend"] == 1


def test_duplicate_dates_keep_the_last_row_in_file_order():
    import tempfile
    from research.round2 import read_raw
    with tempfile.TemporaryDirectory() as tmp:
        f = __import__("pathlib").Path(tmp) / "X.csv"
        f.write_text("date,open,high,low,close,volume\n2024-01-02,1,1,1,10,5\n2024-01-03,1,1,1,11,5\n"
                     "2024-01-03,1,1,1,12,7\n")
        df, dropped = read_raw("X", __import__("pathlib").Path(tmp))
    assert list(df["close"]) == [10.0, 12.0] and dropped[0]["close"] == 11.0


def test_two_splits_close_together_leave_the_first_unconfirmed_and_block():
    from research.round2 import block_breaches, corporate_actions
    days = pd.bdate_range("2020-01-01", periods=12)
    close = pd.Series([100.0] * 4 + [50.0, 50.0] + [5.0] * 6, index=days)   # 1/2, then 1/10 two rows later
    adj, events = corporate_actions(close, None)
    assert [e["confirmed"] for e in events] == [False, True]
    assert abs(adj.iloc[4] / adj.iloc[3] - 0.5) < 1e-12, "the unconfirmed step stays as recorded"
    p = Panel(days, pd.DataFrame({N: adj, G: 100.0}), pd.DataFrame(True, index=days, columns=list(ETFS)))
    assert any(b["etf"] == N and b["ratio"] < 0.8 for b in block_breaches(p, None))


def test_a_full_exit_leaves_no_dust():
    b = Book(GROWW, 0.0, taxed=True)
    d = pd.Timestamp("2020-01-01")
    b.buy(G, 1000.0, d, 3.0)
    b.sell(G, b.units[G] * 3.0 * (1 + 1e-15), pd.Timestamp("2021-06-01"), 3.0)
    assert b.units[G] == 0.0 and b.lots[G] == []


def test_a_sub_paisa_shortfall_sells_nothing():
    p = _panel(5)
    sch = schedule(p)
    a = simulate("BH-N", p, sch, taxed=True)
    assert a.book.orders == 2, "BH-N buys once and sells once at liquidation"


def test_no_switch_at_all_is_a_recorded_fail_not_a_crash():
    from research.strategy_t import evaluate
    days = pd.bdate_range("2009-01-01", "2026-10-09")
    rising = pd.DataFrame({N: np.linspace(100, 400, len(days)), G: np.linspace(100, 300, len(days))}, index=days)
    p = Panel(days, rising, pd.DataFrame(True, index=days, columns=list(ETFS)))
    out = evaluate(p, schedule(p))
    assert out["P"]["t"] == 0.0 and not out["P"]["passed"]
    assert next(c for c in out["criteria"] if c["id"] == "G2")["passed"] is False
    assert out["verdict"].startswith("FAIL")


def test_drawdown_length_counts_sessions_that_fell():
    from research.strategy_t import max_drawdown
    d = pd.bdate_range("2020-01-01", periods=6)
    up = pd.Series([1.0, 1.0, 1.1, 1.2, 1.3, 1.4], index=d.insert(0, d[0])[:6])
    assert max_drawdown(up)[1] == 0, "a series that never falls has no drawdown"
    v = pd.Series([1.0, 0.99, 1.0, 0.9, 0.95, 1.01], index=pd.DatetimeIndex([d[0], d[0], d[1], d[2], d[3], d[4]]))
    dd, n = max_drawdown(v)
    # X_1's two points are one session; back at the peak on d1 ends that stretch (1 session),
    # and the new peak on d1 is regained on d4: 3 sessions.
    assert abs(dd - (-0.1)) < 1e-12 and n == 3


def test_staleness_ignores_m201_and_not_testable_is_its_own_label():
    from research.round2 import NotTestable, check_testable, stale_month_ends
    p = _panel(2)
    sch = schedule(p)
    g = p.genuine.copy()
    last5 = p.sessions[(p.sessions > pd.Timestamp("2026-09-23")) & (p.sessions <= sch.M[-1])]
    g.loc[last5, G] = False
    q = Panel(p.sessions, p.close, g)
    assert stale_month_ends(q, sch) == [], "M_201 fixes X_201 only; no signal uses it"
    raw = {k: p.close[[k]].rename(columns={k: "close"}).assign(volume=1.0) for k in ETFS}
    short = {k: v[v.index < pd.Timestamp("2026-10-01")] for k, v in raw.items()}
    nsei = raw[N]
    try:
        check_testable(short, nsei, p)
    except NotTestable:
        return
    raise AssertionError("a series ending before 2026-10-01 was testable")


def test_a_failed_fetch_freezes_nothing():
    import tempfile
    import run_round2 as rr
    import research.round2 as r2m
    old = (r2m.CACHE_R2, r2m.MANIFEST_R2, rr.STAGING, rr.data.fetch)
    with tempfile.TemporaryDirectory() as tmp:
        t = __import__("pathlib").Path(tmp)
        r2m.CACHE_R2, r2m.MANIFEST_R2, rr.STAGING = t / "cache_r2", t / "MANIFEST_r2.sha256", t / ".staging"

        def half(symbols, lookback, cache):
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "NIFTYBEES.NS.csv").write_text("date,close\n")
            return {"GOLDBEES.NS": "unavailable"}
        rr.data.fetch = half
        try:
            assert rr.fetch() == 2
            assert not r2m.CACHE_R2.exists() and not r2m.MANIFEST_R2.exists() and not rr.STAGING.exists()
        finally:
            r2m.CACHE_R2, r2m.MANIFEST_R2, rr.STAGING, rr.data.fetch = old


def test_fy_of():
    assert fy_of(pd.Timestamp("2026-03-31")) == 2025 and fy_of(pd.Timestamp("2026-04-01")) == 2026


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
