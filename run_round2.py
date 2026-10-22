#!/usr/bin/env python3
"""
Round 2 (research/PREREGISTRATION_2.md, with Amendment 1), in the registered order:

    python run_round2.py size-check  --json scans/r2_size.json            # step 2: must pass before any fetch
    python run_round2.py power-check --timing C --json scans/r2_power.json  # step 2: reported only
    python run_round2.py fetch                                            # step 4: once; writes MANIFEST_r2.sha256
    python run_round2.py audit       --json scans/r2_audit.json           # step 5: published before the run
    python run_round2.py run         --json scans/r2_run.json             # step 6: once
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

import research.data as data
from research import round2 as r2
from research import strategy_t as st

STAGING = r2.CACHE_R2.parent / ".cache_r2.staging"


def _dump(obj, path: str | None) -> None:
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(obj, fh, indent=2, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
        print(f"wrote {path}")


def size_check(count: int, timing: float = 0.0, label: str = "size", seed: int = 20261018) -> dict:
    t0 = time.time()
    rows = []
    for i, p in enumerate(st.Synthetic(timing=timing, seed=seed).panels(count), start=1):
        rows.append(st.p_only(p))
        if i % 250 == 0:
            print(f"  {i}/{count}  passes so far {sum(r['passed'] for r in rows)}  ({time.time() - t0:.0f}s)")
    passes = sum(r["passed"] for r in rows)
    ts = np.array([r["t"] for r in rows])
    out = {"check": label, "datasets": count, "seed": seed, "timing_drift_daily": timing,
           "passes": passes, "pass_rate": passes / count, "t_mean": float(ts.mean()), "t_sd": float(ts.std(ddof=1)),
           "ir_mean": float(np.mean([r["ir"] for r in rows])), "seconds": round(time.time() - t0)}
    if label == "size":
        out["required"] = "passes <= 20 (1.0%)"
        out["ok"] = passes <= 20
    return out


def fetch() -> int:
    """Amendment 1: all three series or nothing; a failed attempt writes nothing."""
    if r2.CACHE_R2.exists() or r2.MANIFEST_R2.exists():
        print(f"[refused] {r2.CACHE_R2} or {r2.MANIFEST_R2.name} already exists; round 2 fetches once",
              file=sys.stderr)
        return 2
    shutil.rmtree(STAGING, ignore_errors=True)
    try:
        errs = data.fetch(list(r2.SYMBOLS.values()), lookback=10_000, cache=STAGING)
    except Exception as exc:                       # any failure: nothing is frozen
        errs = {"fetch": repr(exc)}
    got = data.available(STAGING) if STAGING.exists() else []
    if errs or len(got) != len(r2.SYMBOLS):
        shutil.rmtree(STAGING, ignore_errors=True)
        print(f"[failed] fetch attempt wrote nothing: {errs or got}", file=sys.stderr)
        return 2
    lines = [f"{h}  {n}" for n, h in data.manifest(STAGING).items()]
    r2.MANIFEST_R2.write_text("\n".join(lines) + "\n")
    os.replace(STAGING, r2.CACHE_R2)
    print(f"froze {len(lines)} files in {r2.CACHE_R2}; manifest {r2.MANIFEST_R2}")
    return 0


def prepare() -> tuple[dict, object, object, object]:
    """Load, build, and run every stop, collecting the audit whatever happens."""
    raw, nsei, dups = r2.load_raw()
    panel = r2.build_panel(raw, nsei, dups)
    out: dict = {"audit": panel.audit, "status": "evaluable"}
    sch = None
    try:
        r2.check_testable(raw, nsei, panel)
    except r2.NotTestable as exc:
        out.update(status="not testable", reason=str(exc))
    sch_all = r2.schedule(panel, collect=True)
    out["deferrals"] = sch_all.deferrals
    out["schedule_failures"] = sch_all.failures
    x201 = sch_all.X[-1] if sch_all.X and sch_all.X[-1] is not None else None
    out["block_check"] = r2.block_breaches(panel, x201)
    if not sch_all.failures:
        sch = sch_all
        out["X1"], out["X201"] = str(sch.X[0].date()), str(sch.X[-1].date())
        out["stale_month_ends"] = r2.stale_month_ends(panel, sch)
        out["staleness"] = r2.staleness(panel, sch)
    if out["status"] == "evaluable":
        try:
            if sch is None:
                raise r2.NotEvaluable(sch_all.failures[0]["reason"])
            r2.check_stops(panel, sch)
        except r2.NotEvaluable as exc:
            out.update(status="not evaluable", reason=str(exc))
    return out, panel, sch, raw


def main() -> int:
    ap = argparse.ArgumentParser(description="round 2: strategy T")
    ap.add_argument("step", choices=("size-check", "power-check", "fetch", "audit", "run"))
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--count", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261018)
    ap.add_argument("--timing", type=float, default=None, help="power check: daily drift shift while in/out")
    args = ap.parse_args()

    if args.step == "size-check":
        out = size_check(args.count, seed=args.seed)
        print(json.dumps(out, indent=2))
        _dump(out, args.json_out)
        return 0 if out["ok"] else 1

    if args.step == "power-check":
        if not args.timing:
            print("[refused] power-check needs --timing (the calibrated constant)", file=sys.stderr)
            return 2
        out = size_check(args.count, timing=args.timing, label="power", seed=args.seed)
        print(json.dumps(out, indent=2))
        _dump(out, args.json_out)
        return 0

    if args.step == "fetch":
        return fetch()

    if not r2.MANIFEST_R2.exists() or not r2.CACHE_R2.exists():
        print("[refused] no frozen round-2 inputs: run `python run_round2.py fetch` first", file=sys.stderr)
        return 2
    have = data.manifest(r2.CACHE_R2)
    want = dict(line.split()[::-1] for line in r2.MANIFEST_R2.read_text().splitlines() if line.strip())
    if have != want:
        print("[refused] round-2 inputs differ from MANIFEST_r2.sha256", file=sys.stderr)
        return 2
    info, panel, sch, _ = prepare()
    info["manifest"] = have

    if args.step == "audit" or info["status"] != "evaluable":
        if info["status"] != "evaluable":
            info["verdict"] = f"FAIL — NO TRADE (T is {info['status']}: {info['reason']})"
            print(info["verdict"])
        if args.step == "audit":
            show = {k: v for k, v in info.items() if k not in ("staleness",)}
            print(json.dumps(show, indent=2, default=str)[:8000])
            if "staleness" in info:
                print(f"stale flag (switches on G7): {info['staleness']['stale']}")
        _dump(info, args.json_out)
        return 0

    from research.round2_report import run_all
    out = run_all(panel, sch, info["staleness"]["stale"])
    out["manifest"] = have
    out["audit_summary"] = {k: info[k] for k in ("X1", "X201", "deferrals", "block_check", "status")}
    print(out["verdict"])
    _dump(out, args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
