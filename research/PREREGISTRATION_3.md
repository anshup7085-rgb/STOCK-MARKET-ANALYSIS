# Pre-registration — round 3: strategy V (a defined-risk weekly NIFTY iron condor), forward paper

> **DRAFT — not yet binding.** Independent review is in progress. The binding
> version is the later commit that removes this notice; it will land before the
> paper engine observes any real option price.

Drafted **2026-10-22**. Revised **2026-10-23** after an independent review by four
critics (statistics, market mechanics, ambiguity, project policy) and a code
review. It binds from the commit that removes the draft notice. That commit
records this file's SHA-256 in `trading/fno/strategy_v.py`. Before the commit,
the engine refuses every real feed. Every trade this document judges opens after
it, on data that did not exist when the rules were written.

What the author has seen: no NIFTY option price, chain, implied volatility or
option P&L from any source, live or historical. This project has never fetched
option data. What the author does know:

- the literature on index-option risk premia (section 2) and the SEBI study of
  individual F&O traders;
- the fee and contract facts in section 9, checked by two independent
  skeptics against search extracts of NSE circulars on 2026-10-22;
- the simulations in sections 12 and 13, which use synthetic numbers only.

The rules, parameters, statistic and pass criteria below are fixed. Results are
reported against them as written, including failure. If V fails, it is dead. It
is not re-tuned and re-run with a neighbouring wing width, strike rule, entry
day or credit floor.

**Paper only.** Nothing here places, modifies or cancels an order at a broker.
The paper engine reads market data and journals simulated fills. Live trading of
V needs everything in section 15 first. Even then it is run by the account
holder, confirming each order.

## Summary

| | |
|---|---|
| Hypothesis 6, **V** | A defined-risk NIFTY weekly iron condor, sold about one week before expiry and held to expiry, earns a positive mean return per unit of maximum loss (R = net P&L ÷ M). This holds after every cost, with fills at the touch |
| Structure | Short call and short put about one straddle-width from the index; long call and long put **100 points further out**. Held to expiry, the most it can lose is fixed when it opens |
| Cadence | At most one trade per weekly NIFTY expiry. It opens at the first of 17 fixed slots (10:00–14:00 IST) on an eligible session where every check passes, and is held to expiry. It is cash-settled at **NSE's final settlement price** (section 3) |
| Paper equity | **₹5,00,000, fixed.** A book started with any other amount is not this test |
| Size | Risk budget **B = ₹5,000** a trade (1% of the starting equity, fixed). n is the largest number of lots, at most 10, whose maximum loss M(n), costs included, is ≤ B. In practice n is 0 or 1 (section 8) |
| Missed weeks | A cycle whose entry slots were not observed on good data counts as a full loss, R = −1 (section 5). Skipping a week cannot help, except in a week the index settles more than W beyond a long strike |
| Statistic | An anytime-valid **betting test** on R, over every counted cycle in expiry order. It may be read after every trade with no penalty for looking (section 12) |
| PASS | The test's wealth reaches **200** (α = 0.005) |
| FAIL | Any one of: realised loss reaches ₹50,000; mean R of the first 60 items in the sequence is ≤ 0; 250 items, or 300 counted cycles, without a PASS; any R below −2; the rules change; or the test is abandoned |
| Budget | **Forward slot 1 of 10**, in a new forward family, α = 0.005 (section 14). The historical family is untouched: 5 of its 10 slots are spent |
| Expected pace | Unknown. At most about 50 trades a year, and fewer for every week whose credit is below the one-lot threshold (section 8). The realised pace is reported |

## 1. Why this, and why now

Five hypotheses have failed on Indian price history: the scanner's score, A, B,
B+A and T. The 2023–2026 hold-out is spent. Round 2 showed that the free data is
not good enough for another historical test of NSE ETFs. The remaining honest
routes are forward-only tests on data dated after their own commit, or
historical tests on verified data (ROADMAP, step 1a).

The account holder asked for paper trading in options and futures. V is the F&O
idea with the best case behind it, in a form that fits the risk rules:

- **A structural reason to expect a premium.** Index options are, on average,
  priced above the volatility that follows. Option sellers are paid for bearing
  crash risk. This is documented for US index options: Coval and Shumway (2001)
  on short straddle returns; Bakshi and Kapadia (2003) on the volatility risk
  premium; Carr and Wu (2009) on variance risk premia. **This document makes no
  claim that the premium exists in NIFTY weekly options, or that it survives
  Indian costs.** That is the question.
- **Defined risk.** Every short option is covered by a long option 100 points
  further out. Held to expiry, the worst case is fixed at entry. It holds
  through a gap, a halt or an outage, without needing a stop order to fill. In
  paper, and live under the leg order of section 15, no naked option is ever
  held.
- **No signal is needed.** V does not predict direction. The project has no
  validated directional signal, and the scanner's score is retired (Gate 1,
  2026-10-17).

The evidence against V, stated before the result:

