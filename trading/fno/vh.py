"""
V-H: strategy V's historical track on NSE's daily files (research/PREREGISTRATION_4.md).

This module holds the binding (the SHA-256 of the registration text) and implementation A.
Implementation A reuses strategy V's own cost, size and settlement code (trading/fno/strategy_v.py),
so V-H charges exactly what V charges. Implementation B (research/vh_check.py) is written apart
from it, and the two must agree (section 6).

Input: the NIFTY index-option rows of each session's NSE F&O file, kept verbatim by
tools/vh_download.py under state/vh/raw/YEAR/DATE.csv.gz, and NSE's all-index close files
kept by deploy/backfill.sh under state/nse_archive/index_close/. Every function that reads
them calls check_registration() first.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

import numpy as np

from market.charges import FNO_ZERODHA
from trading.fno.strategy_v import REGISTERED, leg_costs, max_loss, settle

REPO = Path(__file__).resolve().parents[2]
REGISTRATION = REPO / "research" / "PREREGISTRATION_4.md"
REGISTRATION_SHA256 = "eacf2ec247bf3987305dbc31e4d38140a3e40257e3200f856b8930693927b55a"
RAW = REPO / "state" / "vh" / "raw"
INDEX_CLOSE = REPO / "state" / "nse_archive" / "index_close"

WINDOW_FROM, WINDOW_TO = date(2019, 1, 1), date(2026, 10, 18)
LOT = 65                                  # section 4: today's NIFTY lot, every cycle
SCHED = FNO_ZERODHA                       # section 4: the 1 April 2026 Zerodha card
P = REGISTERED                            # V's wing, credit floor and cap, budget, lot cap
CHAIN_DAYS = 10
PERIODS = (("2019-02 to 2020", date(2019, 1, 1), date(2020, 12, 31)),
           ("2021 to 2022", date(2021, 1, 1), date(2022, 12, 31)),
           ("2023 to 19 Nov 2024", date(2023, 1, 1), date(2024, 11, 19)),
           ("20 Nov 2024 on", date(2024, 11, 20), WINDOW_TO))


class RegistrationMismatch(RuntimeError):
    """The registration text is not the one bound on 2026-10-30."""


def check_registration(path: Path = REGISTRATION) -> str:
    got = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if got != REGISTRATION_SHA256:
        raise RegistrationMismatch(f"{path} has SHA-256 {got}, not the bound {REGISTRATION_SHA256}: V-H refuses to run")
    return got


# -- fills (section 4) ------------------------------------------------------------------

def penalty_registered(x: float) -> float:
    return max(0.25, 0.01 * x)


def penalty_none(x: float) -> float:
    return 0.0


def penalty_harsh(x: float) -> float:
    return max(0.5, 0.05 * x)


def fill(close: float, side: str, h) -> float:
    """Sells at close - h, rounded down to the 0.05 tick; buys at close + h, rounded up."""
    tick = Decimal("0.05")
    raw = Decimal(repr(close)) + (Decimal(repr(h(close))) if side == "BUY" else -Decimal(repr(h(close))))
    steps = (raw / tick).to_integral_value(rounding=ROUND_CEILING if side == "BUY" else ROUND_FLOOR)
    return float(steps * tick)


# -- reading the files (section 2) --------------------------------------------------------

@dataclass(frozen=True)
class Opt:
    expiry: date
    strike: float
    right: str
    close: float
    traded: float
    underlying: float | None


def _num(x) -> float | None:
    try:
        v = float(str(x).replace(",", "").strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def parse_rows(text: str) -> list[Opt]:
    """Both NSE layouts, every column by name; a missing column stops the run."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return []
    cols = {k.strip() for k in rows[0] if k}
    udiff = "TckrSymb" in cols
    need = ({"XpryDt", "StrkPric", "OptnTp", "ClsPric", "TtlTradgVol", "UndrlygPric"} if udiff
            else {"EXPIRY_DT", "STRIKE_PR", "OPTION_TYP", "CLOSE", "CONTRACTS"})
    if not need <= cols:
        raise ValueError(f"missing columns {sorted(need - cols)}")
    out = []
    for r in rows:
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        if udiff:
            exp = date.fromisoformat(r["XpryDt"][:10])
            o = Opt(exp, _num(r["StrkPric"]), r["OptnTp"].upper(), _num(r["ClsPric"]) or 0.0,
                    _num(r["TtlTradgVol"]) or 0.0, _num(r["UndrlygPric"]))
        else:
            exp = datetime.strptime(r["EXPIRY_DT"], "%d-%b-%Y").date()
            o = Opt(exp, _num(r["STRIKE_PR"]), r["OPTION_TYP"].upper(), _num(r["CLOSE"]) or 0.0,
                    _num(r["CONTRACTS"]) or 0.0, None)
        if o.strike is not None and o.right in ("CE", "PE"):
            out.append(o)
    return out


