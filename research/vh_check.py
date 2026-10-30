"""
V-H, implementation B (research/PREREGISTRATION_4.md section 6).

Written apart from implementation A (trading/fno/vh.py): its own reading of both NSE layouts,
its own weekly chain and eligible sessions, its own strike rule, fills, charges, settlement
and betting test. It shares only the data files and the fee card's rates (as numbers). The
two must agree on every trade's net P&L within Rs1 and on the outcome; if they do not, the
failing outcome is recorded.
"""

from __future__ import annotations

import csv
import gzip
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from market.charges import FNO_ZERODHA as CARD

Q = 65
W = 100.0
BUDGET = 5000.0
FLOOR, CAP = 20.0, 80.0
MAX_LOTS = 10
END = date(2026, 10, 18)


def money(x: float) -> Decimal:
    return Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def order_cost(price: float, qty: int, sell: bool) -> Decimal:
    t = price * qty
    brk = CARD.brokerage_per_order
    exch = t * CARD.opt_exchange_pct / 100
    sebi = t * CARD.sebi_pct / 100
    parts = [brk, t * CARD.opt_stt_sell_pct / 100 if sell else 0.0, exch, sebi,
             0.0 if sell else t * CARD.opt_stamp_buy_pct / 100, (brk + exch + sebi) * CARD.gst_pct / 100]
    return sum((money(x) for x in parts), Decimal("0"))


def expiry_cost(intrinsic: float, strike: float, value: float, qty: int, long: bool) -> Decimal:
    if intrinsic <= 0:
        return Decimal("0")
    brk = CARD.brokerage_per_order
    sebi = max(value, strike) * qty * CARD.sebi_pct / 100
    parts = [brk, intrinsic * qty * CARD.opt_stt_exercise_pct / 100 if long else 0.0, 0.0, sebi, 0.0,
             (brk + sebi) * CARD.gst_pct / 100]
    return sum((money(x) for x in parts), Decimal("0"))


def worth(kind: str, strike: float, value: float) -> float:
    return max(0.0, value - strike) if kind == "CE" else max(0.0, strike - value)


def expiry_costs(legs, value: float, qty: int) -> Decimal:
    return sum((expiry_cost(worth(k, s, value), s, value, qty, side == "B") for k, s, side, _ in legs), Decimal("0"))


def tick(close: float, pen: float, up: bool) -> float:
    """close +/- pen, exact in decimal, then to the 0.05 tick: buys up, sells down."""
    x = Decimal(repr(close)) + (Decimal(repr(pen)) if up else -Decimal(repr(pen)))
    n = (x / Decimal("0.05")).to_integral_value(rounding="ROUND_CEILING" if up else "ROUND_FLOOR")
    return float(n * Decimal("0.05"))


def read_day(path: Path) -> list[tuple]:
    """(expiry, strike, kind, close, volume, underlying) for every NIFTY option row."""
    text = gzip.open(path, "rt", encoding="utf-8-sig").read().splitlines()
    rdr = csv.reader(text)
    head = [h.strip() for h in next(rdr)]
    ix = {h: i for i, h in enumerate(head)}
    out = []
    for row in rdr:
        if not row:
            continue
        g = lambda name: row[ix[name]].strip()                              # noqa: E731
        if "TckrSymb" in ix:
            out.append((date.fromisoformat(g("XpryDt")[:10]), float(g("StrkPric")), g("OptnTp").upper(),
                        float(g("ClsPric") or 0), float(g("TtlTradgVol") or 0),
                        float(g("UndrlygPric")) if g("UndrlygPric") else None))
        else:
            out.append((datetime.strptime(g("EXPIRY_DT"), "%d-%b-%Y").date(), float(g("STRIKE_PR")),
                        g("OPTION_TYP").upper(), float(g("CLOSE") or 0), float(g("CONTRACTS") or 0), None))
    return out


def index_close(index_dir: Path, d: date, rows) -> float | None:
    f = index_dir / f"{d.year}" / f"ind_close_all_{d.strftime('%d%m%Y')}.csv"
    if f.exists():
        for row in csv.reader(f.read_text(encoding="utf-8-sig", errors="replace").splitlines()):
            if row and row[0].strip().lower() == "nifty 50":
                head = next(csv.reader(f.read_text(encoding="utf-8-sig", errors="replace").splitlines()))
                col = [h.strip().lower() for h in head].index("closing index value")
                try:
                    v = float(row[col])
                    if v > 0:
                        return v
                except ValueError:
                    pass
    u = {r[5] for r in rows or [] if r[5]}
    return u.pop() if len(u) == 1 else None


def bets(rs: list[float]) -> list[float]:
    wealth, path = 1.0, []
    for i in range(len(rs)):
        past = rs[:i]
        n = len(past) + 1
        mean = sum(past) / n
        var = max((0.25 + sum(x * x for x in past)) / n - mean ** 2, 1e-6)
        frac = min(0.5, max(0.0, mean / (var + mean ** 2)))
        wealth = wealth * (1 + frac * rs[i])
        path.append(wealth)
    return path


