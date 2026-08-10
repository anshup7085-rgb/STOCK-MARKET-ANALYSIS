# Setup

## 1. Install

```bash
cd claude_market_agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 2. Verify the wiring before touching a real feed

```bash
PYTHONPATH=. python3 tests/test_offline.py     # 16 tests, no network needed
python run_scan.py --provider mock --symbols TCS INFY HAL
```

`mock` generates synthetic prices. It exists to prove the plumbing works, and it
labels its own output `NOT REAL` so you cannot mistake a smoke test for a signal.

## 3. Free tier — start here

`MARKET_PROVIDER=yfinance` in `.env`. No signup, no key.

```bash
python run_scan.py --symbols TCS INFY RELIANCE HAL --json scans/today.json
```

**What you get:** EOD OHLCV for NSE, ~20 years of history, indices via `^NSEI`
and `^NSEBANK`. Enough for the daily-timeframe swing horizon `PROMPT.md`
specifies.

**What you don't:** live quotes, intraday bars, open interest, option chains,
FII/DII flows, delivery volumes, or corporate-action-adjusted series. Yahoo's
NSE data also carries occasional bad ticks — the liquidity and staleness gates
catch the worst, not all.

## 4. Kite Connect — when you want real OI

Zerodha's API is a paid monthly subscription, and historical data is billed
separately. **Check current pricing on the Kite developer console before
subscribing** — it changes, and I'd rather you verify than take my word.

Setup:

1. Create an app at the Kite developer console. You get an **API key** and an
   **API secret**.
2. Get a daily access token. Zerodha's tokens expire every morning, so this is a
   once-a-day step you run yourself:

```python
# token.py — run once each morning, never commit it
from kiteconnect import KiteConnect
kite = KiteConnect(api_key="your_api_key")
print(kite.login_url())          # open in a browser, log in, copy request_token from the redirect
data = kite.generate_session("REQUEST_TOKEN", api_secret="your_api_secret")
print(data["access_token"])
```

3. Export it:

```bash
export KITE_API_KEY=...
export KITE_ACCESS_TOKEN=...
export MARKET_PROVIDER=kite
```

**Security notes, matching the rules in `CLAUDE.md`:**

- Your password, PIN and TOTP never enter this project. Only Zerodha's own login
  page sees them.
- Your API *secret* is used once in the exchange above and is not stored by any
  module here.
- `.env` is gitignored. Keep it that way.
- Every module in `market/providers/` is read-only. Nothing can place an order.

Automating the daily token means automating a 2FA login. Don't. Type it.

## 5. Risk parameters

```bash
ACCOUNT_EQUITY=            # leave EMPTY to get sizing formulas instead of rupee amounts
RISK_PER_TRADE_PCT=1.0
MAX_SINGLE_POSITION_PCT=20.0
```

Leaving `ACCOUNT_EQUITY` unset is deliberate and supported — `RISK_POLICY.md`
says to give the formula rather than invent a number, and the code does exactly
that. Set it only when you want real quantities.

## 6. Catalysts

```bash
cp catalysts.example.json catalysts.json
python run_scan.py --catalysts catalysts.json
```

Without this file the catalyst block (15 pts) stays unscored and `coverage`
drops, which correctly lowers confidence. The scanner will not guess an event
from a headline — you supply the source and the date.

## 7. Extending the universe

`market/universe.py` ships a ~47-name liquid starting list. For a full scan,
replace `DEFAULT_UNIVERSE` with the current F&O list or a Nifty 500 constituent
dump. On Kite you can pull it live from the instrument master; on yfinance
you'll need a CSV from the NSE site.

---

## Known limitations

Read these before you trust an output.

- **No corporate-action handling.** Splits, bonuses and demergers will distort
  history. `RISK_POLICY.md` says to reject unresolved corporate-action data; the
  code does not yet detect it. Check manually.
- **No F&O ban list.** A name in the ban period can score well and be untradeable.
- **No NSE holiday calendar.** `market_status()` checks weekday and clock only.
- **Breadth is unavailable.** Advance/decline needs a feed this project lacks, so
  the regime call rests on the benchmark alone — a genuine weakness, since a
  benchmark can hold up while breadth rots underneath.
- **Backtesting is not implemented.** The no-lookahead property is tested, so the
  indicators are safe to backtest with, but the harness isn't written.
- **Slippage and charges are excluded** from every max-loss figure. On a
  high-volatility name they are not a rounding error.
