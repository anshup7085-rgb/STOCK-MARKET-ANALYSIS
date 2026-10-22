# Pre-registration — round 3: strategy V (a defined-risk weekly NIFTY iron condor), forward paper

> **DRAFT — not yet binding.** Independent review is in progress. The binding
> version is the later commit that removes this notice; it will land before the
> paper engine observes any real option price.

Drafted **2026-10-22**. It is binding from the commit that removes the draft notice. That commit
comes **before the paper engine records its first observation** of any option
price. Every trade this document judges opens after that commit, so it uses data
that did not exist when the rules were written. The git history of this file is
the evidence of that order.

What the author has seen: no NIFTY option price, chain, implied volatility or
option P&L from any source, live or historical. This project has never fetched
option data. What the author does know: the published literature on index-option
risk premia (section 2), the SEBI study of individual F&O traders, the fee and
contract facts in section 9 (checked by two independent skeptics against search
extracts of NSE circulars on 2026-10-22), and the size and power simulations in
section 13, which use synthetic numbers only.

The rules, parameters, statistic and pass criteria below are fixed. Results are
reported against them as written, including failure. If V fails, it is dead. It
is not re-tuned and re-run with a neighbouring wing width, strike rule or entry
day.

**Paper only.** Nothing here places, modifies or cancels an order at a broker.
The paper engine reads market data and journals simulated fills. Live trading of
V needs everything in section 15 first. Even then it is run by the account
holder, confirming each order.

## Summary

| | |
|---|---|
| Hypothesis 6, **V** | Selling a defined-risk NIFTY weekly iron condor about one week before expiry earns a positive mean P&L after every cost, with fills at the touch |
| Structure | Short call and short put about one straddle-width from the index; long call and long put **100 points further out**. The most it can lose is fixed when it opens |
| Cadence | At most one trade per weekly NIFTY expiry, opened 10:00–14:00 IST on an eligible session, **held to expiry**, cash-settled at the official NIFTY 50 close |
| Size | The largest whole number of lots whose maximum loss, costs included, is **≤ 1% of paper equity** (at most 10 lots). Zero lots means no trade |
| Paper equity | ₹5,00,000 unless the operator sets another amount at `fno init`, before the first entry |
| Statistic | An anytime-valid **betting test** on per-trade return in units of maximum loss, R. It may be checked every week without inflating the error rate (section 12) |
| PASS | The test's wealth reaches **200** (α = 0.005) within 250 settled trades |
| FAIL | Cumulative loss reaches 10% of starting paper equity; or the mean R of the first 60 settled trades is ≤ 0; or 250 trades pass without a PASS |
| Budget | Slot **6** of the 10 lifetime slots, each at α = 0.005 |
| Expected pace | About 50 trades a year. A PASS needs a strong edge, and even a strong one takes about two to three years (section 13) |

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
  further out. The worst case is fixed at entry. It holds through a gap, a halt
  or an outage, without needing a stop order to fill. No naked option is ever
  sold.
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
- **The rules of the market changed in 2024–26**: one weekly index expiry per
  exchange, a larger contract size, upfront premium and expiry-day margin. Any
  premium in the old regime need not carry into this one.

**The author does not expect a PASS.** Section 13 explains why: a forward test
at α = 0.005 can only confirm a large edge, and a large edge is unlikely. The
value of this round is that it is honest, cheap and cannot overfit. Its paper
journal also measures the costs and fill quality that any later F&O idea will
need.

### Futures

Futures are not part of V. A single NIFTY futures lot (contract value around ₹15–20
lakh) on ₹5,00,000 at 1% risk allows a stop only about ₹5,000 ÷ lot size
index points away. At a lot of 65 that is about 77 points, roughly 0.3% of the
index, inside ordinary daily noise. Futures also need a directional signal,
which the project does not have. A futures idea needs its own registration,
with a signal and enough capital for a stop outside the noise.

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
  variables by betting. *JRSS Series B* 86(1). This is the statistic of section 12.

## 3. Instrument and data

- **Instrument.** NIFTY 50 index options on NSE's F&O segment (NFO), underlying
  name `NIFTY` in the contract master. European, cash-settled.
