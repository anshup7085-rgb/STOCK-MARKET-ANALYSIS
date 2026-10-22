# Pre-registration — round 2: strategy T (trend timing on a Nifty ETF and a gold ETF)

Drafted **2026-10-18**, committed **2026-10-19**. It is binding from the commit that adds it.
**Amended 2026-10-22, before any round-2 series was fetched** (Amendment 1, at the end): readings an
independent review of the code found the text left open, fixed before the data exists. That commit
must come **before any round-2 series is fetched, frozen or computed on, and
before any round-2 code exists.** The git history of this file is the evidence
of that order.

What the author has seen: round 1's documents and results, and an availability
listing for Yahoo (each series' first bar and row count, and the fact that
LIQUIDBEES's price is flat). The author has seen no price, return, chart or
statistic of `NIFTYBEES.NS` or `GOLDBEES.NS`. No file in `research/cache/`,
`scans/` or `state/` was opened to write this.

The rules, data, window, statistic and pass criteria below are fixed. Results
are reported against them as written, including failure. A hypothesis that
fails is dead on this history. It is not re-tuned, not re-tested with a
neighbouring parameter, and not re-tested on another slice of the same years.

## Why a round 2, and what it can deliver

Round 1 spent four hypotheses and found no edge: the scanner's score (Gate 1),
A (Nifty 50 above its SMA200, else cash), B (12-1 momentum) and B+A
(`reports/2026-10-17-gate1.md`, `reports/2026-10-17-strategies-AB.md`). Its
closing note set the bar: a next test must bring new data or a new kind of
information.

This round brings what is reachable today:

- **A second asset class.** Gold, through GOLDBEES. Round 1 never used it.
- **Investable prices.** ETF closes carry the expense ratio, tracking error and
  any premium or discount. An index hides them.
- **After-tax accounting.** Round 1 charged no tax. An active rule realises
  gains every year; holding defers them.
- **A statistic that knowledge of levels cannot move.** Round 1's Sharpe
  comparisons flipped with the cash rate and depended on the known sign of the
  drift. This round's test does neither (section 1).

It does **not** bring new equity history. NIFTYBEES tracks the index round 1
tested, and 2023–2026 has already been examined. The multiple-testing budget
charges for that (section 13).

**One hypothesis is registered**, though three were allowed. Every slot spent on
this history is spent for good. The other candidates were either predictable
from what the author already knows, or not investable (section 18).

**What profit means here.** Any profit in T comes from owning Indian equity and
gold. The rule only decides when not to own them. In the literature, the main
benefit of trend rules is shallower drawdowns, not higher returns. Over 16.7
years only a large timing effect can be detected (section 14). **The author
expects FAIL.** That is accepted. After four failures, a test that a small or
lucky effect could pass would mean nothing.

## Summary

| | |
|---|---|
| Hypothesis 5, **T** | Hold each of NIFTYBEES and GOLDBEES, in two half-capital sleeves, only while its month-end close is above its 10-month average (Faber 2007). Otherwise that sleeve sits in cash at 0% |
| Null benchmark, **EXP** | The same two ETFs at constant weights equal to T's own average weights, the rest in cash, rebalanced monthly |
| Primary test, **P** | Mean monthly return of T minus EXP, Newey–West t ≥ 2.5758293 (one-sided α = 0.005) |
| Guards, **G1–G8**, all must hold | Both sub-periods; each sleeve; every financial year left out; costs doubled; the 12-month sign rule in place of Faber's; drawdown; stale quotes; after tax |
| Deployment condition, **D** | After tax, T ends no poorer than **MIX** (50/50, rebalanced yearly) on a history where gold's drift is set equal to the Nifty's. D decides paper trading, not the verdict |
| Reported only | BH-N (NIFTYBEES bought and held), MIX against BH-N, cash at 6%, the Zerodha card, ₹10 lakh, the LTCG exemption, Sharpe and α tests, a bootstrap |
| Window | 200 holding months, February 2010 to September 2026, ending at the first session of October 2026. Fixed by date |
| Data | `NIFTYBEES.NS`, `GOLDBEES.NS`, and `^NSEI` for the calendar and one check. Nothing else is fetched |
| α spent | One slot of a lifetime budget of ten, at 0.005 each. Round 1 is charged four |

---

## 1. The question, and why this statistic

Let w_kt be T's target weight in ETF k for holding month t, and w̄_k its mean
over the window. EXP holds w̄_k of each ETF every month, and 1 − Σ w̄_k in cash.
Before costs, the monthly difference between them is

```
d_t = Σ_k (w_kt − w̄_k) · r_kt
```

Its mean is the sample covariance between T's weights and the next month's
returns. That is pure timing. It credits T only for holding more than its
average in months that beat that ETF's own average return, or less in months
that fell short of it. For a given sequence of weights, three things follow
exactly, before costs:

- **Known drift earns nothing.** Adding a constant to every monthly return of
  an ETF leaves the mean of d unchanged, because Σ_t (w_kt − w̄_k) = 0. That
  gold and the Nifty rose cannot make T pass.
- **The cash rate drops out.** The cash weight's deviation also averages to
  zero, so a constant cash rate adds nothing. Round 1's 0%-versus-6% problem
  cannot recur.
- **Missing dividends and expense ratios drop out**, to the extent that they
  are constant.

What it does not neutralise is knowing *when* an asset trended: the path, not
the level. Section 16 deals with that.

Why not the alternatives:

- **A Sharpe difference** (Jobson–Korkie, Ledoit–Wolf). A rule that sits out
  volatile months has lower volatility than its exposure-matched twin, even with
  no timing at all. Whether that raises its Sharpe at a 0% rate depends on the
  sign of the mean return, which the author knows. Reported only.
- **Regression α on the two ETFs.** The same volatility effect pulls β below
  w̄, and α then picks up (w̄ − β) × the mean return: known drift again.
  Reported only.
- **Bootstrap p-values.** They depend on the random-number path, so two
  implementations can disagree at the threshold. Reported only.

EXP's weights are known only after the window. It is the null, not an
investable alternative. The investable comparisons are MIX and BH-N (section 6).
One of them enters the deployment condition D.

## 2. Data

- **Source.** Yahoo Finance via yfinance, through the project's provider
  (`market/providers/yf.py`). Daily EOD bars, delayed. `auto_adjust` is off:
  closes are split-adjusted where Yahoo recorded the split, and **dividends are
  excluded**. Yahoo is the only reachable source.
- **Fetched, and nothing else:**

| Yahoo symbol | Role | First bar (listing) |
|---|---|---|
| `NIFTYBEES.NS` | Indian equity sleeve; BH-N; MIX | 2009-01-02 |
| `GOLDBEES.NS` | Gold sleeve; MIX | 2009-01-02 |
| `^NSEI` | Trading calendar, and the reference check for NIFTYBEES corporate actions. Its prices enter no return | 2007-09-17 |

- **Not fetched**, so never computed on: MON100, JUNIORBEES, BANKBEES,
  PSUBNKBEES, CPSEETF, the gilt, low-volatility, momentum, silver, midcap and
  IT ETFs, LIQUIDBEES, GC=F, INR=X, ^INDIAVIX, and every other index. Data that
  is never downloaded cannot seed a round-3 idea from round-2 numbers.
- **Freeze.** Fetched once, with lookback 10,000, into a new directory,
  `research/cache_r2/`. The SHA-256 of each file goes into
  `research/MANIFEST_r2.sha256` and is quoted in the report. Nothing is
  re-fetched. The round-1 cache and manifest are neither read nor changed.
- **Loading.** Raw cached bars only: the equivalent of
  `research.data.load(adjust=False)`, pointed at `cache_r2`. `load()` with its
  default adjustment is **never** called on round-2 files. Its rule uses opens
  and the old factor list, and running both rules would double-adjust.
- **Stop conditions.** T is reported **not testable**, and nothing is computed
  on it, if either ETF has no genuine row (section 3) in the five calendar
  sessions ending at the last session of January 2009, or if `^NSEI` or either
  ETF has no row dated on or after 2026-10-01. No substitute series is used.

## 3. Calendar, genuine rows, corporate actions, bad data

These steps run in this order.

1. **Calendar.** The dates on which `^NSEI` has a row, Monday to Friday only,
   less any row that `research.data.placeholder_rows()` flags (zero volume and
   OHLC equal to the previous close). ^NSEI volume is not otherwise used.
   - Weekend special sessions (Saturday or Sunday budget days, DR drills,
     weekend Muhurat sessions) are therefore not sessions.
   - Weekday Muhurat sessions are ordinary sessions. A CNC order can be placed
     in them.
   - ETF rows on dates outside the calendar are dropped and counted.
2. **Genuine row.** An ETF row on a calendar session whose close is finite and
   > 0 **and** whose volume is > 0. Yahoo placeholder rows are therefore missing
   bars. **Q_k(d)** is ETF k's close, after step 3's adjustments, on its last
   genuine row on or before session d.
3. **Corporate actions.** A research-only rule; `market/corporate_actions.py`
   is not changed. For each pair of consecutive genuine rows of an ETF, let
   r = close(later) ÷ close(earlier).
   - **Candidate:** |r − 1| ≥ 0.18, and the factor f nearest to r in
     F = {1/2, 1/3, 1/4, 1/5, 1/10, 2/3, 3/4, 4/5, 1/20, 1/50, 1/100} has
     |r/f − 1| ≤ 0.03.
   - **Confirmed** only if both hold:
     - *persistence:* each of the next five genuine closes (all that exist, if
       fewer) lies strictly between 0.9 and 1.111 times the candidate's close.
       A bad print reverts; a split stays.
     - *reference, NIFTYBEES only:* ^NSEI moved by less than 10% in absolute
       value between the same two dates.
   - **Applied:** on a confirmed event, open, high, low and close on every row
     before it are multiplied by f, and volume is divided by f.
   - Unconfirmed candidates are left as recorded.
   - GOLDBEES has no reference among the fetched series. A genuine
     single-session move of 18% or more in gold is not plausible, and
     persistence separates a split from a bad print.
4. **No bad-tick repair.** This is the house rule in `research/data.py`:
   guessing which move is a bad tick would be fabrication. Every move with
   |r − 1| > 0.10 between consecutive genuine rows is listed in the audit, with
   ^NSEI's move over the same dates for NIFTYBEES.
5. **Block.** After step 3, if any ratio r of either ETF is above 1.25 or below
   0.80, for a later row dated from the last session of January 2009 to X_201,
   T is **not evaluable**. There is no manual fix once the data has been seen.
6. **Staleness at a month-end.** If either ETF has no genuine row in the five
   calendar sessions ending at a month-end M that a signal uses, T is not
   evaluable. Execution sessions are covered by the deferral rule in section 4.

## 4. Schedule and window

- **Month-end M:** the last calendar session of a calendar month.
- **Holding month t = 1 … 200** is the calendar month February 2010 + (t − 1).
  t = 200 is September 2026.
- **M_t** is the last session of the month before holding month t. M_1 is the
  last session of January 2010; M_201 is the last session of September 2026.
- **Execution session X_t:** the first calendar session after M_t on which
  **both** ETFs have genuine rows. If that is not one of the first five calendar
  sessions after M_t, T is not evaluable. Every deferral (an X_t later than the
  first session after M_t) is listed. Every line uses the same X_t.
- **Holding month t** runs from the close of X_t to the close of X_{t+1}.
- **The end.** X_201 is expected to be 2026-10-01. Every line is liquidated at
  the close of X_201. The end is fixed by date, not by when the data happen to
  be frozen.
- **Returns:** r_kt = Q_k(X_{t+1}) ÷ Q_k(X_t) − 1.
- **Why these dates.** The ETFs' Yahoo history starts 2009-01-02. Faber's
  signal first exists at the October 2009 month-end, and the 12-month rule of
  guard G5 at the January 2010 month-end. So February 2010 is the first holding
  month in which every rule here is defined. The data set the start, not the
  returns. It excludes 2008, which the author knows favoured trend rules; that
  works against T.
- **Development:** holding months February 2010 to December 2022
  (t = 1 … 155). **Hold-out:** January 2023 to September 2026 (t = 156 … 200).
  This is round 1's split. Nothing is fitted, so the split checks stability. It
  is not blind.
- **Financial years** (1 April to 31 March), for tax and for G3. A holding month
  belongs to the FY that contains its calendar month. The window touches 18 FYs:
  FY2009-10 (2 months), FY2010-11 to FY2025-26 (16 × 12) and FY2026-27
  (6 months).

## 5. Strategy T

**The menu, by a rule that ignores performance.** Broad Indian equity (the
Nifty 50), plus every non-equity asset class with an NSE ETF that a long-only
CNC account could have bought continuously since January 2009. That yields
gold alone:

- the gilt ETFs start in 2016–2018;
- LIQUIDBEES's price is flat;
- sector, size, PSU and factor ETFs are slices of Indian equity.

Where a class has more than one ETF, the one with the longest Yahoo history is
used. That gives NIFTYBEES and GOLDBEES. NIFTYBEES stays in, although round 1's
A, a close relative on the same index, failed. Dropping it would be selection by
a known result.

**Rules.**

1. Two sleeves, k ∈ {N = NIFTYBEES, G = GOLDBEES}, each with half the capital.
2. At M_t: SMA10_k is the mean of Q_k at the ten month-ends M_t, M_{t−1}, …,
   M_{t−9}. ETF k is **in** if Q_k(M_t) > SMA10_k, strictly. This is Faber's
   (2007) rule, unchanged apart from the asset menu.
3. Target weights: w_kt = 1/2 if in, else 0. The cash weight is
   1 − w_Nt − w_Gt, earning 0%.
4. At the close of X_t the book trades to its targets (section 7). An in-sleeve
   is rebalanced back to exactly 1/2 every month, as Faber does. No leverage, no
   shorting, no minimum order.

Faber used total-return series and T-bills. Here the closes exclude dividends
and cash earns 0%. Both are forced by the data, and both cancel in the primary
statistic to the extent that they are constant.

The parameters (a 10-month average of month-end prices, monthly decisions; a
12-month lookback for G5) are the cited papers' as the author knows them. If a
source turns out to differ, the report says so. The rule tested is the one
written here.

## 6. The other lines

Every line starts at the close of X_1 with ₹5,00,000 in cash, uses the same
sessions and the same engine (section 7), and is liquidated at the close of
X_201.

| Line | Definition | Role |
|---|---|---|
| **EXP** | Targets w̄ = (w̄_N, w̄_G), the means of T's targets over the 200 months; cash 1 − w̄_N − w̄_G. Traded to w̄ at every X_t | The null for P and the guards |
| **T12** | T with the signal "in if Q_k(M_t) > Q_k(M_{t−12})", strictly: the 12-month sign rule of Moskowitz, Ooi & Pedersen (2012), without their volatility scaling. Judged against its own EXP12 | Guard G5 only |
| **BH-N** | 100% NIFTYBEES bought at X_1 and never traded until liquidation | Reported: the account holder's do-nothing alternative |
| **MIX** | 50% NIFTYBEES and 50% GOLDBEES bought at X_1, checked once a year (below) | Deployment condition D; reported |

**MIX's annual check.** Annual monitoring with a 5-point threshold, as in
Jaconetti, Kinniry & Zilbering (Vanguard, 2010). The dates are chained so that
every lot MIX sells at a rebalance is long-term.

- Y_0 = X_1. K_i is the first calendar session dated later than Y_{i−1} + 12
  calendar months.
- Y_i is the first session after K_i on which both ETFs have genuine rows. It
  must be one of the first five sessions after K_i, or MIX is not computable.
- At the close of K_i: if max_k |w_k − 0.5| > 0.05, strictly, on Q at K_i, MIX
  rebalances at the close of Y_i. Otherwise nothing trades, and Y_i still
  anchors the next check.
- No check is made whose Y_i would fall on or after X_201.
- At Y_i, with V the value at that close: the overweight ETF sells gross
  S = V_over − 0.5 V, lots first in, first out. Its proceeds net of sell costs
  buy the other ETF. The notional n solves n + buy costs(n) = net proceeds,
  exactly.

**First purchases.** T, EXP and T12 trade to their targets at X_1 like any
other month, paying costs from cash. BH-N buys the notional n that solves
n + buy costs(n) = ₹5,00,000. MIX buys each ETF the same way with ₹2,50,000.

## 7. Accounting engine (every line)

- **Units** are fractional, in float64. Nothing is rounded, rupees included.
- **Value** at session d = Σ_k units_k × Q_k(d) + cash.
- **Monthly lines (T, EXP, T12).** At the close of X_t, let V_t be the value
  before trading (after any tax payment, in after-tax runs).
  - Target units are w_kt × V_t ÷ Q_k(X_t). The net trade in each ETF is one
    order, at that close. Sells come before buys.
  - All costs (section 8) are paid from cash. Cash can go negative by that
    day's costs. It earns and is charged 0%, and the next month's targets apply
    to a V that includes it.
  - This makes each monthly return exactly additive:

    ```
    R_t = V_{t+1} / V_t − 1 = Σ_k w_kt · r_kt − c_t,      c_t = C_t / V_t
    ```

    C_t is the rupees of costs paid at X_t. c_t splits exactly by ETF: c_kt is
    the cost of ETF k's order, its DP charge included.
- **An order exists** when |w_kt − ŵ_kt| > 10⁻⁹, where ŵ_kt is the ETF's weight
  in V_t before trading.
- **Sale proceeds fund buys at the same close.** Settlement is T+1, and whether
  each broker credits 100% of sale proceeds the same day is unverified. The
  forward paper test checks it.
- **Order of operations at a session:** (1) the div-track credit, at month-ends
  only (section 10); (2) the April tax payment, in after-tax runs only
  (section 9); (3) the line's trades, sells before buys.
- **Monthly points** for every line are the values V_t at X_1 … X_201, after
  any tax payment and before that session's trades (V_1 = ₹5,00,000).
  R_t = V_{t+1} ÷ V_t − 1.
- **Daily value series**, for drawdown: ₹5,00,000 at X_1 before trading, then
  the value after that session's trades at every calendar session up to X_201.
  At X_201 the value before liquidation is used.

## 8. Costs (assumptions, unverified; `verified_on = None`)

The binding card is **Groww's**, the dearer of the account holder's two brokers.
Per order of notional V:

| Item | Buy | Sell |
|---|---|---|
| Brokerage | min(₹20, max(₹5, 0.1% × V)) | same |
| Exchange transaction charge | 0.00297% × V | same |
| SEBI fee | 0.0001% × V | same |
| GST | 18% of (brokerage + exchange + SEBI) | same |
| Stamp duty | 0.015% × V | — |
| STT | — | 0.001% × V on NIFTYBEES; 0 on GOLDBEES |
| Slippage against the close | 0.05% × V | 0.05% × V |
| DP charge (GST-inclusive) | — | ₹23.60 per ETF per session on which it is sold |

- **Zerodha card, reported only:** brokerage 0, DP charge ₹15.34, all else the
  same.
- **Costs doubled** (G4): every percentage and every rupee amount above × 2, for
  every line in the comparison.
- On this card a NIFTYBEES round trip of ₹2,50,000 costs about 0.12% plus ₹71,
  or 0.15% in all. Round 1 charged a flat 0.10% per switch.

## 9. Tax (assumptions; verify before relying)

Today's rules, as stated in the brief, are applied to every year of the window
as if they had always been in force. That is a stated simplification. The real
regime changed several times: equity long-term gains were exempt before 2018;
gold was long-term only after 36 months, with indexation, before 2023; and
specified-fund rules applied in 2023–25.

Rates include the 4% cess. Surcharge is ignored.

| Bucket | Rate |
|---|---|
| EQ-ST: NIFTYBEES held ≤ 12 months | 20% → **20.8%** |
| EQ-LT: NIFTYBEES held > 12 months | 12.5% → **13.0%** |
| AU-ST: GOLDBEES held ≤ 12 months (slab, assumed 30%) | 30% → **31.2%** |
| AU-LT: GOLDBEES held > 12 months | 12.5% → **13.0%** |
| Div-track credit (slab) | **31.2%**, deducted from the credit at once |

1. **Lots.** Every buy opens a lot: date, units, and basis = notional + every
   buy cost of that order. Sales take lots first in, first out, by date and then
   by order of creation. A partly sold lot splits pro rata by units.
2. **Gain** on each lot sold = units × close − its pro-rata share of the order's
   sell costs **other than STT** (STT is not deductible) − its pro-rata basis.
3. **Long-term** if and only if the sale date is later than the purchase date
   plus 12 calendar months. A 29 February purchase maps to 28 February.
4. **At each FY end (31 March)**, each bucket's gains and losses for the FY are
   netted. Then:
   - (a) a net short-term loss offsets, in order, AU-ST, EQ-ST, AU-LT, EQ-LT;
   - (b) a net long-term loss offsets AU-LT, then EQ-LT;
   - (c) losses brought forward, oldest first, offset what remains: short-term
     ones in order (a), long-term ones in order (b);
   - (d) unabsorbed losses carry forward with their character for 8 FYs after
     the FY in which they arose, then lapse;
   - (e) tax = Σ of each positive bucket × its rate. There are no refunds.
5. **Payment.** At the X_t that starts each April's holding month, before
   trading, the tax due comes off cash.
   - Monthly lines then trade to their targets on the reduced V_t, which funds
     it.
   - MIX and BH-N, if cash is then negative, sell each ETF in proportion to its
     value, lots first in, first out. Each gross sale G_k is chosen so that its
     proceeds net of its own costs equal that ETF's share of the shortfall,
     solved to ₹0.01.
   - Those sales realise gains in the new FY.
6. **Liquidation** at the close of X_201: everything is sold with sell costs,
   FY2026-27 is closed at once with any losses brought forward, and its tax is
   paid. **After-tax wealth** is the cash that remains.
7. **Binding runs use no ₹1.25 lakh equity LTCG exemption**; the investor is
   assumed to use it elsewhere. The exemption is a sensitivity: ₹1,25,000 off
   EQ-LT after set-off, each FY, in full for the partial FY2026-27.

Each of these choices works, as far as the author can tell, against the line
that realises gains more often. T and EXP realise every month, MIX once a year,
and BH-N only at the end.

## 10. Tracks and transforms

**Price track.** Closes as recorded, dividends excluded. P and G1–G8 use only
this track.

**Div track** (for D, and reported rows). Excluding dividends understates a line
that holds more NIFTYBEES (MIX, BH-N) against one that holds less (T).

- At each month-end session M with X_1 < M < X_201, every line holding NIFTYBEES
  is credited 0.125% (1.5% a year ÷ 12) of its NIFTYBEES value at that close.
- The credit is reinvested in NIFTYBEES at that close, with no costs, as a new
  lot whose basis is the amount reinvested.
- In after-tax runs, 31.2% of the credit is deducted first.
- 1.5% is at the high end of commonly cited Nifty 50 dividend yields. If
  NIFTYBEES in fact retains dividends in its price, this track double-counts.
  The error then works against T.

**REC:** the series as recorded.

**EQ (equal drift):** GOLDBEES's drift over the window is set equal to the
Nifty's.

- s = the number of calendar sessions in (X_1, X_201]. j(d) = the number of
  sessions in (X_1, d] for d after X_1, and minus the number in (d, X_1] for d
  before it.
- g_N = ln(Q_N(X_201) ÷ Q_N(X_1)). On the div track, add 200 × ln(1.00125), one
  credit for each month-end.
- g_G = ln(Q_G(X_201) ÷ Q_G(X_1)).
- Each genuine GOLDBEES close on session d is replaced by
  close × exp(c × j(d)), with c = (g_N − g_G) ÷ s.

Everything is then recomputed on the transformed series: T's signals (month-ends
before X_1 included), weights, trades, MIX's checks, costs and tax. The checks
in section 3 run on the recorded series only.

