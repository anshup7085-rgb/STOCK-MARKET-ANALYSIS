"""
The crypto paper book: the two rules that beat holding on both coins in the no-tax what-if
(research/INTRADAY_LOG.md step 40), ma50 and mom30 on BTC and ETH, followed forward from the day this
first runs, beside buy-and-hold of each coin. Paper money only; nothing is ever ordered.

Each account starts with Rs1,00,000 and keeps three ledgers that follow the same signals:
  free    no tax on gains, as for a resident of a country that does not tax crypto
  india   India's VDA tax: 31.2% of every sale's gain over its cost, no loss set off, fees not deductible
  spec    the reading that crypto futures are speculative business income (added 26 Nov 2026, at the
          holder's request; unsettled in law, research/INTRADAY_LOG.md step 42): each financial year's net
          profit, fees deducted, taxed at the new regime's slab rates with the rebate up to Rs12 lakh and 4%
          cess, on top of OTHER_INCOME; a year's loss carried forward four years against later profits.
          The slabs and rebate are from memory, to be checked by the holder. Tax falls due when a financial
          year ends (31 March); a tax due while the coin is held is paid from outside (cash can go below 0).

Once a day, after the UTC day's candle closes, each rule decides from closes up to that day (as in
crypto/strategies.py) and trades at that close, paying 0.25% a side. A day processed more than 30 minutes
after it ended (the server was down) is still processed in order, deterministically, and marked
`caught_up`. The journal (state/crypto/paper/journal.jsonl) is hash-chained; every day's record holds
every account's full state, so the book can be checked by reading it alone.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from crypto import store
from crypto.strategies import positions

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "state" / "crypto" / "paper"
COINS = ("BTCUSDT", "ETHUSDT")
RULES = ("hold", "ma50", "mom30")
CAPITAL, FEE, TAX = 100_000.0, 0.0025, 0.312
LATE = timedelta(minutes=30)
OTHER_INCOME = 0.0                       # the holder's other income in the year, for the slab rate
SLABS = ((400_000, 0.0), (800_000, 0.05), (1_200_000, 0.10), (1_600_000, 0.15), (2_000_000, 0.20),
         (2_400_000, 0.25), (float("inf"), 0.30))
REBATE_UPTO, CESS, CARRY_YEARS = 1_200_000, 0.04, 4


def fy_of(d: date) -> int:
    """The financial year (April to March) a day belongs to, by the year it starts in."""
    return d.year if d.month >= 4 else d.year - 1


def slab_tax(income: float) -> float:
    """New-regime income tax with the rebate up to Rs12 lakh (and its marginal relief) and 4% cess."""
    tax, low = 0.0, 0.0
    for top, rate in SLABS:
        if income > low:
            tax += (min(income, top) - low) * rate
        low = top
    if income <= REBATE_UPTO:
        tax = 0.0
    else:
        tax = min(tax, income - REBATE_UPTO)
    return round(tax * (1 + CESS), 2)


def spec_tax(profit: float) -> float:
    """The extra tax a year's net speculative profit adds on top of OTHER_INCOME."""
    return slab_tax(OTHER_INCOME + profit) - slab_tax(OTHER_INCOME) if profit > 0 else 0.0


def _spec_new(fy: int) -> dict:
    return {"cash": CAPITAL, "units": 0.0, "cost": 0.0, "fy": fy, "fy_pnl": 0.0, "carry": [], "tax_paid": 0.0}


def _new(fy: int = 0) -> dict:
    return {"in": False, "free": {"cash": CAPITAL, "units": 0.0},
            "india": {"cash": CAPITAL, "units": 0.0, "cost": 0.0, "tax_paid": 0.0}, "spec": _spec_new(fy)}


def _after_carry(pnl: float, carry: list) -> tuple[float, list]:
    """A year's profit after the losses carried in (oldest first); returns (taxable, carry left)."""
    left = []
    for fy, loss in sorted(carry):
        use = min(loss, max(pnl, 0.0))
        pnl -= use
        if loss - use > 0:
            left.append([fy, loss - use])
    return pnl, left


def close_year(spec: dict, new_fy: int) -> dict:
    """The financial year `spec["fy"]` ends: tax its net profit after carried losses, carry a loss."""
    s = dict(spec, carry=[list(c) for c in spec["carry"]])
    taxable, carry = _after_carry(s["fy_pnl"], s["carry"])
    if taxable > 0:
        t = spec_tax(taxable)
        s["cash"] -= t
        s["tax_paid"] += t
    elif s["fy_pnl"] < 0:
        carry.append([s["fy"], -s["fy_pnl"]])
    s["carry"] = [c for c in carry if new_fy - c[0] <= CARRY_YEARS]
    s["fy"], s["fy_pnl"] = new_fy, 0.0
    return s


def step(acct: dict, want: bool, price: float) -> tuple[dict, str | None]:
    """One day for one account: buy or sell at `price` if the rule changed its mind. Returns (new, action)."""
    a = {"in": acct["in"], "free": dict(acct["free"]), "india": dict(acct["india"]),
         "spec": dict(acct["spec"], carry=[list(c) for c in acct["spec"]["carry"]])}
    if want and not a["in"]:
        f, i, sp = a["free"], a["india"], a["spec"]
        f["units"], f["cash"] = f["cash"] * (1 - FEE) / price, 0.0
        i["cost"], i["units"], i["cash"] = i["cash"], i["cash"] * (1 - FEE) / price, 0.0
        spend = max(sp["cash"], 0.0)
        sp["cost"], sp["units"], sp["cash"] = spend, spend * (1 - FEE) / price, sp["cash"] - spend
        a["in"] = True
        return a, "BUY"
    if not want and a["in"]:
        f, i, sp = a["free"], a["india"], a["spec"]
        f["cash"], f["units"] = f["units"] * price * (1 - FEE), 0.0
        gain = i["units"] * price - i["cost"]
        tax = TAX * gain if gain > 0 else 0.0
        i["cash"], i["tax_paid"] = i["units"] * price * (1 - FEE) - tax, i["tax_paid"] + tax
        i["units"], i["cost"] = 0.0, 0.0
        proceeds = sp["units"] * price * (1 - FEE)                # business income: fees are deducted
        sp["fy_pnl"] += proceeds - sp["cost"]
        sp["cash"], sp["units"], sp["cost"] = sp["cash"] + proceeds, 0.0, 0.0
        a["in"] = False
        return a, "SELL"
    return a, None


