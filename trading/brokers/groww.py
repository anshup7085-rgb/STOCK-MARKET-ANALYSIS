"""
Groww — LIVE order placement.

Places real orders with real money. Built only by the live path in
run_trader.py, after the opt-in checks in trading/live.py.

Written against the published `growwapi` 1.5.0 SDK, reading its source:
place_order(validity, exchange, order_type, product, quantity, segment,
trading_symbol, transaction_type, order_reference_id, price, trigger_price),
get_order_detail / get_order_status_by_reference, cancel_order,
create_smart_order / get_smart_order / cancel_smart_order / get_smart_order_list,
get_ltp, get_holdings_for_user, get_positions_for_user,
get_available_margin_details. Response field names follow Groww's API
reference (groww_order_id, order_status, filled_quantity, average_fill_price,
remark; smart_order_id and status for smart orders).

PROTECTION IS TWO GTTs, NOT ONE OCO
-----------------------------------
Groww's OCO smart order is not available for the CASH segment (it is offered
for F&O and intraday only), and every trade here is CNC delivery. So the stop
and the target are two independent GTT sell orders — a DOWN trigger at the
stop and an UP trigger at the target — and this adapter makes them behave as
one-cancels-other: the moment either leg leaves ACTIVE, the other is
cancelled, before the status is even reported to the engine.

The residual risk is a few seconds wide: price would have to trigger one leg
and then the other before the next status read. The second sell is for shares
the first already sold, so for CNC it is rejected — unless you hold MORE of
that stock outside this system, in which case it would sell those. Don't hold
the same stock by hand in the account this system trades.

FILL DETAILS AFTER A TRIGGER
----------------------------
Groww documents smart-order status but not, in the SDK, how a triggered GTT
names the order it placed. This adapter looks for that order id in the
response; failing that, it accepts exactly one matching executed CNC SELL in
today's order book; failing that, it reports UNKNOWN and the engine raises an
alert. `run_trader.py record-exit` then books the fill from your contract note.
It never invents a fill price.

AUTH
----
Same contract as the read-only provider: you mint GROWW_ACCESS_TOKEN yourself
each day. The adapter refuses to start if GROWW_TOTP_SECRET (or a password or
PIN) is in the environment.

Static IP: SEBI's retail-algo framework expects API orders from a registered
static IP. Check Groww's developer console for how to register yours.
"""

from __future__ import annotations

import os

from trading.brokers.base import Broker, BrokerError, ProtectionImpossible, Throttle
from trading.models import OrderUpdate, ProtectionUpdate, Trade, round_to_tick

_FORBIDDEN_ENV = ("GROWW_TOTP_SECRET", "GROWW_TOTP_KEY", "GROWW_PASSWORD", "GROWW_PIN")

# Groww's published order-API limit is 250/minute (~4/s). Stay under it.
_GROWW_MAX_OPS = 4.0

_OPEN = {"NEW", "ACKED", "TRIGGER_PENDING", "APPROVED", "OPEN",
         "CANCELLATION_REQUESTED", "MODIFICATION_REQUESTED"}
_DONE = {"EXECUTED", "COMPLETED", "DELIVERY_AWAITED"}
_DEAD = {"REJECTED": "REJECTED", "FAILED": "REJECTED", "CANCELLED": "CANCELLED"}

_ORDER_ID_KEYS = ("groww_order_id", "order_id", "triggered_order_id", "child_order_id")


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


