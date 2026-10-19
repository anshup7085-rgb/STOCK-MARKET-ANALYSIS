#!/usr/bin/env python3
"""
Round 2 (research/PREREGISTRATION_2.md), in the registered order:

    python run_round2.py size-check  --json scans/r2_size.json   # step 2: must pass before any fetch
    python run_round2.py power-check --json scans/r2_power.json  # step 2: reported only
    python run_round2.py fetch                                   # step 4: once; writes MANIFEST_r2.sha256
    python run_round2.py audit       --json scans/r2_audit.json  # step 5: published before the run
    python run_round2.py run         --json scans/r2_run.json    # step 6: once
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

import research.data as data
from research import round2 as r2
from research import strategy_t as st


def _dump(obj, path: str | None) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(obj, fh, indent=2, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
        print(f"wrote {path}")


def size_check(count: int, timing: float = 0.0, label: str = "size") -> dict:
    t0 = time.time()
    rows = []
    for i, p in enumerate(st.Synthetic(timing=timing).panels(count), start=1):
        rows.append(st.p_only(p))
        if i % 250 == 0:
            print(f"  {i}/{count}  passes so far {sum(r['passed'] for r in rows)}  ({time.time() - t0:.0f}s)")
    passes = sum(r["passed"] for r in rows)
    ts = np.array([r["t"] for r in rows])
    out = {"check": label, "datasets": count, "seed": 20261018, "timing_drift_daily": timing,
           "passes": passes, "pass_rate": passes / count, "t_mean": float(ts.mean()), "t_sd": float(ts.std(ddof=1)),
           "ir_mean": float(np.mean([r["ir"] for r in rows])), "seconds": round(time.time() - t0)}
    if label == "size":
        out["required"] = "passes <= 20 (1.0%)"
        out["ok"] = passes <= 20
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="round 2: strategy T")
    ap.add_argument("step", choices=("size-check", "power-check", "fetch", "audit", "run"))
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--count", type=int, default=2000)
    ap.add_argument("--timing", type=float, default=None, help="power check: daily drift shift while in/out")
    args = ap.parse_args()

    if args.step == "size-check":
        out = size_check(args.count)
        print(json.dumps(out, indent=2))
        _dump(out, args.json_out)
        return 0 if out["ok"] else 1

    if args.step == "power-check":
        out = size_check(args.count, timing=args.timing or 0.0, label="power")
        print(json.dumps(out, indent=2))
        _dump(out, args.json_out)
        return 0

    if args.step == "fetch":
        if r2.CACHE_R2.exists() and data.available(r2.CACHE_R2):
            print(f"[refused] {r2.CACHE_R2} already holds the frozen inputs; round 2 fetches once", file=sys.stderr)
            return 2
        errs = data.fetch(list(r2.SYMBOLS.values()), lookback=10_000, cache=r2.CACHE_R2)
        if errs:
            print(f"[fatal] fetch failed: {errs}", file=sys.stderr)
            return 2
        lines = [f"{h}  {n}" for n, h in data.manifest(r2.CACHE_R2).items()]
        r2.MANIFEST_R2.write_text("\n".join(lines) + "\n")
        print(f"froze {len(lines)} files; manifest {r2.MANIFEST_R2}")
        return 0

    have = data.manifest(r2.CACHE_R2)
    want = dict(line.split()[::-1] for line in r2.MANIFEST_R2.read_text().splitlines() if line.strip())
    if have != want:
        print("[refused] round-2 inputs differ from MANIFEST_r2.sha256", file=sys.stderr)
        return 2
    raw, nsei = r2.load_raw()
    try:
        panel = r2.build_panel(raw, nsei)
        sch = r2.schedule(panel)
        r2.check_stops(panel, sch)
        stale = r2.staleness(panel, sch)
        status = "evaluable"
    except r2.NotEvaluable as exc:
        print(f"T is NOT EVALUABLE: {exc}")
        _dump({"status": "not evaluable", "reason": str(exc)}, args.json_out)
        return 0

    if args.step == "audit":
        out = {"status": status, "manifest": have, "audit": panel.audit, "deferrals": sch.deferrals,
               "X1": str(sch.X[0].date()), "X201": str(sch.X[-1].date()), "staleness": stale}
        print(json.dumps({k: v for k, v in out.items() if k != "staleness"}, indent=2, default=str)[:6000])
        print(f"stale flag (switches on G7): {stale['stale']}")
        _dump(out, args.json_out)
        return 0

    from research.round2_report import run_all
    out = run_all(panel, sch, stale["stale"])
    out["manifest"] = have
    _dump(out, args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
