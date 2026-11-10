# Opportunity map: everything that could help (28 Oct 2026; statuses checked 10 Nov)

What exists, what it would add, what it costs, and where we stand. Kept up to date, so
nothing useful stays unsaid. **Checked** means we confirmed it ourselves; **reported**
means public sources say so and we have not yet checked it.

Status: **HAVE** running · **NOW** being built or checked · **NEXT** queued · **LATER**
useful, not yet · **SKIP** not worth it.

## 1. Market data

| What | Why it can help | Source and cost | Status |
|---|---|---|---|
| NSE daily F&O file: every NIFTY option and future, its close and open interest, 2018 on | Tests weekly and end-of-day strategies on 8 years instead of waiting | NSE archive, free. **Checked**: 18 dates, April 2018 to October 2026. Weekly NIFTY options exist from 2019 | HAVE (settlement, recorder); NEXT for a past-data test |
| FII, DII, pro and client positions in index futures and options | Where the big players stand | NSE archive, free. **Checked**: participant-wise open interest and volume back to April 2012 | Recorded daily since 28 Oct; history: NOW (`deploy/backfill.sh`) |
| India VIX | When selling options is dangerous | NSE's index file, free. **Checked**: in it from October 2014 (not in the 2012–2014 files). Kite minute data too | Recorded daily; history: NOW (`deploy/backfill.sh`) |
| FII and DII cash flows | Money moving in or out of shares | NSE's API and NSDL's FPI reports both **refuse the cloud server** (checked 28 Oct to 2 Nov: HTTP 403, dropped connection). Recorded instead: NSE's FII derivatives statistics file, from the archive the server can read (**checked** back to 2015) | FII stats kept raw daily since 2 Nov; cash flows SKIP unless a source the server can reach turns up |
| Put-call ratio and the biggest open-interest strikes | Where option sellers have placed their bets | From NSE's daily F&O file | Recorded daily |
| NIFTY and VIX minute candles for years | Replays our intraday triggers and futures strategies | Kite historical API, inside the ₹500 a month plan; 60 days a request. **Checked** 29 Oct: full 375-candle days in 2015, 2018, 2021 and 2024. Futures: day candles back to 2015 joined across expiries; minute candles only for the live contract | NOW: `tools/index_minutes_download.py`, run by `morning` from 9 Nov (2015 on, then the current year daily); first use: the intraday momentum exploration (`research/INTRADAY_LOG.md` step 9) |
| Minute candles of options listed now, with open interest | Our own minute-level option history, from now on | Kite, included. **Checked** 29 Oct: the 3 Nov weekly has minute data with open interest from its listing on 29 Sep; an option that expired on 27 Oct is refused | HAVE from 30 Oct: `tools/save_expiring_options.py`, started by `morning`, saves every NIFTY contract within 15% of the index expiring within 7 days, at 15:40 |
| Minute candles of **expired** options, years back | Tests the intraday option strategies on years instead of months: the biggest speed-up for the intraday goal | **Dhan Data API** (Dhan: a SEBI-registered broker, INZ000006031 per its own pages, live since November 2021), ₹499 + GST a month. Its v2 docs (the holder's export of 27 Oct 2026, read 4 Nov): `POST /charts/rollingoption` with an `access-token` header, securityId 13 for NIFTY, expiryCode 1 = near, 2 = next, 3 = far, up to 5 years, weekly or monthly expiries, minute OHLC, volume, open interest, IV and spot, strikes chosen relative to ATM (ATM ± 10 for index options near expiry, ATM ± 3 for every other contract), `toDate` excluded, 5 data requests a second and 100,000 a day. The docs say both one day and 30 days a call. Not yet known: how complete the minutes are (`tools/dhan_check.py` measures it against our Zerodha copy). Others: ICICI Breeze (free with an ICICI Direct account; depth unclear, **reported**), Upstox Plus (expired-contract API, short minute lookback, **reported**), TrueData and Global Datafeeds (paid, prices on request), a free Zenodo set of 1-minute NIFTY options for 2017 to 2020 (unknown author) | HAVE: checked against our Zerodha copy (4 Nov: OI equal 99.7%, close inside Zerodha's minute range 97.5%, no lean) and downloaded (5 Nov: Oct 2021 to Nov 2026, 0 failed). Exploration reads only up to Oct 2024; Nov 2024 to Oct 2026 is held back for registered tests (`research/INTRADAY_LOG.md`). Turn off the plan's renewal before 4 Dec |
| Minute candles of expired options on the **other indexes** (SENSEX, BANKNIFTY, FINNIFTY, MIDCPNIFTY) | Checks the expiry-day effect on several times as many expiry days, on indexes it was not found on; SENSEX still has a weekly expiry | The same Dhan plan, no extra cost, until it ends on 4 Dec. Its documents say the API serves NSE and BSE index options (read 10 Nov, not yet tried) | NOW: `tools/dhan_download_more.py` |
| Tick-by-tick data and full market depth | Only for strategies faster than ours | Paid vendors | SKIP for now |
| GIFT Nifty and global cues (US index futures, crude, dollar-rupee, US yields) | Overnight direction and gap risk | Free daily sources | LATER (cheap) |
| Results dates of NIFTY's biggest companies | Results days move the index | Public calendars | NEXT (promised 28 Oct, not yet built) |
| Economic calendar: RBI policy, US Fed, inflation, budget, elections | Volatile days, for every strategy | Public calendars | NEXT (promised 28 Oct, not yet built) |
| News headlines, dated | Explains unusual days after the fact | News feeds | LATER |
| Social media and Telegram tips | Mostly noise and manipulation | — | SKIP |

## 2. Testing

| What | Why | Status |
|---|---|---|
| Rules written down before the test, judged by the betting test | Stops us fooling ourselves | HAVE |
| Past-data test of weekly strategies, 2019 to 2026, end of day | A first verdict in weeks, not years | DONE: V-H **failed** (31 Oct): 243 trades, 69.5% won, mean R −0.021. V never goes live. The downloaded files (1,923 sessions) stay on the server for later registered tests |
| Intraday replay of the explorer's strategies on minute history | The same, for intraday | DONE (5–6 Nov): every strategy loses or is flat after costs, Oct 2021–Oct 2024; only the expiry-day straddle shows a small effect (`research/INTRADAY_LOG.md`) |
| Quick idea screening on AlgoTest or Stockmock (AlgoTest: from ₹499 for 500 backtests, free weekly backtests, **reported**) | A fast filter for ideas like your cousin's; never proof | LATER, optional |
| Paper fills against real fills | Measures how much worse real orders fill | At the small-money stage |

## 3. Running safely

| What | Why | Status |
|---|---|---|
| Copy of `state/` off the server | The journals are the evidence | HAVE: Lightsail automatic snapshots, daily at 22:30 IST, last seven kept (enabled 28 Oct). An off-AWS copy: LATER |
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
- **Futures cost more than options per point since April 2026.** A NIFTY futures lot's round trip is about 7.4 index bps (STT 0.05% of the value is most of it); the same exposure through an at-the-money option is about 1.4 bps before theta. Short directional trades should be priced both ways before any is chosen.
- SEBI keeps changing F&O rules (expiry days, lot sizes, margins): every change goes into the events log.

## 5. Knowledge

- **Zerodha Varsity** (free): Options Theory, Option Strategies, Trading Systems, Risk Management and
  Trading Psychology. Optional for the holder (studies come first): the system does not need it, and each
  strategy is explained in plain words before any money goes near it.
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
