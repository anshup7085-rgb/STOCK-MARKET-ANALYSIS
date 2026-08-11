"""
Trade construction: entry, stop, targets, size.

RISK_POLICY.md drives all of it:
  - define maximum loss before entry
  - define invalidation before entry
  - never assume a stop guarantees the exact exit price
  - if account equity is unknown, give the formula rather than invent a rupee amount

Stops are ATR-based rather than round numbers, so the invalidation distance
scales with the instrument's own volatility instead of an arbitrary percentage.

Targets are measured to confirmed structure — the swing highs a rally would
actually have to trade through — and NOT to arithmetic multiples of the stop.
The distinction matters more than it looks. When targets are defined as k x risk,
reward/risk comes back as exactly k for every instrument on every day: true by
construction, and therefore worth nothing as a ranking signal. Measuring reward
to the chart instead lets a name with a ceiling overhead score differently from
one with clear air above it, which is the entire point of the comparison.
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
    target_basis: str            # "structure" | "r-multiple"
    notes: list[str]


def _targets(
    px: float, risk: float, ladder: list[float], th: Thresholds, sign: int
) -> tuple[float, float, float, str]:
    """
    Place T1/T2/stretch on the next three structural levels in the trade's
    direction, falling back to R-multiples only where the chart offers nothing.

    `sign` is +1 for a long (levels above) and -1 for a short (levels below);
    `ladder` arrives ordered nearest-first in either case.

    Falling back is not a failure — a stock at an all-time high genuinely has no
    overhead structure to measure against. It is recorded as the basis so the
    reader knows which kind of number they are looking at.
    """
    if not ladder:
        return (
            px + sign * th.target1_r * risk,
            px + sign * th.target2_r * risk,
            px + sign * th.stretch_r * risk,
            "r-multiple",
        )

    t1 = ladder[0]
    # Beyond the levels the chart supplies, step on by 1.5R rather than
    # inventing structure that isn't there.
    t2 = ladder[1] if len(ladder) > 1 else t1 + sign * th.target1_r * risk
    stretch = ladder[2] if len(ladder) > 2 else t2 + sign * th.target1_r * risk
    return t1, t2, stretch, "structure"


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
        ladder = [r for r in s.resistance if r >= px + 0.25 * atr_val]
        t1, t2, stretch, basis = _targets(px, risk, ladder, th, +1)
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
        ladder = [r for r in s.support if r <= px - 0.25 * atr_val]
        t1, t2, stretch, basis = _targets(px, risk, ladder, th, -1)

    # The number that used to be a constant. Reward is now whatever the chart
    # actually offers between here and T2, expressed in units of risk.
    reward_risk = abs(t2 - px) / risk

    if basis == "structure":
        n_struct = min(len(ladder), 3)
        which = ["T1", "T2", "stretch"][:n_struct]
        extended = ["T1", "T2", "stretch"][n_struct:]
        msg = f"{', '.join(which)} on confirmed swing structure"
        if extended:
            msg += f"; {', '.join(extended)} extended by 1.5R (chart offers nothing further)"
        notes.append(f"{msg} — T2 sits {abs(t2 - px) / atr_val:.1f} ATR away")
        if abs(t1 - px) < 0.5 * risk:
            notes.append(
                f"first resistance sits only {abs(t1 - px) / risk:.2f}R overhead — "
                "the move has to clear it before the trade pays"
            )
    else:
        notes.append(
            "no confirmed structure in the trade's direction — targets fall back to "
            "R-multiples, so this R:R is assumed rather than measured"
        )

    if abs(t2 - px) / atr_val > 6:
        notes.append(
            f"T2 is {abs(t2 - px) / atr_val:.1f} ATRs away — a stretch for a "
            "few-days-to-weeks horizon; consider it a trail target, not a plan"
        )

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
        reward_risk=round(reward_risk, 2),
        atr=round(atr_val, 2),
        structural_stop_used=use_structural,
        target_basis=basis,
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