- **Most individual F&O traders lose.** SEBI's study (press release, September
  2024) found 93% of individual F&O traders lost money between FY22 and FY24,
  about ₹2 lakh each on average. The study does not isolate defined-risk
  premium selling, but it is the base rate.
- **The wings cost money.** Much of the documented premium sits in far
  out-of-the-money puts, which is exactly what V buys back as protection.
- **Costs are heavy.** Four orders a trade, and since 1 April 2026 STT on sold
  options is 0.15% of premium.
- **Negative skew.** Many small wins, occasional full losses. A short run can
  look profitable by luck. Section 12 chooses its statistic for that reason.
- **The market's rules are reported to have changed in 2024–26.** Two skeptics
  could not both confirm these on 2026-10-22: one weekly index expiry per
  exchange, a larger contract size, upfront premium and expiry-day margin. A
  premium in the old regime need not carry into this one.

**The author does not expect a PASS.** Section 13 explains why: a forward test
at α = 0.005 can only confirm a large edge, and a large edge is unlikely. The
value of this round is that it is honest, cheap and cannot overfit. Its paper
journal also measures the costs and fill quality that any later F&O idea will
need.

### Futures

Futures are not part of V. Take a single NIFTY futures lot (reported, not
confirmed: 65 units and ₹15–20 lakh of contract value) on ₹5,00,000 at 1% risk:

- ₹5,000 must cover both the stop and the costs.
- A round trip costs about ₹970 on the Zerodha card at an index of 25,000,
  mostly the 0.05% sell-side STT.
- That leaves the stop only about 62 index points away, roughly 0.25% of the
  index. That is inside ordinary daily noise, and a gap through it loses more.

Futures also need a directional signal, which the project does not have. A
futures idea needs its own registration.

## 2. References

- Bakshi, G. and Kapadia, N. (2003). Delta-hedged gains and the negative market
  volatility risk premium. *Review of Financial Studies* 16(2).
- Carr, P. and Wu, L. (2009). Variance risk premiums. *Review of Financial
  Studies* 22(3).
- Coval, J. and Shumway, T. (2001). Expected option returns. *Journal of
  Finance* 56(3).
- SEBI (September 2024). Press release on the study of individual traders'
  profit and loss in equity F&O, FY22–FY24.
- Waudby-Smith, I. and Ramdas, A. (2024). Estimating means of bounded random
  variables by betting. *JRSS Series B* 86(1). This is the statistic of
  section 12.

## 3. Instrument and data

- **Instrument.** NIFTY 50 index options on NSE's F&O segment (NFO), underlying
  name `NIFTY` in the contract master. European, cash-settled.
- **Contract master.** Read from the feed once each trading day. The first
  successful read of the day is saved in the state directory and its SHA-256
  is journaled. Every pass that day, in any process, uses that copy. A saved
  copy that does not match its journaled hash stops the engine.
  - **No contract fact is hard-coded**: not the lot size, the strike interval
    or the expiry weekday. Two skeptics could not independently confirm any of
    the three.
  - The four legs of a trade must share one lot size.
- **Quotes.** For each option: best bid, best ask, last price, and:
  - the **touch quantity**: the quantity at the best price only (Kite:
    `depth.buy[0].quantity` for the bid, `depth.sell[0].quantity` for the ask),
    never a total over the book such as Kite's `buy_quantity`;
  - the **quote timestamp**: the time the feed stamps on the quote snapshot
    itself (Kite: `timestamp`). A last-trade time is never used for it. A feed
    whose quote has no snapshot timestamp counts as giving none.
- **Index value.** The NIFTY 50 last value from the same feed, with its quote
  timestamp.
- **Feed.** Zerodha Kite Connect or the Groww trading API, each on a paid plan.
  - Chosen once, at `fno init`. Before the book starts, the feed must pass a
    journaled probe of NIFTY options showing a best bid and a best ask, and a
    quote timestamp on the index or on an option.
  - Fixed for the book. One switch to the other feed is allowed, only after two
    adjacent counted cycles were MISSED because the bound feed's last attempt
    at the first unobserved slot of each failed for want of data. The journal
    names those cycles. The switch is permanent; there is no second switch.
  - Every journal record names its feed.
  - Adding any other data source is a new registration.
  - Synthetic (mock) data never enters the paper journal. The engine refuses it.
