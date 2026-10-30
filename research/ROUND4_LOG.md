# Round 4 — log of steps (research/PREREGISTRATION_4.md)

Each step with its date, so the order is on record. Commits are on branch
`claude/brave-allen-kas61d`.

| # | When | Step | Evidence |
|---|---|---|---|
| 1 | 2026-10-30 | **Draft.** V-H, the historical track PREREGISTRATION_3 section 16 reserved, written before any historical option price was fetched. What the author had seen is listed at the top of the draft. Not binding; the account holder reads a plain summary first | this commit |
| 2 | 2026-10-30 | **Binding.** The account holder approved the plain summary (entry at the close; today's lot and fee card for every year; the 1% fill penalty, at least 0.25 points; V's PASS bar, and no live step for V after a FAIL). Before binding the text was tightened: the weekly chain is defined by neighbours within 10 days, a sell filling at or below 0 is no trade, and a later minute-data replay of V's own entry slots is allowed as description only. SHA-256 = `eacf2ec247bf3987305dbc31e4d38140a3e40257e3200f856b8930693927b55a`, recorded in `trading/fno/vh.py`. No historical option price had been fetched. The account holder asked that the tests be run once Dhan's minute data is in hand as well | this commit |
