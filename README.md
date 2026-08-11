# Stock Market Analysis — NSE research agent

An evidence-driven market-intelligence and trading-research kit for NSE equities.
A Python scanner does the arithmetic; an agent does the judgement. It is
**read-only by design** — no module places, modifies or cancels an order.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

PYTHONPATH=. python3 tests/test_offline.py          # 26 tests, no network
python run_scan.py --provider mock --symbols TCS INFY HAL   # wiring check
python run_scan.py --symbols RELIANCE TCS HAL --json scans/today.json
python run_backtest.py --symbols RELIANCE TCS HAL   # does the score predict?
```

Full installation, provider setup and the daily Kite token flow: **`SETUP.md`**.

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

## Code layout

```
run_scan.py              CLI — walks the checklist, emits JSON
run_backtest.py          CLI — walk-forward test of whether the score predicts
market/config.py         thresholds and settings; secrets read from env only
market/indicators.py     causal indicators + confirmed swing structure
market/levels.py         ATR stops, structure-based targets, position sizing
market/score.py          transparent 0-85 model with per-component reasoning
market/backtest.py       path simulation, score-band stats, rank correlation
market/universe.py       candidate list, liquidity gate, regime classifier
market/providers/        yfinance (free, EOD) | kite (paid, intraday + OI) | mock
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

## Two things to understand before trusting an output

**Coverage caps confidence.** A component with no data is dropped from both the
numerator and the denominator rather than scored zero — a missing feed is not a
bad signal. The share of the model that had real data behind it is reported as
`coverage`, and below 55% the conclusion is "insufficient data", not a low score.

**NO TRADE is a valid output.** If nothing clears the score floor, the scanner
says so instead of promoting the least-bad name.

Known limitations — no corporate-action handling, no F&O ban list, no NSE
holiday calendar, no breadth data, no backtest harness, and slippage and charges
excluded from every max-loss figure — are documented in `SETUP.md`. Read them.

This is decision support, not investment advice, and not a guarantee of returns.
