# Strategies A and B on real NSE data — 2026-10-17

The two strategies pre-registered in `research/PREREGISTRATION.md`, run once
against the rules and pass criteria written there, then rebuilt independently
from the written spec alone.

**Verdict: A FAILED. B FAILED. B+A FAILED.** Under the pre-registration all
three hypotheses are now dead. None of them is re-tuned or re-tested on this
data, and none goes on to paper trading.

| Hypothesis | Criteria passed | Failed on |
|---|---|---|
| A — Nifty 50 above its 200-day average, else cash | 2 of 4 | Sharpe below buy-and-hold; too many switches |
| B — 12-1 momentum, top 10 | 1 of 4 | No excess over equal weight in any period; t = −0.20 |
| B+A — B with A's cash filter | 3 of 5 | Development excess −4.3%/yr; t = −0.25 |

## Sequence, and why it can be trusted

| When (UTC) | What |
|---|---|
| 18:34:19 | `PREREGISTRATION.md` committed (`90630fd`). No code for A or B existed. The data was not unseen, though: the same Yahoo series had already been downloaded and used for Gate 1 and the replay of the retired score, and Gate 1 printed results by year for 2023–2026. |
| 18:34:37–18:34:53 | Inputs cached to `research/cache/`. |
| 18:36:00 | `research/data.py` finalised, including the corporate-action back-adjustment. That rule came after the pre-registration and is not part of it (see Data). |
| 18:36:35 | Two independent rebuilds launched. They were given `PREREGISTRATION.md`, the cache and the repo's loader `research/data.py`, and did not read any strategy or metrics code. Both finished by 18:39. |
| 18:40:44 | `research/strategy_a.py` and `strategy_b.py` written from the spec, in parallel with the rebuilds, not after them. |
| 18:43:35 | Results written (`scans/research_AB.json`). Run once. |
| 18:46:17 | `research/MANIFEST.sha256` written: the SHA-256 of every cached file (digest `521be42b…3ea7a`). |

All times 2026-10-17 UTC. No parameter, period, cost or criterion was changed
after a result was seen.

**Replication.** For A the independent rebuild matched to the last printed
digit: CAGR 6.20%, Sharpe 0.529, max drawdown −25.5%, 144 switches. For B and
B+A, CAGR, Sharpe and drawdown matched to three digits. Excess return matched to
within 0.03 percentage points a year. The gap is the EW benchmark's membership
rule, not the cost method: this code admits HAL from its first bar (an execution
on 2018-04-02), the rebuild from the first month-end with a close. With the
rebuild's rule this code gives −0.674%/yr and t = −0.206 for B, against the
rebuild's −0.673% and −0.206. **Every pass/fail call is the same in both.**
Data loading was shared, not reproduced: both used `research/data.py`. The rebuild also tried the open choices
the spec left (EW limited to eligible names, CAGR difference instead of mean
excess, daily rather than monthly drawdown), and none of them changed any
criterion.

Reproduce, on a machine that holds the frozen cache:

```bash
(cd research/cache && sha256sum -c ../MANIFEST.sha256)    # inputs unchanged
python run_research.py all --json scans/research_AB.json
```

The cache itself is Yahoo data and is not in git. A fresh `python -m research.data`
downloads a window ending on the day it runs, so it gives different files. The
manifest proves which inputs were used; it does not preserve them.
`run_research.py` refuses to run on inputs that do not match the manifest, and
`research.data` refuses to overwrite an existing cache without `--refetch`.

## Data

- **Yahoo Finance via yfinance, EOD, delayed.** Last bar **2026-10-16**. Daily
  closes are split-adjusted, and **dividends are excluded** (pre-registered).
  Excluding dividends understates the return of every invested line, the
  benchmarks included.
- **`^NSEI`:** 2007-09-17 to 2026-10-16. A's evaluation starts 2008-07-09, the
  first date with an SMA200 plus two days.
- **Stocks:** 46 of the 47 `DEFAULT_UNIVERSE` names, 2,500 bars each (HAL has
  2,112 because it listed in 2018). `TATAMOTORS.NS` no longer resolves on
  Yahoo, as noted in the Gate 1 report.
- **One corporate-action fix.** TRENT's Yahoo history steps by exactly 2/3 at
  2026-01-01, and everything before that was scaled by 2/3. The step is the edge
  of Yahoo's own partial adjustment, not the event. Bars before 2026, and from
  2026-06-04 on, sit on NSE's 0.05 tick (raw prices). The bars in between do not:
  they are already × 2/3. So the event looks like a bonus around 2026-06-04,
  which no source here confirms. The fix gives the right returns either way. The
  rule was written into `research/data.py` at 18:36, after the pre-registration
  and before any result, so it is not pre-registered. No other jump matched a
  standard factor, and gaps that matched none were left alone.
