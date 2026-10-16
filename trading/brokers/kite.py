"""
Zerodha Kite Connect — LIVE order placement.

This module places real orders with real money. It is never constructed by
the research tools, and the trading engine builds it only after the live
opt-in checks in run_trader.py have passed (see TRADING.md).

Written against kiteconnect 5.2.x, reading its actual signatures:
place_order(variety, exchange, tradingsymbol, transaction_type, quantity,
product, order_type, price, validity, ..., algo_id, tag, market_protection),
place_gtt(trigger_type, tradingsymbol, exchange, trigger_values, last_price,
orders), get_gtt, delete_gtt, order_history, orders, holdings, positions,
margins, ltp, instruments.

AUTH — unchanged from the read-only provider
--------------------------------------------
You log in through Zerodha's own page, every morning, by hand, with your own
2FA. `tools/kite_session.py` exchanges the resulting request token for an
access token and prints it; you export KITE_API_KEY and KITE_ACCESS_TOKEN.
This module reads those two and nothing else. It refuses to start if it finds
a TOTP seed, password or PIN in the environment: storing those so a script can
log in by itself defeats the second factor, and SEBI's retail-algo framework
expects a human 2FA login each trading day.

WHAT IT SENDS
-------------
  * LIMIT orders only. No MARKET orders, so no order can fill at a price the
    engine did not choose. Exits that must happen use a marketable limit.
  * CNC (delivery) product only.
  * Every order carries a `tag` — the engine's idempotency key. Before placing
    an entry, the order book is searched for that tag, so a crash between
    "sent" and "recorded" never produces a second order.
  * `algo_id` is sent if KITE_ALGO_ID is set. Whether your setup needs one
    under the exchange's retail-algo rules is a question for Zerodha, not for
    this code — see TRADING.md.
  * Order-mutating calls are throttled well under the 10 orders/second level
    at which the exchange treats a strategy as a registered algo.

Static IP: from 1 April 2026 Zerodha accepts API orders only from a static IP
whitelisted on your Kite Connect app. Run this from that machine.
"""

from __future__ import annotations

import os

from trading.brokers.base import Broker, BrokerError, ProtectionImpossible, Throttle
from trading.models import OrderUpdate, ProtectionUpdate, Trade, round_to_tick

_FORBIDDEN_ENV = ("KITE_TOTP_SECRET", "KITE_TOTP_KEY", "KITE_PASSWORD", "KITE_PIN")
_DEAD = ("CANCELLED", "REJECTED")