- **Settlement value P.** The final settlement price NSE Clearing applies to
  NIFTY index options expiring on E, by whatever method NSE uses for that
  expiry.
  - Today that is the NIFTY 50 closing value on E. A SEBI circular of 15 Oct
    2026 is reported (not confirmed here) to replace it, from 7 Dec 2026, with
    an index value built from constituent VWAPs over the last 30 minutes. This
    document assumes neither method.

  At each settlement pass, in this order:

  1. **NSE's file.** P is derived from NSE's F&O bhavcopy for E, from the
     settlement prices of the expiring NIFTY options: K + settlement price
     for an in-the-money call, K − settlement price for a put.
     - At least four distinct contracts must give exactly the same value,
       to the paisa. Otherwise this source gives nothing.
     - Every column it reads is checked by name.
  2. **The close rule, for expiries before 7 Dec 2026 only.** If NSE's file
     gives nothing, P is the feed's daily index close for E, if Yahoo's
     `^NSEI` close for E agrees with it within 0.10 index points. A quote's
     `ohlc.close` is never used: Kite documents it as the previous session's
     close.
  3. **Checks on P from any source.** P must be a finite positive number.
     Where the feed's daily close for E exists (else Yahoo's):
     - for expiries before 7 Dec 2026, P must lie within 0.10 index points of
       it;
     - for any expiry, P must lie within 3% of it.

     Otherwise settlement waits, and the reason is journaled.
  4. **By hand.** Only after a settlement pass on a trading day after E has
     failed steps 1–3, the operator may supply NSE's F&O bhavcopy file for E,
     downloaded by hand.
     - P is derived from it exactly as in step 1, and its SHA-256 is
       journaled.
     - A typed value is never accepted.
     - Steps 1 and 2 are still tried first at every pass.
     - A hand-supplied file is accepted only where the feed's or Yahoo's close
       for E exists, and P must pass step 3.
  5. **No value.** A trade with no P by the 10th trading day after E is
     settled at its maximum loss, R = −1. That is the worst case, unless the
     index settled more than W beyond a long strike.

## 4. Calendar and cycles

- **Trading days.** Weekdays not listed as NSE trading holidays in the
  committed `data/nse_holidays.json`.
  - `NSE_HOLIDAYS_FILE` is refused.
  - Weekend and evening special sessions (Muhurat trading, Saturday sessions)
    are not sessions for any count here, and no pass runs in them.
  - A year must be covered before its first session: the file must hold that
    year's NSE holiday list, from a named source. Until it does, no entry pass
    runs that year, and the days are journaled as not observed.
  - The file may change during the test only for dates after the day a pass
    first reads the change. The engine journals each version it reads. A
    version that changes a date on or before that day stops the engine, so
    past sessions can never be re-labelled.
  - Today the file holds 2026 only, and it is unverified.
- **The weekly run.** From each day's master, take the NIFTY expiries on or
  after today, starting from the nearest. The run stops at the first gap of
  more than 10 days; the monthly and quarterly contracts lie beyond that gap.
- **Cycles.** Each expiry E in a weekly run is a cycle. E_prev is the expiry
  before it in the union of all weekly runs read.
  - START is the date of `fno init`. A cycle is **counted** if E_prev is on or
    after START, so the first counted cycle is never one already under way.
  - **Eligible sessions** of E: sessions d with E_prev < d < E and at least
    **3** sessions in (d, E], counting E. For a Tuesday expiry, that is the
    Wednesday and Thursday before it.
- **A moved expiry.** If the exchange moves the expiry of an open trade's
  contracts (for example, a holiday declared after entry), the trade's E
  becomes the date the contract master now shows for those contracts. The
  engine re-reads it every day and journals the move.
- **At most one trade per cycle.** No entry on an expiry day.
  - A trade whose expiry has passed carries no market risk. While it waits for
    P, it does not block the next cycle's entry.

## 5. Entry slots and missed cycles

- **Slots.** Entry passes happen only at the **17 fixed slots** 10:00, 10:15, …,
  13:45, 14:00 IST of an eligible session.
  - A pass belongs to slot t if it starts in [t, t + 120 s). Its window is
    judged by t, not by the clock.
  - Only the runner makes entry passes. A pass started by hand, at start-up or
    at any other time does settlement only.
  - Within a slot's window, the runner may retry a pass that failed for want of
    data, up to three attempts. Only the last attempt's failure is journaled.
  - A slot is decided once. A second pass in the same slot does nothing.
- **The trade.** The first slot of the cycle at which every check in sections
  6–8 passes opens the trade.
- **Observed.** A slot is observed if its pass reached the rule checks, whether
  it entered or not.
- **Unobserved.** A slot is unobserved if any of these holds:
  - no pass ran in it (runner absent, crashed or late, machine asleep, or
    another process holding the state lock);
  - the data was unavailable on the last attempt;
  - the pass failed a check of the feed or the machine rather than the
    market, on the last attempt:
    - the index value, or either at-the-money option, was stamped more than
      120 s before T (if the index has no timestamp, the freshest of the six
      option quotes is the test);
    - any quote was stamped more than 5 s after T;
    - an option had no quote at all;
    - the snapshot was slow (section 7), or nothing was dated today;
    - T fell outside the slot's window;
  - the kill switch blocked it;
  - the year's holiday list was missing.

  An unobserved slot is never made up.
- **MISSED.** A cycle is MISSED if any of its slots before its trade, or all of
  its slots if it has no trade, was unobserved.
  - A MISSED cycle opens no trade, even if a later slot would pass.
  - It enters the statistic as **R = −1**. That is no better than any trade
    could have done unless the index settled more than W beyond a long strike
    (section 12), so skipping a cycle can raise the result only in such a week.
- **No trade.** A cycle with every slot observed and no slot passing every
  check has no trade. It is not in the sequence (section 12).
