"""
The evening report's money lines (tools/day_report.py): the paper capital, today's and the total
net as a share of it, and the most margin open at once. Run: PYTHONPATH=. python tests/test_day_report.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from trading.journal import Journal

ROOT = Path(__file__).resolve().parents[1]


def _entry(j, day, t, pid, margin, broker=None):
    j.append("XP_ENTRY", day, id=pid, strategy=pid.split("-")[0], day=day, direction="any",
             t=f"{day}T{t}:00+05:30", legs=[], risk=1000.0, margin_est=margin, entry_costs=0.0,
             broker_margin=broker)


def _exit(j, day, t, pid, net):
    j.append("XP_EXIT", day, id=pid, day=day, t=f"{day}T{t}:00+05:30", reason="time", net=net, R=0.1, exit_costs=0.0)


def test_money_lines():
    root = Path(tempfile.mkdtemp())
    ex = root / "state" / "fno_explore"
    ex.mkdir(parents=True)
    j = Journal(ex / "journal.jsonl", "fno-explore")
    j.append("XP_INIT", "2026-10-26", capital=500000.0, feed_name="x", fee_card="x", note="")
    _entry(j, "2026-10-30", "09:20", "a-1", 100000.0)
    _exit(j, "2026-10-30", "15:15", "a-1", -3000.0)
    day = "2026-11-03"
    _entry(j, day, "09:20", "a-2", 150000.0)
    _entry(j, day, "09:30", "b-1", 40000.0)
    _exit(j, day, "10:00", "b-1", 1000.0)
    _entry(j, day, "10:20", "c-1", 60000.0)                    # 150k + 60k = 210k is the peak, not 150k + 40k
    _exit(j, day, "15:15", "a-2", 2000.0)
    _exit(j, day, "15:15", "c-1", 3000.0)
    v = root / "state" / "paper_fno"
    v.mkdir(parents=True)
    jv = Journal(v / "journal.jsonl", "fno-paper")
    jv.append("FNO_ENTRY", "2026-10-28", trade={"id": "V-2026-11-03", "lots": 1, "credit": 34.55, "max_loss": 4427.0,
                                                "margin": {"initial": 61000.0, "final": 58000.5}})
    jv.append("FNO_SETTLED", day, trade_id="V-2026-11-03", net=-4486.0, R=-1.01)
    env = {**os.environ, "FNO_REPORT_ROOT": str(root), "PYTHONPATH": str(ROOT)}
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "day_report.py"), day], capture_output=True,
                         text=True, env=env).stdout
    line = next(x for x in out.splitlines() if x.startswith("money: paper capital"))
    assert "Rs500,000" in line and "today Rs+6,000 (+1.20%)" in line, line
    assert "since 2026-10-26 Rs+3,000 (+0.60%)" in line and "~Rs210,000" in line, line
    assert "(estimates, below Zerodha's for spreads)" in line, line
    day2 = "2026-11-04"
    _entry(j, day2, "11:00", "f-1", 2499.0, {"initial": 61000.0, "final": 58500.0})     # Zerodha's figure wins
    _exit(j, day2, "15:15", "f-1", -146.0)
    out2 = subprocess.run([sys.executable, str(ROOT / "tools" / "day_report.py"), day2], capture_output=True,
                          text=True, env=env).stdout
    line2 = next(x for x in out2.splitlines() if x.startswith("money: paper capital"))
    assert "~Rs58,500 (Zerodha's figures)" in line2, line2
    vline = next(x for x in out.splitlines() if x.startswith("money: V's"))
    assert "Rs-4,486 (-0.90%) over 1 week(s)" in vline and "Rs58,000" in vline, vline


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
