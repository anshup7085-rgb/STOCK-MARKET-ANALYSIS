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
