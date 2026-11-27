# Pre-registration — round 4: V-H, the historical track of strategy V (end of day)

Drafted **2026-10-30**. The account holder read the plain summary and approved it
the same day. **Bound 2026-10-30** by the commit that records this file's SHA-256
in `trading/fno/vh.py`; the code that computes V-H refuses any other text. No
historical option price had been fetched when it was bound.

## What the author has seen

Stated before binding, so a reader can judge what could have shaped the rules:

- **No NIFTY option price from before 2026-10-19, from any source,** except one
  day: NSE's F&O file for the 19 Oct 2026 expiry, read on 23 Oct for amendment 1
  of round 3 (ROUND3_LOG step 11). That cycle lies outside this test's window.
- **Row counts and column names** of 18 past F&O files, 2018–2026 (ROUND3_LOG
  step 12). No price was printed or kept.
- **Participant-wise open interest, 2012–2026**: the FII long share in index
  futures, summarised by year. It holds no price.
- **NSE's all-index close files, 2012–2026**, downloaded on 28 Oct. No value
  from them was printed.
- **NIFTY 50 daily history**, used in rounds 1 and 2 (^NSEI, 2008–2026). The
  author therefore knows the index's broad path, including the 2020 crash.
- **Five days of live option quotes and paper fills**, 26–30 Oct 2026, from the
  explorer and V. All are after this test's window.
- The literature and the cost facts cited in PREREGISTRATION_3 sections 1, 2 and 9.

## Summary

| | |
|---|---|
| Hypothesis 6, **V-H** | Strategy V's rules, applied to NSE's end-of-day files, earn a positive mean R per counted cycle over every NIFTY weekly expiry from 2019 to 13 Oct 2026, after every cost and a fill penalty |
| Why | PREREGISTRATION_3 section 16 reserved this track. V's forward test needs years; NSE's daily files hold every weekly expiry since 2019 |
| Data | NSE's F&O bhavcopy (both layouts) and NSE's all-index close file, public, each file's SHA-256 recorded |
| Window | Every NIFTY weekly expiry E with an entry session in NSE's files and E ≤ 2026-10-18, in expiry order |
| Entry | At the **close** of the first eligible session whose checks pass (section 3) |
| Contract and costs | **Today's** NIFTY lot (65) and **today's** fee card (1 April 2026) for every year (section 4) |
| Fill | Each leg 1% of its price worse than the close, at least 0.25 points (section 4) |
| Statistic | V's betting test, unchanged (section 6). PASS when wealth reaches **200** (α = 0.005) |
| Budget | **Historical slot 6 of 10** (PREREGISTRATION_2 section 13), as PREREGISTRATION_3 section 16 reserved |
| A PASS | Is reported. It does not replace V's forward PASS, which every live step needs |
| A FAIL | **No live step for V, whatever the forward track later shows.** The forward track continues as a measurement |

## 1. What the test can and cannot say

- **It tests V's rule, not V's execution.** Live V enters between 10:00 and
  14:00 on live quotes. NSE's files give one price per contract per day. V-H
  enters at the session's close instead.
- **Closing prices are not trade prices.** There is no bid or ask in the
  files. Section 4 charges a fill penalty on every leg, and section 7 reports
  the result with no penalty and with a harsh one.
- **The market changed during the window.** Weekly NIFTY options began in 2019;
  lot sizes changed several times; one weekly index expiry per exchange from
  late 2024; Tuesday expiries from 2025. Section 4 holds the contract and costs
  at today's values so cycles compare. Section 7 reports each period separately.
  None of this can change the verdict.

## 2. Data

- **NSE's F&O bhavcopy** for every session from 1 January 2019 to 18 October
  2026, from NSE's public archive: the UDiFF file where it exists, otherwise the
  older `fo…bhav.csv` file. A session is a date for which NSE publishes the file.
- **Fields used.** For NIFTY index options (UDiFF: `TckrSymb` NIFTY, `FinInstrmTp`
  IDO; older: `SYMBOL` NIFTY, `INSTRUMENT` OPTIDX): expiry, strike, option type,
  closing price (`ClsPric` / `CLOSE`) and contracts traded (`TtlTradgVol` /
  `CONTRACTS`). Every column is read by name; a missing column stops the run.
- **NIFTY 50 close** for a session: NSE's all-index close file, row
  `Nifty 50`, column `Closing Index Value`. If that file is missing, the
  session's NIFTY options' `UndrlygPric` (UDiFF only), if every NIFTY option row
  shows the same value.
- **Storage.** Each downloaded file's SHA-256 is recorded. Only the NIFTY option
  rows are kept. A file NSE does not serve is listed, never filled in.
- **No other source.** Adding one is a new registration.

## 3. Cycles, entry and strikes

- **Expiries.** The union of every expiry date carried by NIFTY option rows in
  the files. A date is in the **weekly chain** if another date of the union lies
  within 10 days of it, before or after; a date with no such neighbour (a
  quarterly or long-dated expiry) is not. E is a date of the chain; E_prev is the
  chain's date before E. The first cycle is the first E that has an E_prev.
- **Eligible sessions** of E, as in PREREGISTRATION_3 section 4: sessions d with
  E_prev < d < E and at least 3 sessions in (d, E], counting E.
- **Entry.** At the first eligible session d whose checks below all pass, at
  its close. If no eligible session passes, the cycle has no trade and is left
  out of the sequence.
