"""
brain/human.py, dashboard/serve.py and brain/events.py on made-up days: a view is about the next session
and counts only if written before it opens; the last on-time view is the one scored, beside the brain's
forecast for the same day; bad input is refused; the server saves a view and nothing else; expiries move to
the trading day before a holiday, and foreign times are shown in IST across summer time.
Run: PYTHONPATH=. python tests/test_human_desk.py
"""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
from datetime import date, datetime
from pathlib import Path

from brain import events as ev
from brain import human
from market.calendar import TradingCalendar

IST = human.IST
CAL = TradingCalendar(holidays={"2026-12-25": "Christmas", "2026-11-24": "Guru Nanak"}, source="test", verified_on="2026-11-27")


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def test_a_view_is_about_the_next_session():
    assert human.target(at(2026, 11, 30, 8, 0), CAL)["session"] == "2026-11-30"          # Monday before the open
    assert human.target(at(2026, 11, 30, 9, 15), CAL)["session"] == "2026-12-01"         # at the open: tomorrow
    t = human.target(at(2026, 11, 27, 16, 0), CAL)                                        # Friday evening
    assert t["session"] == "2026-11-30" and t["after"] == "2026-11-27" and t["deadline"].startswith("2026-11-30T09:15")
    t = human.target(at(2026, 12, 24, 16, 0), CAL)                                        # Christmas is a holiday
    assert t["session"] == "2026-12-28" and t["after"] == "2026-12-24"
    assert human.target(at(2026, 11, 28, 11, 0), CAL)["after"] == "2026-11-27"            # Saturday


def test_bad_views_are_refused():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for kw in ({"up": 101}, {"up": -1}, {"up": "x"}, {}, {"up": 50, "note": "x" * 281}):
            try:
                human.submit(now=at(2026, 11, 27, 20), cal=CAL, root=root, **kw)
            except human.Invalid:
                continue
            raise AssertionError(f"accepted {kw}")
        rec = human.submit(up=65, big=None, note="banks strong\nRBI next week", now=at(2026, 11, 27, 20), cal=CAL, root=root)
        assert rec["up"] == 0.65 and rec["big"] is None and "\n" not in rec["note"] and rec["session"] == "2026-11-30"
        assert human.latest(at(2026, 11, 28, 10), CAL, root)["up"] == 0.65


def _rows():
    """y of a day answers the session after it: 26 Nov's (about 27 Nov) is known, 27 Nov's (about 30 Nov) not yet."""
    return [{"day": date(2026, 11, 25), "y": {"up": 1, "big": 1}}, {"day": date(2026, 11, 26), "y": {"up": 0, "big": 1}},
            {"day": date(2026, 11, 27), "y": {"up": None, "big": None}}]


def test_the_last_on_time_view_is_scored_beside_the_brain():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        human.submit(up=30, big=70, now=at(2026, 11, 26, 20), cal=CAL, root=root)          # about 27 Nov
        human.submit(up=20, big=80, now=at(2026, 11, 27, 9, 0), cal=CAL, root=root)        # changed before the open
        human.submit(up=90, big=10, now=at(2026, 11, 27, 9, 30), cal=CAL, root=root)       # after the open: about 30 Nov
        brain = [{"type": "BRAIN_FORECAST", "payload": {"after": "2026-11-26", "question": "up", "p": 0.55, "base": 0.53}},
                 {"type": "BRAIN_FORECAST", "payload": {"after": "2026-11-26", "question": "big", "p": 0.4, "base": 0.5}}]
        assert human.score(_rows(), brain, root) == 2
        assert human.score(_rows(), brain, root) == 0                                       # never scored twice
        sc = {r["payload"]["question"]: r["payload"] for r in human.records(root) if r["type"] == "HUMAN_SCORED"}
        assert sc["up"]["p"] == 0.2 and sc["up"]["y"] == 0 and sc["up"]["brain_p"] == 0.55 and sc["up"]["base"] == 0.53
        assert sc["big"]["p"] == 0.8 and sc["big"]["y"] == 1 and not sc["up"]["late"]
        s = human.summary(root)
        assert s["up"]["n"] == 1 and s["up"]["right"] == 1 and s["up"]["brain_right"] == 0 and s["late"] == 0