EQ is two-sided. It removes gold's growth relative to the Nifty in whichever
direction it went, so the author's belief about which grew faster can neither
help nor hurt. It does not change the shape of the path. No one-sided cap is
used.

## 11. The primary statistic

For the full window, n = 200 months:

```
d_t  = R^T_t − R^EXP_t  =  Σ_k (w_kt − w̄_k)·r_kt − c^T_t + c^EXP_t        (exact; section 7)
d*_t = Σ_k (w_kt − w̄_k)·(r_kt − r̄_k) − c^T_t + c^EXP_t                   (same mean as d_t)
d̄    = mean of d_t
γ_j  = (1/n) · Σ_{t=j+1..n} (d*_t − d̄)(d*_{t−j} − d̄)
S    = γ_0 + 2 · Σ_{j=1..L} (1 − j/(L+1)) · γ_j,        L = ⌊4·(n/100)^(2/9)⌋ = 4
t    = d̄ / √(S/n),        one-sided p = 1 − Φ(t)
```

- w̄_k is the mean of T's **target** weights, not its drifted weights.
- r̄_k is the mean of r_kt over the same months.
- Using d* in the variance removes the term that drift would add (a
  drift-weighted autocovariance of the weights) without changing the mean. For
  given weights, numerator and denominator are then both unaffected by a
  constant added to an ETF's returns.
