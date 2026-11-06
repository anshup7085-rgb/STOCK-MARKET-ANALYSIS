"""
research/explore_expiry.py on synthetic Dhan-style minutes: its 09:20 / 30% straddle agrees with the
first straddle replay, and the iron fly never loses more than its defined risk.
Run: PYTHONPATH=. python tests/test_explore_expiry.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import tests.test_dhan_data as td
from research import explore_expiry as ee
from research import explore_straddle as es
from trading.fno import dhan_data as dd


def test_the_straddle_variant_matches_the_first_replay_and_the_fly_is_bounded():
    for prices in (lambda off, r, i, atm: 100.0 if atm + 50 * off == 17000 else max(5.0, 100.0 - 10 * abs(off)),
                   lambda off, r, i, atm: (100.0 + (i * 0.8 if r == "CALL" else 0)) if atm + 50 * off == 17000
                   else max(5.0, 100.0 - 10 * abs(off) + (i * 0.3 if r == "CALL" and off > 0 else 0))):
        root = Path(tempfile.mkdtemp())
        td._write(root, td.DAY, prices, lambda i: 17000.0)
        bars = dd.load_day(td.DAY, root=root)
        h = es.half_spread(0.002)
        first = es.replay_day(bars, h)
        mine = ee.replay(bars, "09:19", 0.30, "straddle", h)
        assert abs(first["net"] - mine["net"]) < 0.02 and first["reason"] == mine["reason"], (first, mine)
        fly = ee.replay(bars, "09:19", 0.60, "iron fly +/-300", h)
        assert fly is not None and fly["net"] >= -fly["risk"] - 0.01
        late = ee.replay(bars, "12:59", 0.30, "straddle", h)
        assert late is not None and late["exit"] > "12:59"


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
