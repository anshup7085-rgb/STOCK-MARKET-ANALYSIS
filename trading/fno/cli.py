"""
`python run_trader.py fno ...` — the F&O paper runner for strategy V.

    fno probe    --feed kite|groww          # what the feed really returns; also NSE's settlement file
    fno init     --feed kite                # once, after the registration binds; probes, then starts the book
    fno run      --feed kite                # each trading morning before 10:00, after minting the token
    fno pass     --feed kite                # one settlement-only pass, now
    fno status
    fno evaluate [--json scans/fno_eval.json]
    fno verify
    fno settle   --expiry YYYY-MM-DD --file BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
    fno anchor   [--append]                 # the journal's head, for research/ROUND3_LOG.md (commit and push it)
    fno switch-feed --feed groww --reason "..." --expiries 2026-11-17 2026-11-24
    fno demo     [--weeks 8]                # SYNTHETIC wiring check in a temp dir, never the paper book
    fno explore ...                         # the intraday multi-leg explorer: see trading/fno/explore_cli.py

Paper only. There is no --mode and no order anywhere: nothing here can trade.
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
from trading.fno.book import RunnerBusy
from trading.fno.engine import (ANCHOR_LOG, MOCK_DIR_SUFFIX, REPO_ROOT, FeedRefused, FnoEngine,
                                record_manual_settlement, registration)
from trading.fno.evaluate import ANCHOR_DAYS, anchor_line, anchors, evaluate
from trading.fno.explore import Refused
from trading.fno.strategy_v import REGISTERED, slot_times
from trading.journal import Journal, verify_file

SETTLE_SLOTS = [time(16, 0), time(16, 30), time(17, 0)]
ATTEMPT_OFFSETS_S = (0, 40, 80)            # inside the 120 s slot window (section 5)


def state_dir() -> Path:
    """Section 17: one book, under the repository's own state directory, whatever TRADING_STATE_DIR says."""
    return REPO_ROOT / "state" / "paper_fno"


def _feed(name: str):
    from trading.fno.feeds import GrowwFnoFeed, KiteFnoFeed
    if name == "kite":
        return KiteFnoFeed()
    if name == "groww":
        return GrowwFnoFeed()
    raise DataUnavailable(f"unknown feed {name!r}: use kite or groww")


def _engine(args) -> tuple[FnoEngine, object]:
    from trading.alerts import build_alerter
    sd = state_dir()
    alerter = build_alerter("paper", sd, journal=None)
    alerter.background = False
    eng = FnoEngine(_feed(args.feed), sd, notify=lambda m: alerter.send(m, "fno"))
    return eng, alerter


def _finish(rep, alerter) -> int:
    print(rep.text())
    if alerter is not None and (rep.alerts or rep.urgent):
        alerter.notify(rep, kind="fno")
        alerter.wait()
    return 3 if rep.alerts else 0


def cmd_probe(args) -> int:
    from trading.fno.feeds import nse_fsp
    feed = _feed(args.feed)
    out = feed.probe(REGISTERED.underlying)
    d = datetime.now(IST).date() - timedelta(days=1)
    while d.weekday() != 1:                           # the last Tuesday: a likely NIFTY expiry
        d -= timedelta(days=1)
    value, info = nse_fsp(REGISTERED.underlying, d)
    out["nse_settlement_file"] = {"expiry_tried": d.isoformat(), "derived": value is not None, **info}
    out["registration"] = registration()
    out["note"] = ("shapes only; check lot size, strike gaps, expiry weekday, that option quotes carry depth and a "
                   "timestamp, and whether NSE's settlement file is reachable from this machine")
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_init(args) -> int:
    eng, _ = _engine(args)
    probe = eng.init()
    print(f"F&O paper book started at {eng.state_dir} on {eng.feed.key}: Rs{REGISTERED.starting_equity:,.0f}, "
          f"risk budget Rs{REGISTERED.risk_budget:,.0f} a trade.")
    print(f"Probe: {json.dumps(probe['contracts'])}; lot sizes {probe['lot_sizes']}.")
    print("The first counted cycle is the expiry after the next one. Start `fno run` before 10:00 "
          "on every trading day from now on.")
    return 0


def cmd_pass(args) -> int:
    eng, alerter = _engine(args)
    rep, _ = eng.run_pass()
    return _finish(rep, alerter)


def _sleep_until(at: datetime) -> None:
    while (left := (at - datetime.now(IST)).total_seconds()) > 0:
        _time.sleep(min(left, 20))


