"""
The human desk (research/BLUEPRINT.md, part 6): the holder's own view of the next NSE session, written in a
minute on the dashboard and scored exactly like the brain, on the same two questions, against the same base
rate, beside the brain's forecast for the same day.

  up    will NIFTY close above the last close?                       (a chance, 0 to 100%)
  big   will NIFTY move more than its usual move, either way?        (the brain's definition: more than the
                                                                      median absolute daily move of the last
                                                                      250 sessions)

A view is about the next session. It counts only if written before that session opens (09:15 IST); the
last one written before then is the one scored. A view written later is kept, marked late, and never
counted, the same rule as the brain's. Views and scores go to state/human/journal.jsonl (hash-chained,
never edited). Scoring runs each evening with the brain (run_brain.py today).
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "state" / "human"
IST = ZoneInfo("Asia/Kolkata")
OPEN = time(9, 15)
NOTE_MAX = 280
QUESTIONS = ("up", "big")


class Invalid(ValueError):
    pass


def _journal(root: Path | None):
    from trading.journal import Journal
    root = root or ROOT
    root.mkdir(parents=True, exist_ok=True)
    return Journal(root / "journal.jsonl", "human-desk")


def _trading(d: date, cal) -> bool:
    return cal.is_trading_day(d) if cal is not None else d.weekday() < 5


def target(now: datetime, cal=None) -> dict:
    """The session a view written now is about, the session before it, and the deadline (its opening)."""
    now = now.astimezone(IST)
    d = now.date()
    if not (_trading(d, cal) and now.time() < OPEN):
        d += timedelta(days=1)
        while not _trading(d, cal):
            d += timedelta(days=1)
    before = d - timedelta(days=1)
    while not _trading(before, cal):
        before -= timedelta(days=1)
    return {"session": d.isoformat(), "after": before.isoformat(),
            "deadline": datetime.combine(d, OPEN, IST).isoformat(timespec="minutes")}


def _chance(v, name: str) -> float | None:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise Invalid(f"{name} must be a number from 0 to 100") from None
    if not 0 <= x <= 100 or x != x:
        raise Invalid(f"{name} must be from 0 to 100")
    return round(x / 100, 4)


def submit(up=None, big=None, note: str = "", now: datetime | None = None, cal=None, root: Path | None = None) -> dict:
    """Validate and record one view; returns what was recorded."""
    p_up, p_big = _chance(up, "up"), _chance(big, "big")
    if p_up is None and p_big is None:
        raise Invalid("give at least one of the two chances")
    note = re.sub(r"[\x00-\x1f\x7f]", " ", str(note or "")).strip()
    if len(note) > NOTE_MAX:
        raise Invalid(f"the note is longer than {NOTE_MAX} characters")
    now = (now or datetime.now(IST)).astimezone(IST)
    t = target(now, cal)
    payload = {**t, "up": p_up, "big": p_big, "note": note, "written": now.isoformat(timespec="seconds")}
    _journal(root).append("HUMAN_VIEW", asof=t["after"], **payload)
    return payload


def records(root: Path | None = None) -> list[dict]:
    from trading.journal import Journal
    path = (root or ROOT) / "journal.jsonl"
    return Journal(path, "human-desk").records() if path.exists() else []


def latest(now: datetime | None = None, cal=None, root: Path | None = None) -> dict | None:
    """The view that currently stands for the next session, if one was written."""
    t = target((now or datetime.now(IST)), cal)
    mine = [r["payload"] for r in records(root) if r["type"] == "HUMAN_VIEW" and r["payload"]["session"] == t["session"]]
    return mine[-1] if mine else None


def score(rows: list[dict], brain_records: list[dict], root: Path | None = None) -> int:
    """Score every view whose session has an answer. `rows` are brain.features rows (day, y); the brain's
    journal gives its forecast and base rate for the same day. Returns how many scores were written."""
    recs = records(root)
    views = [r["payload"] for r in recs if r["type"] == "HUMAN_VIEW"]
    done = {(r["payload"]["after"], r["payload"]["question"]) for r in recs if r["type"] == "HUMAN_SCORED"}
    by_day = {r["day"].isoformat(): r for r in rows}
    brain = {(r["payload"]["after"], r["payload"]["question"]): r["payload"] for r in brain_records if r["type"] == "BRAIN_FORECAST"}
    groups: dict[str, list[dict]] = {}
    for v in views:
        groups.setdefault(v["after"], []).append(v)
    j, n = None, 0
    for after, vs in sorted(groups.items()):
        row = by_day.get(after)
        if row is None:
            continue
        on_time = [v for v in vs if datetime.fromisoformat(v["written"]) < datetime.fromisoformat(v["deadline"])]
        v, late = (on_time[-1], False) if on_time else (vs[-1], True)
        for q in QUESTIONS:
            if v.get(q) is None or (after, q) in done or row["y"].get(q) is None:
                continue
            b = brain.get((after, q))
            if b is not None:
                base = b["base"]
            else:
                ys = [r["y"][q] for r in rows if r["day"] <= row["day"] and r["y"].get(q) is not None]
                base = round(sum(ys) / len(ys), 4) if ys else 0.5
            j = j or _journal(root)
            j.append("HUMAN_SCORED", asof=after, after=after, session=v["session"], question=q, p=v[q], base=base,
                     y=row["y"][q], late=late, brain_p=None if b is None else b["p"], note=v.get("note", ""))
            n += 1
    return n


def summary(root: Path | None = None) -> dict:
    """Per question: the holder's record, and the brain's on exactly the same days."""
    from brain import model
    scored = [r["payload"] for r in records(root) if r["type"] == "HUMAN_SCORED" and not r["payload"].get("late")]
    out = {}
    for q in QUESTIONS:
        mine = [s for s in scored if s["question"] == q]
        both = [s for s in mine if s.get("brain_p") is not None]
        out[q] = {"n": len(mine), "right": sum((s["p"] > 0.5) == bool(s["y"]) for s in mine),
                  "you": model.score(mine) if len(mine) >= 2 else {"n": len(mine)},
                  "brain_same_days": model.score([{**s, "p": s["brain_p"]} for s in both]) if len(both) >= 2 else {"n": len(both)},
                  "brain_right": sum((s["brain_p"] > 0.5) == bool(s["y"]) for s in both), "both": len(both)}
    out["late"] = sum(1 for r in records(root) if r["type"] == "HUMAN_SCORED" and r["payload"].get("late"))
    return out
