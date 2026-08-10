"""
Candidate universe and market-regime classification.

PROMPT.md: 'Do not assume that a stock is tradable merely because it appears
interesting. Check liquidity, trading restrictions, corporate-action effects,
and data quality.' The liquidity gate here is the first of those checks.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from market.config import Thresholds
from market.indicators import Snapshot, ema, rsi
from market.score import Regime

BENCHMARK = "^NSEI"          # Nifty 50 spot
BANK_INDEX = "^NSEBANK"

# A liquid starting universe. Replace with the live F&O list or a Nifty 500
# constituent dump for a full scan -- see SETUP.md.
DEFAULT_UNIVERSE: list[str] = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "BHARTIARTL",
    "ITC", "LT", "HCLTECH", "AXISBANK", "KOTAKBANK", "MARUTI", "SUNPHARMA",
    "TITAN", "ULTRACEMCO", "TATAMOTORS", "TATASTEEL", "M&M", "NTPC", "POWERGRID",
    "WIPRO", "TECHM", "ADANIENT", "ADANIPORTS", "JSWSTEEL", "COALINDIA",
    "BAJFINANCE", "BAJAJFINSV", "HINDUNILVR", "ASIANPAINT", "NESTLEIND",
    "GRASIM", "CIPLA", "DRREDDY", "APOLLOHOSP", "EICHERMOT", "HEROMOTOCO",
    "BPCL", "IOC", "ONGC", "HAL", "BEL", "SIEMENS", "TRENT", "DLF", "INDIGO",
]


@dataclass
class LiquidityVerdict:
    passed: bool
    reasons: list[str]


def liquidity_check(s: Snapshot, th: Thresholds) -> LiquidityVerdict:
    reasons: list[str] = []

    if s.median_turnover_cr is None:
        reasons.append("turnover not computable")
    elif s.median_turnover_cr < th.min_median_turnover_cr:
        reasons.append(
            f"median turnover Rs{s.median_turnover_cr:.1f}cr < floor "
            f"Rs{th.min_median_turnover_cr:.0f}cr"
        )

    if s.avg_volume_20 is None:
        reasons.append("average volume not computable")
    elif s.avg_volume_20 < th.min_median_volume:
        reasons.append(
            f"avg volume {s.avg_volume_20:,.0f} < floor {th.min_median_volume:,}"
        )

    if s.bars_available < th.min_bars_required:
        reasons.append(
            f"{s.bars_available} bars < {th.min_bars_required} required for the model"
        )

    return LiquidityVerdict(passed=not reasons, reasons=reasons)


@dataclass
class RegimeCall:
    regime: Regime
    evidence: list[str]
    benchmark_close: float | None
    breadth_note: str


def classify_regime(bench: pd.DataFrame) -> RegimeCall:
    """
    Classify the broad market from benchmark price action alone.

    PROMPT.md asks the classification to be explained, not asserted, so every
    branch records the evidence that produced it.
    """
    if bench.empty or len(bench) < 60:
        return RegimeCall("unknown", ["insufficient benchmark history"], None, "n/a")

    close = bench["close"]
    px = float(close.iloc[-1])
    e20 = ema(close, 20).iloc[-1]
    e50 = ema(close, 50).iloc[-1]
    e200 = ema(close, 200).iloc[-1] if len(close) >= 200 else None
    r = rsi(close, 14).iloc[-1]

    ret20 = float((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) > 21 else 0.0
    daily_ret = close.pct_change().dropna()
    realised_vol = float(daily_ret.tail(20).std() * (252 ** 0.5) * 100)

    ev = [
        f"benchmark {px:,.2f}",
        f"20d return {ret20:+.1f}%",
        f"RSI {r:.1f}" if pd.notna(r) else "RSI n/a",
        f"20d realised vol {realised_vol:.1f}% annualised",
    ]

    above20 = px > e20 if pd.notna(e20) else False
    above50 = px > e50 if pd.notna(e50) else False
    above200 = (px > e200) if (e200 is not None and pd.notna(e200)) else None

    ev.append(
        "above 20/50EMA" if (above20 and above50)
        else "below 20/50EMA" if not (above20 or above50)
        else "mixed vs 20/50EMA"
    )
    if above200 is not None:
        ev.append("above 200EMA" if above200 else "below 200EMA")

    if realised_vol > 22:
        regime: Regime = "volatile"
    elif above20 and above50 and ret20 > 2:
        regime = "uptrend"
    elif (not above20) and (not above50) and ret20 < -2:
        regime = "downtrend"
    elif abs(ret20) <= 2:
        regime = "range"
    else:
        regime = "transitioning"

    return RegimeCall(regime, ev, px, "breadth requires advance/decline data — not in OHLCV")
