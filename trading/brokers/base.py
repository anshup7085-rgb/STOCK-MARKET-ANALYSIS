"""
Broker interface — trade-centric, because a swing position is a small
lifecycle, not a single order:

    submit_entry -> entry_status -> protect -> protection_status
                                  \\-> submit_exit -> exit_status

`protect` places the stop and the target as one one-cancels-other unit that
rests at the broker (a GTT OCO on Kite), so the position stays protected while
this program is not running. That is the property that matters most: a swing
trade spends nearly all its life with nobody watching.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

import pandas as pd

from trading.models import OrderUpdate, ProtectionUpdate, Trade


class BrokerError(RuntimeError):
    """The broker refused or failed. Never to be read as success."""


class ProtectionImpossible(BrokerError):
    """
    The stop/target pair cannot be placed because price is already beyond one
    of them. `side` is "stop" (exit now, the thesis is invalid) or "target"
    (exit now, the trade has paid).
    """

    def __init__(self, side: str, price: float, msg: str):
        super().__init__(msg)
        self.side = side
        self.price = price


class Throttle:
    """
    Minimum spacing between order-mutating calls.

    SEBI's retail-algo framework treats more than 10 orders/second as a
    registered-algo activity; this system targets a fraction of that, and the
    throttle makes it a property of the code rather than of good intentions.
    """

    def __init__(self, per_second: float, clock=time.monotonic, sleep=time.sleep):
        self.gap = 1.0 / per_second if per_second > 0 else 0.0
        self._last = None
        self._clock, self._sleep = clock, sleep

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            due = self._last + self.gap
            if now < due:
                self._sleep(due - now)
                now = due
        self._last = now


class Broker(ABC):
    name: str = "abstract"
    is_live: bool = False

    def sync(self, bars: dict[str, pd.DataFrame], asof: str, now=None) -> None:
        """Advance any simulation to `asof`. Live brokers need nothing: the exchange is the simulator."""

    @abstractmethod
    def submit_entry(self, t: Trade, session_open: bool) -> str: ...

    @abstractmethod
    def entry_status(self, t: Trade) -> OrderUpdate: ...

    @abstractmethod
    def cancel_entry(self, t: Trade) -> None: ...

    @abstractmethod
    def protect(self, t: Trade) -> str: ...

    @abstractmethod
    def protection_status(self, t: Trade) -> ProtectionUpdate: ...

    @abstractmethod
    def cancel_protection(self, t: Trade) -> None: ...

    @abstractmethod
    def submit_exit(self, t: Trade, limit_price: float, session_open: bool, tag: str) -> str: ...

    @abstractmethod
    def exit_status(self, t: Trade) -> OrderUpdate: ...

    def cancel_order(self, order_id: str) -> None:
        """Cancel one working order by id (a stranded protective leg, a stale exit)."""
        raise BrokerError(f"{self.name} cannot cancel order {order_id} individually")

    # Optional surface — live brokers implement these, paper returns None.

    def find_order_by_tag(self, tag: str) -> str | None:
        return None

    def holdings(self) -> dict[str, int] | None:
        return None

    def available_cash(self) -> float | None:
        return None

    def last_price(self, symbol: str) -> float | None:
        return None

    def tick_size(self, symbol: str) -> float:
        return 0.05
