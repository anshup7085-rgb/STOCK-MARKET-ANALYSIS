"""
Trade construction: entry, stop, targets, size.

RISK_POLICY.md drives all of it:
  - define maximum loss before entry
  - define invalidation before entry
  - never assume a stop guarantees the exact exit price
  - if account equity is unknown, give the formula rather than invent a rupee amount

Stops are ATR-based rather than round numbers, so the invalidation distance
scales with the instrument's own volatility instead of an arbitrary percentage.
"""

from __future__ import annotations

from dataclasses import dataclass

from market.config import Thresholds
from market.indicators import Snapshot


@dataclass
class TradeLevels:
    symbol: str
    direction: str
    reference_price: float
    entry_low: float
    entry_high: float
    breakout_entry: float | None
    stop: float
    target1: float
    target2: float
    stretch: float
    risk_per_share: float
    reward_risk: float
    atr: float
    structural_stop_used: bool
    notes: list[str]


def build_levels(s: Snapshot, th: Thresholds, direction: str = "long") -> TradeLevels | None:
    """
    Return None when levels cannot be built honestly — missing ATR, or a stop
    that would sit on the wrong side of price. Never fabricate a level.
    """
    if s.atr14 is None or s.atr14 <= 0 or s.close is None or s.close <= 0:
        return None

    atr_val = s.atr14
    px = s.close
    notes: list[str] = []

    if direction == "long":
        atr_stop = px - th.stop_atr_multiple * atr_val
        # Prefer recent swing low if it sits just under the ATR stop — real
        # structure beats an arithmetic level, provided it isn't absurdly far.
        structural = s.low_20
        use_structural = (
            structural is not None
            and structural < px
            and structural >= atr_stop - atr_val
            and structural <= atr_stop + 0.5 * atr_val
        )
        stop = structural if use_structural else atr_stop
        if use_structural:
            notes.append("stop anchored to 20-bar swing low")
        entry_low, entry_high = px - 0.35 * atr_val, px + 0.25 * atr_val
        breakout = s.high_20
        risk = px - stop
        if risk <= 0:
            return None
        t1 = px + th.target1_r * risk
        t2 = px + th.target2_r * risk
        stretch = px + th.stretch_r * risk
    else:
        atr_stop = px + th.stop_atr_multiple * atr_val
        structural = s.high_20
        use_structural = (
            structural is not None
            and structural > px
            and structural <= atr_stop + atr_val
            and structural >= atr_stop - 0.5 * atr_val
        )
        stop = structural if use_structural else atr_stop
        if use_structural:
            notes.append("stop anchored to 20-bar swing high")
        entry_low, entry_high = px - 0.25 * atr_val, px + 0.35 * atr_val
        breakout = s.low_20
        risk = stop - px
        if risk <= 0:
            return None
        t1 = px - th.target1_r * risk
        t2 = px - th.target2_r * risk
        stretch = px - th.stretch_r * risk

    if s.atr_pct is not None and s.atr_pct > 5:
        notes.append(
            f"ATR is {s.atr_pct:.1f}% of price — expect slippage; the stop is a "
            "trigger, not a guaranteed fill"
        )

    return TradeLevels(
        symbol=s.symbol,
        direction=direction,
        reference_price=px,
        entry_low=round(entry_low, 2),
        entry_high=round(entry_high, 2),
        breakout_entry=round(breakout, 2) if breakout else None,
        stop=round(stop, 2),
        target1=round(t1, 2),
        target2=round(t2, 2),
        stretch=round(stretch, 2),
        risk_per_share=round(risk, 2),
        reward_risk=round(th.target2_r, 2),
        atr=round(atr_val, 2),
        structural_stop_used=use_structural,
        notes=notes,
    )


@dataclass
class PositionSize:
    quantity: int | None
    max_loss: float | None
    capital_required: float | None
    pct_of_equity: float | None
    formula: str
    warnings: list[str]


def size_position(
    levels: TradeLevels,
    equity: float | None,
    risk_pct: float,
    max_single_position_pct: float,
    median_turnover_cr: float | None = None,
) -> PositionSize:
    """Position size from max acceptable loss and stop distance."""
    formula = "quantity = (equity x risk% ) / |entry - stop|"
    warnings: list[str] = []

    if equity is None:
        return PositionSize(
            None, None, None, None, formula,
            ["Account equity not supplied — set ACCOUNT_EQUITY to get a rupee size. "
             "Formula given instead of an invented number."],
        )

    max_loss = equity * (risk_pct / 100.0)
    qty = int(max_loss // levels.risk_per_share)

    if qty <= 0:
        warnings.append(
            "Stop distance exceeds the whole risk budget at 1 share. "
            "Either the instrument is too volatile for this account or risk% is too low."
        )
        return PositionSize(0, 0.0, 0.0, 0.0, formula, warnings)

    capital = qty * levels.reference_price
    pct_equity = capital / equity * 100

    if pct_equity > max_single_position_pct:
        capped_qty = int((equity * max_single_position_pct / 100) // levels.reference_price)
        warnings.append(
            f"Risk-based size is {pct_equity:.0f}% of equity, above the "
            f"{max_single_position_pct:.0f}% concentration cap. Trimmed "
            f"{qty} -> {capped_qty} shares; actual risk falls below the budget."
        )
        qty = capped_qty
        capital = qty * levels.reference_price
        pct_equity = capital / equity * 100
        max_loss = qty * levels.risk_per_share

    if median_turnover_cr is not None:
        position_cr = capital / 1e7
        if position_cr > median_turnover_cr * 0.01:
            warnings.append(
                f"Position is Rs{position_cr:.2f}cr against median daily turnover of "
                f"Rs{median_turnover_cr:.0f}cr — exiting on a bad day will move the price."
            )

    warnings.append("Charges, STT and slippage are NOT deducted from this max loss.")

    return PositionSize(
        quantity=qty,
        max_loss=round(max_loss, 2),
        capital_required=round(capital, 2),
        pct_of_equity=round(pct_equity, 2),
        formula=formula,
        warnings=warnings,
    )
