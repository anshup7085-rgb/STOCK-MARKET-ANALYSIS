"""
Walk-forward backtest harness.

The question this exists to answer is the one the whole project rests on and has
never been asked: **does a higher score actually precede a better outcome?**
Until that has a number attached, the scoring model is an opinion with arithmetic
around it.

Method, and the honesty constraints on each step:

  - At every rebalance bar i, the snapshot is built from `df.iloc[:i+1]` and
    nothing else. PROMPT.md principle 8. The indicators are already proven
    causal by `test_no_lookahead`, and swing pivots carry a confirmation delay,
    so a level is never known before the market could have known it.
  - Entry is the close of bar i — the last price actually printed when the
    signal fired. No entering on the open of the signal bar.
  - The path is then walked bar by bar. Stop and target are checked against the
    real high/low, not the close.
  - When a bar's range spans both stop and target, the STOP is taken. Intraday
    order is unknowable from daily bars, and assuming the good fill is how a
    backtest flatters itself.
  - A gap through the stop fills at the open, not at the stop. RISK_POLICY.md:
    'Never assume a stop guarantees the exact exit price.'
  - Charges, STT and slippage are still excluded. Every R below is therefore
    optimistic by a real, unmodelled amount.

What it deliberately does NOT do: survivorship correction (the universe is
today's liquid names, which is a real forward-looking bias), corporate-action
adjustment, or borrow-cost modelling for shorts. Those are named in SETUP.md and
they are not fixed here — read the results with that in mind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

import pandas as pd

from market.config import Thresholds
from market.indicators import build_snapshot, relative_strength
from market.levels import build_levels
from market.score import build_score
from market.universe import classify_regime, liquidity_check


@dataclass
class Trade:
    symbol: str
    entry_date: str
    entry: float
    stop: float
    target: float
    score: float
    coverage: float
    reward_risk: float
    target_basis: str
    outcome: str            # "target" | "stop" | "timeout"
    r_multiple: float
    bars_held: int


@dataclass
class BandStats:
    label: str
    n: int
    win_rate: float
    mean_r: float
    median_r: float
    target_rate: float
    stop_rate: float


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    bands: list[BandStats] = field(default_factory=list)
    spearman: float | None = None
    horizon: int = 20
    direction: str = "long"
    notes: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def expectancy_r(self) -> float | None:
        return mean(t.r_multiple for t in self.trades) if self.trades else None


def simulate(
    df: pd.DataFrame, i: int, entry: float, stop: float, target: float,
    horizon: int, direction: str,
) -> tuple[str, float, int]:
    """
    Walk the path forward from bar i and return (outcome, R multiple, bars held).

    R is measured against the ORIGINAL risk per share, so a gap-through fill
    reports worse than -1R — which is exactly what happens in the account.
    """
    risk = abs(entry - stop)
    if risk <= 0:
        return "timeout", 0.0, 0
    long = direction == "long"
    last = min(i + horizon, len(df) - 1)

    for j in range(i + 1, last + 1):
        o = float(df["open"].iloc[j])
        hi = float(df["high"].iloc[j])
        lo = float(df["low"].iloc[j])

        # Gap straight through the stop: filled at the open, not the stop.
        if (long and o <= stop) or (not long and o >= stop):
            return "stop", (o - entry) / risk * (1 if long else -1), j - i

        hit_stop = lo <= stop if long else hi >= stop
        hit_target = hi >= target if long else lo <= target

        # Both in one bar -> assume the stop. Daily bars cannot tell us which
        # came first, and guessing the target is how backtests lie.
        if hit_stop:
            return "stop", -1.0, j - i
        if hit_target:
            return "target", abs(target - entry) / risk, j - i

    close = float(df["close"].iloc[last])
    return "timeout", (close - entry) / risk * (1 if long else -1), last - i


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation, ties averaged. Implemented here to avoid a scipy dep."""
    n = len(xs)
    if n < 3:
        return None

    def rank(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda k: v[k])
        r = [0.0] * n
        k = 0
        while k < n:
            j = k
            while j + 1 < n and v[order[j + 1]] == v[order[k]]:
                j += 1
            avg = (k + j) / 2 + 1
            for m in range(k, j + 1):
                r[order[m]] = avg
            k = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _band(trades: list[Trade], label: str, lo: float, hi: float) -> BandStats | None:
    sel = [t for t in trades if lo <= t.score < hi]
    if not sel:
        return None
    rs = [t.r_multiple for t in sel]
    rs_sorted = sorted(rs)
    mid = len(rs_sorted) // 2
    median = (
        rs_sorted[mid] if len(rs_sorted) % 2
        else (rs_sorted[mid - 1] + rs_sorted[mid]) / 2
    )
    return BandStats(
        label=label,
        n=len(sel),
        win_rate=sum(1 for r in rs if r > 0) / len(rs) * 100,
        mean_r=mean(rs),
        median_r=median,
        target_rate=sum(1 for t in sel if t.outcome == "target") / len(sel) * 100,
        stop_rate=sum(1 for t in sel if t.outcome == "stop") / len(sel) * 100,
    )


