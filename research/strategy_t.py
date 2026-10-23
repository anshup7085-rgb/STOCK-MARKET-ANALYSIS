"""
Strategy T — trend timing on NIFTYBEES and GOLDBEES, exactly as pre-registered
(research/PREREGISTRATION_2.md, sections 5-15).

Every line (T, EXP, T12, BH-N, MIX) runs through one accounting engine
(section 7): fractional units at the close, costs paid from cash, FIFO tax
lots in after-tax runs (section 9), the div track and the EQ transform
(section 10). The primary statistic (section 11) and the criteria (section 12)
are computed from the monthly records the engine keeps.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field, fields, replace

import numpy as np
import pandas as pd

from research.round2 import (DEV_LAST, ETFS, G, N, N_MONTHS, NotEvaluable, Panel, Schedule, fy_of,
                             plus_12_months, schedule)

Z_CRIT = 2.5758293
EQUITY = 500_000.0
DIV_MONTHLY = 0.00125            # 1.5% a year / 12
SLAB = 0.312
EXEMPTION = 125_000.0


# -- section 8: costs -----------------------------------------------------------

@dataclass(frozen=True)
class Card:
    name: str = "Groww"
    brokerage_pct: float = 0.1
    brokerage_min: float = 5.0
    brokerage_cap: float = 20.0
    exchange_pct: float = 0.00297
    sebi_pct: float = 0.0001
    gst_pct: float = 18.0
    stamp_buy_pct: float = 0.015
    stt_sell_n_pct: float = 0.001
    stt_sell_g_pct: float = 0.0
    slippage_pct: float = 0.05
    dp_per_sell: float = 23.60

    def doubled(self) -> "Card":
        """G4: every percentage and every rupee amount x 2 (GST's rate included)."""
        return replace(self, name=self.name + " x2",
                       **{f.name: 2 * getattr(self, f.name) for f in fields(self) if f.name != "name"})


GROWW = Card()
ZERODHA = replace(GROWW, name="Zerodha", brokerage_pct=0.0, brokerage_min=0.0, brokerage_cap=0.0,
                  dp_per_sell=15.34)


def order_costs(card: Card, k: str, side: str, v: float) -> tuple[float, float]:
    """(cost, STT within it) of one order of notional v, before any DP charge."""
    if v <= 0:
        return 0.0, 0.0
    b = min(card.brokerage_cap, max(card.brokerage_min, card.brokerage_pct / 100 * v))
    ex, se = card.exchange_pct / 100 * v, card.sebi_pct / 100 * v
    base = b + ex + se + card.gst_pct / 100 * (b + ex + se) + card.slippage_pct / 100 * v
    if side == "buy":
        return base + card.stamp_buy_pct / 100 * v, 0.0
    stt = (card.stt_sell_n_pct if k == N else card.stt_sell_g_pct) / 100 * v
    return base + stt, stt


def _solve(f, target: float, hi: float) -> float:
    """x in [0, hi] with f(x) = target, f increasing; bisection to well under a paisa."""
    lo = 0.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# -- section 9: tax ---------------------------------------------------------------

RATES = {("EQ", "ST"): 0.208, ("EQ", "LT"): 0.130, ("AU", "ST"): 0.312, ("AU", "LT"): 0.130}
ORDER_A = [("AU", "ST"), ("EQ", "ST"), ("AU", "LT"), ("EQ", "LT")]
ORDER_B = [("AU", "LT"), ("EQ", "LT")]
CLASS = {N: "EQ", G: "AU"}


def _absorb(loss: float, pos: dict, order: list) -> float:
    for b in order:
        if loss <= 0:
            break
        take = min(loss, pos[b])
        pos[b] -= take
        loss -= take
    return loss


class Book:
    """One line's account: units, cash, lots, realised gains, carried losses."""

    def __init__(self, card: Card, cash: float, taxed: bool, exemption: bool = False):
        self.card, self.cash, self.taxed, self.exemption = card, cash, taxed, exemption
        self.units = {k: 0.0 for k in ETFS}
        self.lots: dict[str, list[list]] = {k: [] for k in ETFS}          # [date, units, basis]
        self.realised: dict = defaultdict(lambda: defaultdict(float))     # fy -> bucket -> gain
        self.interest: dict = defaultdict(float)                          # fy -> cash interest
        self.carry: list[list] = []                                       # [fy arose, "ST"|"LT", amount]
        self.lapsed = 0.0
        self.tax_by_fy: dict = {}
        self.tax_by_bucket: dict = defaultdict(float)
        self.costs = self.turnover = 0.0
        self.orders = 0
        self.log: list[tuple] = []                                       # (date, etf, side, notional, cost)
        self._dp: set = set()

    def value(self, q: dict) -> float:
        return self.cash + sum(self.units[k] * q[k] for k in ETFS)

    def buy(self, k: str, notional: float, d, price: float) -> float:
        if notional <= 0:
            return 0.0
        cost, _ = order_costs(self.card, k, "buy", notional)
        u = notional / price
        self.units[k] += u
        self.cash -= notional + cost
        if self.taxed:
            self.lots[k].append([d, u, notional + cost])
        self.costs += cost
        self.turnover += notional
        self.orders += 1
        self.log.append((d, k, "buy", notional, cost))
        return cost

    def sell_costs(self, k: str, notional: float, d) -> tuple[float, float]:
        cost, stt = order_costs(self.card, k, "sell", notional)
        if (d, k) not in self._dp:
            cost += self.card.dp_per_sell
        return cost, stt

    def sell(self, k: str, notional: float, d, price: float) -> float:
        if notional <= 0:
            return 0.0
        cost, stt = self.sell_costs(k, notional, d)
        self._dp.add((d, k))
        u = min(notional / price, self.units[k])
        self.units[k] -= u
        self.cash += notional - cost
        if self.taxed:
            self._realise(k, u, d, price, cost - stt)
        if self.units[k] <= 1e-12:                   # Amendment 1: a full exit leaves no dust
            self.units[k] = 0.0
            self.lots[k].clear()
        self.costs += cost
        self.turnover += notional
        self.orders += 1
        self.log.append((d, k, "sell", notional, cost))
        return cost

    def credit(self, k: str, amount: float, d, price: float) -> None:
        """A reinvested credit: no costs, a new lot whose basis is the amount."""
        u = amount / price
        self.units[k] += u
        if self.taxed:
            self.lots[k].append([d, u, amount])

    def _realise(self, k: str, u: float, d, price: float, other_costs: float) -> None:
        total, fy = u, fy_of(d)
        lots = self.lots[k]
        while u > 1e-12 and lots:
            lot = lots[0]
            take = min(u, lot[1])
            basis = lot[2] * take / lot[1]
            gain = take * price - other_costs * take / total - basis
            term = "LT" if d > plus_12_months(lot[0]) else "ST"
            self.realised[fy][(CLASS[k], term)] += gain
            lot[1] -= take
            lot[2] -= basis
            u -= take
            if lot[1] <= 1e-12:
                lots.pop(0)

    def close_fy(self, fy: int) -> float:
        net = {b: self.realised[fy].get(b, 0.0) for b in RATES}
        pos = {b: max(v, 0.0) for b, v in net.items()}
        st = -sum(min(net[b], 0.0) for b in RATES if b[1] == "ST")
        lt = -sum(min(net[b], 0.0) for b in RATES if b[1] == "LT")
        st = _absorb(st, pos, ORDER_A)                                   # (a)
        lt = _absorb(lt, pos, ORDER_B)                                   # (b)
        for e in sorted(self.carry, key=lambda e: e[0]):                 # (c) oldest first
            e[2] = _absorb(e[2], pos, ORDER_A if e[1] == "ST" else ORDER_B)
        carry = [e for e in self.carry if e[2] > 1e-9]
        if st > 1e-9:
            carry.append([fy, "ST", st])
        if lt > 1e-9:
            carry.append([fy, "LT", lt])
        self.carry = []
        for e in carry:                                                  # (d) eight FYs, then lapse
            if fy - e[0] >= 8:
                self.lapsed += e[2]
            else:
                self.carry.append(e)
        if self.exemption:
            pos[("EQ", "LT")] = max(0.0, pos[("EQ", "LT")] - EXEMPTION)
        tax = 0.0
        for b, rate in RATES.items():                                    # (e) no refunds
            tax += pos[b] * rate
            self.tax_by_bucket["-".join(b)] += pos[b] * rate
        itax = max(self.interest.get(fy, 0.0), 0.0) * SLAB
        self.tax_by_bucket["interest"] += itax
        tax += itax
        self.tax_by_fy[f"FY{fy}"] = tax
        return tax


# -- sections 5-7: lines and the engine ------------------------------------------------

def q_row(Q: pd.DataFrame, d) -> dict:
    row = Q.loc[d]
    return {k: float(row[k]) for k in ETFS}


def faber_targets(Q: pd.DataFrame, sch: Schedule) -> np.ndarray:
    """w[t-1, k]: 1/2 if Q_k(M_t) > the mean of Q_k at M_t ... M_{t-9}, strictly; else 0."""
    w = np.zeros((N_MONTHS, 2))
    for t in range(1, N_MONTHS + 1):
        for j, k in enumerate(ETFS):
            q_now = Q.at[sch.M_back(t, 0), k]
            sma = np.mean([Q.at[sch.M_back(t, i), k] for i in range(10)])
            w[t - 1, j] = 0.5 if q_now > sma else 0.0
    return w


def tsmom_targets(Q: pd.DataFrame, sch: Schedule) -> np.ndarray:
    """T12 (guard G5): in if Q_k(M_t) > Q_k(M_{t-12}), strictly."""
    w = np.zeros((N_MONTHS, 2))
    for t in range(1, N_MONTHS + 1):
        for j, k in enumerate(ETFS):
            w[t - 1, j] = 0.5 if Q.at[sch.M_back(t, 0), k] > Q.at[sch.M_back(t, 12), k] else 0.0
    return w


@dataclass
class Run:
    V: np.ndarray                       # V_1 .. V_201: after any tax payment, before trades
    C: np.ndarray                       # rupee costs at X_1 .. X_200
    Ck: np.ndarray                      # per-ETF rupee costs at X_t, DP included
    r: np.ndarray                       # r_kt from Q at the X dates used
    book: Book
    daily: pd.Series | None = None
    before_liquidation: float = 0.0
    after_tax: float | None = None

    @property
    def R(self) -> np.ndarray:
        return self.V[1:] / self.V[:-1] - 1

    @property
    def c(self) -> np.ndarray:
        return self.C / self.V[:-1]

    @property
    def ck(self) -> np.ndarray:
        return self.Ck / self.V[:-1, None]


def mix_checks(panel: Panel, sch: Schedule) -> list[tuple]:
    """Section 6: (K_i, Y_i), chained on anniversaries, every Y_i before X_201."""
    sessions, both = panel.sessions, panel.genuine.all(axis=1).values
    out, Y = [], sch.X[0]
    while True:
        later = np.nonzero(sessions > plus_12_months(Y))[0]
        if not len(later):
            break
        i = later[0]
        y = next((sessions[i + j] for j in range(1, 6) if i + j < len(sessions) and both[i + j]), None)
        if y is None:
            raise NotEvaluable(f"MIX not computable: no common genuine session after {sessions[i].date()}")
        if y >= sch.X[-1]:
            break
        out.append((sessions[i], y))
        Y = y
    return out


def simulate(line: str, panel: Panel, sch: Schedule, *, targets: np.ndarray | None = None,
             card: Card = GROWW, taxed: bool = False, div: bool = False, cash_rate: float = 0.0,
             equity: float = EQUITY, exemption: bool = False, daily: bool = False) -> Run:
    """
    line: "monthly" (T, EXP, T12 — needs targets), "BH-N" or "MIX". Order of
    operations at a session: div credit (month-ends), April tax payment,
    trades (section 7).
    """
    Q = panel.Q
    X = sch.X
    xi = {d: t for t, d in enumerate(X, start=1)}
    book = Book(card, equity, taxed, exemption)
    V = np.zeros(len(X))
    C = np.zeros(len(X) - 1)
    Ck = np.zeros((len(X) - 1, 2))
    me = set(sch.month_end.values)
    checks = {}
    if line == "MIX":
        for K, Y in mix_checks(panel, sch):
            checks[K] = ("check", Y)
            checks.setdefault(Y, None)
    pending: set = set()
    rm = (1 + cash_rate) ** (1 / 12) - 1 if cash_rate else 0.0
    cash_after_prev = None

    sessions = panel.sessions
    lo, hi = sessions.get_loc(X[0]), sessions.get_loc(X[-1])
    if daily or div or line == "MIX":
        walk = sessions[lo:hi + 1]
    else:
        walk = pd.DatetimeIndex(X)
    values = []
    for d in walk:
        q = q_row(Q, d)
        if div and d in me and X[0] < d < X[-1] and book.units[N] > 0:
            amt = DIV_MONTHLY * book.units[N] * q[N]
            book.credit(N, amt * (1 - SLAB) if taxed else amt, d, q[N])
        t = xi.get(d)
        if t is not None:
            if rm and cash_after_prev is not None:
                inc = max(cash_after_prev, 0.0) * rm
                book.cash += inc
                if taxed:
                    book.interest[fy_of(d)] += inc
            if taxed and t <= N_MONTHS and sch.hold_month[t - 1][1] == 4:
                book.cash -= book.close_fy(sch.hold_month[t - 1][0] - 1)
                if line != "monthly" and book.cash < -0.005:   # Amendment 1: under Rs0.01 is none
                    _fund_shortfall(book, d, q)
            V[t - 1] = book.value(q)
            if t <= N_MONTHS:
                costs = _trade(line, book, t, d, q, V[t - 1], targets, card, equity)
                C[t - 1] = sum(costs.values())
                Ck[t - 1] = [costs.get(k, 0.0) for k in ETFS]
            cash_after_prev = book.cash
        if line == "MIX" and d in checks:
            if checks[d] and checks[d][0] == "check":
                val = book.value(q)
                if val > 0 and max(abs(book.units[k] * q[k] / val - 0.5) for k in ETFS) > 0.05:
                    pending.add(checks[d][1])
            if d in pending and d != X[-1]:
                _mix_rebalance(book, d, q)
                pending.discard(d)
        if daily:
            values.append(book.value(q))
    r = np.array([[Q.at[X[t + 1], k] / Q.at[X[t], k] - 1 for k in ETFS] for t in range(len(X) - 1)])
    run = Run(V, C, Ck, r, book)
    if daily:                                # equity at X_1 before trading, then after each session's trades
        run.daily = pd.Series([equity] + values, index=pd.DatetimeIndex([walk[0]]).append(walk))
    q_end = q_row(Q, X[-1])
    run.before_liquidation = book.value(q_end)
    if taxed:
        for k in ETFS:
            if book.units[k] > 0:
                book.sell(k, book.units[k] * q_end[k], X[-1], q_end[k])
        book.cash -= book.close_fy(fy_of(X[-1]))
        run.after_tax = book.cash
    return run


def _trade(line: str, book: Book, t: int, d, q: dict, V: float, targets, card: Card, equity: float) -> dict:
    costs: dict = {}
    if line == "monthly":
        w = targets[t - 1]
        orders = []
        for j, k in enumerate(ETFS):
            w_hat = book.units[k] * q[k] / V
            if abs(w[j] - w_hat) > 1e-9:
                orders.append((k, w[j] * V / q[k] - book.units[k]))
        for k, du in sorted(orders, key=lambda o: o[1]):                 # sells before buys
            if du < 0:
                costs[k] = costs.get(k, 0.0) + book.sell(k, -du * q[k], d, q[k])
            else:
                costs[k] = costs.get(k, 0.0) + book.buy(k, du * q[k], d, q[k])
    elif t == 1:
        budget = {"BH-N": {N: equity}, "MIX": {N: equity / 2, G: equity / 2}}[line]
        for k, cash in budget.items():
            n = _solve(lambda x: x + order_costs(card, k, "buy", x)[0], cash, cash)
            costs[k] = book.buy(k, n, d, q[k])
    return costs


def _mix_rebalance(book: Book, d, q: dict) -> None:
    V = book.value(q)
    over = max(ETFS, key=lambda k: book.units[k] * q[k])
    under = G if over == N else N
    S = book.units[over] * q[over] - 0.5 * V
    if S <= 0:
        return
    cost = book.sell(over, S, d, q[over])
    net = S - cost
    n = _solve(lambda x: x + order_costs(book.card, under, "buy", x)[0], net, net)
    book.buy(under, n, d, q[under])


def _fund_shortfall(book: Book, d, q: dict) -> None:
    """MIX and BH-N: sell each ETF in proportion to its value, net proceeds = its share."""
    short = -book.cash
    vals = {k: book.units[k] * q[k] for k in ETFS}
    total = sum(vals.values())
    for k in ETFS:
        if vals[k] <= 0:
            continue
        share = short * vals[k] / total
        gross = _solve(lambda x: x - book.sell_costs(k, x, d)[0], share, vals[k])
        book.sell(k, min(gross, vals[k]), d, q[k])


# -- section 10: EQ ----------------------------------------------------------------------

def eq_transform(panel: Panel, sch: Schedule, div: bool = False) -> Panel:
    """GOLDBEES's drift over the window set equal to the Nifty's; two anchors only."""
    Q = panel.Q
    X1, X201 = sch.X[0], sch.X[-1]
    s = panel.sessions
    i1, i201 = s.get_loc(X1), s.get_loc(X201)
    gN = math.log(Q.at[X201, N] / Q.at[X1, N]) + (N_MONTHS * math.log(1 + DIV_MONTHLY) if div else 0.0)
    gG = math.log(Q.at[X201, G] / Q.at[X1, G])
    c = (gN - gG) / (i201 - i1)
    j = np.arange(len(s)) - i1
    close = panel.close.copy()
    close[G] = close[G] * np.exp(c * j)
    return panel.with_close(close)


# -- section 11: the statistic ----------------------------------------------------------------

def newey_west_t(dstar: np.ndarray, dbar: float) -> tuple[float, float, int]:
    n = len(dstar)
    L = int(math.floor(4 * (n / 100) ** (2 / 9)))
    e = dstar - dbar
    gam = [float(np.sum(e[j:] * e[:n - j]) / n) for j in range(L + 1)]
    S = gam[0] + 2 * sum((1 - j / (L + 1)) * gam[j] for j in range(1, L + 1))
    if S <= 0:                                   # no variation at all: no evidence
        return 0.0, max(S, 0.0), L
    return dbar / math.sqrt(S / n), S, L


def primary(w: np.ndarray, Tr: Run, E: Run, wbar: np.ndarray) -> dict:
    r = Tr.r
    d = ((w - wbar) * r).sum(axis=1) - Tr.c + E.c
    dstar = ((w - wbar) * (r - r.mean(axis=0))).sum(axis=1) - Tr.c + E.c
    dbar = float(d.mean())
    t, S, L = newey_west_t(dstar, dbar)
    n = len(d)
    p = 0.5 * math.erfc(t / math.sqrt(2))
    se12 = 12 * math.sqrt(S / n)
    return {"n": n, "dbar": dbar, "dbar_annual": 12 * dbar, "t": t, "p": p, "L": L, "S": S,
            "ci90_annual": [12 * dbar - 1.645 * se12, 12 * dbar + 1.645 * se12],
            "tracking_error": math.sqrt(12 * S),
            "detectable_80pct": 3.4175 * math.sqrt(12 * S) / math.sqrt(n / 12),
            "passed": t >= Z_CRIT, "t_ge_3": t >= 3.0, "d": d.tolist(), "dstar": dstar.tolist()}


def identity_gap(w: np.ndarray, run: Run) -> float:
    """Section 7: R_t = sum_k w_kt r_kt - c_t exactly, on the price track before tax."""
    return float(np.max(np.abs(run.R - ((w * run.r).sum(axis=1) - run.c))))


def subset_dbar(w: np.ndarray, Tr: Run, idx: np.ndarray, panel: Panel, sch: Schedule, card: Card) -> float:
    wU = w[idx].mean(axis=0)
    EU = simulate("monthly", panel, sch, targets=np.tile(wU, (N_MONTHS, 1)), card=card)
    vals = ((w[idx] - wU) * Tr.r[idx]).sum(axis=1) - Tr.c[idx] + EU.c[idx]
    return float(vals.mean())


def max_drawdown(v: pd.Series) -> tuple[float, int]:
    """
    Depth on every point; length in calendar sessions (Amendment 1): from a
    peak's session to the first session back at or above it, or to the end,
    counted only for stretches that fall below the peak. X_1's two points are
    one session.
    """
    peak = v.cummax()
    dd = float((v / peak - 1).min())
    pos = pd.Series(range(len(v.index.unique())), index=v.index.unique())
    sess = pos.reindex(v.index).values
    longest, run_peak, peak_pos, fell = 0, v.iloc[0], sess[0], False
    for x, sp in zip(v.values, sess):
        if x >= run_peak:
            if fell:
                longest = max(longest, int(sp - peak_pos))
            run_peak, peak_pos, fell = x, sp, False
        else:
            fell = True
    if fell:
        longest = max(longest, int(sess[-1] - peak_pos))
    return dd, longest


# -- section 12: criteria -----------------------------------------------------------------------

def evaluate(panel: Panel, sch: Schedule, stale: bool = False, card: Card = GROWW) -> dict:
    Q = panel.Q
    w = faber_targets(Q, sch)
    wbar = w.mean(axis=0)
    Tr = simulate("monthly", panel, sch, targets=w, card=card, daily=True)
    E = simulate("monthly", panel, sch, targets=np.tile(wbar, (N_MONTHS, 1)), card=card, daily=True)
    out: dict = {"wbar": wbar.tolist(), "identity_gap": max(identity_gap(w, Tr),
                                                            identity_gap(np.tile(wbar, (N_MONTHS, 1)), E))}
    P = primary(w, Tr, E, wbar)
    out["P"] = P
    crit = []
    fy_hold = np.array([fy_of(pd.Timestamp(year=y, month=m, day=1)) for y, m in sch.hold_month])

    dev, hold = np.arange(DEV_LAST), np.arange(DEV_LAST, N_MONTHS)
    g1 = {"development": subset_dbar(w, Tr, dev, panel, sch, card),
          "holdout": subset_dbar(w, Tr, hold, panel, sch, card)}
    crit.append(("G1", all(v > 0 for v in g1.values()), g1))

    h = {}
    for j, k in enumerate(ETFS):
        h[k] = float(((w[:, j] - wbar[j]) * Tr.r[:, j] - Tr.ck[:, j] + E.ck[:, j]).mean())
    never = [k for j, k in enumerate(ETFS) if len(set(w[:, j])) == 1]
    crit.append(("G2", all(v > 0 for v in h.values()) and not never, {"h": h, "never_switched": never}))

    g3 = {}
    for y in sorted(set(fy_hold)):
        idx = np.nonzero(fy_hold != y)[0]
        g3[f"FY{y}"] = subset_dbar(w, Tr, idx, panel, sch, card)
    crit.append(("G3", all(v > 0 for v in g3.values()), g3))

    c2 = card.doubled()
    T2 = simulate("monthly", panel, sch, targets=w, card=c2)
    E2 = simulate("monthly", panel, sch, targets=np.tile(wbar, (N_MONTHS, 1)), card=c2)
    g4 = float((((w - wbar) * T2.r).sum(axis=1) - T2.c + E2.c).mean())
    crit.append(("G4", g4 > 0, g4))

    w12 = tsmom_targets(Q, sch)
    w12bar = w12.mean(axis=0)
    T12 = simulate("monthly", panel, sch, targets=w12, card=card)
    E12 = simulate("monthly", panel, sch, targets=np.tile(w12bar, (N_MONTHS, 1)), card=card)
    g5 = float((((w12 - w12bar) * T12.r).sum(axis=1) - T12.c + E12.c).mean())
    crit.append(("G5", g5 > 0, {"dbar": g5, "wbar12": w12bar.tolist()}))

    ddT, ddE = max_drawdown(Tr.daily)[0], max_drawdown(E.daily)[0]
    crit.append(("G6", abs(ddT) <= abs(ddE) + 0.05, {"maxdd_T": ddT, "maxdd_EXP": ddE}))

    if stale:
        try:
            s7 = schedule(panel, defer_from=3, defer_to=7)
            T7 = simulate("monthly", panel, s7, targets=w, card=card)
            E7 = simulate("monthly", panel, s7, targets=np.tile(wbar, (N_MONTHS, 1)), card=card)
            g7 = float((((w - wbar) * T7.r).sum(axis=1) - T7.c + E7.c).mean())
            crit.append(("G7", g7 > 0, g7))
        except NotEvaluable as exc:
            crit.append(("G7", False, f"no session by the seventh: {exc}"))
    else:
        crit.append(("G7", True, "not triggered: neither ETF flagged stale"))

    Tt = simulate("monthly", panel, sch, targets=w, card=card, taxed=True)
    Et = simulate("monthly", panel, sch, targets=np.tile(wbar, (N_MONTHS, 1)), card=card, taxed=True)
    crit.append(("G8", Tt.after_tax >= Et.after_tax, {"T": Tt.after_tax, "EXP": Et.after_tax}))

    out["criteria"] = [{"id": "P", "passed": P["passed"], "detail": {k: P[k] for k in ("t", "p", "dbar_annual")}}]
    out["criteria"] += [{"id": i, "passed": bool(ok), "detail": det} for i, ok, det in crit]

    D = {}
    try:
        for track in ("price", "div"):
            eqp = eq_transform(panel, sch, div=(track == "div"))
            TD = simulate("monthly", eqp, sch, targets=faber_targets(eqp.Q, sch), card=card, taxed=True,
                          div=(track == "div"))
            MD = simulate("MIX", eqp, sch, card=card, taxed=True, div=(track == "div"))
            D[track] = {"T": TD.after_tax, "MIX": MD.after_tax, "ok": TD.after_tax >= MD.after_tax}
        d_ok = all(v["ok"] for v in D.values())
    except NotEvaluable as exc:
        D, d_ok = {"error": str(exc)}, False
    out["D"] = {"passed": d_ok, "detail": D}
    binding = all(c["passed"] for c in out["criteria"])
    out["verdict"] = ("PASS" if d_ok else "PASS (timing) — NO TRADE: D fails") if binding else "FAIL — NO TRADE"
    out["runs"] = {"T": Tr, "EXP": E, "T_tax": Tt, "EXP_tax": Et}
    out["targets"] = {"T": w, "T12": w12}
    return out


# -- section 15: metrics -------------------------------------------------------------------------

def line_metrics(run: Run, sch: Schedule, months: slice = slice(0, N_MONTHS)) -> dict:
    V = run.V[months.start:months.stop + 1]
    R = V[1:] / V[:-1] - 1
    n = len(R)
    sd = float(np.std(R, ddof=1))
    rf6 = 1.06 ** (1 / 12) - 1
    out = {"months": n, "cagr": float((V[-1] / V[0]) ** (12 / n) - 1), "vol": sd * math.sqrt(12),
           "sharpe0": float(R.mean() / sd * math.sqrt(12)), "sharpe6": float((R.mean() - rf6) / sd * math.sqrt(12))}
    if run.daily is not None:
        a, b = sch.X[months.start], sch.X[months.stop]
        dd, longest = max_drawdown(run.daily[(run.daily.index >= a) & (run.daily.index <= b)])
        out.update({"max_dd": dd, "longest_dd_sessions": longest,
                    "mar": out["cagr"] / abs(dd) if dd < 0 else None})
    return out


def calendar_returns(run: Run) -> dict:
    """Year-end to year-end on the daily series; the first and last years are marked partial."""
    v = run.daily
    ends = v.groupby(v.index.year).last()
    years = list(ends.index)
    prev, start = v.iloc[0], v.index[0]
    out = {}
    for y, x in ends.items():
        out[str(y)] = {"return": float(x / prev - 1), "partial": y in (years[0], years[-1]),
                       "from": str(start.date()), "to": str(v[v.index.year == y].index[-1].date())}
        prev, start = x, v[v.index.year == y].index[-1]
    return out


# -- reported only ------------------------------------------------------------------------------

def sharpe_diff_hac(a: np.ndarray, b: np.ndarray) -> dict:
    """
    HAC delta-method test of SR(a) - SR(b): Ledoit & Wolf's (2008) gradient and
    moment vector, with a Newey-West (Bartlett) kernel at the section-11 L in place
    of their prewhitened quadratic-spectral kernel (Amendment 1).
    """
    n = len(a)
    mu_a, mu_b = a.mean(), b.mean()
    g_a, g_b = (a ** 2).mean(), (b ** 2).mean()
    sa, sb = math.sqrt(g_a - mu_a ** 2), math.sqrt(g_b - mu_b ** 2)
    diff = mu_a / sa - mu_b / sb
    Y = np.column_stack([a - mu_a, b - mu_b, a ** 2 - g_a, b ** 2 - g_b])
    L = int(math.floor(4 * (n / 100) ** (2 / 9)))
    Psi = Y.T @ Y / n
    for j in range(1, L + 1):
        Gj = Y[j:].T @ Y[:-j] / n
        Psi += (1 - j / (L + 1)) * (Gj + Gj.T)
    grad = np.array([g_a / sa ** 3, -g_b / sb ** 3, -mu_a / (2 * sa ** 3), mu_b / (2 * sb ** 3)])
    se = math.sqrt(float(grad @ Psi @ grad) / n)
    return {"sr_diff_monthly": diff, "sr_diff_annual": diff * math.sqrt(12), "se": se,
            "z": diff / se, "p_two_sided": math.erfc(abs(diff / se) / math.sqrt(2))}


def regression_alpha(R: np.ndarray, r: np.ndarray) -> dict:
    """R^T on r_N and r_G with an intercept; Newey-West t for alpha."""
    n = len(R)
    Xm = np.column_stack([np.ones(n), r])
    beta, *_ = np.linalg.lstsq(Xm, R, rcond=None)
    e = R - Xm @ beta
    L = int(math.floor(4 * (n / 100) ** (2 / 9)))
    Xe = Xm * e[:, None]
    S = Xe.T @ Xe / n
    for j in range(1, L + 1):
        Gj = Xe[j:].T @ Xe[:-j] / n
        S += (1 - j / (L + 1)) * (Gj + Gj.T)
    XtX = np.linalg.inv(Xm.T @ Xm / n)
    cov = XtX @ S @ XtX / n
    return {"alpha_monthly": float(beta[0]), "alpha_annual": float(12 * beta[0]),
            "beta_N": float(beta[1]), "beta_G": float(beta[2]), "t_alpha": float(beta[0] / math.sqrt(cov[0, 0]))}


def stationary_bootstrap_p(d: np.ndarray, block: float = 6.0, reps: int = 10_000, seed: int = 20261018) -> float:
    """
    Politis & Romano (1994), on d* (Amendment 1), centred at its mean, circular
    wrap. Per resample: idx0 = rng.integers(n); jump = rng.random(n) < 1/block;
    starts = rng.integers(n, size=n); element 0 of jump and starts unused.
    p = share of resamples with mean(d*[idx] - mean) >= mean.
    """
    rng = np.random.default_rng(seed)
    n, dbar = len(d), d.mean()
    c = d - dbar
    p_new = 1 / block
    hits = 0
    for _ in range(reps):
        idx = np.empty(n, dtype=int)
        idx[0] = rng.integers(n)
        jump = rng.random(n) < p_new
        starts = rng.integers(n, size=n)
        for i in range(1, n):
            idx[i] = starts[i] if jump[i] else (idx[i - 1] + 1) % n
        if c[idx].mean() >= dbar:
            hits += 1
    return hits / reps


@dataclass
class Synthetic:
    """Section 19 size and power checks: GARCH(1,1) with Student-t(5) innovations."""
    mu: tuple = (0.10 / 252, 0.12 / 252)
    vol: tuple = (0.18, 0.15)
    a: float = 0.08
    b: float = 0.90
    nu: int = 5
    start: str = "2009-01-01"
    end: str = "2026-10-09"
    timing: float = 0.0                 # power check: daily drift +timing while in, -timing while out
    seed: int = 20261018
    extra: dict = field(default_factory=dict)

    def panels(self, count: int):
        """Yields `count` synthetic Panels, generated in order from one Generator."""
        rng = np.random.default_rng(self.seed)
        days = pd.bdate_range(self.start, self.end)
        scale = math.sqrt((self.nu - 2) / self.nu)
        for _ in range(count):
            logp = np.zeros((len(days), 2))
            month = days.year * 12 + days.month
            is_me = np.r_[month[1:] != month[:-1], True]
            for j in range(2):
                var_u = (self.vol[j] ** 2) / 252
                omega = var_u * (1 - self.a - self.b)
                eps = rng.standard_t(self.nu, size=len(days)) * scale
                s2, x, prev_shock = var_u, 0.0, 0.0
                mes = []
                drift = self.mu[j]
                for i in range(len(days)):
                    if i:                                # day 0 closes at exactly 100
                        s2 = omega + self.a * prev_shock ** 2 + self.b * s2
                        shock = math.sqrt(s2) * eps[i]
                        x += drift + shock
                        prev_shock = shock
                    logp[i, j] = x
                    if self.timing and is_me[i]:
                        mes.append(math.exp(x))
                        if len(mes) >= 10:
                            inn = mes[-1] > np.mean(mes[-10:])
                            drift = self.mu[j] + (self.timing if inn else -self.timing)
            close = pd.DataFrame(100 * np.exp(logp), index=days, columns=list(ETFS))
            yield Panel(days, close, pd.DataFrame(True, index=days, columns=list(ETFS)))


def p_only(panel: Panel, card: Card = GROWW) -> dict:
    sch = schedule(panel)
    w = faber_targets(panel.Q, sch)
    wbar = w.mean(axis=0)
    Tr = simulate("monthly", panel, sch, targets=w, card=card)
    E = simulate("monthly", panel, sch, targets=np.tile(wbar, (N_MONTHS, 1)), card=card)
    P = primary(w, Tr, E, wbar)
    return {"t": P["t"], "passed": P["passed"], "ir": P["dbar_annual"] / P["tracking_error"]
            if P["tracking_error"] > 0 else 0.0}
