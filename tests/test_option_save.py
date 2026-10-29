"""
tools/save_expiring_options.py on a fake Kite: which contracts it saves, that it walks back
through 60-day windows, that it never shortens a saved history, and that it keeps out of
V's settlement window. Run: PYTHONPATH=. python tests/test_option_save.py
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import tempfile
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from market.config import IST

spec = importlib.util.spec_from_file_location(
    "save_expiring_options", Path(__file__).resolve().parents[1] / "tools" / "save_expiring_options.py")
so = importlib.util.module_from_spec(spec)
spec.loader.exec_module(so)

TODAY = date(2026, 10, 29)
AFTER_CLOSE = lambda: datetime(2026, 10, 29, 15, 41, tzinfo=IST)            # noqa: E731


class FakeKite:
    def __init__(self, listed: dict[int, date], calls: list):
        self.listed, self.calls = listed, calls          # instrument token -> first trading day

    def instruments(self, exch):
        if exch == "NSE":
            return [{"tradingsymbol": "NIFTY 50", "segment": "INDICES", "instrument_token": 256265}]
        rows = []
        for exp, tag in ((date(2026, 11, 3), "N03"), (date(2026, 11, 10), "N10")):
            for k in (19000, 22500, 22900, 27000):
                for right in ("CE", "PE"):
                    rows.append({"name": "NIFTY", "instrument_type": right, "expiry": exp, "strike": float(k),
                                 "tradingsymbol": f"NIFTY26{tag}{k}{right}", "instrument_token": k + (1 if right == "PE" else 0)
                                 + (100000 if tag == "N10" else 0)})
        rows.append({"name": "NIFTY", "instrument_type": "FUT", "expiry": date(2026, 11, 24), "strike": 0.0,
                     "tradingsymbol": "NIFTY26NOVFUT", "instrument_token": 9})
        rows.append({"name": "BANKNIFTY", "instrument_type": "CE", "expiry": date(2026, 11, 3), "strike": 54000.0,
                     "tradingsymbol": "BANKNIFTY26N0354000CE", "instrument_token": 7})
        return rows

    def historical_data(self, token, start, end, interval, oi=False):
        self.calls.append((token, start[:10], end[:10]))
        if token == 256265:
            return [{"date": datetime(2026, 10, 28, 9, 15, tzinfo=IST), "close": 22900.0}]
        s, e = date.fromisoformat(start[:10]), date.fromisoformat(end[:10])
        first = self.listed.get(token, TODAY - timedelta(days=30))
        out, d = [], max(s, first)
        while d <= e:
            if d.weekday() < 5:
                for m in (15, 16):
                    out.append({"date": datetime.combine(d, dtime(9, m), IST), "open": 1, "high": 2, "low": 1,
                                "close": 2, "volume": 10, **({"oi": 5} if oi else {})})
            d += timedelta(days=1)
        return out


def test_saves_nifty_contracts_expiring_within_a_week_near_the_money_with_their_whole_life():
    root, calls = Path(tempfile.mkdtemp()), []
    kite = FakeKite({22500: TODAY - timedelta(days=100)}, calls)                # one contract listed 100 days ago
    res = so.save(kite, TODAY, root, AFTER_CLOSE, lambda s: None)
    files = sorted(p.name for p in (root / "2026-11-03").glob("*.csv.gz"))
    assert files == ["NIFTY26N0322500CE.csv.gz", "NIFTY26N0322500PE.csv.gz",
                     "NIFTY26N0322900CE.csv.gz", "NIFTY26N0322900PE.csv.gz"]   # 19,000 and 27,000 are >15% away
    assert not (root / "2026-11-10").exists() and not (root / "2026-11-24").exists()   # beyond 7 days
    assert res["contracts"] == 4 and res["failed"] == 0
    long = gzip.decompress((root / "2026-11-03" / "NIFTY26N0322500CE.csv.gz").read_bytes()).decode().splitlines()
    assert long[0] == "time,open,high,low,close,volume,oi"
    first_day = date.fromisoformat(long[1][:10])
    assert first_day <= TODAY - timedelta(days=95)                                # walked back past one window
    assert len({c for c in calls if c[0] == 22500}) >= 2
    man = json.loads((root / "2026-11-03" / "manifest.json").read_text())
    assert man["files"]["NIFTY26N0322900CE.csv.gz"]["candles"] == len(
        gzip.decompress((root / "2026-11-03" / "NIFTY26N0322900CE.csv.gz").read_bytes()).decode().splitlines()) - 1


def test_a_saved_history_is_never_replaced_by_a_shorter_one():
    root, calls = Path(tempfile.mkdtemp()), []
    so.save(FakeKite({22500: TODAY - timedelta(days=100)}, calls), TODAY, root, AFTER_CLOSE, lambda s: None)
    path = root / "2026-11-03" / "NIFTY26N0322500CE.csv.gz"
    before = path.read_bytes()
    so.save(FakeKite({22500: TODAY - timedelta(days=3)}, calls), TODAY, root, AFTER_CLOSE, lambda s: None)
    assert path.read_bytes() == before


def test_it_keeps_out_of_vs_settlement_window():
    waits = []
    so.keep_clear_of_v(lambda: datetime(2026, 11, 3, 16, 0, tzinfo=IST), waits.append)
    assert waits == [80 * 60]                                                    # 16:00 -> 17:20
    waits.clear()
    so.keep_clear_of_v(AFTER_CLOSE, waits.append)
    assert waits == []


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
