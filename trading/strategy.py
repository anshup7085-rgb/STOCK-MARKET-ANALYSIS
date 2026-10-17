"""
From scan to sized, priced trade intents.

The strategy uses the scanner's levels and the backtest's target and horizon.
It differs from the backtest in two honest ways — entry is a next-session
limit order that may not fill (the backtest enters at the signal close), and
only names passing the net R:R floor and the risk limits trade — which is why
`replay` exists to measure the system itself:

  - long only, cash segment (CNC delivery)
  - a buy LIMIT at the top of the scanner's entry zone, valid for one session
  - stop at the scanner's stop; the stop order is a stop-LIMIT a small buffer
    below it (Kite's GTT legs are limit orders)
  - exit at the scanner's Target 2, or after `max_hold_bars` sessions — the
    same target and horizon run_backtest.py measures

Sizing is done on the NET loss at the stop: after charges and slippage, the
loss if the stop fills must fit inside the risk budget. A gap straight through
the stop can still lose more. No sizing rule removes that; RISK_POLICY.md says
so and so does this comment.
"""

from __future__ import annotations

import math

from market.charges import trade_economics
from trading.config import TradingConfig
from trading.models import TradeIntent, round_to_tick


def _signal_bar(c: dict) -> str:
    return str(c.get("data", {}).get("last_bar", ""))[:10]


def intents_from_scan(
    report: dict, cfg: TradingConfig, equity: float, risk_pct: float,
) -> tuple[list[TradeIntent], list[dict]]:
    """
    Build intents for every shortlisted name, best score first.

    Only construction failures are skipped here. Everything that is a policy
    judgement — floors, limits, correlation — is left to the risk engine, so
    each rejection reaches the journal with its reason.
    """
    intents: list[TradeIntent] = []
    skipped: list[dict] = []
    regime = (report.get("regime") or {}).get("classification", "unknown")
    budget = equity * risk_pct / 100.0
    pos_cap = equity * cfg.max_single_position_pct / 100.0

    for c in report.get("shortlist", []):
        sym = c["symbol"]
        lv = c.get("levels")
        if not lv:
            skipped.append({"symbol": sym, "reason": "scanner could not build levels"})
            continue
        if lv.get("direction") != "long":
            skipped.append({"symbol": sym, "reason": "only long CNC trades are executed"})
            continue

        raw_entry = {
            "zone_high": lv["entry_high"],
            "reference": lv["reference_price"],
            "zone_low": lv["entry_low"],
        }.get(cfg.entry_at, lv["entry_high"])
        entry = round_to_tick(raw_entry, how="down")
        stop = round_to_tick(lv["stop"])
        stop_limit = round_to_tick(stop * (1 - cfg.stop_limit_buffer_pct / 100.0), how="down")
        target = round_to_tick(lv["target2"])
        risk_ps = entry - stop
        if risk_ps <= 0 or target <= entry:
            skipped.append({"symbol": sym, "reason": f"levels out of order at entry {entry}"})
            continue

        qty = min(math.floor(budget / risk_ps), math.floor(pos_cap / entry))
        econ = None
        # Shrink until the NET loss at the stop fits the budget. Charges are a
        # few percent of risk, so this converges in a step or two.
        while qty > 0:
            econ = trade_economics(entry, stop, target, qty,
                                   segment=cfg.segment, slippage_pct=cfg.slippage_pct)
            if econ is None or abs(econ.net_loss_at_stop) <= budget:
                break
            qty = min(qty - 1, math.floor(qty * budget / abs(econ.net_loss_at_stop)))
        if qty <= 0 or econ is None:
            skipped.append({
                "symbol": sym,
                "reason": f"risk budget Rs{budget:,.0f} cannot fund one share at "
                          f"Rs{risk_ps:,.2f} risk per share",
            })
            continue

        intents.append(TradeIntent(
            symbol=sym,
            qty=qty,
            entry_limit=entry,
            stop=stop,
            stop_limit=stop_limit,
            target=target,
            reference_price=round(lv["reference_price"], 2),
            score=c["score"],
            coverage=c["coverage"],
            gross_rr=econ.gross_reward_risk,
            net_rr=econ.net_reward_risk,
            breakeven_win_rate_pct=econ.breakeven_win_rate_pct,
            est_max_loss=round(abs(econ.net_loss_at_stop), 2),
            est_charges=econ.cost_at_stop,
            target_basis=lv.get("target_basis", "?"),
            signal_bar=_signal_bar(c),
            data_source=c.get("data", {}).get("source", "?"),
            data_freshness=c.get("data", {}).get("freshness", "?"),
            regime=regime,
            reason=(
                f"score {c['score']}/100 (coverage {c['coverage']:.0%}, {c['confidence']}); "
                f"regime {regime}; T2 on {lv.get('target_basis')}; "
                f"net R:R {econ.net_reward_risk} (gross {econ.gross_reward_risk}); "
                f"breakeven win rate {econ.breakeven_win_rate_pct}%"
            ),
        ))
    intents.sort(key=lambda i: i.score, reverse=True)
    return intents, skipped
