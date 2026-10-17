"""
Strategy A — index trend filter, exactly as pre-registered (research/PREREGISTRATION.md).

Hold the Nifty 50 while its close is above its 200-day SMA, else cash. A signal
on close t executes at close t+1, so the position earning day d's return is the
target decided at close d-2. Each switch costs 0.10% of equity, charged on the
execution day.
"""

from __future__ import annotations

import pandas as pd

from research.metrics import perf, split

COST_PER_SWITCH = 0.0010


def daily_returns(close: pd.Series, sma: int = 200, cost: float = COST_PER_SWITCH,
                  cash_rate: float = 0.0) -> pd.DataFrame:
    """
    Columns: target (decided at that close), position (held over that day),
    switch_cost, strategy (net daily return), benchmark (buy-and-hold).
    Rows start at the first day whose position is defined: first SMA + 2 days.
    """
    avg = close.rolling(sma, min_periods=sma).mean()
    valid = avg.notna()
    target = (close > avg).where(valid)                    # NaN before the SMA exists
    position = target.shift(2)                             # executed at t+1, earns from t+2
    r = close.pct_change()
    # The switch executes at close d when position(d+1) != position(d):
    # charge it to day d, the day the trade happens.
    executes = (position.shift(-1) != position) & position.shift(-1).notna() & position.notna()
    cash_daily = (1 + cash_rate) ** (1 / 252) - 1
    df = pd.DataFrame({
        "target": target,
        "position": position,
        "switch_cost": executes.astype(float) * cost,
        "benchmark": r,
    })
    df = df[df["position"].notna()].copy()
    pos = df["position"].astype(float)
    df["strategy"] = pos * df["benchmark"] + (1 - pos) * cash_daily - df["switch_cost"]
    return df


def evaluate(close: pd.Series) -> dict:
    """Every metric and every pass criterion the pre-registration names."""
    base = daily_returns(close)
    out: dict = {"rows": [], "criteria": []}

    def add(name: str, period: str, series: pd.Series, extra: dict | None = None) -> dict:
        m = perf(series, 252)
        m.update({"strategy": name, "period": period, **(extra or {})})
        out["rows"].append(m)
        return m

    res = {}
    for period, idx in {k: v.index for k, v in split(base["strategy"]).items()}.items():
        sub = base.loc[idx]
        years = len(sub) / 252
        switches = int((sub["switch_cost"] > 0).sum())
        res[("A", period)] = add("A", period, sub["strategy"], {
            "time_in_market": float(sub["position"].astype(float).mean()),
            "switches": switches,
            "switches_per_year": switches / years if years else None,
        })
        res[("BuyHold", period)] = add("BuyHold", period, sub["benchmark"])

    for name, kw in (("A_SMA150", {"sma": 150}), ("A_SMA250", {"sma": 250}), ("A_cash6pct", {"cash_rate": 0.06})):
        d = daily_returns(close, **kw)
        add(name, "robustness", d["strategy"], {
            "time_in_market": float(d["position"].astype(float).mean()),
            "benchmark_over_same_dates": perf(d["benchmark"], 252),
        })

    fa, fb = res[("A", "full")], res[("BuyHold", "full")]
    ha, hb = res[("A", "holdout")], res[("BuyHold", "holdout")]
    crit = out["criteria"]
    crit.append({"id": "A1", "passed": abs(fa["max_dd"]) <= 0.6 * abs(fb["max_dd"]),
                 "detail": f"|maxDD| {abs(fa['max_dd']):.3f} vs 0.6 x {abs(fb['max_dd']):.3f} = {0.6 * abs(fb['max_dd']):.3f}"})
    crit.append({"id": "A2", "passed": fa["sharpe"] >= fb["sharpe"],
                 "detail": f"Sharpe {fa['sharpe']:.3f} vs buy-and-hold {fb['sharpe']:.3f}"})
    crit.append({"id": "A3", "passed": abs(ha["max_dd"]) <= abs(hb["max_dd"]) and ha["sharpe"] >= 0.8 * hb["sharpe"],
                 "detail": f"hold-out |maxDD| {abs(ha['max_dd']):.3f} vs {abs(hb['max_dd']):.3f}; "
                           f"Sharpe {ha['sharpe']:.3f} vs 0.8 x {hb['sharpe']:.3f} = {0.8 * hb['sharpe']:.3f}"})
    crit.append({"id": "A4", "passed": fa["switches_per_year"] <= 6,
                 "detail": f"{fa['switches_per_year']:.2f} switches per year"})
    out["passed"] = all(c["passed"] for c in crit)
    return out
