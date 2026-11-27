# Status: the one page that is kept true (27 Nov 2026)

Read this first. Where another document disagrees with this page, this page is right and that
document is out of date.

## The goal, and the money behind it

- **Goal:** Market Lab (`research/BLUEPRINT.md`): a system that finds what really works in the markets,
  with proof, and later earns from it steadily. Paper first, then real money, with the holder
  confirming every live order.
- **Capital:** to be arranged in future, once a strategy has passed its test (the holder, 26 Nov 2026;
  the holder expects to qualify as a Chartered Accountant by the end of 2027). Research is not limited by
  account size: selling options, spreads and futures are all in scope. The size of each trade will be set
  from the capital available then, at about 1% of it at risk a trade.
- **True at any size:** income is capital times a proven edge; no market pays 10% a day.

## What runs (by itself, except the Indian paper books: those run on days the holder types `morning`, agreed for Tuesdays, the NIFTY expiry day)

| What | Where | State |
|---|---|---|
| Intraday explorer, paper | `trading/fno/explore.py` | Running since 26 Oct. Exploration, not evidence |
| What-if book (every signal, one lot) | `state/fno_whatif` | Running |
| Option-buying paper book | `state/fno_small` | Off by default since 26 Nov (`fno explore run --small` turns it on); its replay lost (`research/INTRADAY_LOG.md` step 21) |
| Strategy V, paper (weekly iron condor) | `trading/fno/strategy_v.py` | Running as a cost and fill measurement only; V never goes live (V-H failed) |
| Market context recorder (FII positions, VIX, option OI) | `trading/fno/context.py` | Running |
| Option minutes of expiring contracts, saved from Kite | `tools/save_expiring_options.py` | Running |
| NIFTY and VIX minutes since 2015, from Kite | `tools/index_minutes_download.py` | Starts on the next `morning` |

## The plan from 20 Nov 2026: Market Lab

Agreed with the holder: several markets (India and crypto first, then the US and others), many strategies on
paper, a news lab, the holder's own daily view scored beside the machine's, and one private dashboard.
The plan: `research/BLUEPRINT.md`. Built so far: the dashboard (`dashboard/build.py`, served by
`deploy/dashboard.sh` on the holder's private Tailscale network only), with a themes section and, from 27 Nov, a live-terminal look (live crypto
prices and charts on the phone, a sector heatmap, NIFTY candles; `dashboard/themes.py`, `dashboard/live.js`) and a
DESK page of live order flow (`desk.html`, `dashboard/desk.py`), the events calendar and the human desk (served by
`dashboard/serve.py`).

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
| Crypto futures desk (the idea of a post the holder showed: tree model, trades ranked by profit per minute), five coins, Jan 2022 to Oct 2024 (`research/perps_lab.py`) | **No edge**, 27 Nov: the model does predict, but 1 to 8 bps a trade against 16 bps of costs; it loses at 15-, 60- and 240-minute horizons on every coin. With limit orders and honest fills (`research/perps_maker.py`) it still loses 3.4 bps a trade: closed |
| **Selling the at-the-money straddle on expiry day** | The one effect standing, and on 15 Nov it repeated on all four other indexes tried (SENSEX, BANKNIFTY, FINNIFTY, MIDCPNIFTY), by a rule fixed before their data was read. Still exploration, not a passed test; it is a seller's trade: one lot needs roughly ₹1.5–2.5 lakh of margin. A power check (23 Nov) found a held-back test would pass only about 8% of the time even if the effect is real, so no test slot is spent; it is followed forward on paper |

Nothing has a measured edge. The correct trading output today is **NO TRADE**.

## Next, in order

1. **The expiry-day straddle, followed forward on paper** (Tuesdays, `research/INTRADAY_LOG.md` step 34).
2. **Momentum and the opening gap** on ten years of NIFTY minutes (steps 9 and 15): they need one `morning`.
3. **Market Lab** (`research/BLUEPRINT.md`): the crypto head runs (steps 31 to 44; the futures desk found no
   edge after costs); the themes page is built (27 Nov); next the news lab and the human desk.
4. **The events calendar** is built (27 Nov, `brain/events.py`); next, tag past sessions with it so research can
   ask whether policy and expiry days behave differently. The **human desk** is live on the dashboard: write a view
   before 09:15 IST, scored each evening beside the brain.
5. **A daily check of the simulator** against what the paper book really did.
5a. **Rule changes:** from 7 Dec 2026 expiring index options settle on a 15:00-15:30 VWAP and F&O trades until 15:45
   (reported; `research/RULES_2026-12.md`). Our settlement reads NSE's own price; the option saver was fixed; the live
   path's close time must follow 15:45 before any live use. SEBI's and NSE's originals are still to be read.
6. **The live order path** only after something passes: today `trading/fno/` cannot place an order.

**Can this reach the goal at all?** The audit of 10 Nov (`research/AUDIT_2026-11-10.md`) was written for
₹20,000 of capital, a limit lifted on 26 Nov; its finding that no market pays 10% a day stands at any size.

The full review behind this list: `research/REVIEW_2026-11-10.md`. Every step is logged in
`research/INTRADAY_LOG.md`. What could help and what it costs: `research/OPPORTUNITY_MAP.md`.
