"""
Round-2 data: calendar, genuine rows, corporate actions, the data audit and the
schedule, exactly as pre-registered (research/PREREGISTRATION_2.md, sections
2-4, with Amendment 1). Raw cached bars only; research.data.load()'s own
adjustment is never applied to round-2 files.
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
END_ROW = pd.Timestamp("2026-10-01")
SIGNAL_LAGS = (*range(10), 12)  # month-ends a signal uses: Faber M_t..M_{t-9}, T12 M_{t-12}


class NotEvaluable(Exception):
    """A pre-registered stop (sections 3-4): T is not evaluable."""


class NotTestable(NotEvaluable):
    """A section-2 stop: T is not testable."""


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


# -- loading: Amendment 1, section 3 step 0 ------------------------------------------

def read_raw(symbol: str, cache: Path = CACHE_R2) -> tuple[pd.DataFrame, list[dict]]:
    """Cached bars as fetched; rows sharing a date reduced to the last in file order."""
    df = pd.read_csv(cache / data._name(symbol), index_col="date", parse_dates=["date"]).astype(float)
    dup = df.index.duplicated(keep="last")
    dropped = [{"date": str(d.date()), **{c: float(v) for c, v in row.items()}}
               for d, row in df[dup].iterrows()]
    return df[~dup].sort_index(kind="stable"), dropped


def load_raw(cache: Path = CACHE_R2) -> tuple[dict, pd.DataFrame, dict]:
    dups = {}
    raw = {}
    for k in ETFS:
        raw[k], dups[k] = read_raw(SYMBOLS[k], cache)
    nsei, dups["NSEI"] = read_raw(SYMBOLS["NSEI"], cache)
    return raw, nsei, dups


# -- section 3 ---------------------------------------------------------------------

def calendar(nsei: pd.DataFrame) -> tuple[pd.DatetimeIndex, dict]:
    """
    ^NSEI dates, Monday to Friday, less placeholder rows. Amendment 1: the
    placeholder test runs on ^NSEI as fetched, weekend rows included; weekend
    rows are dropped after it.
    """
    flagged = set(data.placeholder_rows(nsei))
    weekday = nsei.index[nsei.index.dayofweek < 5]
    sessions = pd.DatetimeIndex([d for d in weekday if d not in flagged])
    alt = set(data.placeholder_rows(nsei[nsei.index.dayofweek < 5]))
    info = {"raw_first": str(nsei.index[0].date()), "raw_last": str(nsei.index[-1].date()),
            "raw_rows": len(nsei), "dropped_weekend": int((nsei.index.dayofweek >= 5).sum()),
            "dropped_placeholder": sorted(str(d.date()) for d in flagged if d.dayofweek < 5),
            "placeholder_if_tested_after_weekend_drop": sorted(str(d.date()) for d in alt),
            "first": str(sessions[0].date()), "last": str(sessions[-1].date()), "sessions": len(sessions)}
    return sessions, info


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
    """
    Amendment 1: every test runs on the recorded closes; no candidate is
    re-detected after an adjustment; confirmed factors apply cumulatively.
    """
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


def build_panel(raw: dict[str, pd.DataFrame], nsei: pd.DataFrame, dups: dict | None = None) -> Panel:
    """Sections 2-3 on raw bars. Never raises: the stops are separate checks."""
    sessions, cal = calendar(nsei)
    audit: dict = {"NSEI": {**cal, "duplicate_dates_dropped": (dups or {}).get("NSEI", [])}}
    nsei_c = nsei["close"].reindex(sessions)
    close = pd.DataFrame(index=sessions, columns=list(ETFS), dtype=float)
    genuine = pd.DataFrame(False, index=sessions, columns=list(ETFS))
    for k in ETFS:
        df = raw[k]
        weekend = df.index.dayofweek >= 5
        off_cal = ~weekend & ~df.index.isin(sessions)
        on_cal = df[df.index.isin(sessions)]
        holder = set(data.placeholder_rows(on_cal))
        is_holder = on_cal.index.isin(list(holder))
        zero_vol = ~is_holder & ~(on_cal["volume"] > 0)
        bad_close = ~is_holder & ~zero_vol & ~(np.isfinite(on_cal["close"]) & (on_cal["close"] > 0))
        good = on_cal[~is_holder & ~zero_vol & ~bad_close]
        adj, events = corporate_actions(good["close"], nsei_c if k == N else None)
        r = adj / adj.shift(1)
        moves = [{"date": str(d.date()), "ratio": float(x),
                  **({"nsei_move": float(nsei_c.loc[d] / nsei_c.loc[adj.index[i - 1]] - 1)} if k == N else {})}
                 for i, (d, x) in enumerate(r.items()) if i and abs(x - 1) > 0.10]
        audit[k] = {"first": str(df.index[0].date()), "last": str(df.index[-1].date()), "rows": len(df),
                    "duplicate_dates_dropped": (dups or {}).get(k, []),
                    "dropped": {"weekend": int(weekend.sum()), "weekday_off_calendar": int(off_cal.sum()),
                                "placeholder": int(is_holder.sum()), "zero_volume": int(zero_vol.sum()),
                                "close_not_positive": int(bad_close.sum())},
                    "genuine_rows": len(good),
                    "corporate_action_candidates": events, "moves_over_10pct": moves}
        close.loc[adj.index, k] = adj.values
        genuine.loc[good.index, k] = True
    return Panel(sessions, close, genuine, nsei_c, audit)


def month_ends(sessions: pd.DatetimeIndex) -> pd.Series:
    """Last calendar session of each calendar month, indexed by (year, month)."""
    s = sessions.to_series()
    return s.groupby([s.index.year, s.index.month]).max()


def _month_shift(y: int, m: int, k: int) -> tuple[int, int]:
    z = y * 12 + (m - 1) + k
    return z // 12, z % 12 + 1


@dataclass
class Schedule:
    M: list[pd.Timestamp]           # M_1..M_201
    X: list[pd.Timestamp]           # X_1..X_201
    deferrals: list[dict]
    month_end: pd.Series            # (year, month) -> session
    hold_month: list[tuple[int, int]]   # (year, month) of holding months 1..200
    failures: list[dict] = field(default_factory=list)

    def M_back(self, t: int, k: int) -> pd.Timestamp:
        """M_{t-k}, for t in 1..201 (month-ends before the window included)."""
        y, m = _month_shift(*FIRST_HOLD, t - 1)          # holding month t
        y, m = _month_shift(y, m, -1 - k)                # month of M_{t-k}
        return self.month_end.loc[(y, m)]


def schedule(panel: Panel, n: int = N_MONTHS, first=FIRST_HOLD, defer_from: int = 1,
             defer_to: int = 5, collect: bool = False) -> Schedule:
    """
    Section 4. X_t is the first session after M_t on which both ETFs have
    genuine rows, within the first five sessions after M_t. G7 reuses this with
    defer_from=3, defer_to=7. With collect=True every failure is listed for the
    audit instead of raising at the first.
    """
    me = month_ends(panel.sessions)
    sessions = panel.sessions
    both = panel.genuine.all(axis=1)
    M, X, deferrals, months, failures = [], [], [], [], []
    for t in range(1, n + 2):
        hy, hm = _month_shift(*first, t - 1)
        y, m = _month_shift(hy, hm, -1)
        if (y, m) not in me.index:
            failures.append({"t": t, "reason": f"no session in {y}-{m:02d}"})
            if not collect:
                raise NotEvaluable(failures[-1]["reason"])
            continue
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
            failures.append({"t": t, "M": str(mt.date()),
                             "reason": f"no common genuine session within {defer_to} sessions after {mt.date()}"})
            if not collect:
                raise NotEvaluable(failures[-1]["reason"])
        M.append(mt)
        X.append(x)
        if t <= n:
            months.append((hy, hm))
    return Schedule(M, X, deferrals, me, months, failures)


def check_testable(raw: dict, nsei: pd.DataFrame, panel: Panel) -> None:
    """Section 2: rows (as fetched) on or after 2026-10-01; a genuine row ending January 2009."""
    for name, df in (("^NSEI", nsei), *((k, raw[k]) for k in ETFS)):
        if df.index.max() < END_ROW:
            raise NotTestable(f"{name}: no row dated on or after {END_ROW.date()}")
    me = month_ends(panel.sessions)
    if (2009, 1) not in me.index:
        raise NotTestable("no session in January 2009")
    i = panel.sessions.get_loc(me.loc[(2009, 1)])
    for k in ETFS:
        if not panel.genuine[k].iloc[max(0, i - 4):i + 1].any():
            raise NotTestable(f"{k}: no genuine row in the five sessions ending {me.loc[(2009, 1)].date()}")


def block_breaches(panel: Panel, x201: pd.Timestamp | None) -> list[dict]:
    """Section 3.5: every post-adjustment ratio above 1.25 or below 0.80, flagged if in the window."""
    me = month_ends(panel.sessions)
    jan09 = me.loc[(2009, 1)] if (2009, 1) in me.index else panel.sessions[0]
    out = []
    for k in ETFS:
        c = panel.close[k].dropna()
        r = c / c.shift(1)
        for d, x in r[(r > 1.25) | (r < 0.80)].items():
            out.append({"etf": k, "date": str(d.date()), "ratio": float(x),
                        "in_window": bool(d >= jan09 and (x201 is None or d <= x201))})
    return out


def stale_month_ends(panel: Panel, sch: Schedule) -> list[dict]:
    """Section 3.6: month-ends a signal uses (t = 1..200) with no genuine row in the five sessions ending there."""
    used = {sch.M_back(t, k) for t in range(1, N_MONTHS + 1) for k in SIGNAL_LAGS}
    out = []
    for mt in sorted(used):
        i = panel.sessions.get_loc(mt)
        for k in ETFS:
            if not panel.genuine[k].iloc[max(0, i - 4):i + 1].any():
                out.append({"etf": k, "M": str(mt.date())})
    return out


def check_stops(panel: Panel, sch: Schedule) -> None:
    """Sections 3.5, 3.6 and 4: raises NotEvaluable on the first stop that applies."""
    if sch.failures:
        raise NotEvaluable(sch.failures[0]["reason"])
    bad = [b for b in block_breaches(panel, sch.X[-1]) if b["in_window"]]
    if bad:
        raise NotEvaluable(f"{bad[0]['etf']}: block rule, ratio {bad[0]['ratio']:.3f} on {bad[0]['date']}")
    st = stale_month_ends(panel, sch)
    if st:
        raise NotEvaluable(f"{st[0]['etf']}: no genuine row in the five sessions ending {st[0]['M']}")


def staleness(panel: Panel, sch: Schedule) -> dict:
    """Section 19 step 5: per FY, share of sessions without a genuine row and of repeated closes."""
    out, stale = {}, False
    s = panel.sessions[(panel.sessions >= sch.X[0]) & (panel.sessions <= sch.X[-1])]
    fy = pd.Series([d.year if d.month >= 4 else d.year - 1 for d in s], index=s)
    for k in ETFS:
        g = panel.genuine[k].reindex(s)
        c = panel.close[k].reindex(s)
        prev_all = panel.close[k].dropna().shift(1)
        rows, flag = {}, False
        for y in sorted(set(fy)):
            m = fy == y
            gk = g[m]
            cg = c[m][gk]
            repeat = float((cg == prev_all.reindex(cg.index)).mean()) if len(cg) else 1.0
            missing = float((~gk).mean())
            rows[f"FY{y}"] = {"missing": missing, "repeated": repeat}
            flag = flag or missing > 0.10 or repeat > 0.10
        out[k] = {"by_fy": rows, "stale": flag}
        stale = stale or flag
    out["stale"] = stale
    return out


def fy_of(d: pd.Timestamp) -> int:
    """Financial year by its starting calendar year (FY2025-26 -> 2025)."""
    return d.year if d.month >= 4 else d.year - 1


def plus_12_months(d: pd.Timestamp) -> pd.Timestamp:
    """Purchase date + 12 calendar months; 29 February maps to 28 February."""
    day = 28 if (d.month == 2 and d.day == 29) else d.day
    return pd.Timestamp(year=d.year + 1, month=d.month, day=day)


def isfinite(x) -> bool:
    return x is not None and math.isfinite(x)
