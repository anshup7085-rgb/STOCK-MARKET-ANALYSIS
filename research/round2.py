"""
Round-2 data: calendar, genuine rows, corporate actions, the data audit and the
schedule, exactly as pre-registered (research/PREREGISTRATION_2.md, sections
2-4). Raw cached bars only; research.data.load()'s own adjustment is never
applied to round-2 files.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

import research.data as data

CACHE_R2 = Path(__file__).resolve().parent / "cache_r2"
MANIFEST_R2 = Path(__file__).resolve().parent / "MANIFEST_r2.sha256"
N, G = "NIFTYBEES", "GOLDBEES"
ETFS = (N, G)
SYMBOLS = {N: "NIFTYBEES.NS", G: "GOLDBEES.NS", "NSEI": "^NSEI"}
FACTORS = (1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 2 / 3, 3 / 4, 4 / 5, 1 / 20, 1 / 50, 1 / 100)
FIRST_HOLD = (2010, 2)          # holding month t = 1
N_MONTHS = 200                  # t = 200 is September 2026
DEV_LAST = 155                  # development: t = 1..155 (to December 2022)


class NotEvaluable(Exception):
    """A pre-registered stop: T is reported not testable / not evaluable."""


@dataclass
class Panel:
    sessions: pd.DatetimeIndex                     # the calendar
    close: pd.DataFrame                            # adjusted close on genuine rows, NaN elsewhere
    genuine: pd.DataFrame                          # bool, sessions x ETFs
    nsei: pd.Series | None = None                  # ^NSEI close on the calendar (checks only)
    audit: dict = field(default_factory=dict)

    @property
    def Q(self) -> pd.DataFrame:
        """Q_k(d): the last genuine (adjusted) close on or before d."""
        return self.close.ffill()

    def with_close(self, close: pd.DataFrame) -> "Panel":
        return Panel(self.sessions, close, self.genuine, self.nsei, self.audit)


# -- section 3 -----------------------------------------------------------------

def calendar(nsei: pd.DataFrame) -> pd.DatetimeIndex:
    """^NSEI rows, Monday to Friday, less Yahoo placeholder rows."""
    weekday = nsei[nsei.index.dayofweek < 5]
    stale = set(data.placeholder_rows(weekday))
    return pd.DatetimeIndex([d for d in weekday.index if d not in stale])


def _candidates(close: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp, float, float]]:
    """(earlier, later, r, f) for consecutive genuine closes near a factor."""
    out = []
    idx = close.index
    for i in range(1, len(close)):
        r = close.iloc[i] / close.iloc[i - 1]
        if abs(r - 1) < 0.18:
            continue
        f = min(FACTORS, key=lambda x: abs(r / x - 1))
        if abs(r / f - 1) <= 0.03:
            out.append((idx[i - 1], idx[i], float(r), f))
    return out


def corporate_actions(close: pd.Series, nsei: pd.Series | None) -> tuple[pd.Series, list[dict]]:
    """Detect on raw closes, confirm by persistence (and ^NSEI for NIFTYBEES), apply."""
    events = []
    adj = close.copy()
    pos = {d: i for i, d in enumerate(close.index)}
    for early, late, r, f in _candidates(close):
        i = pos[late]
        nxt = close.iloc[i + 1:i + 6]
        c = close.iloc[i]
        persist = bool(((nxt > 0.9 * c) & (nxt < 1.111 * c)).all())
        ref_ok, ref_move = True, None
        if nsei is not None:
            ref_move = float(nsei.loc[late] / nsei.loc[early] - 1)
            ref_ok = abs(ref_move) < 0.10
        confirmed = persist and ref_ok
        events.append({"earlier": str(early.date()), "date": str(late.date()), "ratio": r, "factor": f,
                       "persistence": persist, "nsei_move": ref_move, "confirmed": confirmed})
        if confirmed:
            adj.loc[adj.index < late] *= f
    return adj, events


def build_panel(raw: dict[str, pd.DataFrame], nsei: pd.DataFrame) -> Panel:
    """Sections 2-3 on raw bars. Raises NotEvaluable on a pre-registered stop."""
    sessions = calendar(nsei)
    audit: dict = {"calendar": {"first": str(sessions[0].date()), "last": str(sessions[-1].date()),
                                "sessions": len(sessions),
                                "nsei_rows_dropped": int(len(nsei) - len(sessions))}}
    nsei_c = nsei["close"].reindex(sessions)
    close = pd.DataFrame(index=sessions, columns=list(ETFS), dtype=float)
    genuine = pd.DataFrame(False, index=sessions, columns=list(ETFS))
    for k in ETFS:
        df = raw[k]
        on_cal = df[df.index.isin(sessions)]
        good = on_cal[np.isfinite(on_cal["close"]) & (on_cal["close"] > 0) & (on_cal["volume"] > 0)]
        adj, events = corporate_actions(good["close"], nsei_c if k == N else None)
        r = adj / adj.shift(1)
        moves = [{"date": str(d.date()), "ratio": float(x),
                  **({"nsei_move": float(nsei_c.loc[d] / nsei_c.loc[adj.index[i - 1]] - 1)} if k == N else {})}
                 for i, (d, x) in enumerate(r.items()) if i and abs(x - 1) > 0.10]
        audit[k] = {"first": str(df.index[0].date()), "last": str(df.index[-1].date()), "rows": len(df),
                    "dropped_off_calendar": int(len(df) - len(on_cal)),
                    "dropped_not_genuine": int(len(on_cal) - len(good)),
                    "corporate_action_candidates": events, "moves_over_10pct": moves}
        close.loc[adj.index, k] = adj.values
        genuine.loc[good.index, k] = True
    return Panel(sessions, close, genuine, nsei_c, audit)


def month_ends(sessions: pd.DatetimeIndex) -> pd.Series:
    """Last calendar session of each calendar month, indexed by (year, month)."""
    s = sessions.to_series()
    me = s.groupby([s.index.year, s.index.month]).max()
    return me


def _month_shift(y: int, m: int, k: int) -> tuple[int, int]:
    z = y * 12 + (m - 1) + k
    return z // 12, z % 12 + 1


@dataclass
class Schedule:
    M: list[pd.Timestamp]           # M[t] for t = 0..200 means M_{t+1}; M_1..M_201
    X: list[pd.Timestamp]           # X_1..X_201
    deferrals: list[dict]
    month_end: pd.Series            # (year, month) -> session
    hold_month: list[tuple[int, int]]   # (year, month) of holding months 1..200

    def M_back(self, t: int, k: int) -> pd.Timestamp:
        """M_{t-k}, for t in 1..201 (month-ends before the window included)."""
        y, m = _month_shift(*FIRST_HOLD, t - 1)          # holding month t
        y, m = _month_shift(y, m, -1 - k)                # month of M_{t-k}
        return self.month_end.loc[(y, m)]


def schedule(panel: Panel, n: int = N_MONTHS, first=FIRST_HOLD, defer_from: int = 1,
             defer_to: int = 5) -> Schedule:
    """
    Section 4. X_t is the first session after M_t on which both ETFs have
    genuine rows, within the first five sessions after M_t. G7 reuses this with
    defer_from=3, defer_to=7.
    """
    me = month_ends(panel.sessions)
    sessions = panel.sessions
    both = panel.genuine.all(axis=1)
    M, X, deferrals, months = [], [], [], []
    for t in range(1, n + 2):
        hy, hm = _month_shift(*first, t - 1)
        y, m = _month_shift(hy, hm, -1)
        if (y, m) not in me.index:
            raise NotEvaluable(f"no session in {y}-{m:02d}")
        mt = me.loc[(y, m)]
        i = sessions.get_loc(mt)
        x = None
        for j in range(defer_from, defer_to + 1):
            if i + j < len(sessions) and both.iloc[i + j]:
                x = sessions[i + j]
                if j > defer_from:
                    deferrals.append({"t": t, "M": str(mt.date()), "X": str(x.date()), "sessions_after_M": j})
                break
        if x is None:
            raise NotEvaluable(f"no common genuine session within {defer_to} sessions after {mt.date()}")
        M.append(mt)
        X.append(x)
        if t <= n:
            months.append((hy, hm))
    return Schedule(M, X, deferrals, me, months)


def check_stops(panel: Panel, sch: Schedule) -> None:
    """Sections 2, 3.5 and 3.6: stop conditions, the block, month-end staleness."""
    sessions = panel.sessions
    jan09 = sch.month_end.loc[(2009, 1)]
    for k in ETFS:
        i = sessions.get_loc(jan09)
        if not panel.genuine[k].iloc[max(0, i - 4):i + 1].any():
            raise NotEvaluable(f"{k}: no genuine row in the five sessions ending {jan09.date()}")
    end = pd.Timestamp("2026-10-01")
    if sessions[-1] < end or panel.genuine.loc[panel.genuine.index >= end].sum().min() == 0:
        raise NotEvaluable("a series has no row dated on or after 2026-10-01")
    x201 = sch.X[-1]
    for k in ETFS:
        c = panel.close[k].dropna()
        r = c / c.shift(1)
        win = r[(r.index >= jan09) & (r.index <= x201)]
        bad = win[(win > 1.25) | (win < 0.80)]
        if len(bad):
            raise NotEvaluable(f"{k}: block rule, ratio {bad.iloc[0]:.3f} on {bad.index[0].date()}")
    used = {sch.M_back(t, k) for t in range(1, len(sch.X) + 1) for k in range(0, 13)}
    for mt in sorted(used):
        i = sessions.get_loc(mt)
        for k in ETFS:
            if not panel.genuine[k].iloc[max(0, i - 4):i + 1].any():
                raise NotEvaluable(f"{k}: no genuine row in the five sessions ending {mt.date()}")


def staleness(panel: Panel, sch: Schedule) -> dict:
    """Section 19 step 5: per FY, share of sessions without a genuine row and of repeated closes."""
    out, stale = {}, False
    s = panel.sessions[(panel.sessions >= sch.X[0]) & (panel.sessions <= sch.X[-1])]
    for k in ETFS:
        g = panel.genuine[k].reindex(s)
        c = panel.close[k].reindex(s)
        fy = pd.Series([d.year if d.month >= 4 else d.year - 1 for d in s], index=s)
        rows = {}
        for y in sorted(set(fy)):
            m = fy == y
            gk = g[m]
            cg = c[m][gk]
            prev = panel.close[k].dropna()
            prev = prev.shift(1).reindex(cg.index)
            repeat = float((cg == prev).mean()) if len(cg) else 1.0
            missing = float((~gk).mean())
            rows[f"FY{y}"] = {"missing": missing, "repeated": repeat}
            if missing > 0.10 or repeat > 0.10:
                stale = True
        out[k] = rows
    out["stale"] = stale
    return out


def load_raw(cache: Path = CACHE_R2) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    raw = {k: data.load(SYMBOLS[k], adjust=False, cache=cache) for k in ETFS}
    nsei = data.load(SYMBOLS["NSEI"], adjust=False, cache=cache)
    return raw, nsei


def fy_of(d: pd.Timestamp) -> int:
    """Financial year by its starting calendar year (FY2025-26 -> 2025)."""
    return d.year if d.month >= 4 else d.year - 1


def plus_12_months(d: pd.Timestamp) -> pd.Timestamp:
    """Purchase date + 12 calendar months; 29 February maps to 28 February."""
    day = 28 if (d.month == 2 and d.day == 29) else d.day
    return pd.Timestamp(year=d.year + 1, month=d.month, day=day)


def isfinite(x) -> bool:
    return x is not None and math.isfinite(x)
