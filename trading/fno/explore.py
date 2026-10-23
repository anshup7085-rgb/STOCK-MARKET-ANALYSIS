"""
The intraday F&O explorer: paper trading of many multi-leg strategies at once
on NIFTY futures and options, each entered and squared off inside one session.

    EXPLORATION, NOT EVIDENCE. Many strategies watched on the same days will
    produce a winner by luck alone. A strategy earns a claim only through its
    own pre-registered forward test (forward slots 2-10 in ROADMAP.md); the
    screen below says which ones have earned that test, nothing more.

What it does on each pass (one a minute from the runner):

  1. reads the index and appends it to the day's tick file (ticks/DATE.jsonl);
  2. marks every open position at the touch (longs at the bid, shorts at the
     ask) net of every cost on the fee card, and closes it on its stop, trail,
     target, square-off time, the day's loss limit or the kill switch;
  3. for each strategy inside its window with its trigger met, picks the
     contracts by rule, checks each quote (two-sided, tight, fresh, deep enough
     for the order), sizes the structure to its risk budget and the margin
     left, and fills it at the touch.

The decisions (entries, exits, skips, halts) go to a hash-chained journal,
`journal.jsonl`; the observations (index, marks) to the tick files. Nothing
here places an order: there is no broker import in this module.

Risk, per trade, is fixed at entry in rupees:
  - defined structures (spreads, condors, flies, futures hedged by an option):
    the worst loss over a +-20% index band plus round-trip costs; the
    structure's value cannot leave its payoff's range, so this bounds the loss
    at any moment, not only at expiry;
  - undefined ones (futures, short straddles, covered calls): the stop times
    1.5, for the slippage of a stop checked once a pass, plus round-trip costs.
R = net / risk. The screen uses the anytime-valid betting statistic of
research/fno_stats.py on each strategy's R in trade order, at alpha / m for m
strategies in the configuration (e-values with a Bonferroni split); a trade
below R = -2 voids a strategy's screen, as it breaks the bound the statistic
needs.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable

from market.calendar import TradingCalendar, load_calendar
from market.charges import FNO_SCHEDULES, FNO_ZERODHA, FnoSchedule
from market.config import IST
from market.providers.base import DataUnavailable
from research.fno_stats import LAMBDA_CAP, betting_path
from trading.fno.book import exclusive
from trading.fno.feeds import FnoFeed
from trading.fno.library import template
from trading.fno.model import ContractMaster, OptionContract, Quote
from trading.fno.structures import (Leg, LegSpec, atm_strike, breakevens, close_prices, contract_for, entry_costs,
                                    legs_as_dicts, legs_from_dicts, mark, net_premium, order_costs, parse_legs,
                                    payoff, pick_expiry, pick_strike, slopes, strike_step, structural_risk)
from trading.journal import Journal
from trading.risk import KillSwitch

MOCK_SUFFIX = "_mock"
OPEN, LAST_CALL = time(9, 15), time(15, 29)
STOP_SLIP = 1.5             # an undefined-risk stop is checked once a pass: a loss can run past it
MARGIN_PCT = 0.12           # estimate per future or short option, hedges ignored: not the broker's figure
FALLBACK_MARK_MIN = 15      # a forced close with no quotes uses a mark at most this old (one from just
                            # before a 15:15 square-off, at the 15:29 last call), else -1 R
FUTURE_TOLERANCE_S = 5


# -- configuration ------------------------------------------------------------------

def _hhmm(x: str | time) -> time:
    return x if isinstance(x, time) else time.fromisoformat(x)


@dataclass(frozen=True)
class Rule:
    """A target or stop: kind R | credit | points | rupees, and its size."""
    kind: str
    x: float

    @staticmethod
    def parse(d: dict | None, what: str) -> Rule | None:
        if not d:
            return None
        if len(d) != 1:
            raise ValueError(f"{what}: one of R, credit_frac, credit_mult, points, rupees — not {d}")
        (k, v), = d.items()
        ok = {"target": {"R": "R", "credit_frac": "credit", "points": "points", "rupees": "rupees"},
              "stop": {"R": "R", "credit_mult": "credit", "points": "points", "rupees": "rupees"}}[what]
        if k not in ok or not isinstance(v, (int, float)) or not v > 0:
            raise ValueError(f"{what}: {k}={v!r} is not one of {sorted(ok)} with a positive size")
        return Rule(ok[k], float(v))

    def rupees(self, risk: float, credit: float, lot_size: int, units: int) -> float | None:
        if self.kind == "R":
            return self.x * risk
        if self.kind == "credit":
            return self.x * credit if credit > 0 else None
        if self.kind == "points":
            return self.x * lot_size * units
        return self.x * units

    def as_dict(self) -> dict:
        return {"kind": self.kind, "x": self.x}


@dataclass(frozen=True)
class Trigger:
    type: str = "at"                 # at | orb | move | range
    range_end: time = time(9, 30)    # orb: the opening range runs from 09:15 to here
    buffer_pct: float = 0.0          # orb: a break must clear the range by this % of it
    pct: float = 0.0                 # move: |index / open - 1| >= pct %; range: <= pct %

    @staticmethod
    def parse(d: dict) -> Trigger:
        t = Trigger(type=d.get("type", "at"), range_end=_hhmm(d.get("range_end", "09:30")),
                    buffer_pct=float(d.get("buffer_pct", 0.0)), pct=float(d.get("pct", 0.0)))
        if t.type not in ("at", "orb", "move", "range"):
            raise ValueError(f"unknown trigger {t.type!r}: at, orb, move or range")
        if t.type in ("move", "range") and not t.pct > 0:
            raise ValueError(f"a {t.type} trigger needs pct > 0")
        return t


@dataclass(frozen=True)
class Strategy:
    name: str
    legs: dict                       # direction -> tuple[LegSpec, ...]
    trigger: Trigger
    start: time
    end: time
    exit_time: time
    target: Rule | None
    stop: Rule | None
    trail: tuple[float, float] | None    # (arm at R, then exit at R)
    risk_budget: float
    max_units: int
    max_entries: int
    skip_expiry_day: bool
    about: str = ""

    def legs_for(self, direction: str) -> tuple[LegSpec, ...] | None:
        return self.legs.get(direction) or self.legs.get("any")


def spec_defined(specs: tuple[LegSpec, ...]) -> bool:
    """Whether a structure's loss is bounded, from its legs alone (equal lot sizes assumed)."""
    if len({s.expiry for s in specs if s.kind != "FUT"}) > 1:
        return False
    fake = [Leg(s.kind, s.side, "", "", "", 0.0, s.ratio, 1, 0.0, 0.0) for s in specs]
    up, down = slopes(fake)
    return up >= 0 and down >= 0


