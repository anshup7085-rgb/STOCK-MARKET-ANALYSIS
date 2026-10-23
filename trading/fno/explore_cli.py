"""
`python run_trader.py fno explore ...` — the intraday F&O explorer (trading/fno/explore.py).

    fno explore library                          # the strategy templates and what each one is
    fno explore payoff "FUT+ CE-@ATM+2"          # a structure's legs, risk and break-evens (mock prices by default)
    fno explore payoff "CE-@ATM PE-@ATM" --feed kite
    fno explore demo  [--days 5]                 # SYNTHETIC day-by-day run in a temp dir: wiring only
    fno explore init  --feed kite                # once: starts the paper book in state/fno_explore
    fno explore run   --feed kite                # each trading day from 09:10: one pass a minute to 15:30
    fno explore pass  --feed kite                # one pass now
    fno explore status | report [--json F] | verify

Paper only: nothing here can place an order. EXPLORATION, NOT EVIDENCE.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path

from market.calendar import load_calendar
from market.charges import FNO_SCHEDULES, FNO_ZERODHA
from market.config import IST
from trading.fno.book import RunnerBusy
from trading.fno.engine import REPO_ROOT
from trading.fno.explore import (MOCK_SUFFIX, Explorer, Refused, build, describe, format_scoreboard, load_config,
                                 resolve, scoreboard)
from trading.fno.library import TEMPLATES
from trading.fno.structures import parse_legs
from trading.journal import Journal, verify_file

# Your own copy (gitignored, so editing it never makes V's checkout dirty) wins over the committed example.
OWN_CONFIG, EXAMPLE_CONFIG = REPO_ROOT / "config" / "fno_strategies.json", REPO_ROOT / "config" / "fno_strategies.example.json"
DEFAULT_CONFIG = OWN_CONFIG if OWN_CONFIG.exists() else EXAMPLE_CONFIG


def state_dir() -> Path:
    return REPO_ROOT / "state" / "fno_explore"


def _feed(name: str, clock=None):
    if name == "mock":
        from trading.fno.feeds import MockIntradayFeed
        return MockIntradayFeed(clock or (lambda: datetime.now(IST)))
    from trading.fno.cli import _feed as real
    return real(name)


def _explorer(args) -> Explorer:
    from trading.alerts import build_alerter
    sd = state_dir()
    alerter = build_alerter("paper", sd, journal=None)
    alerter.background = False
    return Explorer(_feed(args.feed), sd, load_config(args.config), notify=lambda m: alerter.send(m, "fno"))


def cmd_library(args) -> int:
    for name, t in TEMPLATES.items():
        legs = t["legs"] if isinstance(t["legs"], str) else "  |  ".join(f"{k}: {v}" for k, v in t["legs"].items())
        print(f"{name}\n  legs     {legs}\n  trigger  {t['trigger']}  window {t['window'][0]}-{t['window'][1]}"
              f"\n  exit     {t['exit']}\n  {t['about']}\n")
    return 0


def cmd_payoff(args) -> int:
    if args.feed == "mock":
        at = datetime.fromisoformat(args.at).replace(tzinfo=IST) if args.at else None
        if at is None:
            d = datetime.now(IST).date()
            cal = load_calendar()
            while not cal.is_trading_day(d):
                d += timedelta(days=1)
            at = datetime.combine(d, time(10, 0), IST)
        feed = _feed("mock", lambda: at)
        print("SYNTHETIC prices (mock feed): the shape is right, the numbers are not a market's.\n")
    else:
        feed, at = _feed(args.feed), datetime.now(IST)
    specs = parse_legs(args.legs)
    master = feed.fno_master(args.underlying)
    spot = feed.index_quote(args.underlying).value
    pairs, needs, why = resolve(master, specs, spot, at.date(), {}, 15)
    quotes = feed.quotes(list(needs) + [c for _, c in pairs or []])
    if pairs is None:
        pairs, _, why = resolve(master, specs, spot, at.date(), quotes, 15)
        if pairs is None:
            print(f"cannot build {args.legs!r}: {why}")
            return 2
        quotes.update(feed.quotes([c for _, c in pairs if c.symbol not in quotes]))
    missing = [c.symbol for _, c in pairs if not (quotes.get(c.symbol) and quotes[c.symbol].bid and quotes[c.symbol].ask)]
    if missing:
        print(f"no two-sided quote for {missing}")
        return 2
    legs = build(pairs, quotes, args.units)
    card = describe(legs, spot, FNO_SCHEDULES.get(feed.key, FNO_ZERODHA))
    worst = "-" if card["worst_loss_band"] is None else f"Rs{card['worst_loss_band']:,.2f}"
    print(f"{args.legs}  at {at:%Y-%m-%d %H:%M}, index {spot:,.2f}, {args.units} unit(s)\n")
    for x in card["legs"]:
        print(f"  {x}")
    print(f"\n  credit (+) / debit (-)   Rs{card['credit']:,.2f}"
          f"\n  risk                     {'defined' if card['defined'] else 'UNDEFINED: needs a stop'}"
          f"\n  worst loss, +-20% band   {worst}"
          f"\n  round-trip costs         Rs{card['costs_round_trip']:,.2f}"
          f"\n  break-evens (expiry)     {card['breakevens']}"
          f"\n  margin (estimate)        Rs{card['margin_estimate']:,.0f}\n"
          f"\n  net P&L if the index moves this much and the options sit at expiry value:")
    for k, v in card["net_at_expiry_value"].items():
        print(f"    {k:>6}  Rs{v:>12,.2f}")
    return 0


def cmd_init(args) -> int:
    ex = _explorer(args)
    ex.init()
    print(f"explorer book started in {ex.state_dir} with {len(ex.cfg.strategies)} strategies "
          f"(config sha {ex.cfg.sha[:12]}). EXPLORATION, NOT EVIDENCE.")
    return 0


def cmd_pass(args) -> int:
    print(_explorer(args).run_pass().text())
    return 0


def cmd_run(args) -> int:
    ex = _explorer(args)
    until = time.fromisoformat(args.until)
    print(f"explorer running: one pass every {args.every}s until {until:%H:%M}. Ctrl-C stops it; "
          f"open positions are then closed at the next run's first pass or booked at their last mark.")
    while datetime.now(IST).time() < until:
        try:
            rep = ex.run_pass()
            if rep.entries or rep.exits or rep.alerts:
                print(rep.text(), flush=True)
        except RunnerBusy as exc:
            print(f"[busy] {exc}", file=sys.stderr)
        except Exception as exc:                               # a bad pass must not end the day's management
            print(f"[pass failed] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        now = _time.time()
        _time.sleep(args.every - now % args.every + 2)
    print(format_scoreboard(scoreboard(ex.journal.records())))
    return 0


def cmd_status(args) -> int:
    path = state_dir() / "book.json"
    print(path.read_text() if path.exists() else "no explorer book yet")
    return 0


def cmd_report(args) -> int:
    path = Path(args.state) / "journal.jsonl" if args.state else state_dir() / "journal.jsonl"
    sb = scoreboard(Journal(path, "fno-explore").records())
    print(format_scoreboard(sb))
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(sb, indent=1, default=str))
    return 0


def cmd_verify(args) -> int:
    vr = verify_file(state_dir() / "journal.jsonl")
    print(f"journal {'verifies' if vr.ok else 'DOES NOT VERIFY'}: {vr.records} records" +
          "".join(f"\n  {p}" for p in vr.problems[:10]))
    return 0 if vr.ok else 2


def cmd_demo(args) -> int:
    from trading.fno.feeds import MockIntradayFeed
    root = Path(args.state) if args.state else Path(tempfile.mkdtemp(prefix="fno_explore_demo_"))
    sd = root / f"explore{MOCK_SUFFIX}"
    clock = {"now": datetime.combine(date.fromisoformat(args.start), time(9, 0), IST)}
    feed = MockIntradayFeed(lambda: clock["now"], seed=args.seed)
    ex = Explorer(feed, sd, load_config(args.config), now_fn=lambda: clock["now"])
    ex.init()
    d, days = date.fromisoformat(args.start), 0
    print("SYNTHETIC: a seeded random index and Black-76 option prices. This checks the wiring; "
          "no number below says anything about a market.\n")
    while days < args.days:
        if ex.cal.is_trading_day(d):
            days += 1
            t = datetime.combine(d, time(9, 10), IST)
            while t.time() <= time(15, 30):
                clock["now"] = t
                rep = ex.run_pass(t)
                for line in rep.entries + rep.exits:
                    print(f"{t:%Y-%m-%d %H:%M}  {line}")
                t += timedelta(minutes=1)
        d += timedelta(days=1)
    print("\n" + format_scoreboard(scoreboard(ex.journal.records())))
    print(f"\nstate: {sd}")
    return 0


def add_explore(fs) -> None:
    p = fs.add_parser("explore", help="intraday multi-leg F&O paper exploration (not evidence)")
    xs = p.add_subparsers(dest="explore_cmd", required=True)

    def common(q, feeds=("kite", "groww")):
        q.add_argument("--feed", choices=feeds, required=True)
        q.add_argument("--config", default=str(DEFAULT_CONFIG))

    q = xs.add_parser("library"); q.set_defaults(fn=cmd_library)
    q = xs.add_parser("payoff", help="a structure's legs, risk and break-evens")
    q.add_argument("legs", help='e.g. "FUT+ CE-@ATM+2" or "CE-@ATM PE-@ATM CE+@ATM+4 PE+@ATM-4"')
    q.add_argument("--feed", choices=("mock", "kite", "groww"), default="mock")
    q.add_argument("--units", type=int, default=1)
    q.add_argument("--underlying", default="NIFTY")
    q.add_argument("--at", default=None, help="mock only: YYYY-MM-DDTHH:MM")
    q.set_defaults(fn=cmd_payoff)
    q = xs.add_parser("init"); common(q); q.set_defaults(fn=cmd_init)
    q = xs.add_parser("pass"); common(q); q.set_defaults(fn=cmd_pass)
    q = xs.add_parser("run"); common(q)
    q.add_argument("--every", type=int, default=60)
    q.add_argument("--until", default="15:30")
    q.set_defaults(fn=cmd_run)
    q = xs.add_parser("status"); q.set_defaults(fn=cmd_status)
    q = xs.add_parser("report"); q.add_argument("--json", dest="json_out", default=None)
    q.add_argument("--state", default=None, help="another explorer directory (e.g. a demo's)")
    q.set_defaults(fn=cmd_report)
    q = xs.add_parser("verify"); q.set_defaults(fn=cmd_verify)
    q = xs.add_parser("demo", help="SYNTHETIC wiring check; writes only to a temp dir")
    q.add_argument("--days", type=int, default=5)
    q.add_argument("--start", default="2026-11-02")
    q.add_argument("--seed", type=int, default=7)
    q.add_argument("--config", default=str(DEFAULT_CONFIG))
    q.add_argument("--state", default=None)
    q.set_defaults(fn=cmd_demo)


def dispatch(args) -> int:
    try:
        return args.fn(args)
    except (Refused, RunnerBusy, ValueError, FileNotFoundError) as exc:
        print(f"[refused] {exc}", file=sys.stderr)
        return 2
