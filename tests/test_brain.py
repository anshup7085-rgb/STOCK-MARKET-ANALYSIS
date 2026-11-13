"""
The market brain on synthetic markets whose answer is known: a market whose quiet and stormy days
come in runs (so BIG can be forecast) and whose direction is a coin toss (so UP cannot). Also: NSE's
index file is read by column name, features never look ahead, and the walk-forward model never trains
on the month it forecasts. Run: PYTHONPATH=. python tests/test_brain.py
"""

from __future__ import annotations

import math
import random
import tempfile
from datetime import date, timedelta
from pathlib import Path

from brain import data, features, model


def _market(n: int, seed: int = 7):
    """n sessions of a NIFTY whose daily volatility switches slowly between calm and stormy."""
    rng = random.Random(seed)
    days, d = [], date(2014, 1, 1)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    nifty, vix, level, stormy = {}, {}, 8000.0, False
    for d in days:
        if rng.random() < 0.03:
            stormy = not stormy
        sigma = 0.018 if stormy else 0.006
        r = rng.gauss(0, sigma)
        o = level * math.exp(rng.gauss(0, sigma / 4))
        c = level * math.exp(r)
        nifty[d] = data.Bar(o, max(o, c) * 1.002, min(o, c) * 0.998, c, 22.0)
        vix[d] = data.Bar(None, None, None, 100 * sigma * math.sqrt(252) * math.exp(rng.gauss(0, 0.05)), None)
        level = c
    return {"nifty 50": nifty, "india vix": vix}, days


def test_index_file_is_read_by_column_name():
    raw = ("Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,"
           "Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield\n"
           "Nifty 50,06-11-2026,24500.5,24600,24380.05,\"24,522.20\",10,0.04,1,1,22.5,3.4,1.2\n"
           "India VIX,06-11-2026,15.6,17.9,15.2,17.47,1.9,12.1,-,-,-,-,-\n"
           "Nifty Bank,06-11-2026,-,-,-,-,-,-,-,-,-,-,-\n").encode()
    got = data.parse_index_file(raw)
    assert set(got) == {"nifty 50", "india vix"}                       # a row without a close is dropped
    assert got["nifty 50"] == data.Bar(24500.5, 24600.0, 24380.05, 24522.20, 22.5)
    assert got["india vix"].close == 17.47 and got["india vix"].pe is None
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a" / "index_close" / "2026").mkdir(parents=True)
        (root / "a" / "index_close" / "2026" / "ind_close_all_06112026.csv").write_bytes(raw)
        (root / "d" / "2026-11-09").mkdir(parents=True)
        (root / "d" / "2026-11-09" / "index_close.csv").write_bytes(raw.replace(b"24,522.20", b"24,700.00"))
        idx = data.load_indexes(root / "a", root / "d")
        assert idx["nifty 50"][date(2026, 11, 6)].close == 24522.20     # the archive
        assert idx["nifty 50"][date(2026, 11, 9)].close == 24700.00     # the daily recorder, for a later day


def test_features_use_nothing_after_the_day():
    idx, days = _market(700)
    rows = features.build(idx, {})
    assert rows[0]["day"] == days[features.WARMUP] and len(rows) == 700 - features.WARMUP
    assert rows[-1]["y"] == {"up": None, "big": None}                    # tomorrow has not happened
    cut = 600
    early = features.build({k: {d: v[d] for d in days[:cut]} for k, v in idx.items()}, {})
    same = rows[cut - features.WARMUP - 1]                                # the last row of the shorter history
    assert early[-1]["day"] == same["day"] and early[-1]["x"] == same["x"]   # later days change nothing in it
    assert same["x"]["fii_long"] is None and same["x"]["breadth"] is None     # missing inputs stay missing
    r = rows[10]
    i = days.index(r["day"])
    nxt = math.log(idx["nifty 50"][days[i + 1]].close / idx["nifty 50"][days[i]].close)
    assert r["y"]["up"] == int(nxt > 0) and r["y"]["big"] == int(abs(nxt) > r["usual_move"])