- There is no small-sample factor. Normal critical values are used.

Reported with it: 12·d̄; its 90% interval, 12·(d̄ ± 1.645·√(S/n)); the realised
tracking error √(12·S); and the realised detectable effect at 80% power,
3.4175 × √(12·S) ÷ √(n/12).

**Subsets.** For any set U of holding months (a sub-period, or the window minus
one FY):

- w̄^U is the mean of T's targets over U;
- EXP(w̄^U) is a full-window run of EXP at those weights;

```
d̄^U = mean over t in U of [ Σ_k (w_kt − w̄^U_k)·r_kt − c^T_t + c^{EXP(w̄^U)}_t ]
```

The same definitions apply to T12 against EXP12, and to the runs for G4 and G7.

## 12. Pass criteria

P and every one of G1–G8 must hold. All of them are computed on the price track,
REC, the Groww card, ₹5,00,000, exemption off, cash at 0%.

| # | Criterion | Guards against |
|---|---|---|
| **P** | Full window: t ≥ **2.5758293** (p ≤ 0.005) | chance, across this project's lifetime family |
| G1 | d̄^U > 0 for U = development, and for U = hold-out | an effect from one era |
| G2 | Each sleeve contributes > 0 over the full window: h_k = mean_t [(w_kt − w̄_k)·r_kt − c^T_kt + c^EXP_kt] > 0, for k = N and for k = G (h_N + h_G = d̄). A sleeve that never changes state contributes 0 and fails | one asset carrying the result, for example gold's remembered path |
| G3 | For each of the 18 FYs: d̄^U > 0, with U = the window less that FY's months | one year carrying the result |
| G4 | Full window: d̄ > 0 with every cost doubled (T and EXP rerun; w̄ unchanged) | optimistic costs |
| G5 | Full window: d̄ > 0 for T12 against EXP12 | a result specific to one published signal |
| G6 | \|max DD(T)\| ≤ \|max DD(EXP)\| + 0.05, on daily values | hidden risk |
| G7 | Only if the audit flags either ETF **stale** (section 19, step 5): full-window d̄ > 0 with every X_t replaced by the first session, from the third calendar session after M_t on, on which both ETFs have genuine rows (T and EXP rerun; w̄ unchanged). It must fall by the seventh session after M_t, or G7 fails | stale prints, which flatter trend rules |
| G8 | After-tax wealth of T ≥ after-tax wealth of EXP | timing that does not pay for its tax |

