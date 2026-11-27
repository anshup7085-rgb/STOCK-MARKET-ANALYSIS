"""
crypto/minutes.py and research/perps_lab.py on made-up data: held-back months are refused, a file whose
checksum disagrees is never kept, both of Binance's file layouts read alike, a bracket's outcome and fill are
right (a minute touching both counts as the stop, a gapped stop fills at the open), longs pay positive
funding, a feature reads no later candle, books never hold two trades at once, and a run on a random walk
ends without an edge.
Run: PYTHONPATH=. python tests/test_perps_lab.py
"""

from __future__ import annotations

import hashlib
import io
import math
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

from crypto import minutes as cm
from research import perps_lab as pl

MIN = 60_000


def _zip(name: str, lines: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name.replace(".zip", ".csv"), "\n".join(lines) + "\n")
    return buf.getvalue()


def _kline_rows(t0: int, closes, header=False, seed=0) -> list[str]:
    rng = np.random.default_rng(seed)
    out = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,"
           "taker_buy_quote_volume,ignore"] if header else []
    prev = closes[0]
    for i, c in enumerate(closes):
        hi, lo = max(prev, c) * (1 + 0.0002), min(prev, c) * (1 - 0.0002)
        v = float(rng.uniform(5, 15))
        out.append(f"{t0 + i * MIN},{prev},{hi},{lo},{c},{v},{t0 + i * MIN + MIN - 1},{v * c},{int(rng.integers(10, 99))},"
                   f"{v * float(rng.uniform(0.3, 0.7))},0,0")
        prev = c
    return out


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def _fake_files(root: Path, symbol: str, months: list[date], seed: int) -> None:
    rng = np.random.default_rng(seed)
    p = 100.0
    for k, m in enumerate(months):
        nxt = cm.months(m, date(m.year + (m.month == 12), m.month % 12 + 1, 1))[-1]
        n = (_ms(nxt) - _ms(m)) // MIN
        closes = p * np.exp(np.cumsum(rng.normal(0, 0.0008, n)))
        p = float(closes[-1])
        folder = root / symbol
        folder.mkdir(parents=True, exist_ok=True)
        name = cm.name(symbol, "klines", m)
        (folder / name).write_bytes(_zip(name, _kline_rows(_ms(m), [round(float(c), 4) for c in closes], header=k % 2 == 0, seed=k)))
        fname = cm.name(symbol, "funding", m)
        rows = ["calc_time,funding_interval_hours,last_funding_rate"] + [
            f"{_ms(m) + i * 8 * 3_600_000},8,0.0001" for i in range(n // 480)]
        (folder / fname).write_bytes(_zip(fname, rows))


def test_held_back_months_are_refused():
    with tempfile.TemporaryDirectory() as d:
        for m in (date(2024, 11, 1), date(2026, 10, 1)):
            try:
                cm.download("BTCUSDT", "klines", m, root=Path(d), get=lambda u: b"")
            except cm.Holdout:
                pass
            else:
                raise AssertionError(f"{m} was not refused")
        try:
            cm.load("BTCUSDT", date(2024, 10, 1), date(2024, 11, 30), root=Path(d))
        except cm.Holdout:
            pass
        else:
            raise AssertionError("a held-back month was read")
        try:
            cm.download("BTCUSDT", "klines", date(2026, 11, 1), root=Path(d), get=lambda u: b"", registration_sha256="x")
        except cm.Holdout:
            pass
        else:
            raise AssertionError("a forward month was served for a historical test")


def test_a_bad_checksum_is_never_kept():
    m = date(2024, 1, 1)
    body = _zip("BTCUSDT-1m-2024-01.zip", _kline_rows(_ms(m), [100.0, 101.0]))
    good = hashlib.sha256(body).hexdigest()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        try:
            cm.download("BTCUSDT", "klines", m, root=root,
                        get=lambda u: (b"0" * 64 + b"  x.zip") if u.endswith(".CHECKSUM") else body)
        except ValueError:
            pass
        else:
            raise AssertionError("a file with the wrong checksum was kept")
        assert not list(root.rglob("*.zip"))
        got = cm.download("BTCUSDT", "klines", m, root=root,
                          get=lambda u: f"{good}  x.zip".encode() if u.endswith(".CHECKSUM") else body)
        assert got == "new" and cm.have("BTCUSDT", root) == ["BTCUSDT-1m-2024-01.zip"]
        assert cm.download("BTCUSDT", "klines", m, root=root, get=lambda u: 1 / 0) == "have"
        assert cm.download("BTCUSDT", "klines", date(2024, 2, 1), root=root, get=lambda u: None) == "none"


def test_both_file_layouts_read_alike():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for k, m in enumerate((date(2024, 1, 1), date(2024, 2, 1))):
            name = cm.name("BTCUSDT", "klines", m)
            (root / "BTCUSDT").mkdir(exist_ok=True)
            (root / "BTCUSDT" / name).write_bytes(_zip(name, _kline_rows(_ms(m), [100.0, 100.5, 101.0], header=k == 0)))
        c = cm.load("BTCUSDT", date(2024, 1, 1), date(2024, 2, 29), root=root)
        assert len(c["t"]) == 6 and list(c["close"][:3]) == [100.0, 100.5, 101.0] and list(c["close"][3:]) == [100.0, 100.5, 101.0]
        assert np.all(np.diff(c["t"]) > 0)


def _g(opens, highs, lows, closes):
    n = len(closes)
    return {"t": np.arange(n, dtype=np.int64) * MIN, "open": np.array(opens, float), "high": np.array(highs, float),
            "low": np.array(lows, float), "close": np.array(closes, float)}


def test_bracket_outcomes_and_fills():
    sig = np.full(10, 0.01)                         # u = 0.01 x sqrt(1) with h = 1 ... use h = 4: u = 0.02
    # long from 100: target 103, stop 98
    g = _g([100, 100, 101, 102, 100, 100, 100, 100, 100, 100],
           [100, 100.5, 101.5, 103.2, 100, 100, 100, 100, 100, 100],
           [100, 99.5, 100.5, 101.5, 100, 100, 100, 100, 100, 100],
           [100, 100.2, 101.2, 102.5, 100, 100, 100, 100, 100, 100])
    cls, ret, dur = pl.brackets(g, sig, np.array([0]), 4, 1)
    assert cls[0] == pl.TP and abs(ret[0] - 1.5) < 1e-6 and dur[0] == 3
    # one minute touching both: the stop
    g2 = _g([100, 100, 100, 100, 100], [100, 103.5, 100, 100, 100], [100, 97.5, 100, 100, 100], [100, 100, 100, 100, 100])
    cls, ret, dur = pl.brackets(g2, sig, np.array([0]), 4, 1)
    assert cls[0] == pl.SL and abs(ret[0] + 1.0) < 1e-6 and dur[0] == 1
    # a stop gapped through fills at the open: 96 is -2u
    g3 = _g([100, 96, 100, 100, 100], [100, 96.5, 100, 100, 100], [100, 95.5, 100, 100, 100], [100, 96, 100, 100, 100])
    cls, ret, _ = pl.brackets(g3, sig, np.array([0]), 4, 1)
    assert cls[0] == pl.SL and abs(ret[0] + 2.0) < 1e-6
    # short side of the same gap: the target, +1.5
    cls, ret, _ = pl.brackets(g3, sig, np.array([0]), 4, -1)
    assert cls[0] == pl.TP and abs(ret[0] - 1.5) < 1e-6
    # neither touched: out at the close of minute 4
    g4 = _g([100] * 6, [100.5] * 6, [99.5] * 6, [100, 100, 100, 100, 101, 100])
    cls, ret, dur = pl.brackets(g4, sig, np.array([0]), 4, 1)
    assert cls[0] == pl.TO and abs(ret[0] - 0.5) < 1e-6 and dur[0] == 4
    # a missing candle inside the window: no outcome
    g5 = _g([100] * 6, [100.5, np.nan, 100.5, 100.5, 100.5, 100.5], [99.5] * 6, [100] * 6)
    cls, _, _ = pl.brackets(g5, sig, np.array([0]), 4, 1)
    assert cls[0] == -1


def test_longs_pay_positive_funding():
    ft, fr = np.array([10 * MIN, 20 * MIN]), np.array([0.0001, 0.0003])
    t_in, t_out = np.array([5 * MIN, 5 * MIN, 10 * MIN]), np.array([15 * MIN, 15 * MIN, 25 * MIN])
    paid = pl.funding_paid(ft, fr, t_in, t_out, np.array([1, -1, 1]))
    assert np.allclose(paid, [-0.0001, 0.0001, -0.0003])


def test_features_read_no_later_candle():
    n = 3000
    rng = np.random.default_rng(3)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    g = {"t": np.arange(n, dtype=np.int64) * MIN, "open": closes, "high": closes * 1.0005, "low": closes * 0.9995,
         "close": closes, "volume": rng.uniform(1, 2, n), "trades": rng.uniform(10, 20, n), "taker_buy": rng.uniform(0.3, 1, n)}
    ft, fr = np.array([100 * MIN, 1000 * MIN]), np.array([0.0001, 0.0002])
    full = pl.features(g, ft, fr)
    cut = 2500
    part = pl.features({k: v[:cut] for k, v in g.items()}, ft, fr)
    a, b = full.iloc[:cut].to_numpy(dtype=float), part.to_numpy(dtype=float)
    assert np.allclose(a, b, equal_nan=True, rtol=1e-5, atol=1e-6)


def _rows(times, coins, d_minutes, ev_long, h=15):
    import pandas as pd
    n = len(times)
    return pd.DataFrame({"t_close": np.array(times, dtype=np.int64) * MIN, "coin": coins, "u": np.full(n, 0.001),
                         "ev_long": ev_long, "ev_short": np.full(n, -1.0), "hold_long": np.full(n, 5.0),
                         "hold_short": np.full(n, 5.0), f"c_{h}_long": np.full(n, 2, dtype=np.int8),
                         f"c_{h}_short": np.zeros(n, dtype=np.int8), f"r_{h}_long": np.full(n, 1.5),
                         f"r_{h}_short": np.full(n, -1.0), f"d_{h}_long": np.array(d_minutes, dtype=np.int16),
                         f"d_{h}_short": np.array(d_minutes, dtype=np.int16), f"f_{h}_long": np.zeros(n),
                         f"f_{h}_short": np.zeros(n), "month": ["2024-01"] * n})


def test_books_never_hold_two_trades_at_once():
    # two coins, decisions every 5 minutes, every trade lasts 12 minutes
    times = [0, 0, 5, 5, 10, 10, 15, 15, 20, 20]
    rows = _rows(times, [0, 1] * 5, [12] * 10, np.linspace(0.01, 0.02, 10))
    s, trades = pl.book(rows, 0.0001, 15, 1, desk=True)
    assert list(trades["t_close"] // MIN) == [0, 15] and s["trades"] == 2
    assert list(trades["coin"]) == [1, 1]                    # the higher-ranked coin at each time
    s, trades = pl.book(rows, 0.0001, 15, 1, desk=False)
    assert s["trades"] == 4 and sorted(trades["t_close"] // MIN) == [0, 0, 15, 15]
    # nothing above costs: no trade
    s, _ = pl.book(rows, 1.0, 15, 1, desk=True)
    assert s["trades"] == 0


def test_a_random_walk_shows_no_edge():
    saved = {k: getattr(pl, k) for k in ("DATA_FROM", "TEST_FROM", "TRAIN_MONTHS", "MIN_TRAIN_MONTHS", "MODEL")}
    try:
        pl.DATA_FROM, pl.TEST_FROM = date(2024, 8, 1), date(2024, 10, 1)
        pl.TRAIN_MONTHS, pl.MIN_TRAIN_MONTHS = 2, 2
        pl.MODEL = {**pl.MODEL, "max_iter": 15}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            months = [date(2024, 8, 1), date(2024, 9, 1), date(2024, 10, 1)]
            for i, sym in enumerate(("BTCUSDT", "ETHUSDT")):
                _fake_files(root, sym, months, seed=10 + i)
            res = pl.run(["ETHUSDT", "BTCUSDT"], (15,), root=root, out=root / "r.json", log=lambda *a: None)
        h = res["horizons"]["15"]
        assert res["symbols"][0] == "BTCUSDT"
        assert h["verdict"] == "NO EDGE"
        assert h["taker"]["desk"]["net_bps"] < 5
        assert set(h["taker"]["coins"]) == {"BTCUSDT", "ETHUSDT"}
    finally:
        for k, v in saved.items():
            setattr(pl, k, v)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