- **Yahoo placeholder rows.** 100 rows across the stock files (none in `^NSEI`)
  are Yahoo fill-ins: zero volume, with the price equal to the previous close.
  90 of them sit on two days that 45 names share: 2025-03-18, and 2026-01-15,
  which NSE circulars found by search (not read directly) describe as a trading
  holiday for the Maharashtra municipal elections. So the panel treats at least
  one closed day as a flat trading day. One placeholder falls on a date the test
  traded: DLF on 2019-12-02, an EW execution date, so that month EW bought DLF at
  the previous close. The rows were used as they are; `run_research.py` now
  lists them.

## Strategy A — index trend filter

Hold the Nifty 50 while it closes above its 200-day SMA, else cash at 0%. A
signal at close *t* executes at close *t+1*, and each switch costs 0.10%.

| Period | | CAGR | Vol | Sharpe | Max DD | MAR | Invested | Switches/yr |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Full 2008-07 → 2026-10 | **A** | 6.2% | 12.9% | 0.53 | −25.5% | 0.24 | 71% | 8.1 |
| | Buy & hold | 10.4% | 19.5% | 0.60 | −45.4% | 0.23 | 100% | — |
| Development → 2022 | **A** | 6.3% | 13.6% | 0.51 | −25.5% | 0.25 | 71% | 8.7 |
| | Buy & hold | 11.3% | 21.0% | 0.62 | −45.4% | 0.25 | | |
| Hold-out 2023 → | **A** | 6.0% | 9.9% | 0.64 | −14.6% | 0.41 | 72% | 5.7 |
| | Buy & hold | 6.8% | 12.7% | 0.59 | −15.8% | 0.43 | | |

| # | Criterion | Result |
|---|---|---|
| A1 | \|max DD\| ≤ 0.6 × buy-and-hold | **pass**: 25.5% vs 27.2% |
| A2 | Sharpe ≥ buy-and-hold | **fail**: 0.529 vs 0.604 |
| A3 | Hold-out: \|max DD\| ≤ B&H, and Sharpe ≥ 0.8 × B&H | **pass**: 14.6% vs 15.8%; 0.635 vs 0.468 |
| A4 | ≤ 6 switches a year | **fail**: 8.1 |

**What it says.** The filter does what trend filters are known for: the deepest
drawdown was cut from −45% to −26%. It paid for that with 4.2 points of CAGR a
year and a lower Sharpe. Around the average, the index crosses back and forth,
and each whipsaw costs a switch and misses the rebound. That is also why it
fails A4.

**Robustness (reported only, not used for pass/fail).** Each line is compared
with buy-and-hold over that variant's own dates:

| Variant | Sharpe | B&H Sharpe | Max DD | B&H Max DD |
|---|---:|---:|---:|---:|
| SMA 150 | 0.60 | 0.53 | −22.2% | −51.7% |
| SMA 250 | 0.46 | 0.61 | −28.5% | −40.5% |
| Cash at 6% | 0.66 | 0.60 | −22.6% | −45.4% |

SMA 150 beats buy-and-hold over its own dates only because those dates start
earlier, in the 2008 fall, which hurts buy-and-hold more. On common dates from
2008-09-19, none of SMA 150, 200 or 250 beats buy-and-hold on Sharpe: 0.60, 0.53
and 0.46 against 0.61. Choosing 150 now would be the curve fit the
pre-registration exists to prevent, and it would not even win. Cash at 6% is closer to what a liquid fund pays,
but it does not rescue A. If cash earns 6%, 6% is also the risk-free rate for
both lines, and on that basis A's Sharpe is 0.21 against buy-and-hold's 0.31.
The table's Sharpe figures use 0% for every line.

## Strategy B — 12-1 momentum, and B+A

At each month-end, rank the names by their return from twelve month-ends ago to
one month-end ago. Buy the top 10 at equal weight on the next trading day, and
pay 0.25% per side on the turnover actually traded. B+A holds cash for the month
when the Nifty is below its SMA200 on the formation date: 28 of the 108 months,
15 in development and 13 in hold-out.

Monthly returns run from 108 rebalances executed between 2017-10-03 and
2026-09-01. Development is 63 months to 2022-12, and hold-out is 45 months from 2023-01.

| Period | | CAGR | Vol | Sharpe | Max DD | Excess vs EW /yr | t |
|---|---|---:|---:|---:|---:|---:|---:|
| Full | **B** | 15.0% | 21.7% | 0.76 | −34.3% | −0.66% | −0.20 |
| | **B+A** | 15.9% | 15.2% | 1.05 | −12.9% | −1.26% | −0.25 |
| | EW | 16.8% | 17.8% | 0.97 | −29.7% | — | — |
| | Nifty 50 | 9.6% | 17.1% | 0.62 | −32.2% | — | — |
| Development | **B** | 16.9% | 22.6% | 0.82 | −34.3% | −0.98% | −0.22 |
| | **B+A** | 15.1% | 14.6% | 1.04 | −12.9% | −4.33% | −0.58 |
| | EW | 19.0% | 19.9% | 0.98 | −29.7% | | |
| Hold-out | **B** | 12.3% | 20.6% | 0.67 | −25.9% | −0.21% | −0.04 |
| | **B+A** | 16.9% | 16.1% | 1.05 | −12.4% | +3.03% | +0.46 |
| | EW | 13.7% | 14.5% | 0.96 | −17.4% | | |

