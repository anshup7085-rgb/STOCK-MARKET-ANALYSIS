# Market Lab: the blueprint (agreed with the holder, 20 Nov 2026)

One system that watches several markets live, runs many strategies on paper at once, reads the news
and measures how the market reacts, takes the holder's own daily view, scores everything honestly,
and shows it all on one private dashboard. It finds out what works, with proof, before a rupee
follows it. `STATUS.md` says what is done today; this page is the plan.

## The parts

| # | Part | What it does |
|---|---|---|
| 1 | **Heads: one per market** | Each head brings its own live and daily data. **India** (NSE stocks, F&O, MCX commodities, FII positions, VIX), **Crypto** (Bitcoin, Ethereum and others, live and around the clock), later **US stocks** and other countries |
| 2 | **One store** | Everything kept with its time, never edited: prices, files, news, forecasts, trades. The record is the evidence |
| 3 | **Brains** | For each market, forecasts of the next session (direction, size of the move, volatility) as probabilities, each scored daily against a plain base rate (`brain/`, started 12 Nov) |
| 4 | **Strategy lab** | Many strategies running live on paper in every market, on one shared paper engine and one scorekeeper that allows for the lucky winners that testing many strategies always produces (`trading/fno/explore.py` is the first) |
| 5 | **News lab** | Reads every source it can reach (official: RBI, SEBI, NSE and BSE announcements, government releases; financial news feeds; crypto news), sorts each item (which market, which company, good or bad, how important), measures the market's move after it (5 minutes, 1 hour, 1 day, against a normal day), and builds a library of what moved what |
| 6 | **Human desk** | The holder writes a short view each day (about 5 minutes: "NIFTY up tomorrow because..."). It is scored exactly like the brains, on one scoreboard: the holder, the brains, and both against the base rate |
| 7 | **Dashboard** | One private web page, readable on a phone, with all of the above (`dashboard/build.py`, started 20 Nov) |
| 8 | **Real trading, later** | Only for a strategy that has passed its exam, small at first, every order confirmed by the holder |

## Rules that never change

- Paper first, everywhere. No real order without a passed exam and the holder's yes.
- Test rules are written down before the data they judge is read; held-back years stay held back.
- Every result beside its baseline and the count of days behind it; costs and taxes always included.
- More strategies means more lucky-looking winners: the scorekeeper's bar rises with their number.
- Nothing secret on the dashboard; it is private to the holder's own devices.
- The system runs by itself; the holder's time is about 5 minutes a day (the human desk) plus the
  Zerodha login on Indian trading days.

## Phases

| Phase | What | Estimate | State |
|---|---|---|---|
| 1 | **The shared core and the dashboard.** The paper engine, scorekeeper and reports made usable by any market; the dashboard, served privately | 1–2 weeks | Dashboard v0 built 20 Nov; themes section 27 Nov |
| 2 | **Crypto head.** Free live data from the exchanges, a crypto brain, the first strategies on paper | 1–2 weeks | Prices (history and a live minute recorder) and the crypto brain built 21 Nov; daily rules on paper from 25 Nov; the futures desk tested 27 Nov: no edge after costs |
| 3 | **News lab and human desk** | 2 weeks | Human desk live 27 Nov (and an events calendar); news lab next |
| 4 | **India head grows**: stocks and MCX commodities beside F&O | 2–3 weeks | |
| 5 | **US stocks and other countries** | later | |

## Seeing the dashboard

The page is built on the server each evening (and on demand, `deploy/dashboard.sh`). It is served only
on the server's **Tailscale** address: a free private network between the holder's own devices
(personal plan, from memory; to be checked on tailscale.com). Nothing is opened to the public internet.

1. Phone: install the Tailscale app, sign in (Google sign-in works).
2. Server, once: `curl -fsSL https://tailscale.com/install.sh | sh` then `sudo tailscale up`; open the
   link it prints and sign in with the same account. The login happens in the holder's own browser;
   nothing is pasted into the chat.
3. Server: `deploy/dashboard.sh`. It prints the address to open on the phone, `http://100.x.y.z:8080`.

## Costs, honestly

- **Server:** the AWS Lightsail machine the project already runs on. A system that runs around the clock
  for several markets will likely need a bigger plan (2–4 GB of memory), roughly ₹1,000–2,500 a month
  (estimate; AWS's own price list decides). Not needed until the crypto head runs live.
- **Data:** crypto exchanges' public data is free; NSE's files are free; Kite (₹500 a month) gives
  live Indian prices; news feeds are mostly free.
- **AI sorting of news** (Phase 3): a small monthly cost, to be checked against the provider's price
  list before it is switched on.

## What it will not do

No system makes 10% a day; income will be capital times a proven edge. It finds edges faster and more
honestly than one person can, and keeps them ready for when the capital is arranged.
