"""
Opportunity scoring — the 0-100 model from PROMPT.md, made auditable.

Two design rules carried straight from the kit:

  'Create a transparent 0-100 opportunity score using a methodology you explain.'
      -> every component returns its own reasoning string. Nothing is a black box.

  'Do not force a score if data quality is poor.'
      -> a component with no data is marked unavailable and dropped from BOTH
         numerator and denominator, rather than scored zero. Scoring a missing
         input as zero is indistinguishable from scoring it as bad, and that
         conflation is how you end up rejecting good setups for want of a feed.
         The share of the model you could actually fill is reported as
         `coverage`, and it caps confidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from market.indicators import Snapshot
from market.providers.base import OptionSnapshot

Regime = Literal["uptrend", "downtrend", "range", "volatile", "transitioning", "unknown"]


@dataclass
class Component:
    name: str
    points: float
    max_points: float
    available: bool
    reasoning: str

    @property
    def pct(self) -> float | None:
        if not self.available or self.max_points == 0:
            return None
        return self.points / self.max_points * 100


@dataclass
class Score:
    symbol: str
    components: list[Component] = field(default_factory=list)
    penalty: float = 0.0
    penalty_reasons: list[str] = field(default_factory=list)
    regime_penalty: float = 0.0
    regime_reason: str = ""

    @property
    def available(self) -> list[Component]:
        return [c for c in self.components if c.available]

    @property
    def missing(self) -> list[str]:
        return [c.name for c in self.components if not c.available]

    @property
    def coverage(self) -> float:
        """Fraction of the model's weight that had usable data behind it."""
        total = sum(c.max_points for c in self.components)
        if total == 0:
            return 0.0
        return sum(c.max_points for c in self.available) / total

    @property
    def raw(self) -> float:
        """Renormalised to 100 across available components, then penalised."""
        denom = sum(c.max_points for c in self.available)
        if denom == 0:
            return 0.0
        base = sum(c.points for c in self.available) / denom * 100
        return max(0.0, base - self.penalty - self.regime_penalty)

    @property
    def confidence(self) -> str:
        """Confidence is capped by coverage. Thin data cannot yield high conviction."""
        cov = self.coverage
        if cov < 0.55:
            return "low (insufficient data coverage)"
        if cov < 0.75:
            return "moderate"
        if self.raw >= 75:
            return "high"
        return "moderate"

    def explain(self) -> str:
        lines = [f"Score {self.raw:.1f}/100 | coverage {self.coverage:.0%} | {self.confidence}"]
        for c in self.components:
            if c.available:
                lines.append(f"  {c.name}: {c.points:.1f}/{c.max_points:.0f} — {c.reasoning}")
            else:
                lines.append(f"  {c.name}: UNAVAILABLE — {c.reasoning}")
        for r in self.penalty_reasons:
            lines.append(f"  PENALTY: {r}")
        if self.regime_reason:
            tag = "REGIME" if self.regime_penalty else "regime"
            lines.append(f"  {tag}: {self.regime_reason}")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------

def regime_adjustment(regime: Regime, direction: str = "long") -> tuple[float, str]:
    """
    Regime as a scan-level deduction, NOT a per-name component.

    It used to be one of the seven scored components, worth 10 points. That was
    a category error: the regime is a property of the market, so it returns the
    same number for every candidate in a run — measured spread across a 46-name
    scan was exactly 0.0. Ten points that cannot separate one name from another
    are ten points of noise in a ranking.

    Its real job is different. 'A long in a downtrend is swimming upstream' is a
    statement about whether to trade AT ALL, not about which name to prefer. So
    it now deducts from every candidate equally: the ranking is untouched, the
    absolute scores fall, and in a hostile tape the whole shortlist can drop
    below the floor and correctly produce NO TRADE.
    """
    table = {
        "uptrend": 0.0,
        "transitioning": 4.0,
        "range": 5.0,
        "volatile": 7.0,
        "downtrend": 10.0,
    }
    if regime == "unknown":
        return 0.0, (
            "regime not classified — no adjustment applied, but an unknown tape is "
            "itself a reason to size down"
        )
    pen = table.get(regime, 5.0)
    if direction == "short":
        pen = 10.0 - pen  # a downtrend helps a short as much as it hurts a long
    if pen == 0.0:
        return 0.0, f"{regime} ({direction}) — trading with the tape, no deduction"
    return pen, f"{regime} ({direction}) — {pen:.0f} pt deduction, applied to every candidate"