def _strategy(d: dict, square_off: time) -> Strategy:
    base = dict(template(d["template"])) if d.get("template") else {}
    base.update({k: v for k, v in d.items() if k != "template"})
    name = base.get("name") or d.get("template")
    if not name:
        raise ValueError("a strategy needs a name")
    legs_raw = base.get("legs")
    if not legs_raw:
        raise ValueError(f"{name}: no legs")
    legs_map = legs_raw if isinstance(legs_raw, dict) else {"any": legs_raw}
    legs = {}
    for k, v in legs_map.items():
        if k not in ("long", "short", "any"):
            raise ValueError(f"{name}: legs keys are long, short, any — not {k!r}")
        legs[k] = parse_legs(v)
    ex = base.get("exit", {})
    target, stop = Rule.parse(ex.get("target"), "target"), Rule.parse(ex.get("stop"), "stop")
    for k, specs in legs.items():
        if not spec_defined(specs) and (stop is None or stop.kind == "R"):
            raise ValueError(f"{name} ({k}): {' '.join(s.label for s in specs)} has undefined risk; it needs a stop "
                             "in credit_mult, points or rupees (its risk is the stop)")
    trail = ex.get("trail")
    if trail is not None:
        trail = (float(trail["after_R"]), float(trail["lock_R"]))
        if not trail[0] > trail[1]:
            raise ValueError(f"{name}: a trail arms above the level it locks")
    window = base.get("window", ["09:20", "14:30"])
    s = Strategy(
        name=name, legs=legs, trigger=Trigger.parse(base.get("trigger", {})),
        start=_hhmm(window[0]), end=_hhmm(window[1]),
        exit_time=min(_hhmm(ex.get("time", square_off.isoformat(timespec="minutes"))), square_off),
        target=target, stop=stop, trail=trail, risk_budget=float(base.get("risk_budget", 2500.0)),
        max_units=int(base.get("max_units", 5)), max_entries=int(base.get("max_entries", 1)),
        skip_expiry_day=bool(base.get("skip_expiry_day", False)), about=base.get("about", ""),
    )
    if not (OPEN <= s.start <= s.end < s.exit_time):
        raise ValueError(f"{name}: the window must open after 09:15 and close before the exit time")
    if s.trigger.type == "orb" and s.start < s.trigger.range_end:
        raise ValueError(f"{name}: the window opens before the opening range ends")
    if s.risk_budget <= 0 or s.max_units < 1 or s.max_entries < 1:
        raise ValueError(f"{name}: risk_budget, max_units and max_entries must be positive")
    return s


@dataclass(frozen=True)
class Config:
    underlying: str
    capital: float
    daily_loss_limit: float
    max_open: int
    max_margin_frac: float
    square_off: time
    screen_alpha: float
    max_quote_age_s: float
    spread_abs: float
    spread_frac: float
    fut_spread_frac: float
    window_steps: int
    fee_card: str | None
    strategies: tuple[Strategy, ...]
    raw: dict = field(compare=False, repr=False)

    @property
    def sha(self) -> str:
        return hashlib.sha256(json.dumps(self.raw, sort_keys=True).encode()).hexdigest()


def load_config(src: dict | str | Path) -> Config:
    raw = src if isinstance(src, dict) else json.loads(Path(src).read_text())
    q = raw.get("quotes", {})
    square_off = _hhmm(raw.get("square_off", "15:15"))
    if not (OPEN < square_off < LAST_CALL):
        raise ValueError("square_off must fall inside the session, before 15:29")
    strategies = tuple(_strategy(d, square_off) for d in raw.get("strategies", []) if d.get("enabled", True))
    names = [s.name for s in strategies]
    if len(set(names)) != len(names):
        raise ValueError(f"strategy names repeat: {sorted(n for n, c in Counter(names).items() if c > 1)}")
    if not strategies:
        raise ValueError("no enabled strategy")
    cfg = Config(
        underlying=raw.get("underlying", "NIFTY"), capital=float(raw.get("capital", 500_000)),
        daily_loss_limit=float(raw.get("daily_loss_limit", 15_000)), max_open=int(raw.get("max_open", 4)),
        max_margin_frac=float(raw.get("max_margin_frac", 0.8)), square_off=square_off,
        screen_alpha=float(raw.get("screen_alpha", 0.05)), max_quote_age_s=float(q.get("max_age_s", 60)),
        spread_abs=float(q.get("spread_abs", 1.0)), spread_frac=float(q.get("spread_frac", 0.10)),
        fut_spread_frac=float(q.get("fut_spread_frac", 0.0002)), window_steps=int(q.get("window_steps", 15)),
        fee_card=raw.get("fee_card"), strategies=strategies, raw=raw,
    )
    if cfg.capital <= 0 or cfg.daily_loss_limit <= 0 or not 0 < cfg.max_margin_frac <= 1 or cfg.max_open < 1:
        raise ValueError("capital, daily_loss_limit, max_open must be positive and max_margin_frac in (0, 1]")
    return cfg


