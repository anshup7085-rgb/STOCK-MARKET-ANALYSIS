"""
The crypto paper book on made-up candles: it starts on the latest closed day, follows each rule's decision
at that day's close, taxes only the India ledger and only on gains, never processes a day twice, catches up
missed days in order (marked), and its journal verifies.
Run: PYTHONPATH=. python tests/test_crypto_paper.py
"""

from __future__ import annotations

import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crypto import binance, paper, store
from trading.journal import Journal, verify_file

DAY_MS = 86_400_000
T0 = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _candles(prices):
    return [binance.Candle(T0 + i * DAY_MS, p, p, p, p, 1.0, p, 1) for i, p in enumerate(prices)]


def test_step_taxes_only_india_and_only_gains():
    a, act = paper.step(paper._new(), True, 100.0)
    assert act == "BUY" and a["in"] and a["free"]["cash"] == 0 and a["india"]["cost"] == paper.CAPITAL
    up, act = paper.step(a, False, 120.0)
    units = paper.CAPITAL * (1 - paper.FEE) / 100.0
    gain = units * 120.0 - paper.CAPITAL
    assert act == "SELL" and abs(up["free"]["cash"] - units * 120 * (1 - paper.FEE)) < 1e-6
    assert abs(up["india"]["tax_paid"] - paper.TAX * gain) < 1e-6
    assert abs(up["free"]["cash"] - up["india"]["cash"] - paper.TAX * gain) < 1e-6
    down, _ = paper.step(paper.step(paper._new(), True, 100.0)[0], False, 80.0)
    assert down["india"]["tax_paid"] == 0 and abs(down["india"]["cash"] - down["free"]["cash"]) < 1e-6
    same, act = paper.step(a, True, 130.0)
    assert act is None and same["free"]["units"] == a["free"]["units"]
    free, india = paper.worth(a, 120.0)
    assert free > india and abs(free - units * 120 * (1 - paper.FEE)) < 0.01


def test_the_book_starts_follows_catches_up_and_verifies():
    rising = [100.0 * math.exp(0.002 * i) for i in range(300)]
    falling = [rising[-1] * math.exp(-0.01 * i) for i in range(1, 40)]
    with tempfile.TemporaryDirectory() as tmp:
        keep = store.ROOT
        store.ROOT = Path(tmp) / "k"
        root = Path(tmp) / "p"
        try:
            for c in paper.COINS:
                store.save(c, "1d", _candles(rising))
            day0 = store.utc_day(_candles(rising)[-1])
            now = datetime.combine(day0 + timedelta(days=1), datetime.min.time(), timezone.utc) + timedelta(minutes=7)
            out = paper.run(now, root)
            assert len(out) == 1 and "BUY BTC (hold)" in out[0] and "BUY BTC (ma50)" in out[0]
            assert paper.run(now, root) == ["crypto paper book: nothing new to process"]       # never twice
            for c in paper.COINS:                                   # 39 falling days arrive at once
                store.save(c, "1d", _candles(rising + falling))
            later = now + timedelta(days=39, hours=5)
            out = paper.run(later, root)
            assert len(out) == 39 and any("SELL BTC (ma50)" in line for line in out)
            j = Journal(root / "journal.jsonl", "crypto-paper")
            days = [r["payload"] for r in j.records() if r["type"] == "CP_DAY"]
            assert [d["day"] for d in days] == sorted({d["day"] for d in days}) and len(days) == 40
            assert days[1]["caught_up"] and not days[0]["caught_up"]
            assert verify_file(root / "journal.jsonl").ok
            rows = {f"{r['rule']} {r['coin']}": r for r in paper.summary(root)}
            assert rows["hold BTC"]["in"] and not rows["ma50 BTC"]["in"]
            assert rows["ma50 BTC"]["free"] > rows["hold BTC"]["free"]                      # it stepped aside
            assert rows["ma50 BTC"]["india"] <= rows["ma50 BTC"]["free"]
        finally:
            store.ROOT = keep


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