Only P is a test. G1–G8 are point conditions joined by AND. Each can only block
a pass, so none needs an adjustment to α. There is no "promising" tier: p = 0.01
is a fail.

**Deployment condition D.** On EQ, on **both** the price track and the div
track, with the Groww card, ₹5,00,000, exemption off and cash at 0%: the
after-tax wealth of T ≥ the after-tax wealth of MIX. If MIX is not computable,
D fails.

| Outcome | Result |
|---|---|
| P and G1–G8 hold, and D holds | Hypothesis 5 **PASSES**. T is eligible for paper trading (section 20) |
| P and G1–G8 hold, and D fails | Hypothesis 5 **PASSES** as evidence of timing. T does **not** go to paper: holding the same two ETFs without the rule left the investor richer. **NO TRADE** |
| Any of P or G1–G8 fails, or T is not testable or not evaluable | **FAIL. NO TRADE** |

## 13. Multiple testing: a lifetime budget

The family is every hypothesis judged on Indian-market price history dated on or
before 2026-10-18, from any source. Its family-wise error rate is held at **5%,
one-sided**, by Bonferroni over a declared maximum of **ten** hypotheses, each
at α = 0.005.

| # | Hypothesis | Data | Status | α |
|---|---|---|---|---:|
| 1 | Scanner score (Gate 1) | 46 large caps, 2016–2026 | failed | 0.005 |
| 2 | A: Nifty 50 SMA200 filter | ^NSEI, 2008–2026 | failed | 0.005 |
| 3 | B: 12-1 momentum, top 10 | 46 large caps, 2017–2026 | failed | 0.005 |
| 4 | B+A | both | failed | 0.005 |
| **5** | **T** | NIFTYBEES and GOLDBEES, 2009–2026 | registered here | 0.005 |
| 6–10 | reserve | — | unspent | 0.025 |
| | | | **total** | **0.050** |

