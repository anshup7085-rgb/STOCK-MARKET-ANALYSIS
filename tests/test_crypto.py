"""
The crypto head on a fake Binance: only closed candles are kept, history is fetched once and then only
what is new, the minute recorder appends without gaps or repeats, the crypto brain's inputs never read
the day they forecast, and a forecast is written once, scored the next day, and refused when late.
Run: PYTHONPATH=. python tests/test_crypto.py
"""

from __future__ import annotations

import contextlib
import io
import math
import random
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import run_crypto as rc
from crypto import binance, store
from crypto import brain as cb
from trading.journal import Journal

DAY_MS = 86_400_000


class FakeBinance:
    """Candles of any interval from a seeded random walk; open time t gives the same price every call."""

    def __init__(self, seed=5, until=datetime(2026, 11, 21, tzinfo=timezone.utc)):
        self.calls, self.seed, self.until = [], seed, int(until.timestamp() * 1000)

    def price(self, sym, t):
        rng = random.Random(f"{self.seed}{sym}{t // DAY_MS}")
        return 20000.0 * math.exp(0.0005 * (t // DAY_MS) % 3 + rng.gauss(0, 0.03))

    def __call__(self, url):
        self.calls.append(url)
        q = dict(p.split("=") for p in url.split("?")[1].split("&"))
        step = binance.STEP_MS[q["interval"]]
        start = int(q["startTime"])
        t0 = start - start % step
        rows = []
        for k in range(int(q["limit"])):
            t = t0 + k * step
            if t > self.until:
                break
            c = self.price(q["symbol"], t)
            rows.append([t, f"{c}", f"{c * 1.02}", f"{c * 0.98}", f"{c}", "10", t + step - 1, f"{c * 10}", 100, "0", "0", "0"])
        return rows


def test_only_closed_candles_and_paging():
    fake = FakeBinance()
    now = int(datetime(2026, 11, 21, 0, 30, tzinfo=timezone.utc).timestamp() * 1000)
    got = binance.klines("BTCUSDT", "1h", now - 30 * 3_600_000, now, get=fake, sleep=lambda s: None)
    assert got and all(c.t + 3_600_000 - 1 < now for c in got)                    # the forming hour is not kept
    many = binance.klines("BTCUSDT", "1d", now - 2500 * DAY_MS, now, get=fake, sleep=lambda s: None)
    assert len(fake.calls) >= 4 and len(many) == len({c.t for c in many})          # pages, no repeats


def test_download_once_then_only_new_and_the_store_never_shortens():
    with tempfile.TemporaryDirectory() as tmp:
        keep = store.ROOT
        store.ROOT = Path(tmp)
        rc.NOW, keep_now = (lambda: datetime(2020, 3, 1, 1, 0, tzinfo=timezone.utc)), rc.NOW
        try:
            fake = FakeBinance()
            rc.cmd_download(None, get=fake, sleep=lambda s: None, log=lambda m: None)
            first = len(store.load("BTCUSDT", "1d"))
            assert first > 1100 and store.load("BTCUSDT", "1d")[0].t == rc.START
            n = len(fake.calls)
            rc.NOW = lambda: datetime(2020, 3, 3, 1, 0, tzinfo=timezone.utc)
            rc.cmd_download(None, get=fake, sleep=lambda s: None, log=lambda m: None)
            assert len(store.load("BTCUSDT", "1d")) == first + 2
            assert len(fake.calls) - n == len(binance.SYMBOLS) * 2                 # one request each: only what is new
            store.save("BTCUSDT", "1d", store.load("BTCUSDT", "1d")[:10])           # an old slice saved again
            assert len(store.load("BTCUSDT", "1d")) == first + 2
        finally:
            store.ROOT, rc.NOW = keep, keep_now


def _days(n, seed=2):
    rng = random.Random(seed)
    out, p, t = [], 10000.0, int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    for i in range(n):
        p *= math.exp(rng.gauss(0, 0.03))
        out.append(binance.Candle(t + i * DAY_MS, p, p * 1.02, p * 0.98, p, 1.0, p * 100, 10))
    return out


def test_the_brain_reads_nothing_after_its_day():
    btc, eth = _days(900), _days(900, seed=3)
    rows = cb.build(btc, eth)
    assert len(rows) == 900 - cb.WARMUP and rows[-1]["y"] == {"up": None, "big": None}
    short = cb.build(btc[:700], eth[:700])
    assert short[-1]["day"] == rows[699 - cb.WARMUP]["day"] and short[-1]["x"] == rows[699 - cb.WARMUP]["x"]
    r = rows[100]
    i = 100 + cb.WARMUP
    assert r["y"]["up"] == int(btc[i + 1].close > btc[i].close)
    assert set(r["x"]) == set(cb.FEATURES) and r["x"]["eth_ret1"] is not None


def test_today_writes_once_scores_next_day_and_refuses_late():
    with tempfile.TemporaryDirectory() as tmp:
        keep = (store.ROOT, rc.OUT, rc.NOW)
        store.ROOT, rc.OUT = Path(tmp) / "k", Path(tmp) / "b"
        try:
            btc, eth = _days(1000), _days(1000, seed=3)
            store.save("BTCUSDT", "1d", btc[:-1])
            store.save("ETHUSDT", "1d", eth[:-1])
            last = store.utc_day(btc[-2])
            begins = datetime.combine(last + timedelta(days=1), datetime.min.time(), timezone.utc)
            rc.NOW = lambda: begins + timedelta(minutes=7)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc.cmd_today()
                rc.cmd_today()                                                     # twice: written once
            j = Journal(Path(tmp) / "b" / "journal.jsonl", "crypto-brain")
            assert len([r for r in j.records() if r["type"] == "CRYPTO_FORECAST"]) == 2
            assert "BTC closes higher:" in out.getvalue()
            store.save("BTCUSDT", "1d", btc[-1:])                                  # the day ends
            store.save("ETHUSDT", "1d", eth[-1:])
            rc.NOW = lambda: begins + timedelta(days=1, hours=3)                    # read late: no new forecast
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc.cmd_today()
            scored = [r["payload"] for r in j.records() if r["type"] == "CRYPTO_SCORED"]
            assert len(scored) == 2 and not any(s["late"] for s in scored) and "NO FORECAST" in out.getvalue()
            assert next(s for s in scored if s["question"] == "up")["y"] == int(btc[-1].close > btc[-2].close)
        finally:
            store.ROOT, rc.OUT, rc.NOW = keep


def test_the_minute_recorder_appends_without_gaps_or_repeats():
    with tempfile.TemporaryDirectory() as tmp:
        keep = (rc.LIVE, rc.NOW)
        rc.LIVE = Path(tmp)
        try:
            fake = FakeBinance(until=datetime(2026, 11, 22, tzinfo=timezone.utc))
            t0 = datetime(2026, 11, 20, 23, 50, 30, tzinfo=timezone.utc)
            rc.NOW = lambda: t0
            rc.record_minutes(get=fake, sleep=lambda s: None)
            rc.NOW = lambda: t0 + timedelta(minutes=15)                            # across midnight UTC
            rc.record_minutes(get=fake, sleep=lambda s: None)
            files = sorted((Path(tmp) / "BTCUSDT").glob("*.csv"))
            ts = [int(line.split(",")[0]) for f in files for line in f.read_text().splitlines()[1:]]
            assert [f.name for f in files] == ["2026-11-20.csv", "2026-11-21.csv"]
            assert ts == sorted(set(ts)) and all(b - a == 60_000 for a, b in zip(ts, ts[1:]))
            assert ts[-1] == int((t0 + timedelta(minutes=15)).replace(second=0).timestamp() * 1000) - 60_000
        finally:
            rc.LIVE, rc.NOW = keep


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