def compute(raw_dir: Path, index_dir: Path, penalty) -> list[dict]:
    files = {}
    for f in sorted(raw_dir.glob("*/*.csv.gz")):
        d = date.fromisoformat(f.name[:10])
        if d <= END:
            files[d] = f
    sessions = sorted(files)
    seen = set()
    for d in sessions:                                   # one pass for the expiry dates only
        seen |= {r[0] for r in read_day(files[d])}
    all_exp = sorted(seen)
    cache: dict = {}

    class Days:                                          # a day's rows, read when first needed
        def __contains__(self, d):
            return d in files

        def __getitem__(self, d):
            if d not in cache:
                if len(cache) > 12:
                    cache.pop(next(iter(cache)))
                cache[d] = read_day(files[d])
            return cache[d]

        def get(self, d, default=None):
            return self[d] if d in files else default

    days = Days()
    chain = []
    for j, e in enumerate(all_exp):
        near_before = j > 0 and (e - all_exp[j - 1]).days <= 10
        near_after = j + 1 < len(all_exp) and (all_exp[j + 1] - e).days <= 10
        if near_before or near_after:
            chain.append(e)
    results = []
    for j in range(1, len(chain)):
        e, prev = chain[j], chain[j - 1]
        if e > END:
            break
        cands = []
        for d in sessions:
            if prev < d < e and len([x for x in sessions if d < x <= e]) >= 3:
                cands.append(d)
        if not cands:
            continue
        rec = {"expiry": e, "outcome": None, "entry": None}
        if e not in days:
            rec["outcome"] = "left out"
            results.append(rec)
            continue
        for d in cands:
            spot = index_close(index_dir, d, days[d])
            if spot is None:
                continue
            book = {}
            for (x, k, kind, close, vol, _) in days[d]:
                if x == e:
                    book[(k, kind)] = (close, vol)
            strikes = sorted(k for (k, kind) in book if kind == "CE" and (k, "PE") in book)
            if not strikes:
                continue
            atm = sorted(strikes, key=lambda k: (abs(k - spot), k))[0]
            if book[(atm, "CE")][0] <= 0 or book[(atm, "PE")][0] <= 0:
                continue
            move = book[(atm, "CE")][0] + book[(atm, "PE")][0]
            sc = [k for (k, kind) in book if kind == "CE" and k >= spot + move]
            sp = [k for (k, kind) in book if kind == "PE" and k <= spot - move]
            if not sc or not sp:
                continue
            k_sc, k_sp = min(sc), max(sp)
            plan = [("CE", k_sc, "S"), ("PE", k_sp, "S"), ("CE", k_sc + W, "B"), ("PE", k_sp - W, "B")]
            legs, ok = [], True
            for kind, k, side in plan:
                q = book.get((k, kind))
                if q is None or q[0] <= 0 or q[1] <= 0:
                    ok = False
                    break
                px = tick(q[0], penalty(q[0]), side == "B")
                if side == "S" and px <= 0:
                    ok = False
                    break
                legs.append((kind, k, side, px))
            if not ok:
                continue
            credit = legs[0][3] + legs[1][3] - legs[2][3] - legs[3][3]
            if credit < FLOOR - 1e-9 or credit > CAP + 1e-9:
                continue
            lots, m_best, cost_best = 0, None, None
            for n in range(1, MAX_LOTS + 1):
                qty = Q * n
                entry = sum((order_cost(px, qty, side == "S") for _, _, side, px in legs), Decimal("0"))
                allow = max(expiry_costs(legs, k_sc + W + W, qty), expiry_costs(legs, k_sp - W - W, qty))
                m = float(Decimal(repr((W - credit) * qty)) + entry + allow)
                if round(m, 2) > BUDGET:
                    break
                lots, m_best, cost_best = n, round(m, 2), float(entry)
            if lots == 0:
                continue
            rec["entry"], rec["legs"], rec["credit"], rec["lots"], rec["M"], rec["entry_costs"] = \
                d, legs, credit, lots, m_best, cost_best
            break
        if rec["entry"] is None:
            rec["outcome"] = "no trade"
            results.append(rec)
            continue
        value = index_close(index_dir, e, days.get(e))
        if value is None:
            rec["outcome"] = "left out"
            results.append(rec)
            continue
        qty = Q * rec["lots"]
        pts = rec["credit"]
        for kind, k, side, _ in rec["legs"]:
            pts += worth(kind, k, value) if side == "B" else -worth(kind, k, value)
        net = pts * qty - rec["entry_costs"] - float(expiry_costs(rec["legs"], value, qty))
        rec.update(outcome="trade", settle=value, net=round(net, 2), R=net / rec["M"])
        results.append(rec)
    return results


def outcome(results: list[dict]) -> str:
    rs = [r["R"] for r in results if r["outcome"] == "trade"]
    for i, k in enumerate(bets(rs)):
        if rs[i] < -2:
            return "FAIL"
        if k >= 200:
            return "PASS"
    return "FAIL"