- **Round 1 is charged**, although its own criteria did not control α. This is
  a convention, not an exact correction. Those four slots are used, they cannot
  be re-tested, and what they taught shaped this document.
- **Thresholds never move.** The cap is fixed at ten, so no later round lowers
  a slot's α or forces a recount. When all ten are used, nothing more is judged
  on this history. New evidence must then come from new data, such as months
  after a registration or point-in-time constituents, and starts a new family.
- **One slot this round.** Holm across several slots would let a filler
  hypothesis lower T's bar. Spending two slots on T would close the history
  faster for a modest gain (critical t from 2.58 to 2.33).
- **Within T there is one test.** The guards and D can only make passing
  harder.
- **Positive dependence** between members of the family makes Bonferroni
  conservative. That is accepted.
- **Reported only:** whether t ≥ 3.0, the hurdle Harvey, Liu & Zhu (2016)
  propose for new factors.
- **Two independent implementations** (section 19). If they disagree on any
  call, the failing outcome is recorded.

## 14. Power, stated before the result

The precision of a mean depends on the span, not on how often it is sampled.
200 months is 16.7 years. Let TE be the annualised tracking error of d. A
one-sided test at α = 0.005 then detects, with 80% power, an information ratio
(12·d̄ ÷ TE) of (2.5758 + 0.8416) ÷ √16.67 = **0.84**, whatever TE turns out to
be.

| True information ratio | 0 | 0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.84 | 1.0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Chance that P passes | 0.5% | 4% | 9% | 17% | 30% | 45% | 80% | 93% |

TE itself is an **assumption**, used only in this section. It assumes each
sleeve is in about 70% of months, NIFTYBEES volatility of 16–22% a year,
GOLDBEES of 13–17%, and no correlation between them:

| | Low | Central | High |
|---|---:|---:|---:|
| TE of d | 4.7% | 5.4% | 6.4% |
| Standard error of 12·d̄ | 1.2% | 1.3% | 1.6% |
| Detectable at 80% power | 4.0% a year | 4.5% | 5.3% |
| One-sided 95% upper bound if the estimate is zero | 1.9% | 2.2% | 2.6% |

**What the literature suggests.** The author reads the long-sample trend
evidence (Moskowitz, Ooi & Pedersen 2012; Hurst, Ooi & Pedersen 2017) as an
information ratio of roughly 0.2–0.4 per asset, for a long-or-flat rule against
an exposure-matched benchmark. Two weakly correlated assets might reach 0.3–0.5.
Huang, Li, Wang & Zhou (2020) argue that even this is statistically fragile. On
that reading P passes 9–30% of the time **even if trend timing works as
published**, and G1–G8 and D lower that further. The guards cost little for an
effect spread across both sleeves and all years. They cost a great deal for an
effect concentrated in one sleeve or one year, which is their purpose.

So:

