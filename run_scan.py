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

The pipeline itself lives in market/scan.py, shared with the trading engine so
that a traded plan is always the plan this script prints.
"""

from __future__ import annotations

import argparse
import json
import sys

from market.config import settings
from market.providers import DataUnavailable, get_provider
from market.scan import load_catalysts, market_status, scan  # noqa: F401  (re-exported)


def main() -> int:
    ap = argparse.ArgumentParser(description="NSE opportunity scan")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--provider", default=None, help="yfinance | groww | kite | mock")
    ap.add_argument("--direction", default="long", choices=["long", "short"])
    ap.add_argument("--catalysts", default=None, help="path to catalysts JSON")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--min-score", type=float, default=None)
    ap.add_argument("--equity", type=float, default=None,
                    help="account equity; overrides ACCOUNT_EQUITY for this run")
    ap.add_argument("--segment", default="delivery", choices=["delivery", "intraday"],
                    help="charge schedule to apply")
    ap.add_argument("--slippage", type=float, default=0.05,
                    help="assumed slippage %% per leg, applied against you")
    args = ap.parse_args()

    try:
        provider = get_provider(args.provider)
    except DataUnavailable as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        return 2

    report = scan(
        provider,
        args.symbols,
        direction=args.direction,
        catalysts=load_catalysts(args.catalysts),
        equity=args.equity if args.equity is not None else settings.account_equity,
        segment=args.segment,
        slippage=args.slippage,
        min_score=args.min_score,
    ).report

    out = json.dumps(report, indent=2, default=str)
    if args.json_out:
        with open(args.json_out, "w") as fh:
            fh.write(out)
        print(f"Wrote {args.json_out}: {len(report['candidates'])} analysed, "
              f"{len(report['shortlist'])} shortlisted, "
              f"{len(report['rejected'])} rejected on liquidity/staleness/corporate action.")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
