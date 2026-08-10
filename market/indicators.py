"""
Technical indicators.

PROMPT.md principle 8 — 'No hindsight. Do not use future information when
evaluating a historical setup.' Every function here is causal: the value at
row i uses only rows <= i. Nothing is centred, shifted backwards, or
forward-filled from the future.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    # All-gain window: RSI is 100 by definition, not NaN.
    return out.where(avg_loss != 0, 100.0).where(avg_gain.notna())


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR — the volatility unit used for every stop in this system."""
    return true_range(df).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rolling_high(df: pd.DataFrame, period: int) -> pd.Series:
    return df["high"].rolling(period, min_periods=period).max()


def rolling_low(df: pd.DataFrame, period: int) -> pd.Series:
    return df["low"].rolling(period, min_periods=period).min()


def relative_strength(close: pd.Series, bench_close: pd.Series, period: int = 60) -> float | None:
    """
    Excess return vs a benchmark over `period` bars, in percentage points.

    Indices are aligned by date first — comparing misaligned series is a classic
    way to manufacture a signal that isn't there.
    """
    joined = pd.concat([close.rename("s"), bench_close.rename("b")], axis=1).dropna()
    if len(joined) < period + 1:
        return None
    s = joined["s"]
    b = joined["b"]
    s_ret = (s.iloc[-1] / s.iloc[-period - 1] - 1) * 100
    b_ret = (b.iloc[-1] / b.iloc[-period - 1] - 1) * 100
    return float(s_ret - b_ret)


@dataclass
class Snapshot:
    """Everything the scorer needs from one symbol's price history."""

    symbol: str
    close: float
    ema20: float | None
    ema50: float | None
    ema200: float | None
    rsi14: float | None
    atr14: float | None
    atr_pct: float | None            # ATR as % of price — the volatility regime
    high_20: float | None
    low_20: float | None
    high_52w: float | None
    pct_from_52w_high: float | None
    volume: float
    avg_volume_20: float | None
    volume_ratio: float | None       # today vs 20-day average
    median_turnover_cr: float | None
    gap_pct: float | None            # today's open vs yesterday's close
    ret_5: float | None
    ret_20: float | None
    bars_available: int


def build_snapshot(df: pd.DataFrame, symbol: str) -> Snapshot:
    """Collapse an OHLCV frame into the final-bar feature set."""
    if df.empty:
        raise ValueError(f"{symbol}: empty frame")

    close = df["close"]
    e20, e50, e200 = ema(close, 20), ema(close, 50), ema(close, 200)
    r = rsi(close, 14)
    a = atr(df, 14)
    avg_vol = close.index.to_series().pipe(lambda _: df["volume"].rolling(20, min_periods=5).mean())
    turnover_cr = (df["close"] * df["volume"]) / 1e7  # rupees -> crore

    last = -1
    last_close = float(close.iloc[last])
    last_atr = _f(a.iloc[last])
    prev_close = float(close.iloc[-2]) if len(close) > 1 else None
    last_open = float(df["open"].iloc[last])

    high_52w = float(df["high"].tail(252).max()) if len(df) >= 20 else None

    return Snapshot(
        symbol=symbol,
        close=last_close,
        ema20=_f(e20.iloc[last]),
        ema50=_f(e50.iloc[last]),
        ema200=_f(e200.iloc[last]),
        rsi14=_f(r.iloc[last]),
        atr14=last_atr,
        atr_pct=(last_atr / last_close * 100) if last_atr and last_close else None,
        high_20=_f(rolling_high(df, 20).iloc[last]),
        low_20=_f(rolling_low(df, 20).iloc[last]),
        high_52w=high_52w,
        pct_from_52w_high=(
            (last_close / high_52w - 1) * 100 if high_52w else None
        ),
        volume=float(df["volume"].iloc[last]),
        avg_volume_20=_f(avg_vol.iloc[last]),
        volume_ratio=(
            float(df["volume"].iloc[last] / avg_vol.iloc[last])
            if _f(avg_vol.iloc[last])
            else None
        ),
        median_turnover_cr=float(turnover_cr.tail(20).median()) if len(df) >= 5 else None,
        gap_pct=((last_open / prev_close - 1) * 100) if prev_close else None,
        ret_5=_pct_change(close, 5),
        ret_20=_pct_change(close, 20),
        bars_available=len(df),
    )


def _f(v) -> float | None:
    """NaN -> None, so 'missing' is never silently arithmetic-ed into a number."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f


def _pct_change(series: pd.Series, periods: int) -> float | None:
    if len(series) < periods + 1:
        return None
    return float((series.iloc[-1] / series.iloc[-periods - 1] - 1) * 100)
