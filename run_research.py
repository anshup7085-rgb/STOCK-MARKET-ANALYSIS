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
from pathlib import Path

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
    ap.add_argument("--allow-different-inputs", action="store_true",
                    help="run on a cache that does not match research/MANIFEST.sha256 (a new experiment, not a reproduction)")
    args = ap.parse_args()
    if not data.available():
        print("[fatal] no cached data — run: python -m research.data", file=sys.stderr)
        return 2
    differ = data.manifest_mismatches()
    if differ:
        print(f"INPUTS DIFFER FROM THE PRE-REGISTERED RUN ({len(differ)}): " + "; ".join(differ[:5]), file=sys.stderr)
        if not args.allow_different_inputs:
            print("[refused] these results would not be the pre-registered ones; "
                  "pass --allow-different-inputs to run anyway", file=sys.stderr)
            return 2
    out = {"manifest": data.manifest(), "inputs_match_manifest": not differ,
           "corporate_action_fixes": {}, "placeholder_rows": {}}
    for f in data.available():
        sym = f[:-4].replace("IDX_", "^").replace("_AND_", "&")
        raw = data.load(sym, adjust=False)
        fixes = data.corporate_action_fixes(raw)
        if fixes:
            out["corporate_action_fixes"][sym] = fixes
        stale = data.placeholder_rows(raw)
        if len(stale):
            out["placeholder_rows"][sym] = [str(d.date()) for d in stale]
    if args.which in ("A", "all"):
        from research.strategy_a import evaluate as eval_a
        out["A"] = eval_a(data.load(data.INDEX)["close"])
        show("A", out["A"])
    if args.which in ("B", "all"):
        from research.strategy_b import evaluate as eval_b
        out["B"] = eval_b(DEFAULT_UNIVERSE)
        show("B / B+A", out["B"])
    print(f"\ncorporate-action back-adjustments applied: {out['corporate_action_fixes'] or 'none'}")
    n_stale = sum(len(v) for v in out["placeholder_rows"].values())
    if n_stale:
        print(f"Yahoo placeholder rows (zero volume, price = previous close), used as they are: "
              f"{n_stale} in {len(out['placeholder_rows'])} files")
    if "B" in out:
        hits = [(s, d) for s, ds in out["placeholder_rows"].items() for d in ds if d in out["B"]["rebalance_dates"]]
        out["B"]["placeholder_rows_on_rebalance_dates"] = hits
        print(f"  of which on a B month-end or execution date: {hits or 'none'}")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_out, "w") as fh:
            json.dump(out, fh, indent=2, default=str)
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
