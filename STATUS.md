# Status: the one page that is kept true (10 Nov 2026)

Read this first. Where another document disagrees with this page, this page is right and that
document is out of date.

## The goal, and the money behind it

- **Goal:** a steady income from intraday NSE F&O. Paper first, then small real money, with the holder
  confirming every live order.
- **Real capital: ₹20,000** (the holder, 10 Nov 2026). Options are preferred; futures only if needed.
- **What ₹20,000 allows.** Selling options or trading a future needs roughly ₹1.5–2.5 lakh of margin a
  lot, and a spread about ₹30,000 or more, so neither fits. What fits is **buying one lot of an option**:
  an at-the-money NIFTY option costs about ₹6,000–11,000 (6 Nov: ₹161 x 65 = ₹10,465), a cheaper out-of-the-money one ₹2,000–4,000.
- **What that means for risk.** One lot is the smallest trade there is. A normal stop on a bought option
  loses ₹1,300–4,000, which is 6–20% of the account in one trade. So the account cannot yet be sized
  safely; real money waits for a tested edge **and** a size the account can carry.
- **The small book** (`config/fno_small.example.json`, from 10 Nov): a paper book of exactly ₹20,000 that
  buys one option lot at a time, with a day's loss limit of ₹2,000. It runs beside the older ₹5,00,000
  explorer and is the part of the evening report that shows what the real account could have done.

## What runs every trading day (the holder types `morning` by 09:10, `deploy/evening.sh` after the close)

| What | Where | State |
|---|---|---|
| Intraday explorer, paper | `trading/fno/explore.py` | Running since 26 Oct. Exploration, not evidence |
| What-if book (every signal, one lot) | `state/fno_whatif` | Running |
| **The small book: ₹20,000, bought options only**, paper | `state/fno_small` | Starts on the next `morning` |
| Strategy V, paper (weekly iron condor) | `trading/fno/strategy_v.py` | Running as a cost and fill measurement only; V never goes live (V-H failed) |
| Market context recorder (FII positions, VIX, option OI) | `trading/fno/context.py` | Running |
| Option minutes of expiring contracts, saved from Kite | `tools/save_expiring_options.py` | Running |
| NIFTY and VIX minutes since 2015, from Kite | `tools/index_minutes_download.py` | Starts on the next `morning` |

## The market brain (from 12 Nov 2026)

A second line of work beside the F&O tests: a system that reads NSE's free daily files, forecasts the next
session as probabilities and scores itself, and trades nothing until a score proves skill. Plan and stages:
`research/BRAIN.md`. Stage 1 (its record on history, `run_brain.py backfill`) is built; its first run on
real data is pending.

## What has been tested, and what it said

| Tested | Result |
|---|---|
| Equity score (swing trades) | **Failed** Gate 1, 17 Oct. Retired |
| Strategies A, B, B+A; T (ETF timing) | **Failed** (T: not evaluable on the data) |
| V-H: weekly iron condor on 2019–2026 | **Failed**, 31 Oct: won 69.5% of weeks and still lost |
| The explorer's nine strategies, replayed on Oct 2021–Oct 2024 | All lose or are flat after costs |
| Opening-range breakout, by future and by bought option | Both fail. Fees take the future's; time decay takes the option's |
| Selling the straddle only when options are dear | Adds nothing outside expiry day |
| **Selling the at-the-money straddle on expiry day** | The one effect standing: positive in all six straddle variants, but small (about ₹17,000 a year a lot) and it needs far more than ₹20,000 |

Nothing has a measured edge. The correct trading output today is **NO TRADE**.

## Next, in order

1. **More data before Dhan's plan ends (4 Dec):** `tools/dhan_download_more.py` (SENSEX, BANKNIFTY,
   FINNIFTY, MIDCPNIFTY weekly options).
2. **Does the expiry-day effect repeat on those indexes?** Rules fixed before their data is read.
3. **The buyer's side,** since buying is what ₹20,000 can do: intraday momentum and the opening gap on
   ten years of NIFTY minutes (`research/INTRADAY_LOG.md` steps 9 and 15), then through bought options
   on Dhan's minutes; and the holder's cousin's own rules, tested exactly as he trades them, once we
   have them.
4. **The events calendar** (RBI, Budget, elections, US Fed).
5. **A daily check of the simulator** against what the paper book really did.
6. **The live order path** only after something passes: today `trading/fno/` cannot place an order.

**Can this reach the goal at all?** The audit against SEBI's studies and our own results: `research/AUDIT_2026-11-10.md`. In short: ₹2,000 a day on ₹20,000 is 10% a day and is not reachable in any market; intraday F&O at this size is very unlikely to pay even a little; four tests remain and finish by about 4 Dec, and the holder decides then.

The full review behind this list: `research/REVIEW_2026-11-10.md`. Every step is logged in
`research/INTRADAY_LOG.md`. What could help and what it costs: `research/OPPORTUNITY_MAP.md`.