# -- what the journal says ------------------------------------------------------------

@dataclass
class XState:
    init: dict | None = None
    config_sha: str | None = None
    generations: list[dict] = field(default_factory=list)      # {seq, sha, config}
    open: dict[str, dict] = field(default_factory=dict)        # id -> entry payload
    closed: list[dict] = field(default_factory=list)           # entry payload + "exit"
    entries: Counter = field(default_factory=Counter)          # (day, strategy) -> n
    halted: dict[str, dict] = field(default_factory=dict)      # day -> halt payload
    skips: set[tuple[str, str, str]] = field(default_factory=set)   # (day, strategy, code), journaled once a day
    masters: dict[str, str] = field(default_factory=dict)
    void: list[str] = field(default_factory=list)

    @staticmethod
    def from_records(records: list[dict]) -> XState:
        st = XState()
        for r in records:
            k, p = r.get("type"), r.get("payload", {})
            if k == "XP_INIT" and st.init is None:
                st.init = p
            elif k == "XP_CONFIG":
                st.config_sha = p["sha"]
                st.generations.append({"seq": r["seq"], "sha": p["sha"], "config": p["config"]})
            elif k == "XP_MASTER":
                st.masters[p["date"]] = p["sha256"]
            elif k == "XP_ENTRY":
                if p["id"] in st.open or any(c["id"] == p["id"] for c in st.closed):
                    st.void.append(f"seq {r['seq']}: a second entry {p['id']}")
                    continue
                st.open[p["id"]] = dict(p)
                st.entries[(p["day"], p["strategy"])] += 1
            elif k == "XP_EXIT":
                e = st.open.pop(p["id"], None)
                if e is None:
                    st.void.append(f"seq {r['seq']}: an exit for {p['id']}, which is not open")
                    continue
                e["exit"] = p
                st.closed.append(e)
            elif k == "XP_SKIP":
                st.skips.add((p["day"], p["strategy"], p["code"]))
            elif k == "XP_HALT":
                st.halted.setdefault(p["day"], p)
        return st

    def realised(self, day: str) -> float:
        return round(sum(c["exit"]["net"] for c in self.closed if c["exit"]["day"] == day), 2)

    @property
    def realised_total(self) -> float:
        return round(sum(c["exit"]["net"] for c in self.closed), 2)


class Ticks:
    """The day's observations: one line a pass with the index and each open position's mark. Not the record."""

    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir) / "ticks"

    def append(self, day: date, rec: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / f"{day.isoformat()}.jsonl").open("a") as fh:
            fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")

    def read(self, day: date | str) -> list[dict]:
        path = self.dir / f"{day if isinstance(day, str) else day.isoformat()}.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


# -- triggers --------------------------------------------------------------------------

def session_open(ticks: list[dict]) -> tuple[float | None, str]:
    """The day's first index reading, if the runner saw the open (a reading by 09:17)."""
    first = next((t for t in ticks if time.fromisoformat(t["t"][11:19]) >= OPEN), None)
    if first is None or time.fromisoformat(first["t"][11:19]) > time(9, 17):
        return None, "the open was not observed (no reading between 09:15 and 09:17)"
    return float(first["spot"]), ""


def fire(trig: Trigger, ticks: list[dict], spot: float, now: datetime) -> tuple[str | None, str, dict]:
    """(direction or None, why not, evidence). Reads only readings already taken today."""
    if trig.type == "at":
        return "any", "", {}
    open_, why = session_open(ticks)
    if open_ is None:
        return None, why, {}
    change = (spot / open_ - 1) * 100
    if trig.type == "move":
        ev = {"open": open_, "change_pct": round(change, 3)}
        if abs(change) >= trig.pct:
            return ("long" if change > 0 else "short"), "", ev
        return None, f"index {change:+.2f}% from the open, needs {trig.pct}%", ev
    if trig.type == "range":
        ev = {"open": open_, "change_pct": round(change, 3)}
        if abs(change) <= trig.pct:
            return "any", "", ev
        return None, f"index {change:+.2f}% from the open, outside {trig.pct}%", ev
    if now.time() < trig.range_end:
        return None, "the opening range is still forming", {}
    rng = [float(t["spot"]) for t in ticks if OPEN <= time.fromisoformat(t["t"][11:19]) < trig.range_end]
    last = max((time.fromisoformat(t["t"][11:19]) for t in ticks
                if time.fromisoformat(t["t"][11:19]) < trig.range_end), default=None)
    end_dt = datetime.combine(now.date(), trig.range_end)
    if len(rng) < 3 or last is None or (end_dt - datetime.combine(now.date(), last)).total_seconds() > 180:
        return None, "the opening range was not observed through its end", {}
    hi, lo = max(rng), min(rng)
    ev = {"open": open_, "range_high": hi, "range_low": lo, "readings": len(rng)}
    if spot > hi * (1 + trig.buffer_pct / 100):
        return "long", "", ev
    if spot < lo * (1 - trig.buffer_pct / 100):
        return "short", "", ev
    return None, f"index {spot:.2f} inside the opening range {lo:.2f}-{hi:.2f}", ev


# -- contracts ------------------------------------------------------------------------------

