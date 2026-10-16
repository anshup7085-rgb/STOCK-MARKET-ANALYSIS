# Stock Market Analysis — NSE research agent

An evidence-driven market-intelligence and trading system for NSE equities.
A Python scanner does the arithmetic; an agent does the judgement; a gated
execution layer acts on the plan. The research half (`market/`) is **read-only
by design**. Orders exist only in `trading/` — paper by default, live only
through the opt-in, evidence gates and per-order confirmation in **`TRADING.md`**.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

PYTHONPATH=. python3 tests/test_offline.py          # research layer, no network
PYTHONPATH=. python3 tests/test_trading.py          # execution layer, no network
python run_scan.py --provider mock --symbols TCS INFY HAL   # wiring check
python run_scan.py --symbols RELIANCE TCS HAL --json scans/today.json
python run_backtest.py --symbols RELIANCE TCS HAL   # does the score predict?

# full plan with costs and net returns
python run_scan.py --equity 500000 --json scans/today.json
python render_plan.py scans/today.json > scans/today.md
```

Paper trading — the system acting on its own plans, with simulated fills:

```bash
python run_trader.py init  --mode paper --equity 500000
python run_trader.py cycle --mode paper             # once a day, after 16:00 IST
python run_trader.py status --mode paper
python run_trader.py replay --equity 500000         # the whole system through history
```

Full installation, provider setup and the daily Kite token flow: **`SETUP.md`**.
Execution, live trading, what to pay for and the daily routine: **`TRADING.md`**.

## What each file is for

| File | Purpose |
|---|---|
| `CLAUDE.md` | Project instructions for the agent — the tooling contract and the rules that override convenience |
| `PROMPT.md` | The analysis framework: operating principles, scoring model, trade construction, output format |
| `RISK_POLICY.md` | Position sizing, portfolio limits, no-trade conditions |
| `DATA_SOURCES.md` | Source hierarchy and citation requirements |
| `SESSION_CHECKLIST.md` | The order to work in, per session |
| `OUTPUT_TEMPLATE.md` | The report shape |
| `SETUP.md` | Install, providers, credentials, known limitations |
| `ROADMAP.md` | Current state, the evidence gates before live capital, kill criteria |
| `TRADING.md` | The execution layer: modes, risk controls, live setup, daily routine |

## Code layout

```
run_scan.py              CLI — walks the checklist, emits JSON
run_backtest.py          CLI — walk-forward test of whether the score predicts
render_plan.py           CLI — scan JSON into a readable structured trade plan
run_trader.py            CLI — the trading system: init, cycle, watch, status, gates, replay, kill
market/scan.py           the scan pipeline, shared by run_scan.py and the trading engine
market/calendar.py       NSE holiday calendar (data/nse_holidays.json — fill from the circular)
market/corporate_actions.py  flags unadjusted splits/bonuses from price fingerprints
market/charges.py        Indian equity costs, net R:R, breakeven win rate
market/config.py         thresholds and settings; secrets read from env only
market/indicators.py     causal indicators + confirmed swing structure
market/levels.py         ATR stops, structure-based targets, position sizing
market/score.py          transparent 0-85 model with per-component reasoning
market/backtest.py       path simulation, score-band stats, rank correlation
market/universe.py       candidate list, liquidity gate, regime classifier
market/providers/        yfinance (free, EOD) | groww | kite (intraday + OI) | mock
trading/engine.py        the daily cycle: scan, reconcile, breakers, entries, journal
trading/risk.py          pre-trade risk engine, circuit breakers, kill switch
trading/strategy.py      scan candidates into sized intents (sized on net loss)
trading/brokers/         paper (simulated fills) | kite (live: LIMIT + GTT OCO) | groww (live: LIMIT + paired GTTs)
trading/journal.py       hash-chained, append-only audit log
trading/gates.py         ROADMAP Gates 1-3 evaluated from recorded evidence
trading/live.py          the live opt-in: ack phrase, gates, confirmation, policy
trading/replay.py        the whole system walked through history
tools/kite_session.py    the daily manual Kite login, made less fiddly
reports/                 rendered session reports
```

## How the score is built

Six components, 85 points, each returning its own reasoning string:

| Component | Points | Source |
|---|---:|---|
| Trend structure | 20 | EMA stack, position vs 52-week high |
| Momentum / relative strength | 15 | RSI, 20-day return, excess return vs benchmark |
| Volume / liquidity | 10 | Median turnover, volume vs 20-day average |
| Catalyst | 15 | `catalysts.json` — supplied by you, never inferred |
| Derivatives / positioning | 10 | Open interest (Kite only) |
| Risk / reward | 15 | Measured to confirmed swing structure |

Market regime is **not** one of them. It is a property of the market, so it
returns the same value for every candidate and cannot rank anything — it is
applied instead as a deduction to every candidate equally, which leaves the
ranking untouched but can push a whole shortlist below the floor in a hostile
tape. Penalties (stale data, wide ATR, gaps, thin history, binary events) cap at
15 points.

## Costs and expected return

Every candidate carries an `economics` block: brokerage, STT, exchange
transaction charges, SEBI fees, stamp duty, GST and DP charges on the real round
trip, plus assumed slippage applied **against** the trade on both legs. From that
it reports net profit at target, net loss at stop, net reward:risk, and the
**breakeven win rate** — the fraction of trades you must win merely to break even.

That last number is the honest form of "expected return". A true expected return
needs a win rate; a win rate is an empirical property of the strategy; and
`run_backtest.py` exists to measure it. Until that number exists for a real feed,
the report gives you EV across a range of assumed win rates rather than a single
figure that would be a forecast in disguise.

Default charge rates are published-retail figures shipped **unverified** on
purpose — statutory rates change. Check them against your own contract note and
set `verified_on`.

## Two things to understand before trusting an output

**Coverage caps confidence.** A component with no data is dropped from both the
numerator and the denominator rather than scored zero — a missing feed is not a
bad signal. The share of the model that had real data behind it is reported as
`coverage`, and below 55% the conclusion is "insufficient data", not a low score.

**NO TRADE is a valid output.** If nothing clears the score floor, the scanner
says so instead of promoting the least-bad name.

Known limitations — corporate actions detected but not adjusted, an NSE holiday
calendar that ships empty until you fill it, no breadth data, and a score whose
predictive power on real data is still unmeasured — are documented in `SETUP.md`
and `TRADING.md`. Read them.

This is decision support, not investment advice, and not a guarantee of returns.
