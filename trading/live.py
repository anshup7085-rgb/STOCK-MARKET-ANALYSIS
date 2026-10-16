"""
The live opt-in. Everything that must be true before a real order can exist.

  1. LIVE_TRADING_ACK in the environment equals the phrase below, typed by you.
  2. Kite credentials minted by hand today (the broker adapter enforces this).
  3. ROADMAP Gates 1 and 2 pass on recorded evidence — OR, for per-order
     confirmed trading only, you pass --override-gates with a written reason,
     which is journaled before anything else happens. Autonomous execution has
     no override: it requires the gates.
  4. Autonomous execution additionally requires a policy file you wrote and
     signed, and its limits can only tighten the configured ones.

None of this makes trading safe. It makes it deliberate, bounded and auditable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from trading.config import TradingConfig
from trading.confirm import Confirmer, PolicyConfirmer, TerminalConfirmer
from trading.gates import GateReport, gate1_backtest, gate2_paper
from trading.journal import Journal

LIVE_ACK_PHRASE = "I accept that this places real orders with real money"


class LiveRefused(RuntimeError):
    pass


@dataclass
class LiveAuthorisation:
    confirmer: Confirmer
    gates: list[GateReport]
    overridden: bool
    policy: dict | None


def load_policy(path: Path) -> dict:
    """
    An autonomous-execution policy, written by the account holder. Example:

        {"autonomous_live": true,
         "acknowledged_by": "Your Name",
         "acknowledged_on": "2026-10-16",
         "max_risk_per_trade_pct": 0.25,
         "max_open_positions": 3,
         "max_orders_per_day": 6}
    """
    try:
        pol = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise LiveRefused(f"autonomous policy unreadable at {path}: {exc}") from exc
    if pol.get("autonomous_live") is not True:
        raise LiveRefused("policy does not set \"autonomous_live\": true")
    for k in ("acknowledged_by", "acknowledged_on"):
        if not str(pol.get(k) or "").strip():
            raise LiveRefused(f"policy is missing '{k}' — an unsigned policy is not a decision")
    return pol


def apply_policy_limits(cfg: TradingConfig, pol: dict) -> None:
    """Policy limits can only tighten."""
    if "max_risk_per_trade_pct" in pol:
        cfg.live_max_risk_per_trade_pct = min(cfg.live_max_risk_per_trade_pct, float(pol["max_risk_per_trade_pct"]))
    if "max_open_positions" in pol:
        cfg.max_open_positions = min(cfg.max_open_positions, int(pol["max_open_positions"]))
    if "max_orders_per_day" in pol:
        cfg.max_orders_per_day = min(cfg.max_orders_per_day, int(pol["max_orders_per_day"]))


def authorise_live(
    cfg: TradingConfig,
    journal: Journal,
    *,
    backtest_json: str | None,
    autonomous: bool,
    override_reason: str | None,
    env: dict | None = None,
    confirmer: Confirmer | None = None,
) -> LiveAuthorisation:
    env = env if env is not None else dict(os.environ)
    if env.get("LIVE_TRADING_ACK", "").strip() != LIVE_ACK_PHRASE:
        raise LiveRefused(
            "Live mode needs LIVE_TRADING_ACK set to exactly:\n"
            f'  "{LIVE_ACK_PHRASE}"\n'
            "Set it yourself, in the shell that runs the system."
        )

    paper_journal = cfg.state_root / "paper" / "journal.jsonl"
    gates = [gate1_backtest(backtest_json), gate2_paper(paper_journal)]
    gates_ok = all(g.passed for g in gates)
    failures = [f"{g.gate} / {c.name}: {c.detail}" for g in gates for c in g.checks if c.status == "fail"]

    if autonomous:
        if not gates_ok:
            raise LiveRefused(
                "Autonomous live execution requires ROADMAP Gates 1 and 2 to pass. "
                "There is no override for autonomous mode. Failing:\n  " + "\n  ".join(failures)
            )
        pol = load_policy(cfg.state_dir / "execution_policy.json")
        apply_policy_limits(cfg, pol)
        journal.append("LIVE_AUTHORISED", autonomous=True, policy=pol,
                       gates=[g.text() for g in gates])
        return LiveAuthorisation(PolicyConfirmer(pol), gates, False, pol)

    overridden = False
    if not gates_ok:
        reason = (override_reason or "").strip()
        if len(reason) < 20:
            raise LiveRefused(
                "ROADMAP gates are not passed, so live trading is refused. To trade with "
                "per-order confirmation anyway, pass --override-gates \"<your reason, in a "
                "sentence>\". It is journaled before anything else. Failing:\n  "
                + "\n  ".join(failures)
            )
        journal.append("GATE_OVERRIDE", reason=reason, failing=failures)
        overridden = True
    journal.append("LIVE_AUTHORISED", autonomous=False, overridden=overridden,
                   gates=[g.text() for g in gates])
    return LiveAuthorisation(confirmer or TerminalConfirmer(), gates, overridden, None)
