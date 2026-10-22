"""
Strategy V, exactly as research/PREREGISTRATION_3.md writes it. Pure functions:
no feed, no clock, no file. The engine supplies quotes and time.

Section numbers in comments refer to that document. A number here that differs
from the document is a bug in this file, not a choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable

from market.calendar import TradingCalendar
from market.charges import FnoSchedule, option_order_costs, option_settlement_costs
from trading.fno.model import ContractMaster, IndexQuote, OptionContract, Quote


@dataclass(frozen=True)
class VParams:
    underlying: str = "NIFTY"
    wing: float = 100.0                     # W, index points (6)
    min_credit_frac: float = 0.20           # C >= 0.20 W (8)
    risk_frac: float = 0.01                 # M(n) <= 1% of equity (8)
    max_lots: int = 10                      # (8)
    entry_start: time = time(10, 0)         # (5)
    entry_end: time = time(14, 0)           # (5)
    min_sessions_to_expiry: int = 3         # sessions in (d, E] (4)
    spread_abs: float = 1.00                # ask - bid <= max(1.00, 0.10 mid) (7)
    spread_frac: float = 0.10
    max_quote_age_s: float = 120.0          # (7)
    harm_stop_frac: float = 0.10            # (11)
    settle_after: time = time(16, 0)        # (3)
    cross_check_tol: float = 0.0005         # 0.05% between the two closes (3)
    second_source_wait: int = 3             # trading days, then the feed's value alone (3)
    worst_case_after: int = 10              # trading days, then settle at -M (3)
    futility_n: int = 60                    # (12)
    max_trades: int = 250                   # (12)
    threshold: float = 200.0                # K >= 1/alpha, alpha = 0.005 (12)


REGISTERED = VParams()

ROLES = (("short_call", "CE", "SELL"), ("short_put", "PE", "SELL"),
         ("long_call", "CE", "BUY"), ("long_put", "PE", "BUY"))


@dataclass
class Skip:
    """No trade at this pass, and why. `code` is stable; `detail` is for people."""
    code: str
    detail: str
    data: dict = field(default_factory=dict)


@dataclass
class Plan:
    expiry: date
    index: dict
    atm_strike: float
    atm_quotes: dict
    expected_move: float
    legs: list[dict]
    credit: float
    lot_size: int
    lots: int
    qty: int
    entry_costs: dict
    entry_costs_total: float
    settlement_allowance: float
    max_loss: float
    equity_for_sizing: float
    budget: float
    flags: list[str]

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["expiry"] = self.expiry.isoformat()
        return d


# -- calendar (4) ------------------------------------------------------------

def sessions_after(d: date, e: date, cal: TradingCalendar) -> int:
    """Trading sessions in (d, e], counting e."""
    n, x = 0, d + timedelta(days=1)
    while x <= e:
        n += cal.is_trading_day(x)
        x += timedelta(days=1)
    return n


def ineligible(today: date, expiry: date, cal: TradingCalendar, p: VParams = REGISTERED) -> str | None:
    if today >= expiry:
        return "expiry day"
    left = sessions_after(today, expiry, cal)
    if left < p.min_sessions_to_expiry:
        return f"{left} session(s) to expiry, {p.min_sessions_to_expiry} needed"
    return None


def in_entry_window(now: datetime, p: VParams = REGISTERED) -> bool:
    return p.entry_start <= now.time().replace(tzinfo=None) <= p.entry_end


# -- quotes (7) --------------------------------------------------------------

def quote_problem(q: Quote | None, now: datetime, p: VParams = REGISTERED) -> str | None:
    if q is None:
        return "no quote"
    if q.bid is None or q.bid <= 0:
        return "no bid"
    if q.ask is None or q.ask <= q.bid:
        return "ask not above bid"
    mid = (q.bid + q.ask) / 2
    if q.ask - q.bid > max(p.spread_abs, p.spread_frac * mid) + 1e-9:
        return f"spread {q.ask - q.bid:.2f} wider than max({p.spread_abs:.2f}, {p.spread_frac:.0%} of mid {mid:.2f})"
    if q.exchange_ts is not None and (now - q.exchange_ts).total_seconds() > p.max_quote_age_s:
        return f"quote {int((now - q.exchange_ts).total_seconds())}s old"
    return None


def touch_problem(q: Quote, side: str, qty: int) -> str | None:
    have = q.bid_qty if side == "SELL" else q.ask_qty
    if have is not None and have < qty:
        return f"{'bid' if side == 'SELL' else 'ask'} quantity {have} < order {qty}"
    return None


def intrinsic(right: str, strike: float, value: float) -> float:
    return max(0.0, value - strike) if right == "CE" else max(0.0, strike - value)


# -- size (8) ----------------------------------------------------------------

def leg_costs(legs: list[dict], qty: int, sched: FnoSchedule) -> dict[str, dict]:
    return {leg["role"]: option_order_costs(leg["price"], qty, leg["side"], sched).as_dict() for leg in legs}


def settlement_allowance(legs: list[dict], qty: int, p: VParams, sched: FnoSchedule) -> float:
    """
    Settlement costs if the index settles exactly W beyond a long strike, the worse
    side: what the fee card charges there (STT on the long leg, and the two
    conservative readings on both in-the-money legs). Inside that zone the costs
    are no higher, so R >= -1 there exactly.
    """
    strike = {g["role"]: g["strike"] for g in legs}
    worst = 0.0
    for value in (strike["long_call"] + p.wing, strike["long_put"] - p.wing):
        worst = max(worst, sum(option_settlement_costs(intrinsic(g["right"], g["strike"], value), qty,
                                                       long=g["side"] == "BUY", sched=sched).as_dict()["total"]
                               for g in legs))
    return worst


def max_loss(credit: float, legs: list[dict], lot_size: int, lots: int, entry_costs_total: float,
             p: VParams, sched: FnoSchedule) -> float:
    return ((p.wing - credit) * lot_size * lots + entry_costs_total
            + settlement_allowance(legs, lot_size * lots, p, sched))


# -- entry (6-8) -------------------------------------------------------------

def plan_entry(master: ContractMaster, expiry: date, index: IndexQuote,
               fetch: Callable[[list[OptionContract]], dict[str, Quote]],
               now: datetime, equity_for_sizing: float,
               sched: FnoSchedule, p: VParams = REGISTERED) -> Plan | Skip:
    flags: list[str] = []
    if index.exchange_ts is None:
        flags.append("index value has no exchange timestamp; fetch time used")
    elif (now - index.exchange_ts).total_seconds() > p.max_quote_age_s:
        return Skip("index_stale", f"index value {int((now - index.exchange_ts).total_seconds())}s old")
    s = index.value

    both = sorted(set(master.strikes(expiry, "CE")) & set(master.strikes(expiry, "PE")))
    if not both:
        return Skip("no_strikes", f"no strike for {expiry} lists both a call and a put")
    k_atm = min(both, key=lambda k: (abs(k - s), k))            # tie -> the lower strike
    atm = [master.get(expiry, k_atm, "CE"), master.get(expiry, k_atm, "PE")]
    quotes = fetch(atm)
    atm_q = {}
    for c in atm:
        q = quotes.get(c.symbol)
        why = quote_problem(q, now, p)
        if why:
            return Skip("atm_quote", f"{c.symbol}: {why}", {"quote": q.as_dict() if q else None})
        atm_q[c.right] = q
    em = atm_q["CE"].mid + atm_q["PE"].mid

    calls, puts = master.strikes(expiry, "CE"), master.strikes(expiry, "PE")
    up = [k for k in calls if k >= s + em]
    down = [k for k in puts if k <= s - em]
    if not up or not down:
        return Skip("short_not_listed", f"no listed strike beyond S +- EM ({s:.2f} +- {em:.2f})")
    strikes = {"short_call": up[0], "short_put": down[-1],
               "long_call": up[0] + p.wing, "long_put": down[-1] - p.wing}
    contracts = {}
    for role, right, _ in ROLES:
        c = master.get(expiry, strikes[role], right)
        if c is None:
            return Skip("leg_not_listed", f"{role} {strikes[role]:g} {right} is not listed for {expiry}")
        contracts[role] = c
    lots_seen = {c.lot_size for c in contracts.values()}
    if len(lots_seen) != 1:
        return Skip("lot_size_mismatch", f"legs carry lot sizes {sorted(lots_seen)}")
    q_lot = lots_seen.pop()

    quotes = fetch(list(contracts.values()))
    leg_q = {}
    for role, _, _ in ROLES:
        q = quotes.get(contracts[role].symbol)
        why = quote_problem(q, now, p)
        if why:
            return Skip("leg_quote", f"{role} {contracts[role].symbol}: {why}",
                        {"quote": q.as_dict() if q else None})
        leg_q[role] = q
    if any(q.exchange_ts is None for q in list(leg_q.values()) + list(atm_q.values())):
        flags.append("an option quote has no exchange timestamp; fetch time used")

    credit = (leg_q["short_call"].bid + leg_q["short_put"].bid
              - leg_q["long_call"].ask - leg_q["long_put"].ask)
    if credit >= p.wing:
        return Skip("credit_not_below_wing", f"credit {credit:.2f} >= wing {p.wing:g}: bad data",
                    {"quotes": {r: q.as_dict() for r, q in leg_q.items()}})
    if credit < p.min_credit_frac * p.wing - 1e-9:
        return Skip("credit_below_floor", f"credit {credit:.2f} < {p.min_credit_frac * p.wing:.2f}",
                    {"credit": round(credit, 4)})

    legs = [{
        "role": role, "symbol": contracts[role].symbol, "exchange": contracts[role].exchange,
        "strike": contracts[role].strike, "right": right,
        "side": side, "price": leg_q[role].bid if side == "SELL" else leg_q[role].ask,
        "mid": leg_q[role].mid, "quote": leg_q[role].as_dict(),
    } for role, right, side in ROLES]

    budget = p.risk_frac * equity_for_sizing
    freeze = [c.freeze_qty for c in contracts.values() if c.freeze_qty]
    chosen = None
    for n in range(p.max_lots, 0, -1):
        if freeze and n * q_lot > min(freeze):
            continue
        costs = leg_costs(legs, n * q_lot, sched)
        total = sum(c["total"] for c in costs.values())
        m = max_loss(credit, legs, q_lot, n, total, p, sched)
        if m <= budget:
            chosen = (n, costs, total, m)
            break
    if chosen is None:
        costs1 = leg_costs(legs, q_lot, sched)
        m1 = max_loss(credit, legs, q_lot, 1, sum(c["total"] for c in costs1.values()), p, sched)
        return Skip("zero_lots", f"1 lot needs Rs{m1:,.2f} of maximum loss; the budget is Rs{budget:,.2f}",
                    {"max_loss_1_lot": round(m1, 2), "budget": round(budget, 2), "credit": round(credit, 4),
                     "lot_size": q_lot})
    n, costs, total, m = chosen

    for leg in legs:
        why = touch_problem(leg_q[leg["role"]], leg["side"], n * q_lot)
        if why:
            return Skip("touch_size", f"{leg['role']}: {why}")
    if any((leg_q[r].bid_qty if s == "SELL" else leg_q[r].ask_qty) is None for r, _, s in ROLES):
        flags.append("touch size unknown")

    return Plan(
        expiry=expiry, index=index.as_dict(), atm_strike=k_atm,
        atm_quotes={r: q.as_dict() for r, q in atm_q.items()}, expected_move=em, legs=legs,
        credit=credit, lot_size=q_lot, lots=n, qty=n * q_lot, entry_costs=costs,
        entry_costs_total=total, settlement_allowance=settlement_allowance(legs, n * q_lot, p, sched),
        max_loss=m, equity_for_sizing=equity_for_sizing, budget=budget, flags=flags,
    )


# -- settlement and flatten (10) ---------------------------------------------

def settle(trade: dict, value: float, sched: FnoSchedule) -> dict:
    """Cash settlement at the official close `value`. `trade` is a book record."""
    qty = trade["qty"]
    legs, gross_pts, costs = [], trade["credit"], {}
    for leg in trade["legs"]:
        i = intrinsic(leg["right"], leg["strike"], value)
        gross_pts += i if leg["side"] == "BUY" else -i
        c = option_settlement_costs(i, qty, long=leg["side"] == "BUY", sched=sched).as_dict()
        costs[leg["role"]] = c
        legs.append({"role": leg["role"], "intrinsic": i})
    gross = gross_pts * qty
    settle_total = sum(c["total"] for c in costs.values())
    net = gross - trade["entry_costs_total"] - settle_total
    return {"value": value, "legs": legs, "gross_points": gross_pts, "gross": gross,
            "settlement_costs": costs, "settlement_costs_total": settle_total,
            "net": net, "R": net / trade["max_loss"]}


def flatten_fill(trade: dict, quotes: dict[str, Quote], now: datetime, sched: FnoSchedule,
                 p: VParams = REGISTERED) -> dict | Skip:
    """Close every leg at the touch: shorts bought at the ask, longs sold at the bid."""
    exits, debit = [], 0.0
    for leg in trade["legs"]:
        q = quotes.get(leg["symbol"])
        why = quote_problem(q, now, p)
        if why:
            return Skip("flatten_quote", f"{leg['role']} {leg['symbol']}: {why}")
        price = q.ask if leg["side"] == "SELL" else q.bid
        debit += price if leg["side"] == "SELL" else -price
        exits.append({"role": leg["role"], "side": "BUY" if leg["side"] == "SELL" else "SELL",
                      "price": price, "quote": q.as_dict()})
    if debit > p.wing:
        return Skip("flatten_debit", f"closing debit {debit:.2f} exceeds the wing {p.wing:g}: quotes are not sane")
    qty = trade["qty"]
    costs = {e["role"]: option_order_costs(e["price"], qty, e["side"], sched).as_dict() for e in exits}
    exit_total = sum(c["total"] for c in costs.values())
    gross = (trade["credit"] - debit) * qty
    net = gross - trade["entry_costs_total"] - exit_total
    return {"exits": exits, "debit": debit, "gross": gross, "exit_costs": costs,
            "exit_costs_total": exit_total, "net": net, "R": net / trade["max_loss"]}
