"""
The round-3 statistic (research/PREREGISTRATION_3.md, section 12), shared by the
simulation and by the paper evaluator so both run the same arithmetic.
"""

from __future__ import annotations

import math

import numpy as np

THRESHOLD = 200.0          # 1 / alpha, alpha = 0.005
LAMBDA_CAP = 0.5
PRIOR_SECOND_MOMENT = 0.25
MIN_VARIANCE = 1e-6


def bet_sizes(r) -> np.ndarray:
    """lambda_i from R_1..R_{i-1} only, with one pseudo-observation of mean 0, second moment 0.25."""
    r = np.asarray(r, dtype=float)
    lam = np.empty(len(r))
    total, total_sq = 0.0, PRIOR_SECOND_MOMENT
    for i, x in enumerate(r):
        n = i + 1                               # the pseudo-observation plus i real ones
        m = total / n
        v = max(total_sq / n - m * m, MIN_VARIANCE)
        lam[i] = min(LAMBDA_CAP, max(0.0, m / (v + m * m)))
        total += x
        total_sq += x * x
    return lam


def betting_path(r) -> np.ndarray:
    """K_1..K_n. A factor at or below zero sets K to zero for good."""
    r = np.asarray(r, dtype=float)
    factors = 1.0 + bet_sizes(r) * r
    path = np.empty(len(r))
    k = 1.0
    for i, f in enumerate(factors):
        k = 0.0 if (k == 0.0 or f <= 0.0) else k * f
        path[i] = k
    return path


def newey_west_t(x, lags: int | None = None) -> float:
    """One-sample HAC t, Bartlett kernel, L = floor(4 (n/100)^(2/9)). Used only to show why it was rejected."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if lags is None:
        lags = int(math.floor(4 * (n / 100) ** (2 / 9)))
    e = x - x.mean()
    s = float(e @ e) / n
    for lag in range(1, lags + 1):
        s += 2 * (1 - lag / (lags + 1)) * float(e[lag:] @ e[:-lag]) / n
    return 0.0 if s <= 0 else float(x.mean() / math.sqrt(s / n))
