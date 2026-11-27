"""
research/perps_maker.py on made-up minutes: a limit order that is only touched does not fill, one traded
through does; the stop counts from the fill minute and the target only after it; a target exit pays the
maker fee and a stop the taker fee plus slippage; an unfilled order frees the desk at the next decision;
a random walk ends DEAD and freezes no model.
Run: PYTHONPATH=. python tests/test_perps_maker.py
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import numpy as np

from research import perps_lab as pl
from research import perps_maker as pm
from tests.test_perps_lab import _fake_files

MIN = 60_000


def _g(rows):
    """rows: (open, high, low, close) per minute."""
    a = np.array(rows, dtype=float)
    return {"t": np.arange(len(a), dtype=np.int64) * MIN, "open": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3]}


def _run(rows, side=1, h=4, wait=2):
    g = _g(rows)
    sig = np.full(len(rows), 0.01)                  # u = 0.01 x sqrt(4) = 0.02: long target 103, stop 98
    return {k: v[0] for k, v in pm.maker_trades(g, sig, np.array([0]), h, side, wait=wait).items()}


FLAT = (100, 100.5, 99.9, 100)


def test_a_touch_is_not_a_fill_a_trade_through_is():
    touch = [FLAT, (100, 100.5, 100.0, 100), (100, 100.5, 100.0, 100)] + [FLAT] * 6
    assert _run(touch)["status"] == pm.NOFILL
    r = _run(touch)
    assert r["dur"] == 2                            # free again when the order is cancelled
    through = [FLAT, (100, 100.5, 99.9, 100)] + [(100, 100.5, 99.9, 100)] * 7
    r = _run(through)
    assert r["status"] == pm.FILLED and r["cls"] == pl.TO and r["fill_at"] == 1


def test_stop_from_the_fill_minute_target_only_after():
    # the fill minute itself reaches both 103.5 and 97.5: the stop counts, the target does not
    both = [FLAT, (100, 103.5, 97.5, 100)] + [FLAT] * 7
    r = _run(both)
    assert r["status"] == pm.FILLED and r["cls"] == pl.SL and abs(r["ret"] + 1) < 1e-9
    assert abs(r["fees"] - (pm.MAKER + pm.TAKER_SIDE)) < 1e-12
    # the target in the fill minute only, then nothing: a time-out, not a win
    early = [FLAT, (100, 103.5, 99.5, 100)] + [FLAT] * 7
    assert _run(early)["cls"] == pl.TO
    # the target traded through the minute after the fill: a win at the maker fee both ways
    win = [FLAT, (100, 100.2, 99.5, 100), (100, 103.2, 99.8, 103)] + [FLAT] * 6
    r = _run(win)
    assert r["cls"] == pl.TP and abs(r["ret"] - 1.5) < 1e-9 and abs(r["fees"] - 2 * pm.MAKER) < 1e-12
    assert r["dur"] == 2
    # a target only touched (high exactly 103) is not taken
    touch_tp = [FLAT, (100, 100.2, 99.5, 100), (100, 103.0, 99.8, 102.9)] + [FLAT] * 6
    assert _run(touch_tp)["cls"] == pl.TO


def test_short_side_and_a_gapped_stop():
    # a sell at 100 fills when a high trades above it; the next minute opens at 103: stop 102, filled at 103
    rows = [FLAT, (100, 100.4, 99.8, 100), (103, 103.5, 102.8, 103)] + [FLAT] * 6
    r = _run(rows, side=-1)
    assert r["status"] == pm.FILLED and r["cls"] == pl.SL and abs(r["ret"] + 1.5) < 1e-9


def test_a_missing_minute_makes_no_trade():
    rows = [FLAT, (100, 100.5, 99.9, 100), (np.nan, np.nan, np.nan, np.nan)] + [FLAT] * 6
    assert _run(rows)["status"] == pm.BAD


def test_a_random_walk_is_dead_and_freezes_nothing():
    saved = {k: getattr(pl, k) for k in ("DATA_FROM", "TEST_FROM", "TRAIN_MONTHS", "MIN_TRAIN_MONTHS", "MODEL")}
    old_h = pm.H
    try:
        pl.DATA_FROM, pl.TEST_FROM = date(2024, 8, 1), date(2024, 10, 1)
        pl.TRAIN_MONTHS, pl.MIN_TRAIN_MONTHS = 2, 2
        pl.MODEL = {**pl.MODEL, "max_iter": 15}
        pm.H = 60
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for i, sym in enumerate(("BTCUSDT", "ETHUSDT")):
                _fake_files(root, sym, [date(2024, 8, 1), date(2024, 9, 1), date(2024, 10, 1)], seed=20 + i)
            res = pm.run(["ETHUSDT", "BTCUSDT"], root=root, out=root / "m.json", model_path=root / "m.pkl",
                         log=lambda *a: None)
            assert res["verdict"] == "DEAD" and not (root / "m.pkl").exists()
            assert 0 <= res["desk"]["fill_rate"] <= 1
    finally:
        for k, v in saved.items():
            setattr(pl, k, v)
        pm.H = old_h


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
