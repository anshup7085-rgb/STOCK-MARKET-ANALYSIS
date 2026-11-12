# The market brain: the plan (12 Nov 2026)

Agreed with the holder on 12 Nov 2026: build a system that reads the whole market, learns every day,
and must prove itself before a rupee follows it. This page is the plan; `STATUS.md` says what is done.

## What it is

Every evening the brain reads NSE's free daily files (every index and sector, India VIX, what foreign
funds and others hold in futures and options), writes what it expects of the next session as
probabilities, and the following evening marks itself right or wrong. It trades nothing. Code:
`brain/`, run by `run_brain.py`.

"Learning every day" means three exact things here: one more day of data; a model fitted again on
everything before the day it forecasts; and a score that grows by one honest answer a day.

## Why this shape

- **It cannot fool us.** A forecast is written before the session and kept in a journal that cannot be
  edited. Each is scored against a plain baseline (how often the answer is yes anyway).
- **It costs nothing to run.** NSE's files need no login: no Kite, no morning routine, no bigger server.
- **It keeps the goal alive honestly.** Income is capital times a real edge. This finds out whether
  there is an edge, with proof, while the capital grows by saving.

## Stages

| # | Stage | State |
|---|---|---|
| 1 | **A paper record on history.** Two questions about the next session: will NIFTY close UP; will its move be BIG (larger than its usual move). Walk-forward on 2016 to Oct 2024: `run_brain.py backfill`. The reading was fixed before the first run (in `run_brain.py`) | Built 12 Nov; first run pending |
| 2 | **The daily forecast and its score, by itself.** A cron job after the close: fetch the day's files, score yesterday, forecast tomorrow, write a one-page brief. Forecasts in a hash-chained journal | Next |
| 3 | **Read more of the market.** Every stock's daily price, volume and delivery (NSE's bhavcopy), option positions by strike, the events calendar (RBI, Budget, results) | After 2 |
| 4 | **Does it know more than prices do?** A forecast of BIG is worth money only if it beats what option prices already imply. A test with rules bound first, on the held-back years (Nov 2024 to Oct 2026) | After the record of 1 and 2 justifies a slot |
| 5 | **Trading.** Only on a passed test, on paper first, then at a size the account can carry | Not before 4 |

## Rules it keeps

- The two years from 1 Nov 2024 to 18 Oct 2026 are held back, as for every other test: the record of
  stage 1 stops on 31 Oct 2024, and no model is fitted or scored on the held-back days before a
  registration. They may be read only as the market's recent past when a forecast for today needs it.
- Every result is shown beside its baseline, with the count of days behind it.
- Skill on a question is not income. The step from one to the other is stage 4, and it can fail.
