"""
The scan pipeline as a function.

`run_scan.py` used to hold all of this inline. The trading engine needs the
exact same arithmetic — a paper or live trade must be the plan the scanner
printed, not a cousin of it — so the pipeline lives here and both callers use
it. Output is unchanged for the CLI apart from two additions: suspected
corporate actions are now rejected (PROMPT.md: reject unresolved corporate-
action data), and a still-forming daily bar is dropped before scoring.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, time

import pandas as pd

from market.calendar import load_calendar
from market.charges import SCHEDULES, trade_economics
from market.config import IST, MARKET_CLOSE, MARKET_OPEN, Thresholds, settings
from market.corporate_actions import suspect_corporate_actions
from market.indicators import build_snapshot, relative_strength
from market.levels import build_levels, size_position
from market.providers.base import Bars, DataProvider, DataUnavailable
from market.score import build_score
from market.universe import BENCHMARK, DEFAULT_UNIVERSE, classify_regime, liquidity_check

NOTIONAL = 100_000.0    # basis for cost arithmetic when equity is unknown

# The session ends at 15:30, but NSE's official close is computed from the last
# half hour of trade and published after it, so a daily candle read at 15:31 can
# still change. Today's bar is treated as final only from here.
DAILY_BAR_FINAL = (16, 0)


def market_status(now: datetime) -> dict:
    """Checklist steps 1-3: date/time, exchange, open/closed."""
    cal = load_calendar()
    is_weekday = now.weekday() < 5
    is_holiday = cal.is_holiday(now.date())
    open_t = time(*MARKET_OPEN)
    close_t = time(*MARKET_CLOSE)
    is_open = is_weekday and not is_holiday and open_t <= now.time() <= close_t
    if is_open:
        note = "Live session"
    elif is_holiday:
        note = f"NSE holiday: {cal.holidays[now.date().isoformat()]}"
    else:
        note = "Outside session hours — the last bar is a closing print, not a live quote."
    if not cal.loaded:
        note += " NSE holidays are not checked here; verify against the exchange calendar."
    return {
        "timestamp_ist": now.isoformat(),
        "exchange": "NSE",
        "weekday": now.strftime("%A"),
        "session_open": is_open,
        "holiday_calendar": cal.status(),
        "note": note,
    }


def load_catalysts(path: str | None) -> dict:
    """
    Catalysts are supplied, never inferred.

    Expected shape, mirroring DATA_SOURCES.md's requirement of a source and date:
      {"HAL": {"note": "Q1 FY27 results", "date": "2026-08-12",
               "source": "NSE filing", "verified": true, "binary": true}}
    """
    if not path:
        return {}
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[warn] catalyst file unreadable ({exc}) — scoring without it", file=sys.stderr)
        return {}


def bar_date(ts) -> "datetime.date":
    """Calendar date of a bar index entry, in IST whether or not it is tz-aware."""
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert(IST)
    return t.date()


def completed_daily_bars(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """
    Drop today's daily bar until it is final.

    A live provider asked for daily candles at 09:20 returns a 'today' row built
    from five minutes of trade. Scoring on it would mix a partial bar into
    indicators the backtest computed on whole ones — the live signal would not
    be the signal that was tested.
    """
    if df.empty:
        return df
    if bar_date(df.index[-1]) == now.date() and now.time() < time(*DAILY_BAR_FINAL):
        return df.iloc[:-1]
    return df


@dataclass
class ScanResult:
    report: dict
    bars: dict[str, Bars] = field(default_factory=dict)
    benchmark: Bars | None = None


def scan(
    provider: DataProvider,
    symbols: list[str] | None = None,
    *,
    direction: str = "long",
    catalysts: dict | None = None,
    equity: float | None = None,
    risk_pct: float | None = None,
    segment: str = "delivery",
    slippage: float = 0.05,
    min_score: float | None = None,
    now: datetime | None = None,
    th: Thresholds | None = None,
) -> ScanResult:
    """Walk SESSION_CHECKLIST.md steps 1-15 and return the report plus the bars used."""
    now = now or datetime.now(IST)
    th = th or settings.thresholds
    min_score = min_score if min_score is not None else th.min_score
    catalysts = catalysts or {}
    risk_pct = risk_pct if risk_pct is not None else settings.risk_per_trade_pct
    result = ScanResult(report={})

    report: dict = {
        "market_status": market_status(now),
        "provider": {"name": provider.name, "capabilities": provider.capabilities()},
        "credentials_present": settings.credential_status(),
        "candidates": [],
        "rejected": [],
        "errors": [],
        "data_limitations": [],
    }
    result.report = report

    # Steps 4-5: benchmark and regime -------------------------------------
    try:
        bench = provider.get_bars(BENCHMARK, "1d", 300)
        bench.df = completed_daily_bars(bench.df, now)
        result.benchmark = bench
        regime_call = classify_regime(bench.df)
        report["regime"] = {
            "classification": regime_call.regime,
            "evidence": regime_call.evidence,
            "benchmark_close": regime_call.benchmark_close,
            "benchmark_last_bar": str(bench.last_bar_time),
            "benchmark_staleness_days": round(bench.staleness_days(now) or 0, 2),
            "breadth": regime_call.breadth_note,
        }
        bench_close = bench.df["close"]
    except DataUnavailable as exc:
        report["regime"] = {"classification": "unknown", "evidence": [str(exc)]}
        report["data_limitations"].append(f"benchmark unavailable: {exc}")
        regime_call = None
        bench_close = None

    if not provider.supports_options:
        report["data_limitations"].append(
            "No option chain on this provider — the derivatives block (10 pts) is "
            "unscored and coverage is capped accordingly."
        )
    if not provider.supports_intraday:
        report["data_limitations"].append(
            "EOD bars only — intraday structure, gaps within the session and live "
            "quotes are invisible."
        )

    symbols = symbols or DEFAULT_UNIVERSE
    regime = regime_call.regime if regime_call else "unknown"

    # Steps 6-10: filter, analyse, rank ------------------------------------
    for sym in symbols:
        try:
            bars = provider.get_bars(sym, "1d", 300)
        except DataUnavailable as exc:
            report["errors"].append({"symbol": sym, "error": str(exc)})
            continue
        bars.df = completed_daily_bars(bars.df, now)
        result.bars[sym] = bars

        try:
            snap = build_snapshot(bars.df, sym)
        except ValueError as exc:
            report["errors"].append({"symbol": sym, "error": str(exc)})
            continue

        verdict = liquidity_check(snap, th)
        if not verdict.passed:
            report["rejected"].append({"symbol": sym, "reasons": verdict.reasons})
            continue

        staleness = bars.staleness_days(now)
        if staleness is not None and staleness > th.max_bar_staleness_days:
            report["rejected"].append(
                {"symbol": sym, "reasons": [f"data {staleness:.1f} days stale"]}
            )
            continue

        actions = suspect_corporate_actions(bars.df)
        if actions:
            report["rejected"].append({
                "symbol": sym,
                "reasons": [
                    f"suspected unadjusted corporate action {a.date} "
                    f"(open/prev close {a.ratio}, {a.likely}) — resolve before trading"
                    for a in actions
                ],
            })
            continue

        levels = build_levels(snap, th, direction)
        rs = (
            relative_strength(bars.df["close"], bench_close, 60)
            if bench_close is not None
            else None
        )

        cat = catalysts.get(sym, {})
        # Measured from the chart, not read from config. Passing th.target2_r
        # here was the bug that made this component constant for every name.
        rr = levels.reward_risk if levels else None

        score = build_score(
            snap,
            regime=regime,
            direction=direction,
            rel_strength=rs,
            reward_risk=rr,
            options=provider.get_options(sym) if provider.supports_options else None,
            has_catalyst=cat.get("present") if cat else None,
            catalyst_note=cat.get("note", ""),
            catalyst_verified=bool(cat.get("verified", False)),
            staleness_days=staleness,
            min_turnover_cr=th.min_median_turnover_cr,
            min_rr=th.min_reward_risk,
            max_staleness=th.max_bar_staleness_days,
            event_risk=bool(cat.get("binary", False)),
            event_note=cat.get("note", ""),
        )

        sizing = (
            size_position(
                levels,
                equity,
                risk_pct,
                settings.max_single_position_pct,
                snap.median_turnover_cr,
            )
            if levels
            else None
        )

        # Net economics. Without equity there is no real quantity, so the
        # arithmetic runs on a Rs1,00,000 notional and is labelled as such —
        # the ratios and the breakeven win rate hold either way, the rupee
        # figures scale.
        econ = None
        if levels:
            if sizing and sizing.quantity:
                qty, basis = sizing.quantity, "position size from risk budget"
            else:
                qty = int(NOTIONAL / levels.reference_price)
                basis = f"Rs{NOTIONAL:,.0f} notional (no account equity supplied)"
            econ = trade_economics(
                levels.reference_price, levels.stop, levels.target2, qty,
                segment=segment, slippage_pct=slippage,
            )
            if econ:
                econ_d = asdict(econ)
                econ_d["quantity_basis"] = basis
                econ_d["expected_value_by_win_rate"] = {
                    f"{p}%": round(econ.expected_value_at(p), 2)
                    for p in (30, 40, 50, 60, 70)
                }
                econ = econ_d

        report["candidates"].append(
            {
                "symbol": sym,
                "score": round(score.raw, 1),
                "coverage": round(score.coverage, 2),
                "confidence": score.confidence,
                "unscored_components": score.missing,
                "explain": score.explain(),
                "snapshot": asdict(snap),
                "levels": asdict(levels) if levels else None,
                "sizing": asdict(sizing) if sizing else None,
                "economics": econ,
                "data": {
                    "source": bars.source,
                    "freshness": bars.freshness,
                    "last_bar": str(bars.last_bar_time),
                    "staleness_days": round(staleness or 0, 2),
                },
            }
        )

    sched = SCHEDULES[segment]
    report["cost_model"] = {
        "schedule": sched.name,
        "segment": segment,
        "slippage_pct_per_leg": slippage,
        "rates_verified": not sched.unverified(),
        "note": (
            "Statutory rates change and brokerage varies by broker and plan. These "
            "are published-retail defaults, not a contract note. Verify before "
            "trusting any rupee figure."
        ),
    }
    if sched.unverified():
        report["data_limitations"].append(
            "Charge rates are UNVERIFIED defaults — net returns below are indicative."
        )

    report["candidates"].sort(key=lambda c: c["score"], reverse=True)
    report["shortlist"] = [c for c in report["candidates"] if c["score"] >= min_score]

    # Step 15: do not force a trade ---------------------------------------
    if not report["shortlist"]:
        report["decision_hint"] = (
            f"NO TRADE — nothing cleared {min_score:.0f}/100. "
            "PROMPT.md permits and expects this outcome."
        )
    else:
        report["decision_hint"] = (
            f"{len(report['shortlist'])} candidate(s) cleared {min_score:.0f}. "
            "Levels are arithmetic only. Before acting, verify each catalyst against a "
            "primary source, write the bear case, and confirm the names are not "
            "correlated into one bet."
        )
    return result