- **The kill switch** (`state/paper_fno/KILL` or `state/KILL`) is for faults
  (feed, machine, journal) and the account holder's absence. It is not for a
  view of the market. It blocks entries and makes the cycle MISSED.
- **Event weeks.** V trades event weeks (Union Budget, RBI policy, election
  results, global shocks) like any other. Its defined maximum loss is its event
  control.

## 6. Strikes

At the pass:

1. **Index value.** S is the NIFTY 50 last value. Its fetch time is the pass
   time T.
2. **At-the-money strike.** K_atm is the listed strike for E nearest S that has
   both a call and a put. On a tie, the lower strike.
3. **Expected move.** EM = mid(CE at K_atm) + mid(PE at K_atm), the
   at-the-money straddle, where mid = (bid + ask) ÷ 2.
4. **Short call.** K_sc is the lowest listed call strike ≥ S + EM.
5. **Short put.** K_sp is the highest listed put strike ≤ S − EM.
6. **Long wings.** K_lc = K_sc + W and K_lp = K_sp − W, with **W = 100 index
   points**.

All six options (the at-the-money pair and the four legs) must be listed for E.
Otherwise there is no trade at this pass.

## 7. Quote checks

- **Two-sided.** Each of the six options has bid > 0 and ask > bid.
- **Spread.** ask − bid ≤ max(1.00, 0.10 × mid) index points.
- **Fresh.** T minus the quote timestamp is ≤ 120 seconds for each option that
  has a timestamp; an option without one is flagged.
  - An older quote on one of the four legs is a market condition (an illiquid
    strike), and the pass is a no-trade.
  - An older quote on the at-the-money pair, which trades continuously, is a
    feed check (section 5), as are the index check and any timestamp after T.
- **Coherent.** All six option quotes are read after S, the last of them within
  10 seconds of T. Otherwise: no trade at this pass ("slow snapshot").
- **Dated today.** The index quote, or at least one option quote, carries a
  timestamp dated today.
- **Clock.** T is the runner machine's clock, which must be NTP-synchronised.
  It is checked against the exchange timestamps at every pass (section 5),
  never taken on trust.

## 8. Fill, credit and size

- **Fill at the touch.** Paper sells fill at the bid and paper buys at the ask.
  Never at the mid.
- **Credit**, in index points:

  C = bid(K_sc) + bid(K_sp) − ask(K_lc) − ask(K_lp)

- **Credit limits.**
  - **Floor: C ≥ 20.00** (0.20 W). Below that, the breakeven win rate is
    above 80% before costs.
  - **Cap: C ≤ 80.00** (0.80 W). Above it, the quotes are treated as bad data:
    flagged, no trade. With W − C ≥ 20, a trade held to expiry falls to
    R = −2 only if the index settles more than about 13,000 points beyond a
    long strike.
- **Lot size.** q comes from the contract master.
- **Maximum loss.** M(n) = (W − C) × q × n + entry costs(n) + settlement
  allowance(n).
  - Entry costs come from the fee card (section 9), for the four orders.
  - The settlement allowance is the section 9 settlement cost with the index
    exactly W beyond a long strike, on whichever side it is larger. That
    covers brokerage plus GST and the SEBI fee plus GST on both in-the-money
    legs, and STT on the long leg.
  - Within that zone, the loss never exceeds M, so R ≥ −1.
- **Lots.** n is the largest whole number with M(n) ≤ **B = ₹5,000**, n ≤ 10, and
  n × q ≤ the contract's freeze quantity where the master gives one.
  - **n = 0 means no trade.** The record holds the arithmetic: 1 lot needs
    M(1), the budget is B.
  - **The binding filter.** At q = 65, one lot fits only when C is at least
    about **25.6** points; at q = 75, about **35.6**. Two lots need C of about
    63 or more. So in practice n is 0 or 1, and the budget, not the 20-point
    floor, is the credit filter that binds.
  - **These thresholds are arithmetic** on the section 9 card, rounded. They
    move with any NSE lot-size revision. B does not change with P&L.
- **Size at the touch.** After n is set, each of the four legs must show at
  least n × q at the touch: bid quantity for a sell, ask quantity for a buy.
  - If any leg shows less, the pass opens nothing. n is not reduced to fit.
  - The at-the-money pair places no order and is not checked for size.
  - A leg whose feed reports no touch quantity is flagged "touch size
    unknown".
- **Arithmetic.**
  - Every comparison in sections 6–8 is exact in decimal: prices are on a
    0.05 tick, so mids and EM are exact to 0.005.
  - Each charge on each order is rounded half-up to ₹0.01 before summing.

## 9. The fee card

Effective from 1 April 2026 (`market/charges.py`). Every statutory and
exchange rate, and Groww's brokerage, was confirmed by two skeptics against
search extracts of the primary NSE circulars and the Income Tax Department's
STT page.

Zerodha's ₹20 per options order is agreed by both skeptics. The entry is still
marked unconfirmed, because they disagree on when Zerodha's ₹40 rate applies (a
debit balance, or a cash shortfall). A paper account never triggers that.
None of these rates has yet been checked against a contract note, so
`verified_on` is null.