class Archive:
    """The downloaded sessions, read one day at a time: holding seven years of rows at once would not
    fit a small server. A session is a date with a file (section 2)."""

    def __init__(self, raw: Path = RAW):
        check_registration()
        self.files = {}
        for p in sorted(Path(raw).glob("*/*.csv.gz")):
            d = date.fromisoformat(p.name[:10])
            if WINDOW_FROM <= d <= WINDOW_TO:
                self.files[d] = p
        self.days = sorted(self.files)
        self._cache: dict[date, list[Opt]] = {}

    def __len__(self) -> int:
        return len(self.days)

    def __contains__(self, d: date) -> bool:
        return d in self.files

    def rows(self, d: date) -> list[Opt]:
        if d not in self._cache:
            if len(self._cache) > 12:
                self._cache.pop(next(iter(self._cache)))
            self._cache[d] = parse_rows(gzip.decompress(self.files[d].read_bytes()).decode("utf-8-sig"))
        return self._cache[d]

    def expiries(self) -> set[date]:
        out: set[date] = set()
        for d in self.days:
            out |= {o.expiry for o in self.rows(d)}
        self._cache.clear()
        return out


def load_sessions(raw: Path = RAW) -> Archive:
    return Archive(raw)


def nifty_close_from_index_file(text: str) -> float | None:
    for r in csv.DictReader(io.StringIO(text)):
        r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        if r.get("index name", "").lower() == "nifty 50":
            return _num(r.get("closing index value", ""))
    return None


def nifty_close(d: date, rows: list[Opt] | None, index_dir: Path = INDEX_CLOSE) -> float | None:
    """NSE's all-index file; else the day's option rows, if they all carry one UndrlygPric (section 2)."""
    p = index_dir / str(d.year) / f"ind_close_all_{d:%d%m%Y}.csv"
    if p.exists():
        v = nifty_close_from_index_file(p.read_bytes().decode("utf-8-sig", errors="replace"))
        if v and v > 0:
            return v
    vals = {o.underlying for o in rows or [] if o.underlying}
    return vals.pop() if len(vals) == 1 else None


# -- cycles (section 3) -------------------------------------------------------------------

def weekly_chain(expiries: set[date]) -> list[date]:
    s = sorted(expiries)
    return [e for i, e in enumerate(s)
            if (i > 0 and (e - s[i - 1]).days <= CHAIN_DAYS) or (i + 1 < len(s) and (s[i + 1] - e).days <= CHAIN_DAYS)]


def eligible(e: date, e_prev: date, sessions: list[date]) -> list[date]:
    return [d for d in sessions if e_prev < d < e and sum(1 for x in sessions if d < x <= e) >= P.min_sessions_to_expiry]


@dataclass
class Cycle:
    expiry: date
    entry: date | None = None
    outcome: str = ""                    # "trade", "no trade", "left out"
    why: list[str] = field(default_factory=list)
    trade: dict | None = None
    settlement: dict | None = None
    R: float | None = None
    net: float | None = None


