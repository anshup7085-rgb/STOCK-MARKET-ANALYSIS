"""
research/power_expiry.py: the capped betting path is V's when no cap binds and can never be ended by a
trade at its known worst; design A fails on any trade at -2R; the winged trade's loss has a floor; and
a clear edge passes far more often than a halved one. Synthetic trades and minutes only.
Run: PYTHONPATH=. python tests/test_power_expiry.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

import tests.test_dhan_data as td
from research import power_expiry as pe
from research.fno_stats import betting_path
from trading.fno import dhan_data as dd


def test_capped_path_is_vs_when_no_cap_binds_and_survives_its_worst_case():
    rng = np.random.default_rng(1)
    r = rng.normal(0.1, 0.6, 300)
    assert np.allclose(pe.capped_path(r, [10.0] * 300), betting_path(r))
    worst = [-10.0] * 5 + list(r)                           # five losses at their known floor, -10R each
    caps = [1 / (1.01 * 10.0)] * len(worst)
    assert pe.capped_path(worst, caps)[-1] > 0               # the cap keeps the wealth alive
    assert betting_path([0.4] * 10 + [-10.0])[-1] == 0.0     # without it one such loss ends the test


def _trade(net_a, net_w, risk_a=1000.0, risk_w=8000.0):
    return {"index": "NIFTY", "net_a": net_a, "risk_a": risk_a, "R_a": net_a / risk_a, "net_w": net_w,
            "risk_w": risk_w, "R_b": net_w / risk_w, "R_c": net_w / risk_a, "R_floor": -risk_w / risk_a}


def test_designs_and_the_halved_effect():
    edge = [_trade(600.0, 560.0), _trade(-300.0, -320.0)] * 50          # mean R +0.15, clear
    assert not pe.passes("A", edge + [_trade(-2500.0, -2400.0)])         # one -2.5R trade fails design A
    assert pe.passes("A", edge * 2)
    half = pe.shifted(edge, 0.5)
    assert abs(np.mean([t["R_a"] for t in half]) - 0.075) < 1e-9            # the average result halved
    rng = np.random.default_rng(3)
    full, halved = pe.chance("A", edge, rng), pe.chance("A", half, rng)
    assert full > 0.9 and halved < full, (full, halved)
    # the same edge, its bets capped by a worst case of -8R: far weaker, which is what design C measures
    assert pe.chance("C", edge, rng) < full


def test_one_day_builds_both_trades_and_the_winged_loss_has_a_floor():
    root = Path(tempfile.mkdtemp())
    # the strike sold at 13:00 is 17050 (the synthetic ATM moves there at minute 200); its put soars after 13:15
    falls = lambda off, r, i, atm: (100.0 + (2.0 * max(0, i - 240) if r == "PUT" else 0)) if atm + 50 * off == 17050 \
        else max(5.0, 100.0 - 9 * abs(off))                                      # noqa: E731
    td._write(root, td.DAY, falls, lambda i: 17000.0)
    t = pe.one_day(dd.load_day(td.DAY, root=root), 65, "NIFTY", td.DAY)
    assert t is not None and t["reason_a"] == "stop" and t["R_floor"] < -1
    assert t["R_b"] >= -1.01 and t["net_w"] >= -t["risk_w"] - 1.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
