"""
tools/dhan_check.py against a fake Dhan that serves our own synthetic week back, a session per call:
it finds the near-expiry code, aligns the minutes and reports the match, and the token is used only
in the request header. Run: PYTHONPATH=. python tests/test_dhan_check.py
"""

from __future__ import annotations

import gzip
import importlib.util
import io
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, datetime, time, timedelta
from pathlib import Path

from market.config import IST

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dhan_check", ROOT / "tools" / "dhan_check.py")
dc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dc)

DAYS = [date.fromisoformat(d) for d in dc.SESSIONS]


def _minutes(d):
    t = datetime.combine(d, time(9, 15), IST)
    return [t + timedelta(minutes=i) for i in range(375)]


def _ours(folder: Path):
    folder.mkdir(parents=True)
    for k in (23600, 23650):
        for kind in ("CE", "PE"):
            lines = ["time,open,high,low,close,volume,oi"]
            for d in DAYS:
                for i, t in enumerate(_minutes(d)):
                    lines.append(f"{t.isoformat()},1,1,1,{100 + i * 0.05:.2f},10,{5000 + i}")
            (folder / f"NIFTY26N03{k}{kind}.csv.gz").write_bytes(gzip.compress("\n".join(lines).encode()))


def test_the_check_finds_the_near_code_and_reports_the_match():
    tmp = Path(tempfile.mkdtemp())
    _ours(tmp / "ours")
    dc.OURS, dc.OUT = tmp / "ours", tmp / "out"
    dc.getpass.getpass = lambda prompt="": "TOKEN-123"
    dc.GAP_S = 0
    calls = []

    def fake_call(token, body):
        calls.append(body)
        assert token == "TOKEN-123"
        if body["expiryCode"] != 1:
            return 200, {"data": {"ce": None, "pe": None}}
        d = date.fromisoformat(body["fromDate"])
        assert body["toDate"] == (d + timedelta(days=1)).isoformat()             # one session a call
        ts, k, c, oi = [], [], [], []
        for i, t in enumerate(_minutes(d)):
            ts.append(int(t.timestamp()))
            k.append(23600.0 if i < 200 else 23650.0)
            c.append(round(100 + i * 0.05 + (0.1 if i % 50 == 0 else 0), 2))      # 8 of 375 off by 0.10
            oi.append(5000 + i)
        s = {"timestamp": ts, "strike": k, "close": c, "oi": oi}
        return 200, {"data": {"ce": s if body["drvOptionType"] == "CALL" else None,
                              "pe": s if body["drvOptionType"] == "PUT" else None}}

    dc.call = fake_call
    out = io.StringIO()
    with redirect_stdout(out):
        assert dc.main() == 0
    text = out.getvalue()
    assert "code 1: 750 minutes" in text and "code 2: no data" in text and "code 3: no data" in text
    assert "expiry code 1" in text and "OVERALL: 31500 Dhan minutes; 31500 found" in text
    assert "close equal to the tick 97.9%" in text and "TOKEN-123" not in text
    assert all(b["securityId"] == 13 and b["interval"] == "1" for b in calls)
    assert not any("TOKEN" in p.read_text() for p in (tmp / "out").iterdir())     # replies only, no token


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