def resolve(master: ContractMaster, specs: tuple[LegSpec, ...], spot: float, today: date,
            quotes: dict[str, Quote], window: int) -> tuple[list | None, set[OptionContract], str]:
    """
    The contracts a structure's rules name: ([(spec, contract)] or None, the
    contracts whose quotes the rules still need, why not). Rules that read
    prices (EM, PREM) name their quotes first and resolve on the second call.
    """
    out, needs, why = [], set(), ""
    for sp in specs:
        e = pick_expiry(master, sp, today)
        if e is None:
            why = why or f"no {sp.expiry} expiry for {sp.label}"
            continue
        if sp.kind == "FUT":
            c = contract_for(master, sp, e, None)
            if c is None:
                why = why or "no future listed"
            else:
                out.append((sp, c))
            continue
        straddle = None
        if sp.strike.startswith("EM"):
            atm = atm_strike(master, e, spot)
            pair = [master.get(e, atm, "CE"), master.get(e, atm, "PE")] if atm is not None else []
            needs.update(c for c in pair if c)
            mids = [quotes[c.symbol].mid for c in pair if c and c.symbol in quotes]
            if len(mids) == 2 and all(m for m in mids):
                straddle = sum(mids)
            else:
                why = why or "the at-the-money straddle is not quoted"
                continue
        if sp.strike.startswith("PREM"):
            listed, atm = master.strikes(e, sp.kind), atm_strike(master, e, spot)
            step = strike_step(listed, atm) if atm is not None else None
            if step:
                needs.update(c for k in listed if abs(k - atm) <= window * step
                             for c in [master.get(e, k, sp.kind)] if c)
        k = pick_strike(master, sp, e, spot, straddle, quotes)
        c = contract_for(master, sp, e, k) if k is not None else None
        if c is None:
            why = why or f"no listed strike for {sp.label}"
            continue
        out.append((sp, c))
    return (None if why else out), needs, why


def quote_problem(q: Quote | None, now: datetime, side: str, qty: int, is_future: bool, cfg: Config) -> str:
    if q is None:
        return "no quote"
    if not q.bid or not q.ask or q.ask < q.bid:
        return "not two-sided"
    mid = (q.bid + q.ask) / 2
    limit = max(cfg.spread_abs, (cfg.fut_spread_frac if is_future else cfg.spread_frac) * mid)
    if q.ask - q.bid > limit + 1e-9:
        return f"spread {q.ask - q.bid:.2f} wider than {limit:.2f}"
    if q.exchange_ts is None:
        return "no exchange timestamp"
    age = (now - q.exchange_ts).total_seconds()
    if age > cfg.max_quote_age_s:
        return f"quote {age:.0f}s old"
    if age < -FUTURE_TOLERANCE_S:
        return "quote stamped after the pass: clock"
    depth = q.ask_qty if side == "BUY" else q.bid_qty
    if depth is not None and depth < qty:
        return f"top of book {depth} < order {qty}"
    return ""


def build(pairs: list, quotes: dict[str, Quote], units: int) -> list[Leg]:
    out = []
    for sp, c in pairs:
        q = quotes[c.symbol]
        out.append(Leg(kind=sp.kind, side=sp.side, symbol=c.symbol, exchange=c.exchange, expiry=c.expiry.isoformat(),
                       strike=c.strike, lots=sp.ratio * units, lot_size=c.lot_size,
                       price=q.ask if sp.side == "BUY" else q.bid, mid=round(q.mid, 4), quote=q.as_dict()))
    return out


def exit_costs_at_entry_prices(legs: list[Leg], sched: FnoSchedule) -> float:
    return round(sum(order_costs(g, "SELL" if g.side == "BUY" else "BUY", g.price, sched) for g in legs), 2)


def margin_estimate(legs: list[Leg], spot: float, defined: bool, structural: float) -> float:
    """
    Not the broker's figure. Defined: the worst loss or the premium paid.
    Undefined: 12% of the notional of the open-ended exposure (the steeper of
    the two losing slopes: one lot for a short straddle, as SPAN offsets its
    sides), plus any premium paid.
    """
    debit = max(0.0, -net_premium(legs))
    if defined:
        return round(max(structural, debit), 2)
    up, down = slopes(legs)
    return round(MARGIN_PCT * spot * max(-up, -down, 0) + debit, 2)


@dataclass
class Sized:
    legs: list[Leg]
    units: int
    defined: bool
    structural: float
    risk: float
    costs_in: float
    costs_out_est: float
    credit: float
    stop: float | None
    target: float | None
    margin: float