- **Contract master.** Read from the feed on each trading day before the first
  pass: expiries, strikes, lot size, tick size, trading symbols.
  - **No contract fact is hard-coded.** Not the lot size, the strike interval or
    the expiry weekday. Two skeptics could not independently confirm any of the
    three on 2026-10-22.
  - The four legs of a trade must share one lot size. Otherwise there is no trade.
- **Quotes.** Best bid, best ask, the quantity at each where the feed reports
  it, last price, and the exchange timestamp where the feed gives one.
- **Index value.** The NIFTY 50 last value from the same feed, at each pass.
- **Feed.** Zerodha Kite Connect (paid Connect plan) or the Groww trading API
  (paid plan). Every journal record names the feed.
  - Synthetic (mock) data never enters the paper journal. The engine refuses it.
- **Settlement value.** The official NIFTY 50 closing value on the expiry date.
  1. The engine reads it from the feed's daily candle for the index, at or
     after 16:00 IST on the expiry date.
  2. It cross-checks against a second, independent source: Yahoo's `^NSEI`
     close for that date (EOD, delayed).
  3. If the two differ by more than 0.05%, or either is missing, settlement
     waits. The operator enters the closing value NSE publishes, with its
     source; the journal records it as a manual settlement source.
  4. If the feed has the value but the second source never does, after 3
     trading days the feed's value is used and the record is flagged.
  5. A trade still unsettled 10 trading days after its expiry is settled at
     its maximum loss M (section 8). That is the worst case, so no missing data
     can flatter V.

## 4. Calendar and eligibility

- **Trading days** come from `data/nse_holidays.json`. Today that list is
  unverified; each record states its status. An error in it can only move the
  entry by a session.
- **The cycle.** On a session d, the cycle's expiry E is the earliest NIFTY
  option expiry in that day's contract master on or after d.
- **Eligible session.** d is eligible for E if d < E and at least **3** trading
  sessions fall in (d, E], with E counted. For a Tuesday expiry, that makes
  the Wednesday and the Thursday before it eligible.
- **At most one trade per E.** If no eligible session yields a trade, E has no
  trade, and the record says why.
- **No entry on an expiry day**, and none while another V trade with an expiry
  on or after today is open.
  - A trade whose expiry is before today carries no market risk; it is only
    waiting for its settlement value. It does not block a new entry.
  - Its maximum loss M is subtracted from equity for sizing until it settles.

## 5. Entry passes

- An entry pass may run at any time from **10:00 to 14:00 IST**, on an eligible
  session. The runner schedules one every 15 minutes.
- The first pass at which every check in sections 6 to 8 succeeds opens the
  trade.
- A pass that fails any check opens nothing. The journal records the failed
  check. The next pass tries again from scratch.
- The kill switch (`state/paper_fno/KILL` or `state/KILL`) blocks every entry.
  It never closes a position.

## 6. Strikes

At the pass:

1. **Index value.** S is the NIFTY 50 last value.
2. **At-the-money strike.** K_atm is the listed strike for E nearest S that has
   both a call and a put. On a tie, the lower strike.
3. **Expected move.** EM = mid(CE at K_atm) + mid(PE at K_atm), the at-the-money
   straddle, where mid = (bid + ask) / 2.
4. **Short call.** K_sc is the lowest listed call strike ≥ S + EM.
5. **Short put.** K_sp is the highest listed put strike ≤ S − EM.
6. **Long wings.** K_lc = K_sc + W and K_lp = K_sp − W, with **W = 100 index
   points**.

All six options (the at-the-money pair and the four legs) must be listed for E.
Otherwise there is no trade at this pass.

## 7. Quote checks

Each of the six options must pass every check:

- **Two-sided.** bid > 0 and ask > bid.
- **Spread.** ask − bid ≤ max(1.00, 0.10 × mid) index points.
- **Fresh.** Where the feed gives an exchange timestamp, it is no more than
  120 seconds before the pass. Where it gives none, the fetch time is used and
  the record is flagged.
