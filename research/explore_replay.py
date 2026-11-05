"""
EXPLORATION, NOT EVIDENCE: the intraday explorer itself (trading/fno/explore.py), run minute by minute
through every session of the exploration years (Oct 2021 to Oct 2024) on Dhan's option minutes.

    deploy/explore_replay.sh        # in the background, about an hour; or, in the foreground:
    PYTHONPATH=. .venv/bin/python research/explore_replay.py [--days N] [--spread 0.002]

The explorer's own code decides everything: triggers, strikes, sizing, risk budgets, the position
and margin limits, stops, targets, the 15:15 square-off, costs. A feed built from Dhan's minutes
stands in for Kite:

- at a pass at time t the feed serves the minute bar that closed at t (the bar labelled t - 1 min);
- an option's bid and ask are its close -/+ a half-spread (0.2% of the price, at least one tick),
  with deep touch quantities; there is no real order book in the data;
- the index is Dhan's spot field, with the day's open, high and low built from the minutes so far;
- the near-month future is the index itself +/- 0.01% (no futures minutes are in the data; the
  basis drifts little within a day);
- the contract master lists the nearest weekly expiry's strikes Dhan carried that day (ATM +/- 10)
  and the month's future; expiry dates come from NSE's own F&O files (state/vh/raw). A rule that
  names a strike outside ATM +/- 10 finds no quote and the explorer skips it, as it would live.

Each session runs in a fresh book, as each live day effectively does (all positions close by 15:15),
twice: with the strategies and limits of config/fno_strategies.example.json, and as the what-if book
(every signal, one unit, no budget, position, margin or loss limit). One lot of 65, today's fees.

Writes state/dhan/explore/replay.json (every trade) and prints, per strategy and book: trades, win
rate, mean R, net, worst stretch, and the split by sessions left to expiry and by year.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

from market.calendar import TradingCalendar
from market.config import IST
from market.providers.base import DataUnavailable
from trading.fno import dhan_data as dd
from trading.fno import vh
from trading.fno.explore import MOCK_SUFFIX, Explorer, load_config
from trading.fno.explore_cli import DEFAULT_CONFIG, whatif_config
from trading.fno.feeds import FnoFeed
from trading.fno.model import ContractMaster, IndexQuote, OptionContract, Quote

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "state" / "dhan" / "explore" / "replay.json"
LOT = 65
DEPTH = 100_000


def tick(x: float, up: bool) -> float:
    n = (Decimal(repr(x)) / Decimal("0.05")).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return float(n * Decimal("0.05"))


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class DhanReplayFeed(FnoFeed):
    """One session of Dhan's minutes, served as Kite would serve them live. SYNTHETIC to the explorer."""
    key, name, is_mock = "dhan-replay", "Dhan expired-options minutes (exploration replay)", True

    def __init__(self, day: date, bars: dict, expiry: date, fut_expiry: date, clock: Clock, half: float):
        self.day, self.bars, self.expiry, self.clock, self.half = day, bars, expiry, clock, half
        strikes = sorted({b.strike for m in bars.values() for b in m.values()})
        cs = [OptionContract(f"NIFTY{expiry:%y%m%d}{int(k)}{r}", "NIFTY", expiry, k, r, LOT, 0.05,
                             freeze_qty=LOT * 27) for k in strikes for r in ("CE", "PE")]
        self.fut = OptionContract(f"NIFTY{fut_expiry:%y%b}FUT".upper(), "NIFTY", fut_expiry, 0.0, "FUT", LOT, 0.05,
                                  freeze_qty=LOT * 27)
        self.master = ContractMaster(cs + [self.fut], self.name, datetime.combine(day, time(9, 0), IST))
        self.spots = {}
        for hm, m in bars.items():
            s = next((b.spot for b in m.values() if b.spot), None)
            if s:
                self.spots[hm] = s

    def _hm(self) -> str:
        return (self.clock() - timedelta(minutes=1)).strftime("%H:%M")

    def contract_master(self, underlying):
        return self.master

    def fno_master(self, underlying):
        return self.master

    def index_quote(self, underlying):
        hm, now = self._hm(), self.clock()
        if hm not in self.spots:
            raise DataUnavailable(f"no minute {hm}")
        seen = [v for k, v in self.spots.items() if k <= hm]
        return IndexQuote(self.spots[hm], now, now, seen[0], max(seen), min(seen))

    def quotes(self, contracts):
        hm, now, out = self._hm(), self.clock(), {}
        for c in contracts:
            if c.right == "FUT":
                s = self.spots.get(hm)
                if s:
                    out[c.symbol] = Quote(c.symbol, tick(s * 0.9999, False), tick(s * 1.0001, True), DEPTH, DEPTH, s,
                                          now, now)
                continue
            b = dd.at_strike(self.bars, hm, c.strike, "CALL" if c.right == "CE" else "PUT")
            if b is None or b.close <= 0:
                continue
            h = max(0.05, self.half * b.close)
            bid = tick(b.close - h, False)
            out[c.symbol] = Quote(c.symbol, bid if bid >= 0.05 else None, tick(b.close + h, True), DEPTH, DEPTH,
                                  b.close, now, now)
        return out

    def index_close(self, underlying, day):
        return None


def monthly_expiry(chain: list[date], day: date) -> date | None:
    """The month's last weekly expiry on or after `day`: the NIFTY monthly, and the futures' expiry."""
    for e in chain:
        if e < day:
            continue
        nxt = [x for x in chain if x > e]
        if not nxt or nxt[0].month != e.month:
            return e
    return None


