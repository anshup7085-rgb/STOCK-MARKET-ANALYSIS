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

It uses the levels, target and time exit `run_scan.py` prints and
`run_backtest.py` measures. It is **not identical** to the backtest: the
backtest enters at the signal bar's close and takes every scored setup, while
the system enters with a limit order next session (which may not fill) and only
trades names that pass the net R:R floor and every risk limit. Gate 1 asks
whether the *score* predicts; `replay` measures *this system*, and paper trading
measures it forward.

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
journal is the evidence Gate 2 reads, so it is strict about what counts: only
trades on a **real** data feed (the paper book refuses `--provider mock`; use
`replay --provider mock` for wiring checks), journaled within five days of the
market date they concern. A week of missed cron runs costs evidence rather than
inventing it.

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

# Zerodha
python run_trader.py cycle --mode live --broker kite  --provider kite  --backtest scans/backtest.json
python run_trader.py watch --mode live --broker kite  --provider kite      # during the session

# or Groww
python run_trader.py cycle --mode live --broker groww --provider groww --backtest scans/backtest.json
python run_trader.py watch --mode live --broker groww --provider groww
```

`--broker` defaults to `LIVE_BROKER`, else `kite`. A live book is bound to the
broker of its first live cycle — order ids from one broker mean nothing to the
other — so switching brokers means closing out and starting a new live book.

The live path refuses unless all of the following hold:

1. `LIVE_TRADING_ACK` is set to exactly the phrase above.
2. Broker credentials minted by hand today are present — `KITE_API_KEY` +
   `KITE_ACCESS_TOKEN`, or `GROWW_ACCESS_TOKEN` — and **no** TOTP seed, password or
   PIN is in the environment.
3. ROADMAP **Gates 1 and 2 pass** (`python run_trader.py gates`). If they do not, you
   may still trade **with per-order confirmation** by passing
   `--override-gates "<your reason>"`; the reason is journaled before anything
   else happens. Autonomous mode has no override.
4. Each order is confirmed at the terminal by typing it back (`BUY 12 TCS`). With
   no terminal attached — e.g. from cron — nothing is sent.

**What a confirmation covers.** The whole plan on the ticket: the entry, the
stop/target protection placed once it fills, and the time or emergency exit the plan
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
| Zerodha account + Kite Connect app | Order API and, on the paid plan, market data | Zerodha's support pages (checked 2026-10-19): the **Personal** plan is free and covers orders and account APIs but no live or historical data; the **Connect** plan is Rs500/month per API key with live data and historical candles. From October 2026 Zerodha credits the Rs500 in months where you pay at least Rs2,000 of brokerage. Orders alone work on the free plan with `--provider yfinance`; the live price check needs the paid plan or Groww data. |
| **Static IP**, whitelisted on the app | Zerodha accepts API **orders** only from a registered static IP from 1 April 2026; read-only calls work from anywhere | Up to two IPs (primary + secondary). Simplest: a small VPS with a static IP, which is also where cron runs. IPs may only be shared within immediate family. |
| Demat debit authorisation (DDPI) | Automated **sell** orders of delivery holdings — every stop and target here — need it | Without it, sells wait for a per-sell CDSL authorisation and GTT legs can fail. Zerodha charges Rs100 + GST (Rs118) once to activate it (support page, checked 2026-10-19). |
| A daily manual login | Access tokens expire each morning; SEBI's framework expects a human 2FA login each trading day | `python tools/kite_session.py` — you log in on Zerodha's page; it prints the export line. It never stores your secret or a TOTP seed. |
| Algo ID, if required | The exchange framework tags algo orders | Ask Zerodha whether a self-built strategy at this order rate needs one. If they issue one, `export KITE_ALGO_ID=...` and every order carries it. |

**Groww**, if you trade through it instead:

| Item | Notes |
|---|---|
| Groww trading-API subscription | groww.in/trade-api (checked 2026-10-19): a free trial with orders but no data; Rs499 + GST a month (shown as an early-bird price against Rs2,000), or Rs4,999 + GST a year. |
| Static IP | SEBI's framework expects API orders from a registered static IP — check how Groww registers yours. |
| DDPI | Same reason as Zerodha: automated sells of delivery holdings. |
| Daily token | Mint `GROWW_ACCESS_TOKEN` yourself (SETUP.md §3b). Never store the TOTP seed — the adapter refuses to start if it sees one. |

Data: use the same broker for data and orders (`--provider kite --broker kite`,
or `--provider groww --broker groww`) so the scan, the live price check and the
orders all see the same prices.

### How protection differs between the two brokers

| | Zerodha | Groww |
|---|---|---|
| Stop + target | One **GTT one-cancels-other** order, held by Zerodha | Groww offers OCO only for F&O and intraday, **not delivery** — so two separate **GTTs** (stop: DOWN trigger; target: UP trigger) |
| When one fires | Zerodha cancels the other | **This system** cancels the other the moment it sees one leave ACTIVE (every cycle, and every minute under `watch`) |
| Gap risk between the two | none | A few seconds: if price hits one leg and then the other before the system looks, the second sell targets shares already sold. For CNC that is rejected — **unless you hold more of that stock outside the system**, which it would then sell. Don't hand-trade the same stock in the account the system uses. |
| Fill price after a trigger | Read from the order the GTT placed | Read from the order the GTT placed if Groww names it; else the single exact matching sell in today's order book; else **UNKNOWN** — the system alerts and you book it with `record-exit` from your contract note. It never guesses a price. |
| Order rate | ≤ 5/second | ≤ 4/second (Groww's published order limit is 250/minute) |

The Groww adapter was written against the `growwapi` 1.5.0 source and Groww's
documented response fields, and tested against a fake client — like the Kite
adapter, it has not yet placed an order on a real account. Run it first with a
one-share position you are willing to lose, and watch every step.

## Daily routine

**Paper** — after the close, from cron on a machine with market data. On a
fresh Linux server (Ubuntu/Debian, Python 3.10+, an ordinary user account — the
script refuses to run as root):

```bash
sudo apt install python3-venv git cron          # stock Ubuntu/Debian lack python3-venv
git clone <repo> && cd <repo>
deploy/setup_server.sh --paper-equity 500000 --install-cron
```

It builds `.venv`, installs the requirements (`--broker kite` / `--broker groww`
add the SDKs), runs the three test suites on that machine and checks that the
market feed is reachable. It writes `~/.config/nse-trader/env` (mode 600, no
secrets, for you to fill in), creates the paper book, and installs two cron jobs
between marker lines, leaving your other jobs alone: the paper cycle at 16:20
IST Mon–Fri, and `verify` of the journal on Saturdays. The server's time zone
must be IST or UTC all year; a zone with daylight saving, or any other offset,
is refused rather than guessed. Re-running it is safe; a damaged marker block
is refused and left for you to fix. Logs go to `logs/` (kept 400 days). The
journal in `state/` is the record. To keep the env file elsewhere, set
`NSE_TRADER_ENV=/path/to/env` when running setup; the cron lines carry it.

Cron tells nobody when a job fails. `deploy/cron_cycle.sh` does: a cycle that
exits with anything but 0 or 3, or a journal that does not verify, is sent to
your WhatsApp through `run_trader.py notify` once alerts are configured. Before
that it is only in `logs/`. The wrapper also refuses to run, with exit 78, if the
env file is not yours and mode 600, or holds a TOTP seed, password, PIN, broker
API secret or the live ack phrase.

The script installs **no live job** and never sets `LIVE_TRADING_ACK`. A paper
cycle runs whatever strategy `trading/strategy.py` holds — today that is the
score Gate 1 retired, so a paper book now measures the plumbing (fills, alerts,
journal) and gives a forward read on the score, not a strategy worth money.

**Live** — by hand, because each order needs you:

1. ~09:00 — mint today's token (`python tools/kite_session.py` for Zerodha; your
   own token step for Groww) and export it.
2. ~09:20 — `python run_trader.py cycle --mode live --broker kite --provider kite --backtest ...`
   (or `--broker groww --provider groww`).
   It reconciles, scans on yesterday's completed bar, checks each name's live
   price against its stop, and asks you to confirm each order.
3. Leave `python run_trader.py watch --mode live --broker ... --provider ...` running.
   It places the stop/target the moment an entry fills, cancels the surviving
   Groww leg the moment the other fires, books exits as they happen, cancels a
   stranded stop leg so an emergency exit can go out, and makes one settling
   pass at 15:40 before it ends — Kite's order book only lasts the day.
   **Without it, a position bought at 09:25 is unprotected until the next cycle.**
   `watch` and `flatten` only reduce risk, so they need the ack phrase but not
   the evidence gates.
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
| `record-exit --trade-id T --qty N --price P --note S` | Book a fill the broker could not report, from your contract note. Sends nothing; journaled |
| `replay --equity N [...]` | Walk the whole system through history |

## F&O paper trading — strategy V

A separate, **paper-only** engine in `trading/fno/` trades one pre-registered
idea: a defined-risk NIFTY weekly iron condor
(`research/PREREGISTRATION_3.md`).

- **What it trades.** Each week it sells a call and a put about one
  straddle-width from the index and buys protection 100 points beyond each.
  It holds to expiry and settles at the official close.
- **Risk.** The most a trade can lose is fixed when it opens, and it is never
  more than 1% of paper equity.
- **What it is not.** It places no order anywhere. There is no `--mode`; it
  cannot go live.

**What you need**

| Item | Why |
|---|---|
| A market-data plan: **Kite Connect** (Zerodha, paid Connect plan) or the **Groww trading API** (paid plan) | Live option quotes with bid and ask. Yahoo has no option data. Both plans were listed at about Rs500 a month when last checked; check the current price before you pay |
| A machine that stays on 09:45–17:00 IST on trading days | A small VPS or your own computer. Paper needs **no static IP**: the static-IP rule covers orders, and this sends none |
| Two minutes each trading morning | Mint the day's access token by hand (`python tools/kite_session.py` for Zerodha), export it, start the runner |
| `pip install kiteconnect` or `pip install growwapi` | The feed's SDK |

**First morning**

```bash
python run_trader.py fno probe --feed kite      # or --feed groww; prints response shapes, no prices
python run_trader.py fno init  --feed kite --equity 500000
```

Send the `probe` output for review before the first entry day. It shows
whether the lot size, strike interval and expiry weekday match what V expects.
It also shows whether quotes carry depth and timestamps. **None of those facts
is hard-coded.**

**Every trading morning**

```bash
export KITE_API_KEY=... KITE_ACCESS_TOKEN=...   # minted by hand today
tmux new -s fno 'python run_trader.py fno run --feed kite'
```

The runner:

- makes a pass now, then one every 15 minutes from 10:00 to 14:00;
- makes settlement passes at 16:00, 16:30 and 17:00;
- exits after the last pass.

A day it does not run is visible in the journal as a day with no runner.

**Commands**

| Command | Does |
|---|---|
| `fno status` | Paper equity, every trade and its legs, journal integrity |
| `fno evaluate [--json F]` | The registered statistic and verdict, plus the descriptive record, from the journal alone |
| `fno verify` | Re-hash the journal chain |
| `fno flatten --feed F --reason R` | Close the open trade at the touch. It is off-rule and **still counts** |
| `fno settle --expiry D --value V --source S` | Enter NSE's published close when the two sources disagree |
| `fno demo` | Synthetic wiring check in a temp directory. Never touches the paper book |
| `kill --reason R` | Blocks new F&O entries too: `state/KILL` is global |

**What to expect.** About 50 trades a year. Each trade earns a small credit
most weeks and occasionally loses up to its fixed maximum.

- **The verdict needs an edge of about 0.15R a trade** before two and a half
  years of weekly trades can show it. A smaller edge will most likely end in
  FAIL.
- **Paper profit is not evidence until the verdict says PASS.** The record
  still measures real costs and fill quality, and any later F&O idea needs those.

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
- **Holiday calendar is unverified and short.** `data/nse_holidays.json` holds the
  rest of 2026 from secondary sources that agreed with each other. Confirm it
  against the NSE circular, add 2027 when published, and set `verified_on` — Gate 2
  checks it.
- **Charge rates are unverified defaults.** There are two fee cards in
  `market/charges.py`: Zerodha-style (`delivery`, zero delivery brokerage) and
  `groww_delivery` (lower of Rs20 or 0.1% per order, Rs5 floor, ~Rs20 + GST DP per
  sell). The live path picks Groww's card automatically for `--broker groww`;
  paper uses `CHARGE_SCHEDULE` (default `delivery`). Check each against a real
  contract note and set `verified_on`.
- **Holdings reconciliation is approximate** and only raises alerts; a person
  resolves mismatches.
- **Alerts depend on Twilio and WhatsApp's rules.** Without an approved WhatsApp
  template, alerts arrive only within 24 hours of your last message to the
  number; calls to Indian numbers come from a foreign number and can be flagged.
  Run `alert-test` after any change.
- **Corporate actions** are detected from price fingerprints and rejected, not
  adjusted.
- **No live entries the morning after a long weekend.** Staleness is measured in
  calendar days, so on a Tuesday after a Monday holiday yesterday's "last
  completed bar" is four-plus days old and every name is rejected as stale. The
  system errs to NO TRADE that morning.
- **An order call that fails has an unknown outcome.** The engine asks the
  broker for the order's tag and adopts it if it landed; when the broker cannot
  say, the trade waits up to two minutes for the next reconcile to settle it.

## Alerts — WhatsApp, and a phone call when it is urgent

| What happened | You get |
|---|---|
| **Urgent** (live): a position without protection, a stop leg stranded below the market, an exit that may not have gone out, an exit blocked after 3 rejections, the kill switch tripping, shares missing at the broker | A **phone call**. If you don't pick up (no answer, busy, voicemail) → **WhatsApp** with the details |
| Urgent in **paper** | WhatsApp only — a simulated position cannot lose money (`ALERT_CALLS=always` to ring anyway) |
| Any other alert | WhatsApp |
| End of the daily cycle | A one-message summary on WhatsApp (`ALERT_DAILY_SUMMARY=0` to turn off) |

The same alert is sent once per 30 minutes (`ALERT_DEDUP_MINUTES`), so `watch`
does not ring you every minute while a condition lasts — and an alert that
failed to deliver is retried on the next tick. A call rings on a background
thread, so `watch` keeps protecting positions while your phone rings. Alerts
can never stop trading: every failure is journaled as `ALERT_FAILED`.

**Setup** (Twilio does both WhatsApp and calls from one account):

1. Create a Twilio account and an **API key** (Console → API keys). Keep the
   key secret out of the repo — put it in the server's environment file with
   `chmod 600`.
2. **Calls:** buy a voice-capable Twilio number. Calls to Indian numbers must come
   from a non-Indian number, and Indian phones may label it spam — **save it in
   your contacts as "TRADING ALERT"**.
3. **WhatsApp, for testing:** join the Twilio WhatsApp sandbox from your phone
   (Console → Messaging → Try WhatsApp) and use its `whatsapp:+1...` number.
   Sandbox messages only arrive within 24 hours of your last message to it.
4. **WhatsApp, for real:** register a WhatsApp sender, create a Messaging
   Service, and get a *utility* template approved with one variable, e.g.
   `Trading alert: {{1}}`. Set its Content SID below. Without a template,
   WhatsApp refuses business-initiated messages outside the 24-hour window
   (Twilio error 63016).
5. Set the variables (see `.env.example`), then:

```bash
python run_trader.py alert-test --channel whatsapp
python run_trader.py alert-test --channel call      # rings you; don't answer to test the fallback
```

Per-message and per-minute call charges apply — check Twilio's current pricing.

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
  paper_fno/                 F&O paper book for strategy V
    book.json                trades, cycles, manual settlement values
    journal.jsonl            hash-chained; `fno evaluate` reads only this
    KILL                     F&O-only kill switch
    runner.lock              one pass at a time
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