- **Index fresh.** The index value meets the same 120-second test.
- **Size at the touch.** Where the feed reports the quantity at the touch, each
  leg's touch quantity is at least the order quantity: bid quantity for a sell,
  ask quantity for a buy. Where it reports none, the record is flagged
  "touch size unknown".

## 8. Fill, credit and size

- **Fill at the touch.** Paper sells fill at the bid and paper buys at the ask.
  Never at the mid.
- **Credit**, in index points:

  C = bid(K_sc) + bid(K_sp) − ask(K_lc) − ask(K_lp)

- **Credit limits.**
  - C ≥ **0.20 × W = 20.00 points**. Below that, the breakeven win rate is
    above 80% before costs.
  - C < W. Anything else is bad data. It is flagged, and there is no trade.
- **Lot size.** q is the lot size, from the contract master.
- **Maximum loss** M(n) for n lots, in rupees:

  M(n) = (W − C) × q × n + entry costs(n) + 2 × ₹20 × 1.18 + 0.0015 × W × q × n

  - Entry costs come from the fee card (section 9) for the four orders.
  - The last two terms allow for settlement costs: brokerage on two
    in-the-money legs, and STT on a long leg settling up to W in the money.
  - M is the loss if the index settles anywhere up to W beyond a long strike.
    Beyond that, only the settlement STT grows, at 0.15% of the extra
    intrinsic value.
- **Equity.** E_q = starting paper equity + net P&L of settled trades − M of
  trades waiting for a settlement value.
- **Lots.** n is the largest whole number with M(n) ≤ 0.01 × E_q, n ≤ 10, and
  n × q ≤ the contract's freeze quantity where the master gives one.
  - **n = 0 means no trade.** The record holds the arithmetic: 1 lot needs M(1),
    the budget is 0.01 × E_q.

## 9. The fee card

Effective from 1 April 2026. Each rate was confirmed by two skeptics against
search extracts of the primary NSE circulars and the Income Tax Department's
STT page (`market/charges.py`). None has yet been checked against a contract
note, so `verified_on` is null.

| Charge | Options | Futures (not used by V) |
|---|---|---|
| Brokerage, Groww | ₹20 per executed order | ₹20 per executed order |
| Brokerage, Zerodha | ₹20 per executed order | lower of 0.03% or ₹20 |
| STT | 0.15% of premium, sell side; 0.15% of intrinsic value on a long option settled in the money | 0.05% of value, sell side |
| NSE transaction charge (IPFT inside it) | 0.03553% of premium, each side | 0.00183% of value, each side |
| SEBI turnover fee | 0.0001% of premium, each side | 0.0001%, each side |
| Stamp duty | 0.003% of premium, buy side | 0.002%, buy side |
| GST | 18% on brokerage + transaction charge + SEBI fee | same |

**Settlement.** Two charges are not established by the checked sources. Both
are taken the way less favourable to V:

- **Brokerage on a leg that settles in the money**: charged at ₹20 + GST per
  leg.
- **SEBI fee**: charged on the intrinsic value of each in-the-money leg.

No exchange transaction charge is applied on settlement; none was found.
Out-of-the-money legs expire with no charge.

V uses the **Groww** card if the feed is Groww, else the Zerodha card. For
options the two are identical.

## 10. Holding and exit

- **Held to expiry.** There is no profit-take, no stop and no discretionary
  exit. The defined structure is the stop.
  - A profit-taking variant was considered and not registered. It adds orders,
    costs and a dependence on intraday monitoring. A missed pass would change
    its result, but V's result is the same whether or not the runner is
    watching. If it is ever tested, it is a new registration.
- **Settlement.** With P the settlement value (section 3):
  - intrinsic value I(call, K) = max(0, P − K), and I(put, K) = max(0, K − P);
  - gross P&L in points = C − [I(K_sc) − I(K_lc)] − [I(K_sp) − I(K_lp)];
  - net P&L in rupees = gross × q × n − entry costs − settlement costs.
- **Operator flatten.** `fno flatten` closes all legs at the touch: shorts
  bought back at the ask, longs sold at the bid. It is journaled as an
  off-rule exit. **The trade still counts**, at its realised net P&L. A
  trade is never deleted or excluded.