def _slot_pass(eng, alerter, day: date, slot: time) -> int:
    """Up to three attempts inside the slot's window; only the last journals a data failure."""
    worst = 0
    start = datetime.combine(day, slot, IST)
    for i, off in enumerate(ATTEMPT_OFFSETS_S):
        at = start + timedelta(seconds=off)
        if datetime.now(IST) >= start + timedelta(seconds=REGISTERED.slot_window_s):
            break
        _sleep_until(at)
        final = i == len(ATTEMPT_OFFSETS_S) - 1
        try:
            rep, status = eng.run_pass(slot=slot, final=final)
        except RunnerBusy:
            _time.sleep(5)
            continue
        worst = max(worst, _finish(rep, alerter))
        if status != "retry":
            break
    return worst


def cmd_run(args) -> int:
    """The day's runner: a settlement pass now, each entry slot, then the settlement slots."""
    eng, alerter = _engine(args)
    if eng.state().init is None:
        print("[refused] no F&O paper book: run `fno init` first", file=sys.stderr)
        return 2
    today = datetime.now(IST).date()
    eng.journal.append("FNO_RUNNER_START", asof=today.isoformat(), feed=eng.feed.key)
    worst = 0
    behind = anchoring_behind()
    if behind:
        msg = (f"V anchoring is behind: records {behind[:5]} have no anchor committed. Run `fno anchor --append`, "
               "then commit and push research/ROUND3_LOG.md. A PASS cannot be reported without it.")
        print(f"[warning] {msg}", file=sys.stderr)
        alerter.send(f"[F&O PAPER] {msg}", "fno")
    try:
        rep, _ = eng.run_pass()
        worst = _finish(rep, alerter)
        if eng.cal.is_trading_day(today):
            for slot in slot_times():
                if datetime.now(IST) >= datetime.combine(today, slot, IST) + timedelta(seconds=REGISTERED.slot_window_s):
                    continue                          # a slot already gone is missed, never made up (5)
                try:
                    worst = max(worst, _slot_pass(eng, alerter, today, slot))
                except Exception as exc:              # one bad pass must not end the day's runner
                    eng.journal.append("FNO_PASS_ERROR", asof=today.isoformat(), feed=eng.feed.key, error=repr(exc)[:500])
                    worst = max(worst, 3)
            for t in SETTLE_SLOTS:
                at = datetime.combine(today, t, IST)
                if datetime.now(IST) > at + timedelta(minutes=20):
                    continue
                _sleep_until(at)
                try:
                    rep, _ = eng.run_pass()
                    worst = max(worst, _finish(rep, alerter))
                except RunnerBusy:
                    pass
                except Exception as exc:
                    eng.journal.append("FNO_PASS_ERROR", asof=today.isoformat(), feed=eng.feed.key, error=repr(exc)[:500])
                    worst = max(worst, 3)
    except KeyboardInterrupt:
        pass
    finally:
        eng.journal.append("FNO_RUNNER_STOP", asof=today.isoformat(), feed=eng.feed.key)
    return worst


def cmd_status(args) -> int:
    from trading.fno.state import State
    sd = state_dir()
    path = sd / "journal.jsonl"
    st = State.from_records(Journal(path, "fno-paper").records())
    if st.init is None:
        print(f"no F&O paper book at {sd}: run `fno init` first")
        return 2
    vr = verify_file(path)
    print(f"V paper book ({st.feed}), started {st.start}, Rs{REGISTERED.starting_equity:,.0f}; "
          f"journal {'verifies' if vr.ok else 'DOES NOT VERIFY'} ({vr.records} records)")
    if st.verdict:
        print(f"  VERDICT: {st.verdict['verdict']}")
    settled = [t for t in st.trades.values() if t["status"] == "SETTLED"]
    print(f"  settled {len(settled)}, net Rs{sum(t['net'] for t in settled):+,.2f}; "
          f"missed cycles {sum(c['outcome'] == 'missed' for c in st.cycles.values())}")
    for t in list(st.trades.values())[-8:]:
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


def cmd_settle(args) -> int:
    try:
        value = record_manual_settlement(state_dir(), args.expiry, Path(args.file))
    except ValueError as exc:
        print(f"[refused] {exc}", file=sys.stderr)
        return 2
    print(f"NSE's file gives {value} for {args.expiry}; the next pass checks it against the close and settles")
    return 0


def cmd_anchor(args) -> int:
    line = anchor_line(state_dir() / "journal.jsonl")
    print(line)
    if args.append:
        with ANCHOR_LOG.open("a") as fh:
            fh.write(f"\n{line}\n")
        print(f"appended to {ANCHOR_LOG}; now commit and push it: "
              "git add research/ROUND3_LOG.md && git commit -m 'V anchor' && git push")
    return 0