- **A pass** means an effect of about 4–5% a year or more, or luck at the 0.5%
  level. Because power is low, the estimate on a pass is biased upward (the
  winner's curse; Gelman & Carlin 2014). The forward effect should be expected
  to be well below the backtest's.
- **A fail with an estimate near zero** rules out large timing value, above
  about 2–3% a year. It says little about modest value.
- **A fail with p between 0.005 and 0.05** is a fail.

## 15. Metrics, and what is reported only

For T, EXP, T12, BH-N and MIX, for the full window, development and hold-out.
Sub-periods are slices of the full run, with drawdowns measured on the slice.

- **From the monthly points** (identical for every line):
  - CAGR = (V at the period's last point ÷ V at its first)^(12 ÷ months) − 1,
    where the full window's last point is V_201, before liquidation;
  - volatility = sd(R_t, ddof = 1) × √12;
  - Sharpe = mean ÷ sd × √12, at 0% for relative comparison only, and also at
    a constant 6% a year (1.06^(1/12) − 1 a month).
- **From the daily series:** maximum drawdown; the longest drawdown in sessions
  (from a peak to the first session at or above it, or to the end);
  MAR = CAGR ÷ |max DD|.
- **Activity:** the share of months each sleeve is in; switches a year;
  turnover a year; orders; costs in rupees.
- **After tax:** wealth after liquidation, and CAGR = (that wealth ÷
  ₹5,00,000)^(12/200) − 1; tax by bucket and by FY; losses lapsed; the value
  before liquidation.
- **Calendar-year returns,** with the first and last years marked partial.
- **The primary statistic block** (section 11), each sleeve's contribution, and
  d̄ by FY.

**Reported only, never used for a pass or a fail:**

- Every line's after-tax wealth under REC and EQ, on the price and div tracks.
- One-at-a-time sensitivities from the binding cell: the Zerodha card;
  ₹10,00,000; the exemption; costs doubled; cash at 6% (credited monthly on the
  cash left after the trades at X_t and, in after-tax runs, taxed at 31.2% in
  its FY).
- MIX against BH-N, and the **breakeven relative drift**. Let Δ be MIX's
  after-tax CAGR minus BH-N's, under EQ on the price track. MIX then roughly
  matches BH-N after tax as long as gold grows no more than 2Δ a year slower
  than the Nifty. This is arithmetic, not a forecast.
- The Ledoit–Wolf (2008) Sharpe difference, T against EXP. A regression of R^T
  on r_N and r_G (α, β, Newey–West t).
- A stationary bootstrap (Politis & Romano 1994) p-value for d̄: mean block 6
  months, 10,000 resamples, `numpy.random.default_rng(20261018)`.
- Whether t ≥ 3.0.

**Not computed at all:** other moving-average lengths, other lookbacks, a
1/3/12-month blend, volatility scaling, other menus (MON100 included), other
weights, other start dates. Round 1 showed that computing neighbours invites
choosing one.

## 16. Contamination disclosure

The author has not opened any round-2 series, any cache file, `scans/` or
`state/`, and has written no round-2 code. Channel by channel:

| What the author knows | Could it help T pass? | What the design does |
|---|---|---|
| **Levels.** The Nifty and gold in rupees both rose a great deal from 2009; the rupee fell | No | P and G1–G5 give no credit for an ETF's average return (section 1). G8 compares lines with the same average exposure. D uses two-sided EQ, and a known positive drift only makes D harder, because T is partly in cash |
| **Gold's path, in outline.** Rose 2009–12; fell 2013–15; flat to 2018; rose 2019–20; eased 2021–22; rose very strongly 2023–26 | **Yes.** A trend rule plausibly sidestepped part of 2013–15 and held 2023–26 | G2 needs the NIFTYBEES sleeve to contribute too. G3 removes any one year. Only the canonical rule is used. Nothing neutralises the rest |
| **The Nifty's path.** The 2011 and 2015–16 drawdowns; the 2020 crash and V-shaped rebound (usually a whipsaw loss for trend rules); the 2021 rally; 2022; the 2024–25 correction | Mostly against T | Stated. 2008, the best-known trend win, falls outside the window because the data start in 2009 |
| **Round 1.** ^NSEI buy-and-hold Sharpe 0.60; A's daily SMA200 invested 71% of the time, CAGR 6.2% against 10.4%, max DD −25.5% against −45.4%; SMA150 looked better and SMA250 worse; cash at 6% lifted A; the 2023 onward hold-out figures; Gate 1's results by year | Against T | Round 1's figures suggest the 200-day filter added little or nothing on the Nifty beyond its average exposure. **The author therefore expects the NIFTYBEES sleeve to fail G2, and T to FAIL** |
| **Instrument facts.** LIQUIDBEES pays its return as units; MON100 traded at premiums when overseas limits bound; the author believes, without having checked, that NIFTYBEES and GOLDBEES re-denominated their units around 2019–20 | Not through returns | MON100 is not fetched. That belief is the only reason for the factors 1/20, 1/50 and 1/100 and for the persistence check. The audit shows what happened |
| **The literature's results in other markets** | It is the prior, not data | Canonical published parameters only: 10 months, 12 months, monthly decisions |
| **Typical volatilities** | Only through the power statement | Section 14 is labelled an assumption |

**Residual risk, stated plainly.** The author has a vague sense that trend rules
"would have done well" on gold. No benchmark neutralises knowledge of a path.
G2 stops it from carrying a pass on its own, but it can still tilt a borderline
result. That is why a pass leads to paper trading, not to money.

## 17. Known limitations, stated before the result

- **Power is the dominant limitation** (section 14). Expect FAIL.
- **One path:** one country, two assets, 200 months. The hold-out is not blind.
- **Dividends are excluded.** For a constant yield this cancels in P. The div
  track's 1.5% is an assumption, and NIFTYBEES's distribution history is not
  known to the author.
- **Cash earns 0%.** That cancels in P and G8. It biases D against T by roughly
  T's average cash share × the cash rate. The 6% rows show the size.
- **An ETF's price is not its NAV.** Early thin trading and stale prints are in
  the returns; the block rule, the audit and G7 catch some of it, not all.
- **GOLDBEES tracks the domestic gold price,** import duty and GST included.
  Changes to those are policy moves, not market moves.
- **Yahoo data** may hold bad ticks below the block threshold, and ^NSEI may
  miss a session.
- **Costs and tax are unverified assumptions.** Today's tax law is applied to
  every year. The 30% slab, the ignored surcharge, the 8-year lapse and no
  refunds are stated simplifications. The after-tax results depend on account
  size, through the fixed charges and the exemption; ₹5,00,000 is binding.
- **Fills are at the close,** in fractional units, with no market impact.
  Same-day use of sale proceeds is assumed.
- **EXP's in-sample weights bias the statistic slightly against T**, by about
  1/n × the covariance between returns and later weights. Accepted as
  conservative.
- **Normal critical values** are used on 200 months of possibly fat-tailed
  differences. G3 guards against a single dominant year.
- **Mild survivorship among instruments.** These are ETFs that still exist, and
  the oldest of their kind.
- **A month-end rule cannot react inside a month.** A fast crash does its damage
  before the rule moves. That is the published rule, not a flaw in the test.
- **No contributions or withdrawals** are modelled, and only drawdown is
  measured, not whether an investor would sit through it.

## 18. Considered and not registered

| Candidate | Why not |
|---|---|
| A static 50/50 Nifty–gold mix, or one-third each with the Nasdaq-100, rebalanced yearly, against BH-N | A static mix against one of its own components is a claim about levels and second moments: gold's low correlation with the Nifty, and gold holding up in 2011 and 2020. The author knows these, so the outcome is largely predictable. Drift transforms remove average growth, not volatility, correlation or the timing of crashes. MIX is reported as arithmetic only, with no claim of edge |
| MON100 in any line | Premium-to-NAV regimes while overseas limits halted creation; history only from 2011; a narrow index; a run the author remembers. Not fetched |
| Volatility-managed Nifty exposure (Moreira & Muir 2017) | Cutting exposure in high-variance months makes both a Sharpe test and a regression α depend on the sign of the drift, which is known. Real-time versions rarely survive (Cederburg et al. 2020) |
| 12-month time-series momentum with volatility scaling (Moskowitz, Ooi & Pedersen 2012) | At a 40% volatility target capped at full exposure, the scaling barely binds for these ETFs, so it is the 12-month sign rule. Used as guard G5 |
| Trend timing on NSE sector indices | Not investable (no ETF history). Under Holm, a research-only pass would lower T's bar |
| Any variant of A on the Nifty alone | The neighbour of a dead hypothesis, on the same series |
| Dual or relative momentum across two or three ETFs | Not a cross-section; it would need MON100 |
| Longer history from proxies (^CNX200 from 2004, ^CRSLDX, GC=F × INR=X) | It brings 2005–08 into the window, the episode the author knows favoured trend rules. The proxies are not what the account trades |
| A bond sleeve (SETF10GILT from 2016, LTGILTBEES from 2018) | Too short. Adding it part-way would be a second, contaminated choice |
| Halloween, turn of the month | The author can reconstruct much of the seasonal path. Both realise short-term gains every year |
| Two α slots for T | It closes this history faster, for a modest gain in power |

## 19. Sequence

1. **Commit this file.** `research/cache_r2/` must not exist, and no round-2
   code may exist. ROADMAP step 1a gives the account holder approval of the
   hypothesis. The report records whether it was given before the fetch.
2. **Code, with no data.**
   - `research/data.py` accepts a cache directory. Round-1 behaviour is
     unchanged.
   - A round-2 loader that reads raw bars from `cache_r2` and applies section 3
     with its own factor list; and `research/strategy_t.py`, with the engine,
     written from this text alone.
   - **Unit tests for causality:** nothing trades on its signal session; a
     signal at M uses no price after M; EQ uses only its two anchor closes.
   - **Size check, which must pass.** 2,000 synthetic datasets, generated in
     order from `numpy.random.default_rng(20261018)`. Each has:
     - two ETFs on a Monday-to-Friday calendar from 2009-01-01 to 2026-10-09,
       starting at 100, with volume 1 every day;
     - daily log returns μ + σ_t·ε_t, with μ = 0.10/252 for one ETF and
       0.12/252 for the other;
     - GARCH(1,1): σ²_t = ω + 0.08·(σ_{t−1}ε_{t−1})² + 0.90·σ²_{t−1}, with ω
       set for an unconditional volatility of 18% and 15% a year, and σ²_0
       equal to it;
     - ε independent Student-t with 5 degrees of freedom, scaled to unit
       variance.

     The full pipeline (costs, ₹5,00,000) runs on each, and P is applied. The
     false-pass rate must be at most 1.0%, that is 20 or fewer. If it is not,
     the code is wrong or the statistic is mis-sized. Nothing is fetched until
     it is fixed, and any change to this document is a dated amendment,
     committed before the fetch.
   - **Power check, reported only.** The same, except that each ETF's daily
     drift is raised by a constant while its signal is in and lowered by the
     same constant while it is out, with the constant set so that the
     information ratio of d is about 0.84. About 80% of runs should pass P.
3. **Gates.** The allocation gates in section 20 are put to the account holder.
   If approved, they are committed to ROADMAP before step 6.
4. **Fetch and freeze** the three series, once, and write
   `research/MANIFEST_r2.sha256`. The provider passes `.NS` and `^` symbols
   through unchanged.
5. **Publish the data audit**, committed before any strategy run. Per series:
   - first and last bar, and rows;
   - rows dropped (weekend, off the calendar, placeholders, zero volume);
   - every corporate-action candidate, and whether it was confirmed;
   - every move above 10%, and the block check;
   - every deferral;
   - per FY, over the calendar sessions from X_1 to X_201, each ETF's share of
     sessions with no genuine row, and its share of genuine rows whose close
     equals the previous genuine close. Either share above 10% in any FY flags
     that ETF **stale**, which switches on G7.
6. **Run once.** Every number in section 15 goes into `reports/<date>-round2.md`,
   with commit hashes, fetch and run times in UTC, the manifest, the audit, and
   every criterion, pass or fail. The report says "EOD, delayed" out loud.
7. **Two independent rebuilds**, each given only this file and the frozen
   cache, without reading the round-2 code. Where the text proves ambiguous,
   the reading less favourable to T governs, and is recorded. On any
   disagreement in a pass or fail call, the failing outcome stands.
   - Each rebuild must reproduce **P**. It must reproduce G1–G8 and D only if P
     passes in the primary run and in both rebuilds. A P that fails anywhere is
     a FAIL whatever the rest shows, so nothing further can change the verdict.
8. **Nothing changes after a result:** no parameter, date, cost, tax
   assumption, track or criterion. Ambiguities found before step 4 are fixed by
   dated amendment and listed in the report.

## 20. What a pass leads to

A pass with D makes T eligible for **paper trading only**. It is not a forecast.
Given the winner's curse, the backtest number overstates the edge.

Today `trading/` runs long-only CNC swing trades, with a stop and a target on
every position, and has no rebalance mode. T needs the following, each as its
own PR with tests:

1. **An allocation mode.** Monthly targets become whole-unit orders on X_t,
   sells first, worked between 15:00 and 15:30 as LIMIT orders within a collar
   of the last price, leaving less than one unit of cash per ETF. The signal
   inputs, targets and orders are journaled on the existing hash chain.
2. **A position type with no stop and no target, by design.** It is recorded as
   such, so the protection check does not report it UNPROTECTED every cycle. The
   rule is the risk control.
3. **A separate, journaled allocation policy, approved by the account holder.**
   - The swing limits would block T: a 20% position cap against 50% sleeves; a
     −2% daily-loss breaker that an ordinary index day can trip; a −10%
     drawdown breaker.
   - **They are not loosened.** The allocation policy has its own: an
     allow-list of NIFTYBEES and GOLDBEES; orders on scheduled sessions only;
     post-trade weights at target; no leverage and no shorts; a kill switch that
     blocks buys but never blocks a scheduled exit to cash.
4. **ETF fee schedules** in `market/charges.py`: 0.001% STT on equity-ETF
   sells, none on gold ETFs and none on buys. Verified against a contract note,
   then `verified_on` is set.
5. **A FIFO tax-lot ledger** implementing section 9.
6. **A check, at each broker, that sale proceeds fund a buy in the same
   session.** If not, buys go in the next session and the gap is journaled.

**Proposed allocation gates.** These are for the account holder's approval.
ROADMAP's swing gates stay unchanged: Spearman tests and 60-trade counts cannot
apply to a book that trades once a month.

- **Gate 1-A:** this document's PASS with D, with both rebuilds agreeing.
- **Gate 2-A (paper → small live):**
  - at least 12 consecutive monthly paper rebalances by `cycle --mode paper`,
    journaled in real time;
  - every target identical to the research code's on the same bars;
  - a mean absolute monthly difference between paper and model returns of at
    most 0.10%;
  - realised costs at most 2 × the model;
  - the journal intact, and the NSE holiday calendar verified.

  Forward returns are reported, not used as a hurdle: twelve months carry no
  statistical information. Demotion: a forward maximum drawdown worse than
  EXP's over the same dates by more than 5 points sends T back to research.
- **Gate 3-A:** at least 12 live rebalances, every order confirmed by the
  account holder in their own shell; at most 25% of the account in T; mean
  slippage against the close of at most 10 basis points; tax lots that
  reconcile with the broker's tax P&L. Only then does the account holder decide
  on scale.

**Pace, honestly.** The earliest Gate 2-A is about twelve months after paper
trading starts, and Gate 3-A a year after that. Forward data arrives at twelve
observations a year. It checks the machinery and the costs. At the effect sizes
in section 14, confirming a monthly timing rule statistically would take well
over a decade.

## 21. What a fail leads to

T is dead on this history, and so are its variants: T12, other averages and
lookbacks, other menus, weights and start dates. Systematic timing then has no
measured edge in this project, and the output stays **NO TRADE**. Five slots
remain.

A fail says nothing either way about holding a static mix of ETFs. MIX is a
reported baseline with no claim of edge. Holding it, or a Nifty ETF untouched,
is an investment decision for the account holder, outside this research. Any
further idea, the static mix included, goes to a forward-only registration, on
data dated after its own commit.

## References

- Cederburg, O'Doherty, Wang & Yan (2020), "On the performance of
  volatility-managed portfolios", *Journal of Financial Economics* 138(1).
- Faber (2007), "A quantitative approach to tactical asset allocation",
  *Journal of Wealth Management* 9(4).
- Gelman & Carlin (2014), "Beyond power calculations: assessing Type S (sign)
  and Type M (magnitude) errors", *Perspectives on Psychological Science* 9(6).
- Harvey, Liu & Zhu (2016), "…and the cross-section of expected returns",
  *Review of Financial Studies* 29(1).
- Huang, Li, Wang & Zhou (2020), "Time series momentum: is it there?",
  *Journal of Financial Economics* 135(3).
- Hurst, Ooi & Pedersen (2017), "A century of evidence on trend-following
  investing", *Journal of Portfolio Management* 44(1).
- Jaconetti, Kinniry & Zilbering (2010), *Best Practices for Portfolio
  Rebalancing*, Vanguard.
- Ledoit & Wolf (2008), "Robust performance hypothesis testing with the Sharpe
  ratio", *Journal of Empirical Finance* 15(5).
- Moreira & Muir (2017), "Volatility-managed portfolios", *Journal of Finance*
  72(4).
- Moskowitz, Ooi & Pedersen (2012), "Time series momentum", *Journal of
  Financial Economics* 104(2).
- Newey & West (1987), *Econometrica* 55(3); the lag rule ⌊4·(n/100)^(2/9)⌋ is
  Newey & West (1994), *Review of Economic Studies* 61(4).
- Politis & Romano (1994), "The stationary bootstrap", *Journal of the American
  Statistical Association* 89(428).

---

## Amendment 1 — 2026-10-22, before the fetch

An independent review compared the round-2 code with this text before any
round-2 series existed (`research/cache_r2/` absent). Section 19 allows changes
only by dated amendment before step 4; these are they. Each either pins a
reading the text left open, so that two implementers get the same numbers, or
adds a rule for a case the text did not cover. None changes the hypothesis, the
statistic, a threshold or a criterion.

**Data (sections 2–3)**

1. **Step 0, duplicate dates.** Before anything else, rows of a fetched file that
   share a date are reduced to the last such row in file order. The others are
   dropped, counted and listed in the audit with their OHLCV.
2. **Calendar (3.1).** `placeholder_rows()` runs on ^NSEI as fetched, weekend rows
   included; weekend rows are dropped after it. The audit also lists the dates
   the other order would flag.
3. **Corporate actions (3.3).** Candidates, the persistence test and the ^NSEI
   reference test all use the recorded closes of the genuine rows, as fetched. No
   close is adjusted for another event before these tests, and no candidate is
   re-detected after an adjustment, so a later candidate inside an earlier one's
   five-close window counts against the earlier one's persistence. After every
   candidate is classified, each confirmed factor applies to all rows before its
   event, cumulatively.
4. **Drop categories (3.2, audit).** Each ETF row falls in the first that applies:
   weekend; weekday off the calendar; placeholder (`placeholder_rows()` on the
   ETF's on-calendar rows); zero volume; close not finite or not above 0. What
   is left is genuine.
5. **A failed fetch (2).** A fetch that does not return all three series writes
   nothing, freezes nothing and may be repeated, at most three attempts in all,
   each logged with its time. After a third failure T is not testable.
6. **Stops.** Section 2's tests ("not testable") run on rows as fetched, before
   the calendar and the schedule. Section 3.6's staleness test covers only the
   month-ends a signal uses: M_{t−k} for t = 1…200 and k = 0…9 and 12. M_201 fixes
   X_201 and no signal. The audit is published whatever stop applies, with every
   block-rule breach and every schedule failure, not only the first.

**Engine and tax (sections 7–9)**

7. **A full exit.** A sale that leaves fewer than 10⁻¹² units of an ETF leaves
   zero units and no lots. Floating-point residue is not a holding.
8. **Shortfall (9.5).** A shortfall under ₹0.01 after a tax payment is no
   shortfall: it stays in cash at 0%, as in section 7.
9. **Set-off (9.4).** Within the FY being closed, (a) is applied before (b), and
   both before (c). In (c), brought-forward losses are applied one at a time in
   the order of the FY in which they arose, oldest first, whatever their
   character. Where a short-term and a long-term loss arose in the same FY, the
   short-term one goes first. A short-term loss offsets in order (a), a long-term
   one in order (b).
10. **Costs doubled (8, G4).** Every row of the cost table is doubled, the GST
    rate included (18% → 36% of brokerage, exchange and SEBI charges).

**Criteria and reported numbers (sections 12, 15, 19)**

11. **G6** uses the daily value series of the runs **without tax**. No April
    payment enters it. Every drawdown row in section 15 uses the same pre-tax
    series. The after-tax runs are used only for after-tax wealth.
12. **Degenerate variance.** If S = 0 (no variation at all in d*), t = 0: no
    evidence. P fails, and every guard and D are still computed.
13. **Activity (15)** is reported for the full window, development and hold-out,
    from the pre-tax REC price-track run:
    - orders and rupee costs: every order at a session in [X_a, X_{b+1}) for
      holding months a…b;
    - turnover a year: buys plus sells in that span (two-sided, the X_1 purchases
      included), divided by the mean of V_t over the slice and by the years;
    - share in, per sleeve: the mean of 1[w_kt > 0] over the slice (T and T12
      only);
    - switches a year, per sleeve: the months in the slice with w_kt ≠ w_k,t−1,
      taking w_k,0 = 0 so an entry at X_1 counts (T and T12 only).
14. **d̄ by FY (15)** is the full-window d_t of section 11 averaged over each FY's
    holding months: a decomposition of d̄. It is not d̄^U and not G3's statistic.
15. **Longest drawdown (15)** is counted in calendar sessions, from a peak's
    session to the first session at or above it, or to the end. Only stretches
    that fall below the peak count. X_1's two points are one session.
16. **Calendar-year returns (15)** run from year-end value to year-end value on
    the daily series; the first and last years are marked partial with their
    dates.
17. **Bootstrap (15)** resamples d*_t, centred at d̄, circular. Per resample:
    idx₀ = rng.integers(n); then jump = rng.random(n) < 1/6; then
    starts = rng.integers(n, size=n), elements 0 unused. One-sided
    p = #{mean(d*[idx] − d̄) ≥ d̄} / 10,000.
18. **The Sharpe-difference row (15)** is a HAC delta-method test: Ledoit &
    Wolf's (2008) moment vector and gradient, with a Newey–West kernel at the
    section-11 L instead of their prewhitened quadratic-spectral kernel. It is
    labelled that way, not as Ledoit–Wolf's estimator.
19. **Synthetic checks (19.2).** Each synthetic ETF closes at exactly 100 on the
    first day; returns start on the second. The first ETF has μ = 10% and
    σ = 18% a year, the second μ = 12% and σ = 15%. Per dataset, the first ETF's
    Student-t draws are taken, then the second's, each as one
    `standard_t(5, size=days)` call.
20. **Power check (19.2).** The drift constant is calibrated on a different seed
    (20261019), from about 300 datasets at each of three constants, by linear
    interpolation to a mean information ratio of 0.84. The 2,000 reported runs
    use seed 20261018. Because t's spread across datasets is about 1.2, not 1,
    expect roughly 75% to pass, not section 14's 80%.

**Outcome labels.** A section-2 stop prints "FAIL — NO TRADE (T is not
testable: …)", a section 3–4 stop "FAIL — NO TRADE (T is not evaluable: …)".

