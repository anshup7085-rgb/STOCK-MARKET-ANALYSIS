# Trading system

The execution half of this project. `market/` measures and plans; `trading/`
acts on the plan — on paper by default, live only through an opt-in that
checks the evidence first.

Nothing here makes the strategy profitable. Whether the score predicts
anything on real NSE data is still **unmeasured** (ROADMAP Gate 1). This system
exists so that when that answer comes in, there is a disciplined, audited way to
act on it — and so that paper trading can start collecting evidence now.

## How a trade happens

```
run_scan pipeline ──► intent ──► risk engine ──► confirmation ──► broker ──► journal
 (market/scan.py)   (sized on    (19 checks,    (paper: none;    (paper sim  (hash-chained,
                     NET loss)    21 live,       live: you type   or Kite)    every decision)
                                  all logged)    "BUY 12 TCS")
```

Once per trading day, `run_trader.py cycle`:

1. **Scans** with exactly the pipeline `run_scan.py` prints — same levels, same
   score, same economics. A still-forming daily bar is dropped first (today's bar
   counts only after 16:00 IST, once NSE's closing price is final).
2. **Syncs** — paper simulates the new bar; live needs nothing.
3. **Reconciles** every active trade: entry filled? protection in place? stop or
   target hit? held too long? Protection is always checked before anything else.
4. **Circuit breakers** — a daily loss or drawdown beyond the limit engages the kill
   switch. Release is manual.
5. **Entries** — each shortlisted name becomes a sized intent, passes or fails the
   risk engine (every check and its reason journaled), is confirmed, then sent.
6. **Persists** the book and journals the cycle summary.

## The strategy, precisely

It is what `run_scan.py` describes and `run_backtest.py` measures, so paper and
live results are evidence about the thing that was tested.

| | |
|---|---|
| Universe | `market/universe.py` default list, or `--symbols` |
| Side | Long only. Retail cannot carry a short overnight in the cash segment |
| Product | CNC (delivery) |
| Entry | BUY LIMIT at the top of the scanner's entry zone, valid one session |
| Stop | Scanner's stop. Sent as a stop-**limit** 1% below (`STOP_LIMIT_BUFFER_PCT`) |
| Target | Scanner's Target 2 |
| Protection | Stop and target as one GTT one-cancels-other order, resting at the broker |
| Time exit | After 20 sessions (`MAX_HOLD_BARS`) — the backtest horizon |
| Size | Largest quantity whose **net** loss at the stop (charges + slippage) fits the risk budget, capped at 20% of capital |
| Filters | Score ≥ 60, coverage ≥ 55%, **net** R:R ≥ 2.0, plus every risk check below |

## Risk controls

**Per order (the risk engine, `trading/risk.py`).** Kill switch off, long CNC
only, score, coverage and net R:R floors, levels ordered, not on
`data/restricted.txt`, no existing position in the name, max open positions,
max entries per cycle, max orders per day, net loss within the per-trade budget,
position within the concentration cap, total open risk within the portfolio cap,
capital available (and, live, the broker's available cash), entry within 10% of
the last close, live price still above the stop, charges below 15% of the rupee
risk, and correlation ≤ 0.75 with everything already held.

**Account level.** A daily loss of 2% (`DAILY_LOSS_LIMIT_PCT`) or a drawdown of
10% from peak (`MAX_DRAWDOWN_PCT`) engages the kill switch. The kill switch is a
file (`state/KILL`, or `state/<mode>/KILL`) — create it by hand, by cron, or
over SSH from a phone, and nothing needs to be running to honour it. It blocks
new entries and cancels pending ones; **it never removes a protective stop.**

**Order flow.** LIMIT orders only — the system never sends a MARKET order.
Order-placing calls are throttled to ≤ 5 per second, half the 10/second level at
which the exchange treats a strategy as a registered algo. Every order carries a
client tag; after a crash the engine asks the broker for that tag before
sending anything again.

**Live caps.** Risk per trade is capped at **0.25%** in live mode
(`LIVE_MAX_RISK_PER_TRADE_PCT`), whatever `RISK_PER_TRADE_PCT` says — ROADMAP
Gate 3's "small live" stage.

## Modes

### Paper — start here

```bash
python run_trader.py init  --mode paper --equity 500000
python run_trader.py cycle --mode paper            # once a day, after 16:00 IST
python run_trader.py status --mode paper
```

Fills are simulated on daily bars, and every ambiguity goes against you: an
order never fills on the bar its signal came from, a limit must be traded
**through** (not touched), a bar that spans stop and target takes the stop, the
target is never credited on the day of entry, a gap below the stop fills at the
open — and a gap below the stop-*limit* leaves the position **unprotected**,
because that is what a real stop-limit does. The engine then sends an emergency
exit, as it would live.

Run it daily from cron (an example is under the daily routine below). The paper
journal is the evidence Gate 2 reads, and it only counts trades journaled in
real time.

### Replay — the system through history

```bash
python run_trader.py replay --equity 500000 --start 2024-01-01      # real feed
python run_trader.py replay --provider mock --equity 500000         # wiring check
```

The same engine, session by session, on history truncated at each date: same
sizing, risk limits, correlation check, kill switch, fill rules and charges.
Where `run_backtest.py` asks "does the score predict?", replay asks "what would
this whole system have done?". Each run gets its own directory under
`state/replays/`. Replay results are research, not Gate 2 evidence.

### Live

```bash
export LIVE_TRADING_ACK="I accept that this places real orders with real money"
python run_trader.py init  --mode live --equity 200000     # capital the system may use
python run_trader.py cycle --mode live --provider kite --backtest scans/backtest.json
python run_trader.py watch --mode live --provider kite     # during the session
```

The live path refuses unless all of the following hold:

1. `LIVE_TRADING_ACK` is set to exactly the phrase above.
2. `KITE_API_KEY` and a `KITE_ACCESS_TOKEN` minted by hand today are present, and
   **no** TOTP seed, password or PIN is in the environment.
3. ROADMAP **Gates 1 and 2 pass** (`python run_trader.py gates`). If they do not, you
   may still trade **with per-order confirmation** by passing
   `--override-gates "<your reason>"`; the reason is journaled before anything
   else happens. Autonomous mode has no override.
4. Each order is confirmed at the terminal by typing it back (`BUY 12 TCS`). With
   no terminal attached — e.g. from cron — nothing is sent.

**What a confirmation covers.** The whole plan on the ticket: the entry, the GTT
stop/target placed once it fills, and the time or emergency exit the plan
already names. Those follow-on orders only ever reduce or close the confirmed
position, like a bracket order. Anything off the ticket (`flatten`) needs its
own confirmation.

**Autonomous live** (no per-order confirmation) needs both gates passed **and** a
policy file you write and sign at `state/live/execution_policy.json`:

```json
{"autonomous_live": true,
 "acknowledged_by": "Your Name",
 "acknowledged_on": "2026-10-16",
 "max_risk_per_trade_pct": 0.25,
 "max_open_positions": 3,
 "max_orders_per_day": 6}
```

The policy can only tighten the configured limits, never loosen them.

## What to set up and pay for (live)

Check every item below against the broker's and exchange's current pages
before you pay or trade — these rules changed in 2025–26 and may change again.

| Item | Why | Notes |
|---|---|---|
| Zerodha account + Kite Connect app | Order API and, on the paid plan, market data | Third-party write-ups put the Connect plan at Rs500/month and describe a free Personal plan with orders but no market data. Confirm on the Kite developer console. |
| **Static IP**, whitelisted on the app | Zerodha accepts API **orders** only from a registered static IP from 1 April 2026; read-only calls work from anywhere | Up to two IPs (primary + secondary). Simplest: a small VPS with a static IP, which is also where cron runs. IPs may only be shared within immediate family. |
| Demat debit authorisation (DDPI) | Automated **sell** orders of delivery holdings — every stop and target here — need it | Without it, sells wait for a per-sell CDSL authorisation and GTT legs can fail. Confirm the current requirement with Zerodha. |
| A daily manual login | Access tokens expire each morning; SEBI's framework expects a human 2FA login each trading day | `python tools/kite_session.py` — you log in on Zerodha's page; it prints the export line. It never stores your secret or a TOTP seed. |
| Algo ID, if required | The exchange framework tags algo orders | Ask Zerodha whether a self-built strategy at this order rate needs one. If they issue one, `export KITE_ALGO_ID=...` and every order carries it. |

Data: live cycles should use `--provider kite` (needs the paid data plan) so
the scan, the live price check and the orders all come from the same source.

## Daily routine

**Paper** — after the close, from cron on a machine with market data:

```cron
# 16:20 IST, Mon–Fri. Exit code 3 means alerts — wire it to mail or a notifier.
20 16 * * 1-5  cd /path/to/repo && .venv/bin/python run_trader.py cycle --mode paper >> state/paper/cron.log 2>&1
```

**Live** — by hand, because each order needs you:

1. ~09:00 — `python tools/kite_session.py`, export the token.
2. ~09:20 — `python run_trader.py cycle --mode live --provider kite --backtest ...`.
   It reconciles, scans on yesterday's completed bar, checks each name's live
   price against its stop, and asks you to confirm each order.
3. Leave `python run_trader.py watch --mode live --provider kite` running until
   15:30. It places the GTT stop/target the moment an entry fills and books exits
   as they happen. **Without it, a position bought at 09:25 is unprotected until
   the next cycle.**
4. Anything odd: `python run_trader.py kill --reason "..."`. Then look.

## Commands

| Command | Does |
|---|---|
| `init --mode M --equity N` | Create a book. Never overwrites an existing one |
| `cycle --mode M` | One daily cycle |
| `watch --mode live` | Reconcile-only loop during the session |
| `status --mode M` | Equity, open risk, active trades, kill switch, journal integrity |
| `verify --mode M` | Re-hash the journal chain; reports the first edited line |
| `gates [--backtest F]` | Evaluate ROADMAP Gates 1–3 from recorded evidence |
| `kill --reason R [--mode M]` | Engage the kill switch (all modes if no `--mode`) |
| `release --reason R` | Release it (journaled) |
| `flatten --mode M --reason R` | Exit everything at a marketable limit (live: type FLATTEN) |
| `replay --equity N [...]` | Walk the whole system through history |

## Known gaps — read before trusting it

- **The strategy is unvalidated.** Gate 1 has not been run on real data. Paper
  trading can start now because it costs nothing, but its results are not
  validation until Gate 1 passes.
- **Gaps through the stop.** Sizing caps the loss *at the stop*. A gap through it
  loses more, and a gap through the stop-limit leaves the position open until the
  emergency exit fills.
- **The fill-to-protection window, live.** An entry that fills mid-session is
  protected only once `watch` (or the next cycle) sees it.
- **Partial fills.** A partly filled entry is protected only once its order
  completes or expires at the close.
- **Holiday calendar ships empty.** `data/nse_holidays.json` must be filled from
  the NSE circular (Gate 2 checks it). Until then holidays are not known.
- **Charge rates are unverified defaults.** Check `market/charges.py` against a
  contract note and set `verified_on`.
- **Holdings reconciliation is approximate** and only raises alerts; a person
  resolves mismatches.
- **No notifications.** Exit code 3 signals alerts; hook it to whatever you read.
- **Corporate actions** are detected from price fingerprints and rejected, not
  adjusted.

## State layout

```
state/                       gitignored — account data never goes to git
  KILL                       global kill switch (if present)
  paper/  live/
    book.json                trades, capital, marks — rewritten every cycle
    journal.jsonl            hash-chained audit log — append only
    paper_broker.json        paper only: simulated orders
    execution_policy.json    live only, optional: your signed autonomous policy
    KILL                     mode kill switch (circuit breakers write here)
  replays/<run-id>/replay/   one directory per replay run
```

## Sources for the regulatory and broker notes

Broker and exchange rules change; these were the references when this was
written. Re-check them.

- Zerodha support — static IP for Kite Connect order placement:
  https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/static-ip
- Kite Connect fee revision thread: https://kite.trade/forum/discussion/comment/49630
- FYERS notice on the SEBI retail-algo framework from 1 April 2026 (static IP,
  daily 2FA, 10 orders/second): https://fyers.in/notice-board/new-sebi-framework-for-retail-algo-trading-from-april-01-2026/
- 5paisa on 2FA and audit-trail requirements: https://www.5paisa.com/news/sebi-tightens-algo-trading-rules-with-mandatory-2fa-and-audit-trails-from-april-1

The primary documents are SEBI's circular on safer retail participation in
algorithmic trading and NSE's implementation circulars. Read those, not a
summary, before going live.
