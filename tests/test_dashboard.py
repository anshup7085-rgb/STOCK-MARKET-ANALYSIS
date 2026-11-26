"""
dashboard/build.py on a made-up server: every section is built from the records that exist, a missing
record says so instead of breaking the page, and text from the records is escaped.
Run: PYTHONPATH=. python tests/test_dashboard.py
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

from dashboard import build as b
from trading.journal import Journal

HEAD = "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,P/E\n"


def _index(root: Path, d: date, nifty: float, vix: float):
    p = root / "state" / "nse_archive" / "index_close" / str(d.year) / f"ind_close_all_{d:%d%m%Y}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(HEAD + f"Nifty 50,{d:%d-%m-%Y},1,1,1,{nifty},22\nIndia VIX,{d:%d-%m-%Y},-,-,-,{vix},-\n")


def test_an_empty_server_builds_a_page_that_says_what_is_missing():
    with tempfile.TemporaryDirectory() as tmp:
        page = b.build(Path(tmp))
    assert page.startswith("<!doctype html>") and "Market Lab" in page and "Could not build" not in page
    assert "No NSE index file yet" in page and "No forecast yet" in page and "not started" in page


def test_a_working_server_shows_the_market_the_brain_the_books_and_the_findings():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _index(root, date(2026, 11, 18), 25000.0, 15.0)
        _index(root, date(2026, 11, 19), 25250.0, 14.0)
        j = Journal(root / "state" / "brain" / "journal.jsonl", "brain")
        j.append("BRAIN_FORECAST", asof="2026-11-19", after="2026-11-19", question="up", p=0.56, base=0.54)
        j.append("BRAIN_FORECAST", asof="2026-11-19", after="2026-11-19", question="big", p=0.37, base=0.5)
        j.append("BRAIN_SCORED", asof="2026-11-18", after="2026-11-18", question="up", p=0.6, base=0.54, y=1, late=False)
        j.append("BRAIN_SCORED", asof="2026-11-09", after="2026-11-09", question="up", p=0.6, base=0.54, y=0, late=True)
        s = Journal(root / "state" / "fno_small" / "journal.jsonl", "fno-explore")
        s.append("XP_INIT", "2026-11-11", capital=20000.0, feed_name="x", fee_card="x", note="")
        s.append("XP_EXIT", "2026-11-12", id="long_cheap-1", net=-750.0, R=-0.2)
        log = root / "research" / "INTRADAY_LOG.md"
        log.parent.mkdir(parents=True)
        log.write_text("| 1 | 2026-11-04 | **Old <step>** | x |\n| 2 | 2026-11-19 | **New finding & more** | y |\n")
        page = b.build(root)
    assert "25,250.00" in page and "+1.00%" in page and "Close of Thursday 19 Nov 2026" in page
    assert "<b>56%</b>" in page and "<b>37%</b>" in page and "For the session after 2026-11-19" in page
    assert "<td>UP</td><td class=num>1</td><td class=num>1</td>" in page          # the late one is not counted
    assert "Option-buying book" in page and "−₹750" in page and "₹19,250" in page
    assert page.index("NIFTY closes higher") < page.index("A bigger move")
    assert "New finding &amp; more" in page and "Old &lt;step&gt;" in page          # escaped
    assert page.index("New finding") < page.index("Old &lt;")                       # newest first
    assert "Could not build" not in page


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