def test_the_brain_finds_skill_where_there_is_some_and_none_where_there_is_none():
    idx, days = _market(2600)
    rows = features.build(idx, {})
    big = model.walk_forward(rows, "big")
    up = model.walk_forward(rows, "up")
    sb, su = model.score(big), model.score(up)
    assert sb["n"] > 1500 and sb["skill"] > 0.05 and sb["z"] > 5, sb       # stormy days come in runs
    assert abs(su["skill"]) < 0.02 and su["z"] < 2.58, su                  # direction was a coin toss
    assert 0.40 < su["right"] < 0.60 and sb["right"] > 0.60
    first = date.fromisoformat(big[0]["day"])
    assert sum(1 for r in rows if r["day"] < date(first.year, first.month, 1)) >= model.MIN_TRAIN
    import run_brain
    text = "\n".join(run_brain.line("big", big))
    assert "SHOWS SKILL in exploration" in text and "skill by year" in text, text
    assert "no skill shown" in "\n".join(run_brain.line("up", up))


def test_walk_forward_never_trains_on_the_month_it_forecasts_or_on_days_it_may_not():
    idx, days = _market(1200)
    rows = features.build(idx, {})
    seen = []
    real_fit = model.fit

    def spy(train, target):
        seen.append((train[-1]["day"], len(train)))
        return real_fit(train, target)

    model.fit = spy
    try:
        preds = model.walk_forward(rows, "big")
        barred = date.fromisoformat(preds[0]["day"]) - timedelta(days=200)
        seen.clear()
        model.walk_forward(rows, "big", may_train=lambda d: d < barred)
    finally:
        model.fit = real_fit
    assert preds and all(last < barred for last, _ in seen)               # the barred days were never fitted
    months = sorted({q["day"][:7] for q in preds})
    assert len(months) > 10
    m = model.fit([r for r in rows[:600] if r["y"]["big"] is not None], "big")
    p = model.predict(m, rows[700])
    assert 0.0 < p < 1.0 and 0.2 < m["base"] < 0.8



def test_today_forecasts_before_the_day_and_scores_it_after():
    import contextlib
    import io

    import run_brain
    from trading.journal import Journal, verify_file
    idx, days = _market(840)
    head = "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,P/E\n"

    def put(root, d):
        n, v = idx["nifty 50"][d], idx["india vix"][d]
        p = root / "a" / "index_close" / str(d.year) / f"ind_close_all_{d:%d%m%Y}.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(head + f"Nifty 50,{d:%d-%m-%Y},{n.open},{n.high},{n.low},{n.close},22\n"
                     f"India VIX,{d:%d-%m-%Y},-,-,-,{v.close},-\n")

    def run(fn):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = fn(None)
        return rc, buf.getvalue()

    keep = (data.ARCHIVE, data.DAILY, run_brain.OUT)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        data.ARCHIVE, data.DAILY, run_brain.OUT = root / "a", root / "d", root / "brain"
        try:
            for d in days[:-1]:
                put(root, d)
            rc, out = run(run_brain.cmd_today)
            assert rc == 0 and f"after the close of {days[-2]}" in out and "nothing scored yet" in out, out
            assert "NIFTY closes higher:" in out and "either way:" in out
            j = Journal(root / "brain" / "journal.jsonl", "brain")
            first = [r for r in j.records() if r["type"] == "BRAIN_FORECAST"]
            assert len(first) == 2 and all(r["payload"]["after"] == days[-2].isoformat() for r in first)
            run(run_brain.cmd_today)                               # a second run the same evening adds nothing
            assert len(j.records()) == 2
            put(root, days[-1])                                    # the next session happens
            rc, out = run(run_brain.cmd_today)
            recs = j.records()
            scored = [r["payload"] for r in recs if r["type"] == "BRAIN_SCORED"]
            assert len(scored) == 2 and {s["question"] for s in scored} == {"up", "big"}
            real_up = int(idx["nifty 50"][days[-1]].close > idx["nifty 50"][days[-2]].close)
            assert next(s for s in scored if s["question"] == "up")["y"] == real_up
            assert next(s for s in scored if s["question"] == "up")["p"] == next(
                r["payload"]["p"] for r in first if r["payload"]["question"] == "up")     # the forecast as written
            assert "UP: 1 scored" in out and len([r for r in recs if r["type"] == "BRAIN_FORECAST"]) == 4
            assert verify_file(root / "brain" / "journal.jsonl").ok
            rc, out = run(run_brain.cmd_check)
            assert rc == 0 and "NIFTY 50 read from" in out and "not read from" not in out
        finally:
            data.ARCHIVE, data.DAILY, run_brain.OUT = keep
    assert run_brain.may_train(date(2024, 10, 31)) and not run_brain.may_train(date(2025, 6, 2))
    assert not run_brain.may_train(date(2026, 10, 16)) and run_brain.may_train(date(2026, 11, 12))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
