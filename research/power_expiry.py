"""
BEFORE A SLOT IS SPENT: could a registered test of the expiry-day straddle on the held-back years
pass, if the effect found in exploration is real? Exploration data only (to 31 Oct 2024).

    PYTHONPATH=. .venv/bin/python research/power_expiry.py

The held-back years (Nov 2024 to Oct 2026) hold about 200 weekly expiry days: NIFTY's and SENSEX's,
the only two indexes left with weekly expiries after SEBI's change of November 2024. The test would
be V's betting statistic at alpha = 0.005 (PASS when its wealth reaches 200). That statistic is valid
only for results bounded below: with its bet capped at 0.5, any trade at or below -2R ends it. Exploration
already holds such a trade (MIDCPNIFTY, 8 Apr 2024: a two-minute fall, a loss about ten times the stop;
INTRADAY_LOG step 28). So three designs are measured, each on the same expiry days:

  A  the straddle alone, R = net / (1.5 x stop + costs); a trade at or below -2R is a FAIL
  B  the straddle with wings bought ten strikes out (the farthest Dhan's data holds), so the loss has
     a floor; R = net / the worst loss, which cannot pass -1
  C  the same winged trade, R measured as in A, each trade's bet capped so its worst case cannot
     end the test (a cap of 1 / its worst R, known at entry)

For each: the trades of all five indexes in exploration (entry 13:00, stop 30% of the credit, 0.5%
half-spread, today's lots and fees), how often R fell below -2, and the chance of a PASS in 200
trades drawn from them at random, if the true effect is what exploration saw, and if it is half.

The reading, fixed before the run (19 Nov 2026): a design is worth a slot only if its chance of a
PASS is at least 50% at the effect exploration saw. If none is, no slot is spent: the effect is
then too small for its tail to be proved on two years, and it is followed forward on paper instead.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from research.explore_expiry import ENTRIES, replay
from research.explore_expiry_more import LOTS, MORE, expiry_days, sessions
from research.explore_straddle import half_spread
from research.fno_stats import LAMBDA_CAP, MIN_VARIANCE, PRIOR_SECOND_MOMENT, THRESHOLD, betting_path
from trading.fno import dhan_data as dd

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "state" / "dhan" / "explore" / "power_expiry.json"
ENTRY = "13:00"
STOP, HALF, WINGS = 0.30, 0.005, 10
N_TRADES, DRAWS, SEED = 200, 2000, 20261119


def capped_path(r, caps) -> np.ndarray:
    """V's betting wealth, with each bet also capped at caps[i] (known before trade i)."""
    r, caps = np.asarray(r, dtype=float), np.asarray(caps, dtype=float)
    k, total, total_sq, out = 1.0, 0.0, PRIOR_SECOND_MOMENT, np.empty(len(r))
    for i, x in enumerate(r):
        n = i + 1
        m = total / n
        v = max(total_sq / n - m * m, MIN_VARIANCE)
        lam = min(LAMBDA_CAP, caps[i], max(0.0, m / (v + m * m)))
        f = 1.0 + lam * x
        k = 0.0 if (k == 0.0 or f <= 0.0) else k * f
        out[i] = k
        total += x
        total_sq += x * x
    return out


def passes(design: str, sample: list[dict]) -> bool:
    if design == "A":
        r = [t["R_a"] for t in sample]
        if min(r) <= -2:
            return False
        return bool((betting_path(r) >= THRESHOLD).any())
    if design == "B":
        return bool((betting_path([t["R_b"] for t in sample]) >= THRESHOLD).any())
    caps = [1.0 / (1.01 * abs(t["R_floor"])) for t in sample]
    return bool((capped_path([t["R_c"] for t in sample], caps) >= THRESHOLD).any())


def shifted(trades: list[dict], frac: float) -> list[dict]:
    """The same trades with the average net cut by `frac` (a smaller true effect), R recomputed."""
    cut = frac * float(np.mean([t["net_a"] for t in trades]))
    cut_w = frac * float(np.mean([t["net_w"] for t in trades]))
    out = []
    for t in trades:
        out.append({**t, "R_a": (t["net_a"] - cut) / t["risk_a"], "R_b": (t["net_w"] - cut_w) / t["risk_w"],
                    "R_c": (t["net_w"] - cut_w) / t["risk_a"]})
    return out


