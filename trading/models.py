"""
Domain objects for the execution layer.

The unit of work is a Trade, not an order: an entry, the protective exit that
must exist whenever shares are held, and whatever exit eventually closes it.
A swing position lives for days, so the question the engine asks every cycle is
"what state is each trade in, and is it protected?" — order-level bookkeeping
hangs off that.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Literal

TradeStatus = Literal["PENDING_ENTRY", "OPEN", "EXITING", "CLOSED", "CANCELLED"]

# Order states, normalised across brokers.
OrderState = Literal["SUBMITTED", "OPEN", "COMPLETE", "CANCELLED", "REJECTED", "EXPIRED", "UNKNOWN"]

# Protective (stop + target, one-cancels-other) states.
ProtectionState = Literal[
    "NONE",               # nothing placed
    "ACTIVE",             # resting, neither leg triggered
    "TRIGGERED_OPEN",     # a leg triggered and its order is working
    "STOP_FILLED",
    "TARGET_FILLED",
    "TRIGGERED_UNFILLED", # a leg triggered and its order died unfilled: UNPROTECTED
    "CANCELLED",          # gone without triggering (expired, deleted by hand)
    "UNKNOWN",            # the broker said something we could not parse
]

DEFAULT_TICK = 0.05


def round_to_tick(price: float, tick: float = DEFAULT_TICK, how: str = "nearest") -> float:
    """
    Snap a price to the exchange tick. An off-tick limit is rejected by the
    exchange, so every price this layer sends goes through here.

    'down' for prices that must not be exceeded (a buy limit, a stop-sell
    limit); 'up' for ones that must not be undercut.
    """
    if tick <= 0:
        return round(price, 2)
    n = price / tick
    if how == "down":
        n = math.floor(n + 1e-9)
    elif how == "up":
        n = math.ceil(n - 1e-9)
    else:
        n = round(n)
    return round(n * tick, 2)


@dataclass
class OrderUpdate:
    state: OrderState
    filled_qty: int = 0
    avg_price: float | None = None
    message: str = ""
    at: str | None = None        # bar date (paper) or broker timestamp (live)


@dataclass
class ProtectionUpdate:
    state: ProtectionState
    fill_price: float | None = None
    filled_qty: int = 0
    message: str = ""
    at: str | None = None
    leg: str | None = None        # "STOP" | "TARGET" once a leg has triggered
    order_id: str | None = None   # the order a triggered leg placed, when known


@dataclass
class TradeIntent:
    """A plan the scanner produced, sized and priced, not yet risk-checked."""

    symbol: str
    qty: int
    entry_limit: float
    stop: float              # trigger
    stop_limit: float        # the limit the stop order is sent at, <= stop
    target: float
    reference_price: float
    score: float
    coverage: float
    gross_rr: float
    net_rr: float
    breakeven_win_rate_pct: float
    est_max_loss: float      # net of charges and assumed slippage, at the stop
    est_charges: float
    target_basis: str
    signal_bar: str          # date of the completed bar the signal was built on
    data_source: str
    data_freshness: str
    regime: str
    reason: str
    direction: str = "long"
    product: str = "CNC"

    @property
    def risk_per_share(self) -> float:
        return self.entry_limit - self.stop

    @property
    def notional(self) -> float:
        return self.qty * self.entry_limit


@dataclass
class ExitLeg:
    qty: int
    price: float
    at: str
    reason: str              # "stop" | "target" | "time" | "emergency" | "manual"


@dataclass
class Trade:
    trade_id: str
    symbol: str
    direction: str
    status: TradeStatus
    qty: int                 # planned
    entry_limit: float
    stop: float
    stop_limit: float
    target: float
    reference_price: float
    signal_bar: str
    score: float
    reason: str
    created_at: str
    max_hold_bars: int
    tag: str                 # client tag sent to the broker; idempotency key

    entry_order_id: str | None = None
    filled_qty: int = 0
    entry_price: float | None = None
    entry_at: str | None = None

    protection_id: str | None = None
    protection_state: str = "NONE"

    exit_order_id: str | None = None
    exit_reason_pending: str | None = None
    exit_attempts: int = 0
    exit_tag: str | None = None          # last exit tag sent; resolves an exit whose outcome is unknown
    exit_tag_at: str | None = None       # wall-clock time that tag was sent
    exit_limit: float | None = None      # limit of the working exit order
    exit_rejections: dict[str, int] = field(default_factory=dict)   # date -> rejected exits that day
    # Bumped whenever a protective pair is discarded, so a broker that keys
    # protective orders by reference id (Groww) gets fresh ids for the next
    # pair — and the SAME ids when retrying a pair that was half placed.
    protect_attempts: int = 0
    exits: list[ExitLeg] = field(default_factory=list)

    bars_held: int = 0
    gross_pnl: float | None = None
    charges: float | None = None
    net_pnl: float | None = None
    r_multiple: float | None = None
    net_r_multiple: float | None = None
    closed_at: str | None = None
    alerts: list[str] = field(default_factory=list)

    @property
    def remaining_qty(self) -> int:
        return self.filled_qty - sum(leg.qty for leg in self.exits)

    @property
    def active(self) -> bool:
        return self.status in ("PENDING_ENTRY", "OPEN", "EXITING")

    @property
    def exit_price(self) -> float | None:
        q = sum(leg.qty for leg in self.exits)
        if not q:
            return None
        return sum(leg.qty * leg.price for leg in self.exits) / q

    def open_risk(self) -> float:
        """Rupees lost if the stop fills exactly at its trigger, before costs."""
        if self.status == "PENDING_ENTRY":
            return self.qty * max(0.0, self.entry_limit - self.stop)
        if self.status in ("OPEN", "EXITING"):
            px = self.entry_price if self.entry_price is not None else self.entry_limit
            return self.remaining_qty * max(0.0, px - self.stop)
        return 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Trade":
        d = dict(d)
        d["exits"] = [ExitLeg(**e) for e in d.get("exits", [])]
        return cls(**d)
