"""
Offline tests. No network, no broker, no API key.

The important one is test_no_lookahead: it proves indicators computed on a
truncated series match those computed on the full series at the same bar.
If that ever fails, every backtest the system produces is worthless.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from market.config import Thresholds
from market.indicators import atr, build_snapshot, ema, rsi
from market.levels import build_levels, size_position
from market.score import build_score
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


def test_r_multiple_math():
    snap = build_snapshot(synth(), "TEST")
    lv = build_levels(snap, TH, "long")
    implied = (lv.target2 - lv.reference_price) / lv.risk_per_share
    assert abs(implied - TH.target2_r) < 0.02, f"T2 is not {TH.target2_r}R: {implied}"


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
