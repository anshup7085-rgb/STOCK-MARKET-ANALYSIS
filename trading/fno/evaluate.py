"""
`fno evaluate`: the round-3 verdict and the descriptive record, computed from
the journal alone (research/PREREGISTRATION_3.md, section 12).

The same `decide` runs inside the engine after every pass, so the engine stops
entering the moment the registered verdict is reached, and not before.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import numpy as np

from market.calendar import TradingCalendar, load_calendar
from market.config import IST
from research.fno_stats import betting_path
from trading.fno.state import State
from trading.fno.strategy_v import REGISTERED, VParams
from trading.journal import Journal, verify_file


def sequence(st: State, cal: TradingCalendar, today: date) -> tuple[list[dict], int, str | None]:
    """
    Section 12: counted cycles in expiry order, up to the first one not yet
    resolved. A trade enters at its R; a MISSED cycle enters at R = -1; a cycle
    the rules left without a trade is not in the sequence. Returns the items, the
    number of counted cycles closed so far, and why the run stopped (if it did).
    """
    items, closed, stop = [], 0, None
    for e, prev in st.counted_cycles():
        t = st.trade_for(e)
        if t is not None:
            if t["status"] != "SETTLED":
                stop = f"{t['id']} not settled"
                break
            closed += 1
            items.append({"expiry": e.isoformat(), "kind": "trade", "R": t["R"], "net": t["net"], "trade": t})
            continue
        c = st.cycles.get(e)
        if c is None:
            stop = f"cycle {e} still open" if e >= today else f"cycle {e} not yet closed"
            break
        closed += 1
        if c["outcome"] == "missed":
            items.append({"expiry": e.isoformat(), "kind": "missed", "R": -1.0, "net": None})
    return items, closed, stop


def _abandoned(st: State, today: date, p: VParams) -> str | None:
    days = sorted(set(st.master_days))
    marks = [st.start] + days + [today]
    for a, b in zip(marks, marks[1:]):
        if (b - a).days > p.abandon_after_days:
            return f"no contract master read from {a} to {b}"
    breaks = st.chain_breaks()
    if breaks:
        a, b = breaks[0]
        return f"the weekly expiry chain has a gap from {a} to {b}"
    return None


def verdict_from(items: list[dict], closed: int, p: VParams = REGISTERED) -> tuple[str, int | None]:
    """Section 12's outcomes over the expiry-ordered sequence, in order: harm, PASS, futility, no evidence."""
    r = np.array([x["R"] for x in items], dtype=float)
    if (r < -2).any():
        return "FAIL — bound broken: a trade's R fell below -2", int(np.argmax(r < -2)) + 1
    path = betting_path(r) if len(r) else np.array([])
    cum_net = 0.0
    for i, x in enumerate(items):
        k = i + 1
        if x["net"] is not None:
            cum_net += x["net"]
        if cum_net <= -p.harm_stop:
            return f"FAIL — harm stop at item {k} (net Rs{cum_net:,.0f})", k
        if path[i] >= p.threshold:
            return f"PASS — betting wealth {path[i]:.1f} >= {p.threshold:g} at item {k}", k
        if k == p.futility_n and r[:k].mean() <= 0:
            return f"FAIL — futility: mean R of the first {k} items is {r[:k].mean():+.3f}", k
        if k == p.max_trades:
            return f"FAIL — no evidence: {k} items without a PASS", k
    if closed >= p.max_cycles:
        return f"FAIL — no evidence: {closed} weekly expiries without a PASS", len(items)
    k = path[-1] if len(path) else 1.0
    return f"UNDECIDED — {len(items)} item(s) in the sequence; betting wealth {k:.2f} of {p.threshold:g}", None


def decide(st: State, cal: TradingCalendar, now: datetime, p: VParams = REGISTERED) -> tuple[str, int | None]:
    """The registered verdict for the journal so far, or UNDECIDED (section 12)."""
    if st.start is None:
        return "NO BOOK", None
    items, closed, _ = sequence(st, cal, now.date())
    verdict, at = verdict_from(items, closed, p)
    if at is None:
        why = _abandoned(st, now.date(), p)
        if why:
            return f"FAIL — abandoned: {why}", len(items)
    return verdict, at