| # | Criterion | B | B+A |
|---|---|---|---|
| B1 | Hold-out excess > 0 and Sharpe ≥ EW | **fail** (−0.21%; 0.67 vs 0.96) | **pass** (+3.03%; 1.05 vs 0.96) |
| B2 | Development excess > 0 | **fail** (−0.98%) | **fail** (−4.33%) |
| B3 | Full-period t > 2.0 | **fail** (−0.20) | **fail** (−0.25) |
| B4 | \|max DD\| ≤ 1.25 × EW | **pass** (34.3% vs 37.1%) | **pass** (12.9% vs 37.1%) |
| DD | B+A \|max DD\| ≤ B | — | **pass** (12.9% vs 34.3%) |

**What it says.**

- **Momentum added nothing.** Picking the ten strongest names did no better than
  holding all 46 equally, in either half of the data. It was more volatile and
  drew down further. The t-statistic is −0.20; the pre-registered bar was +2.0.
- **B+A's good-looking numbers come from the cash filter, not from momentum.** Its
  Sharpe of 1.05 and −12.9% drawdown look better than EW's 0.97 and −29.7%, but
  it lost to EW by 4.3% a year in development. Its hold-out win, on 45 months
  with t = 0.46, is the kind of result that appears by chance. The
  pre-registration said in advance that a B+A-only result "is about the trend
  filter, not momentum". Neither even got that far.
- **The benchmark's 16.8% is not an achievable return.** EW is today's large caps,
  bought nine years ago. Many are in the list *because* they rose. Survivorship
  flatters every line built from the stock list: B, B+A and EW. (The Nifty 50
  row is the real index.) That is why the comparisons are relative to EW and
  not to zero.

## The old system's 10-year replay (context)

`python run_trader.py replay --provider yfinance --lookback 2500 --equity 500000`
replays the retired score through the full execution engine. That covers paper
fills, risk engine, circuit breakers and the journal. It covers 2,500 sessions,
2016-08-26 to 2026-10-16:

| | |
|---|---|
| Closed trades | 44, all between 2017-03 and 2017-09 |
| Net result | +₹60,105 on ₹5,00,000 after ₹10,487 of charges |
| Win rate / net expectancy | 50% / +0.37R, on n = 44 (too few to mean anything) |
| Worst drawdown | −5.5% |

**The headline numbers do not describe ten years.** On 2017-08-10 a −2.17% day
breached the −2.0% daily-loss limit, and the kill switch engaged as designed.
Releasing it is a person's decision, and nobody releases it in a replay. So the
engine took **no new entries for the remaining 2,264 of 2,500 sessions**, and
the trades already open ran off through their stops, one target and time exits.
The replay's summary did not say this. It now does: `replay` reports a
`kill_switch` block with the date it first stood and the sessions it covered
(`trading/replay.py` `halt_summary`; tests `test_replay_reports_when_the_kill_switch_froze_it`
and `test_replay_command_prints_the_halt`).

The replay is not re-run with the breaker loosened. That would mean weakening a
control to make trades happen, for a score Gate 1 has already retired. What the
replay did establish is the useful half: the breaker fires on real data and the
engine honours it for nine years without a single new entry.

## Data limitations

- **EOD, delayed, Yahoo.** Occasional bad ticks are possible, and no
  intraday path is modelled. Fills are at the close.
- **Dividends excluded** throughout. This is not quite like for like. It
  understates the always-invested lines (buy-and-hold, EW, Nifty 50) more than A,
  which is invested 71% of days, and B+A, which is in cash 28 of 108 months. So
  it flatters those two by roughly (share of time in cash) × (dividend yield).
  The direction is known; the size depends on a yield this report does not source.
- **Survivorship bias, severe for B.** The universe has no point-in-time index
  constituents, and the names that fell out of the index are missing.
- **Costs are flat assumptions:** 0.10% per switch for A, 0.25% per side for B.
  They are not the fee card in `market/charges.py`. They were fixed before the run.
  Whether 0.10% is generous for an ETF switch is not shown here: the fee card has
  no ETF rates.
- **The index stands in for an ETF.** For A, ETF tracking error and expense ratio
  are not modelled.

## What this leaves

1. **Nothing in this repo has a measured edge.** Gate 1 retired the score. A, B
   and B+A are dead by their own pre-registered terms. The correct trading output
   today is **NO TRADE**.
2. The execution layer is unaffected and ready for a strategy that earns it.
   Paper and live have nothing to run yet.
3. Every further idea tested on this same 2017–2026 history spends credibility.
   The single 2023–2026 hold-out has now been used by Gate 1 (by year), the
   replay, and A, B and B+A: it is spent. A next hypothesis is worth testing only
   if it brings **new data** or a **new kind of information**, for example
   point-in-time index constituents (which remove the survivorship bias that
   flatters B), or forward paper trading of a pre-registered rule from today.
   Searching the same bars for a variant that would have worked does not count.
