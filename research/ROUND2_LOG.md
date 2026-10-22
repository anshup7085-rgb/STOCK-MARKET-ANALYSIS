# Round 2 — log of steps (research/PREREGISTRATION_2.md, section 19)

Each step with its UTC time, so the order is on record. Commits are on branch
`claude/brave-allen-kas61d`.

| # | When (UTC) | Step | Evidence |
|---|---|---|---|
| 1 | 2026-10-19 15:32:56 | Pre-registration committed. `research/cache_r2/` did not exist and no round-2 code existed | commit `7042f8f` |
| — | 2026-10-18 to 19 | Account holder's approval: general authority ("do whatever you deem fit … make profit") given in chat on 2026-10-18. The account holder had not read this specific document before the fetch | chat |
| 2 | 2026-10-19 15:46:45 | Round-2 code committed, written from the text with no data | commit `c88126d` |
| 2 | 2026-10-19 15:58:35 | **Size check passed**: 3 false passes in 2,000 synthetic datasets (0.15%; the limit is 20, 1.0%). Mean t −0.32, sd 1.06. Seed 20261018; ETF 1 μ 10%/yr, σ 18%; ETF 2 μ 12%/yr, σ 15%; draws in order, ETF 1 then ETF 2 per dataset | `scans/r2_size.json` (not committed: `scans/` is local) |
| 2 | 2026-10-21 15:33:09 | Power check, first run (superseded below): drift constant 0.00056 tuned by eye on 40 datasets of the reported seed; realised IR 0.77, P passed 68.1%. The review found it below the registered 0.84 and calibrated on the reported seed | `scans/r2_power.json` (overwritten) |
| 2 | 2026-10-22 06:13:01 | Pre-fetch review of the code against the text: 24 findings confirmed by skeptics, 3 rejected. **Amendment 1** committed with the fixes; `research/cache_r2/` still absent | commit `379543a` |
| 2 | 2026-10-22 06:32:13 | **Size check re-run on the amended code: passed**, 3 false passes in 2,000 (0.15%; limit 20). Synthetic series now start at exactly 100 | `scans/r2_size.json` |
| 2 | 2026-10-22 06:35:20 | Power check, reported only. Calibrated on seed 20261019, 300 datasets at each of 0.00058 / 0.00064 / 0.00070: mean IR 0.745 / 0.797 / 0.836; least-squares line gives 0.000703 for IR 0.84. Reported run, seed 20261018, 2,000 datasets: realised mean IR 0.886, **P passed 77.7%** (Amendment 1 expected about 75%) | `scans/r2_power.json` |

Readings taken where the text left a choice, each the one less favourable to T
or neutral, recorded before the fetch:

- **Costs doubled (G4):** every percentage and rupee amount, the GST rate
  included (18% → 36%). Doubling everything raises T's costs more than EXP's,
  because T's switches are the large orders.
- **Size check:** the first ETF gets μ = 10%/yr with σ = 18%, the second
  μ = 12%/yr with σ = 15% (the order in which the text lists them).
- **Cash at 6% (reported only):** interest on positive cash only, credited at
  the next execution session, taxed at 31.2% in the FY of the credit.
- **Daily value series:** two points on X_1 — ₹5,00,000 before trading, then
  the value after X_1's trades.
