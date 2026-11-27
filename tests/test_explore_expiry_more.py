"""
research/explore_expiry_more.py: the rule that finds expiry days from the prices alone, the reading
fixed in advance, and the refusal to read an index before its download is complete. Synthetic bars.
Run: PYTHONPATH=. python tests/test_explore_expiry_more.py
"""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

from research import explore_expiry_more as em
from trading.fno import dhan_data as dd


def _bars(open_straddle: float, close_straddle: float, spot: float = 50000.0):
    def pair(v):
        b = dd.Bar(50000.0, v / 2, v / 2, v / 2, v / 2, 1, 1, 15.0, spot)
        return {(0, "CALL"): b, (0, "PUT"): b}
    return {em.FIRST: pair(open_straddle), em.LAST: pair(close_straddle)}


def test_expiry_days_are_found_from_a_contract_in_its_last_minutes():
    # a week: the straddle decays day by day, is nearly worthless at Thursday's close, and Friday opens fresh
    days = [date(2023, 1, 2) + timedelta(days=k) for k in range(5)]
    book = {days[0]: _bars(600, 520), days[1]: _bars(510, 400), days[2]: _bars(390, 260),
            days[3]: _bars(250, 30), days[4]: _bars(700, 640)}
    assert em.expiry_days(days, book.get) == [days[3]]
    # an overnight jump in a contract with days left (a third dearer) is not an expiry
    book[days[1]] = _bars(690, 400)
    assert em.expiry_days(days, book.get) == [days[3]]
    # a holiday (no bars) between the last day and the fresh week does not hide the expiry
    book[days[4]] = {}
    later = days + [date(2023, 1, 9)]
    book[later[-1]] = _bars(700, 640)
    assert em.expiry_days(later, book.get) == [days[3]]
    assert em.time_value(_bars(600, 520), em.FIRST) == 600 / 50000.0 and em.time_value({}, em.FIRST) is None


def test_the_reading_fixed_in_advance():
    lo, hi = em.SPREADS
    assert em.verdict({lo: [1.0, 2.0, 3.0], hi: [1.0, 2.0, -3.0]})
    assert not em.verdict({lo: [1.0, 2.0, -3.0], hi: [1.0, 2.0, 3.0]})          # one entry time negative at 0.2%
    assert not em.verdict({lo: [1.0, 2.0, 3.0], hi: [1.0, -2.0, -3.0]})         # only one left at 0.5%


def test_an_index_is_not_read_before_its_download_is_complete():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "SENSEX"
        folder = root / "code1" / dd.series_name(0, "CALL")
        folder.mkdir(parents=True)
        (folder / "2024-10-30.json.gz").write_bytes(b"")
        assert not em.complete(root)
        (folder / "2024-11-01.json.gz").write_bytes(b"")
        assert em.complete(root)
        assert not em.complete(Path(tmp) / "BANKNIFTY")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
