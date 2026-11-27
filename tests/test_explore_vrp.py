"""
research/explore_vrp.py's measures on synthetic minutes: the index series, implied volatility at the
entry minute (a fraction or a percent), realized volatility with the overnight gaps, and the thirds.
Run: PYTHONPATH=. python tests/test_explore_vrp.py
"""

from __future__ import annotations

import math

from research import explore_vrp as ev
from trading.fno import dhan_data as dd


def _day(start: float, step: float, iv: float | None = 15.0):
    """375 minutes; the index multiplies by exp(step) every minute."""
    out, s = {}, start
    for i in range(375):
        hm = f"{9 + (15 + i) // 60:02d}:{(15 + i) % 60:02d}"
        b = dd.Bar(17000.0, 100, 100, 100, 100, 1, 1, iv, s)
        out[hm] = {(0, "CALL"): b, (0, "PUT"): b}
        s *= math.exp(step)
    return out


def test_series_iv_and_realized():
    d = _day(17000.0, 0.0002)
    s = ev.spots(d)
    assert len(s) == 375 and s[0] == 17000.0 and "09:19" in d and "15:29" in d
    assert ev.iv_at(d) == 15.0 and ev.iv_at(_day(17000.0, 0.0, iv=0.15)) == 15.0
    assert ev.iv_at(_day(17000.0, 0.0, iv=None)) is None
    # two sessions of 374 minute moves of 0.0002 and one overnight gap of zero (the next opens at the close)
    a = s
    b = ev.spots(_day(a[-1], 0.0002))
    want = math.sqrt((2 * 374 * 0.0002 ** 2) * 252 / 2) * 100
    assert math.isclose(ev.realized([a, b]), want, rel_tol=1e-6)
    gap = ev.spots(_day(a[-1] * math.exp(0.01), 0.0002))
    want_gap = math.sqrt((2 * 374 * 0.0002 ** 2 + 0.01 ** 2) * 252 / 2) * 100
    assert math.isclose(ev.realized([a, gap]), want_gap, rel_tol=1e-6)
    assert ev.realized([a, a[:100]]) is None and ev.realized([]) is None


def test_thirds_cover_every_reading_in_order():
    rows = [{"x": float(v), "net": v, "R": 0.0} for v in range(10)] + [{"x": None, "net": 0, "R": 0}]
    parts = ev.thirds(rows, "x")
    assert [len(p) for _, p in parts] == [3, 3, 4]
    assert [r["x"] for _, p in parts for r in p] == [float(v) for v in range(10)]
    assert parts[0][0].startswith("low third (0.00 to 2.00)")
    assert "10 days" in ev.block([r for _, p in parts for r in p])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