def anchoring_behind() -> list[int]:
    """Entries and settlements more than five days old with no anchor in the log at or after them (section 17)."""
    recs = Journal(state_dir() / "journal.jsonl", "fno-paper").records()
    anc = anchors(ANCHOR_LOG, recs)
    limit = datetime.now(IST) - timedelta(days=ANCHOR_DAYS - 2)
    have = [a["seq"] for a in anc["anchors"]]
    return [r["seq"] for r in recs if r["type"] in ("FNO_ENTRY", "FNO_SETTLED")
            and datetime.fromisoformat(r["ts"]) < limit and not any(s >= r["seq"] for s in have)]


def cmd_switch_feed(args) -> int:
    eng = FnoEngine(_feed(args.feed), state_dir())
    eng.switch_feed(args.reason, args.expiries)
    print(f"book now reads {args.feed}")
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
                    fsp_source=lambda u, d: (feed.index_close(u, d), {"source": "mock"}),
                    now_fn=lambda: clock["now"])
    eng.init()
    end = clock["now"].date() + timedelta(weeks=args.weeks)
    d = clock["now"].date()
    while d <= end:
        if cal.is_trading_day(d):
            clock["now"] = datetime.combine(d, time(9, 30), IST)
            eng.run_pass()
            for t in slot_times():
                clock["now"] = datetime.combine(d, t, IST)
                rep, _ = eng.run_pass(slot=t)
                for a in rep.actions:
                    print(f"{clock['now']:%Y-%m-%d %H:%M}  {a}")
            for t in SETTLE_SLOTS:
                clock["now"] = datetime.combine(d, t, IST)
                rep, _ = eng.run_pass()
                for a in rep.actions:
                    print(f"{clock['now']:%Y-%m-%d %H:%M}  {a}")
        d += timedelta(days=1)
    out = evaluate(sd / "journal.jsonl", now=clock["now"], cal=cal)
    print(f"\nSYNTHETIC DEMO — not market data, not evidence. State: {sd}")
    print(json.dumps({k: out.get(k) for k in ("verdict", "items", "cycles", "betting_wealth")}, indent=2, default=str))
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("fno", help="F&O paper trading of strategy V (research/PREREGISTRATION_3.md)")
    fs = p.add_subparsers(dest="fno_cmd", required=True)

    def feed(q):
        q.add_argument("--feed", choices=("kite", "groww"), required=True)

    q = fs.add_parser("probe"); feed(q); q.set_defaults(fn=cmd_probe)
    q = fs.add_parser("init"); feed(q); q.set_defaults(fn=cmd_init)
    q = fs.add_parser("run"); feed(q); q.set_defaults(fn=cmd_run)
    q = fs.add_parser("pass", help="one settlement-only pass now; entries happen only in `run`'s slots")
    feed(q); q.set_defaults(fn=cmd_pass)
    q = fs.add_parser("status"); q.set_defaults(fn=cmd_status)
    q = fs.add_parser("evaluate"); q.add_argument("--json", dest="json_out", default=None)
    q.set_defaults(fn=cmd_evaluate)
    q = fs.add_parser("verify"); q.set_defaults(fn=cmd_verify)
    q = fs.add_parser("settle", help="NSE's F&O bhavcopy for the expiry, downloaded by hand, after the automatic sources failed")
    q.add_argument("--expiry", required=True)
    q.add_argument("--file", required=True, help="the bhavcopy .zip or .csv as downloaded from NSE")
    q.set_defaults(fn=cmd_settle)
    q = fs.add_parser("anchor", help="print the journal's head for research/ROUND3_LOG.md")
    q.add_argument("--append", action="store_true", help="append it to research/ROUND3_LOG.md")
    q.set_defaults(fn=cmd_anchor)
    q = fs.add_parser("switch-feed", help="once, after two adjacent cycles were MISSED by the bound feed's data failures")
    feed(q)
    q.add_argument("--reason", required=True)
    q.add_argument("--expiries", nargs=2, required=True, metavar="YYYY-MM-DD")
    q.set_defaults(fn=cmd_switch_feed)
    q = fs.add_parser("demo", help="SYNTHETIC wiring check; writes only to a temp dir")
    q.add_argument("--weeks", type=int, default=8)
    q.add_argument("--start", default="2026-11-02")
    q.add_argument("--seed", type=int, default=7)
    q.add_argument("--state", default=None, help="parent directory for the demo state (default: a new temp dir)")
    q.set_defaults(fn=cmd_demo)

    from trading.fno.explore_cli import add_explore
    add_explore(fs)


def dispatch(args) -> int:
    try:
        return args.fn(args)
    except (DataUnavailable, FeedRefused, Refused, RunnerBusy, FileNotFoundError, ValueError) as exc:
        print(f"[refused] {exc}", file=sys.stderr)
        return 2
