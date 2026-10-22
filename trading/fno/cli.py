"""
`python run_trader.py fno ...` — the F&O paper runner for strategy V.

    fno probe    --feed kite|groww          # first morning: what the feed really returns
    fno init     --feed kite --equity 500000
    fno run      --feed kite                # each trading morning, after minting the token
    fno pass     --feed kite                # one pass, now
    fno status
    fno evaluate [--json scans/fno_eval.json]
    fno verify
    fno flatten  --feed kite --reason "..." # off-rule exit at the touch; the trade still counts
    fno settle   --expiry YYYY-MM-DD --value 25012.35 --source "NSE closing value, <where>"
    fno demo     [--weeks 8]                # SYNTHETIC wiring check in a temp dir, never the paper book

Paper only. There is no --mode: nothing here can place an order.
Exit codes: 0 ok, 2 refused/fatal, 3 completed with alerts.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path

from market.calendar import load_calendar
from market.config import IST
from market.providers.base import DataUnavailable
from trading.config import TradingConfig
from trading.fno.book import FnoBookStore, RunnerBusy
from trading.fno.engine import MOCK_DIR_SUFFIX, FeedRefused, FnoEngine, record_manual_settlement
from trading.fno.evaluate import evaluate
from trading.fno.strategy_v import REGISTERED
from trading.journal import Journal, verify_file

STATE_NAME = "paper_fno"

# Entry passes every 15 minutes across the window, then settlement passes after the close.
ENTRY_SLOTS = [time(h, m) for h in range(10, 14) for m in (0, 15, 30, 45)] + [time(14, 0)]
SETTLE_SLOTS = [time(16, 0), time(16, 30), time(17, 0)]


def state_dir() -> Path:
    return TradingConfig(mode="paper").state_root / STATE_NAME


def _feed(name: str):
    from trading.fno.feeds import GrowwFnoFeed, KiteFnoFeed
    if name == "kite":
        return KiteFnoFeed()
    if name == "groww":
        return GrowwFnoFeed()
    raise DataUnavailable(f"unknown feed {name!r}: use kite or groww")


def _notifier(sd: Path):
    from trading.alerts import build_alerter
    a = build_alerter("paper", sd, journal=Journal(sd / "journal.jsonl", "fno-paper"))
    a.background = False
    return a


def _engine(args) -> tuple[FnoEngine, object]:
    sd = state_dir()
    alerter = _notifier(sd)
    eng = FnoEngine(_feed(args.feed), sd, notify=lambda m: alerter.send(m, "fno"))
    return eng, alerter


def _finish(rep, alerter) -> int:
    print(rep.text())
    if alerter is not None and (rep.alerts or rep.urgent):
        alerter.notify(rep, kind="fno")
        alerter.wait()
    return 3 if rep.alerts else 0


def cmd_probe(args) -> int:
    feed = _feed(args.feed)
    out = feed.probe(REGISTERED.underlying)
    out["note"] = ("shapes only, no prices; check lot size, strike gaps and expiry weekday against "
                   "PREREGISTRATION_3.md section 3, and that option quotes carry depth and a timestamp")
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_init(args) -> int:
    sd = state_dir()
    if FnoBookStore(sd / "book.json").exists():
        print(f"[refused] {sd / 'book.json'} exists; the paper book is started once", file=sys.stderr)
        return 2
    eng = FnoEngine(_feed(args.feed), sd)
    book = eng.init(args.equity)
    print(f"F&O paper book started at {sd} with Rs{book.starting_equity:,.0f}, feed {book.feed}.")
    print(f"Fee card: {eng.sched.name} (verified_on={eng.sched.verified_on}). Calendar: {eng.cal.status()}")
    return 0


def cmd_pass(args) -> int:
    eng, alerter = _engine(args)
    return _finish(eng.run_pass(switch_reason=args.switch_feed_reason), alerter)


def _next_slot(now: datetime, cal) -> datetime | None:
    if not cal.is_trading_day(now.date()):
        return None
    for t in ENTRY_SLOTS + SETTLE_SLOTS:
        at = datetime.combine(now.date(), t, IST)
        if at > now:
            return at
    return None


def cmd_run(args) -> int:
    """The day's runner: a pass now, then one per slot, until the last settlement slot."""
    eng, alerter = _engine(args)
    today = datetime.now(IST).date().isoformat()
    eng.journal.append("FNO_RUNNER_START", asof=today, feed=eng.feed.key)
    worst = 0
    try:
        while True:
            try:
                rep = eng.run_pass(switch_reason=args.switch_feed_reason)
                worst = max(worst, _finish(rep, alerter))
            except RunnerBusy as exc:
                print(f"[refused] {exc}", file=sys.stderr)
                return 2
            except FeedRefused as exc:
                print(f"[refused] {exc}", file=sys.stderr)
                return 2
            except Exception as exc:          # one bad pass must not end the day's runner
                eng.journal.append("FNO_PASS_ERROR", asof=today, error=repr(exc)[:500])
                print(f"[error] pass failed: {exc!r}", file=sys.stderr)
                worst = max(worst, 3)
            nxt = _next_slot(datetime.now(IST), eng.cal)
            if nxt is None:
                break
            while (left := (nxt - datetime.now(IST)).total_seconds()) > 0:
                _time.sleep(min(left, 30))
    except KeyboardInterrupt:
        pass
    finally:
        eng.journal.append("FNO_RUNNER_STOP", asof=today)
    return worst