## 11. Risk limits

- At most one open V trade (section 4).
- Each trade's maximum loss M(n) is ≤ 1% of paper equity (section 8).
- **Harm stop.** Once cumulative net realised P&L reaches −10% of starting
  paper equity, V has failed. No further entries.
- The kill switch blocks entries (section 5).
- These limits may only be tightened by the operator, and each change is
  journaled. Loosening any of them is a new registration.

## 12. The statistic

**Per-trade return.** For trade i, in order of expiry:

R_i = net P&L_i ÷ M_i(n_i)

R_i ≥ −1 whenever the index settles within W of a long strike. Below that it
can fall slightly under −1, through settlement STT alone.

**Why not a t-test.** V's returns are negatively skewed. With skewed returns, a
Student or Newey–West t-test passes noise far more often than its nominal rate.
The author simulated it before writing this section: 2,000–20,000 synthetic
condor P&L series per scenario, each with zero true mean, fat-tailed or normal
index moves, and three strike geometries. A two-look t-test (z ≥ 3.09 at 60
trades, z ≥ 2.65 at 150) passed noise **1.4% to 4.1%** of the time. That is 3 to
8 times the intended 0.5%. It is rejected.

**The betting test** (Waudby-Smith and Ramdas). It assumes only that each
R_i ≥ −2. It may be read after every trade with no penalty for looking.

- **Null.** E[R_i | trades before i] ≤ 0 for every i.
- **Wealth.** K_0 = 1, and K_i = K_{i−1} × (1 + λ_i × R_i).
- **Bet size.** λ_i uses only trades 1 to i − 1, with one pseudo-observation of
  mean 0 and second moment 0.25:
  - m = (R_1 + … + R_{i−1}) ÷ i;
  - s = (0.25 + R_1² + … + R_{i−1}²) ÷ i;
  - v = max(s − m², 10⁻⁶);
  - λ_i = min(0.5, max(0, m ÷ (v + m²))).
- **Why it is valid.** λ_i ≤ 0.5 and R_i ≥ −2 keep every factor
  non-negative. Under the null, K is then a non-negative supermartingale.
  Ville's inequality bounds the chance that it *ever* reaches 1/α by α. If a
  factor is ever ≤ 0, K stays at 0 and V cannot pass.

**Outcomes.** Checked after each settled trade, the first to occur decides:

- **PASS**: K_i ≥ **200**, with i ≤ 250.
- **FAIL, harm**: the harm stop (section 11).
- **FAIL, futility**: at i = 60, mean(R_1 … R_60) ≤ 0.
- **FAIL, no evidence**: i = 250 without a PASS.

Stopping early for futility or harm can only lower the false-pass rate.

**Reported, never decided on.** These are labelled descriptive:

- trades, win rate, mean R, mean and total net ₹;
- each trade's breakeven win rate;
- costs as a share of credit;
- the cumulative P&L curve and maximum drawdown;
- fill quality: for each trade, the sum over legs of the distance from fill to
  mid, in points and in R;
- the share of passes that failed a quote check, by check;
- days the runner did not run.

## 13. Size and power, stated before any trade

Simulation, synthetic only. The script is `research/fno_sim.py`; seeds and
counts are in `research/ROUND3_LOG.md`.

- **Model.** Index moves to expiry are normal or Student-t with 3, 4 or 6
  degrees of freedom. Short strikes sit at 0.8 or 1.2 standard deviations, with
  wings 0.26 or 0.5 standard deviations wide. Under the null, the credit is set
  so the true mean P&L is exactly zero.
- **Size.** Over 9 null scenarios, 4,000 runs each, with the test read after
  every trade up to 250 trades, the false-pass rate was **0.03% to 0.35%**
  (limit 0.5%).
- **Power.** 0.8σ shorts, 0.26σ wings, t(4) moves, so the standard deviation of
  R is about 0.56. The table shows PASS within 250 trades, with 1,500 runs each.

  | True mean R per trade | Per-trade Sharpe | PASS | Median trades to PASS |
  |---:|---:|---:|---:|
  | 0.05 | 0.09 | 4% | 122 |
  | 0.10 | 0.18 | 25% | 144 |
  | 0.15 | 0.27 | 68% | 129 |
  | 0.20 | 0.36 | 96% | 101 |

