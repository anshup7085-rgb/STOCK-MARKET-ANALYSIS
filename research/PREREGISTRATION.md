# Pre-registration — strategies A and B

Written and committed **2026-10-17, before either strategy has been run on any
data.** The git history of this file is the evidence of that. The rules, the
data, the periods and the pass criteria below are fixed; results are reported
against them as written, including failure. A strategy that fails is dead — it
is not re-tuned and re-tested on the same data.

Why this exists: the original scoring model failed Gate 1 on real data
(`reports/2026-10-17-gate1.md`). ROADMAP kill criterion 3 — "results hold only
with parameters tuned after seeing the test set" — is avoided by deciding every
parameter here first.

Honesty note on prior knowledge: the author knows the broad history of Indian
markets (the 2008 and 2020 crashes, the 2021 rally). Parameters are therefore
the canonical published values, not values chosen for this data.

Common to both:

- **Data:** Yahoo Finance via yfinance, daily, split-adjusted closes, **dividends
  excluded** (the same convention as `market/providers/yf.py`). Cached once to
  `research/cache/` before any computation; the SHA-256 of every file is
  recorded in the results report so the inputs are fixed.
- **Execution timing:** a signal computed on the close of day *t* is executed at
  the close of day *t+1*. Nothing trades on the bar that produced its signal.
- **Annualisation:** 252 trading days, 12 months. Sharpe uses a 0% risk-free
  rate (stated so the comparison with the benchmark is like for like).
- **Development / hold-out split:** development ends **2022-12-31**, hold-out is
  **2023-01-01 → last bar**. Neither strategy fits any parameter, so the split
  checks stability across time rather than protecting a fit.

---

## Strategy A — index trend filter

**Hypothesis.** Holding the Nifty 50 only while it is above its 200-day simple
moving average gives a better risk-adjusted return than holding it always,
mainly by avoiding the deepest drawdowns.

**Rules.**

1. Series: Nifty 50 index, Yahoo `^NSEI`, all available history.
2. At each close *t*: target = **invested** if close*t* > SMA200*t*, else **cash**.
3. A change in target executes at the close of *t+1*. The position held over
   day *d* (close *d−1* → close *d*) is the target decided at close *d−2*.
4. Cash earns **0%** (primary). Sensitivity, reported only: 6% a year.
5. Costs: **0.10% of equity per switch** (each in→out or out→in), charged on the
   execution day. Covers brokerage, STT, exchange charges, stamp duty,
   slippage and ETF spread.
6. The index stands in for a Nifty 50 ETF (e.g. NIFTYBEES); tracking error and
   expense ratio are not modelled.

**Benchmark.** Buy-and-hold `^NSEI` over exactly the same dates, no costs.

**Evaluation period.** Full = first date with an SMA200 value plus 2 days, to
the last bar. Also reported separately for development and hold-out.

**Metrics.** CAGR, annualised volatility, Sharpe, maximum drawdown, MAR
(CAGR ÷ |max drawdown|), time invested, switches per year.

**Pass criteria — all must hold, net of costs, cash at 0%:**

| # | Criterion |
|---|---|
| A1 | Full period: |max drawdown| ≤ 0.6 × buy-and-hold's |max drawdown| |
| A2 | Full period: Sharpe ≥ buy-and-hold Sharpe |
| A3 | Hold-out: |max drawdown| ≤ buy-and-hold's, **and** Sharpe ≥ 0.8 × buy-and-hold Sharpe |
| A4 | Full period: ≤ 6 switches per year on average |

**Robustness, reported only, not used for pass/fail:** SMA 150 and SMA 250;
cash at 6%.

---

## Strategy B — cross-sectional momentum (12-1)

**Hypothesis.** Among large-cap NSE stocks, those with the highest return over
the past twelve months excluding the most recent month go on to outperform an
equal-weighted portfolio of the same stocks over the following month.

**Rules.**

1. Universe: the names in `market/universe.py` `DEFAULT_UNIVERSE` that return
   data. Daily closes, the 2,500 most recent bars per name.
2. Formation at each month-end *M* (last trading day of a calendar month that
   has data): momentum*i* = close*i* at month-end *M−1* ÷ close*i* at month-end
   *M−12* − 1. A name needs both values to be eligible.
3. Select the **top 10** by momentum (ties broken alphabetically by symbol).
4. Execute at the close of the first trading day after *M*; equal weight,
   1/10 each; hold until the next rebalance executes.
5. Costs: **0.25% of traded notional per side** (buys and sells at each
   rebalance), covering STT, stamp duty, exchange charges, GST, DP charges,
   Groww-style brokerage and 0.05% slippage.
6. Cash earns 0%. No leverage, no shorting.

**Variant B+A.** Identical, except at each formation date *M*: if `^NSEI` closes
below its SMA200 on *M*, hold cash for that month instead.

**Benchmarks.**
- **EW:** the same universe, equal-weighted, rebalanced on the same dates, with
  the same cost model. This is the primary comparison, because it shares the
  universe's survivorship bias.
- **Nifty 50** buy-and-hold, reported for context.

**Periods.** First executable rebalance (twelve month-ends of history needed) to
the last complete holding month. Development = rebalances executed up to
2022-12-31; hold-out = after.

**Metrics.** Monthly returns; CAGR, volatility, Sharpe, maximum drawdown; mean
monthly excess return over EW, and its t-statistic (mean ÷ standard error).

**Pass criteria for B — all must hold, net of costs:**

| # | Criterion |
|---|---|
| B1 | Hold-out: annualised excess return over EW > 0, **and** Sharpe ≥ EW Sharpe |
| B2 | Development: annualised excess return over EW > 0 |
| B3 | Full period: t-statistic of monthly excess return over EW > 2.0 |
| B4 | Full period: |max drawdown| ≤ 1.25 × EW's |max drawdown| |

**Pass criteria for B+A:** B1–B4 applied to B+A, **plus** |max drawdown| of
B+A ≤ |max drawdown| of B.

Two hypotheses are tested here, B and B+A. Both are reported whatever happens. If
only B+A passes, the result is about the trend filter, not momentum.

**Known limitations, stated before the result:** survivorship bias is
**severe** for momentum on today's large caps, because some of today's names are
large precisely because they rose; it flatters B and is only partly cancelled
by benchmarking against EW of the same names. There are no point-in-time index
constituents, the universe is 46 names, and dividends are excluded. A pass on
this data is therefore necessary but not sufficient: it would justify a test
on point-in-time constituents, not trading.

---

## What a pass leads to

A pass makes the strategy eligible for the next ROADMAP rung (paper trading),
after the execution engine gains the strategy's exit rules: a trend exit for A
and a monthly rebalance for B. Neither fits the current stop/target swing
engine as-is. A pass is not a forecast of returns.
