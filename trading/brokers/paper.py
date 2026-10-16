"""
Paper broker — simulated fills on daily bars.

The fill rules are the backtest's rules, made order-shaped, and every
ambiguity resolves AGAINST the trade:

  * Causality. An order placed after bar D can only fill on bars dated after
    D. The bar the signal was computed on is never used to fill it.
  * Entry (BUY LIMIT L, valid one session). Open at or below L fills at the
    open plus slippage, capped at L. Otherwise it fills at L only if the low
    trades THROUGH L — a touch is not a fill; you may be last in the queue.
    Untouched, it expires.
  * Protection (stop trigger S with a SELL LIMIT at S' <= S, plus a target T,
    one-cancels-other) is attached the moment the entry fills, modelling a
    live session that places the GTT as soon as the fill is seen.
      - On the fill bar only the stop is checked. Daily bars cannot say whether
        the high came before or after the fill, so the target is never
        credited on the day of entry; the stop always can be.
      - An open below S triggers at the open. The leg is a limit at S': it
        fills near the open if the open is still above S', at S' if price
        trades back up to it, and otherwise NOT AT ALL — the position is left
        unprotected, exactly as a real stop-limit leaves it after a gap.
      - When one bar spans both S and T, the stop is taken.
  * Exit (SELL LIMIT, one session). Mirror of the entry.

Charges are not deducted here; the engine charges every closed trade through
market/charges.py so paper and live P&L are computed the same way.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from market.scan import bar_date
from trading.brokers.base import Broker, BrokerError
from trading.models import OrderUpdate, ProtectionUpdate, Trade


@dataclass
class SimOrder:
    id: str
    symbol: str
    side: str                    # BUY | SELL
    qty: int
    limit: float
    placed_after: str            # fills only on bars dated after this
    state: str = "OPEN"          # OPEN | COMPLETE | EXPIRED | CANCELLED
    filled_qty: int = 0
    avg_price: float | None = None
    at: str | None = None
    message: str = ""
    attach: dict | None = None   # entries: {"stop", "stop_limit", "target"}
    oco_id: str | None = None
    tag: str = ""


@dataclass
class SimOco:
    id: str
    symbol: str
    qty: int
    stop: float
    stop_limit: float
    target: float
    placed_after: str
    fill_bar: str | None = None      # entry fill date: evaluated from here, stop-only on it
    entry_fill: float | None = None
    checked_through: str | None = None
    state: str = "ACTIVE"
    fill_price: float | None = None
    at: str | None = None
    message: str = ""


class PaperBroker(Broker):
    name = "PAPER — simulated fills on daily bars"
    is_live = False

    def __init__(self, state_path: str | Path, slippage_pct: float = 0.05):
        self.path = Path(state_path)
        self.slip = slippage_pct / 100.0
        self.orders: dict[str, SimOrder] = {}
        self.ocos: dict[str, SimOco] = {}
        self.seq = 0
        self.asof: str | None = None
        self._load()

    # -- persistence ------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text())
        self.seq = raw.get("seq", 0)
        self.asof = raw.get("asof")
        self.orders = {k: SimOrder(**v) for k, v in raw.get("orders", {}).items()}
        self.ocos = {k: SimOco(**v) for k, v in raw.get("ocos", {}).items()}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "seq": self.seq,
            "asof": self.asof,
            "orders": {k: asdict(v) for k, v in self.orders.items()},
            "ocos": {k: asdict(v) for k, v in self.ocos.items()},
        }, indent=1))
        os.replace(tmp, self.path)

    def _new_id(self, prefix: str) -> str:
        self.seq += 1
        return f"P{prefix}{self.seq:06d}"

    def _placed_after(self, t: Trade) -> str:
        return max(filter(None, [self.asof, t.signal_bar]))

    # -- simulation -------------------------------------------------------

    def sync(self, bars: dict[str, pd.DataFrame], asof: str) -> None:
        self.asof = asof
        for o in [o for o in self.orders.values() if o.state == "OPEN" and o.side == "BUY"]:
            self._run_order(o, bars.get(o.symbol), asof)
        for oco in [x for x in self.ocos.values() if x.state == "ACTIVE"]:
            self._run_oco(oco, bars.get(oco.symbol), asof)
        for o in [o for o in self.orders.values() if o.state == "OPEN" and o.side == "SELL"]:
            self._run_order(o, bars.get(o.symbol), asof)
        self._save()

    @staticmethod
    def _rows(df: pd.DataFrame | None, after: str, through: str):
        if df is None or df.empty:
            return
        for ts, row in df.iterrows():
            d = bar_date(ts).isoformat()
            if after < d <= through:
                yield d, float(row["open"]), float(row["high"]), float(row["low"])

    def _run_order(self, o: SimOrder, df, asof: str) -> None:
        for d, op, hi, lo in self._rows(df, o.placed_after, asof):
            px = None
            if o.side == "BUY":
                if op <= o.limit:
                    px = min(op * (1 + self.slip), o.limit)
                elif lo < o.limit:
                    px = o.limit
            else:
                if op >= o.limit:
                    px = max(op * (1 - self.slip), o.limit)
                elif hi > o.limit:
                    px = o.limit
            if px is None:
                o.state, o.at, o.message = "EXPIRED", d, "limit not traded through; day order expired"
                return
            o.state, o.filled_qty, o.avg_price, o.at = "COMPLETE", o.qty, round(px, 2), d
            if o.side == "BUY" and o.attach:
                oco = SimOco(
                    id=self._new_id("G"), symbol=o.symbol, qty=o.qty,
                    stop=o.attach["stop"], stop_limit=o.attach["stop_limit"],
                    target=o.attach["target"], placed_after=d,
                    fill_bar=d, entry_fill=o.avg_price,
                )
                self.ocos[oco.id] = oco
                o.oco_id = oco.id
                self._run_oco(oco, df, asof)
            return

    def _stop_leg(self, trigger_px: float, hi: float, stop_limit: float):
        if trigger_px >= stop_limit:
            return "STOP_FILLED", max(trigger_px * (1 - self.slip), stop_limit)
        if hi >= stop_limit:
            return "STOP_FILLED", stop_limit
        return "TRIGGERED_UNFILLED", None

    def _run_oco(self, x: SimOco, df, asof: str) -> None:
        if df is None or df.empty:
            return
        for ts, row in df.iterrows():
            d = bar_date(ts).isoformat()
            if d > asof:
                break
            if x.fill_bar is not None:
                if d < x.fill_bar:
                    continue
            elif d <= x.placed_after:
                continue
            if x.checked_through is not None and d <= x.checked_through:
                continue
            op, hi, lo = float(row["open"]), float(row["high"]), float(row["low"])
            s, sl, tgt = x.stop, x.stop_limit, x.target
            res = None
            if d == x.fill_bar:
                start = x.entry_fill if x.entry_fill is not None else op
                if start <= s:
                    res = self._stop_leg(start, hi, sl)
                elif lo <= s:
                    res = ("STOP_FILLED", max(s * (1 - self.slip), sl))
            elif op <= s:
                res = self._stop_leg(op, hi, sl)
            elif op >= tgt:
                res = ("TARGET_FILLED", max(op * (1 - self.slip), tgt))
            elif lo <= s:
                res = ("STOP_FILLED", max(s * (1 - self.slip), sl))
            elif hi > tgt:
                res = ("TARGET_FILLED", tgt)
            x.checked_through = d
            if res:
                x.state, px = res
                x.fill_price = round(px, 2) if px is not None else None
                x.at = d
                if x.state == "TRIGGERED_UNFILLED":
                    x.message = (f"opened {op} below stop-limit {sl}; the limit leg never "
                                 "traded and expired — position UNPROTECTED")
                return

    # -- Broker surface ---------------------------------------------------

    def submit_entry(self, t: Trade, session_open: bool) -> str:
        o = SimOrder(
            id=self._new_id("E"), symbol=t.symbol, side="BUY", qty=t.qty,
            limit=t.entry_limit, placed_after=self._placed_after(t), tag=t.tag,
            attach={"stop": t.stop, "stop_limit": t.stop_limit, "target": t.target},
        )
        self.orders[o.id] = o
        self._save()
        return o.id

    def _order(self, oid: str | None) -> SimOrder:
        if not oid or oid not in self.orders:
            raise BrokerError(f"paper order {oid} not found")
        return self.orders[oid]

    @staticmethod
    def _update(o: SimOrder) -> OrderUpdate:
        return OrderUpdate(o.state, o.filled_qty, o.avg_price, o.message, o.at)

    def entry_status(self, t: Trade) -> OrderUpdate:
        return self._update(self._order(t.entry_order_id))

    def cancel_entry(self, t: Trade) -> None:
        o = self._order(t.entry_order_id)
        if o.state == "OPEN":
            o.state, o.message = "CANCELLED", "cancelled by engine"
            self._save()

    def protect(self, t: Trade) -> str:
        o = self.orders.get(t.entry_order_id or "")
        if o and o.oco_id and t.protection_id is None:
            # Attached at fill time. It may already have resolved on the fill
            # bar; protection_status will say how.
            return o.oco_id
        x = SimOco(
            id=self._new_id("G"), symbol=t.symbol, qty=t.remaining_qty,
            stop=t.stop, stop_limit=t.stop_limit, target=t.target,
            placed_after=self.asof or t.signal_bar,
        )
        self.ocos[x.id] = x
        self._save()
        return x.id

    def protection_status(self, t: Trade) -> ProtectionUpdate:
        x = self.ocos.get(t.protection_id or "")
        if x is None:
            return ProtectionUpdate("UNKNOWN", message=f"paper OCO {t.protection_id} not found")
        filled = x.qty if x.state in ("STOP_FILLED", "TARGET_FILLED") else 0
        return ProtectionUpdate(x.state, x.fill_price, filled, x.message, x.at)

    def cancel_protection(self, t: Trade) -> None:
        x = self.ocos.get(t.protection_id or "")
        if x and x.state == "ACTIVE":
            x.state, x.message = "CANCELLED", "cancelled by engine"
            self._save()

    def submit_exit(self, t: Trade, limit_price: float, session_open: bool, tag: str) -> str:
        o = SimOrder(
            id=self._new_id("X"), symbol=t.symbol, side="SELL", qty=t.remaining_qty,
            limit=limit_price, placed_after=self.asof or t.signal_bar, tag=tag,
        )
        self.orders[o.id] = o
        self._save()
        return o.id

    def exit_status(self, t: Trade) -> OrderUpdate:
        return self._update(self._order(t.exit_order_id))
