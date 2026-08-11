#!/usr/bin/env python3
"""
Render a scan into the structured trade plan from OUTPUT_TEMPLATE.md.

Usage
-----
    python run_scan.py --json scans/today.json --equity 500000
    python render_plan.py scans/today.json > scans/today.md

This turns the scanner's JSON into the report a human actually reads: entry,
stop, targets, quantity, every charge line, net reward:risk, and the breakeven
win rate each plan demands.

What it does NOT fill in, because no script can:

  - the catalyst, with a source and a date
  - the bear case and the early-exit conditions
  - whether the shortlist is secretly one sector bet
  - F&O ban status, corporate actions, trading restrictions

Those sections are emitted with explicit TODO markers rather than left out, so a
plan that has not had judgement applied to it is visibly incomplete rather than
quietly passing as finished.
"""

from __future__ import annotations

import json
import sys


def px(x: float | None) -> str:
    """Display rounding only. The scanner's value is untouched in the JSON."""
    return "—" if x is None else f"{x:,.2f}"


def money(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"Rs{x:,.0f}" if abs(x) >= 1000 else f"Rs{x:,.2f}"


def render(rep: dict, top: int = 5) -> str:
    L: list[str] = []
    ms = rep.get("market_status", {})
    rg = rep.get("regime", {})
    cm = rep.get("cost_model", {})
    prov = rep.get("provider", {}).get("name", "unknown")
    shortlist = rep.get("shortlist", [])[:top]

    L += [
        "# Trade Plan",
        "",
        f"**Generated:** {ms.get('timestamp_ist','?')} · **Exchange:** {ms.get('exchange','?')} · "
        f"**Session:** {'OPEN' if ms.get('session_open') else 'closed'}",
        f"**Data source:** {prov}",
        "",
    ]

    if "MOCK" in prov.upper() or "NOT REAL" in prov.upper():
        L += [
            "> ## THIS PLAN IS NOT TRADEABLE",
            "> The prices below are synthetic. Every level, cost and return figure is",
            "> arithmetic performed on invented data. It demonstrates the format only.",
            "",
        ]

    L += [
        "## MARKET STATUS",
        "",
        f"- **Regime:** {rg.get('classification','unknown')}",
        f"- **Evidence:** {'; '.join(rg.get('evidence', [])) or 'none'}",
        "- **Market breadth:** UNAVAILABLE — needs advance/decline data",
        "- **Leading / weak sectors:** UNAVAILABLE — sector rotation not implemented",
        "- **Macro drivers:** TODO — judgement layer",
        "- **Event risks:** TODO — judgement layer",
        f"- **Last benchmark bar:** {rg.get('benchmark_last_bar','n/a')} "
        f"(stale {rg.get('benchmark_staleness_days','?')} days)",
        "",
        "## COST MODEL",
        "",
        f"- **Schedule:** {cm.get('schedule','n/a')} ({cm.get('segment','?')})",
        f"- **Slippage assumed:** {cm.get('slippage_pct_per_leg','?')}% per leg, against the trade",
        f"- **Rates verified:** {'yes' if cm.get('rates_verified') else '**NO — published defaults**'}",
        "",
    ]

    if not shortlist:
        L += [
            "## DECISION: NO TRADE",
            "",
            rep.get("decision_hint", "Nothing cleared the score floor."),
            "",
        ]
        if rep.get("errors"):
            L += ["### Why nothing was analysed", ""]
            for e in rep["errors"][:6]:
                L.append(f"- `{e['symbol']}` — {e['error']}")
            L.append("")
        return "\n".join(L)

    # ---- summary table ----
    L += [
        "## TOP OPPORTUNITIES",
        "",
        "| # | Ticker | Dir | Entry zone | Stop | T1 | T2 | Gross R:R | Net R:R | Breakeven win% | Score |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, c in enumerate(shortlist, 1):
        lv, ec = c.get("levels") or {}, c.get("economics") or {}
        L.append(
            f"| {i} | **{c['symbol']}** | {lv.get('direction','—')} | "
            f"{lv.get('entry_low','—')}–{lv.get('entry_high','—')} | {lv.get('stop','—')} | "
            f"{lv.get('target1','—')} | {lv.get('target2','—')} | "
            f"{ec.get('gross_reward_risk','—')} | {ec.get('net_reward_risk','—')} | "
            f"{ec.get('breakeven_win_rate_pct','—')}% | {c['score']} |"
        )
    L.append("")

    # ---- per-trade detail ----
    L += ["## TRADE DETAILS", ""]
    for i, c in enumerate(shortlist, 1):
        lv, ec, sz = c.get("levels") or {}, c.get("economics") or {}, c.get("sizing") or {}
        d = c.get("data", {})
        L += [
            f"### {i}. {c['symbol']} — {lv.get('direction','long')}",
            "",
            f"**Score {c['score']}/100** · coverage {int(c['coverage']*100)}% · {c['confidence']}",
            f"· data {d.get('freshness','?')}, last bar {d.get('last_bar','?')}",
            "",
            "| Level | Price |",
            "|---|---:|",
            f"| Reference | {px(lv.get('reference_price'))} |",
            f"| Entry zone | {lv.get('entry_low','—')} – {lv.get('entry_high','—')} |",
            f"| Breakout entry | {lv.get('breakout_entry','—')} |",
            f"| **Stop / invalidation** | **{lv.get('stop','—')}** |",
            f"| Target 1 | {lv.get('target1','—')} |",
            f"| Target 2 | {lv.get('target2','—')} |",
            f"| Stretch | {lv.get('stretch','—')} |",
            f"| Risk per share | {lv.get('risk_per_share','—')} |",
            "",
            f"**Target basis:** `{lv.get('target_basis','?')}` — "
            + (
                "measured to confirmed swing structure."
                if lv.get("target_basis") == "structure"
                else "no structure overhead; ratio is assumed, not measured."
            ),
            "",
        ]

        if ec:
            L += [
                f"**Economics** — {ec.get('quantity_basis','')}",
                "",
                "| | Gross | Net of all charges |",
                "|---|---:|---:|",
                f"| Quantity | {ec['qty']} | — |",
                f"| Capital | {money(ec['notional'])} | — |",
                f"| Profit at T2 | {money(ec['gross_profit_at_target'])} | **{money(ec['net_profit_at_target'])}** |",
                f"| Loss at stop | {money(ec['gross_loss_at_stop'])} | **{money(ec['net_loss_at_stop'])}** |",
                f"| Reward : risk | {ec['gross_reward_risk']} | **{ec['net_reward_risk']}** |",
                "",
                f"- Round-trip cost: {money(ec['cost_at_target'])} on a win, "
                f"{money(ec['cost_at_stop'])} on a loss",
                f"- Cost drag: **{ec['cost_drag_r']}R** — price must move "
                f"**{ec['breakeven_move_pct']}%** just to cover charges",
                f"- **Breakeven win rate: {ec['breakeven_win_rate_pct']}%** — "
                "below this the plan loses money over time",
                "",
                "**Expected value per trade, by win rate** (win rate is an input, "
                "not a forecast — measure it with `run_backtest.py`):",
                "",
                "| Win rate | " + " | ".join(ec["expected_value_by_win_rate"].keys()) + " |",
                "|---|" + "---:|" * len(ec["expected_value_by_win_rate"]),
                "| EV/trade | "
                + " | ".join(money(v) for v in ec["expected_value_by_win_rate"].values())
                + " |",
                "",
            ]
            for w in ec.get("warnings", []):
                L.append(f"> **!** {w}")
            if ec.get("warnings"):
                L.append("")

        if sz.get("warnings"):
            for w in sz["warnings"]:
                L.append(f"> **Sizing:** {w}")
            L.append("")

        L += ["**Score breakdown**", "", "```", c["explain"], "```", ""]

        if lv.get("notes"):
            L += ["**Level notes**", ""] + [f"- {n}" for n in lv["notes"]] + [""]

        L += [
            "**Judgement layer — not produced by the scanner:**",
            "",
            "- [ ] **Catalyst:** _TODO — name it, with source and date, or leave unscored_",
            "- [ ] **Fundamental / event evidence:** _TODO_",
            "- [ ] **Bear case:** _TODO — why this fails_",
            "- [ ] **Exit early if:** _TODO — conditions short of the stop_",
            "- [ ] **F&O ban / corporate action check:** _TODO — not automated_",
            "",
            "---",
            "",
        ]

    # ---- portfolio ----
    total_risk = sum(
        abs((c.get("economics") or {}).get("net_loss_at_stop", 0)) for c in shortlist
    )
    total_cap = sum((c.get("economics") or {}).get("notional", 0) for c in shortlist)
    L += [
        "## PORTFOLIO PLAN",
        "",
        f"- Positions on the shortlist: **{len(shortlist)}**",
        f"- Combined capital at risk: **{money(total_cap)}**",
        f"- Combined maximum loss if every stop hits: **{money(total_risk)}**",
        "- [ ] **Correlation check:** _TODO — the scanner ranks names independently "
        "and cannot see that three of these may be the same sector bet_",
        "- [ ] **Event concentration:** _TODO — are these all reporting the same week?_",
        "",
        "## DATA QUALITY / LIMITATIONS",
        "",
    ]
    for lim in rep.get("data_limitations", []):
        L.append(f"- {lim}")
    if rep.get("rejected"):
        L.append(f"- {len(rep['rejected'])} name(s) rejected on liquidity or staleness")
    L += [
        "- Charges are modelled; **slippage beyond the assumed figure is not**",
        "- No F&O ban list, holiday calendar or corporate-action detection",
        "",
        "## DECISION",
        "",
        f"{rep.get('decision_hint','')}",
        "",
        "**This is not a recommendation to trade.** It is arithmetic on price history "
        "with the judgement sections left deliberately blank. Fill them in, or treat "
        "the plan as unfinished.",
        "",
    ]
    return "\n".join(L)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    with open(sys.argv[1]) as fh:
        rep = json.load(fh)
    print(render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
