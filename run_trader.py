#!/usr/bin/env python3
"""
Trading system CLI. Paper by default; live only through the opt-in in
trading/live.py. Read TRADING.md before using `--mode live`.

    python run_trader.py init    --mode paper --equity 500000
    python run_trader.py cycle   --mode paper                  # once a day, after the close
    python run_trader.py status  --mode paper
    python run_trader.py verify  --mode paper                  # journal integrity
    python run_trader.py gates   --backtest scans/backtest.json
    python run_trader.py replay  --provider mock --equity 500000
    python run_trader.py kill    --reason "..."                # no new entries, anywhere
    python run_trader.py release --reason "..."
    python run_trader.py flatten --mode paper --reason "..."
    python run_trader.py record-exit --trade-id ID --qty N --price P --note "contract note ..."

    # live — see TRADING.md for the daily routine
    python run_trader.py cycle   --mode live --provider kite  --broker kite  [--override-gates "..."]
    python run_trader.py cycle   --mode live --provider groww --broker groww [--override-gates "..."]
    python run_trader.py watch   --mode live --provider kite  --broker kite

Exit codes: 0 ok, 2 refused/fatal, 3 completed with alerts (wire this to a notifier).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time as _time
from datetime import date, datetime, time

from market.config import IST, settings
from market.providers import DataUnavailable, get_provider
from market.scan import load_catalysts, market_status
from market.universe import DEFAULT_UNIVERSE
from trading.book import BookStore
from trading.brokers.base import BrokerError
from trading.brokers.paper import PaperBroker
from trading.config import TradingConfig
from trading.confirm import SimulatedConfirmer
from trading.engine import TradingEngine
from trading.gates import gate1_backtest, gate2_paper, gate3_live
from trading.journal import Journal, verify_file
from trading.live import LiveRefused, authorise_live
from trading.replay import ReplayProvider, replay_summary, run_replay
from trading.risk import KillSwitch


def _cfg(mode: str) -> TradingConfig:
    return TradingConfig(mode=mode)


def _engine(cfg: TradingConfig, args, reduce_only: bool = False) -> TradingEngine:
    journal = Journal(cfg.journal_path, cfg.mode)
    if cfg.is_live:
        if (args.provider or settings.provider or "").strip().lower() in ("mock", "synthetic"):
            raise LiveRefused("live mode refuses the mock provider")
        auth = authorise_live(
            cfg, journal, backtest_json=args.backtest,
            autonomous=args.autonomous, override_reason=args.override_gates,
            reduce_only=reduce_only,
        )
        provider = get_provider(args.provider)
        broker = _live_broker(cfg, args.broker)
        confirmer = auth.confirmer
    else:
        provider = get_provider(args.provider)
        broker = PaperBroker(cfg.state_dir / "paper_broker.json", cfg.slippage_pct)
        confirmer = SimulatedConfirmer()
    return TradingEngine(
        cfg, provider, broker, journal, BookStore(cfg.book_path), confirmer,
        symbols=args.symbols, catalysts=load_catalysts(args.catalysts),
    )


def _live_broker(cfg: TradingConfig, choice: str | None):
    """
    Build the live broker, and hold a live book to the broker its order ids
    came from: a Kite order id means nothing to Groww, and vice versa.
    """
    name = (choice or os.getenv("LIVE_BROKER") or "kite").strip().lower()
    if name not in ("kite", "groww"):
        raise LiveRefused(f"unknown live broker '{name}' — use kite or groww")
    store = BookStore(cfg.book_path)
    book = store.load()
    if book.broker and book.broker != name:
        raise LiveRefused(
            f"this live book trades through {book.broker}; it cannot switch to {name} "
            "with positions and order ids from the other broker. Close it out first, "
            "then start a new live book."
        )
    if name == "groww":
        from trading.brokers.groww import GrowwBroker
        broker = GrowwBroker(live_ack=True, max_orders_per_second=cfg.max_orders_per_second)
        cfg.segment = "groww_delivery"      # Groww's fee card, not Zerodha's
    else:
        from trading.brokers.kite import KiteBroker
        broker = KiteBroker(live_ack=True, max_orders_per_second=cfg.max_orders_per_second)
    if book.broker is None:
        book.broker = name
        store.save(book)
    return broker


# ---------------------------------------------------------------------------

def cmd_init(args) -> int:
    cfg = _cfg(args.mode)
    store = BookStore(cfg.book_path)
    try:
        book = store.create(cfg.mode, args.equity)
    except (FileExistsError, ValueError) as exc:
        print(f"[refused] {exc}", file=sys.stderr)
        return 2
    Journal(cfg.journal_path, cfg.mode).append(
        "INIT", starting_equity=book.starting_equity, config={
            k: str(v) for k, v in vars(cfg).items()
        },
    )
    print(f"Initialised {cfg.mode} book at {cfg.book_path} with Rs{args.equity:,.0f}.")
    if cfg.is_live:
        print("This is the capital the system may use, not your account balance. The "
              "broker's available cash is checked separately before every order.")
    return 0


def cmd_cycle(args) -> int:
    cfg = _cfg(args.mode)
    try:
        eng = _engine(cfg, args)
        rep = eng.run_cycle()
    except (LiveRefused, BrokerError, DataUnavailable, FileNotFoundError) as exc:
        print(f"[refused] {exc}", file=sys.stderr)
        return 2
    print(rep.text())
    return 3 if rep.alerts else 0


def cmd_watch(args) -> int:
    cfg = _cfg(args.mode)
    if not cfg.is_live:
        print("[refused] watch is for live sessions; paper fills are simulated on daily bars.",
              file=sys.stderr)
        return 2
    try:
        eng = _engine(cfg, args, reduce_only=True)
    except (LiveRefused, BrokerError, DataUnavailable, FileNotFoundError) as exc:
        print(f"[refused] {exc}", file=sys.stderr)
        return 2
    alerts = False

    def tick(now) -> None:
        nonlocal alerts
        rep = eng.watch_once(now)
        if rep.actions or rep.alerts:
            print(rep.text())
            alerts = alerts or bool(rep.alerts)

    print(f"watching every {args.interval}s while the session is open (Ctrl-C to stop)")
    while True:
        now = datetime.now(IST)
        if market_status(now)["session_open"]:
            tick(now)
            _time.sleep(args.interval)
            continue
        if now.weekday() < 5 and now.time() < time(9, 15):
            _time.sleep(min(args.interval, 60))        # started early: wait for the open
            continue
        # After the close. Kite's order book only lasts the day: an entry that
        # filled or a DAY order that died is readable today and not tomorrow.
        # One last pass once the exchange has settled DAY orders books them.
        if time(15, 30) <= now.time() < time(16, 0):
            settle = now.replace(hour=15, minute=40, second=0, microsecond=0)
            if now < settle:
                print("session closed — one settling pass at 15:40")
                _time.sleep((settle - now).total_seconds())
            tick(datetime.now(IST))
        print("watch ends")
        break
    return 3 if alerts else 0


def cmd_status(args) -> int:
    cfg = _cfg(args.mode)
    try:
        book = BookStore(cfg.book_path).load()
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2
    v = verify_file(cfg.journal_path)
    kill = KillSwitch(cfg.kill_paths).engaged()
    closed = book.closed()
    net_r = [t.net_r_multiple for t in closed if t.net_r_multiple is not None]
    print(f"{cfg.mode.upper()} book — started {book.created_at[:10]} with Rs{book.starting_equity:,.0f}")
    print(f"  equity Rs{book.equity():,.0f} | realised net Rs{book.realised_net:,.0f} | "
          f"unrealised Rs{book.unrealised():,.0f} | open risk Rs{book.open_risk():,.0f}")
    print(f"  peak Rs{book.peak_equity:,.0f} | uncommitted capital Rs{book.available_capital():,.0f}")
    print(f"  kill switch: {kill or 'off'}")
    print(f"  journal: {v.records} records, {'intact' if v.ok else 'BROKEN — ' + '; '.join(v.problems[:2])}")
    if net_r:
        wins = sum(1 for x in net_r if x > 0)
        print(f"  closed: {len(closed)} trades, {wins / len(net_r):.0%} winners, "
              f"{sum(net_r) / len(net_r):+.3f}R net per trade")
    active = book.active()
    if active:
        print("  active:")
        for t in active:
            mark = book.marks.get(t.symbol)
            print(f"    {t.trade_id:<28s} {t.status:<13s} {t.filled_qty or t.qty:>5d} "
                  f"entry {t.entry_price or t.entry_limit} stop {t.stop} target {t.target} "
                  f"mark {mark} held {t.bars_held}/{t.max_hold_bars} protection {t.protection_state}")
    return 0


def cmd_verify(args) -> int:
    cfg = _cfg(args.mode)
    v = verify_file(cfg.journal_path)
    print(f"{cfg.journal_path}: {v.records} records — {'INTACT' if v.ok else 'BROKEN'}")
    for p in v.problems:
        print(f"  {p}")
    return 0 if v.ok else 2


def cmd_gates(args) -> int:
    cfg = _cfg("live")
    reps = [
        gate1_backtest(args.backtest),
        gate2_paper(cfg.state_root / "paper" / "journal.jsonl"),
        gate3_live(cfg.state_root / "live" / "journal.jsonl",
                   cfg.state_root / "paper" / "journal.jsonl", cfg.max_drawdown_pct),
    ]
    for r in reps:
        print(r.text())
        if r.stats:
            print("  stats: " + json.dumps(r.stats))
        print()
    return 0


def cmd_kill(args) -> int:
    cfg = _cfg(args.mode or "paper")
    paths = [cfg.state_dir / "KILL"] if args.mode else [cfg.state_root / "KILL"]
    p = KillSwitch(paths).engage(f"manual: {args.reason}")
    for mode in ([args.mode] if args.mode else ["paper", "live"]):
        c = _cfg(mode)
        if c.journal_path.exists():
            Journal(c.journal_path, mode).append("KILL_SWITCH_ENGAGED", reason=f"manual: {args.reason}", path=str(p))
    print(f"Kill switch engaged: {p}. No new entries. Protective orders are untouched.")
    return 0


def cmd_release(args) -> int:
    gone = []
    for mode in ("paper", "live"):
        c = _cfg(mode)
        gone += KillSwitch(c.kill_paths).release()
        if c.journal_path.exists():
            Journal(c.journal_path, mode).append("KILL_SWITCH_RELEASED", reason=args.reason)
    print("Released: " + (", ".join(str(p) for p in dict.fromkeys(gone)) or "nothing was engaged"))
    return 0


def cmd_record_exit(args) -> int:
    """Book a fill the system could not read from the broker. Sends nothing."""
    cfg = _cfg(args.mode)
    try:
        eng = TradingEngine(cfg, None, None, Journal(cfg.journal_path, cfg.mode),
                            BookStore(cfg.book_path), SimulatedConfirmer())
        rep = eng.record_manual_exit(args.trade_id, args.qty, args.price, args.reason,
                                     args.note, at=args.date)
    except (ValueError, FileNotFoundError) as exc:
        print(f"[refused] {exc}", file=sys.stderr)
        return 2
    print(rep.text())
    return 0


def cmd_flatten(args) -> int:
    cfg = _cfg(args.mode)
    if cfg.is_live:
        typed = input("This exits EVERY open position at a marketable limit. Type FLATTEN: ")
        if typed.strip() != "FLATTEN":
            print("Not confirmed. Nothing sent.")
            return 2
    try:
        eng = _engine(cfg, args, reduce_only=True)
    except (LiveRefused, BrokerError, DataUnavailable, FileNotFoundError) as exc:
        print(f"[refused] {exc}", file=sys.stderr)
        return 2
    rep = eng.flatten(args.reason)
    print(rep.text())
    return 3 if rep.alerts else 0


def cmd_replay(args) -> int:
    run_id = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
    base = TradingConfig(mode="replay")
    cfg = TradingConfig(mode="replay", state_root=base.state_root / "replays" / run_id)
    try:
        inner = get_provider(args.provider)
    except DataUnavailable as exc:
        print(f"[fatal] {exc}", file=sys.stderr)
        return 2
    symbols = args.symbols or DEFAULT_UNIVERSE
    print(f"loading {len(symbols)} symbols from {inner.name} ...")
    provider = ReplayProvider(inner, symbols, lookback=args.lookback)
    if not provider.session_dates():
        print("[fatal] no history loaded: " + "; ".join(list(provider.errors.values())[:3]), file=sys.stderr)
        return 2
    store = BookStore(cfg.book_path)
    store.create("replay", args.equity)
    journal = Journal(cfg.journal_path, "replay")
    journal.append("INIT", starting_equity=args.equity, provider=provider.name)
    engine = TradingEngine(
        cfg, provider, PaperBroker(cfg.state_dir / "paper_broker.json", cfg.slippage_pct),
        journal, store, SimulatedConfirmer(), symbols=symbols,
        catalysts=load_catalysts(args.catalysts),
    )
    start = date.fromisoformat(args.start) if args.start else None
    end = date.fromisoformat(args.end) if args.end else None

    def show(rep):
        if rep.submitted or any("CLOSED" in a for a in rep.actions) or rep.kill:
            print(rep.text())

    reports = run_replay(lambda: engine, provider, start, end, progress=show if args.verbose else None)
    summary = replay_summary(store.load(), cfg.journal_path)
    summary["provider"] = provider.name
    summary["sessions_replayed"] = len(reports)
    print(json.dumps(summary, indent=2))
    if "MOCK" in provider.name.upper():
        print("SYNTHETIC DATA — this exercises the system, it says nothing about the market.")
    return 0


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="NSE swing trading system (paper by default)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, modes=("paper", "live")):
        p.add_argument("--mode", default="paper", choices=modes)
        p.add_argument("--provider", default=None, help="yfinance | groww | kite | mock")
        p.add_argument("--symbols", nargs="*", default=None)
        p.add_argument("--catalysts", default=None)
        p.add_argument("--backtest", default=None, help="run_backtest.py --json output, for Gate 1")
        p.add_argument("--override-gates", default=None,
                       help="live, per-order confirmed only: trade despite failed gates; reason is journaled")
        p.add_argument("--autonomous", action="store_true",
                       help="live without per-order confirmation; needs gates + a signed policy")
        p.add_argument("--broker", default=None, choices=("kite", "groww"),
                       help="live broker (default: LIVE_BROKER env, else kite)")

    p = sub.add_parser("init")
    p.add_argument("--mode", default="paper", choices=("paper", "live"))
    p.add_argument("--equity", type=float, required=True,
                   help="capital the system may use (paper: simulated starting capital)")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("cycle")
    common(p)
    p.set_defaults(fn=cmd_cycle)

    p = sub.add_parser("watch")
    common(p)
    p.add_argument("--interval", type=int, default=60)
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("flatten")
    common(p)
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_flatten)

    p = sub.add_parser("record-exit", help="book a fill the broker could not report (sends nothing)")
    p.add_argument("--mode", default="live", choices=("paper", "live"))
    p.add_argument("--trade-id", required=True)
    p.add_argument("--qty", type=int, required=True)
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--reason", default="manual", choices=("stop", "target", "time", "emergency", "manual"))
    p.add_argument("--note", required=True, help="where the fill came from, e.g. contract note number")
    p.add_argument("--date", default=None, help="YYYY-MM-DD of the fill (default today)")
    p.set_defaults(fn=cmd_record_exit)

    for name, fn in (("status", cmd_status), ("verify", cmd_verify)):
        p = sub.add_parser(name)
        p.add_argument("--mode", default="paper", choices=("paper", "live"))
        p.set_defaults(fn=fn)

    p = sub.add_parser("gates")
    p.add_argument("--backtest", default=None)
    p.set_defaults(fn=cmd_gates)

    p = sub.add_parser("kill")
    p.add_argument("--reason", required=True)
    p.add_argument("--mode", default=None, choices=("paper", "live"),
                   help="omit to stop every mode")
    p.set_defaults(fn=cmd_kill)

    p = sub.add_parser("release")
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_release)

    p = sub.add_parser("replay")
    p.add_argument("--provider", default=None)
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--catalysts", default=None)
    p.add_argument("--equity", type=float, required=True)
    p.add_argument("--start", default=None, help="YYYY-MM-DD")
    p.add_argument("--end", default=None, help="YYYY-MM-DD")
    p.add_argument("--lookback", type=int, default=800)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(fn=cmd_replay)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
