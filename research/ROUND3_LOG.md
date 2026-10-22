# Round 3 — log of steps (research/PREREGISTRATION_3.md)

Each step with its time, so the order is on record. Commits are on branch
`claude/brave-allen-kas61d`.

| # | When | Step | Evidence |
|---|---|---|---|
| 1 | 2026-10-22 | F&O facts checked: 55 claims about fees, contract rules and broker APIs, each by one researcher and two skeptics; 19 confirmed by both, the rest left out of the rules or read from the feed at run time | session scratch file `fo_facts.json` (not committed) |
| 2 | 2026-10-22 | Two-look t-test checked on synthetic condor P&L **before** choosing the statistic: 1.6%–4.5% false passes against a 0.5% target in 12 null scenarios (10,000 runs each, seed 20261022). Rejected | `python research/fno_sim.py ttest` → `scans/r3_ttest.json` |
| 3 | 2026-10-22 | Betting test, size: 0.13%–0.48% false passes in 9 null scenarios (4,000 runs each, read after every trade to 250, futility rule applied; seed 20261022). Limit 0.5% | `python research/fno_sim.py size` → `scans/r3_size.json` |
| 4 | 2026-10-22 | Betting test, power (reported only): true mean R 0.05 / 0.10 / 0.15 / 0.20 → PASS within 250 trades 3.5% / 24.9% / 66.0% / 94.4% (1,500 runs each, seed 20261022) | `python research/fno_sim.py power` → `scans/r3_power.json` |

Nothing in this table used a market price. The size and power checks need no
data; the paper engine has not yet run against a real feed.