def try_entry(d: date, e: date, rows: list[Opt], s: float, h) -> tuple[dict | None, str]:
    """Sections 3 and 4 at session d's close. (trade, '') or (None, reason)."""
    opts = {(o.strike, o.right): o for o in rows if o.expiry == e}
    both = sorted({k for (k, r) in opts if r == "CE"} & {k for (k, r) in opts if r == "PE"})
    if not both:
        return None, "no strikes for E"
    k_atm = min(both, key=lambda k: (abs(k - s), k))
    ce, pe = opts[(k_atm, "CE")], opts[(k_atm, "PE")]
    if not (ce.close > 0 and pe.close > 0):
        return None, "at-the-money close missing"
    em = ce.close + pe.close
    calls = sorted(k for (k, r) in opts if r == "CE" and k >= s + em)
    puts = sorted(k for (k, r) in opts if r == "PE" and k <= s - em)
    if not calls or not puts:
        return None, "no short strike"
    k_sc, k_sp = calls[0], puts[-1]
    legs_spec = (("short_call", "CE", "SELL", k_sc), ("short_put", "PE", "SELL", k_sp),
                 ("long_call", "CE", "BUY", k_sc + P.wing), ("long_put", "PE", "BUY", k_sp - P.wing))
    legs = []
    for role, right, side, k in legs_spec:
        o = opts.get((k, right))
        if o is None:
            return None, f"{role} not listed"
        if not o.close > 0:
            return None, f"{role} has no close"
        if not o.traded > 0:
            return None, f"{role} did not trade"
        px = fill(o.close, side, h)
        if side == "SELL" and px <= 0:
            return None, f"{role} would sell at or below 0"
        legs.append({"role": role, "right": right, "side": side, "strike": k, "price": px, "close": o.close})
    credit = round(legs[0]["price"] + legs[1]["price"] - legs[2]["price"] - legs[3]["price"], 4)
    if credit < P.min_credit:
        return None, "credit below the floor"
    if credit > P.max_credit:
        return None, "credit above the cap"
    n, m_n, costs_n = 0, None, None
    for lots in range(1, P.max_lots + 1):
        costs = round(sum(c["total"] for c in leg_costs(legs, LOT * lots, SCHED).values()), 2)
        m = max_loss(credit, legs, LOT, lots, costs, P, SCHED)
        if m > P.risk_budget:
            break
        n, m_n, costs_n = lots, m, costs
    if n == 0:
        return None, "one lot over the budget"
    return {"entry": d.isoformat(), "expiry": e.isoformat(), "spot": s, "atm": k_atm, "em": round(em, 4),
            "legs": legs, "credit": credit, "lots": n, "qty": n * LOT, "entry_costs_total": costs_n,
            "max_loss": m_n}, ""


def run(sessions: Archive, h=penalty_registered, index_dir: Path = INDEX_CLOSE,
        expiries: set[date] | None = None) -> list[Cycle]:
    days = sessions.days
    chain = weekly_chain(expiries if expiries is not None else sessions.expiries())
    out = []
    for i, e in enumerate(chain):
        if i == 0 or e > WINDOW_TO:
            continue
        e_prev = chain[i - 1]
        elig = eligible(e, e_prev, days)
        if not elig:
            continue                                      # before the files begin
        c = Cycle(e)
        if e not in sessions:
            c.outcome = "left out"
            c.why.append("no NSE file for E")
            out.append(c)
            continue
        for d in elig:
            s = nifty_close(d, sessions.rows(d), index_dir)
            if s is None:
                c.why.append(f"{d}: no NIFTY close")
                continue
            trade, why = try_entry(d, e, sessions.rows(d), s, h)
            if trade:
                c.entry, c.trade = d, trade
                break
            c.why.append(f"{d}: {why}")
        if c.trade is None:
            c.outcome = "no trade"
            out.append(c)
            continue
        p_val = nifty_close(e, sessions.rows(e), index_dir)
        if p_val is None:
            c.outcome = "left out"
            c.why.append("no NIFTY close on E")
            out.append(c)
            continue
        c.settlement = settle(c.trade, p_val, SCHED)
        c.outcome, c.net, c.R = "trade", c.settlement["net"], c.settlement["R"]
        out.append(c)
    return out


