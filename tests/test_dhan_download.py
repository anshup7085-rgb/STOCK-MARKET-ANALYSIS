"""
tools/dhan_download.py against a fake Dhan: 30-day windows, short replies filled a day at a time, the
one-day mode, empty windows, a token that expires mid-way and a resume that fetches nothing twice.
Run: PYTHONPATH=. python tests/test_dhan_download.py
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

from market.config import IST

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dhan_download", ROOT / "tools" / "dhan_download.py")
dl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dl)


def _reply(days, right):
    ts = [int(datetime.combine(d, time(9, 15), IST).timestamp()) + 60 * i for d in days for i in range(3)]
    s = {"timestamp": ts, "close": [1.0] * len(ts), "strike": [23000.0] * len(ts)}
    return {"data": {"ce": s if right == "CALL" else None, "pe": s if right == "PUT" else None}}


def _weekdays(a, b):
    return [a + timedelta(days=k) for k in range((b - a).days + 1) if (a + timedelta(days=k)).weekday() < 5]


def test_windows_short_replies_empty_windows_and_resume():
    tmp = Path(tempfile.mkdtemp())
    dl.START = date(2026, 9, 1)
    dl.PLAN = [(1, 0), (2, -3)]
    calls = []

    def fake(token, body, sleep=None):
        assert token == "SECRET-TOKEN-XYZ"
        calls.append(body)
        a, b = date.fromisoformat(body["fromDate"]), date.fromisoformat(body["toDate"])
        if a < date(2026, 9, 10):
            return None                                                   # before Dhan's first day
        days = _weekdays(a, b)
        if body["strike"] == "ATM-3" and (b - a).days > 2:
            days = days[:2]                                               # a short reply
        return _reply(days, body["drvOptionType"])

    dl.request = fake
    stats = dl.run("SECRET-TOKEN-XYZ", date(2026, 10, 30), tmp, lambda s: None, lambda m: None, size=30)
    assert stats["failed"] == 0 and stats["windows"] == 2 * 2 * 2       # 2 series x 2 rights x 2 windows
    assert stats["empty"] == 4 and stats["day_fills"] > 0
    assert (tmp / "code1" / "ATM_CALL" / "2026-10-01.json.gz").exists()
    assert list((tmp / "code2" / "ATM-3_PUT" / "days").glob("*.json.gz"))      # the short window was filled
    man = [json.loads(x) for x in (tmp / "manifest.jsonl").read_text().splitlines()]
    assert all(len(m["sha256"]) == 64 for m in man)
    blob = json.loads(gzip.decompress((tmp / "code1" / "ATM_PUT" / "2026-10-01.json.gz").read_bytes()))
    assert blob["data"]["pe"]["timestamp"] and "SECRET-TOKEN-XYZ" not in json.dumps(man)
    calls.clear()
    dl.run("SECRET-TOKEN-XYZ", date(2026, 10, 30), tmp, lambda s: None, lambda m: None, size=30)
    assert calls == []                                                    # resumed: nothing fetched twice


def test_one_day_mode_and_a_token_that_expires():
    tmp = Path(tempfile.mkdtemp())
    dl.START = date(2026, 10, 26)
    dl.PLAN = [(1, 0)]
    n = {"calls": 0}

    def fake(token, body, sleep=None):
        n["calls"] += 1
        if n["calls"] == 4:
            raise dl.TokenExpired("HTTP 401")
        a = date.fromisoformat(body["fromDate"])
        assert date.fromisoformat(body["toDate"]) == a + timedelta(days=1) and a.weekday() < 5
        return _reply([a], body["drvOptionType"])

    dl.request = fake
    try:
        dl.run("T", date(2026, 10, 30), tmp, lambda s: None, lambda m: None, size=1)
        raise AssertionError("an expired token did not stop the run")
    except dl.TokenExpired:
        pass
    assert len(list((tmp / "code1" / "ATM_CALL").glob("*.json.gz"))) == 3
    dl.request = lambda token, body, sleep=None: _reply([date.fromisoformat(body["fromDate"])], body["drvOptionType"])
    stats = dl.run("T2", date(2026, 10, 30), tmp, lambda s: None, lambda m: None, size=1)
    assert stats["windows"] == 7 and stats["skipped"] == 3                # 5 + 5 weekdays, 3 already saved
    assert dl.windows(date(2026, 10, 30), date(2026, 11, 2), 1) == [(date(2026, 10, 30), date(2026, 10, 31)),
                                                                     (date(2026, 11, 2), date(2026, 11, 3))]


if __name__ == "__main__":
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