def run_backtest(
    bars: dict[str, pd.DataFrame],
    th: Thresholds,
    *,
    benchmark: pd.DataFrame | None = None,
    horizon: int = 20,
    step: int = 5,
    warmup: int = 200,
    direction: str = "long",
) -> BacktestResult:
    """
    Walk every symbol forward, scoring only on history and settling on the path.

    `step` spaces out rebalance bars. Sampling every bar would produce heavily
    overlapping trades whose outcomes share the same price action, which inflates
    the apparent sample size without adding independent evidence.
    """
    res = BacktestResult(horizon=horizon, direction=direction)
    bench_close = benchmark["close"] if benchmark is not None else None

    for sym, df in bars.items():
        if len(df) < warmup + horizon + 1:
            res.notes.append(f"{sym}: only {len(df)} bars — skipped")
            continue

        for i in range(warmup, len(df) - horizon, step):
            hist = df.iloc[: i + 1]                      # causal slice
            try:
                snap = build_snapshot(hist, sym)
            except ValueError:
                continue

            if not liquidity_check(snap, th).passed:
                continue

            levels = build_levels(snap, th, direction)
            if levels is None:
                continue

            regime = "unknown"
            rs = None
            if bench_close is not None:
                bhist = bench_close.loc[: hist.index[-1]]
                if len(bhist) >= 60:
                    regime = classify_regime(
                        benchmark.loc[: hist.index[-1]]
                    ).regime
                    rs = relative_strength(hist["close"], bhist, 60)

            score = build_score(
                snap,
                regime=regime,
                direction=direction,
                rel_strength=rs,
                reward_risk=levels.reward_risk,
                min_turnover_cr=th.min_median_turnover_cr,
                min_rr=th.min_reward_risk,
                max_staleness=th.max_bar_staleness_days,
            )

            outcome, r, held = simulate(
                df, i, levels.reference_price, levels.stop, levels.target2,
                horizon, direction,
            )
            res.trades.append(
                Trade(
                    symbol=sym,
                    entry_date=str(hist.index[-1].date()),
                    entry=round(levels.reference_price, 2),
                    stop=levels.stop,
                    target=levels.target2,
                    score=round(score.raw, 1),
                    coverage=round(score.coverage, 2),
                    reward_risk=levels.reward_risk,
                    target_basis=levels.target_basis,
                    outcome=outcome,
                    r_multiple=round(r, 3),
                    bars_held=held,
                )
            )

    for label, lo, hi in [
        ("<40", 0, 40), ("40-50", 40, 50), ("50-60", 50, 60),
        ("60-70", 60, 70), ("70-80", 70, 80), ("80+", 80, 1e9),
    ]:
        b = _band(res.trades, label, lo, hi)
        if b:
            res.bands.append(b)

    res.spearman = _spearman(
        [t.score for t in res.trades], [t.r_multiple for t in res.trades]
    )
    res.notes.append(
        "Charges, STT and slippage excluded — every R here is optimistic."
    )
    res.notes.append(
        "Universe is today's liquid names, so results carry survivorship bias."
    )
    return res


def format_report(res: BacktestResult) -> str:
    """Plain-text summary. The verdict line is deliberately blunt."""
    out = [
        f"Backtest — {res.direction}, {res.horizon}-bar horizon, {res.n} trades",
        "",
        f"{'score band':>11s} {'n':>5s} {'win%':>7s} {'mean R':>8s} "
        f"{'med R':>7s} {'target%':>8s} {'stop%':>7s}",
    ]
    for b in res.bands:
        out.append(
            f"{b.label:>11s} {b.n:5d} {b.win_rate:6.1f}% {b.mean_r:8.3f} "
            f"{b.median_r:7.3f} {b.target_rate:7.1f}% {b.stop_rate:6.1f}%"
        )

    exp = res.expectancy_r
    out += ["", f"overall expectancy: {exp:+.3f}R per trade" if exp is not None else "no trades"]
    if res.spearman is not None:
        out.append(f"score vs outcome (Spearman): {res.spearman:+.3f}")

    out.append("")
    if res.spearman is None or res.n < 30:
        out.append("VERDICT: sample too small to conclude anything.")
    elif res.spearman > 0.10:
        out.append("VERDICT: score carries signal — higher scores did precede better outcomes.")
    elif res.spearman < -0.10:
        out.append("VERDICT: score is INVERTED — it ranked the wrong way round.")
    else:
        out.append(
            "VERDICT: no detectable relationship between score and outcome. "
            "The model ranks, but not usefully."
        )

    out += [""] + [f"note: {n}" for n in res.notes]
    return "\n".join(out)