# -- the statistic (section 6) ------------------------------------------------------------

def betting_path(rs: list[float]) -> list[float]:
    """V's bet: lambda_i from items before i with one pseudo-observation (mean 0, second moment 0.25)."""
    k, out, s1, s2 = 1.0, [], 0.0, 0.0
    for i, r in enumerate(rs, start=1):
        m = s1 / i
        v = max((0.25 + s2) / i - m * m, 1e-6)
        lam = min(0.5, max(0.0, m / (v + m * m)))
        k *= 1.0 + lam * r
        out.append(k)
        s1 += r
        s2 += r * r
    return out


def verdict(cycles: list[Cycle]) -> dict:
    items = [c for c in cycles if c.outcome == "trade"]
    rs = [c.R for c in items]
    path = betting_path(rs)
    for i, (c, k) in enumerate(zip(items, path), start=1):
        if not np.isfinite(c.R) or c.R < -2:
            return {"outcome": "FAIL", "why": f"bound broken: item {i} ({c.expiry}) has R {c.R:.3f}", "item": i}
        if k >= P.threshold:
            return {"outcome": "PASS", "why": f"betting wealth {k:.1f} >= {P.threshold:g} at item {i} ({c.expiry})",
                    "item": i}
    return {"outcome": "FAIL", "why": f"no evidence: {len(rs)} items, wealth {path[-1] if path else 1.0:.2f} "
                                       f"(peak {max(path) if path else 1.0:.2f}) never reached {P.threshold:g}",
            "item": None}


def describe(cycles: list[Cycle]) -> dict:
    """Section 7: reported, never decided on."""
    items = [c for c in cycles if c.outcome == "trade"]
    rs = [c.R for c in items]
    nets = [c.net for c in items]
    path = betting_path(rs)
    cum, peak, dd, harm_at = 0.0, 0.0, 0.0, None
    for i, n in enumerate(nets, start=1):
        cum += n
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
        if harm_at is None and cum <= -P.harm_stop:
            harm_at = i
    futility = (len(rs) >= P.futility_n and float(np.mean(rs[:P.futility_n])) <= 0)

    def block(sel):
        r = [c.R for c in sel]
        n = [c.net for c in sel]
        return {"items": len(sel), "win_rate": round(float(np.mean([x > 0 for x in n])), 3) if n else None,
                "mean_R": round(float(np.mean(r)), 4) if r else None, "net_total": round(sum(n), 2),
                "net_mean": round(float(np.mean(n)), 2) if n else None}

    credit_pts = [c.trade["credit"] * c.trade["qty"] for c in items]
    costs = [c.trade["entry_costs_total"] + c.settlement["settlement_costs_total"] for c in items]
    return {
        "cycles": len(cycles), "items": len(items),
        "no_trade": sum(c.outcome == "no trade" for c in cycles),
        "left_out": sum(c.outcome == "left out" for c in cycles),
        "no_trade_reasons": _reasons(cycles),
        "all": block(items), "max_drawdown": round(dd, 2),
        "wealth_final": round(path[-1], 3) if path else 1.0, "wealth_peak": round(max(path), 3) if path else 1.0,
        "harm_stop_item": harm_at, "futility_at_60": futility if len(rs) >= P.futility_n else None,
        "worst_ten": [{"expiry": c.expiry.isoformat(), "R": round(c.R, 3), "net": c.net}
                      for c in sorted(items, key=lambda c: c.R)[:10]],
        "periods": {name: block([c for c in items if a <= c.expiry <= b]) for name, a, b in PERIODS},
        "costs_share_of_credit": round(sum(costs) / sum(credit_pts), 4) if credit_pts else None,
    }


def _reasons(cycles: list[Cycle]) -> dict:
    out: dict[str, int] = {}
    for c in cycles:
        if c.outcome == "no trade" and c.why:
            key = c.why[-1].split(": ", 1)[-1]
            out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
