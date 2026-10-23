"""
Strategy V, exactly as research/PREREGISTRATION_3.md writes it. Pure functions:
no feed, no clock, no file. The engine supplies quotes and time.

Section numbers in comments refer to that document. A number here that differs
from the document is a bug in this file, not a choice. Where the document is
silent, this file at the binding commit is the rule (section 17).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable

from market.calendar import TradingCalendar
from market.charges import FnoSchedule, option_order_costs, option_settlement_costs
from trading.fno.model import ContractMaster, IndexQuote, OptionContract, Quote

# SHA-256 of research/PREREGISTRATION_3.md at the binding commit. The engine
# refuses a real feed unless the file on disk matches it (section 17).
REGISTRATION_SHA256 = None


@dataclass(frozen=True)
class VParams:
    underlying: str = "NIFTY"
    starting_equity: float = 500_000.0     # fixed (Summary)
    risk_budget: float = 5_000.0           # B: 1% of the starting equity, fixed (8)
    wing: float = 100.0                    # W, index points (6)
    min_credit: float = 20.00              # C >= 0.20 W (8)
    max_credit: float = 80.00              # C <= 0.80 W, else bad data (8)
    max_lots: int = 10                     # (8)
    first_slot: time = time(10, 0)         # 17 slots, 10:00 ... 14:00 (5)
    slot_every_min: int = 15
    slots: int = 17
    slot_window_s: int = 120               # a pass belongs to slot t if it starts in [t, t + 120 s) (5)
    min_sessions_to_expiry: int = 3        # sessions in (d, E] (4)
    spread_abs: float = 1.00               # ask - bid <= max(1.00, 0.10 mid) (7)
    spread_frac: float = 0.10
    max_quote_age_s: int = 120             # (7)
    max_snapshot_s: int = 10               # every option quote read within 10 s after S (7)
    harm_stop: float = 50_000.0            # 10% of the starting equity (11)
    settle_after: time = time(16, 0)       # (3)
    close_rule_until: date = date(2026, 12, 7)   # expiries before this: FSP is the close (3)
    close_match_points: float = 0.10       # (3)
    fsp_sanity_frac: float = 0.03          # (3)
    worst_case_after: int = 10             # trading days, then settle at -M (3)
    futility_n: int = 60                   # (12)
    max_trades: int = 250                  # (12)
    max_cycles: int = 300                  # (12)
    abandon_after_days: int = 21           # (12)
    threshold: float = 200.0               # K >= 1/alpha, alpha = 0.005 (12)


REGISTERED = VParams()

ROLES = (("short_call", "CE", "SELL"), ("short_put", "PE", "SELL"),
         ("long_call", "CE", "BUY"), ("long_put", "PE", "BUY"))

# Skips caused by the feed or the machine, not the market (section 5): the engine
# treats them as data failures, so the slot is unobserved and never a rule outcome.
DATA_CODES = frozenset({"index_stale", "clock", "no_quote", "slow_snapshot", "no_timestamp_today",
                        "stale_snapshot"})
FUTURE_TOLERANCE_S = 5


def milli(x: float) -> int:
    """Exact decimal comparison for prices on a 0.05 tick: everything is a multiple of 0.005 (8)."""
    return round(x * 1000)


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
    budget: float
    flags: list[str]

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["expiry"] = self.expiry.isoformat()
        return d


# -- calendar and slots (4, 5) -------------------------------------------------

def sessions_after(d: date, e: date, cal: TradingCalendar) -> int:
    """Trading sessions in (d, e], counting e."""
    n, x = 0, d + timedelta(days=1)
    while x <= e:
        n += cal.is_trading_day(x)
        x += timedelta(days=1)
    return n


def eligible_sessions(e: date, e_prev: date, cal: TradingCalendar, p: VParams = REGISTERED) -> list[date]:
    """Sessions d with E_prev < d < E and at least 3 sessions in (d, E] (4)."""
    out, d = [], e_prev + timedelta(days=1)
    while d < e:
        if cal.is_trading_day(d) and sessions_after(d, e, cal) >= p.min_sessions_to_expiry:
            out.append(d)
        d += timedelta(days=1)
    return out


def slot_times(p: VParams = REGISTERED) -> list[time]:
    base = datetime.combine(date(2000, 1, 1), p.first_slot)
    return [(base + timedelta(minutes=p.slot_every_min * k)).time() for k in range(p.slots)]


def slot_of(now: datetime, p: VParams = REGISTERED) -> time | None:
    """The slot a pass starting at `now` belongs to, or None (5)."""
    for t in slot_times(p):
        start = datetime.combine(now.date(), t, now.tzinfo)
        if start <= now < start + timedelta(seconds=p.slot_window_s):
            return t
    return None


def expected_slots(e: date, e_prev: date, cal: TradingCalendar, tz, p: VParams = REGISTERED) -> list[datetime]:
    return [datetime.combine(d, t, tz) for d in eligible_sessions(e, e_prev, cal, p) for t in slot_times(p)]


# -- quotes (7) --------------------------------------------------------------

def quote_problem(q: Quote | None, at: datetime, p: VParams = REGISTERED) -> str | None:
    if q is None:
        return "no quote"
    if q.bid is None or q.bid <= 0:
        return "no bid"
    if q.ask is None or milli(q.ask) <= milli(q.bid):
        return "ask not above bid"
    spread, mid = milli(q.ask) - milli(q.bid), (milli(q.ask) + milli(q.bid)) / 2
    if spread > max(milli(p.spread_abs), p.spread_frac * mid):
        return f"spread {q.ask - q.bid:.2f} wider than max({p.spread_abs:.2f}, {p.spread_frac:.0%} of mid {mid / 1000:.3f})"
    if q.exchange_ts is not None and (at - q.exchange_ts).total_seconds() > p.max_quote_age_s:
        return f"quote {int((at - q.exchange_ts).total_seconds())}s old"
    return None


def touch_problem(q: Quote, side: str, qty: int) -> str | None:
    have = q.bid_qty if side == "SELL" else q.ask_qty
    if have is not None and have < qty:
        return f"{'bid' if side == 'SELL' else 'ask'} quantity {have} < order {qty}"
    return None


# -- settlement arithmetic (8, 10) ------------------------------------------------

def intrinsic(right: str, strike: float, value: float) -> float:
    return max(0.0, value - strike) if right == "CE" else max(0.0, strike - value)


def settlement_costs(legs: list[dict], qty: int, value: float, sched: FnoSchedule) -> dict[str, dict]:
    return {g["role"]: option_settlement_costs(intrinsic(g["right"], g["strike"], value), qty,
                                               long=g["side"] == "BUY", sched=sched,
                                               notional_price=max(value, g["strike"])).as_dict()
            for g in legs}


def settlement_allowance(legs: list[dict], qty: int, p: VParams, sched: FnoSchedule) -> float:
    """
    The section 9 settlement costs with the index exactly W beyond a long strike,
    on whichever side they are larger. Inside that zone costs are no higher, so
    R >= -1 there exactly (8).
    """
    strike = {g["role"]: g["strike"] for g in legs}
    return max(round(sum(c["total"] for c in settlement_costs(legs, qty, v, sched).values()), 2)
               for v in (strike["long_call"] + p.wing, strike["long_put"] - p.wing))


def max_loss(credit: float, legs: list[dict], lot_size: int, lots: int, entry_costs_total: float,
             p: VParams, sched: FnoSchedule) -> float:
    return round((p.wing - credit) * lot_size * lots + entry_costs_total
                 + settlement_allowance(legs, lot_size * lots, p, sched), 2)


def leg_costs(legs: list[dict], qty: int, sched: FnoSchedule) -> dict[str, dict]:
    return {g["role"]: option_order_costs(g["price"], qty, g["side"], sched).as_dict() for g in legs}


# -- entry (6-8) -------------------------------------------------------------

def plan_entry(master: ContractMaster, expiry: date, index: IndexQuote,
               fetch: Callable[[list[OptionContract]], dict[str, Quote]],
               sched: FnoSchedule, p: VParams = REGISTERED,
               snapshot: dict | None = None) -> Plan | Skip:
    """
    `index` is read first; its fetch time is the pass time T (7). `snapshot`, if
    given, collects every quote read, so the journal can replay the pass (17).
    """
    snap = snapshot if snapshot is not None else {}
    snap["index"] = index.as_dict()
    t = index.fetched_at
    flags: list[str] = []

    def clock_problem(ts: datetime | None, what: str) -> Skip | None:
        if ts is not None and (ts - t).total_seconds() > FUTURE_TOLERANCE_S:
            return Skip("clock", f"{what} stamped {int((ts - t).total_seconds())}s after the pass time: "
                                 "the machine clock is behind")
        return None

    if index.exchange_ts is not None:
        if (t - index.exchange_ts).total_seconds() > p.max_quote_age_s:
            return Skip("index_stale", f"index value {int((t - index.exchange_ts).total_seconds())}s old")
        if (bad := clock_problem(index.exchange_ts, "the index value")):
            return bad
    s = round(index.value, 2)

    both = sorted(set(master.strikes(expiry, "CE")) & set(master.strikes(expiry, "PE")))
    if not both:
        return Skip("no_strikes", f"no strike for {expiry} lists both a call and a put")
    k_atm = min(both, key=lambda k: (abs(milli(k) - milli(s)), k))      # tie -> the lower strike
    atm = [master.get(expiry, k_atm, "CE"), master.get(expiry, k_atm, "PE")]
    quotes = fetch(atm)
    snap["atm"] = {c.symbol: (quotes[c.symbol].as_dict() if c.symbol in quotes else None) for c in atm}
    atm_q = {}
    for c in atm:
        q = quotes.get(c.symbol)
        if q is None:
            return Skip("no_quote", f"the feed returned no quote for {c.symbol}")
        if (bad := clock_problem(q.exchange_ts, c.symbol)):
            return bad
        if q.exchange_ts is not None and (t - q.exchange_ts).total_seconds() > p.max_quote_age_s:
            # The at-the-money pair trades continuously: a stale one is the feed, not the market.
            return Skip("stale_snapshot", f"{c.symbol} quote {int((t - q.exchange_ts).total_seconds())}s old")
        why = quote_problem(q, t, p)
        if why:
            return Skip("atm_quote", f"{c.symbol}: {why}")
        atm_q[c.right] = q
    em_m = (milli(atm_q["CE"].bid) + milli(atm_q["CE"].ask) + milli(atm_q["PE"].bid) + milli(atm_q["PE"].ask)) / 2
    em = em_m / 1000

    calls, puts = master.strikes(expiry, "CE"), master.strikes(expiry, "PE")
    up = [k for k in calls if milli(k) >= milli(s) + em_m]
    down = [k for k in puts if milli(k) <= milli(s) - em_m]
    if not up or not down:
        return Skip("short_not_listed", f"no listed strike beyond S +- EM ({s:.2f} +- {em:.3f})")
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
    snap["legs"] = {r: (quotes[c.symbol].as_dict() if c.symbol in quotes else None) for r, c in contracts.items()}
    leg_q = {}
    for role, _, _ in ROLES:
        q = quotes.get(contracts[role].symbol)
        if q is None:
            return Skip("no_quote", f"the feed returned no quote for {role} {contracts[role].symbol}")
        if (bad := clock_problem(q.exchange_ts, contracts[role].symbol)):
            return bad
        why = quote_problem(q, t, p)
        if why:
            return Skip("leg_quote", f"{role} {contracts[role].symbol}: {why}")
        leg_q[role] = q
    every = list(leg_q.values()) + list(atm_q.values())
    if max(q.fetched_at for q in every) > t + timedelta(seconds=p.max_snapshot_s):
        return Skip("slow_snapshot", f"option quotes read more than {p.max_snapshot_s}s after the index value")
    stamps = [q.exchange_ts for q in every if q.exchange_ts is not None]
    if index.exchange_ts is None:
        flags.append("index value has no exchange timestamp; the freshest option quote is the clock check")
        if not stamps:
            return Skip("no_timestamp_today", "neither the index nor any option quote carries a timestamp")
        if (t - max(stamps)).total_seconds() > p.max_quote_age_s:
            return Skip("stale_snapshot", f"the freshest option quote is {int((t - max(stamps)).total_seconds())}s old")
    if not any(ts.date() == t.date() for ts in stamps + ([index.exchange_ts] if index.exchange_ts else [])):
        return Skip("no_timestamp_today", "neither the index nor any option quote carries a timestamp dated today")
    if any(q.exchange_ts is None for q in every):
        flags.append("an option quote has no exchange timestamp; fetch time used")

    credit_m = milli(leg_q["short_call"].bid) + milli(leg_q["short_put"].bid) \
        - milli(leg_q["long_call"].ask) - milli(leg_q["long_put"].ask)
    credit = credit_m / 1000
    if credit_m > milli(p.max_credit):
        return Skip("credit_above_cap", f"credit {credit:.2f} > {p.max_credit:.2f}: bad data")
    if credit_m < milli(p.min_credit):
        return Skip("credit_below_floor", f"credit {credit:.2f} < {p.min_credit:.2f}", {"credit": credit})

    legs = [{
        "role": role, "symbol": contracts[role].symbol, "exchange": contracts[role].exchange,
        "strike": contracts[role].strike, "right": right, "side": side,
        "price": leg_q[role].bid if side == "SELL" else leg_q[role].ask,
        "mid": (milli(leg_q[role].bid) + milli(leg_q[role].ask)) / 2000,
    } for role, right, side in ROLES]

    freeze = [c.freeze_qty for c in contracts.values() if c.freeze_qty]
    chosen = None
    for n in range(p.max_lots, 0, -1):
        if freeze and n * q_lot > min(freeze):
            continue
        costs = leg_costs(legs, n * q_lot, sched)
        total = round(sum(c["total"] for c in costs.values()), 2)
        m = max_loss(credit, legs, q_lot, n, total, p, sched)
        if m <= p.risk_budget:
            chosen = (n, costs, total, m)
            break
    if chosen is None:
        costs1 = leg_costs(legs, q_lot, sched)
        m1 = max_loss(credit, legs, q_lot, 1, round(sum(c["total"] for c in costs1.values()), 2), p, sched)
        return Skip("zero_lots", f"1 lot needs Rs{m1:,.2f} of maximum loss; the budget is Rs{p.risk_budget:,.2f}",
                    {"max_loss_1_lot": m1, "budget": p.risk_budget, "credit": credit, "lot_size": q_lot})
    n, costs, total, m = chosen

    for g in legs:
        why = touch_problem(leg_q[g["role"]], g["side"], n * q_lot)
        if why:
            return Skip("touch_size", f"{g['role']}: {why}")
    if any((leg_q[r].bid_qty if sd == "SELL" else leg_q[r].ask_qty) is None for r, _, sd in ROLES):
        flags.append("touch size unknown")

    return Plan(
        expiry=expiry, index=index.as_dict(), atm_strike=k_atm,
        atm_quotes={r: q.as_dict() for r, q in atm_q.items()}, expected_move=em, legs=legs,
        credit=credit, lot_size=q_lot, lots=n, qty=n * q_lot, entry_costs=costs,
        entry_costs_total=total, settlement_allowance=settlement_allowance(legs, n * q_lot, p, sched),
        max_loss=m, budget=p.risk_budget, flags=flags,
    )


# -- settlement (10) ---------------------------------------------------------

def settle(trade: dict, value: float, sched: FnoSchedule) -> dict:
    """Cash settlement at the final settlement price `value`. `trade` is a book record."""
    qty = trade["qty"]
    gross_pts = trade["credit"]
    legs = []
    for g in trade["legs"]:
        i = intrinsic(g["right"], g["strike"], value)
        gross_pts += i if g["side"] == "BUY" else -i
        legs.append({"role": g["role"], "intrinsic": i})
    costs = settlement_costs(trade["legs"], qty, value, sched)
    gross = round(gross_pts * qty, 2)
    settle_total = round(sum(c["total"] for c in costs.values()), 2)
    net = round(gross - trade["entry_costs_total"] - settle_total, 2)
    return {"value": value, "legs": legs, "gross_points": round(gross_pts, 3), "gross": gross,
            "settlement_costs": costs, "settlement_costs_total": settle_total,
            "net": net, "R": net / trade["max_loss"]}