class GrowwBroker(Broker):
    name = "Groww — LIVE"
    is_live = True
    exchange = "NSE"
    segment = "CASH"

    def __init__(self, *, live_ack: bool, max_orders_per_second: float, client=None):
        if not live_ack:
            raise BrokerError(
                "GrowwBroker places real orders and is only built by the live path "
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
            token = os.getenv("GROWW_ACCESS_TOKEN")
            if not token:
                raise BrokerError(
                    "GROWW_ACCESS_TOKEN not set. Mint it yourself each day (TRADING.md, "
                    "Groww section) and export it."
                )
            try:
                from growwapi import GrowwAPI
            except ImportError as exc:  # pragma: no cover
                raise BrokerError("Run: pip install growwapi") from exc
            client = GrowwAPI(token)
        self.g = client
        self.throttle = Throttle(min(max_orders_per_second, _GROWW_MAX_OPS))
        self._ticks: dict[str, float] = {}

    # -- plumbing ---------------------------------------------------------

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BrokerError:
            raise
        except Exception as exc:
            raise BrokerError(f"{getattr(fn, '__name__', 'groww call')}: {exc}") from exc

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        code = str(getattr(exc, "code", ""))
        return (type(exc).__name__ == "GrowwAPINotFoundException" or code == "404"
                or "not found" in str(exc).lower())

    def tick_size(self, symbol: str) -> float:
        if symbol not in self._ticks:
            tick = 0.05
            try:
                inst = self.g.get_instrument_by_exchange_and_trading_symbol(self.exchange, symbol)
                tick = float((inst or {}).get("tick_size") or 0.05)
            except Exception:
                pass
            self._ticks[symbol] = tick
        return self._ticks[symbol]

    @staticmethod
    def _map(o: dict) -> OrderUpdate:
        st = str(o.get("order_status") or "").upper()
        if st in _DONE:
            state = "COMPLETE"
        elif st in _DEAD:
            state = _DEAD[st]
        elif st in _OPEN:
            state = "OPEN"
        else:
            state = "UNKNOWN"
        avg = _num(o.get("average_fill_price"))
        return OrderUpdate(
            state=state,
            filled_qty=int(o.get("filled_quantity") or 0),
            avg_price=avg if avg else None,
            message=str(o.get("remark") or st),
            at=str(o.get("trade_date") or o.get("exchange_time") or o.get("created_at") or ""),
        )

    def _detail(self, order_id: str) -> dict:
        return self._call(self.g.get_order_detail, segment=self.segment,
                          groww_order_id=str(order_id)) or {}

    def find_order_by_tag(self, tag: str) -> str | None:
        try:
            r = self.g.get_order_status_by_reference(segment=self.segment, order_reference_id=tag)
        except Exception as exc:
            if self._is_not_found(exc):
                return None
            # Anything else means we do not KNOW the tag is unused, so nothing
            # may be sent on the assumption that it is.
            raise BrokerError(f"could not check reference {tag}: {exc}") from exc
        oid = (r or {}).get("groww_order_id")
        return str(oid) if oid else None

    def _place(self, *, side: str, t: Trade, qty: int, price: float, tag: str) -> str:
        self.throttle.wait()
        r = self._call(
            self.g.place_order,
            validity="DAY",
            exchange=self.exchange,
            order_type="LIMIT",
            product="CNC",
            quantity=int(qty),
            segment=self.segment,
            trading_symbol=t.symbol,
            transaction_type=side,
            order_reference_id=tag,
            price=price,
        ) or {}
        status = str(r.get("order_status") or "").upper()
        if status in ("FAILED", "REJECTED"):
            raise BrokerError(f"Groww {status}: {r.get('remark') or 'no remark'}")
        oid = r.get("groww_order_id")
        if not oid:
            raise BrokerError("place_order returned no groww_order_id — check the Groww order book by hand")
        return str(oid)

    # -- entries ----------------------------------------------------------

    def submit_entry(self, t: Trade, session_open: bool) -> str:
        existing = self.find_order_by_tag(t.tag)
        if existing:
            return existing
        price = round_to_tick(t.entry_limit, self.tick_size(t.symbol), "down")
        return self._place(side="BUY", t=t, qty=t.qty, price=price, tag=t.tag)

    def entry_status(self, t: Trade) -> OrderUpdate:
        return self._map(self._detail(str(t.entry_order_id)))

    def cancel_entry(self, t: Trade) -> None:
        self.cancel_order(str(t.entry_order_id))

    def cancel_order(self, order_id: str) -> None:
        if self._map(self._detail(str(order_id))).state == "OPEN":
            self.throttle.wait()
            self._call(self.g.cancel_order, groww_order_id=str(order_id), segment=self.segment)

    # -- protection: two GTTs held together by this adapter ---------------

    def last_price(self, symbol: str) -> float | None:
        key = f"{self.exchange}_{symbol}"
        r = self._call(self.g.get_ltp, exchange_trading_symbols=(key,), segment=self.segment) or {}
        v = r.get(key)
        if isinstance(v, dict):
            v = v.get("ltp") or v.get("last_price")
        return _num(v)

    @staticmethod
    def _leg_refs(t: Trade) -> tuple[str, str]:
        k = t.protect_attempts % 10
        return f"{t.tag[:16]}P{k}S", f"{t.tag[:16]}P{k}T"

    def _active_gtts_by_ref(self) -> dict[str, str]:
        """reference_id -> smart_order_id for ACTIVE CASH GTTs (crash recovery)."""
        try:
            r = self.g.get_smart_order_list(smart_order_type="GTT", segment=self.segment,
                                            status="ACTIVE", page=0, page_size=50) or {}
        except Exception as exc:
            raise BrokerError(f"could not list GTTs to check for an earlier placement: {exc}") from exc
        rows = r.get("orders") or r.get("smart_orders") or r.get("data") or []
        return {
            str(x.get("reference_id")): str(x.get("smart_order_id"))
            for x in rows if isinstance(x, dict) and x.get("reference_id") and x.get("smart_order_id")
        }

    def _gtt(self, t: Trade, ref: str, direction: str, trigger: float, price: float) -> str:
        self.throttle.wait()
        r = self._call(
            self.g.create_smart_order,
            smart_order_type="GTT",
            segment=self.segment,
            trading_symbol=t.symbol,
            quantity=int(t.remaining_qty),
            product_type="CNC",
            exchange=self.exchange,
            duration="GTC",
            reference_id=ref,
            trigger_price=f"{trigger:.2f}",
            trigger_direction=direction,
            order={"order_type": "LIMIT", "price": price, "transaction_type": "SELL"},
        ) or {}
        sid = r.get("smart_order_id")
        if not sid:
            raise BrokerError(f"GTT {ref} accepted without a smart_order_id — check Groww by hand")
        return str(sid)

    def protect(self, t: Trade) -> str:
        ltp = self.last_price(t.symbol)
        if ltp is None:
            raise BrokerError(f"no last price for {t.symbol}; cannot place GTTs")
        if ltp <= t.stop:
            raise ProtectionImpossible("stop", ltp, f"LTP {ltp} already at/below stop {t.stop}")
        if ltp >= t.target:
            raise ProtectionImpossible("target", ltp, f"LTP {ltp} already at/above target {t.target}")
        tick = self.tick_size(t.symbol)
        s_ref, t_ref = self._leg_refs(t)
        existing = self._active_gtts_by_ref()
        stop_id = existing.get(s_ref) or self._gtt(
            t, s_ref, "DOWN", round_to_tick(t.stop, tick), round_to_tick(t.stop_limit, tick, "down"))
        try:
            tgt_id = existing.get(t_ref) or self._gtt(
                t, t_ref, "UP", round_to_tick(t.target, tick, "up"), round_to_tick(t.target, tick, "up"))
        except BrokerError:
            # A stop with no target is still protection; a target with no stop
            # is not. Keep the stop, report the failure so the engine alerts.
            raise BrokerError(
                f"stop GTT {stop_id} placed but the target GTT failed — the position "
                "is protected on the downside only; re-run to retry the target"
            )
        return f"{stop_id}|{tgt_id}"

    def _leg(self, sid: str) -> dict:
        return self._call(self.g.get_smart_order, segment=self.segment,
                          smart_order_type="GTT", smart_order_id=sid) or {}

    def _cancel_leg(self, sid: str) -> None:
        self.throttle.wait()
        self._call(self.g.cancel_smart_order, segment=self.segment,
                   smart_order_type="GTT", smart_order_id=sid)

    def _triggered_order(self, leg: dict, t: Trade) -> dict | None:
        """The order a triggered GTT placed: named in the response, or the
        single matching executed CNC SELL in today's order book. Else None."""
        for k in _ORDER_ID_KEYS:
            if leg.get(k):
                return self._detail(str(leg[k]))
        inner = leg.get("order") if isinstance(leg.get("order"), dict) else {}
        for k in _ORDER_ID_KEYS:
            if inner.get(k):
                return self._detail(str(inner[k]))
        # Fallback: exactly one EXECUTED CNC sell of exactly this quantity in
        # this symbol today, that is not one of our own exit orders. Anything
        # looser could pick up a manual trade and book the wrong price.
        book = self._call(self.g.get_order_list, page=0, page_size=50, segment=self.segment) or {}
        rows = book.get("order_list") or book.get("orders") or []
        own_exit = f"{t.tag[:18]}X"
        hits = [
            o for o in rows if isinstance(o, dict)
            and o.get("trading_symbol") == t.symbol
            and str(o.get("transaction_type") or "").upper() == "SELL"
            and str(o.get("product") or "").upper() == "CNC"
            and self._map(o).state == "COMPLETE"
            and int(o.get("filled_quantity") or 0) == t.remaining_qty
            and not str(o.get("order_reference_id") or "").startswith(own_exit)
        ]
        return hits[0] if len(hits) == 1 else None

    def protection_status(self, t: Trade) -> ProtectionUpdate:
        ids = str(t.protection_id or "").split("|")
        if len(ids) != 2:
            return ProtectionUpdate("UNKNOWN", message=f"unrecognised protection id {t.protection_id!r}")
        stop_id, tgt_id = ids
        legs = {"STOP": (stop_id, self._leg(stop_id)), "TARGET": (tgt_id, self._leg(tgt_id))}
        status = {k: str(v[1].get("status") or "").upper() for k, v in legs.items()}

        if status["STOP"] == "ACTIVE" and status["TARGET"] == "ACTIVE":
            return ProtectionUpdate("ACTIVE")

        # One leg has left ACTIVE: cancel the survivor FIRST, so two
        # independent GTTs behave as one-cancels-other.
        for kind, (sid, _) in legs.items():
            if status[kind] == "ACTIVE":
                self._cancel_leg(sid)

        fired = [k for k in ("STOP", "TARGET") if status[k] not in ("ACTIVE", "CANCELLED", "")]
        if not fired:
            return ProtectionUpdate(
                "CANCELLED",
                message=f"GTT legs stop={status['STOP'] or '?'} target={status['TARGET'] or '?'}; "
                        "the survivor was cancelled so the pair can be re-placed",
            )
        kind = fired[0]
        order = self._triggered_order(legs[kind][1], t)
        if order is None:
            return ProtectionUpdate(
                "UNKNOWN",
                message=f"{kind} GTT is {status[kind]} but its order could not be identified — "
                        "check Groww's order book and book the fill with `run_trader.py record-exit`",
            )
        u = self._map(order)
        oid = str(order.get("groww_order_id") or "") or None
        if u.state == "COMPLETE":
            return ProtectionUpdate(f"{kind}_FILLED", u.avg_price, u.filled_qty or t.remaining_qty,
                                    u.message, u.at, leg=kind, order_id=oid)
        if u.state == "OPEN":
            return ProtectionUpdate("TRIGGERED_OPEN", u.avg_price, u.filled_qty,
                                    f"{kind} leg order working: {u.message}", u.at, leg=kind, order_id=oid)
        return ProtectionUpdate("TRIGGERED_UNFILLED", u.avg_price, u.filled_qty,
                                f"{kind} leg order {u.state}: {u.message}", u.at, leg=kind, order_id=oid)

    def cancel_protection(self, t: Trade) -> None:
        for sid in str(t.protection_id or "").split("|"):
            if sid and str(self._leg(sid).get("status") or "").upper() == "ACTIVE":
                self._cancel_leg(sid)

    # -- exits ------------------------------------------------------------

    def submit_exit(self, t: Trade, limit_price: float, session_open: bool, tag: str) -> str:
        existing = self.find_order_by_tag(tag)
        if existing and self._map(self._detail(existing)).state not in ("CANCELLED", "REJECTED"):
            return existing
        price = round_to_tick(limit_price, self.tick_size(t.symbol), "down")
        return self._place(side="SELL", t=t, qty=t.remaining_qty, price=price, tag=tag)

    def exit_status(self, t: Trade) -> OrderUpdate:
        return self._map(self._detail(str(t.exit_order_id)))

    # -- account ----------------------------------------------------------

    def holdings(self) -> dict[str, int] | None:
        """Approximate CNC quantity per symbol. Alerts only — never acted on."""
        qty: dict[str, int] = {}
        h = self._call(self.g.get_holdings_for_user) or {}
        for x in h.get("holdings") or []:
            s = str(x.get("trading_symbol"))
            qty[s] = qty.get(s, 0) + int(x.get("quantity") or 0) + int(x.get("t1_quantity") or 0)
        p = self._call(self.g.get_positions_for_user, segment=self.segment) or {}
        for x in p.get("positions") or []:
            if str(x.get("product") or "").upper() == "CNC":
                s = str(x.get("trading_symbol"))
                qty[s] = qty.get(s, 0) + int(x.get("quantity") or 0)
        return qty

    def available_cash(self) -> float | None:
        m = self._call(self.g.get_available_margin_details) or {}
        eq = m.get("equity_margin_details") or {}
        for v in (eq.get("cnc_balance_available"), m.get("clear_cash")):
            f = _num(v)
            if f is not None:
                return f
        return None