def chance(design: str, trades: list[dict], rng) -> float:
    hits = 0
    for _ in range(DRAWS):
        idx = rng.integers(0, len(trades), N_TRADES)
        hits += passes(design, [trades[i] for i in idx])
    return hits / DRAWS


def one_day(bars: dict, lot: int, name: str, day) -> dict | None:
    """The straddle alone and the winged straddle on one expiry day, or None if either cannot be built."""
    bar = ENTRIES[ENTRY]
    m = bars.get(bar, {})
    k0, k1 = m.get((0, "CALL")), m.get((1, "CALL"))
    if k0 is None or k1 is None or k1.strike <= k0.strike:
        return None
    step = k1.strike - k0.strike
    h = half_spread(HALF)
    a = replay(bars, bar, STOP, "straddle", h, lot=lot)
    w = replay(bars, bar, STOP, "winged", h, lot=lot, wing=WINGS * step)
    if a is None or w is None:
        return None
    return {"index": name, "day": day.isoformat(), "net_a": a["net"], "risk_a": a["risk"], "R_a": a["R"],
            "net_w": w["net"], "risk_w": w["risk"], "R_b": w["R"], "R_c": w["net"] / a["risk"],
            "R_floor": -w["risk"] / a["risk"], "reason_a": a["reason"], "reason_w": w["reason"]}


def nifty_days() -> list:
    from trading.fno import vh
    archive = vh.Archive(vh.RAW)
    chain = set(vh.weekly_chain(archive.expiries()))
    return [d for d in dd.sessions() if dd.EXPLORE_FROM <= d <= dd.EXPLORE_TO and d in chain]


def main() -> int:
    plan = [("NIFTY", dd.ROOT, 65, nifty_days())]
    for name, lot in LOTS.items():
        root = MORE / name
        plan.append((name, root, lot, expiry_days(sessions(root), lambda d, r=root: dd.load_day(d, offsets=(0,), root=r))))
    trades: list[dict] = []
    print(f"BEFORE A SLOT IS SPENT: the power of a held-back test of the expiry-day straddle (entry {ENTRY}, stop "
          f"{STOP:.0%}, half-spread {HALF:.1%}), from exploration trades only; reading fixed before the run")
    for name, root, lot, days in plan:
        mine = []
        for d in days:
            t = one_day(dd.load_day(d, root=root), lot, name, d)
            if t:
                mine.append(t)
        trades += mine
        if mine:
            ra = [t["R_a"] for t in mine]
            print(f"  {name:<10} {len(mine):3d} expiry days: straddle mean R {np.mean(ra):+.3f}, net Rs{sum(t['net_a'] for t in mine):+,.0f}, "
                  f"{sum(r <= -2 for r in ra)} at or below -2R (worst {min(ra):+.2f}); winged net "
                  f"Rs{sum(t['net_w'] for t in mine):+,.0f}, mean R {np.mean([t['R_b'] for t in mine]):+.4f} of its worst loss")
    if len(trades) < 100:
        print("too few trades to measure power")
        return 2
    rng = np.random.default_rng(SEED)
    pools = {"all five indexes": trades, "NIFTY and SENSEX only": [t for t in trades if t["index"] in ("NIFTY", "SENSEX")]}
    result = {}
    names = {"A": "A straddle alone (a trade at -2R or worse fails it)",
             "B": "B winged, R of the worst loss",
             "C": "C winged, R as A, each bet capped by its worst case"}
    for label, pool in pools.items():
        print(f"\nchance of a PASS in {N_TRADES} trades drawn from {label} ({len(pool)} trades), {DRAWS} draws:")
        for design in "ABC":
            full = chance(design, pool, rng)
            half = chance(design, shifted(pool, 0.5), rng)
            result[f"{label} / {design}"] = {"full": full, "half": half}
            print(f"  {names[design]:<52} effect as seen {full:5.1%}; half of it {half:5.1%}")
    best = max(v["full"] for k, v in result.items() if k.startswith("NIFTY and SENSEX"))
    print(f"\nby the reading fixed in advance: {'a slot may be worth it' if best >= 0.5 else 'NO SLOT: follow it forward on paper'}"
          f" (best chance on NIFTY and SENSEX trades {best:.0%}, needed 50%)")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"written": datetime.now().isoformat(timespec="seconds"), "trades": trades,
                               "chances": result}, indent=1))
    print(f"saved: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
