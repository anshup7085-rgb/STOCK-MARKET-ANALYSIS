#!/usr/bin/env python3
"""
Market scan runner — walks SESSION_CHECKLIST.md in order and emits JSON.

Usage
-----
    python run_scan.py                          # full default universe
    python run_scan.py --symbols TCS INFY HAL   # targeted
    python run_scan.py --provider kite          # live intraday + OI
    python run_scan.py --catalysts catalysts.json
    python run_scan.py --json out.json

The output is deliberately machine-readable. Claude Code reads it, applies the
judgement layers PROMPT.md asks for (bear case, invalidation, portfolio
correlation) and renders OUTPUT_TEMPLATE.md. This script does arithmetic;
it does not do narrative, and it never places an order.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, time

from market.config import IST, MARKET_CLOSE, MARKET_OPEN, settings
from market.indicators import build_snapshot, relative_strength
from market.levels import build_levels, size_position
from market.providers import DataUnavailable, get_provider
from market.score import build_score
from market.universe import (
    BENCHMARK,
    DEFAULT_UNIVERSE,
    classify_regime,
    liquidity_check,
)


def market_status(now: datetime) -> dict:
    """Checklist steps 1-3: date/time, exchange, open/closed."""
    is_weekday = now.weekday() < 5
    open_t = time(*MARKET_OPEN)
    close_t = time(*MARKET_CLOSE)
    is_open = is_weekday and open_t <= now.time() <= close_t
    return {
        "timestamp_ist": now.isoformat(),
        "exchange": "NSE",
        "weekday": now.strftime("%A"),
        "session_open": is_open,
        "note": (
            "Live session" if is_open
            else "Outside session hours — the last bar is a closing print, not a live quote. "
                 "NSE holidays are not checked here; verify against the exchange calendar."
        ),
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


def main() -> int:
    ap = argparse.ArgumentParser(description="NSE opportunity scan")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--provider", default=None, help="yfinance | kite")
    ap.add_argument("--direction", default="long", choices=["long", "short"])
    ap.add_argument("--catalysts", default=None, help="path to catalysts JSON")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--min-score", type=float, default=None)
    args = ap.parse_args()

    now = datetime.now(IST)
    th = settings.thresholds
    min_score = args.min_score if args.min_score is not None else th.min_score
    catalysts = load_catalysts(args.catalysts)

    try:
        provider = get_provider(args.provider)
    except DataUnavailable as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        return 2

    report: dict = {
        "market_status": market_status(now),
        "provider": {"name": provider.name, "capabilities": provider.capabilities()},
        "credentials_present": settings.credential_status(),
        "candidates": [],
        "rejected": [],
        "errors": [],
        "data_limitations": [],
    }

    # Steps 4-5: benchmark and regime -------------------------------------
    try:
        bench = provider.get_bars(BENCHMARK, "1d", 300)
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

    symbols = args.symbols or DEFAULT_UNIVERSE
    regime = regime_call.regime if regime_call else "unknown"

    # Steps 6-10: filter, analyse, rank ------------------------------------
    for sym in symbols:
        try:
            bars = provider.get_bars(sym, "1d", 300)
        except DataUnavailable as exc:
            report["errors"].append({"symbol": sym, "error": str(exc)})
            continue

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

        levels = build_levels(snap, th, args.direction)
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
            direction=args.direction,
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
                settings.account_equity,
                settings.risk_per_trade_pct,
                settings.max_single_position_pct,
                snap.median_turnover_cr,
            )
            if levels
            else None
        )

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
                "data": {
                    "source": bars.source,
                    "freshness": bars.freshness,
                    "last_bar": str(bars.last_bar_time),
                    "staleness_days": round(staleness or 0, 2),
                },
            }
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

    out = json.dumps(report, indent=2, default=str)
    if args.json_out:
        with open(args.json_out, "w") as fh:
            fh.write(out)
        print(f"Wrote {args.json_out}: {len(report['candidates'])} analysed, "
              f"{len(report['shortlist'])} shortlisted, "
              f"{len(report['rejected'])} rejected on liquidity/staleness.")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