def score_trend(s: Snapshot) -> Component:
    """Trend structure, 20 pts. EMA stack + position vs 52w high."""
    if s.ema20 is None or s.ema50 is None or s.close is None:
        return Component("trend structure", 0, 20, False, "insufficient bars for EMA stack")

    pts = 0.0
    notes = []

    if s.close > s.ema20:
        pts += 4
        notes.append("above 20EMA")
    if s.close > s.ema50:
        pts += 4
        notes.append("above 50EMA")
    if s.ema200 is not None:
        if s.close > s.ema200:
            pts += 5
            notes.append("above 200EMA")
    else:
        pts += 2.5  # neutral credit; 200EMA genuinely unavailable
        notes.append("200EMA n/a")

    if s.ema20 > s.ema50:
        pts += 4
        notes.append("20>50 stacked")

    if s.pct_from_52w_high is not None and s.pct_from_52w_high > -10:
        pts += 3
        notes.append(f"{s.pct_from_52w_high:.1f}% from 52w high")

    return Component("trend structure", min(pts, 20), 20, True, ", ".join(notes) or "no trend confirmation")


def score_momentum(s: Snapshot, rel_strength: float | None) -> Component:
    """Momentum + relative strength, 15 pts. Punishes overbought extension."""
    if s.rsi14 is None:
        return Component("momentum/RS", 0, 15, False, "RSI unavailable")

    pts = 0.0
    notes = [f"RSI {s.rsi14:.1f}"]

    # Sweet spot is strong-but-not-parabolic. Above 75 you are buying extension.
    if 55 <= s.rsi14 <= 70:
        pts += 7
    elif 70 < s.rsi14 <= 75:
        pts += 4
        notes.append("extended")
    elif s.rsi14 > 75:
        pts += 1
        notes.append("overbought")
    elif 45 <= s.rsi14 < 55:
        pts += 3
    else:
        pts += 1
        notes.append("weak")

    if s.ret_20 is not None:
        if s.ret_20 > 10:
            pts += 4
        elif s.ret_20 > 3:
            pts += 3
        elif s.ret_20 > 0:
            pts += 1.5
        notes.append(f"20d {s.ret_20:+.1f}%")

    if rel_strength is not None:
        if rel_strength > 5:
            pts += 4
        elif rel_strength > 0:
            pts += 2
        notes.append(f"RS vs bench {rel_strength:+.1f}pp")
    else:
        notes.append("RS n/a")

    return Component("momentum/RS", min(pts, 15), 15, True, ", ".join(notes))


def score_liquidity(s: Snapshot, min_turnover_cr: float) -> Component:
    """Volume and liquidity, 10 pts. Also the hard tradability gate."""
    if s.median_turnover_cr is None:
        return Component("volume/liquidity", 0, 10, False, "turnover unavailable")

    pts = 0.0
    notes = [f"median turnover Rs{s.median_turnover_cr:.0f}cr"]

    t = s.median_turnover_cr
    if t >= min_turnover_cr * 8:
        pts += 6
    elif t >= min_turnover_cr * 3:
        pts += 5
    elif t >= min_turnover_cr:
        pts += 3
    else:
        pts += 0
        notes.append("BELOW LIQUIDITY FLOOR")

    if s.volume_ratio is not None:
        if s.volume_ratio > 1.5:
            pts += 4
            notes.append(f"volume {s.volume_ratio:.1f}x avg")
        elif s.volume_ratio > 1.0:
            pts += 2
            notes.append(f"volume {s.volume_ratio:.1f}x avg")
        else:
            notes.append(f"volume {s.volume_ratio:.1f}x avg (light)")

    return Component("volume/liquidity", min(pts, 10), 10, True, ", ".join(notes))


