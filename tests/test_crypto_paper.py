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
    free, india, spec = paper.worth(a, 120.0)
    assert free > india and abs(free - units * 120 * (1 - paper.FEE)) < 0.01
    assert spec == free                                           # no other income: a small profit is under the rebate


def test_slab_tax_rebate_and_marginal_relief():
    assert paper.slab_tax(400_000) == 0 and paper.slab_tax(1_200_000) == 0
    assert paper.slab_tax(1_220_000) == round(20_000 * 1.04, 2)            # marginal relief: no more than the excess
    assert paper.slab_tax(1_300_000) == round(75_000 * 1.04, 2)
    assert paper.slab_tax(3_000_000) == round((20_000 + 40_000 + 60_000 + 80_000 + 100_000 + 180_000) * 1.04, 2)


def test_the_business_ledger_sets_off_losses_and_taxes_a_years_net_profit():
    keep = paper.OTHER_INCOME
    paper.OTHER_INCOME = 1_500_000                                # a salary in the 15% band
    try:
        s = paper._spec_new(2026)
        s["fy_pnl"] = -30_000.0
        s = paper.close_year(s, 2027)                             # a loss: carried, no tax
        assert s["carry"] == [[2026, 30_000.0]] and s["tax_paid"] == 0
        s["fy_pnl"] = 50_000.0
        s = paper.close_year(s, 2028)                             # 50,000 less the 30,000 carried: 20,000 taxed
        assert s["carry"] == [] and abs(s["tax_paid"] - 20_000 * 0.15 * 1.04) < 0.01
        old = paper._spec_new(2020)
        old["fy_pnl"] = -10_000.0
        old = paper.close_year(old, 2021)
        old["fy_pnl"] = 0.0
        for fy in range(2022, 2027):
            old = paper.close_year(old, fy)
        assert old["carry"] == []                                 # gone after four years
        a, _ = paper.step(paper._new(2026), True, 100.0)
        b, _ = paper.step(a, False, 90.0)
        assert b["spec"]["fy_pnl"] < -10_000 and b["india"]["tax_paid"] == 0   # a business loss is kept to set off
    finally:
        paper.OTHER_INCOME = keep


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
            assert rows["ma50 BTC"]["spec"] is not None
        finally:
            store.ROOT = keep


def test_a_running_book_gains_the_third_ledger_from_its_vda_ledger():
    rising = [100.0 * math.exp(0.002 * i) for i in range(300)]
    with tempfile.TemporaryDirectory() as tmp:
        keep = store.ROOT
        store.ROOT = Path(tmp) / "k"
        root = Path(tmp) / "p"
        try:
            for c in paper.COINS:
                store.save(c, "1d", _candles(rising[:-1]))
            day0 = store.utc_day(_candles(rising)[-2])
            j = Journal(root / "journal.jsonl", "crypto-paper")
            old = {}
            for r in paper.RULES:
                for c in paper.COINS:
                    a = paper._new(2026)
                    a.pop("spec")                                  # a book written before the third ledger
                    old[f"{r} {c}"] = a
            j.append("CP_INIT", asof=day0.isoformat(), start=day0.isoformat(), capital=paper.CAPITAL)
            j.append("CP_DAY", asof=day0.isoformat(), day=day0.isoformat(), accounts=old, caught_up=False)
            for c in paper.COINS:
                store.save(c, "1d", _candles(rising))
            now = datetime.combine(day0 + timedelta(days=2), datetime.min.time(), timezone.utc)
            out = paper.run(now, root)
            recs = j.records()
            assert len(out) == 1 and any(r["type"] == "CP_NOTE" for r in recs)
            last = [r for r in recs if r["type"] == "CP_DAY"][-1]["payload"]["accounts"]
            assert all("spec" in a and a["spec_value"] is not None for a in last.values())
            assert verify_file(root / "journal.jsonl").ok
        finally:
            store.ROOT = keep


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
