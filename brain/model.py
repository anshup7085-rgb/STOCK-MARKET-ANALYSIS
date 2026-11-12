"""
The brain's model, kept simple enough to read: a logistic regression with a small ridge penalty,
fitted again at the start of every month on every answered row before it (walk-forward), so a
forecast never sees its own future. Beside each forecast stands the baseline it must beat: the
plain share of "yes" in the same training rows.

A missing feature is set to its training average, so it neither helps nor hurts.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np

from brain.features import FEATURES

MIN_TRAIN = 500
RIDGE = 1.0


def _matrix(rows: list[dict], mean=None, std=None):
    raw = np.array([[np.nan if r["x"].get(f) is None else r["x"][f] for f in FEATURES] for r in rows], dtype=float)
    if mean is None:
        have = ~np.isnan(raw)
        n = np.maximum(have.sum(axis=0), 1)
        filled = np.where(have, raw, 0.0)
        mean = filled.sum(axis=0) / n                          # 0 for an input that is never there
        std = np.sqrt((np.where(have, raw - mean, 0.0) ** 2).sum(axis=0) / n)
        std = np.where(std == 0, 1.0, std)
    z = (np.where(np.isnan(raw), mean, raw) - mean) / std
    return np.hstack([np.ones((len(rows), 1)), z]), mean, std


def fit(rows: list[dict], target: str) -> dict:
    """Ridge logistic regression by Newton's method. Returns the weights and the scaling."""
    X, mean, std = _matrix(rows)
    y = np.array([r["y"][target] for r in rows], dtype=float)
    w = np.zeros(X.shape[1])
    pen = np.eye(X.shape[1]) * RIDGE
    pen[0, 0] = 0.0
    for _ in range(25):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        step = np.linalg.solve(X.T @ (X * (p * (1 - p))[:, None]) + pen, X.T @ (y - p) - pen @ w)
        w += step
        if np.abs(step).max() < 1e-8:
            break
    return {"w": w, "mean": mean, "std": std, "base": float(y.mean()), "n": len(rows)}


def predict(m: dict, row: dict) -> float:
    X, _, _ = _matrix([row], m["mean"], m["std"])
    return float(1 / (1 + math.exp(-max(-30.0, min(30.0, float(X[0] @ m["w"]))))))


def walk_forward(rows: list[dict], target: str, first: date | None = None, last: date | None = None,
                 may_train=lambda d: True) -> list[dict]:
    """A forecast for every row from `first` to `last` whose answer is known, each from a model fitted
    at the start of its month on the answered rows before that month that `may_train` allows."""
    out, model, month = [], None, None
    for i, r in enumerate(rows):
        d = r["day"]
        if (first and d < first) or (last and d > last) or r["y"][target] is None:
            continue
        if (d.year, d.month) != month:
            month = (d.year, d.month)
            start = date(d.year, d.month, 1)
            train = [t for t in rows[:i] if t["day"] < start and t["y"][target] is not None and may_train(t["day"])]
            model = fit(train, target) if len(train) >= MIN_TRAIN else None
        if model is None:
            continue
        out.append({"day": d.isoformat(), "p": round(predict(model, r), 4), "base": round(model["base"], 4),
                    "y": r["y"][target]})
    return out


def score(preds: list[dict]) -> dict:
    """Brier scores of the model and of the baseline, the skill between them, and how sure it is.
    skill = 1 - model's Brier / baseline's: above 0 the model beats the plain base rate. z is the
    average daily gain over the baseline against its own scatter (days treated as independent)."""
    if len(preds) < 2:
        return {"n": len(preds)}
    gain = [(q["base"] - q["y"]) ** 2 - (q["p"] - q["y"]) ** 2 for q in preds]
    bm = sum((q["p"] - q["y"]) ** 2 for q in preds) / len(preds)
    bb = sum((q["base"] - q["y"]) ** 2 for q in preds) / len(preds)
    mean = sum(gain) / len(gain)
    sd = math.sqrt(sum((g - mean) ** 2 for g in gain) / (len(gain) - 1))
    return {"n": len(preds), "right": sum((q["p"] > 0.5) == bool(q["y"]) for q in preds) / len(preds),
            "yes_rate": sum(q["y"] for q in preds) / len(preds), "brier": round(bm, 5), "brier_base": round(bb, 5),
            "skill": round(1 - bm / bb, 4) if bb > 0 else 0.0,
            "z": round(mean / sd * math.sqrt(len(gain)), 2) if sd > 0 else 0.0}
