"""
crypto/strategies.py and research/explore_crypto.py on made-up prices: a rule reads no later close, the dip
rule holds five days, buy-and-hold is taxed once on its whole gain, and a loss is never set against a gain.
Run: PYTHONPATH=. python tests/test_crypto_rules.py
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

from crypto.strategies import RULES, WARMUP, positions
from research import explore_crypto as ec


def _walk(n, seed=1):
    rng, p, out = random.Random(seed), 100.0, []
    for _ in range(n):
        p *= math.exp(rng.gauss(0.001, 0.04))
        out.append(p)
    return out


def test_rules_read_no_later_close():
    closes = _walk(700)
    for rule in RULES:
        full = positions(closes, rule)
        for cut in (300, 450, 699):
            assert positions(closes[:cut], rule) == full[:cut], rule
        assert not any(full[:WARMUP])


def test_the_dip_rule_holds_five_days():
    closes = [100.0] * 250 + [85.0] + [86.0] * 20
    pos = positions(closes, "dip")
    assert pos[250] and pos[250:255] == [True] * 5 and not pos[255]


def _days(n):
    d0 = ec.START - timedelta(days=WARMUP + 5)
    return [d0 + timedelta(days=i) for i in range(n)]


def test_hold_is_taxed_once_and_losses_are_never_set_off():
    n = WARMUP + 5 + 30
    days = _days(n)
    up = [100.0 * (1.01 ** i) for i in range(n)]
    keep = ec.START, ec.END
    ec.END = days[-1]
    try:
        r = ec.run(days, up, "hold")
        first = next(i for i, d in enumerate(days) if d >= ec.START)
        units = ec.CAPITAL * (1 - ec.FEE) / up[first]
        gain = units * up[-1] - ec.CAPITAL
        assert abs(r["tax"] - ec.TAX * gain) < 0.01 and r["trades"] == 1
        assert abs(r["end"] - (units * up[-1] * (1 - ec.FEE) - ec.TAX * gain)) < 0.01
        # one winning round trip and one losing one: tax on the win, nothing back for the loss
        prices = [100.0] * (WARMUP + 5) + [100.0, 110.0, 110.0, 99.0, 99.0, 99.0] + [99.0] * 24
        import crypto.strategies as cs
        real = cs.positions
        ec.positions = lambda closes, rule: [False] * (WARMUP + 5) + [True, False, True, False] + [False] * 26
        r2 = ec.run(days, prices, "custom")
        ec.positions = real
        assert r2["trades"] == 2 and r2["tax"] > 0
        u1 = ec.CAPITAL * (1 - ec.FEE) / 100.0
        assert abs(r2["tax"] - ec.TAX * (u1 * 110.0 - ec.CAPITAL)) < 0.01       # only the win is taxed
    finally:
        ec.START, ec.END = keep
    hold = {"end": 30_000.0, "worst_fall": -0.8}
    assert ec.label({"end": 31_000.0, "worst_fall": -0.9}, hold) == "BEATS HOLDING"
    assert ec.label({"end": 25_000.0, "worst_fall": -0.3}, hold) == "SAFER"
    assert ec.label({"end": 25_000.0, "worst_fall": -0.5}, hold) == "NO"
    assert ec.label({"end": 19_000.0, "worst_fall": -0.1}, hold) == "NO"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
