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


if __name__ == "__main__":
    test_days_that_closed_differently_are_separated_from_the_cost_of_the_spread()
    print("ok")
