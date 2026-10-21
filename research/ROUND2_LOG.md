# Round 2 — log of steps (research/PREREGISTRATION_2.md, section 19)

Each step with its UTC time, so the order is on record. Commits are on branch
`claude/brave-allen-kas61d`.

| # | When (UTC) | Step | Evidence |
|---|---|---|---|
| 1 | 2026-10-19 15:32:56 | Pre-registration committed. `research/cache_r2/` did not exist and no round-2 code existed | commit `7042f8f` |
| — | 2026-10-18 to 19 | Account holder's approval: general authority ("do whatever you deem fit … make profit") given in chat on 2026-10-18. The account holder had not read this specific document before the fetch | chat |
| 2 | 2026-10-19 15:46:45 | Round-2 code committed, written from the text with no data | commit `c88126d` |
| 2 | 2026-10-19 15:58:35 | **Size check passed**: 3 false passes in 2,000 synthetic datasets (0.15%; the limit is 20, 1.0%). Mean t −0.32, sd 1.06. Seed 20261018; ETF 1 μ 10%/yr, σ 18%; ETF 2 μ 12%/yr, σ 15%; draws in order, ETF 1 then ETF 2 per dataset | `scans/r2_size.json` (not committed: `scans/` is local) |
| 2 | 2026-10-21 | Power check, reported only: drift raised by 0.00056 a day while a sleeve's signal is in and lowered while out (calibrated on 40 datasets to an information ratio near 0.84); 2,000 datasets | `scans/r2_power.json` |

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
