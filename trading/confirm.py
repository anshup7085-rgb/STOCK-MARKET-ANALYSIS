"""
Order confirmation.

PROMPT.md: before any live order, display instrument, quantity, order type,
price/trigger, stop/invalidation, estimated maximum loss, estimated charges and
the reason — and require explicit confirmation immediately before placing it,
unless the user has deliberately configured a compliant autonomous policy.

What a confirmation covers: the whole PLAN on the ticket — the entry, the
stop/target pair placed once it fills, and the time or emergency exit the plan
already names. Those follow-on orders only ever reduce or close the confirmed
position, which is what a bracket order at any broker does. Anything not on
the ticket (flattening the book, a manual order) needs its own confirmation.

Confirmation is by typing the order back — `BUY 12 TCS` — not by pressing y.
A reflexive keypress is not a decision.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod

from trading.config import TradingConfig
from trading.models import TradeIntent


def build_ticket(it: TradeIntent, cfg: TradingConfig, equity: float) -> dict:
    live = cfg.is_live
    return {
        "MODE": "LIVE — REAL MONEY" if live else cfg.mode.upper() + " — simulated, no real order",
        "Instrument": f"NSE:{it.symbol} — equity, CNC delivery",
        "Side / quantity": f"BUY {it.qty}",
        "Order type / price": f"LIMIT {it.entry_limit:.2f}, valid today only",
        "Stop / invalidation": (
            f"trigger {it.stop:.2f}, sells at limit {it.stop_limit:.2f} "
            "(GTT one-cancels-other, placed once the entry fills)"
        ),
        "Target": f"{it.target:.2f} — T2, {it.target_basis} (same GTT)",
        "Time exit": f"after {cfg.max_hold_bars} sessions if neither leg has filled",
        "Capital": f"Rs{it.notional:,.0f} ({it.notional / equity:.1%} of Rs{equity:,.0f})",
        "Estimated maximum loss": (
            f"Rs{it.est_max_loss:,.0f} net of charges and {cfg.slippage_pct}% slippage. "
            "A gap through the stop can lose more; a gap through the stop-LIMIT can "
            "leave the position open."
        ),
        "Estimated charges": f"Rs{it.est_charges:,.2f} round trip at the stop (UNVERIFIED default rates)",
        "Reward : risk": (
            f"net {it.net_rr} (gross {it.gross_rr}); breakeven win rate "
            f"{it.breakeven_win_rate_pct}% — the system's real win rate is not yet measured"
        ),
        "Reason": it.reason,
        "Data": f"{it.data_source} ({it.data_freshness}), signal bar {it.signal_bar}",
    }


def render_ticket(ticket: dict) -> str:
    w = max(len(k) for k in ticket)
    lines = ["", "=" * 72]
    lines += [f"{k:<{w}}  {v}" for k, v in ticket.items()]
    lines.append("=" * 72)
    return "\n".join(lines)


class Confirmer(ABC):
    name = "abstract"

    @abstractmethod
    def confirm(self, ticket: dict, phrase: str) -> tuple[bool, str]:
        """(approved, how) — `how` goes in the journal verbatim."""


class SimulatedConfirmer(Confirmer):
    """Paper and replay: nothing real happens, so nothing needs a person."""

    name = "simulated"

    def confirm(self, ticket: dict, phrase: str) -> tuple[bool, str]:
        return True, "simulated mode — no real order"


class DenyConfirmer(Confirmer):
    name = "deny"

    def __init__(self, reason: str):
        self.reason = reason

    def confirm(self, ticket: dict, phrase: str) -> tuple[bool, str]:
        return False, self.reason


class TerminalConfirmer(Confirmer):
    """A person at a terminal types the order back, or it does not go."""

    name = "terminal"

    def __init__(self, input_fn=input, output=print, isatty=None):
        self._input = input_fn
        self._out = output
        self._isatty = isatty or (lambda: sys.stdin.isatty())

    def confirm(self, ticket: dict, phrase: str) -> tuple[bool, str]:
        if not self._isatty():
            return False, (
                "no interactive terminal — a live order needs a person to confirm it. "
                "Run the cycle by hand, or configure an autonomous policy (TRADING.md)."
            )
        self._out(render_ticket(ticket))
        try:
            typed = self._input(f"Type exactly '{phrase}' to place this order, anything else to skip: ")
        except EOFError:
            return False, "input closed before confirmation"
        if typed.strip() == phrase:
            return True, f"confirmed at the terminal by typing '{phrase}'"
        return False, f"declined at the terminal (typed {typed.strip()!r})"


class PolicyConfirmer(Confirmer):
    """Autonomous live execution under a written, acknowledged policy."""

    name = "autonomous-policy"

    def __init__(self, policy: dict):
        self.policy = policy

    def confirm(self, ticket: dict, phrase: str) -> tuple[bool, str]:
        return True, (
            f"autonomous policy acknowledged by {self.policy['acknowledged_by']} "
            f"on {self.policy['acknowledged_on']}"
        )
