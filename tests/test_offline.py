"""
Offline tests. No network, no broker, no API key.

The important one is test_no_lookahead: it proves indicators computed on a
truncated series match those computed on the full series at the same bar.
If that ever fails, every backtest the system produces is worthless.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from market.backtest import _spearman, run_backtest, simulate
from market.charges import DELIVERY, INTRADAY, round_trip_costs, trade_economics
from market.config import Thresholds
from market.indicators import atr, build_snapshot, ema, pivot_highs, rsi
from market.levels import build_levels, size_position
from market.score import build_score, regime_adjustment
from market.universe import classify_regime, liquidity_check

TH = Thresholds()


def synth(n=300, start=100.0, drift=0.0008, vol=0.015, seed=7, volume=1_500_000):
    """Geometric random walk with OHLC derived consistently from the close."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = start * np.exp(np.cumsum(rets))
    idx = pd.bdate_range(end=pd.Timestamp("2026-08-10"), periods=n)
    open_ = close * (1 + rng.normal(0, vol / 3, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    vols = rng.integers(int(volume * 0.5), int(volume * 1.5), n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vols},
        index=idx,
    )


# -- indicator correctness -------------------------------------------------

def test_rsi_bounded():
    df = synth()
    r = rsi(df["close"], 14).dropna()
    assert not r.empty
    assert r.min() >= 0 and r.max() <= 100, "RSI escaped [0,100]"


def test_atr_positive():
    df = synth()
    a = atr(df, 14).dropna()
    assert (a > 0).all(), "ATR must be strictly positive"


def test_ema_tracks_trend():
    df = synth(drift=0.004, vol=0.008, seed=3)   # strong uptrend
    e20 = ema(df["close"], 20).iloc[-1]
    e50 = ema(df["close"], 50).iloc[-1]
    assert e20 > e50, "in a sustained uptrend the fast EMA should lead"


def test_no_lookahead():
    """Truncated-series values must equal full-series values at the same bar."""
    df = synth(n=300)
    cut = 250
    for name, full, part in [
        ("ema20", ema(df["close"], 20), ema(df["close"].iloc[:cut], 20)),
        ("rsi14", rsi(df["close"], 14), rsi(df["close"].iloc[:cut], 14)),
        ("atr14", atr(df, 14), atr(df.iloc[:cut], 14)),
    ]:
        a, b = float(full.iloc[cut - 1]), float(part.iloc[-1])
        assert abs(a - b) < 1e-9, f"{name} leaks future information: {a} vs {b}"


# -- levels and sizing -----------------------------------------------------

def test_long_levels_ordered():
    snap = build_snapshot(synth(), "TEST")
    lv = build_levels(snap, TH, "long")
    assert lv is not None
    assert lv.stop < lv.reference_price < lv.target1 < lv.target2 < lv.stretch
    assert lv.risk_per_share > 0


def test_short_levels_ordered():
    snap = build_snapshot(synth(), "TEST")
    lv = build_levels(snap, TH, "short")
    assert lv is not None
    assert lv.stop > lv.reference_price > lv.target1 > lv.target2 > lv.stretch


def test_r_multiple_fallback_when_chart_offers_nothing():
    """
    A name at an all-time high has no overhead structure to measure against, so
    targets fall back to R-multiples and T2 lands at exactly target2_r.

    This replaces an earlier test that asserted T2 was ALWAYS 3.0R. That
    assertion held only because targets were defined as multiples of the stop —
    it was the bug, written down as a guarantee.
    """
    snap = build_snapshot(synth(), "TEST")
    snap.resistance = []
    lv = build_levels(snap, TH, "long")
    assert lv.target_basis == "r-multiple"
    implied = (lv.target2 - lv.reference_price) / lv.risk_per_share
    assert abs(implied - TH.target2_r) < 0.02, f"fallback T2 is not {TH.target2_r}R: {implied}"


def test_sizing_without_equity_gives_formula_not_number():
    """RISK_POLICY.md: never invent a rupee amount."""
    snap = build_snapshot(synth(), "TEST")
    lv = build_levels(snap, TH, "long")
    ps = size_position(lv, None, 1.0, 20.0)
    assert ps.quantity is None and ps.max_loss is None
    assert "formula" in ps.formula.lower() or "=" in ps.formula


def test_sizing_respects_risk_budget():
    snap = build_snapshot(synth(), "TEST")
    lv = build_levels(snap, TH, "long")
    equity = 1_000_000.0
    ps = size_position(lv, equity, 1.0, 100.0)   # cap disabled to isolate risk maths
    assert ps.max_loss <= equity * 0.01 + lv.risk_per_share


def test_concentration_cap_bites():
    snap = build_snapshot(synth(), "TEST")
    lv = build_levels(snap, TH, "long")
    ps = size_position(lv, 1_000_000.0, 50.0, 20.0)   # absurd risk% forces the cap
    assert ps.pct_of_equity <= 20.01
    assert any("cap" in w.lower() for w in ps.warnings)


# -- gates and scoring -----------------------------------------------------

def test_illiquid_rejected():
    df = synth(volume=500, start=8.0)            # thin and cheap
    snap = build_snapshot(df, "THIN")
    assert not liquidity_check(snap, TH).passed


def test_liquid_accepted():
    df = synth(volume=2_000_000, start=1500.0)
    snap = build_snapshot(df, "FAT")
    assert liquidity_check(snap, TH).passed


def test_missing_components_renormalise_not_penalise():
    """A missing feed must not look like a bad signal."""
    snap = build_snapshot(synth(drift=0.003, vol=0.01, seed=11), "TEST")
    lv = build_levels(snap, TH, "long")

    thin = build_score(snap, regime="uptrend", reward_risk=TH.target2_r)
    rich = build_score(
        snap, regime="uptrend", reward_risk=TH.target2_r,
        has_catalyst=True, catalyst_note="results", catalyst_verified=True,
    )
    assert thin.coverage < rich.coverage
    assert "catalyst" in thin.missing
    assert 0 <= thin.raw <= 100
    assert thin.confidence.startswith(("low", "moderate")), (
        "thin coverage must cap confidence"
    )


def test_score_bounds_and_explainability():
    snap = build_snapshot(synth(), "TEST")
    sc = build_score(snap, regime="uptrend", reward_risk=3.0)
    assert 0 <= sc.raw <= 100
    assert "Score" in sc.explain() and "coverage" in sc.explain()


def test_regime_classifier_directions():
    up = classify_regime(synth(drift=0.004, vol=0.007, seed=5))
    down = classify_regime(synth(drift=-0.004, vol=0.007, seed=5))
    assert up.regime in ("uptrend", "volatile", "transitioning")
    assert down.regime in ("downtrend", "volatile", "transitioning")
    assert up.evidence, "regime call must carry its evidence"


def test_regime_unknown_on_short_history():
    assert classify_regime(synth(n=20)).regime == "unknown"


# -- structure detection ---------------------------------------------------

def test_pivots_are_confirmed_not_predicted():
    """
    Pivots from a truncated series must be a PREFIX of pivots from the full
    series. If truncation ever changes an earlier pivot, the detector is reading
    bars that had not printed yet.
    """
    df = synth(n=300)
    full = pivot_highs(df, 3, 3)
    part = pivot_highs(df.iloc[:250], 3, 3)
    assert part == full[: len(part)], "pivot detection leaks future bars"


def test_pivots_lag_the_last_bar():
    """The final `right` bars cannot have produced a confirmed pivot yet."""
    df = synth(n=120)
    highs = df["high"].to_numpy()
    piv = set(pivot_highs(df, 3, 3))
    assert not (piv & set(highs[-3:])), "a pivot was confirmed before its window closed"


def test_targets_vary_with_structure():
    """
    The bug this project shipped with: R:R identical for every name because
    targets were multiples of the stop. Different charts must now give different
    ratios.
    """
    ratios = {
        build_levels(build_snapshot(synth(seed=s, drift=d), f"S{s}"), TH, "long").reward_risk
        for s, d in [(1, 0.001), (2, -0.001), (3, 0.004), (4, 0.0), (5, 0.002)]
    }
    assert len(ratios) > 1, f"reward:risk is still constant across charts: {ratios}"


def test_reward_risk_is_consistent_with_levels():
    """The reported ratio must equal the levels it was derived from."""
    snap = build_snapshot(synth(), "TEST")
    lv = build_levels(snap, TH, "long")
    implied = (lv.target2 - lv.reference_price) / lv.risk_per_share
    assert abs(implied - lv.reward_risk) < 0.02, "R:R disagrees with its own targets"
    assert lv.target_basis in ("structure", "r-multiple")


# -- regime is a scan-level deduction, not a ranking component -------------

def test_regime_does_not_rank_but_does_deduct():
    snap = build_snapshot(synth(drift=0.003, seed=11), "TEST")
    lv = build_levels(snap, TH, "long")
    up = build_score(snap, regime="uptrend", reward_risk=lv.reward_risk)
    down = build_score(snap, regime="downtrend", reward_risk=lv.reward_risk)

    assert "market regime" not in [c.name for c in up.components], (
        "regime is back in the per-name components, where it cannot rank"
    )
    assert up.coverage == down.coverage, "regime must not move coverage"
    assert down.raw < up.raw, "a long in a downtrend must score lower"


def test_regime_adjustment_mirrors_for_shorts():
    long_pen, _ = regime_adjustment("downtrend", "long")
    short_pen, _ = regime_adjustment("downtrend", "short")
    assert long_pen > short_pen, "a downtrend should hurt longs, not shorts"


# -- backtest harness ------------------------------------------------------

def test_simulate_takes_the_stop_when_a_bar_spans_both():
    """Ambiguous bars must resolve against the trade, never in its favour."""
    df = pd.DataFrame(
        {"open": [100, 100], "high": [100, 120], "low": [100, 80],
         "close": [100, 110], "volume": [1, 1]},
        index=pd.bdate_range(end=pd.Timestamp("2026-08-10"), periods=2),
    )
    outcome, r, _ = simulate(df, 0, entry=100, stop=90, target=110,
                             horizon=1, direction="long")
    assert outcome == "stop" and r == -1.0


def test_simulate_gap_fills_worse_than_the_stop():
    """RISK_POLICY.md: a stop is a trigger, not a guaranteed price."""
    df = pd.DataFrame(
        {"open": [100, 80], "high": [100, 85], "low": [100, 78],
         "close": [100, 82], "volume": [1, 1]},
        index=pd.bdate_range(end=pd.Timestamp("2026-08-10"), periods=2),
    )
    outcome, r, _ = simulate(df, 0, entry=100, stop=90, target=130,
                             horizon=1, direction="long")
    assert outcome == "stop" and r < -1.0, f"gap should exceed 1R loss, got {r}"


def test_backtest_runs_and_stays_causal():
    # Prices high enough to clear the Rs25cr turnover floor — the default synth
    # is deliberately thin and the liquidity gate rejects it, as it should.
    bars = {
        f"S{i}": synth(n=320, seed=i, start=1500.0, volume=2_000_000)
        for i in range(4)
    }
    res = run_backtest(bars, TH, horizon=20, step=10, warmup=200)
    assert res.n > 0, "harness produced no trades"
    assert all(-8 < t.r_multiple < 20 for t in res.trades), "implausible R multiple"
    assert all(t.outcome in ("target", "stop", "timeout") for t in res.trades)


def test_spearman_detects_direction():
    assert _spearman([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) > 0.99
    assert _spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) < -0.99


# -- charges and net economics ---------------------------------------------

def test_delivery_stt_is_both_sides_intraday_is_sell_only():
    """The single biggest cost line, and it is not symmetric across segments."""
    d = round_trip_costs(1000.0, 1100.0, 100, DELIVERY)
    i = round_trip_costs(1000.0, 1100.0, 100, INTRADAY)
    # delivery: 0.1% of 100k buy + 0.1% of 110k sell
    assert abs(d.stt - (100_000 * 0.001 + 110_000 * 0.001)) < 0.01
    # intraday: 0.025% of the sell leg only
    assert abs(i.stt - (110_000 * 0.00025)) < 0.01
    assert i.stt < d.stt


def test_gst_applies_to_brokerage_not_to_stt():
    """GST is on brokerage + exchange + SEBI. Charging it on STT is a classic slip."""
    c = round_trip_costs(1000.0, 1100.0, 100, INTRADAY)
    expected_gst = (c.brokerage + c.exchange_txn + c.sebi) * 0.18
    assert abs(c.gst - expected_gst) < 0.01


def test_brokerage_cap_bites_on_a_large_order():
    small = round_trip_costs(100.0, 110.0, 100, INTRADAY)      # 10k turnover
    large = round_trip_costs(10_000.0, 11_000.0, 100, INTRADAY)  # 1,000k turnover
    assert small.brokerage < 40.0
    assert abs(large.brokerage - 40.0) < 0.01, "Rs20/order cap should bind on both legs"


def test_costs_make_net_worse_than_gross():
    e = trade_economics(1000.0, 950.0, 1150.0, 100, segment="delivery")
    assert e.net_profit_at_target < e.gross_profit_at_target
    assert abs(e.net_loss_at_stop) > abs(e.gross_loss_at_stop)
    assert e.net_reward_risk < e.gross_reward_risk


def test_breakeven_win_rate_matches_its_own_definition():
    """p = |L| / (W + |L|) — the number the whole plan hangs on."""
    e = trade_economics(1000.0, 950.0, 1150.0, 100, segment="delivery")
    p = e.breakeven_win_rate_pct / 100.0
    ev = p * e.net_profit_at_target - (1 - p) * abs(e.net_loss_at_stop)
    # The published rate is rounded to 0.1%, which is worth +/-0.0005 of the
    # win/loss spread in EV. Tolerance tracks the position, not a flat rupee.
    tol = 0.001 * (e.net_profit_at_target + abs(e.net_loss_at_stop))
    assert abs(ev) < tol, f"at the breakeven win rate EV must be ~0, got {ev}"


def test_expected_value_is_zero_at_breakeven_and_rises_above():
    e = trade_economics(1000.0, 950.0, 1150.0, 100, segment="delivery")
    be = e.breakeven_win_rate_pct
    tol = 0.001 * (e.net_profit_at_target + abs(e.net_loss_at_stop))
    assert abs(e.expected_value_at(be)) < tol
    assert e.expected_value_at(be + 10) > 0
    assert e.expected_value_at(be - 10) < 0


def test_tiny_position_is_flagged_as_eaten_by_costs():
    """Fixed charges dominate a small position — the model must say so."""
    e = trade_economics(100.0, 95.0, 115.0, 5, segment="delivery")
    assert any("too small" in w for w in e.warnings), e.warnings


def test_unverified_rates_are_declared():
    """RATE ACCURACY: defaults ship unverified and must announce it."""
    e = trade_economics(1000.0, 950.0, 1150.0, 100)
    assert not e.schedule_verified
    assert any("UNVERIFIED" in w for w in e.warnings)


def test_slippage_is_applied_against_the_trade_both_ways():
    clean = trade_economics(1000.0, 950.0, 1150.0, 100, slippage_pct=0.0)
    slipped = trade_economics(1000.0, 950.0, 1150.0, 100, slippage_pct=0.5)
    assert slipped.net_profit_at_target < clean.net_profit_at_target
    assert abs(slipped.net_loss_at_stop) > abs(clean.net_loss_at_stop)


def test_short_economics_do_not_silently_invert():
    e = trade_economics(1000.0, 1050.0, 900.0, 100, segment="intraday")
    assert e.net_profit_at_target > 0, "a short to a lower target must profit"
    assert e.net_loss_at_stop < 0


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
