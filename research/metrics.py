"""Performance metrics on a return series. Decimals throughout (-0.38 = -38%)."""

from __future__ import annotations

import math

import pandas as pd


def perf(returns: pd.Series, periods_per_year: int) -> dict:
    """CAGR, volatility, Sharpe (0% risk-free), max drawdown and MAR for one return series."""
    r = returns.dropna()
    if len(r) < 2:
        return {"n": len(r), "cagr": None, "vol": None, "sharpe": None, "max_dd": None, "mar": None}
    equity = (1.0 + r).cumprod()
    years = len(r) / periods_per_year
    cagr = float(equity.iloc[-1] ** (1.0 / years) - 1.0)
    sd = float(r.std(ddof=1))
    vol = sd * math.sqrt(periods_per_year)
    sharpe = float(r.mean()) / sd * math.sqrt(periods_per_year) if sd > 0 else None
    curve = pd.concat([pd.Series([1.0]), equity.reset_index(drop=True)])
    max_dd = float((curve / curve.cummax() - 1.0).min())
    return {
        "n": len(r),
        "start": str(r.index[0].date()),
        "end": str(r.index[-1].date()),
        "cagr": cagr,
        "vol": vol,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "mar": cagr / abs(max_dd) if max_dd < 0 else None,
    }


def split(returns: pd.Series, dev_end: str = "2022-12-31") -> dict[str, pd.Series]:
    """The pre-registered periods: full, development (to dev_end), hold-out (after)."""
    cut = pd.Timestamp(dev_end)
    return {
        "full": returns,
        "development": returns[returns.index <= cut],
        "holdout": returns[returns.index > cut],
    }