| Charge | Options | Futures (not used by V) |
|---|---|---|
| Brokerage, Groww | ₹20 per executed order | ₹20 per executed order |
| Brokerage, Zerodha | ₹20 per executed order | lower of 0.03% or ₹20 |
| STT | 0.15% of premium, sell side; 0.15% of intrinsic value on a long option settled in the money | 0.05% of value, sell side |
| NSE transaction charge (IPFT inside it) | 0.03553% of premium, each side | 0.00183% of value, each side |
| SEBI turnover fee | 0.0001% of premium, each side | 0.0001%, each side |
| Stamp duty | 0.003% of premium, buy side | 0.002%, buy side |
| GST | 18% on brokerage + transaction charge + SEBI fee | same |

**Settlement.** The checked sources do not establish every charge on a leg that
settles in the money:

- **Brokerage**: charged at ₹20 + GST per in-the-money leg, the less
  favourable reading.
- **SEBI fee**: charged on the leg's notional value, max(P, K) × quantity, the
  basis SEBI's regulation uses for exercise and assignment.
- **Exchange transaction charge**: none is known, and none is applied. This
  is the one unestablished settlement charge not taken the less favourable
  way. Reports list it as a limitation.

Out-of-the-money legs expire with no charge. V uses the Groww card on the Groww
feed and the Zerodha card on Kite. For options the two are identical.

## 10. Holding and settlement

- **Held to expiry.** There is no profit-take, no stop and no exit of any kind
  before expiry. The defined structure is the stop.
  - A profit-taking variant was considered and not registered. It adds orders,
    costs and a dependence on intraday monitoring. If it is ever tested, it is a
    new registration.
  - There is **no operator exit** in paper. Paper holds no money, so an exit
    could protect nothing. It would only let a discretionary decision into the
    record.
- **Settlement.** With P from section 3:
  - intrinsic value I(call, K) = max(0, P − K), and I(put, K) = max(0, K − P);
  - gross P&L in points = C − [I(K_sc) − I(K_lc)] − [I(K_sp) − I(K_lp)];
  - net P&L in rupees = gross × q × n − entry costs − settlement costs.

## 11. Risk limits

- At most one open V trade (section 4). Each trade's M(n) ≤ B = ₹5,000.
- **Harm stop.** V fails once the cumulative net P&L of the trades in the
  section 12 sequence reaches −₹50,000 (10% of the starting equity).
- **Fixed.** Every limit in sections 4–12 is fixed for the test. Changing any of
  them, tighter or looser, ends the test at that moment. The verdict is then
  the one already reached, or FAIL if none was.
  - The engine refuses to run with parameters other than those its FNO_INIT
    records.
  - An FNO_ENTRY or FNO_SETTLED carrying a different hash of this text, or of
    the parameters, ends the test in the same way.

## 12. The statistic

**Per-trade return.** R = net P&L ÷ M(n).

- R ≥ −1 whenever the index settles no more than W beyond a long strike.
- Further out, R falls below −1 only through settlement STT and the SEBI fee on
  the extra intrinsic value.
- A PASS is evidence about mean R. Mean net rupees is reported only as
  description.

**Why not a t-test.** V's returns are negatively skewed, and a t-test then
passes noise more often than its nominal rate. The author simulated it before
writing this section (seed 20261022):

- 10,000 synthetic condor P&L series in each of 12 scenarios, with zero true
  mean, normal or Student-t (6, 4, 3 degrees of freedom) index moves, and three
  strike geometries.
- A two-look Newey–West rule (z ≥ 3.09 at 60 trades, z ≥ 2.65 at 150) passed
  noise **1.6% to 4.5%** of the time. That is 3 to 9 times the intended 0.5%.
- Part of the excess is the Newey–West estimator's small-sample bias. Most of
  it is skew: a reviewer's check found a plain Student t-test, which is exact
  on normal returns, still passed 1.1% to 3.8% of condor series.

It is rejected.

**The sequence.** Counted cycles (section 4), in order of expiry:

- a cycle with a trade enters at the trade's R;
- a MISSED cycle enters at R = −1;
- a cycle the rules left without a trade is left out.

The verdict is evaluated only on the longest run from the first counted cycle
in which every cycle is resolved: its trade settled, or the cycle closed. A
cycle that resolves before an earlier one waits its turn. The items are
numbered i = 1, 2, …

**The betting test** (Waudby-Smith and Ramdas):

- **Null.** E[R_i | items before i] ≤ 0 for every i.
- **Wealth.** K_0 = 1, and K_i = K_{i−1} × (1 + λ_i × R_i).
- **Bet size.** λ_i uses only items 1 to i − 1, with one pseudo-observation of
  mean 0 and second moment 0.25:
  - m = (R_1 + … + R_{i−1}) ÷ i;
  - s = (0.25 + R_1² + … + R_{i−1}²) ÷ i;
  - v = max(s − m², 10⁻⁶);
  - λ_i = min(0.5, max(0, m ÷ (v + m²))).
