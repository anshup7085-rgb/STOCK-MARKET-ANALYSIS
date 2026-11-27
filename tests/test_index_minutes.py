"""
The NIFTY and VIX minute download (tools/index_minutes_download.py) on a fake Kite, reading it back
(trading/fno/index_minutes.py) with the held-back years kept back, and the intraday momentum
exploration (research/explore_momentum.py) on synthetic sessions whose answer is known.
Run: PYTHONPATH=. python tests/test_index_minutes.py
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from market.config import IST
from research import explore_momentum as em
from trading.fno import index_minutes as im
from trading.fno.dhan_data import Holdout

spec = importlib.util.spec_from_file_location(
    "index_minutes_download", Path(__file__).resolve().parents[1] / "tools" / "index_minutes_download.py")
dl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dl)

TODAY = date(2017, 3, 10)


class FakeKite:
    def __init__(self, fail_year: int | None = None):
        self.calls, self.fail_year = [], fail_year

    def instruments(self, exch):
        return [{"tradingsymbol": "NIFTY 50", "segment": "INDICES", "instrument_token": 256265},
                {"tradingsymbol": "INDIA VIX", "segment": "INDICES", "instrument_token": 264969},
                {"tradingsymbol": "INFY", "segment": "NSE", "instrument_token": 1}]

    def historical_data(self, token, start, end, interval):
        s, e = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
        self.calls.append((token, s, e))
        assert interval == "minute" and (e - s).days < 60, (s, e)
        if self.fail_year and s.year == self.fail_year:
            raise RuntimeError("gateway 502")
        out, d = [], s
        while d <= e:
            if d.weekday() < 5:
                t0 = datetime.combine(d, dtime(9, 15), IST)
                for i in range(375):
                    out.append({"date": t0 + timedelta(minutes=i), "open": 100.0, "high": 101.0, "low": 99.0,
                                "close": 100.5})
            d += timedelta(days=1)
        return out


def test_download_windows_skip_and_failure():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        k = FakeKite()
        s = dl.save(k, TODAY, root=root, sleep=lambda x: None)
        assert s["failed"] == 0 and s["fetched"] == 2 * 3, s            # 2015, 2016, 2017 for both
        assert all(c[2] < TODAY for c in k.calls)                        # never today: it is not over
        assert {c[0] for c in k.calls} == {256265, 264969}
        m = json.loads((root / "NIFTY_50" / "manifest.json").read_text())
        assert m["years"]["2015"]["complete"] and not m["years"]["2017"]["complete"]
        assert m["years"]["2016"]["sessions"] == m["years"]["2016"]["full_sessions"] == 261
        assert m["years"]["2017"]["last"].startswith("2017-03-09")
        # a rerun fetches only the year not yet complete
        k2 = FakeKite()
        s2 = dl.save(k2, TODAY, root=root, sleep=lambda x: None)
        assert s2["skipped"] == 4 and s2["fetched"] == 2 and {c[1].year for c in k2.calls} == {2017}
        # a failing year is counted and keeps the file already there
        before = (root / "NIFTY_50" / "2017.csv.gz").read_bytes()
        s3 = dl.save(FakeKite(fail_year=2017), TODAY + timedelta(days=30), root=root, sleep=lambda x: None)
        assert s3["failed"] == 2 and (root / "NIFTY_50" / "2017.csv.gz").read_bytes() == before


def test_loader_and_holdout():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dl.save(FakeKite(), TODAY, root=root, sleep=lambda x: None)
        got = im.load(im.NIFTY, date(2016, 2, 1), date(2016, 2, 5), root=root)
        assert list(got) == [date(2016, 2, d) for d in range(1, 6)]
        day = got[date(2016, 2, 1)]
        assert len(day) == 375 and min(day) == "09:15" and max(day) == "15:29" and day["09:44"].close == 100.5
        for end, reg in ((date(2024, 11, 1), None), (date(2026, 10, 19), "a" * 64)):
            try:
                im.load(im.NIFTY, date(2024, 10, 1), end, root=root, registration_sha256=reg)
                raise AssertionError("a held-back or forward session was served")
            except Holdout:
                pass
        assert not (root / "holdout_reads.log").exists()
        im.load(im.NIFTY, date(2024, 10, 1), date(2024, 11, 5), root=root, registration_sha256="b" * 64)
        log = (root / "holdout_reads.log").read_text().splitlines()
        assert len(log) == 1 and json.loads(log[0])["registration_sha256"] == "b" * 64


def _sessions(n: int, follow: bool):
    """Synthetic days: the first half hour moves 0.6% up or down; the late windows move 0.3% after it,
    with it (follow) or against it."""
    out, d, level = {}, date(2018, 1, 1), 10000.0
    for i in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        up = i % 3 != 0
        s = 0.006 if up else -0.006
        late = (0.003 if up else -0.003) * (1 if follow else -1)
        m = {}
        lv = level
        for j in range(375):
            hm = (datetime.combine(d, dtime(9, 15)) + timedelta(minutes=j)).strftime("%H:%M")
            if hm == "09:44":
                lv = level * (1 + s)
            if hm in ("14:59", "14:44"):
                lv = level * (1 + s)
            if hm in ("15:29", "15:14"):
                lv = level * (1 + s) * (1 + late)
            m[hm] = im.Candle(level if j == 0 else lv, lv, lv, lv)
        out[d] = m
        level = m["15:29"].close
        d += timedelta(days=1)
    return out


def test_momentum_exploration_finds_a_planted_effect():
    for follow in (True, False):
        rows = em.days_of(_sessions(60, follow))
        assert len(rows) == 59                                          # the first day has no yesterday
        for signal in em.SIGNALS:
            for window in em.WINDOWS:
                ts = em.trades(rows, signal, window, big_only=True)
                assert len(ts) == 59
                g = [t["gross_bps"] for t in ts]
                assert all(abs(x - (30 if follow else -30)) < 0.01 for x in g), g[:3]
                # one futures lot: costs of about 7 to 8 bps (STT 0.05% the biggest) are taken off
                net_bps = [t["net"] / (t["entry"] * em.LOT) * 1e4 for t in ts]
                assert all((21 if follow else -40) < x < (24 if follow else -36) for x in net_bps), net_bps[:3]
    small = em.trades(em.days_of(_sessions(10, True)), "gap + first half hour", "15:00-15:30", big_only=False)
    assert len(small) == 9 and "a test would need" in em.line(small)



def test_gap_fade_signs_and_thresholds():
    from research import explore_gap as eg
    out, d, level = {}, date(2019, 1, 1), 10000.0
    for i in range(30):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        gap = (0.012, -0.007, 0.002)[i % 3]                    # up 1.2%, down 0.7%, a small one
        o = level * (1 + gap)
        m = {}
        for j in range(375):
            hm = (datetime.combine(d, dtime(9, 15)) + timedelta(minutes=j)).strftime("%H:%M")
            lv = o if hm < "09:25" else o * (1 - gap / 2)      # half the gap is given back from 09:25 and holds
            m[hm] = im.Candle(o if j == 0 else lv, lv, lv, lv)
        out[d] = m
        level = m["15:29"].close
        d += timedelta(days=1)
    rows = eg.days_of(out)
    assert len(rows) == 29 and abs(rows[0]["gap"] + 0.007) < 1e-9
    big = eg.trades(rows, 0.01, "09:20", "15:15")
    assert len(big) == 9 and all(abs(t["gross_bps"] - 60) < 0.01 for t in big)     # faded 1.2% gaps: 0.6% back
    some = eg.trades(rows, 0.005, "09:20", "10:15")
    assert len(some) == 19 and all(t["gross_bps"] > 30 for t in some)
    late = eg.trades(rows, 0.005, "09:30", "15:15")
    assert all(abs(t["gross_bps"]) < 1e-6 for t in late) and all(t["net"] < 0 for t in late)   # costs only

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