- **Strikes,** as in PREREGISTRATION_3 section 6, with closes for mids:
  1. S is the NIFTY 50 close on d.
  2. K_atm is the strike for E nearest S that has both a call and a put row on
     d. On a tie, the lower strike.
  3. EM = close(CE at K_atm) + close(PE at K_atm).
  4. K_sc is the lowest call strike ≥ S + EM; K_sp the highest put strike
     ≤ S − EM.
  5. K_lc = K_sc + 100 and K_lp = K_sp − 100.
- **Checks on d.** All six options have rows for E on d with a close above 0.
  Each of the four legs traded at least one contract on d (a close with no
  trade that day is a stale price).

## 4. Fill, credit, size and costs

- **Fill penalty.** h(x) = max(0.25, 0.01 × x) points for a leg whose close is
  x. Sells fill at x − h(x), buys at x + h(x), rounded to the 0.05 tick (sells
  down, buys up). A sell that would fill at or below 0 means no trade at d.
- **Credit** C = sells − buys, in points. **Floor C ≥ 20.00, cap C ≤ 80.00,**
  as in V. Outside them: no trade at d.
- **Contract.** q = 65 for every cycle, the NIFTY lot on the day of binding.
- **Costs.** The Zerodha card of 1 April 2026 (`market/charges.py`, FNO_ZERODHA)
  for every cycle, with PREREGISTRATION_3 section 9's settlement charges.
- **Size.** n is the largest whole number ≤ 10 with
  M(n) = (100 − C) × q × n + entry costs(n) + settlement allowance(n) ≤ ₹5,000.
  n = 0 means no trade at d.

## 5. Holding and settlement

- Held to expiry. No exit of any kind.
- **P** is the NIFTY 50 close on E from NSE's all-index file: the final
  settlement price of NIFTY options throughout this window.
- Net P&L and R exactly as PREREGISTRATION_3 sections 10 and 12:
  R = net ÷ M(n).
- A cycle whose E has no NIFTY 50 close in either source is listed and left out.

## 6. The statistic

PREREGISTRATION_3 section 12, unchanged in its bet sizing, null and bounds:

- the sequence is every cycle with a trade, in expiry order;
- wealth K_i = K_{i−1} × (1 + λ_i R_i), with λ_i from items 1 … i − 1 as in V;
- outcomes, first to occur decides, in this order within an item:
  1. **FAIL, bound broken**: R_i < −2;
  2. **PASS**: K_i ≥ 200;
  3. **FAIL, no evidence**: the window ends without a PASS.

**What is dropped from V, and why.** V's harm stop (−₹50,000), its futility rule
(60 items with mean R ≤ 0) and its caps (250 items, 300 cycles) protect a
forward test that runs for years. Here the window is fixed and every cycle
already exists. With them, the oldest market (2019–2020) would decide the
verdict alone. Dropping a stopping rule cannot raise the false-pass rate above
α: Ville's inequality holds at every item. Whether and where the harm stop would
have been crossed is reported (section 7).

**Two independent implementations** compute the sequence (PREREGISTRATION_2
section 19). If they disagree on any trade's R by more than ₹1 of net P&L, or on
the outcome, the failing outcome is recorded and the cause reported.

## 7. Reported, never decided on

Every figure here covers the whole window, including items after the outcome.

- Items, cycles without a trade (by check), cycles left out (by cause).
- The wealth K after every item, and the item, if any, at which V's harm stop
  (−₹50,000) or futility rule would have ended a forward test.
- Win rate, mean R, total and mean net ₹, maximum drawdown, worst ten trades.
- The same figures for each period: Feb 2019–Dec 2020, 2021–2022, Jan 2023 to the
  last expiry before 20 Nov 2024, and the rest of the window.
- The same sequence with no fill penalty, and with the harshest spread V would
  accept, h(x) = max(0.5, 0.05 × x).
- Costs as a share of credit.
- **If minute prices of past NIFTY options are bought later** (for example from
  Dhan), V's own entry slots (10:00–14:00) may be replayed on the cycles they
  cover, as a cross-check of the close-entry approximation. It is labelled
  descriptive and never changes the verdict.

## 8. Order of work

1. The account holder reads the plain summary (done, 2026-10-30).
2. **Binding** (done, 2026-10-30): the commit that records this file's SHA-256
   in the code. The code refuses to run against any other text.
3. The downloader and both implementations are tested on synthetic files.
4. Only then are NSE's files for the window downloaded, on the runner's machine.
5. Both implementations run once. The verdict and section 7 are reported as
   they come out, including failure.

Any change after binding is a new registration that spends a new slot.

---

## Not registered: the opening-range breakout, explored first

The explorer's `future_breakout` is 0 for 3 in paper. Kite serves NIFTY 50
minute candles back to 2015 (checked 29 Oct). Rather than spend a historical
slot on it straight away:

- **Exploration**: 2015-01-01 to 2020-12-31 only. Rules and parameters may be
  varied freely there, and nothing found there counts as evidence.
- **Held back**: 2021-01-01 to 2026-10-18 is not downloaded until a test on it is
  registered (it would spend historical slot 7). If the exploration finds
  nothing worth registering, the held-back years stay unread.
- The index stands in for the future: the explorer trades the near-month
  future, whose intraday moves track the index; the futures cost card applies.
