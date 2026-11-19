"""research/trace_expiry_more.py on a made-up saved result. Run: PYTHONPATH=. python tests/test_trace_expiry_more.py"""

from __future__ import annotations

from research import trace_expiry_more as tr


def _t(day, net, reason="time", exit_="15:14", carried=0):
    return {"day": day, "net": net, "reason": reason, "exit": exit_, "carried": carried}


def test_days_that_closed_differently_are_separated_from_the_cost_of_the_spread():
    saved = {"X entry 09:20 half-spread 0.2%": [_t("2024-01-04", 1000.0), _t("2024-01-11", -9000.0, "stop", "13:02", 2)],
             "X entry 09:20 half-spread 0.5%": [_t("2024-01-04", 900.0), _t("2024-01-11", -3000.0, "stop", "10:41", 40)]}
    rows = tr.compare(saved, "X", "09:20")
    assert [r["same_close"] for r in rows] == [True, False]
    assert rows[0]["diff"] == -100.0 and rows[1]["diff"] == 6000.0
    assert "stop at 10:41" in tr.side(rows[1]["b"]) and "40 carried" in tr.side(rows[1]["b"])
    assert tr.side(None) == "no trade" and tr.compare(saved, "Y", "09:20") == []


def test_the_minutes_around_a_stop_show_the_index_beside_the_option():
    import gzip
    import json
    import tempfile
    from datetime import date, datetime, time, timedelta
    from pathlib import Path

    from market.config import IST
    from trading.fno import dhan_data as dd
    day = date(2024, 4, 8)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for right in ("CALL", "PUT"):
            t0 = datetime.combine(day, time(9, 15), IST)
            ts = [int((t0 + timedelta(minutes=i)).timestamp()) for i in range(375)]
            close = [30.0] * 375
            if right == "CALL":
                close[253] = 180.0                                # 13:28: the call alone jumps; the index does not
            s = {"timestamp": ts, "strike": [10000.0] * 375, "open": close, "high": close, "low": close,
                 "close": close, "volume": [1] * 375, "oi": [1] * 375, "iv": [20.0] * 375, "spot": [10000.0] * 375}
            path = root / "code1" / dd.series_name(0, right) / f"{day.isoformat()}.json.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(json.dumps({"data": {"ce" if right == "CALL" else "pe": s}}).encode()))
        rows = [{"day": day.isoformat(), "a": _t(day.isoformat(), -19825.0, "stop", "13:28"),
                 "b": _t(day.isoformat(), -2291.0, "stop", "13:27")}]
        out = tr.minutes_around("X", "09:20", day.isoformat(), rows, root=root)
    assert "strike 10000" in out[0] and "13:27, 13:28" in out[0]
    line = next(x for x in out if x.strip().startswith("13:28"))
    assert "10000.0" in line and "180.00" in line and "210.00" in line, line
    assert out[2].strip().startswith("13:19") and out[-1].strip().startswith("13:32")
    assert "not one of the saved" in tr.minutes_around("X", "09:20", "2024-04-09", rows, root=root)[0]


if __name__ == "__main__":
    test_days_that_closed_differently_are_separated_from_the_cost_of_the_spread()
    test_the_minutes_around_a_stop_show_the_index_beside_the_option()
    print("ok")