- **Why it is valid.** It needs every R_i ≥ −2. Sections 8 and 10 provide this
  (the credit cap; no operator exit). With λ_i ≤ 0.5, every factor is then
  non-negative, and under the null K is a non-negative supermartingale. Ville's
  inequality bounds the chance that it *ever* reaches 1/α by α.
  - **If any R_i < −2 ever occurs**, V is FAIL ("bound broken"). Setting K to 0
    would not keep the test valid.
  - **MISSED cycles** enter at −1, a fixed value at or below 0, so they keep
    the supermartingale whatever caused them.
  - **Cycles without a trade** are chosen by rules using only information
    available at the pass.

  Neither can raise the false-pass rate.

**Outcomes.** Evaluated cycle by cycle in expiry order, as each joins the run;
the first to occur decides. Within one item the order is:

1. **FAIL, bound broken**: R_i < −2.
2. **FAIL, harm**: net P&L of the trades among items 1 … i ≤ −₹50,000.
3. **PASS**: K_i ≥ **200**.
4. **FAIL, futility**: i = 60 and mean(R_1 … R_60) ≤ 0.
5. **FAIL, no evidence**: i = 250.

After each counted cycle, item or not: **FAIL, no evidence** once 300 counted
cycles have closed without a PASS.

**Once an FNO_VERDICT is journaled, it is the verdict.** A later recomputation
that differs is reported as a discrepancy, never substituted.

Two more FAILs can occur at any point:

- **FAIL, abandoned**: no contract master was read for more than 21 days, or
  the weekly expiry chain has a gap of more than 10 days. Either way the
  counted cycles can no longer be known. (If NSE ever ends NIFTY weekly
  expiries, the second rule ends V.) It decides on its own date: the 22nd day
  without a master, or the day after the gap opens. It applies only to cycles
  that resolve on or after that date.
- **FAIL, the rules changed**: section 11.

The engine stops entering when an outcome is reached, and not before. Stopping
early, for futility, harm, time or abandonment, can only lower the false-pass
rate.

**Reported, never decided on.** These are labelled descriptive:

- the feed and the dates the record covers;
- items, trades and MISSED cycles;
- win rate, mean R, mean and total net ₹, the cumulative P&L and the maximum
  drawdown;
- each trade's credit, M, net, R and breakeven win rate;
- costs as a share of credit;
- fill quality: each trade's fill-to-mid distance in points and in R;
- passes without a trade, by check;
- unobserved slots, by cause; the runner's days;
- the number of trades carrying each data flag: fetch time used for want of a
  quote timestamp, touch size unknown, settlement by hand, settlement on the
  close rule, settlement at −M, and no feed close to cross-check.

## 13. Size and power, stated before any trade

Synthetic only. Every number here comes from the committed `research/fno_sim.py`
at its default seed, 20261022, copied from `scans/r3_ttest.json`,
`scans/r3_size.json` and `scans/r3_power.json`.

- **Model.** Index moves to expiry are normal or Student-t. Short strikes sit at
  0.8 or 1.2 standard deviations, with wings 0.26 or 0.5 standard deviations
  wide. Under the null, the credit is set so the true mean P&L is exactly
  zero. The size nulls use normal, t(4) and t(3) moves; the t-test runs also
  use t(6).
- **Not simulated.** The harm stop, MISSED cycles and cycles without a trade
  are not simulated. Each can only lower the PASS rate.
- **Size.** In 9 null scenarios, 4,000 runs each, with the test read after every
  trade up to 250 trades, the false-pass rate was **0.13% to 0.48%** (limit
  0.5%).
  - The highest, 19 of 4,000 runs (normal moves, 0.8σ shorts, 0.5σ wings),
    is within one Monte Carlo standard error (0.11 points) of the limit.
  - Ville's inequality, not the simulation, is the guarantee.
- **Power.** 0.8σ shorts, 0.26σ wings, t(4) moves; the standard deviation of R
  is about 0.56. The table shows PASS within 250 trades, with 1,500 runs each.

  | True mean R per trade | Per-trade Sharpe | PASS | Median trades to PASS |
  |---:|---:|---:|---:|
  | 0.05 | 0.09 | 3.5% | 115 |
  | 0.10 | 0.18 | 24.9% | 139 |
  | 0.15 | 0.27 | 66.0% | 136 |
  | 0.20 | 0.36 | 94.4% | 98 |

Read plainly:

- **No win rate has been measured.** As arithmetic only: at C = 30 points
  and one lot (M ≈ ₹4,714), a full win is about +0.39R.
  - The breakeven win rate is then about 72%.
  - A mean of 0.15R (about ₹700 a trade, not a week) needs a win rate of
    about 83% if every loss were a full loss.
- **If the true mean were 0.15R**, V would PASS in about two runs out of three,
  after a median of 136 trades. That is about two years and nine months if every
  week traded, and longer for every week with no trade. One run in three would
  not pass at all.
- **A modest edge (0.05R) is almost never found.** That is the price of
  α = 0.005 on a few dozen independent trades a year. It cannot be bought down
  without lowering the bar.
