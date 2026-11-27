"""
Reading Dhan's stored minutes back (trading/fno/dhan_data.py) and the straddle exploration replay
(research/explore_straddle.py), on synthetic replies laid out as tools/dhan_download.py stores them.
Also: the held-back years cannot be read without a bound registration, and every such read is logged.
Run: PYTHONPATH=. python tests/test_dhan_data.py
"""

from __future__ import annotations

import gzip
import json
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

from market.charges import FNO_ZERODHA, option_order_costs
from market.config import IST
from research import explore_straddle as es
from trading.fno import dhan_data as dd

DAY = date(2022, 3, 2)                                            # a Wednesday, inside the exploration years


def _write(root: Path, day: date, prices, spot_path):
    """prices(offset, right, minute_index) -> close. ATM moves 50 points at minute 200 (spot rises)."""
    nxt = day + timedelta(days=1)
    for off in dd.OFFSETS:
        for right in ("CALL", "PUT"):
            ts, k, c, sp = [], [], [], []
            for d in (day, nxt):                                  # Dhan's toDate came back inclusive
                t0 = datetime.combine(d, time(9, 15), IST)
                for i in range(385):                              # to 15:39, as Dhan sends
                    atm = 17000 if i < 200 else 17050
                    ts.append(int((t0 + timedelta(minutes=i)).timestamp()))
                    k.append(float(atm + 50 * off))
                    c.append(prices(off, right, i, atm))
                    sp.append(spot_path(i))
            s = {"timestamp": ts, "strike": k, "open": c, "high": c, "low": c, "close": c, "volume": [1] * len(ts),
                 "oi": [1] * len(ts), "iv": [15.0] * len(ts), "spot": sp}
            p = root / "code1" / dd.series_name(off, right) / f"{day.isoformat()}.json.gz"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(gzip.compress(json.dumps({"data": {"ce": s if right == "CALL" else None,
                                                             "pe": s if right == "PUT" else None}}).encode()))


def test_holdout_is_refused_without_a_registration_and_logged_with_one():
    root = Path(tempfile.mkdtemp())
    dd.AUDIT = root / "audit.log"
    for d in (date(2024, 11, 4), date(2026, 10, 19)):
        try:
            dd.load_day(d, root=root)
            raise AssertionError(f"{d} was read without a registration")
        except dd.Holdout:
            pass
    assert dd.load_day(date(2024, 11, 4), root=root, registration_sha256="ab" * 32) == {}
    log = [json.loads(x) for x in dd.AUDIT.read_text().splitlines()]
    assert log[0]["day"] == "2024-11-04" and log[0]["caller"].startswith("test_dhan_data.py")
    try:
        dd.load_day(date(2026, 10, 19), root=root, registration_sha256="ab" * 32)
        raise AssertionError("forward data was read as history")
    except dd.Holdout:
        pass


def test_a_fixed_strike_is_followed_across_atm_offsets():
    root = Path(tempfile.mkdtemp())
    _write(root, DAY, lambda off, r, i, atm: 100.0 + off, lambda i: 17000.0)
    bars = dd.load_day(DAY, root=root)
    assert len(bars) == 375 and min(bars) == "09:15" and max(bars) == "15:29"     # this day only, market hours
    assert dd.at_strike(bars, "09:30", 17000.0, "CALL").close == 100.0            # offset 0 before the move
    assert dd.at_strike(bars, "13:00", 17000.0, "CALL").close == 99.0             # offset -1 after it
    assert dd.sessions(root) == [DAY]


def test_the_straddle_replay_stops_and_times_out_as_the_explorer_does():
    calm = Path(tempfile.mkdtemp())
    _write(calm, DAY, lambda off, r, i, atm: 100.0 if atm + 50 * off == 17000 else 80.0, lambda i: 17000.0)
    t = es.replay_day(dd.load_day(DAY, root=calm), es.half_spread(0.002))
    sell = es.tick(100.0 - 0.2, False)                                            # 99.80
    buy = es.tick(100.0 + 0.2, True)                                              # 100.20
    cin = 2 * option_order_costs(sell, 65, "SELL", FNO_ZERODHA).total
    cout = 2 * option_order_costs(buy, 65, "BUY", FNO_ZERODHA).total
    assert t["reason"] == "time" and t["exit"] == "15:14" and t["strike"] == 17000.0
    assert abs(t["net"] - ((2 * sell - 2 * buy) * 65 - cin - cout)) < 0.02
    assert abs(t["credit"] - 2 * sell * 65) < 0.01 and abs(t["stop"] - 0.3 * t["credit"]) < 0.01
    assert abs(t["risk"] - (t["stop"] * 1.5 + cin + 2 * option_order_costs(sell, 65, "BUY", FNO_ZERODHA).total)) < 0.02

    run = Path(tempfile.mkdtemp())                                                # the call doubles by noon
    _write(run, DAY, lambda off, r, i, atm: (100.0 + (i * 0.5 if r == "CALL" else 0)) if atm + 50 * off == 17000
           else 80.0, lambda i: 17000.0 + i)
    t2 = es.replay_day(dd.load_day(DAY, root=run), es.half_spread(0.002))
    assert t2["reason"] == "stop" and t2["net"] <= -t2["stop"] and "09:20" < t2["exit"] < "15:14"
    assert t2["carried_minutes"] == 0                                             # the strike was found all day


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
