#!/usr/bin/env python3
"""
Round 3 size and power simulation (research/PREREGISTRATION_3.md, sections 12-13).
Synthetic numbers only: no option price from any market enters this file.

An iron condor's settlement P&L is modelled in units of the index move's standard
deviation: shorts at +-k, wings w further out, index move z drawn from a normal
or a unit-variance Student t. Under the null the credit C is set so the true
mean P&L is zero; R = (C + payoff) / (w - C), so R lies in [-1, C / (w - C)].

    python research/fno_sim.py ttest            # the rejected two-look t-test
    python research/fno_sim.py size             # the betting test under 9 nulls
    python research/fno_sim.py power            # the betting test under 4 edges
"""

from __future__ import annotations

import argparse
import json
import math

import numpy as np

from research.fno_stats import betting_path, newey_west_t

GEOMETRIES = ((0.8, 0.26), (1.2, 0.26), (0.8, 0.5))
THRESHOLD, N_MAX, N_FUTILITY = 200.0, 250, 60


def _draws(rng: np.random.Generator, df: int, n: int) -> np.ndarray:
    if df == 0:
        return rng.standard_normal(n)
    return rng.standard_t(df, n) / math.sqrt(df / (df - 2))


def _payoff(z: np.ndarray, k: float, w: float) -> np.ndarray:
    return -(np.clip(z - k, 0, w) + np.clip(-k - z, 0, w))


def _null_credit(rng: np.random.Generator, df: int, k: float, w: float) -> float:
    return float(-_payoff(_draws(rng, df, 2_000_000), k, w).mean())


def ttest(reps: int, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    out = []
    for df in (0, 6, 4, 3):
        for k, w in GEOMETRIES:
            c = _null_credit(rng, df, k, w)
            m = w - c
            passes = 0
            for _ in range(reps):
                r = (c + _payoff(_draws(rng, df, 150), k, w)) / m
                if newey_west_t(r[:60]) >= 3.0902:
                    passes += 1
                elif r[:60].mean() > 0 and newey_west_t(r) >= 2.6521:
                    passes += 1
            out.append({"df": df, "k": k, "w": w, "reps": reps, "false_pass_rate": passes / reps})
    return out


def _betting_runs(rng, df, k, w, reps, shift=0.0) -> dict:
    c = _null_credit(rng, df, k, w)
    m = w - c
    passes, when = 0, []
    for _ in range(reps):
        r = (c + _payoff(_draws(rng, df, N_MAX), k, w)) / m + shift
        path = betting_path(r)
        hit = np.nonzero(path >= THRESHOLD)[0]
        if len(hit) and not (hit[0] + 1 > N_FUTILITY and r[:N_FUTILITY].mean() <= 0):
            passes += 1
            when.append(int(hit[0]) + 1)
    sd = float(((c + _payoff(_draws(rng, df, 200_000), k, w)) / m).std())
    return {"df": df, "k": k, "w": w, "shift": shift, "reps": reps, "sd_R": round(sd, 3),
            "pass_rate": passes / reps, "median_trades_to_pass": int(np.median(when)) if when else None}


def size(reps: int, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    return [_betting_runs(rng, df, k, w, reps) for df in (0, 4, 3) for k, w in GEOMETRIES]


def power(reps: int, seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    return [_betting_runs(rng, 4, 0.8, 0.26, reps, shift=s) for s in (0.05, 0.10, 0.15, 0.20)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=("ttest", "size", "power"))
    ap.add_argument("--reps", type=int, default=None)
    ap.add_argument("--seed", type=int, default=20261022)
    args = ap.parse_args()
    fn, reps = {"ttest": (ttest, 10_000), "size": (size, 4_000), "power": (power, 1_500)}[args.which]
    rows = fn(args.reps or reps, args.seed)
    print(json.dumps({"check": args.which, "seed": args.seed, "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
