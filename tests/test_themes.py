"""
dashboard/themes.py on made-up NSE files: returns over 21, 63 and 252 sessions and against NIFTY 50 are
right, the strongest comes first, an index with a short history shows a dash, and a server with no file
says so.
Run: PYTHONPATH=. python tests/test_themes.py
"""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

from dashboard import build as b
from dashboard import themes as t

HEAD = "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,P/E\n"


def _write(root: Path, n: int = 300, new_from: int = 270):
    d = date(2025, 6, 2)
    for i in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        rows = [("Nifty 50", 100 * 1.001 ** i), ("Nifty IT", 100 * 1.002 ** i), ("Nifty Metal", 100 * 0.999 ** i),
                ("Nifty India Defence", 100 * 1.003 ** i)]
        if i >= new_from:
            rows.append(("Nifty AI Catalysts", 50 + i))
        p = root / "state" / "nse_archive" / "index_close" / str(d.year) / f"ind_close_all_{d:%d%m%Y}.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(HEAD + "".join(f"{name},{d:%d-%m-%Y},1,1,1,{c:.6f},20\n" for name, c in rows))
        d += timedelta(days=1)
    return d - timedelta(days=1)


def test_returns_and_ranking():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root)
        days, g = t.rows(root)
        assert len(days) == t.READ
        it = next(r for r in g["sectors"] if r["label"] == "IT")
        assert abs(it["1M"] - (1.002 ** 21 - 1)) < 1e-6 and abs(it["12M"] - (1.002 ** 252 - 1)) < 1e-6
        assert abs(it["rel3M"] - ((1.002 / 1.001) ** 63 - 1)) < 1e-6
        assert it["above200"] is True and abs(it["from_high"]) < 1e-9
        metal = next(r for r in g["sectors"] if r["label"] == "Metal")
        assert metal["above200"] is False and metal["rel3M"] < 0
        ai = next(r for r in g["themes"] if r["label"] == "AI catalysts")
        assert "1M" in ai and "3M" not in ai and "above200" not in ai       # 30 sessions of history only
        ranked = t.by_strength(g["sectors"] + g["themes"])
        assert [r["label"] for r in ranked][:3] == ["Defence", "IT", "Metal"] and ranked[-1]["label"] == "AI catalysts"
        html = "".join(t.section(root))
        assert "1 of 2 sectors are above their 200-day average" in html
        assert html.index("Defence") < html.index("AI catalysts")
        assert "<td class=num>—</td>" in html                                 # the short history shows a dash
        page = b.build(root)
        assert "Themes: which sectors are moving" in page and "Could not build" not in page


def test_no_file_says_so():
    with tempfile.TemporaryDirectory() as tmp:
        assert "No NSE index file yet" in "".join(t.section(Path(tmp)))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