def worth(acct: dict, price: float) -> tuple[float, float, float]:
    """(tax-free value, India VDA value, speculative-business value), each as if sold now, after its tax."""
    f, i, sp = acct["free"], acct["india"], acct["spec"]
    free = f["cash"] + f["units"] * price * (1 - FEE)
    gain = i["units"] * price - i["cost"]
    india = i["cash"] + i["units"] * price * (1 - FEE) - (TAX * gain if i["units"] and gain > 0 else 0.0)
    proceeds = sp["units"] * price * (1 - FEE)
    pnl = sp["fy_pnl"] + (proceeds - sp["cost"] if sp["units"] else 0.0)
    taxable, _ = _after_carry(pnl, sp["carry"])
    spec = sp["cash"] + proceeds - spec_tax(taxable)
    return round(free, 2), round(india, 2), round(spec, 2)


def run(now: datetime, root: Path | None = None) -> list[str]:
    """Process every closed UTC day not yet in the journal; returns lines for the log."""
    from trading.journal import Journal
    root = root or ROOT
    root.mkdir(parents=True, exist_ok=True)
    j = Journal(root / "journal.jsonl", "crypto-paper")
    recs = j.records()
    days_done = [r["payload"]["day"] for r in recs if r["type"] == "CP_DAY"]
    state = recs[-1]["payload"]["accounts"] if days_done else None
    out = []
    series = {c: [k for k in store.load(c, "1d")] for c in COINS}
    if any(len(v) < 250 for v in series.values()):
        return ["crypto paper book: too little price history"]
    by_day = {c: {store.utc_day(k): i for i, k in enumerate(v)} for c, v in series.items()}
    common = sorted(set(by_day[COINS[0]]).intersection(*[set(by_day[c]) for c in COINS[1:]]))
    if state is None:
        start = common[-1]                                       # the book begins with the latest closed day
        j.append("CP_INIT", asof=start.isoformat(), start=start.isoformat(), capital=CAPITAL, fee=FEE, tax=TAX,
                 coins=list(COINS), rules=list(RULES), other_income=OTHER_INCOME)
        todo, state = [start], {f"{r} {c}": _new(fy_of(start)) for r in RULES for c in COINS}
    else:
        last = date.fromisoformat(days_done[-1])
        todo = [d for d in common if d > last]
        if any("spec" not in a for a in state.values()):         # the third ledger joined a running book
            for a in state.values():
                i = a["india"]
                a["spec"] = {"cash": i["cash"], "units": i["units"], "cost": i["cost"], "fy": fy_of(last),
                             "fy_pnl": 0.0, "carry": [], "tax_paid": 0.0}
            j.append("CP_NOTE", asof=last.isoformat(), note="the speculative-business ledger added; it starts from "
                     "each account's VDA ledger as of this day", other_income=OTHER_INCOME)
    for d in todo:
        ends = datetime.combine(d + timedelta(days=1), datetime.min.time(), timezone.utc)
        rec, actions = {}, []
        for c in COINS:
            closes = [k.close for k in series[c][:by_day[c][d] + 1]]
            price = closes[-1]
            for r in RULES:
                key = f"{r} {c}"
                want = positions(closes, r)[-1]
                prev = state[key]
                if prev["spec"]["fy"] != fy_of(d):                # a financial year has ended: its tax falls due
                    prev = dict(prev, spec=close_year(prev["spec"], fy_of(d)))
                acct, act = step(prev, want, price)
                acct["price"] = price
                acct["action"] = act
                acct["free_value"], acct["india_value"], acct["spec_value"] = worth(acct, price)
                rec[key] = acct
                if act:
                    actions.append(f"{act} {c[:-4]} ({r}) at ${price:,.2f}")
        state = rec
        j.append("CP_DAY", asof=d.isoformat(), day=d.isoformat(), accounts=rec, caught_up=now > ends + LATE)
        out.append(f"crypto paper book {d}: " + ("; ".join(actions) if actions else "no trade"))
    return out or ["crypto paper book: nothing new to process"]


def summary(root: Path | None = None) -> list[dict]:
    """The latest state of every account: rule, coin, holding, the two values and the change since the start."""
    from trading.journal import Journal
    path = (root or ROOT) / "journal.jsonl"
    if not path.exists():
        return []
    recs = Journal(path, "crypto-paper").records()
    days = [r for r in recs if r["type"] == "CP_DAY"]
    if not days:
        return []
    last = days[-1]["payload"]
    rows = []
    for key, a in last["accounts"].items():
        rule, coin = key.split(" ")
        rows.append({"rule": rule, "coin": coin[:-4], "in": a["in"], "free": a["free_value"], "india": a["india_value"],
                     "spec": a.get("spec_value"),
                     "day": last["day"], "since": days[0]["payload"]["day"], "days": len(days)})
    return rows
