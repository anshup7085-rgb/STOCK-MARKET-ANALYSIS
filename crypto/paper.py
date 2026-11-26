"""
The crypto paper book: the two rules that beat holding on both coins in the no-tax what-if
(research/INTRADAY_LOG.md step 40), ma50 and mom30 on BTC and ETH, followed forward from the day this
first runs, beside buy-and-hold of each coin. Paper money only; nothing is ever ordered.

Each account starts with Rs1,00,000 and keeps two ledgers that follow the same signals:
  free    no tax on gains, as for a resident of a country that does not tax crypto
  india   India's tax: 31.2% of every sale's gain over its cost, no loss set off, fees not deductible

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


def _new() -> dict:
    return {"in": False, "free": {"cash": CAPITAL, "units": 0.0},
            "india": {"cash": CAPITAL, "units": 0.0, "cost": 0.0, "tax_paid": 0.0}}


def step(acct: dict, want: bool, price: float) -> tuple[dict, str | None]:
    """One day for one account: buy or sell at `price` if the rule changed its mind. Returns (new, action)."""
    a = {"in": acct["in"], "free": dict(acct["free"]), "india": dict(acct["india"])}
    if want and not a["in"]:
        f, i = a["free"], a["india"]
        f["units"], f["cash"] = f["cash"] * (1 - FEE) / price, 0.0
        i["cost"], i["units"], i["cash"] = i["cash"], i["cash"] * (1 - FEE) / price, 0.0
        a["in"] = True
        return a, "BUY"
    if not want and a["in"]:
        f, i = a["free"], a["india"]
        f["cash"], f["units"] = f["units"] * price * (1 - FEE), 0.0
        gain = i["units"] * price - i["cost"]
        tax = TAX * gain if gain > 0 else 0.0
        i["cash"], i["tax_paid"] = i["units"] * price * (1 - FEE) - tax, i["tax_paid"] + tax
        i["units"], i["cost"] = 0.0, 0.0
        a["in"] = False
        return a, "SELL"
    return a, None


def worth(acct: dict, price: float) -> tuple[float, float]:
    """(tax-free value, India value if sold now after its tax)."""
    f, i = acct["free"], acct["india"]
    free = f["cash"] + f["units"] * price * (1 - FEE)
    gain = i["units"] * price - i["cost"]
    india = i["cash"] + i["units"] * price * (1 - FEE) - (TAX * gain if i["units"] and gain > 0 else 0.0)
    return round(free, 2), round(india, 2)


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
                 coins=list(COINS), rules=list(RULES))
        todo, state = [start], {f"{r} {c}": _new() for r in RULES for c in COINS}
    else:
        last = date.fromisoformat(days_done[-1])
        todo = [d for d in common if d > last]
    for d in todo:
        ends = datetime.combine(d + timedelta(days=1), datetime.min.time(), timezone.utc)
        rec, actions = {}, []
        for c in COINS:
            closes = [k.close for k in series[c][:by_day[c][d] + 1]]
            price = closes[-1]
            for r in RULES:
                key = f"{r} {c}"
                want = positions(closes, r)[-1]
                acct, act = step(state[key], want, price)
                acct["price"] = price
                acct["action"] = act
                acct["free_value"], acct["india_value"] = worth(acct, price)
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
                     "day": last["day"], "since": days[0]["payload"]["day"], "days": len(days)})
    return rows
