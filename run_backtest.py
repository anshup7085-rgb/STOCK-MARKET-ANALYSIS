#!/usr/bin/env python3
"""
Backtest runner — answers whether the score predicts anything.

Usage
-----
    python run_backtest.py --provider mock          # wiring check, NOT a result
    python run_backtest.py --symbols TCS INFY HAL   # real feed, targeted
    python run_backtest.py --horizon 10 --step 3
    python run_backtest.py --json scans/backtest.json

On the mock provider the answer is meaningless by construction: synthetic
geometric random walks have no structure to detect, so a score near zero
correlation is the CORRECT result there and proves only that the harness is not
manufacturing signal out of noise. Run it against a real feed before drawing any
conclusion about the model.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from market.backtest import format_report, run_backtest
from market.config import settings
from market.providers import DataUnavailable, get_provider
from market.universe import BENCHMARK, DEFAULT_UNIVERSE


def main() -> int:
    ap = argparse.ArgumentParser(description="Walk-forward backtest of the score")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--provider", default=None, help="yfinance | kite | mock")
    ap.add_argument("--direction", default="long", choices=["long", "short"])
    ap.add_argument("--horizon", type=int, default=20, help="bars to hold")
    ap.add_argument("--step", type=int, default=5, help="bars between rebalances")
    ap.add_argument("--warmup", type=int, default=200, help="bars before first trade")
    ap.add_argument("--lookback", type=int, default=800, help="bars to fetch")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    try:
        provider = get_provider(args.provider)
    except DataUnavailable as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        return 2

    symbols = args.symbols or DEFAULT_UNIVERSE
    bars: dict = {}
    errors: list[str] = []

    for sym in symbols:
        try:
            bars[sym] = provider.get_bars(sym, "1d", args.lookback).df
        except DataUnavailable as exc:
            errors.append(f"{sym}: {exc}")

    if not bars:
        print("[fatal] no symbol returned data — nothing to backtest.", file=sys.stderr)
        for e in errors[:5]:
            print(f"  {e}", file=sys.stderr)
        return 2

    benchmark = None
    try:
        benchmark = provider.get_bars(BENCHMARK, "1d", args.lookback).df
    except DataUnavailable as exc:
        errors.append(f"benchmark unavailable: {exc}")

    res = run_backtest(
        bars,
        settings.thresholds,
        benchmark=benchmark,
        horizon=args.horizon,
        step=args.step,
        warmup=args.warmup,
        direction=args.direction,
    )
    if benchmark is None:
        res.notes.append("No benchmark — regime was 'unknown' throughout, so the "
                         "regime deduction never applied.")
    for e in errors:
        res.notes.append(e)
    if provider.name.startswith("MOCK"):
        res.notes.insert(0, "SYNTHETIC DATA — this measures the harness, not the model.")

    print(f"provider: {provider.name}")
    print(f"symbols fetched: {len(bars)}/{len(symbols)}")
    print()
    print(format_report(res))

    if args.json_out:
        payload = {
            "provider": provider.name,
            "horizon": res.horizon,
            "direction": res.direction,
            "n_trades": res.n,
            "expectancy_r": res.expectancy_r,
            "spearman": res.spearman,
            "bands": [asdict(b) for b in res.bands],
            "notes": res.notes,
            "trades": [asdict(t) for t in res.trades],
        }
        with open(args.json_out, "w") as fh:
            json.dump(payload, fh, indent=2, default=str)
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