Read plainly:

- A mean of 0.15R is about ₹750 a week on a ₹5,000 risk.
- At ~50 trades a year, a strong edge is found in about two and a half years.
- A modest edge (0.05R) is almost never found.

That is the price of α = 0.005 on a few dozen independent trades a year. It
cannot be bought down without lowering the bar. The faster route is a
historical test on verified option prices (section 16).

## 14. Budget

V spends **slot 6** of 10, at α = 0.005. Slots 1–5 went to the score, A, B, B+A
and T. Section 16 would charge a historical track separately (slot 7).

## 15. What a PASS leads to, and what a FAIL leads to

**PASS.** V has cleared research. Before any live order:

- **Gate 2 for V.** At least 60 settled paper trades and positive mean R.
  Realised fill quality must not be worse than the paper record's own median
  by more than 0.2R.
- **Account holder's setup.** Each item is checked again against the
  broker's current pages:
  - a broker F&O segment;
  - an API plan that places orders;
  - a static IP registered for orders;
  - margin;
  - the daily manual 2FA login.
- **Live code.** Separate, reviewed code for multi-leg F&O orders. Today
  `trading/` has none, by design.
- **Small live.** 0.25% risk per trade with **per-order confirmation** by
  the account holder, as `TRADING.md` sets out for equities. Then the
  ROADMAP's Gate 3.

**FAIL.** V and every variant with a different wing, strike multiple, entry
day or credit floor are dead on this forward data. The journal remains as a
measurement of F&O costs and fills.

## 16. Reserved: a historical track (not registered here)

Broker APIs may offer historical candles for expired NIFTY option contracts
(Groww's backtesting endpoints list expiries and contracts by date). Nobody has
confirmed this; it is not established.

If a verified source exists, a historical track for V may be registered
**by amendment**, before any historical option price is fetched. The
amendment may only add how data is handled:

- source and window;
- an availability-only probe, run first;
- the fill haircut in place of a touch;
- the audit and its stops.

It may not change sections 3 to 12 for the forward track. A historical PASS
would serve as Gate 1 for V and spend slot 7. The forward track continues
either way.

## 17. Fixed and free

- **Fixed.** Every number and rule in sections 3 to 12.
- **Free.** Engineering that changes no rule:
  - feed adapters, retries and logging;
  - the runner's schedule within the windows above;
  - bug fixes.
  
  Each code change is a commit. A fix that would change a past trade's
  numbers is not applied to the past: the journal stays as written, and the
  fix applies from its commit.
- **Free, journaled.** The operator may start the paper book later. Days the
  runner did not run are recorded as such and are not made up.

## 18. Operation

- Each trading morning the account holder mints the day's access token by hand.
  No password, PIN, TOTP seed or API secret is ever stored.
- They then start `python run_trader.py fno run --feed kite` (or `--feed groww`)
  in a terminal multiplexer on a machine that stays on during market hours.
  The runner:
  - makes the entry passes;
  - makes the settlement pass at or after 16:00 IST on expiry days;
  - then exits.
- Paper needs no static IP: the API's read-only calls work from anywhere.
- `python run_trader.py fno status` and `fno evaluate` show the book, the open
  trade, every settled trade and the statistic. `fno verify` checks the
  journal's hash chain.

## 19. Known limitations

- **Paper fills at the touch are optimistic** for size beyond the quantity
  shown. The touch-size check covers this only where the feed reports
  quantities.
- **Partial fills, rejections and margin calls do not exist in paper.**
  - Exchange margin for one lot of a hedged condor is recorded where the
    broker's margin API gives it.
  - Paper equity is far above it, so margin never binds.
- **The settlement brokerage and SEBI-fee readings are conservative guesses**
  (section 9). Other readings would only raise R.
- **The holiday list is unverified**, which can shift an entry by one session.
- **One structure, one width.** A FAIL says nothing about other premium-selling
  structures. It does mean they need their own registration and slot.