def score_catalyst(
    has_catalyst: bool | None,
    catalyst_note: str = "",
    verified: bool = False,
) -> Component:
    """
    Catalyst, 15 pts.

    Deliberately NOT auto-derived. DATA_SOURCES.md demands a named source and an
    event date for every material catalyst, and a scraper guessing from headlines
    cannot meet that bar. This stays a human/agent input with an audit trail.
    """
    if has_catalyst is None:
        return Component(
            "catalyst", 0, 15, False,
            "no catalyst assessed — supply one with a source and date, or leave unscored",
        )
    if not has_catalyst:
        return Component("catalyst", 2, 15, True, "no identified catalyst")
    pts = 13 if verified else 7
    tag = "verified" if verified else "UNVERIFIED — confirm before sizing"
    return Component("catalyst", pts, 15, True, f"{catalyst_note} ({tag})")


def score_derivatives(opt: OptionSnapshot | None) -> Component:
    """Derivatives/positioning, 10 pts. Unavailable on free feeds — say so."""
    if opt is None or not opt.available or opt.put_call_ratio is None:
        return Component(
            "derivatives/positioning", 0, 10, False,
            "no option chain on this provider — connect Kite for real OI",
        )
    pcr = opt.put_call_ratio
    if 0.9 <= pcr <= 1.3:
        pts, note = 7.0, "balanced positioning"
    elif pcr > 1.3:
        pts, note = 5.0, "put-heavy"
    else:
        pts, note = 4.0, "call-heavy"
    return Component("derivatives/positioning", pts, 10, True, f"PCR {pcr:.2f} — {note}")


def score_reward_risk(rr: float | None, min_rr: float) -> Component:
    """Risk/reward, 15 pts. The single most load-bearing component."""
    if rr is None:
        return Component("risk/reward", 0, 15, False, "levels not computable")
    if rr >= 4:
        pts = 15.0
    elif rr >= 3:
        pts = 12.0
    elif rr >= min_rr:
        pts = 9.0
    elif rr >= 1.5:
        pts = 4.0
    else:
        pts = 0.0
    flag = "" if rr >= min_rr else f" — BELOW {min_rr}:1 FLOOR"
    return Component("risk/reward", pts, 15, True, f"{rr:.2f}:1{flag}")


# --------------------------------------------------------------------------
# Penalties — max 15, per PROMPT.md
# --------------------------------------------------------------------------

def compute_penalty(
    s: Snapshot,
    staleness_days: float | None,
    max_staleness: int,
    event_risk: bool = False,
    event_note: str = "",
) -> tuple[float, list[str]]:
    pen = 0.0
    why: list[str] = []

    if staleness_days is not None and staleness_days > max_staleness:
        pen += 6
        why.append(f"data {staleness_days:.1f} days stale")

    if s.atr_pct is not None and s.atr_pct > 6:
        pen += 4
        why.append(f"ATR {s.atr_pct:.1f}% of price — stop will be wide, gaps likely")

    if s.gap_pct is not None and abs(s.gap_pct) > 4:
        pen += 3
        why.append(f"{s.gap_pct:+.1f}% gap — stop execution unreliable")

    if s.bars_available < 120:
        pen += 3
        why.append(f"only {s.bars_available} bars of history")

    if event_risk:
        pen += 5
        why.append(f"binary event risk: {event_note or 'unspecified'}")

    return min(pen, 15.0), why


def build_score(
    s: Snapshot,
    *,
    regime: Regime,
    direction: str = "long",
    rel_strength: float | None = None,
    reward_risk: float | None = None,
    options: OptionSnapshot | None = None,
    has_catalyst: bool | None = None,
    catalyst_note: str = "",
    catalyst_verified: bool = False,
    staleness_days: float | None = None,
    min_turnover_cr: float = 25.0,
    min_rr: float = 2.0,
    max_staleness: int = 4,
    event_risk: bool = False,
    event_note: str = "",
) -> Score:
    sc = Score(symbol=s.symbol)
    sc.components = [
        score_trend(s),
        score_momentum(s, rel_strength),
        score_liquidity(s, min_turnover_cr),
        score_catalyst(has_catalyst, catalyst_note, catalyst_verified),
        score_derivatives(options),
        score_reward_risk(reward_risk, min_rr),
    ]
    sc.penalty, sc.penalty_reasons = compute_penalty(
        s, staleness_days, max_staleness, event_risk, event_note
    )
    sc.regime_penalty, sc.regime_reason = regime_adjustment(regime, direction)
    return sc