- **Even a PASS does not make V tradable live on ₹5,00,000** (section 15).

## 14. Budget: two families

- **The historical family** (PREREGISTRATION_2 section 13): hypotheses judged
  on Indian prices dated on or before 2026-10-18. Five of its ten slots are
  spent. V's forward test spends none of them.
- **The forward family**, declared here: hypotheses judged only on prices dated
  after their own binding commit.
  - Family-wise error rate 5%, one-sided, by Bonferroni over at most ten
    hypotheses, each at α = 0.005.
  - The cap is fixed and never recounted.
  - **V's forward test is forward slot 1.**

## 15. What a PASS leads to, and what a FAIL leads to

**PASS.** A forward PASS stands in for ROADMAP Gate 1 for V. Gate 1's score
criteria (Spearman, score bands) do not apply to a strategy with no score. This
substitution, and V's Gate 2 below, are put to the account holder and, if
approved, written into ROADMAP before any live order.

- **Gate 2 for V.**
  - At least 60 settled paper trades, journaled in real time.
  - Mean R > 0.
  - `data/nse_holidays.json` verified (`verified_on` set) and covering the
    coming year.
  - Slippage cannot be measured in paper, where fills are at the touch by
    construction. It is first measured live, under Gate 3 (live mean R within
    0.3R of paper).
  - `fno evaluate` is V's evidence. `run_trader.py gates` does not read V's
    journal.
- **Capital.** At the live cap of 0.25% risk per trade, one lot needs live
  capital of at least M(1) ÷ 0.0025.
  - At a lot of 65 and credits of 40 to 20 points (M(1) ≈ ₹4,100–5,400),
    that is about **₹16–21 lakh**.
  - With less, small live sizes to zero lots, and V cannot be traded live.
  - This document does not loosen the live cap to change that.
- **The account holder's setup.** Each item is checked again against the
  broker's current pages:
  - a broker F&O segment;
  - an API plan that places orders;
  - a static IP registered for orders;
  - the daily manual 2FA login;
  - margin, including the expiry-day margin on short index options;
  - that the broker lets a hedged short NIFTY option position be held through
    expiry to cash settlement, with no expiry-day auto square-off. If it does
    not, live V differs from the tested V and needs its own registration.
- **Live code.** Separate, reviewed code for multi-leg F&O orders. Today
  `trading/` has none, by design.
  - It buys both long legs first. A short leg is sent only after the long leg
    on its side has filled, and only if the credit at the actual fills still
    meets section 8.
  - If a long leg does not fill, no short is sent, and any filled long is
    sold.
  - One confirmation by the account holder covers the whole four-leg ticket,
    so no confirmation step can leave a short without its wing.
- **Small live.** One lot only, on capital at which M(1) ≤ 0.25% of equity,
  with per-order confirmation by the account holder as `TRADING.md` sets out.
  Then the ROADMAP's Gate 3.
- **Pace.** A PASS takes a median of about 136 trades. Gate 3 then needs 40
  live trades. No scaled decision on V is possible in less than about three and
  a half years.

**FAIL.** V, and every variant with a different wing, strike multiple, entry
day or credit floor, is dead on this forward data. The journal remains as a
measurement of F&O costs and fills.

## 16. Reserved: a historical track (not registered here)

Broker APIs may offer historical candles for expired NIFTY option contracts.
Groww's backtesting endpoints list expiries and contracts by date, but whether
they reach expired weekly options is not established.

If a verified source exists, a historical track for V may be registered **by a
separate registration**, before any historical option price is fetched. It may
not change sections 3–12 for the forward track.

It spends one **historical** slot (the sixth).

- Its PASS is reported, but does not replace the forward PASS, which every
  live step needs.
- If it FAILs, no live step follows for V, whatever the forward track shows.
  The forward track continues as a measurement.

## 17. The record, binding and changes

- **The journal is the record.**
  - A trade exists once its FNO_ENTRY record is appended. It closes with its
    first FNO_SETTLED record.
  - If the journal holds more than one FNO_ENTRY for one expiry, or more than
    one close for one trade, the first is the record. Any later one is void:
    reported, never evaluated, and it makes the journal NOT EVALUABLE.
  - An FNO_ENTRY is also void if:
    - an FNO_CYCLE for its expiry precedes it;
    - its slot was already decided;
    - it was written more than 5 minutes after its slot began.
  - The FNO_ENTRY is written before its slot's FNO_PASS, so a crash between
    them keeps the trade and marks the slot.
  - A torn last line (a power cut mid-append) is reported and tolerated when
    the next record chains to the last whole one.
  - The engine rebuilds its state from the journal on every pass. `book.json`
    is a readable snapshot only.
- **Binding.**
  - The engine refuses every real feed while this file carries the draft
    notice, or does not match the SHA-256 recorded in
    `trading/fno/strategy_v.py`.
  - It also refuses while the repository has any uncommitted change or
    untracked file, other than `research/ROUND3_LOG.md`.
  - FNO_INIT, FNO_ENTRY and FNO_SETTLED carry this file's hash and the code's
    git commit.
  - A book whose FNO_INIT predates the binding commit is not evidence.
