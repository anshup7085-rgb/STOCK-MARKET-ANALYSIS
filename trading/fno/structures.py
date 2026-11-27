"""
Multi-leg F&O structures for the intraday engine (trading/fno/explore.py): any
mix of futures and option legs, chosen by rule from the day's contract master and
a live snapshot, with the arithmetic every trade needs before it opens:

  - which contracts: expiry and strike selectors (ATM +- steps, multiples of the
    at-the-money straddle, a target premium);
  - what it can lose: the expiry payoff over a +-20% band of the index, and
    whether the loss is bounded by the structure (defined) or only by a stop;
  - what it is worth now: a mark at the touch (longs at the bid, shorts at the
    ask), net of every cost on the fee card, entry and exit.

Pure functions; no feed, no clock, no file. Nothing here places an order.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date

from market.charges import FnoSchedule, futures_order_costs, option_order_costs
from trading.fno.model import ContractMaster, OptionContract, Quote

KINDS = ("FUT", "CE", "PE")
SIDES = ("BUY", "SELL")
BAND = 0.20                 # payoff examined over +-20% of the index: beyond NSE's market-wide halt levels


@dataclass(frozen=True)
class LegSpec:
    kind: str                # FUT | CE | PE
    side: str                # BUY | SELL
    ratio: int = 1           # lots of this leg per unit of the structure
    expiry: str = "weekly"   # weekly | next_weekly | monthly
    strike: str = "ATM"      # ATM, ATM+2, ATM-1 (strike steps); EM+1.0 (straddles from S); PREM:20 (premium <= 20)

    def __post_init__(self):
        if self.kind not in KINDS or self.side not in SIDES or self.ratio < 1:
            raise ValueError(f"bad leg {self}")
        if self.kind == "FUT" and (self.expiry != "monthly" or self.strike != "ATM"):
            raise ValueError("a futures leg trades the near-month contract and takes no strike rule")
        if self.kind != "FUT" and not re.fullmatch(r"ATM([+-]\d+)?|EM[+-]\d+(\.\d+)?|PREM:\d+(\.\d+)?", self.strike):
            raise ValueError(f"unknown strike rule {self.strike!r}")

    @property
    def label(self) -> str:
        sign = "+" if self.side == "BUY" else "-"
        what = "FUT" if self.kind == "FUT" else f"{self.kind}@{self.strike}"
        return f"{sign}{self.ratio if self.ratio > 1 else ''}{what}"


def parse_legs(text: str) -> tuple[LegSpec, ...]:
    """
    A compact notation, one leg per token:  "FUT+ CE-@ATM+2"  "CE+@ATM PE+@ATM"
    Sign after the kind: + buy, - sell; optional ratio digit after the sign;
    optional @rule; optional /expiry (weekly, next_weekly, monthly).
    """
    legs = []
    for tok in text.split():
        m = re.fullmatch(r"(FUT|CE|PE)([+-])(\d*)(?:@([^/]+))?(?:/(weekly|next_weekly|monthly))?", tok)
        if not m:
            raise ValueError(f"cannot read leg {tok!r}")
        kind, sign, ratio, rule, exp = m.groups()
        if kind == "FUT" and rule:
            raise ValueError(f"{tok!r}: a futures leg takes no strike rule")
        legs.append(LegSpec(kind, "BUY" if sign == "+" else "SELL", int(ratio or 1),
                            exp or ("monthly" if kind == "FUT" else "weekly"), rule or "ATM"))
    return tuple(legs)


# -- selection -------------------------------------------------------------------

def option_expiries(master: ContractMaster, today: date) -> list[date]:
    return sorted({c.expiry for c in master.contracts if c.right in ("CE", "PE") and c.expiry >= today})


def futures_expiries(master: ContractMaster, today: date) -> list[date]:
    return sorted({c.expiry for c in master.contracts if c.right == "FUT" and c.expiry >= today})


def pick_expiry(master: ContractMaster, spec: LegSpec, today: date) -> date | None:
    if spec.kind == "FUT":
        xs = futures_expiries(master, today)
        return xs[0] if xs else None
    xs = option_expiries(master, today)
    if spec.expiry == "weekly":
        return xs[0] if xs else None
    if spec.expiry == "next_weekly":
        return xs[1] if len(xs) > 1 else None
    fut = futures_expiries(master, today)          # the monthly option expiry is the futures expiry
    return fut[0] if fut and fut[0] in xs else None


def strike_step(strikes: list[float], near: float) -> float | None:
    ks = sorted(strikes)
    gaps = [b - a for a, b in zip(ks, ks[1:]) if abs(a - near) <= 1000]
    return min(gaps) if gaps else None


def atm_strike(master: ContractMaster, expiry: date, spot: float) -> float | None:
    both = sorted(set(master.strikes(expiry, "CE")) & set(master.strikes(expiry, "PE")))
    return min(both, key=lambda k: (abs(k - spot), k)) if both else None


def pick_strike(master: ContractMaster, spec: LegSpec, expiry: date, spot: float,
                straddle: float | None, quotes: dict[str, Quote] | None = None) -> float | None:
    """The strike a rule names, or None if the master does not list it."""
    listed = master.strikes(expiry, spec.kind)
    if not listed:
        return None
    atm = atm_strike(master, expiry, spot)
    if atm is None:
        return None
    rule = spec.strike
    if rule.startswith("ATM"):
        step = strike_step(listed, atm)
        n = int(rule[3:] or 0)
        k = atm + n * step if step else None
        return k if k in listed else None
    if rule.startswith("EM"):
        if not straddle:
            return None
        target = spot + float(rule[2:]) * straddle
        up = [k for k in listed if k >= target] if spec.kind == "CE" else []
        down = [k for k in listed if k <= target] if spec.kind == "PE" else []
        if spec.kind == "CE":
            return up[0] if up else None
        return down[-1] if down else None
    cap = float(rule.split(":")[1])                 # PREM:x -> the listed strike whose mid is closest below x
    if quotes is None:
        return None
    best = None
    for k in listed:
        c = master.get(expiry, k, spec.kind)
        q = quotes.get(c.symbol) if c else None
        if q and q.bid and q.ask and q.ask > q.bid:
            mid = (q.bid + q.ask) / 2
            if mid <= cap and (best is None or mid > best[1]):
                best = (k, mid)
    return best[0] if best else None


# -- payoff and risk -----------------------------------------------------------------

@dataclass
class Leg:
    """One filled leg of an open structure."""
    kind: str
    side: str
    symbol: str
    exchange: str
    expiry: str
    strike: float
    lots: int
    lot_size: int
    price: float              # entry fill (touch)
    mid: float
    quote: dict = field(default_factory=dict)

    @property
    def qty(self) -> int:
        return self.lots * self.lot_size

    @property
    def sign(self) -> int:
        return 1 if self.side == "BUY" else -1


def leg_value(g: Leg, s_t: float, spot: float) -> float:
    """
    A leg's value per unit if the index goes from `spot` to `s_t` and every option
    is at its expiry value. The future moves one-for-one with the index from its
    own fill, its basis held fixed: inside a day the basis barely moves, and
    valuing a future at the index would book its basis as a phantom loss.
    """
    if g.kind == "FUT":
        return g.price + (s_t - spot)
    return max(0.0, s_t - g.strike) if g.kind == "CE" else max(0.0, g.strike - s_t)


def payoff(legs: list[Leg], s_t: float, spot: float) -> float:
    """Rupee P&L, before costs, with the index at s_t and the options at expiry value."""
    return sum(g.sign * (leg_value(g, s_t, spot) - g.price) * g.qty for g in legs)


def slopes(legs: list[Leg]) -> tuple[float, float]:
    """
    Rupees gained per index point beyond the outermost strikes: (as the index
    rises, as it falls). A negative slope is a loss that grows without limit.
    """
    up = sum(g.sign * g.qty for g in legs if g.kind in ("FUT", "CE"))
    down = sum(g.sign * g.qty for g in legs if g.kind == "PE") - sum(g.sign * g.qty for g in legs if g.kind == "FUT")
    return up, down


def kinks(legs: list[Leg], spot: float) -> list[float]:
    lo, hi = spot * (1 - BAND), spot * (1 + BAND)
    return sorted({lo, hi, spot} | {g.strike for g in legs if g.kind != "FUT" and lo < g.strike < hi})


def structural_risk(legs: list[Leg], spot: float) -> tuple[float, bool]:
    """
    (worst loss in rupees over the +-20% band, defined?), before costs.

    Defined means the loss stops growing at both ends: the options share one
    expiry (a calendar's value before the near expiry is not its payoff) and
    neither slope beyond the outermost strikes points down. For a defined
    structure of one expiry the band's worst case bounds the loss at any time,
    not just at expiry: the structure's value cannot leave its payoff's range.
    The payoff is piecewise linear, so its strikes and the band's ends find the
    exact minimum.
    """
    if len({g.expiry for g in legs if g.kind != "FUT"}) > 1:
        return math.inf, False
    worst = min(payoff(legs, x, spot) for x in kinks(legs, spot))
    up, down = slopes(legs)
    return max(0.0, -worst), up >= 0 and down >= 0


def breakevens(legs: list[Leg], spot: float) -> list[float]:
    """Index values inside the band where the expiry payoff, before costs, crosses zero."""
    xs = kinks(legs, spot)
    ys = [payoff(legs, x, spot) for x in xs]
    out = []
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if y0 == 0:
            out.append(x0)
        elif y0 * y1 < 0:
            out.append(x0 + (x1 - x0) * y0 / (y0 - y1))
    if ys and ys[-1] == 0:
        out.append(xs[-1])
    return [round(x, 2) for x in sorted(set(out))]


def net_premium(legs: list[Leg]) -> float:
    """Rupees received (+) or paid (-) for the option legs at their fills."""
    return round(sum(-g.sign * g.price * g.qty for g in legs if g.kind != "FUT"), 2)


def order_costs(g: Leg, side: str, price: float, sched: FnoSchedule) -> float:
    fn = futures_order_costs if g.kind == "FUT" else option_order_costs
    return fn(price, g.qty, side, sched).total


def entry_costs(legs: list[Leg], sched: FnoSchedule) -> float:
    return round(sum(order_costs(g, g.side, g.price, sched) for g in legs), 2)


def close_prices(legs: list[Leg], quotes: dict[str, Quote]) -> dict[str, float] | None:
    """What closing each leg at the touch would fill at: longs sell at the bid, shorts buy at the ask."""
    out = {}
    for g in legs:
        q = quotes.get(g.symbol)
        px = (q.bid if g.side == "BUY" else q.ask) if q else None
        if px is None or px <= 0:
            if g.kind != "FUT" and g.side == "BUY" and q is not None and q.ask:
                px = 0.0                               # a long option with no bid is worth nothing to us now
            else:
                return None
        out[g.symbol] = px
    return out


def mark(legs: list[Leg], closes: dict[str, float], sched: FnoSchedule, entry_costs_total: float) -> dict:
    """Net P&L if the structure were closed now at `closes`, every cost included."""
    gross = sum(g.sign * (closes[g.symbol] - g.price) * g.qty for g in legs)
    exit_costs = sum(order_costs(g, "SELL" if g.side == "BUY" else "BUY", closes[g.symbol], sched)
                     for g in legs if closes[g.symbol] > 0)
    net = gross - entry_costs_total - exit_costs
    return {"gross": round(gross, 2), "exit_costs": round(exit_costs, 2), "net": round(net, 2)}


def legs_as_dicts(legs: list[Leg]) -> list[dict]:
    return [asdict(g) for g in legs]


def legs_from_dicts(rows: list[dict]) -> list[Leg]:
    return [Leg(**r) for r in rows]


def contract_for(master: ContractMaster, spec: LegSpec, expiry: date, strike: float | None) -> OptionContract | None:
    if spec.kind == "FUT":
        return master.get(expiry, 0.0, "FUT")
    return master.get(expiry, strike, spec.kind) if strike is not None else None
