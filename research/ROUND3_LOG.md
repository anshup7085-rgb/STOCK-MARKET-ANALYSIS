# Round 3 — log of steps (research/PREREGISTRATION_3.md)

Each step with its time, so the order is on record. Commits are on branch
`claude/brave-allen-kas61d`.

| # | When | Step | Evidence |
|---|---|---|---|
| 1 | 2026-10-22 | F&O facts checked: 55 claims about fees, contract rules and broker APIs, each by one researcher and two skeptics; 19 confirmed by both, the rest left out of the rules or read from the feed at run time | session scratch file `fo_facts.json` (not committed) |
| 2 | 2026-10-22 | Two-look t-test checked on synthetic condor P&L **before** choosing the statistic: 1.6%–4.5% false passes against a 0.5% target in 12 null scenarios (10,000 runs each, seed 20261022). Rejected | `python research/fno_sim.py ttest` → `scans/r3_ttest.json` |
| 3 | 2026-10-22 | Betting test, size: 0.13%–0.48% false passes in 9 null scenarios (4,000 runs each, read after every trade to 250, futility rule applied; seed 20261022). Limit 0.5% | `python research/fno_sim.py size` → `scans/r3_size.json` |
| 4 | 2026-10-22 | Betting test, power (reported only): true mean R 0.05 / 0.10 / 0.15 / 0.20 → PASS within 250 trades 3.5% / 24.9% / 66.0% / 94.4% (1,500 runs each, seed 20261022) | `python research/fno_sim.py power` → `scans/r3_power.json` |
| 5 | 2026-10-22 | Draft and engine committed, marked DRAFT, not binding | commit `e381365` |
| 6 | 2026-10-22/23 | Independent review: four critics of the draft (statistics, market mechanics, ambiguity, policy: 54 findings) and a code review (19 findings, two of its four lenses cut short by a usage limit). The skeptic stage was cut short too; each finding was checked by the author instead, against the text, the code and the facts file | session scratch files (not committed) |
| 7 | 2026-10-23 | Revision. Settlement moved to NSE's final settlement price (a SEBI circular reported for 7 Dec 2026 changes how expiring index options settle). Paper capital and the ₹5,000 budget fixed. Entry at 17 fixed slots; a missed slot makes its cycle MISSED at R = −1. Operator exit removed. Credit capped at 0.80 W so R ≥ −2 holds. Verdict order and sequence defined. A new forward family declared. The journal made the only record, with duplicates void. The engine refuses real feeds until the text is bound, by hash. Fee rounding half-up per charge; SEBI fee on settlement notional | commit `15937d5` |
| 8 | 2026-10-23 | Two further independent reviews of the revision: one of the rules against the code (15 findings, 2 blocking: a typed settlement value chosen after the close; feed and clock faults counted as rule no-trades), one of the feeds and the tests (11 code defects, including a NaN settlement value accepted as maximum profit, the Kite quote rate limit, a probe that accepted a feed without timestamps; 37 of 60 planted bugs not caught by the tests). All confirmed by reproduction and fixed: settlement by hand only from NSE's own file; feed and clock faults make a slot unobserved; the day's master, the holiday list, the parameters and the text are checked against the journal; late or repeated entry records are void; one feed switch only; anchors of the journal's head committed weekly, required for a PASS; the verdict decided strictly in cycle order, with the recorded verdict standing | this commit |

Nothing in this table used a market price. The size and power checks need no
data; the paper engine has not yet run against a real feed.