def evaluate(journal_path: Path, now: datetime | None = None, p: VParams = REGISTERED,
             cal: TradingCalendar | None = None) -> dict:
    now = now or datetime.now(IST)
    cal = cal or load_calendar()
    vr = verify_file(journal_path)
    recs = Journal(journal_path, "fno-paper").records()
    st = State.from_records(recs)
    out: dict = {"journal": str(journal_path), "journal_verifies": vr.ok, "journal_records": vr.records,
                 "journal_problems": vr.problems[:10], "void_records": st.void}
    if st.init is None:
        out["verdict"] = "NO BOOK — no FNO_INIT record"
        return out
    out.update(start=st.start.isoformat(), feed=st.feed, modes=sorted(st.modes),
               registration={k: st.init.get(k) for k in ("prereg_sha256", "registered_sha256", "git_commit")})
    verdict, at = decide(st, cal, now, p)
    problems = []
    if not vr.ok:
        problems.append("the journal does not verify")
    if st.modes != {"fno-paper"}:
        problems.append(f"record modes {sorted(st.modes)} are not all fno-paper")
    if st.inits != 1:
        problems.append(f"{st.inits} FNO_INIT records")
    if st.void:
        problems.append(f"{len(st.void)} void duplicate record(s)")
    if not st.init.get("prereg_sha256") or st.init.get("prereg_sha256") != st.init.get("registered_sha256"):
        problems.append("FNO_INIT does not carry the registered hash of PREREGISTRATION_3.md")
    out["verdict"] = (f"NOT EVALUABLE — {'; '.join(problems)} (as recorded: {verdict})" if problems else verdict)
    out["decided_at_item"] = at
    out["recorded_verdict"] = st.verdict

    items, closed, stop = sequence(st, cal, now.date())
    out["cycles_closed"] = closed
    out["sequence_stops_at"] = stop
    r = np.array([x["R"] for x in items], dtype=float)
    out["betting_wealth"] = float(betting_path(r)[-1]) if len(r) else 1.0
    trades = [x["trade"] for x in items if x["kind"] == "trade"]
    out["items"] = [{"expiry": x["expiry"], "kind": x["kind"], "R": round(x["R"], 4),
                     "net": None if x["net"] is None else round(x["net"], 2)} for x in items]
    out["cycles"] = {e.isoformat(): c["outcome"] for e, c in sorted(st.cycles.items())}
    out["descriptive"] = _descriptive(trades, items, recs)
    return out


def _flags(t: dict) -> list[str]:
    f = list(t.get("flags") or [])
    s = t.get("settlement") or {}
    src = str(s.get("source", ""))
    if src.startswith("entered by the operator"):
        f.append("settlement entered by the operator")
    if "close, matching Yahoo" in src:
        f.append("settled on the index close (pre-change rule)")
    if src.startswith("no final settlement price"):
        f.append("settled at -M after 10 trading days")
    if (s.get("checks") or {}).get("flag"):
        f.append(s["checks"]["flag"])
    return f


def _descriptive(trades: list[dict], items: list[dict], recs: list[dict]) -> dict:
    """Section 12, 'Reported, never decided on'."""
    out: dict = {"label": "DESCRIPTIVE — not a test; the verdict above is the only decision",
                 "trades": len(trades), "missed_cycles": sum(x["kind"] == "missed" for x in items)}
    if trades:
        net = np.array([t["net"] for t in trades], dtype=float)
        rr = np.array([t["R"] for t in trades], dtype=float)
        cum = np.cumsum(net)
        peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
        per = []
        for t in trades:
            win = t["credit"] * t["qty"] - t["entry_costs_total"]
            fill_pts = sum(abs(g["price"] - g["mid"]) for g in t["legs"])
            per.append({"id": t["id"], "lots": t["lots"], "credit": t["credit"], "max_loss": t["max_loss"],
                        "net": t["net"], "R": round(t["R"], 4),
                        "breakeven_win_rate": round(1.0 if win <= 0 else t["max_loss"] / (win + t["max_loss"]), 4),
                        "fill_vs_mid_points": round(fill_pts, 3),
                        "fill_vs_mid_R": round(fill_pts * t["qty"] / t["max_loss"], 4),
                        "costs": round(t["entry_costs_total"] + ((t.get("settlement") or {}).get("settlement_costs_total") or 0), 2),
                        "flags": _flags(t)})
        flag_counts: dict[str, int] = {}
        for x in per:
            for f in x["flags"]:
                flag_counts[f] = flag_counts.get(f, 0) + 1
        out.update(win_rate=float((net > 0).mean()), mean_R=float(rr.mean()), mean_net=float(net.mean()),
                   total_net=float(net.sum()), cumulative_net=[round(float(c), 2) for c in cum],
                   max_drawdown=float((peak - cum).max()),
                   costs_share_of_credit=float(sum(x["costs"] for x in per)
                                               / sum(t["credit"] * t["qty"] for t in trades)),
                   flag_counts=flag_counts, per_trade=per)
    checks: dict[str, int] = {}
    unobserved: dict[str, int] = {}
    for rec in recs:
        if rec["type"] != "FNO_PASS":
            continue
        pl = rec["payload"]
        if pl.get("outcome") == "no_trade":
            checks[pl.get("code", "?")] = checks.get(pl.get("code", "?"), 0) + 1
        elif pl.get("outcome") in ("data_unavailable", "blocked", "calendar_missing"):
            unobserved[pl["outcome"]] = unobserved.get(pl["outcome"], 0) + 1
    out["passes_without_trade_by_check"] = checks
    out["passes_unobserved_by_cause"] = unobserved
    out["runner_days"] = sorted({rec["asof"] for rec in recs if rec["type"] == "FNO_RUNNER_START"})
    return out
