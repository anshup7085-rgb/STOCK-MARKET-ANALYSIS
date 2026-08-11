# Roadmap

Where this project is, where it goes, and — the part that matters — what
evidence is required before it touches money.

## Where we are

| Capability | State |
|---|---|
| Indicators, levels, sizing | Verified — 26/26 offline tests, incl. no-lookahead and pivot-confirmation causality |
| Score discrimination | Fixed — R:R 1 → 43 distinct values; regime moved to a scan-level deduction |
| Backtest harness | Built — 752-trade control on random walks returns Spearman +0.021, correctly finding nothing |
| Honest failure | Verified — dead feed gives regime unknown, zero candidates, NO TRADE |
| Market data | **Blocked** — 403 at the egress gateway for every financial host |
| Does the score predict? | **Unknown** — the harness is ready; the answer may be no |
| Sector / breadth | Not built |
| Tradability gates | Not built |
| Execution | Absent by design — read-only |

## The promotion ladder

Four rungs. Each upward step requires **all** of its evidence, not the best two
items of it. Each stage can be demoted if its criteria stop being met.

```
RESEARCH ──gate 1──▶ PAPER ──gate 2──▶ SMALL LIVE ──gate 3──▶ SCALED
   ▲                   │                   │                    │
   └───────────────────┴───────────────────┴────────────────────┘
                      fail your criteria → drop a rung
```

### Gate 1 — research to paper

- Backtest on **real NSE data**, out of sample
- Spearman correlation between score and outcome **> 0.15**
- **n > 300** non-overlapping trades
- Positive expectancy **after** modelled brokerage, STT and slippage
- Higher score bands beat lower ones — not merely the top band beating the mean

### Gate 2 — paper to small live

- **≥ 60 paper trades** logged in real time, with no retrospective edits
- Realised slippage within **0.2R** of the backtest's assumption
- Every tradability gate built and firing: F&O ban list, NSE holiday calendar,
  corporate-action detection

### Gate 3 — small live to scaled

- **≥ 40 live trades** at 0.25% risk per trade
- Realised expectancy within **0.3R** of paper — a wide gap means paper lied
- Maximum drawdown inside the plan
- Correlation checks clean: no month where the book was effectively one bet

These thresholds are set **now**, before results exist. That is the only moment
they can be set honestly; after the first encouraging backtest every one of them
becomes negotiable.

## Horizons

**Now (days)**
- Unblock a market feed, or accept that we are building blind
- Run the backtest on real NSE history and publish the number, whatever it is
- Write the first `catalysts.json` by hand, with sources and dates

**Next (weeks)**
- Sector rotation and breadth, so the regime call stops resting on one index
- F&O ban list, NSE holiday calendar, corporate-action detection
- Portfolio-level correlation and concentration checks
- Universe from 47 names to the full F&O list

**Later (months)**
- Paper-trading loop with real-time logging and no retrospective edits
- Position management: trailing stops, partial exits at T1
- Regime-adaptive weights — only if the backtest justifies them
- Broker authorisation model, per-order confirmation, complete decision log

Ordering note: widening the universe is deliberately late. Widening a scanner
whose ranking is unvalidated just produces more confidently-ranked noise.

## Kill criteria

Deciding in advance what would make us stop is the cheapest risk control
available. Abandon the scoring model as a ranking tool if:

- The backtest on real data shows **no relationship** between score and outcome,
  and no honest variation fixes it.
- Expectancy is positive gross but **negative after costs** — the edge exists and
  belongs to the broker.
- Results hold only with parameters tuned **after** seeing the test set. That is
  a curve fit, not a strategy.
- Live results diverge sharply from paper and the divergence cannot be explained.

A negative result is not a wasted project. It is the most valuable thing this
system can tell you, and it is built to be able to say it.

## The current blocker

No market feed is reachable. Yahoo, NSE and BSE all return 403 to the egress
gateway; a headless Chromium hits the identical wall, and a no-proxy request
still returns 403, so the allowlist is enforced at the network layer rather than
in the HTTP client. This is an environment network-policy setting.

Two ways out:

- **Free** — allowlist `query1.finance.yahoo.com`, `query2.finance.yahoo.com` and
  `fc.yahoo.com`. EOD NSE history, roughly twenty years, enough to run Gate 1.
- **Paid** — Kite Connect, per `SETUP.md` §4. Adds intraday bars and real open
  interest, lifting the derivatives block from UNAVAILABLE. Costs a manual 2FA
  token each morning; do not automate that.
