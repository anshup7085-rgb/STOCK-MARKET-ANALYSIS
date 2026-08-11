# Scoring model repair + backtest harness — 2026-08-11

Two changes to the analysis layer, both provable without a live feed, plus the
harness that will eventually tell us whether any of it works.

## 1. Risk/reward was constant by construction

**The bug.** `run_scan.py` passed `th.target2_r` — the config constant 3.0 — to
the scorer as the candidate's reward/risk ratio. Targets were built as
`price + k x risk`, so the ratio came back as exactly `k` for every instrument on
every day. Measured across a 47-name scan: **1 distinct value, spread 0.0**.

`score.py` calls this component "the single most load-bearing" at 15 points. It
was 15 points of nothing.

**The fix.** Targets are now measured to confirmed swing structure — the swing
highs a rally would actually have to trade through — with the ATR stop unchanged.

- `market/indicators.py` gains `pivot_highs` / `pivot_lows`, which confirm a
  pivot only after `right` bars have printed past it. The last `right` bars can
  never produce one. That delay is deliberate: a pivot confirmed by bars that
  have not happened yet is lookahead.
- Levels within 0.5 ATR of each other collapse into one ceiling, and the
  survivor is the one price meets *first* — the conservative choice.
- `market/levels.py` places T1/T2/stretch on the next three structural levels,
  falling back to R-multiples only where the chart offers nothing (a name at an
  all-time high genuinely has no overhead structure). The basis is recorded as
  `target_basis` so a reader knows which kind of number they are looking at.

## 2. Market regime could never rank anything

Regime is a property of the *market*, so `score_regime` returned the same points
for every candidate in a run — again spread 0.0, this time across 10 points.

It has been removed from the per-name components and re-applied as a scan-level
deduction: every candidate loses the same amount, the ranking is untouched, and
in a hostile tape the whole shortlist can fall below the floor and correctly
produce NO TRADE. That was always its real job — "should we trade at all", not
"which name is better".

The model is now 85 points across six components.

## Measured effect

Old code and new code, run against **identical** synthetic data (see note below),
47 names, same catalyst file:

| | before | after |
|---|---:|---:|
| R:R distinct values | 1 | 43 |
| R:R range | 3.00 – 3.00 | 0.59 – 5.02 |
| R:R component spread | 0.0 | 15.0 |
| regime component spread | 0.0 | removed |
| median score | 57.1 | 35.5 |
| shortlisted at ≥60 | 20 / 47 | 4 / 47 |

Discriminating weight rises from 45 of 70 available points to the full 70. The
shortlist tightening is the point: 20 of 47 names clearing the bar was a symptom
of a score that could not tell them apart.

Top-10 overlap before/after is 8/10, but reordered — the fix changes *ranking*,
not just absolute levels.

## 3. Backtest harness

`market/backtest.py` + `run_backtest.py`. Walk-forward, and honest about it:

- Snapshot at bar *i* is built from `df.iloc[:i+1]` and nothing else.
- Entry at the close of the signal bar; the path is then walked bar by bar
  against real highs and lows.
- **When a bar spans both stop and target, the stop is taken.** Daily bars cannot
  tell us which came first, and assuming the good fill is how a backtest lies.
- **A gap through the stop fills at the open**, so the trade reports worse than
  −1R — which is what happens in the account.
- Output: trade count, win rate, mean and median R by score band, target/stop
  rates, overall expectancy, and Spearman rank correlation between score and
  outcome.

**Control run on synthetic data:** 752 trades, Spearman **+0.021**, expectancy
+0.029R. That is the correct answer — geometric random walks contain no
structure to detect, so a harness reporting no signal there is proving it does
not manufacture signal out of noise. It measures the harness, not the model.

The real question — does the score predict anything on actual NSE data — remains
unanswered, because there is still no feed.

## Also fixed

`market/providers/mock.py` seeded its RNG from `hash(symbol)`. Python randomises
string hashing per process, so the mock provider produced different synthetic
prices on every run and no before/after comparison on it was meaningful. Now
seeded from `zlib.crc32`, so it is reproducible.

## Tests

26/26 pass, up from 16. New coverage:

- `test_pivots_are_confirmed_not_predicted` — pivots from a truncated series must
  be a prefix of pivots from the full series
- `test_pivots_lag_the_last_bar` — the final bars cannot yet have confirmed one
- `test_targets_vary_with_structure` — the regression test for the bug above
- `test_reward_risk_is_consistent_with_levels` — the ratio must equal its levels
- `test_regime_does_not_rank_but_does_deduct` — regime moves score, not coverage
- `test_simulate_takes_the_stop_when_a_bar_spans_both`
- `test_simulate_gap_fills_worse_than_the_stop`
- `test_backtest_runs_and_stays_causal`, `test_spearman_detects_direction`

One existing test was **deleted**, not repaired: `test_r_multiple_math` asserted
that T2 is always exactly 3.0R. That assertion held only because of the bug — it
was the defect written down as a guarantee. It is replaced by
`test_r_multiple_fallback_when_chart_offers_nothing`, which checks the same
arithmetic on the fallback path where it is genuinely correct.

## Still true, still unfixed

No live feed reachable from this environment — Yahoo, NSE and BSE all return 403
to the egress gateway, and a headless Chromium hits the identical wall because
the allowlist is enforced at the network layer rather than in the HTTP client.
No F&O ban list, no NSE holiday calendar, no corporate-action detection, no
breadth data, and slippage and charges are excluded from every figure here.
