"""tools/status.py on an empty and a filled made-up server. Run: PYTHONPATH=. python tests/test_status.py"""

from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path

from trading.journal import Journal

SPEC = Path(__file__).resolve().parents[1] / "tools" / "status.py"


def _load(root: Path):
    os.environ["STATUS_ROOT"] = str(root)
    spec = importlib.util.spec_from_file_location("status_tool", SPEC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_empty_and_filled():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        text = "\n".join(_load(root).lines())
        assert "India explorer: last active never; 0 paper trades" in text and "none yet" in text
        assert "NIFTY brain: last forecast after never" in text and "crypto recorder:" in text
        j = Journal(root / "state" / "fno_explore" / "journal.jsonl", "fno-explore")
        j.append("XP_EXIT", "2026-11-24", id="expiry_straddle-2026-11-24-1", net=1200.0, R=0.4)
        j.append("XP_EXIT", "2026-11-24", id="debit_spread-2026-11-24-1", net=-300.0, R=-0.2)
        b = Journal(root / "state" / "brain" / "journal.jsonl", "brain")
        b.append("BRAIN_FORECAST", asof="2026-11-24", after="2026-11-24", question="up", p=0.6, base=0.54)
        b.append("BRAIN_SCORED", asof="2026-11-23", after="2026-11-23", question="up", p=0.6, base=0.54, y=1, late=False)
        (root / "state" / "index_minutes" / "NIFTY_50").mkdir(parents=True)
        for y in ("2015", "2026"):
            (root / "state" / "index_minutes" / "NIFTY_50" / f"{y}.csv.gz").write_bytes(b"")
        text = "\n".join(_load(root).lines())
        assert "last active 2026-11-24; 2 paper trades, net Rs+900; expiry straddle 1 trade(s), net Rs+1,200" in text
        assert "NIFTY minutes on disk: 2015 to 2026" in text
        assert "NIFTY brain: last forecast after 2026-11-24; live score UP 1/1; BIG 0/0" in text
    os.environ.pop("STATUS_ROOT", None)


if __name__ == "__main__":
    test_empty_and_filled()
    print("ok")