def size(s: Strategy, pairs: list, quotes: dict[str, Quote], spot: float, sched: FnoSchedule) -> Sized | str:
    """The structure at the most units its budget allows, or why none."""
    lots = {c.lot_size for _, c in pairs}
    if len(lots) != 1:
        return f"lot sizes differ across legs {sorted(lots)}"
    lot = lots.pop()

    def at(units: int) -> Sized | str:
        legs = build(pairs, quotes, units)
        structural, defined = structural_risk(legs, spot)
        cin, cout = entry_costs(legs, sched), exit_costs_at_entry_prices(legs, sched)
        credit = net_premium(legs)
        if defined:
            risk = structural + cin + cout
            stop = s.stop.rupees(risk, credit, lot, units) if s.stop else None
        else:
            stop = s.stop.rupees(0.0, credit, lot, units) if s.stop else None
            if stop is None:
                return "an undefined-risk structure without a usable stop (a credit stop needs a credit)"
            risk = stop * STOP_SLIP + cin + cout
        if s.stop and stop is None:
            return "the stop is a multiple of a credit, and this structure is a debit"
        target = s.target.rupees(risk, credit, lot, units) if s.target else None
        if s.target and target is None:
            return "the target is a fraction of a credit, and this structure is a debit"
        if not risk > 0:
            return "no risk to size against"
        return Sized(legs, units, defined, round(structural, 2), round(risk, 2), cin, cout, credit,
                     None if stop is None else round(stop, 2), None if target is None else round(target, 2),
                     margin_estimate(legs, spot, defined, structural))

    one = at(1)
    if isinstance(one, str):
        return one
    units = min(s.max_units, int(s.risk_budget // one.risk))
    for sp, c in pairs:
        if c.freeze_qty:
            units = min(units, c.freeze_qty // (sp.ratio * c.lot_size))
    if units < 1:
        return f"one unit risks Rs{one.risk:,.0f}, over the Rs{s.risk_budget:,.0f} budget"
    return at(units)


# -- the engine -------------------------------------------------------------------------------

class Refused(RuntimeError):
    pass


@dataclass
class PassReport:
    at: str
    spot: float | None = None
    entries: list[str] = field(default_factory=list)
    exits: list[str] = field(default_factory=list)
    skips: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    open: list[dict] = field(default_factory=list)
    day_pnl: float = 0.0

    def text(self) -> str:
        lines = [f"[explore {self.at}] index {self.spot if self.spot is not None else 'unavailable'}; "
                 f"day P&L Rs{self.day_pnl:,.0f}; {len(self.open)} open"]
        lines += [f"  + {x}" for x in self.entries] + [f"  - {x}" for x in self.exits]
        lines += [f"  ! {x}" for x in self.alerts] + [f"  . {x}" for x in self.skips]
        return "\n".join(lines)


class Explorer:
    def __init__(self, feed: FnoFeed, state_dir: Path, config: Config, *, calendar: TradingCalendar | None = None,
                 sched: FnoSchedule | None = None, now_fn: Callable[[], datetime] | None = None,
                 notify: Callable[[str], None] | None = None):
        self.state_dir = Path(state_dir)
        if feed.is_mock != self.state_dir.name.endswith(MOCK_SUFFIX):
            raise Refused(f"synthetic data stays out of the paper explorer: a mock feed runs only in a "
                          f"'*{MOCK_SUFFIX}' directory, and a real feed never runs in one")
        self.feed, self.cfg = feed, config
        self.cal = calendar or load_calendar()
        self.sched = sched or FNO_SCHEDULES.get(config.fee_card or feed.key, FNO_ZERODHA)
        self.now_fn = now_fn or (lambda: datetime.now(IST))
        self.notify = notify or (lambda msg: None)
        self.mode = "fno-explore-mock" if feed.is_mock else "fno-explore"
        self.journal = Journal(self.state_dir / "journal.jsonl", self.mode)
        self.ticks = Ticks(self.state_dir)
        self.kill = KillSwitch([self.state_dir / "KILL", self.state_dir.parent / "KILL"])

    def _log(self, kind: str, at: date, /, **payload) -> dict:
        return self.journal.append(kind, asof=at.isoformat(), feed=self.feed.key, **payload)

    def state(self) -> XState:
        return XState.from_records(self.journal.records())

    def init(self) -> None:
        now = self.now_fn()
        with exclusive(self.state_dir):
            if self.journal.records():
                raise Refused(f"{self.journal.path} already holds a book")
            self._log("XP_INIT", now.date(), capital=self.cfg.capital, feed_name=self.feed.name,
                      fee_card=self.sched.name, note="EXPLORATION, NOT EVIDENCE")
            self._log("XP_CONFIG", now.date(), sha=self.cfg.sha, config=self.cfg.raw)

    # -- one pass --------------------------------------------------------------------------

    def run_pass(self, now: datetime | None = None) -> PassReport:
        now = now or self.now_fn()
        today = now.date()
        rep = PassReport(at=now.isoformat(timespec="seconds"))
        with exclusive(self.state_dir):
            st = self.state()
            if st.init is None:
                raise Refused("no explorer book here: run `fno explore init` first")
            if st.config_sha != self.cfg.sha:
                self._log("XP_CONFIG", today, sha=self.cfg.sha, config=self.cfg.raw)
                rep.alerts.append("configuration changed: a new generation starts; the screen restarts")
                st = self.state()
            for e in [e for e in st.open.values() if e["day"] < today.isoformat()]:
                self._fallback_close(e, now, rep, "not squared off on its day (the runner was down)")
            if not self.cal.is_trading_day(today) or now.time() < time(9, 0):
                return rep
            st = self.state()
            master = self._master(st, today, rep)
            spot = None
            try:
                index = self.feed.index_quote(self.cfg.underlying)
                age = (now - index.exchange_ts).total_seconds() if index.exchange_ts else None
                if age is None or age > self.cfg.max_quote_age_s or age < -FUTURE_TOLERANCE_S:
                    rep.alerts.append(f"index reading unusable (exchange timestamp age {age})")
                else:
                    spot = index.value
            except DataUnavailable as exc:
                rep.alerts.append(f"index unavailable: {str(exc)[:160]}")
            rep.spot = spot
            ticks = self.ticks.read(today)

            cands = self._candidates(st, now, spot, ticks, master, rep) if master and spot else []
            want = {g["symbol"]: g for e in st.open.values() for g in e["legs"]}
            need: dict[str, OptionContract] = {}
            for s, d, ev, pairs, needs in cands:
                need.update({c.symbol: c for c in needs})
                need.update({c.symbol: c for _, c in pairs or []})
            quotes = self._quotes(list(need.values()), [_contract(g) for g in want.values()], rep)
            marks = self._manage(st, now, quotes, rep)
            if spot is not None:
                self.ticks.append(today, {"t": now.isoformat(), "spot": spot, "marks": marks})
            st = self.state()
            self._day_limit(st, now, quotes, marks, rep)
            st = self.state()
            for s, d, ev, pairs, needs in cands:
                self._enter(st, s, d, ev, pairs, master, spot, now, quotes, rep)
                st = self.state()
            rep.day_pnl = round(st.realised(today.isoformat()) + sum(marks.get(i, 0.0) for i in st.open), 2)
            rep.open = [{"id": e["id"], "strategy": e["strategy"], "net": marks.get(e["id"])} for e in st.open.values()]
            self._snapshot(st, marks)
        return rep

    def _master(self, st: XState, today: date, rep: PassReport) -> ContractMaster | None:
        path = self.state_dir / "master" / f"{today.isoformat()}.json"
        if path.exists() and today.isoformat() in st.masters:
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != st.masters.get(today.isoformat()):
                raise Refused(f"{path} does not match the hash journaled for it")
            return _master_from_json(raw.decode())
        try:
            m = self.feed.fno_master(self.cfg.underlying)
        except DataUnavailable as exc:
            rep.alerts.append(f"no contract master today: {str(exc)[:160]}")
            return None
        raw = _master_to_json(m).encode()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, path)
        futs = sorted({c.expiry.isoformat() for c in m.contracts if c.right == "FUT"})
        self._log("XP_MASTER", today, date=today.isoformat(), sha256=hashlib.sha256(raw).hexdigest(),
                  source=m.source, contracts=len(m), futures=futs,
                  lot_sizes=sorted({c.lot_size for c in m.contracts}))
        return m

    def _quotes(self, fresh: list[OptionContract], held: list[OptionContract], rep: PassReport) -> dict[str, Quote]:
        cs = {c.symbol: c for c in held + fresh}
        if not cs:
            return {}
        try:
            return self.feed.quotes(list(cs.values()))
        except DataUnavailable as exc:
            rep.alerts.append(f"quotes unavailable: {str(exc)[:160]}")
            return {}

    def _skip(self, st: XState, s: Strategy, today: date, code: str, detail: str, rep: PassReport) -> None:
        rep.skips.append(f"{s.name}: {detail}")
        key = (today.isoformat(), s.name, code)
        if key not in st.skips:                          # the first of each kind a day: a trace, not a flood
            self._log("XP_SKIP", today, day=key[0], strategy=s.name, code=code, detail=detail[:300])
            st.skips.add(key)

    def _gate(self, st: XState, s: Strategy, now: datetime, rep: PassReport) -> bool:
        """May this strategy open a position now? Silent outside its window; journals any other no."""
        today = now.date()
        day = today.isoformat()
        if not (s.start <= now.time() <= s.end) or st.entries[(day, s.name)] >= s.max_entries:
            return False
        if any(e["strategy"] == s.name for e in st.open.values()):
            return False
        killed = self.kill.engaged()
        if killed:
            self._skip(st, s, today, "kill", f"kill switch engaged ({killed})", rep)
        elif day in st.halted:
            self._skip(st, s, today, "halted", "the day's loss limit was hit", rep)
        elif len(st.open) >= self.cfg.max_open:
            self._skip(st, s, today, "max_open", f"{len(st.open)} positions open, the limit", rep)
        else:
            return True
        return False

    def _candidates(self, st, now, spot, ticks, master, rep) -> list:
        """Strategies that may enter on this pass, with their contracts as far as rules can name them unquoted."""
        today, out = now.date(), []
        for s in self.cfg.strategies:
            if not self._gate(st, s, now, rep):
                continue
            d, why, ev = fire(s.trigger, ticks, spot, now)
            if d is None:
                self._skip(st, s, today, "trigger", why, rep)
                continue
            specs = s.legs_for(d)
            if specs is None:
                self._skip(st, s, today, "direction", f"no legs for a {d} signal", rep)
                continue
            pairs, needs, why = resolve(master, specs, spot, today, {}, self.cfg.window_steps)
            out.append((s, d, ev, pairs, needs))
        return out

    def _enter(self, st, s, d, ev, pairs, master, spot, now, quotes, rep) -> None:
        today = now.date()
        if not self._gate(st, s, now, rep):             # an earlier entry or a halt on this pass
            return
        specs = s.legs_for(d)
        if pairs is None:
            pairs, _, why = resolve(master, specs, spot, today, quotes, self.cfg.window_steps)
            if pairs is None:
                self._skip(st, s, today, "contracts", why, rep)
                return
            missing = [c for _, c in pairs if c.symbol not in quotes]
            if missing:
                quotes.update(self._quotes(missing, [], rep))
        if s.skip_expiry_day and any(c.right != "FUT" and c.expiry == today for _, c in pairs):
            self._skip(st, s, today, "expiry_day", "its options expire today", rep)
            return
        for sp, c in pairs:                              # priced only from two-sided, tight, fresh quotes
            why = quote_problem(quotes.get(c.symbol), now, sp.side, 0, sp.kind == "FUT", self.cfg)
            if why:
                self._skip(st, s, today, "quote", f"{c.symbol}: {why}", rep)
                return
        sized = size(s, pairs, quotes, spot, self.sched)
        if isinstance(sized, str):
            self._skip(st, s, today, "size", sized, rep)
            return
        for (sp, c), g in zip(pairs, sized.legs):        # and deep enough for the sized order
            why = quote_problem(quotes.get(c.symbol), now, sp.side, g.qty, sp.kind == "FUT", self.cfg)
            if why:
                self._skip(st, s, today, "quote", f"{c.symbol}: {why}", rep)
                return
        used = sum(e["margin_est"] for e in st.open.values())
        room = self.cfg.max_margin_frac * (self.cfg.capital + st.realised_total) - used
        if sized.margin > room:
            self._skip(st, s, today, "margin", f"needs ~Rs{sized.margin:,.0f} margin, Rs{max(room, 0):,.0f} left", rep)
            return
        n = st.entries[(today.isoformat(), s.name)] + 1
        entry = {
            "id": f"{s.name}-{today.isoformat()}-{n}", "strategy": s.name, "day": today.isoformat(),
            "t": now.isoformat(), "config_sha": self.cfg.sha, "direction": d, "trigger": ev, "spot": spot,
            "legs": legs_as_dicts(sized.legs), "units": sized.units, "defined": sized.defined,
            "structural_loss": sized.structural, "risk": sized.risk, "stop_rupees": sized.stop,
            "target_rupees": sized.target, "trail": list(s.trail) if s.trail else None,
            "exit_time": s.exit_time.isoformat(timespec="minutes"), "entry_costs": sized.costs_in,
            "exit_costs_est": sized.costs_out_est, "credit": sized.credit, "margin_est": sized.margin,
            "breakevens": breakevens(sized.legs, spot), "fee_card": self.sched.name,
        }
        self._log("XP_ENTRY", today, **entry)
        legs = " ".join(f"{'+' if g.side == 'BUY' else '-'}{g.lots}x{g.symbol}@{g.price:.2f}" for g in sized.legs)
        msg = (f"{s.name} ({d}) opened: {legs}; risk Rs{sized.risk:,.0f}"
               f"{'' if sized.defined else ' (stop-based)'}, credit Rs{sized.credit:,.0f}")
        rep.entries.append(msg)
        self.notify(f"[F&O EXPLORE, paper] {msg}")

    def _manage(self, st: XState, now: datetime, quotes: dict[str, Quote], rep: PassReport) -> dict[str, float]:
        """Mark each open position and close the ones whose exit has come. Returns the marks still open."""
        marks: dict[str, float] = {}
        killed = self.kill.engaged()
        halted = now.date().isoformat() in st.halted          # one that had no quote when the limit hit
        ticks = self.ticks.read(now.date())
        for e in list(st.open.values()):
            legs = legs_from_dicts(e["legs"])
            closes = close_prices(legs, quotes)
            if closes is None:
                if now.time() >= LAST_CALL:
                    self._fallback_close(e, now, rep, "no quote to close at by 15:29")
                elif now.time() >= _hhmm(e["exit_time"]):
                    rep.alerts.append(f"{e['id']}: due to close, but a leg has no quote; retrying")
                continue
            m = mark(legs, closes, self.sched, e["entry_costs"])
            net, risk = m["net"], e["risk"]
            peak = max([net] + [t["marks"][e["id"]] for t in ticks if e["id"] in t.get("marks", {})])
            reason = None
            if killed:
                reason = "kill switch"
            elif halted:
                reason = "daily loss limit"
            elif e["stop_rupees"] is not None and net <= -e["stop_rupees"]:
                reason = "stop"
            elif e["trail"] and peak >= e["trail"][0] * risk and net <= e["trail"][1] * risk:
                reason = "trail"
            elif e["target_rupees"] is not None and net >= e["target_rupees"]:
                reason = "target"
            elif now.time() >= _hhmm(e["exit_time"]):
                reason = "time"
            if reason:
                self._close(e, now, rep, reason, closes, m, [])
            else:
                marks[e["id"]] = net
        return marks

    def _day_limit(self, st: XState, now: datetime, quotes: dict[str, Quote], marks: dict, rep: PassReport) -> None:
        day = now.date().isoformat()
        pnl = st.realised(day) + sum(marks.get(i, 0.0) for i in st.open)
        if day in st.halted or pnl > -self.cfg.daily_loss_limit:
            return
        self._log("XP_HALT", now.date(), day=day, pnl=round(pnl, 2), limit=self.cfg.daily_loss_limit)
        rep.alerts.append(f"day loss Rs{-pnl:,.0f} reached the Rs{self.cfg.daily_loss_limit:,.0f} limit: "
                          "closing everything, no more entries today")
        self.notify(f"[F&O EXPLORE, paper] {rep.alerts[-1]}")
        for e in list(st.open.values()):
            legs = legs_from_dicts(e["legs"])
            closes = close_prices(legs, quotes)
            if closes is None:
                rep.alerts.append(f"{e['id']}: no quote to close at; it stays open until one comes")
                continue
            self._close(e, now, rep, "daily loss limit", closes, mark(legs, closes, self.sched, e["entry_costs"]), [])
            marks.pop(e["id"], None)

    def _close(self, e: dict, now: datetime, rep: PassReport, reason: str, closes: dict | None, m: dict,
               flags: list[str]) -> None:
        ticks = self.ticks.read(e["day"])
        seen = [t["marks"][e["id"]] for t in ticks if e["id"] in t.get("marks", {})] + [m["net"]]
        x = {"id": e["id"], "strategy": e["strategy"], "day": e["day"], "t": now.isoformat(), "reason": reason,
             "closes": closes, "gross": m["gross"], "exit_costs": m["exit_costs"], "net": m["net"],
             "R": round(m["net"] / e["risk"], 4), "best": max(seen), "worst": min(seen), "flags": flags}
        self._log("XP_EXIT", date.fromisoformat(e["day"]), **x)
        msg = f"{e['id']} closed ({reason}): net Rs{m['net']:,.0f} = {x['R']:+.2f}R"
        rep.exits.append(msg)
        self.notify(f"[F&O EXPLORE, paper] {msg}")

    def _fallback_close(self, e: dict, now: datetime, rep: PassReport, why: str) -> None:
        """No price to close at: the last mark if it is recent, else a full loss of the trade's risk."""
        ticks = [t for t in self.ticks.read(e["day"]) if e["id"] in t.get("marks", {})]
        last = ticks[-1] if ticks else None
        fresh = last and (now - datetime.fromisoformat(last["t"])).total_seconds() <= FALLBACK_MARK_MIN * 60
        if fresh:
            net, how = float(last["marks"][e["id"]]), f"last mark at {last['t'][11:19]}"
        else:
            net, how = -e["risk"], "no recent mark: booked at -1 R"
        rep.alerts.append(f"{e['id']}: {why}; {how}")
        self._close(e, now, rep, "unobserved", None, {"gross": None, "exit_costs": None, "net": round(net, 2)},
                    [why, how])

    def _snapshot(self, st: XState, marks: dict) -> None:
        snap = {"note": "rebuilt from journal.jsonl after each pass; the journal is the record. EXPLORATION, NOT EVIDENCE",
                "capital": self.cfg.capital, "realised": st.realised_total,
                "open": [{**e, "mark": marks.get(e["id"])} for e in st.open.values()],
                "closed": len(st.closed)}
        path = self.state_dir / "book.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, indent=1, default=str))
        os.replace(tmp, path)


def _contract(g: dict) -> OptionContract:
    return OptionContract(g["symbol"], "", date.fromisoformat(g["expiry"]), g["strike"],
                          "FUT" if g["kind"] == "FUT" else g["kind"], g["lot_size"], 0.05, exchange=g["exchange"])


def _master_to_json(m: ContractMaster) -> str:
    return json.dumps({"source": m.source, "fetched_at": m.fetched_at.isoformat(),
                       "contracts": [c.as_dict() for c in m.contracts]}, sort_keys=True)


def _master_from_json(raw: str) -> ContractMaster:
    d = json.loads(raw)
    cs = [OptionContract(**{**c, "expiry": date.fromisoformat(c["expiry"])}) for c in d["contracts"]]
    return ContractMaster(cs, d["source"], datetime.fromisoformat(d["fetched_at"]))


# -- the scoreboard -------------------------------------------------------------------------

def scoreboard(records: list[dict]) -> dict:
    """Per strategy, from the journal alone: what happened, net of costs, and the screen."""
    st = XState.from_records(records)
    gen = st.generations[-1] if st.generations else None
    names = [s.get("name") or s.get("template") for s in (gen["config"]["strategies"] if gen else [])
             if s.get("enabled", True)]
    m = max(1, len(names))
    alpha = float(gen["config"].get("screen_alpha", 0.05)) if gen else 0.05
    threshold = m / alpha
    out = {"note": "EXPLORATION, NOT EVIDENCE: paper fills at the touch, net of costs; the screen picks strategies "
                   "for a registered forward test, it does not show an edge",
           "strategies_in_generation": m, "screen_threshold": threshold, "open": len(st.open),
           "realised": st.realised_total, "void": st.void, "by_strategy": {}}
    seen = sorted({c["strategy"] for c in st.closed} | set(names))
    for name in seen:
        cs = [c for c in st.closed if c["strategy"] == name]
        rs = [c["exit"]["R"] for c in cs]
        nets = [c["exit"]["net"] for c in cs]
        cum, peak, dd = 0.0, 0.0, 0.0
        for x in nets:
            cum += x
            peak = max(peak, cum)
            dd = min(dd, cum - peak)
        cur = [c["exit"]["R"] for c in cs if gen and c["config_sha"] == gen["sha"]]
        bound_broken = any(r < -1 / LAMBDA_CAP for r in cur)
        k = float(betting_path(cur)[-1]) if cur and not bound_broken else (0.0 if bound_broken else 1.0)
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x <= 0]
        costs = [c["entry_costs"] + (c["exit"]["exit_costs"] or 0.0) for c in cs]
        out["by_strategy"][name] = {
            "trades": len(cs), "wins": len(wins), "win_rate": round(len(wins) / len(cs), 3) if cs else None,
            "net": round(sum(nets), 2), "mean_R": round(sum(rs) / len(rs), 4) if rs else None,
            "worst_R": min(rs) if rs else None, "best_R": max(rs) if rs else None,
            "profit_factor": round(sum(wins) / -sum(losses), 3) if losses and sum(losses) < 0 else None,
            "max_drawdown": round(dd, 2), "costs": round(sum(costs), 2),
            "exits": dict(Counter(c["exit"]["reason"] for c in cs)),
            "unobserved_exits": sum(1 for c in cs if c["exit"]["reason"] == "unobserved"),
            "screen": {"trades": len(cur), "e_value": round(k, 4), "threshold": threshold,
                       "passed": (k >= threshold) and not bound_broken,
                       "void": "a trade below -2 R breaks the statistic's bound" if bound_broken else None},
            "enabled": name in names,
        }
    return out


def format_scoreboard(sb: dict) -> str:
    lines = [sb["note"], f"realised Rs{sb['realised']:,.0f}; open {sb['open']}; screen: e-value >= "
                         f"{sb['screen_threshold']:.0f} ({sb['strategies_in_generation']} strategies)", "",
             f"{'strategy':<20}{'trades':>7}{'win%':>7}{'mean R':>8}{'net Rs':>11}{'maxDD':>10}{'costs':>9}"
             f"{'e-value':>9}  exits"]
    for name, x in sb["by_strategy"].items():
        wr = f"{100 * x['win_rate']:.0f}" if x["win_rate"] is not None else "-"
        mr = f"{x['mean_R']:+.3f}" if x["mean_R"] is not None else "-"
        flag = " SCREEN PASSED" if x["screen"]["passed"] else (" (screen void)" if x["screen"]["void"] else "")
        lines.append(f"{name:<20}{x['trades']:>7}{wr:>7}{mr:>8}{x['net']:>11,.0f}{x['max_drawdown']:>10,.0f}"
                     f"{x['costs']:>9,.0f}{x['screen']['e_value']:>9.2f}  "
                     f"{', '.join(f'{k} {v}' for k, v in x['exits'].items())}{flag}")
    if sb["void"]:
        lines.append(f"VOID records: {sb['void'][:5]}")
    return "\n".join(lines)


def describe(legs: list[Leg], spot: float, sched: FnoSchedule) -> dict:
    """The payoff card for a structure: what it costs, what it can lose, where it breaks even."""
    structural, defined = structural_risk(legs, spot)
    cin, cout = entry_costs(legs, sched), exit_costs_at_entry_prices(legs, sched)
    table = {f"{pct:+.1f}%": round(payoff(legs, spot * (1 + pct / 100), spot) - cin - cout, 2)
             for pct in (-5, -3, -2, -1, -0.5, 0, 0.5, 1, 2, 3, 5)}
    return {"legs": [f"{'+' if g.side == 'BUY' else '-'}{g.lots}x {g.symbol} @ {g.price:.2f}" for g in legs],
            "credit": net_premium(legs), "defined": defined,
            "worst_loss_band": None if math.isinf(structural) else round(structural, 2),
            "costs_round_trip": round(cin + cout, 2), "breakevens": breakevens(legs, spot),
            "margin_estimate": margin_estimate(legs, spot, defined, 0.0 if math.isinf(structural) else structural),
            "net_at_expiry_value": table}