def cmd_status(args) -> int:
    sd = state_dir()
    store = FnoBookStore(sd / "book.json")
    if not store.exists():
        print(f"no F&O paper book at {sd}: run `fno init` first")
        return 2
    book = store.load()
    vr = verify_file(sd / "journal.jsonl")
    print(f"F&O paper book ({book.feed}), started {book.created_at[:10]} with Rs{book.starting_equity:,.0f}")
    print(f"  realised Rs{book.realised():+,.2f}; paper equity Rs{book.starting_equity + book.realised():,.2f}")
    print(f"  journal: {'verifies' if vr.ok else 'DOES NOT VERIFY'} ({vr.records} records)")
    if book.halted:
        print(f"  HALTED: {book.halted}")
    for t in book.trades[-8:]:
        legs = " ".join(f"{'-' if g['side'] == 'SELL' else '+'}{g['strike']:g}{g['right']}" for g in t["legs"])
        tail = f"net Rs{t['net']:+,.2f} ({t['R']:+.2f}R)" if t.get("net") is not None else f"max loss Rs{t['max_loss']:,.0f}"
        print(f"  {t['id']} {t['status']:<20} {t['lots']} lot(s) credit {t['credit']:.2f} [{legs}] {tail}")
    return 0 if vr.ok else 2


def cmd_evaluate(args) -> int:
    out = evaluate(state_dir() / "journal.jsonl")
    print(json.dumps(out, indent=2, default=str))
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(out, indent=2, default=str))
    return 0 if out.get("journal_verifies", True) else 2


def cmd_verify(args) -> int:
    vr = verify_file(state_dir() / "journal.jsonl")
    print(f"{'OK' if vr.ok else 'BROKEN'}: {vr.records} records")
    for p in vr.problems[:20]:
        print(f"  {p}")
    return 0 if vr.ok else 2


def cmd_flatten(args) -> int:
    eng, alerter = _engine(args)
    return _finish(eng.flatten(args.reason), alerter)


def cmd_settle(args) -> int:
    try:
        record_manual_settlement(state_dir(), args.expiry, args.value, args.source)
    except ValueError as exc:
        print(f"[refused] {exc}", file=sys.stderr)
        return 2
    print(f"recorded {args.value} for {args.expiry}; the next pass settles it")
    return 0


def cmd_demo(args) -> int:
    """SYNTHETIC: drive the engine through simulated weeks to show the wiring. Never the paper book."""
    from trading.fno.feeds import MockFnoFeed
    root = Path(args.state) if args.state else Path(tempfile.mkdtemp(prefix="fno_demo_"))
    sd = root / f"demo{MOCK_DIR_SUFFIX}"
    cal = load_calendar()
    clock = {"now": datetime.combine(date.fromisoformat(args.start), time(9, 0), IST)}
    feed = MockFnoFeed(lambda: clock["now"], seed=args.seed, calendar=cal)
    eng = FnoEngine(feed, sd, calendar=cal, second_close=lambda u, d: feed.index_close(u, d),
                    now_fn=lambda: clock["now"])
    eng.init(args.equity)
    end = clock["now"].date() + timedelta(weeks=args.weeks)
    d = clock["now"].date()
    while d <= end:
        if cal.is_trading_day(d):
            for t in ENTRY_SLOTS + SETTLE_SLOTS:
                clock["now"] = datetime.combine(d, t, IST)
                rep = eng.run_pass()
                for a in rep.actions:
                    print(f"{clock['now']:%Y-%m-%d %H:%M}  {a}")
        d += timedelta(days=1)
    out = evaluate(sd / "journal.jsonl")
    print(f"\nSYNTHETIC DEMO — not market data, not evidence. State: {sd}")
    print(json.dumps({k: out.get(k) for k in ("settled", "verdict", "wealth_K", "descriptive",
                                              "passes_without_trade_by_reason")}, indent=2, default=str))
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("fno", help="F&O paper trading of strategy V (research/PREREGISTRATION_3.md)")
    fs = p.add_subparsers(dest="fno_cmd", required=True)

    def feed(q, required=True):
        q.add_argument("--feed", choices=("kite", "groww"), required=required)

    q = fs.add_parser("probe"); feed(q); q.set_defaults(fn=cmd_probe)
    q = fs.add_parser("init"); feed(q)
    q.add_argument("--equity", type=float, default=500_000.0)
    q.set_defaults(fn=cmd_init)
    for name, fn in (("pass", cmd_pass), ("run", cmd_run)):
        q = fs.add_parser(name); feed(q)
        q.add_argument("--switch-feed-reason", default=None,
                       help="only to move the book to a different feed; the reason is journaled")
        q.set_defaults(fn=fn)
    q = fs.add_parser("status"); q.set_defaults(fn=cmd_status)
    q = fs.add_parser("evaluate"); q.add_argument("--json", dest="json_out", default=None)
    q.set_defaults(fn=cmd_evaluate)
    q = fs.add_parser("verify"); q.set_defaults(fn=cmd_verify)
    q = fs.add_parser("flatten"); feed(q); q.add_argument("--reason", required=True)
    q.set_defaults(fn=cmd_flatten)
    q = fs.add_parser("settle")
    q.add_argument("--expiry", required=True)
    q.add_argument("--value", type=float, required=True)
    q.add_argument("--source", required=True, help="where NSE's closing value was read")
    q.set_defaults(fn=cmd_settle)
    q = fs.add_parser("demo", help="SYNTHETIC wiring check; writes only to a temp dir")
    q.add_argument("--weeks", type=int, default=8)
    q.add_argument("--start", default="2026-11-02")
    q.add_argument("--equity", type=float, default=500_000.0)
    q.add_argument("--seed", type=int, default=7)
    q.add_argument("--state", default=None, help="parent directory for the demo state (default: a new temp dir)")
    q.set_defaults(fn=cmd_demo)


def dispatch(args) -> int:
    try:
        return args.fn(args)
    except (DataUnavailable, FeedRefused, RunnerBusy, FileNotFoundError) as exc:
        print(f"[refused] {exc}", file=sys.stderr)
        return 2
