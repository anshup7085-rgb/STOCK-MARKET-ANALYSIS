"""
`fno evaluate`: the round-3 statistic and the descriptive record, computed from
the journal alone (research/PREREGISTRATION_3.md, section 12).

The journal, not the book, is the evidence: a book can be edited, a journal
that verifies cannot be without the chain saying so. An evaluation of a journal
that does not verify says so first and decides nothing.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from research.fno_stats import betting_path
from trading.fno.strategy_v import REGISTERED, VParams
from trading.journal import Journal, verify_file


def evaluate(journal_path: Path, p: VParams = REGISTERED) -> dict:
    vr = verify_file(journal_path)
    recs = Journal(journal_path, "fno-paper").records()
    out: dict = {"journal": str(journal_path), "journal_verifies": vr.ok, "journal_records": vr.records,
                 "journal_problems": vr.problems[:10]}
    init = next((r for r in recs if r["type"] == "FNO_INIT"), None)
    if init is None:
        out["verdict"] = "NO BOOK — no FNO_INIT record"
        return out
    start = float(init["payload"]["starting_equity"])
    out.update(starting_equity=start, feed=init["payload"].get("feed"), mode=init.get("mode"))

    entries = {r["payload"]["trade"]["id"]: r["payload"]["trade"] for r in recs if r["type"] == "FNO_ENTRY"}
    closes = {r["payload"]["trade_id"]: {**r["payload"], "kind": r["type"]}
              for r in recs if r["type"] in ("FNO_SETTLED", "FNO_FLATTENED")}
    order = sorted(entries.values(), key=lambda t: t["expiry"])
    # Section 12 orders trades by expiry. Only the run of settled trades before the
    # first unsettled one is evaluated, so a delayed settlement never reorders R.
    done = []
    for t in order:
        if t["id"] not in closes:
            break
        done.append((t, closes[t["id"]]))
    out["open_or_unsettled"] = [t["id"] for t in order if t["id"] not in closes]

    r = np.array([c["R"] for _, c in done], dtype=float)
    net = np.array([c["net"] for _, c in done], dtype=float)
    n = len(r)
    out["settled"] = n
    out["verdict"], out["decided_at_trade"] = _outcome(r, net, start, p)
    if not vr.ok:
        out["verdict"] = "UNDECIDABLE — the journal does not verify; " + out["verdict"]
    path = betting_path(r) if n else np.array([])
    out["wealth_K"] = float(path[-1]) if n else 1.0
    out["wealth_needed"] = p.threshold

    if n:
        wins = net > 0
        cum = np.cumsum(net)
        peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
        credits = np.array([t["credit"] * t["qty"] for t, _ in done])
        costs = np.array([t["entry_costs_total"] + _close_costs(c) for t, c in done])
        be = [_breakeven(t) for t, _ in done]
        slip_r = [_fill_vs_mid(t) for t, _ in done]
        out["descriptive"] = {
            "label": "DESCRIPTIVE — not a test; the verdict above is the only decision",
            "win_rate": float(wins.mean()), "mean_R": float(r.mean()), "mean_net": float(net.mean()),
            "total_net": float(net.sum()), "max_drawdown": float((peak - cum).max()),
            "mean_breakeven_win_rate": float(np.mean(be)),
            "costs_share_of_credit": float(costs.sum() / credits.sum()) if credits.sum() else None,
            "mean_fill_vs_mid_R": float(np.mean(slip_r)),
            "paper_equity": start + float(net.sum()),
        }
        out["trades"] = [{"id": t["id"], "lots": t["lots"], "credit": round(t["credit"], 2),
                          "max_loss": round(t["max_loss"], 2), "net": round(c["net"], 2), "R": round(c["R"], 4),
                          "how": "flattened (off-rule)" if c["kind"] == "FNO_FLATTENED" else "settled"}
                         for t, c in done]

    skips: dict[str, int] = {}
    for rec in recs:
        if rec["type"] == "FNO_NO_TRADE":
            code = rec["payload"].get("code", "?")
            skips[code] = skips.get(code, 0) + 1
    out["passes_without_trade_by_reason"] = skips
    out["cycles_without_trade"] = [rec["payload"]["expiry"] for rec in recs if rec["type"] == "FNO_CYCLE_NO_TRADE"]
    out["runner_days"] = sorted({rec["asof"] for rec in recs if rec["type"] == "FNO_RUNNER_START"})
    return out


def _outcome(r: np.ndarray, net: np.ndarray, start: float, p: VParams) -> tuple[str, int | None]:
    """The first of PASS / FAIL (harm, futility, no evidence) to occur, in trade order."""
    if not len(r):
        return "UNDECIDED — no settled trades yet", None
    path = betting_path(r)
    cum = np.cumsum(net)
    for i in range(len(r)):
        k = i + 1
        if cum[i] <= -p.harm_stop_frac * start:
            return f"FAIL — harm stop at trade {k} (realised Rs{cum[i]:,.0f})", k
        if path[i] >= p.threshold:
            return f"PASS — betting wealth {path[i]:.1f} >= {p.threshold:g} at trade {k}", k
        if k == p.futility_n and r[:k].mean() <= 0:
            return f"FAIL — futility: mean R of the first {k} trades is {r[:k].mean():+.3f}", k
        if k == p.max_trades:
            return f"FAIL — no evidence: {k} trades without a PASS", k
    return f"UNDECIDED — {len(r)} settled trade(s); wealth {path[-1]:.2f} of {p.threshold:g} needed", None


def _close_costs(c: dict) -> float:
    d = c.get("detail") or {}
    return float(d.get("settlement_costs_total") or d.get("exit_costs_total") or 0.0)


def _breakeven(t: dict) -> float:
    """Win rate at which this trade breaks even: lose M, or win the credit less entry costs."""
    win = t["credit"] * t["qty"] - t["entry_costs_total"]
    return 1.0 if win <= 0 else t["max_loss"] / (win + t["max_loss"])


def _fill_vs_mid(t: dict) -> float:
    """Points given up against the mid on the four entry fills, in units of M."""
    pts = sum(abs(g["price"] - g["mid"]) for g in t["legs"] if g.get("mid") is not None)
    return pts * t["qty"] / t["max_loss"]
