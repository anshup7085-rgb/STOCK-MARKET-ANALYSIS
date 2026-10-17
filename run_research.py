#!/usr/bin/env python3
"""
Run the pre-registered strategies (research/PREREGISTRATION.md) on the frozen
data cache and print every metric and pass criterion.

    python -m research.data                    # fetch + freeze inputs, once
    python run_research.py all --json scans/research_AB.json
"""

from __future__ import annotations

import argparse
import json
import sys

import research.data as data
from market.universe import DEFAULT_UNIVERSE


def _fmt(v):
    return "    n/a" if v is None else f"{v:+7.1%}"


def show(name: str, res: dict) -> None:
    print(f"\n=== Strategy {name} ===")
    print(f"{'strategy':10s} {'period':12s} {'start':10s} {'CAGR':>7s} {'vol':>7s} {'Sharpe':>6s} {'maxDD':>7s}")
    for r in res["rows"]:
        sh = f"{r['sharpe']:6.2f}" if r.get("sharpe") is not None else "   n/a"
        print(f"{r['strategy']:10s} {r['period']:12s} {r['start']} {_fmt(r['cagr'])} {_fmt(r['vol'])} {sh} {_fmt(r['max_dd'])}")
    for c in res["criteria"]:
        print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['id']}: {c['detail']}")
    print(f"  RESULT: {res['passed']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="pre-registered strategy research")
    ap.add_argument("which", choices=("A", "B", "all"))
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()
    if not data.available():
        print("[fatal] no cached data — run: python -m research.data", file=sys.stderr)
        return 2
    out = {"manifest": data.manifest(), "corporate_action_fixes": {}}
    for f in data.available():
        sym = f[:-4].replace("IDX_", "^").replace("_AND_", "&")
        fixes = data.corporate_action_fixes(data.load(sym, adjust=False))
        if fixes:
            out["corporate_action_fixes"][sym] = fixes
    if args.which in ("A", "all"):
        from research.strategy_a import evaluate as eval_a
        out["A"] = eval_a(data.load(data.INDEX)["close"])
        show("A", out["A"])
    if args.which in ("B", "all"):
        from research.strategy_b import evaluate as eval_b
        out["B"] = eval_b(DEFAULT_UNIVERSE)
        show("B / B+A", out["B"])
    print(f"\ncorporate-action back-adjustments applied: {out['corporate_action_fixes'] or 'none'}")
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(out, fh, indent=2, default=str)
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