- **One book.** V's paper book is `state/paper_fno` under the repository,
  whatever `TRADING_STATE_DIR` says.
  - `fno init` refuses a state directory whose journal already holds records.
  - **Anchors.** `fno anchor` prints the journal's sequence number and head
    hash, as a line `anchor: seq N sha256 H`.
    - The line is committed to `research/ROUND3_LOG.md` and pushed to GitHub.
      It must cover every FNO_ENTRY and FNO_SETTLED within 7 days, so in
      practice this happens weekly.
    - GitHub's record of when each anchor was pushed is the outside clock.
    - `fno evaluate` checks every anchor against the journal. A mismatch makes
      the journal NOT EVALUABLE.
    - A PASS is reported only if every FNO_ENTRY and FNO_SETTLED is covered by
      an anchor committed within 7 days of it. FAILs need no anchor.
    - Messages to the account holder also carry the head hash. They are a
      second copy, not the anchor.
  - Any other FNO_INIT on real data after the binding commit must be reported:
    a second book, a restart, another state root or a second runner. The
    verdict comes from the first book only. If that book's journal is lost or
    does not verify, V is NOT EVALUABLE, and its slot is spent as a FAIL.
- **Evaluable.** `fno evaluate` gives a verdict only if all of these hold:
  - the journal verifies;
  - every record has mode `fno-paper`;
  - there is exactly one FNO_INIT, carrying the registered hash;
  - no record is void;
  - no day has two contract masters, and every entry pass used its day's;
  - the holiday file matches the last version journaled, for past dates.

  Otherwise it reports NOT EVALUABLE.
- **Silences.** Where sections 3–12 are silent or ambiguous, the behaviour of
  the code at the binding commit is the rule.
- **Bug fixes.** A later change counts as a bug fix only if the old behaviour
  contradicts a sentence of sections 3–12 as written. Its commit message must
  quote that sentence. Any other change to what a pass decides is a rule
  change, and needs a new registration.
  - Every entry pass journals T, S, the master's hash and every quote it read,
    so that old and new code can be replayed on it.
- **Corrections.** The journal is never edited. When a fix shows that a past
  trade's net P&L, M or R was computed wrongly, a correction record is
  appended with the corrected values and the fix's commit. The verdict then
  uses, for that trade, the lower of the recorded and corrected R.
- **Before a PASS is reported**, every item's R is recomputed from the
  journal's recorded quotes, fills and settlement values. The recomputation is
  done by an implementation written from this document without reading
  `trading/fno/`.
  - Every P is re-derived from NSE's bhavcopy for E, fetched again for the
    recomputation.
  - The anchors are checked against GitHub's push history.
  - Where the two implementations disagree, the lower R governs. A
    disagreement on the verdict records the failing outcome.

## 18. Operation

- Each trading morning, before 10:00 IST, the account holder mints the day's
  access token by hand and starts the runner:

  `python run_trader.py fno run --feed kite` (or `--feed groww`)

  Run it in a terminal multiplexer, on a machine that stays on. No password,
  PIN, TOTP seed or API secret is ever stored.
- **The runner**:
  - makes a settlement pass at start;
  - makes the 17 entry-slot passes on eligible sessions;
  - makes settlement passes at 16:00, 16:30 and 17:00;
  - exits.

  On a Tuesday-expiry week, the eligible sessions are the Wednesday and the
  Thursday. **Missing the runner on an eligible morning makes that week a full
  loss in the test.**
- **Static IP.** Zerodha's support page (checked 2026-10-19) says read-only API
  calls need no static IP. For Groww this is unchecked; confirm it before
  relying on it.
- **Weekly.** `fno anchor --append`, then commit and push
  `research/ROUND3_LOG.md`. Or send the printed line to be committed. The
  runner warns when anchoring falls behind.
- **Commands.** `fno status` and `fno evaluate` show the record and the
  statistic. `fno verify` checks the journal's hash chain.

## 19. Known limitations

- **Paper fills at the touch are optimistic** for size beyond the quantity
  shown. The touch-size check covers this only where the feed reports it.
- **Partial fills, rejections and margin calls do not exist in paper.** The
  broker's margin figure for the basket is recorded where its API gives it.
- **Settlement charges are partly guesses.** The brokerage and SEBI-fee
  readings are conservative. The absence of an exchange charge on settlement
  is not.
- **NSE's settlement file is read from NSE's archive.** If the machine cannot
  reach it, settlement waits for the close rule (before 7 Dec 2026) or for an
  entry by hand.
  - The derivation from option settlement prices assumes NSE's file states
    each expiring option's final settlement value. If it does not, the
    four-way agreement check refuses it.
- **The holiday list is unverified.** A wrong list can change which sessions
  are eligible, though not the expiry date, which comes from the master.
- **START is chosen by the operator once**, by when `fno init` runs.
- **One structure, one width.** A FAIL says nothing about other premium-selling
  structures. It does mean they need their own registration and slot.