class KiteBroker(Broker):
    name = "Zerodha Kite Connect — LIVE"
    is_live = True
    exchange = "NSE"

    def __init__(self, *, live_ack: bool, max_orders_per_second: float, client=None):
        if not live_ack:
            raise BrokerError(
                "KiteBroker places real orders and is only built by the live path "
                "after its opt-in checks. Refusing."
            )
        for var in _FORBIDDEN_ENV:
            if os.getenv(var):
                raise BrokerError(
                    f"{var} is set in the environment. Remove it. Storing a TOTP seed, "
                    "password or PIN lets a script log in without you, which defeats "
                    "two-factor authentication. Nothing here will read it."
                )
        if client is None:
            api_key = os.getenv("KITE_API_KEY")
            token = os.getenv("KITE_ACCESS_TOKEN")
            if not api_key or not token:
                raise BrokerError(
                    "KITE_API_KEY / KITE_ACCESS_TOKEN not set. Log in by hand and run "
                    "tools/kite_session.py (see TRADING.md, daily routine)."
                )
            try:
                from kiteconnect import KiteConnect
            except ImportError as exc:  # pragma: no cover
                raise BrokerError("Run: pip install kiteconnect") from exc
            client = KiteConnect(api_key=api_key)
            client.set_access_token(token)
        self.k = client
        self.algo_id = os.getenv("KITE_ALGO_ID") or None
        self.throttle = Throttle(max_orders_per_second)
        self._ticks: dict[str, float] | None = None

    # -- plumbing ---------------------------------------------------------

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BrokerError:
            raise
        except Exception as exc:
            # The SDK's messages carry no credentials; pass them through so the
            # journal records the broker's own words.
            raise BrokerError(f"{getattr(fn, '__name__', 'kite call')}: {exc}") from exc

    def tick_size(self, symbol: str) -> float:
        if self._ticks is None:
            try:
                dump = self._call(self.k.instruments, self.exchange)
                self._ticks = {
                    str(i["tradingsymbol"]): float(i.get("tick_size") or 0.05) for i in dump
                }
            except BrokerError:
                self._ticks = {}
        return self._ticks.get(symbol, 0.05)

    def _history(self, order_id: str) -> dict:
        hist = self._call(self.k.order_history, order_id)
        if not hist:
            raise BrokerError(f"order {order_id} has no history at the broker")
        return hist[-1]

    @staticmethod
    def _map(o: dict) -> OrderUpdate:
        st = str(o.get("status") or "").upper()
        if st == "COMPLETE":
            state = "COMPLETE"
        elif st in _DEAD:
            state = st
        elif st:
            state = "OPEN"          # OPEN, TRIGGER PENDING, AMO REQ RECEIVED, ...
        else:
            state = "UNKNOWN"
        avg = o.get("average_price")
        return OrderUpdate(
            state=state,
            filled_qty=int(o.get("filled_quantity") or 0),
            avg_price=float(avg) if avg else None,
            message=str(o.get("status_message") or st),
            at=str(o.get("exchange_timestamp") or o.get("order_timestamp") or ""),
        )

    def find_order_by_tag(self, tag: str) -> str | None:
        hits = [
            o for o in self._call(self.k.orders)
            if o.get("tag") == tag or tag in (o.get("tags") or [])
        ]
        if not hits:
            return None
        return str(hits[-1]["order_id"])

    def _place(self, *, side: str, t: Trade, qty: int, price: float, session_open: bool, tag: str) -> str:
        params = dict(
            variety="regular" if session_open else "amo",
            exchange=self.exchange,
            tradingsymbol=t.symbol,
            transaction_type=side,
            quantity=int(qty),
            product="CNC",
            order_type="LIMIT",
            price=price,
            validity="DAY",
            tag=tag,
        )
        if self.algo_id:
            params["algo_id"] = self.algo_id
        self.throttle.wait()
        oid = self._call(self.k.place_order, **params)
        if not oid:
            raise BrokerError("place_order returned no order id — check the Kite order book by hand")
        return str(oid)

    # -- entries ----------------------------------------------------------

    def submit_entry(self, t: Trade, session_open: bool) -> str:
        existing = self.find_order_by_tag(t.tag)
        if existing:
            return existing
        price = round_to_tick(t.entry_limit, self.tick_size(t.symbol), "down")
        return self._place(side="BUY", t=t, qty=t.qty, price=price,
                           session_open=session_open, tag=t.tag)

    def entry_status(self, t: Trade) -> OrderUpdate:
        return self._map(self._history(str(t.entry_order_id)))

    def cancel_entry(self, t: Trade) -> None:
        o = self._history(str(t.entry_order_id))
        if self._map(o).state == "OPEN":
            self.throttle.wait()
            self._call(self.k.cancel_order, o.get("variety") or "regular", str(t.entry_order_id))

    # -- protection (GTT OCO) ---------------------------------------------

    def last_price(self, symbol: str) -> float | None:
        key = f"{self.exchange}:{symbol}"
        q = self._call(self.k.ltp, key)
        try:
            return float(q[key]["last_price"])
        except (KeyError, TypeError, ValueError):
            return None

    def protect(self, t: Trade) -> str:
        ltp = self.last_price(t.symbol)
        if ltp is None:
            raise BrokerError(f"no last price for {t.symbol}; cannot place GTT")
        if ltp <= t.stop:
            raise ProtectionImpossible("stop", ltp, f"LTP {ltp} already at/below stop {t.stop}")
        if ltp >= t.target:
            raise ProtectionImpossible("target", ltp, f"LTP {ltp} already at/above target {t.target}")
        tick = self.tick_size(t.symbol)
        qty = t.remaining_qty
        legs = [
            {"transaction_type": "SELL", "quantity": qty, "order_type": "LIMIT",
             "product": "CNC", "price": round_to_tick(t.stop_limit, tick, "down")},
            {"transaction_type": "SELL", "quantity": qty, "order_type": "LIMIT",
             "product": "CNC", "price": round_to_tick(t.target, tick, "up")},
        ]
        self.throttle.wait()
        res = self._call(
            self.k.place_gtt,
            trigger_type="two-leg",
            tradingsymbol=t.symbol,
            exchange=self.exchange,
            trigger_values=[round_to_tick(t.stop, tick), round_to_tick(t.target, tick, "up")],
            last_price=ltp,
            orders=legs,
        )
        tid = res.get("trigger_id") if isinstance(res, dict) else res
        if not tid:
            raise BrokerError("GTT accepted without a trigger_id — check the Kite GTT book by hand")
        return str(tid)

    def protection_status(self, t: Trade) -> ProtectionUpdate:
        g = self._call(self.k.get_gtt, int(str(t.protection_id)))
        status = str(g.get("status") or "").lower()
        if status == "active":
            return ProtectionUpdate("ACTIVE")
        if status in ("cancelled", "deleted", "expired", "disabled", "rejected"):
            return ProtectionUpdate("CANCELLED", message=f"GTT {status}")
        if status != "triggered":
            return ProtectionUpdate("UNKNOWN", message=f"GTT status '{status}'")

        # Legs are in the order they were placed: [stop, target].
        for idx, leg in enumerate(g.get("orders") or []):
            res = leg.get("result")
            if not res:
                continue
            kind = "STOP" if idx == 0 else "TARGET"
            ores = res.get("order_result") or {}
            oid = ores.get("order_id")
            if not oid or str(ores.get("status") or "success").lower() not in ("success",):
                return ProtectionUpdate(
                    "TRIGGERED_UNFILLED",
                    message=f"{kind} leg triggered but its order failed: "
                            f"{ores.get('rejection_reason') or ores.get('status')}",
                )
            u = self._map(self._history(str(oid)))
            if u.state == "COMPLETE":
                return ProtectionUpdate(f"{kind}_FILLED", u.avg_price, u.filled_qty, u.message, u.at)
            if u.state == "OPEN":
                return ProtectionUpdate("TRIGGERED_OPEN", u.avg_price, u.filled_qty,
                                        f"{kind} leg order working: {u.message}", u.at)
            return ProtectionUpdate("TRIGGERED_UNFILLED", u.avg_price, u.filled_qty,
                                    f"{kind} leg order {u.state}: {u.message}", u.at)
        return ProtectionUpdate("UNKNOWN", message="GTT triggered but no leg reported a result")

    def cancel_protection(self, t: Trade) -> None:
        self.throttle.wait()
        self._call(self.k.delete_gtt, int(str(t.protection_id)))

    # -- exits ------------------------------------------------------------

    def submit_exit(self, t: Trade, limit_price: float, session_open: bool, tag: str) -> str:
        existing = self.find_order_by_tag(tag)
        if existing and self._map(self._history(existing)).state not in _DEAD:
            return existing
        price = round_to_tick(limit_price, self.tick_size(t.symbol), "down")
        return self._place(side="SELL", t=t, qty=t.remaining_qty, price=price,
                           session_open=session_open, tag=tag)

    def exit_status(self, t: Trade) -> OrderUpdate:
        return self._map(self._history(str(t.exit_order_id)))

    # -- account ----------------------------------------------------------

    def holdings(self) -> dict[str, int] | None:
        """
        Approximate CNC quantity per symbol: settled + T1 holdings plus today's
        CNC day position. Used to raise alerts, never to act — a person resolves
        a mismatch.
        """
        qty: dict[str, int] = {}
        for h in self._call(self.k.holdings) or []:
            s = str(h.get("tradingsymbol"))
            qty[s] = qty.get(s, 0) + int(h.get("quantity") or 0) + int(h.get("t1_quantity") or 0)
        pos = self._call(self.k.positions) or {}
        for p in pos.get("day", []) if isinstance(pos, dict) else []:
            if p.get("product") == "CNC" and p.get("exchange") == self.exchange:
                s = str(p.get("tradingsymbol"))
                qty[s] = qty.get(s, 0) + int(p.get("quantity") or 0)
        return qty

    def available_cash(self) -> float | None:
        m = self._call(self.k.margins, "equity") or {}
        net = m.get("net")
        if net is None:
            net = (m.get("available") or {}).get("live_balance")
        return float(net) if net is not None else None
