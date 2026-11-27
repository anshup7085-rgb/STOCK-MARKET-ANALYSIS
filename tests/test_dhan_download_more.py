"""
tools/dhan_download_more.py on a fake Dhan: each index is checked before anything is downloaded, the
at-the-money call is asked first and a day with no weekly contract is not asked for 41 more times, a
second run resumes without a request, and the daily request budget stops it cleanly.
Run: PYTHONPATH=. python tests/test_dhan_download_more.py
"""

from __future__ import annotations

import importlib.util
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

from market.config import IST
from trading.fno import dhan_data as dd

spec = importlib.util.spec_from_file_location(
    "dhan_download_more", Path(__file__).resolve().parents[1] / "tools" / "dhan_download_more.py")
dm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dm)

NO_WEEKLY_FROM = date(2021, 10, 6)                                   # the fake index loses its weekly here


class FakeDhan:
    def __init__(self, level: float = 51000.0):
        self.calls, self.level = [], level

    def __call__(self, token, body, sleep):
        a = date.fromisoformat(body["fromDate"])
        self.calls.append((body["securityId"], body["exchangeSegment"], body["strike"], body["drvOptionType"], a))
        assert body["expiryFlag"] == "WEEK" and body["expiryCode"] == 1 and body["interval"] == "1"
        if a >= NO_WEEKLY_FROM and a != dm.PROBE_DAY:
            return None
        t0 = datetime.combine(a, time(9, 15), IST)
        ts = [int((t0 + timedelta(minutes=i)).timestamp()) for i in range(375)]
        col = [1.0] * 375
        key = "ce" if body["drvOptionType"] == "CALL" else "pe"
        return {"data": {key: {"timestamp": ts, "strike": [51000.0] * 375, "open": col, "high": col, "low": col,
                               "close": col, "volume": col, "oi": col, "iv": col, "spot": [self.level] * 375}}}


def test_probe_accepts_the_index_and_refuses_the_wrong_one():
    assert dm.probe("BANKNIFTY", "T", 25000.0, fetch=FakeDhan(51000.0), sleep=lambda s: None) == ""
    assert "outside" in dm.probe("MIDCPNIFTY", "T", 25000.0, fetch=FakeDhan(51000.0), sleep=lambda s: None)
    assert "NIFTY's own" in dm.probe("FINNIFTY", "T", 25000.0, fetch=FakeDhan(25010.0), sleep=lambda s: None)
    assert "minutes came back" in dm.probe("SENSEX", "T", None, fetch=lambda t, b, s: None, sleep=lambda s: None)
    sensex = dm.body(dm.UNDERLYINGS["SENSEX"], 0, "CALL", dm.PROBE_DAY, dm.PROBE_DAY)
    assert sensex["exchangeSegment"] == "BSE_FNO" and sensex["securityId"] == 51 and sensex["strike"] == "ATM"


def test_download_order_empty_days_resume_and_budget():
    end = date(2021, 10, 8)                                          # Fri 1, Mon 4 to Fri 8: six weekdays
    with tempfile.TemporaryDirectory() as tmp:
        root, fake = Path(tmp), FakeDhan()
        s = dm.run("T", "BANKNIFTY", end, root=root, fetch=fake, sleep=lambda x: None, log=lambda m: None)
        # three days with a weekly contract (1, 4, 5 Oct): 42 requests each; three without: one each
        assert s["requests"] == 3 * 42 + 3 and s["saved"] == s["requests"] and s["empty_days"] == 3, s
        assert not s["stopped"] and s["failed"] == 0
        assert fake.calls[0][1:4] == ("NSE_FNO", "ATM", "CALL") and fake.calls[0][0] == 25
        assert [c[2] for c in fake.calls[:6]] == ["ATM", "ATM", "ATM+1", "ATM+1", "ATM-1", "ATM-1"]
        day = dd.load_day(date(2021, 10, 4), root=root / "BANKNIFTY")          # the loader reads it as NIFTY's
        assert len(day) == 375 and len(day["09:15"]) == 42 and day["09:15"][(0, "CALL")].spot == 51000.0
        again = FakeDhan()
        s2 = dm.run("T", "BANKNIFTY", end, root=root, fetch=again, sleep=lambda x: None, log=lambda m: None)
        assert s2["requests"] == 0 and not again.calls and s2["skipped"] == 3 * 42 + 3
    with tempfile.TemporaryDirectory() as tmp:
        s3 = dm.run("T", "SENSEX", end, root=Path(tmp), fetch=FakeDhan(), sleep=lambda x: None, log=lambda m: None,
                    budget=50)
        assert s3["stopped"] and s3["requests"] == 50
        s4 = dm.run("T", "SENSEX", end, root=Path(tmp), fetch=FakeDhan(), sleep=lambda x: None, log=lambda m: None)
        assert not s4["stopped"] and s4["requests"] == 3 * 42 + 3 - 50              # it resumes where it stopped


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
