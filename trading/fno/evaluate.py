"""
`fno evaluate`: the round-3 verdict and the descriptive record, computed from
the journal alone (research/PREREGISTRATION_3.md, section 12).

The same `decide` runs inside the engine after every pass, so the engine stops
entering the moment the registered verdict is reached, and not before. Once an
FNO_VERDICT is journaled, it is the verdict; a later recomputation that differs
is reported as a discrepancy, never substituted.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from market.calendar import DEFAULT_PATH as HOLIDAYS_PATH
from market.calendar import TradingCalendar, load_calendar
from market.config import IST
from research.fno_stats import betting_path
from trading.fno.state import State
from trading.fno.strategy_v import REGISTERED, VParams
from trading.journal import GENESIS, Journal, _digest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ANCHOR_RE = re.compile(r"anchor:\s*seq\s+(\d+)\s+sha256\s+([0-9a-f]{64})")
ANCHOR_DAYS = 7


def verify_tolerant(path: Path) -> dict:
    """
    The hash chain, tolerating a torn line (power cut mid-append) when the next
    whole record chains to the last whole record before it. Anything else breaks it.
    """
    p = Path(path)
    out = {"ok": True, "records": 0, "torn": [], "problems": []}
    if not p.exists():
        return out
    prev, expected = GENESIS, 1
    with p.open() as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                out["torn"].append(lineno)
                continue
            out["records"] += 1
            if rec.get("seq") != expected:
                out["problems"].append(f"line {lineno}: seq {rec.get('seq')} where {expected} expected")
            if rec.get("prev") != prev:
                out["problems"].append(f"line {lineno}: chain broken")
            if _digest(rec) != rec.get("hash"):
                out["problems"].append(f"line {lineno}: content does not match its hash (edited)")
            expected, prev = int(rec.get("seq", expected)) + 1, rec.get("hash")
    out["ok"] = not out["problems"]
    return out


def cycles_in_order(st: State) -> list[dict]:
    """
    Every counted cycle in expiry order, resolved or not: a trade (with its R once
    settled), MISSED (R = -1), or no trade (left out of the sequence). Each carries
    the date it resolved.
    """
    out = []
    for e, prev in st.counted_cycles():
        t = st.trade_for(e)
        if t is not None:
            done = t["status"] == "SETTLED"
            out.append({"expiry": e, "kind": "trade", "resolved": done, "R": t["R"], "net": t["net"],
                        "on": date.fromisoformat(t["closed_asof"]) if done else None, "trade": t})
            continue
        c = st.cycles.get(e)
        if c is None:
            out.append({"expiry": e, "kind": "open", "resolved": False})
        else:
            kind = "missed" if c["outcome"] == "missed" else "no trade"
            out.append({"expiry": e, "kind": kind, "resolved": True, "R": -1.0 if kind == "missed" else None,
                        "net": None, "on": date.fromisoformat(c["asof"])})
    return out


def abandoned_on(st: State, today: date, p: VParams = REGISTERED) -> tuple[date, str] | None:
    """The first day the test counts as abandoned (section 12), if it does."""
    days = sorted(set(st.master_days))
    marks = [st.start] + days + [today]
    for a, b in zip(marks, marks[1:]):
        if (b - a).days > p.abandon_after_days:
            return a + timedelta(days=p.abandon_after_days + 1), f"no contract master read from {a} to {b}"
    breaks = st.chain_breaks()
    if breaks:
        a, b = breaks[0]
        return a + timedelta(days=1), f"the weekly expiry chain has a gap from {a} to {b}"
    return None


def decide(st: State, cal: TradingCalendar, now: datetime, p: VParams = REGISTERED) -> tuple[str, int | None]:
    """
    Section 12, cycle by cycle in expiry order. Within an item: bound, harm, PASS,
    futility, no evidence; then the cap on counted cycles. Abandonment decides at
    its own date if that comes first. A rule break (section 11) ends the run.
    """
    if st.start is None:
        return "NO BOOK", None
    abandon = abandoned_on(st, now.date(), p)
    rs: list[float] = []
    cum_net, closed = 0.0, 0
    for c in cycles_in_order(st):
        if not c["resolved"]:
            break
        if abandon and c["on"] >= abandon[0]:
            return f"FAIL — abandoned: {abandon[1]}", len(rs)
        closed += 1
        if c["kind"] != "no trade":
            rs.append(c["R"])
            k = len(rs)
            if not np.isfinite(c["R"]) or c["R"] < -2:
                return f"FAIL — bound broken: item {k} has R {c['R']} below -2", k
            if c["net"] is not None:
                cum_net += c["net"]
            if cum_net <= -p.harm_stop:
                return f"FAIL — harm stop at item {k} (net Rs{cum_net:,.0f})", k
            wealth = betting_path(np.array(rs))[-1]
            if wealth >= p.threshold:
                return f"PASS — betting wealth {wealth:.1f} >= {p.threshold:g} at item {k}", k
            if k == p.futility_n and np.mean(rs) <= 0:
                return f"FAIL — futility: mean R of the first {k} items is {np.mean(rs):+.3f}", k
            if k == p.max_trades:
                return f"FAIL — no evidence: {k} items without a PASS", k
        if closed >= p.max_cycles:
            return f"FAIL — no evidence: {closed} counted cycles closed without a PASS", len(rs)
    if st.rule_breaks:
        return f"FAIL — the rules changed during the test: {st.rule_breaks[0]}", len(rs)
    if abandon and abandon[0] <= now.date():
        return f"FAIL — abandoned: {abandon[1]}", len(rs)
    wealth = betting_path(np.array(rs))[-1] if rs else 1.0
    return f"UNDECIDED — {len(rs)} item(s) in the sequence; betting wealth {wealth:.2f} of {p.threshold:g}", None


def anchors(log: Path, recs: list[dict], repo: Path = REPO_ROOT) -> dict:
    """
    Section 17: head hashes committed to research/ROUND3_LOG.md. Each must match
    the journal; a PASS needs every FNO_ENTRY and FNO_SETTLED covered by an anchor
    committed within 7 days of it (git's commit time; GitHub's push record is the
    check the independent recomputation makes).
    """
    by_seq = {r["seq"]: r for r in recs}
    try:
        text = log.read_text()
    except OSError:
        text = ""
    found, bad = [], []
    for seq, h in ANCHOR_RE.findall(text):
        seq = int(seq)
        if seq not in by_seq or by_seq[seq]["hash"] != h:
            bad.append(f"anchor seq {seq} does not match the journal")
            continue
        when = None
        try:
            rel = str(Path(log).resolve().relative_to(Path(repo).resolve()))
            out = subprocess.run(["git", "-C", str(repo), "log", "--reverse", "--format=%cI", f"-S{h}", "--", rel],
                                 capture_output=True, text=True, timeout=20).stdout
            when = datetime.fromisoformat(out.splitlines()[0]) if out.strip() else None
        except (OSError, subprocess.SubprocessError, ValueError):
            when = None
        found.append({"seq": seq, "committed": when.isoformat() if when else None})
    uncovered = []
    for r in recs:
        if r["type"] not in ("FNO_ENTRY", "FNO_SETTLED"):
            continue
        ts = datetime.fromisoformat(r["ts"])
        ok = any(a["seq"] >= r["seq"] and a["committed"]
                 and datetime.fromisoformat(a["committed"]) <= ts + timedelta(days=ANCHOR_DAYS) for a in found)
        if not ok:
            uncovered.append(r["seq"])
    return {"anchors": found, "mismatched": bad, "uncovered_records": uncovered}


def problems_with(st: State, vr: dict, cal: TradingCalendar) -> list[str]:
    """Section 17: what makes a journal NOT EVALUABLE, whatever its verdict."""
    out = []
    if not vr["ok"]:
        out.append("the journal does not verify")
    if st.modes != {"fno-paper"}:
        out.append(f"record modes {sorted(st.modes)} are not all fno-paper")
    if st.inits != 1:
        out.append(f"{st.inits} FNO_INIT records")
    if st.void:
        out.append(f"{len(st.void)} void record(s)")
    if not st.init.get("prereg_sha256") or st.init.get("prereg_sha256") != st.init.get("registered_sha256"):
        out.append("FNO_INIT does not carry the registered hash of PREREGISTRATION_3.md")
    doubled = [d.isoformat() for d, shas in st.master_sha.items() if len(shas) > 1]
    if doubled:
        out.append(f"more than one contract master on {', '.join(doubled[:3])}")
    stray = [d.isoformat() for d, sha in st.pass_master_sha if sha is not None and sha not in st.master_sha.get(d, [])]
    if stray:
        out.append(f"entry passes used a contract master not journaled for their day ({stray[0]})")
    current, journaled = dict(cal.holidays), st.holidays_now()
    if st.calendars and current != journaled:
        changed = sorted({d for d in set(current) | set(journaled) if current.get(d) != journaled.get(d)})
        if st.last_asof and changed[0] <= st.last_asof.isoformat():
            out.append(f"data/nse_holidays.json now differs from the journaled list on {changed[0]}")
    return out


def evaluate(journal_path: Path, now: datetime | None = None, p: VParams = REGISTERED,
             cal: TradingCalendar | None = None, anchor_log: Path | None = None, repo: Path | None = None) -> dict:
    now = now or datetime.now(IST)
    cal = cal or load_calendar(HOLIDAYS_PATH)
    vr = verify_tolerant(journal_path)
    recs = Journal(journal_path, "fno-paper").records()
    st = State.from_records(recs)
    out: dict = {"journal": str(journal_path), "journal_verifies": vr["ok"], "journal_records": vr["records"],
                 "torn_lines": vr["torn"], "journal_problems": vr["problems"][:10], "void_records": st.void}
    if st.init is None:
        out["verdict"] = "NO BOOK — no FNO_INIT record"
        return out
    out.update(start=st.start.isoformat(), feed=st.feed, modes=sorted(st.modes),
               registration={k: st.init.get(k) for k in ("prereg_sha256", "registered_sha256", "git_commit")})

    recomputed, at = decide(st, cal, now, p)
    verdict = st.verdict["verdict"] if st.verdict else recomputed
    out["recomputed_verdict"] = recomputed
    if st.verdict and recomputed != verdict:
        out["discrepancy"] = f"recorded {verdict!r}; recomputed {recomputed!r}"

    problems = problems_with(st, vr, cal)
    anc = anchors(anchor_log or REPO_ROOT / "research" / "ROUND3_LOG.md", recs, repo or REPO_ROOT)
    out["anchoring"] = anc
    if anc["mismatched"]:
        problems.append(anc["mismatched"][0])
    if verdict.startswith("PASS") and anc["uncovered_records"]:
        problems.append(f"a PASS needs anchors: records {anc['uncovered_records'][:5]} are not anchored within 7 days")
    out["verdict"] = f"NOT EVALUABLE — {'; '.join(problems)} (verdict as computed: {verdict})" if problems else verdict
    out["decided_at_item"] = st.verdict.get("decided_at") if st.verdict else at

    seq = cycles_in_order(st)
    items = [c for c in seq if c["resolved"] and c["kind"] in ("trade", "missed")]
    out["cycles"] = [{"expiry": c["expiry"].isoformat(), "kind": c["kind"], "resolved": c["resolved"],
                      "R": None if c.get("R") is None else round(c["R"], 4),
                      "net": None if c.get("net") is None else round(c["net"], 2)} for c in seq]
    rs = np.array([c["R"] for c in items], dtype=float)
    out["betting_wealth"] = float(betting_path(rs)[-1]) if len(rs) else 1.0
    out["descriptive"] = _descriptive([c["trade"] for c in items if c["kind"] == "trade"], items, recs)
    return out


def _flags(t: dict) -> list[str]:
    f = list(t.get("flags") or [])
    s = t.get("settlement") or {}
    src = str(s.get("source", ""))
    if "supplied by hand" in src:
        f.append("settlement from a bhavcopy supplied by hand")
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
                 "trades": len(trades), "missed_cycles": sum(x["kind"] == "missed" for x in items),
                 "limitation": "no exchange transaction charge is applied on settlement (none is known)"}
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


def anchor_line(journal_path: Path) -> str:
    recs = Journal(journal_path, "fno-paper").records()
    if not recs:
        raise ValueError("the journal is empty")
    r = recs[-1]
    return f"anchor: seq {r['seq']} sha256 {r['hash']} ({r['ts'][:19]})"
