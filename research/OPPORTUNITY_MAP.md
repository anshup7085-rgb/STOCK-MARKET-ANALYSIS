# Opportunity map: everything that could help (28 Oct 2026)

What exists, what it would add, what it costs, and where we stand. Kept up to date, so
nothing useful stays unsaid. **Checked** means we confirmed it ourselves; **reported**
means public sources say so and we have not yet checked it.

Status: **HAVE** running · **NOW** being built or checked · **NEXT** queued · **LATER**
useful, not yet · **SKIP** not worth it.

## 1. Market data

| What | Why it can help | Source and cost | Status |
|---|---|---|---|
| NSE daily F&O file: every NIFTY option and future, its close and open interest, 2018 on | Tests weekly and end-of-day strategies on 8 years instead of waiting | NSE archive, free. **Checked**: 18 dates, April 2018 to October 2026. Weekly NIFTY options exist from 2019 | HAVE (settlement, recorder); NEXT for a past-data test |
| FII, DII, pro and client positions in index futures and options | Where the big players stand | NSE archive, free | Recorded daily since 28 Oct; how far back the archive goes: NOW (`tools/context_history_check.py`) |
| India VIX | When selling options is dangerous | NSE's index file (free); Kite minute data | Recorded daily; history: NOW |
| FII and DII cash flows | Money moving in or out of shares | NSE, latest day only; NSDL keeps FPI history from June 2014 (**reported**) | Recorded daily (morning and evening); history: LATER |
| Put-call ratio and the biggest open-interest strikes | Where option sellers have placed their bets | From NSE's daily F&O file | Recorded daily |
| NIFTY and VIX minute candles for years | Replays our intraday triggers and futures strategies | Kite historical API, inside the ₹500 a month plan; 60 days a request; back to 2015 (**reported**) | NOW: one-time Kite check runs with the next `morning` |
| Minute candles of options listed now, with open interest | Our own minute-level option history, from now on | Kite, included; expired options are not served (**reported**, checked by the Kite check) | NEXT: download every contract before it expires |
| Minute candles of **expired** options, years back | Tests the intraday option strategies on years instead of months: the biggest speed-up for the intraday goal | **Dhan Data API**, ₹499 + GST a month: 5 years, minute OHLC, volume, open interest, IV and spot, strikes ATM ± 10 for index options near expiry, 31 days a request (**reported**). Others: ICICI Breeze (free with an ICICI Direct account; depth unclear, **reported**), Upstox Plus (expired-contract API, short minute lookback, **reported**), TrueData and Global Datafeeds (paid, prices on request), a free Zenodo set of 1-minute NIFTY options for 2017 to 2020 (unknown author) | NEXT: one month of Dhan after the Kite check, if you agree |
| Tick-by-tick data and full market depth | Only for strategies faster than ours | Paid vendors | SKIP for now |
| GIFT Nifty and global cues (US index futures, crude, dollar-rupee, US yields) | Overnight direction and gap risk | Free daily sources | LATER (cheap) |
| Results dates of NIFTY's biggest companies | Results days move the index | Public calendars | NOW: step 3 |
| Economic calendar: RBI policy, US Fed, inflation, budget, elections | Volatile days, for every strategy | Public calendars | NOW: step 3 |
| News headlines, dated | Explains unusual days after the fact | News feeds | NOW: step 3 |
| Social media and Telegram tips | Mostly noise and manipulation | — | SKIP |

## 2. Testing

| What | Why | Status |
|---|---|---|
| Rules written down before the test, judged by the betting test | Stops us fooling ourselves | HAVE |
| Past-data test of weekly strategies, 2019 to 2026, end of day | A first verdict in weeks, not years | NEXT (its rules are written first) |
| Intraday replay of the explorer's strategies on minute history | The same, for intraday | NEXT, after the expired-option data |
| Quick idea screening on AlgoTest or Stockmock (AlgoTest: from ₹499 for 500 backtests, free weekly backtests, **reported**) | A fast filter for ideas like your cousin's; never proof | LATER, optional |
| Paper fills against real fills | Measures how much worse real orders fill | At the small-money stage |

## 3. Running safely

| What | Why | Status |
|---|---|---|
| Copy of `state/` off the server | The journals are the evidence, and today they live on one machine | NEXT |
| A fixed (static) IP for live orders | Required for API orders from 1 April 2026 (Zerodha support page). A Lightsail static IP is free while attached; it is then entered in the Kite developer profile | Before live |
| Phone-tap confirmation of each live order | You stay in control | Before live |
| An alert when a runner stops during the day | Today it is noticed only in the evening | LATER |
| Server reboot on a weekend | Pending system updates | Pending |

## 4. Money, rules and tax

- **Kite Connect** costs ₹500 a month (cut from ₹2,000 in May 2025, **reported**). Historical data is
  included since February 2025. From October 2026 the fee is waived in a month with ₹2,000 or more of
  brokerage (Kite forum, **reported**).
- Margin needed by each strategy, worked out before any live step.
- F&O profit is business income (ITR-3): a chartered accountant once real money starts.
- SEBI keeps changing F&O rules (expiry days, lot sizes, margins): every change goes into the events log.

## 5. Knowledge

- **Zerodha Varsity** (free): Options Theory, Option Strategies, Trading Systems, and Risk Management and
  Trading Psychology. The best use of an hour a day while the tests run.
- Your cousin's real trades, set beside our results.

## Not worth it

Paid tips, signal groups, "99% accurate" indicators, adding to losing trades, and going live before a
test passes.

## Sources (as found on 28 Oct 2026)

- Kite expired options: [forum](https://kite.trade/forum/discussion/comment/52658/); historical included:
  [forum](https://kite.trade/forum/discussion/comment/49021/); ₹500 price:
  [Business Today, 6 May 2025](https://www.businesstoday.in/markets/stocks/story/for-traders-who-can-code-zerodha-cuts-rates-as-nithin-kamath-explains-nses-new-rulebook-474897-2025-05-06);
  fee waiver: [forum](https://kite.trade/forum/discussion/comment/53028/); minute data to 2015:
  [forum](https://kite.trade/forum/discussion/6691/historical-data)
- Static IP: [Zerodha support](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/static-ip);
  Lightsail static IP: [AWS](https://docs.aws.amazon.com/lightsail/latest/userguide/understanding-static-ip-addresses-in-amazon-lightsail.html)
- Dhan expired options: [DhanHQ gem docs](https://www.rubydoc.info/gems/DhanHQ/DhanHQ/Models/ExpiredOptionsData),
  [Dhan support](https://dhan.co/support/platforms/dhanhq-api/do-dhan-provides-expired-options-data-via-the-api-s/);
  price: [Dhan support](https://dhan.co/support/platforms/dhanhq-api/how-does-the-dhanhq-data-api-subscription-work/)
- Breeze: [PyPI](https://pypi.org/project/breeze-historical-options/); Upstox:
  [announcement](https://upstox.com/developer/api-documentation/announcements/expired-instruments-api);
  Zenodo: [record](https://zenodo.org/records/10899828); AlgoTest:
  [blog](https://algotest.in/blog/best-backtesting-software-for-options-trading-in-india/)
- NSDL FPI history: [SEBI](https://www.sebi.gov.in/statistics/fpi-investment/latest.html)
