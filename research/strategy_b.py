"""
Strategy B — 12-1 cross-sectional momentum, and B+A with the index trend
filter, exactly as pre-registered (research/PREREGISTRATION.md).

At each month-end M: momentum = close(M-1) / close(M-12) - 1. Buy the top 10,
equal weight, at the close of the first trading day after M; hold to the next
rebalance. Costs are 0.25% of traded notional per side, with weights drifting
between rebalances so turnover is what a real account would trade.
"""

from __future__ import annotations

import math

import pandas as pd

import research.data as data
from research.metrics import perf, split

COST_PER_SIDE = 0.0025
TOP_N = 10


def panel(symbols: list[str]) -> pd.DataFrame:
    closes = {}
    for s in symbols:
        try:
            closes[s] = data.load(s)["close"]
        except FileNotFoundError:
            continue
    return pd.DataFrame(closes).sort_index().ffill()


def schedule(px: pd.DataFrame) -> pd.DataFrame:
    """Month-ends M, and the execution day E (first trading day after M)."""
    days = px.index
    month_end = days.to_series().groupby([days.year, days.month]).max().sort_values()
    rows = []
    for m in month_end:
        nxt = days[days > m]
        if len(nxt):
            rows.append({"M": m, "E": nxt[0]})
    return pd.DataFrame(rows)


def run(px: pd.DataFrame, nifty: pd.Series, trend_filter: bool = False,
        equal_weight: bool = False) -> pd.DataFrame:
    """
    One row per holding month k: executed at E_k, held to E_{k+1}.
    Columns: E, holdings, turnover, gross, net.
    """
    sch = schedule(px)
    sma = nifty.rolling(200, min_periods=200).mean()
    months = list(sch["M"])
    rows = []
    weights = pd.Series(dtype=float)        # drifted weights going into each rebalance
    for k in range(12, len(sch) - 1):
        M, E, E_next = sch.loc[k, "M"], sch.loc[k, "E"], sch.loc[k + 1, "E"]
        m1, m12 = months[k - 1], months[k - 12]
        mom = (px.loc[m1] / px.loc[m12] - 1).dropna()
        if equal_weight:
            pick = sorted(px.loc[E].dropna().index)
        else:
            pick = sorted(mom.index, key=lambda s: (-mom[s], s))[:TOP_N]   # ties: alphabetical
        in_cash = False
        if trend_filter:
            level, avg = nifty.asof(M), sma.asof(M)
            in_cash = not (isinstance(avg, float) and not math.isnan(avg) and level >= avg)
        if in_cash or not pick:
            target = pd.Series(dtype=float)
        else:
            target = pd.Series(1.0 / len(pick), index=pick)
        names = sorted(set(target.index) | set(weights.index))
        turnover = float(sum(abs(target.get(s, 0.0) - weights.get(s, 0.0)) for s in names))
        if target.empty:
            gross, new_w = 0.0, pd.Series(dtype=float)
        else:
            rets = px.loc[E_next, target.index] / px.loc[E, target.index] - 1
            gross = float((target * rets).sum())
            grown = target * (1 + rets)
            new_w = grown / grown.sum()
        net = (1 - COST_PER_SIDE * turnover) * (1 + gross) - 1
        rows.append({"M": M, "E": E, "E_next": E_next, "holdings": list(target.index),
                     "turnover": turnover, "gross": gross, "net": net})
        weights = new_w
    out = pd.DataFrame(rows)
    out.index = pd.DatetimeIndex(out["E"])
    return out


def _excess(a: pd.Series, b: pd.Series) -> dict:
    x = (a - b).dropna()
    se = float(x.std(ddof=1)) / math.sqrt(len(x)) if len(x) > 1 else float("nan")
    return {"ann_excess_vs_EW": float(x.mean()) * 12, "excess_tstat": float(x.mean()) / se if se else None,
            "n_months": len(x)}


def evaluate(symbols: list[str]) -> dict:
    px = panel(symbols)
    nifty = data.load("^NSEI")["close"]
    runs = {
        "B": run(px, nifty),
        "B+A": run(px, nifty, trend_filter=True),
        "EW": run(px, nifty, equal_weight=True),
    }
    idx = runs["B"].index
    nf = pd.Series([nifty.asof(e2) / nifty.asof(e) - 1 for e, e2 in zip(runs["B"]["E"], runs["B"]["E_next"])], index=idx)
    out: dict = {"rows": [], "criteria": [], "universe": list(px.columns)}
    res = {}
    for name in ("B", "B+A", "EW", "Nifty50"):
        series = nf if name == "Nifty50" else runs[name]["net"]
        for period, sub in split(series).items():
            m = perf(sub, 12)
            if name in ("B", "B+A"):
                m.update(_excess(sub, split(runs["EW"]["net"])[period]))
                m["avg_turnover"] = float(runs[name].loc[sub.index, "turnover"].mean())
            m.update({"strategy": name, "period": period})
            out["rows"].append(m)
            res[(name, period)] = m

    def crit(prefix: str, s: str) -> list[bool]:
        h, d, f, ew_h, ew_f = (res[(s, "holdout")], res[(s, "development")], res[(s, "full")],
                               res[("EW", "holdout")], res[("EW", "full")])
        checks = [
            ("B1", h["ann_excess_vs_EW"] > 0 and h["sharpe"] >= ew_h["sharpe"],
             f"hold-out excess {h['ann_excess_vs_EW']:+.2%}/yr; Sharpe {h['sharpe']:.2f} vs EW {ew_h['sharpe']:.2f}"),
            ("B2", d["ann_excess_vs_EW"] > 0, f"development excess {d['ann_excess_vs_EW']:+.2%}/yr"),
            ("B3", (f["excess_tstat"] or 0) > 2.0, f"full-period t-stat {f['excess_tstat']:.2f} (n={f['n_months']})"),
            ("B4", abs(f["max_dd"]) <= 1.25 * abs(ew_f["max_dd"]),
             f"|maxDD| {abs(f['max_dd']):.3f} vs 1.25 x EW {abs(ew_f['max_dd']):.3f} = {1.25 * abs(ew_f['max_dd']):.3f}"),
        ]
        for cid, ok, detail in checks:
            out["criteria"].append({"id": f"{prefix}{cid}", "passed": bool(ok), "detail": detail})
        return [ok for _, ok, _ in checks]

    b_ok = all(crit("B:", "B"))
    ba = crit("B+A:", "B+A")
    dd_ok = abs(res[("B+A", "full")]["max_dd"]) <= abs(res[("B", "full")]["max_dd"])
    out["criteria"].append({"id": "B+A:DD", "passed": dd_ok,
                            "detail": f"|maxDD| B+A {abs(res[('B+A', 'full')]['max_dd']):.3f} vs B {abs(res[('B', 'full')]['max_dd']):.3f}"})
    out["passed"] = {"B": b_ok, "B+A": all(ba) and dd_ok}
    sch = schedule(px)
    out["rebalance_dates"] = sorted({str(d.date()) for d in pd.concat([sch["M"], sch["E"]])})
    out["first_rebalance"] = str(idx[0].date())
    out["last_rebalance"] = str(idx[-1].date())
    out["holdings_last"] = runs["B"]["holdings"].iloc[-1]
    return out
