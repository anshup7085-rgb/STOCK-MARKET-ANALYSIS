"""
EXPLORATION, NOT EVIDENCE: does NIFTY's first half hour predict its last half hour? On the exploration
years only (Jan 2015 to Oct 2024), from NIFTY 50 minute candles (tools/index_minutes_download.py).

    PYTHONPATH=. .venv/bin/python research/explore_momentum.py

The idea is "market intraday momentum" (Gao, Han, Li and Zhou, Journal of Financial Economics 2018,
on the S&P 500): the first half hour's return, gap included, predicts the last half hour's in the
same direction. Trade: at the start of the late window, take one NIFTY futures lot in the signal's
direction; close it at the window's end. The grid, all 8 variants reported, fixed before the run:

    signal   gap + first half hour: yesterday's last minute to 09:45 (the paper's definition)
             first half hour only:  today's 09:15 open to 09:45
    window   15:00 to 15:30, the paper's last half hour
             14:45 to 15:15, inside the explorer's 15:15 square-off
    days     every day; or only days whose signal moved 0.5% or more

Levels are minute closes: the candle labelled 09:44 closes at 09:45. The future is the index itself
(no futures minutes exist for these years; the basis moves little in half an hour), with the
half-spread our paper trades met (0.007%, tools/fill_quality.py) on each fill, one lot of 65 and
today's Zerodha fees, futures STT 0.05% included. The reading fixed in advance: a gross effect that
is there in most years but does not clear the cost is not a strategy on futures; it may be one on
options (cheaper per point of exposure, before theta), which Dhan's option minutes can test next.
Only a variant whose neighbours agree counts.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from market.charges import FNO_ZERODHA, futures_order_costs, option_order_costs
from trading.fno import index_minutes as im
from trading.fno.dhan_data import EXPLORE_TO

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "state" / "index_minutes" / "explore_momentum.json"
FROM = date(2015, 1, 1)
LOT = 65
FUT_HALF = 0.00007
SIGNALS = {"gap + first half hour": None, "first half hour only": "09:15"}
WINDOWS = {"15:00-15:30": ("14:59", "15:29"), "14:45-15:15": ("14:44", "15:14")}
BIG = 0.005
HELD_BACK_SESSIONS = 480                 # about Nov 2024 to Oct 2026
Z = 10.6                                 # trades needed by the betting test at alpha 0.005: about Z / s^2


def fut_round_trip(level: float, long: bool) -> float:
    """Rupees: fees on both orders plus the half-spread paid on each, one lot."""
    a, b = ("BUY", "SELL") if long else ("SELL", "BUY")
    fees = futures_order_costs(level, LOT, a, FNO_ZERODHA).total + futures_order_costs(level, LOT, b, FNO_ZERODHA).total
    return fees + 2 * FUT_HALF * level * LOT


def days_of(sessions: dict) -> list[dict]:
    """One row per session with every level the grid needs; sessions missing one are dropped."""
    rows, prev = [], None
    for d, m in sessions.items():
        need = ["09:15", "09:44"] + [x for w in WINDOWS.values() for x in w]
        if prev is not None and all(k in m for k in need) and "15:29" in prev:
            rows.append({"day": d, "prev": prev["15:29"].close, "open": m["09:15"].open, "at945": m["09:44"].close,
                         **{k: m[k].close for k in need[1:]}})
        prev = m
    return rows


def trades(rows: list[dict], signal: str, window: str, big_only: bool) -> list[dict]:
    a, b = WINDOWS[window]
    out = []
    for r in rows:
        base = r["prev"] if SIGNALS[signal] is None else r["open"]
        s = r["at945"] / base - 1
        if s == 0 or (big_only and abs(s) < BIG):
            continue
        long = s > 0
        move = r[b] / r[a] - 1
        signed = move if long else -move
        net = signed * r[a] * LOT - fut_round_trip(r[a], long)
        out.append({"day": r["day"].isoformat(), "signal": round(s, 6), "entry": round(r[a], 2), "gross_bps": round(signed * 1e4, 3),
                    "net": round(net, 2)})
    return out


def line(ts: list[dict]) -> str:
    if len(ts) < 2:
        return "too few days"
    g = [t["gross_bps"] for t in ts]
    n = [t["net"] for t in ts]
    mg, sg = sum(g) / len(g), math.sqrt(sum((x - sum(g) / len(g)) ** 2 for x in g) / (len(g) - 1))
    mn, sn = sum(n) / len(n), math.sqrt(sum((x - sum(n) / len(n)) ** 2 for x in n) / (len(n) - 1))
    years: dict[str, float] = defaultdict(float)
    for t in ts:
        years[t["day"][:4]] += t["gross_bps"]
    up = sum(1 for v in years.values() if v > 0)
    s = mn / sn if sn else 0.0
    need = f"a test would need about {Z / s ** 2:,.0f} trades" if s > 0 else "no net edge to test"
    return (f"{len(ts)} days, right {sum(x > 0 for x in g) / len(g):.1%}, gross {mg:+.2f} bps a day "
            f"(t {mg / sg * math.sqrt(len(g)) if sg else float('nan'):+.1f}), gross up in {up} of {len(years)} years; "
            f"futures net Rs{sum(n):+,.0f} (Rs{mn:+,.0f} a day); {need}")


def main() -> int:
    sessions = im.load(im.NIFTY, FROM, EXPLORE_TO)
    rows = days_of(sessions)
    if len(rows) < 100:
        print("too few NIFTY minute sessions: let `morning` run tools/index_minutes_download.py first")
        return 2
    levels = sorted(r["14:59"] for r in rows)
    mid = levels[len(levels) // 2]
    fut_bps = fut_round_trip(mid, True) / (mid * LOT) * 1e4
    opt = option_order_costs(150, LOT, "BUY", FNO_ZERODHA).total + option_order_costs(150, LOT, "SELL", FNO_ZERODHA).total
    opt_bps = (opt + 2 * 0.002 * 150 * LOT) / (0.5 * LOT * mid) * 1e4
    print(f"EXPLORATION, NOT EVIDENCE: NIFTY intraday momentum, {len(rows)} sessions, {rows[0]['day']} to "
          f"{rows[-1]['day']} ({len(sessions) - len(rows)} dropped for a missing minute); all 8 variants, "
          f"fixed before the run")
    print(f"  cost of a round trip at the median level ({mid:,.0f}), in index bps: one futures lot {fut_bps:.1f}; "
          f"one at-the-money option lot (Rs150, delta 0.5, 0.2% half-spread) {opt_bps:.1f}, before theta")
    for window, (a, b) in WINDOWS.items():
        drift = [(r[b] / r[a] - 1) * 1e4 for r in rows]
        print(f"  window {window}, always long (the drift to beat): {sum(drift) / len(drift):+.2f} bps a day")
    results = {}
    for signal in SIGNALS:
        for window in WINDOWS:
            for big in (False, True):
                name = f"{signal:<22} window {window} {'signal 0.5%+' if big else 'every day   '}"
                results[name] = trades(rows, signal, window, big)
                print(f"  {name}: {line(results[name])}")
    print(f"  (held back, Nov 2024 to Oct 2026: about {HELD_BACK_SESSIONS} sessions)")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"written": datetime.now().isoformat(timespec="seconds"), "trades": results}, indent=1))
    print(f"saved: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