def test_a_view_written_after_the_open_is_kept_but_never_counted():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        rec = human.submit(up=70, big=50, now=at(2026, 11, 27, 9, 0), cal=CAL, root=root)
        rows = _rows()
        # rewrite history the honest way: a view whose deadline had passed when written (written on the day)
        from trading.journal import Journal
        Journal(root / "journal.jsonl", "human-desk").append(
            "HUMAN_VIEW", asof="2026-11-26", session="2026-11-27", after="2026-11-26", deadline="2026-11-27T09:15+05:30",
            up=0.9, big=0.9, note="", written="2026-11-27T10:00:00+05:30")
        human.score(rows, [], root)
        sc = [r["payload"] for r in human.records(root) if r["type"] == "HUMAN_SCORED"]
        assert all(not s["late"] for s in sc) and {s["p"] for s in sc} == {0.7, 0.5}  # the on-time view wins
        assert rec["session"] == "2026-11-27"


def test_the_server_saves_a_view_and_refuses_the_rest():
    from dashboard import serve
    with tempfile.TemporaryDirectory() as d:
        pages, root = Path(d) / "pages", Path(d) / "human"
        pages.mkdir()
        (pages / "index.html").write_text("<p>hi</p>")
        srv = serve.make_server("127.0.0.1", 0, pages=pages, human_root=root, cal=CAL)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]

        def call(method, path, body=None, headers=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, r.read()
        try:
            assert call("GET", "/index.html")[0] == 200
            code, body = call("GET", "/api/state")
            assert code == 200 and "session" in json.loads(body)["target"]
            ok = json.dumps({"up": 60, "big": 35, "note": "test"})
            code, body = call("POST", "/api/view", ok, {"Content-Type": "application/json"})
            assert code == 200 and json.loads(body)["view"]["up"] == 0.6
            assert call("POST", "/api/view", json.dumps({"up": 500}), {"Content-Type": "application/json"})[0] == 400
            assert call("POST", "/api/view", "not json", {"Content-Type": "application/json"})[0] == 400
            assert call("POST", "/api/view", "x" * 5000, {"Content-Type": "application/json"})[0] == 413
            assert call("POST", "/index.html", ok, {"Content-Type": "application/json"})[0] == 404
            assert len([r for r in human.records(root) if r["type"] == "HUMAN_VIEW"]) == 1
        finally:
            srv.shutdown()


def test_expiries_move_before_a_holiday_and_times_are_shown_in_ist():
    nov = {e.day: e.event for e in ev.expiries(date(2026, 11, 16), date(2026, 11, 30), CAL)}
    assert "moved from Tue 24 Nov" in nov[date(2026, 11, 23)] and "monthly" in nov[date(2026, 11, 23)]
    dec = [e for e in ev.expiries(date(2026, 12, 1), date(2026, 12, 31), CAL) if "NIFTY" in e.event]
    assert [e.day.day for e in dec] == [1, 8, 15, 22, 29] and "monthly" in dec[-1].event
    sensex = [e.day for e in ev.expiries(date(2026, 12, 21), date(2026, 12, 27), CAL) if "SENSEX" in e.event]
    assert sensex == [date(2026, 12, 24)]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "events.csv"
        p.write_text("when,tz,market,event,importance,status,source\n"
                     "2026-12-09 14:00,America/New_York,US,Fed,high,confirmed,x\n"
                     "2027-03-17 14:00,America/New_York,US,Fed March,high,confirmed,x\n"
                     "2026-12-07,Asia/Kolkata,IN,Rule change,high,to-check,x\n")
        e = {x.event: x for x in ev.load(p)}
        assert e["Fed"].at == datetime(2026, 12, 10, 0, 30, tzinfo=IST)                   # winter: 10:30 hours ahead
        assert e["Fed March"].at == datetime(2027, 3, 17, 23, 30, tzinfo=IST)             # New York summer time
        assert e["Rule change"].all_day
        up = ev.upcoming(at(2026, 12, 6, 12), 5, CAL, p)
        assert [x.event for x in up if x.market != "IN" or "Rule" in x.event] == ["Rule change", "Fed"]
        assert ev.on(date(2026, 12, 25), CAL, p)[0].event.startswith("NSE closed")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