def run_day(day: date, bars: dict, expiry: date, fut_expiry: date, cfg, half: float) -> list[dict]:
    clock = Clock(datetime.combine(day, time(9, 0), IST))
    feed = DhanReplayFeed(day, bars, expiry, fut_expiry, clock, half)
    tmp = Path(tempfile.mkdtemp(prefix="replay_")) / f"book{MOCK_SUFFIX}"
    try:
        ex = Explorer(feed, tmp, cfg, calendar=TradingCalendar(), now_fn=clock)
        ex.init()
        t = datetime.combine(day, time(9, 0), IST)
        while t.time() <= time(15, 29):
            clock.now = t
            try:
                ex.run_pass(t)
            except Exception as exc:                          # noqa: BLE001 - one bad pass must not end the day
                print(f"  {day} {t:%H:%M}: pass failed: {type(exc).__name__}: {str(exc)[:100]}", flush=True)
            t += timedelta(minutes=1)
        trades, entries = [], {}
        for r in ex.journal.records():
            p = r["payload"]
            if r["type"] == "XP_ENTRY":
                entries[p["id"]] = p
            elif r["type"] == "XP_EXIT" and p["id"] in entries:
                e = entries[p["id"]]
                trades.append({"day": day.isoformat(), "strategy": e["strategy"], "direction": e["direction"],
                               "opened": e["t"][11:16], "closed": p["t"][11:16], "reason": p["reason"],
                               "risk": e["risk"], "net": p["net"], "R": p["R"],
                               "margin_est": e.get("margin_est")})
        return trades
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


def report(trades: list[dict], left: dict[str, int]) -> list[str]:
    lines = []
    by: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by[t["strategy"]].append(t)
    for name in sorted(by):
        ts = by[name]
        nets = [t["net"] for t in ts]
        cum = peak = worst = 0.0
        for n in nets:
            cum += n
            peak = max(peak, cum)
            worst = min(worst, cum - peak)
        wins = sum(n > 0 for n in nets)
        lines.append(f"  {name:<16} {len(ts):4d} trades, won {wins / len(ts):4.0%}, mean R {sum(t['R'] for t in ts) / len(ts):+.3f}, "
                     f"net Rs{sum(nets):+10,.0f}, worst stretch Rs{worst:,.0f}")
        dte: dict[str, float] = defaultdict(float)
        cnt: dict[str, int] = defaultdict(int)
        yrs: dict[str, float] = defaultdict(float)
        for t in ts:
            k = left.get(t["day"])
            key = "?" if k is None else (str(k) if k < 4 else "4+")
            dte[key] += t["net"]
            cnt[key] += 1
            yrs[t["day"][:4]] += t["net"]
        lines.append("      by sessions left to expiry: " + "; ".join(
            f"{k} ({cnt[k]}) Rs{dte[k]:+,.0f}" for k in ("0", "1", "2", "3", "4+", "?") if cnt[k]))
        lines.append("      by year: " + "; ".join(f"{y} Rs{v:+,.0f}" for y, v in sorted(yrs.items())))
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="the explorer replayed on Dhan's minutes (exploration)")
    ap.add_argument("--days", type=int, default=0, help="only the first N sessions (a quick check)")
    ap.add_argument("--spread", type=float, default=0.002, help="half-spread as a fraction of the price")
    args = ap.parse_args()
    raw = json.loads(Path(DEFAULT_CONFIG).read_text())
    books = {"explorer": load_config(raw), "what-if": load_config(whatif_config(raw))}
    archive = vh.Archive(vh.RAW)
    chain = vh.weekly_chain(archive.expiries())
    nse_days = set(archive.days)
    days = [d for d in dd.sessions() if dd.EXPLORE_FROM <= d <= dd.EXPLORE_TO and d in nse_days]
    if args.days:
        days = days[:args.days]
    print(f"EXPLORATION, NOT EVIDENCE: the explorer replayed on {len(days)} sessions, {days[0]} to {days[-1]}, "
          f"half-spread {args.spread:.1%}", flush=True)
    trades: dict[str, list[dict]] = {b: [] for b in books}
    left: dict[str, int] = {}
    for i, d in enumerate(days):
        e = next((x for x in chain if x >= d), None)
        fe = monthly_expiry(chain, d)
        bars = dd.load_day(d)
        if e is None or fe is None or not bars:
            continue
        left[d.isoformat()] = sum(1 for s in archive.days if d < s <= e)
        for name, cfg in books.items():
            trades[name] += run_day(d, bars, e, fe, cfg, args.spread)
        if (i + 1) % 25 == 0:
            print(f"  {datetime.now():%H:%M} {i + 1}/{len(days)} sessions; trades so far: "
                  + ", ".join(f"{b} {len(v)}" for b, v in trades.items()), flush=True)
    lines = []
    for name in books:
        lines.append(f"\n{name} book ({'limits as configured' if name == 'explorer' else 'every signal, 1 unit, no limits'}):")
        lines += report(trades[name], left)
    print("\n".join(lines), flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"written": datetime.now().isoformat(timespec="seconds"), "spread": args.spread,
                               "sessions": len(days), "left_to_expiry": left, "trades": trades,
                               "summary": lines}, indent=1))
    print(f"saved: {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
