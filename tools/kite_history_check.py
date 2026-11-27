#!/usr/bin/env python3
"""
What this Kite Connect app can fetch from Kite's historical API. Read-only.

`morning` runs it once, in the background after the runners start: it needs the day's
login, and a second login the same day can end the first. Its output lands in
logs/kite-history-check-DATE.log and the evening report shows it.

It prints only counts and date ranges, never a price. It checks:
  1. NIFTY 50 and INDIA VIX minute candles on one day in 2015, 2018, 2021 and 2024, and last week
  2. the nearest NIFTY future: minute candles with open interest for the last week, and the
     continuous (expired contracts joined) day and minute candles
  3. NIFTY options listed now: minute candles with open interest since listing, for the
     nearest expiry's at-the-money call and the farthest expiry's
  4. a NIFTY option that has expired: whether any minute candle comes back
"""

from __future__ import annotations

import json
import os
import sys
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DONE = REPO / "logs" / "kite-history-check.done"
GAP_S = 0.4                       # Kite allows about 3 historical requests a second


def _span(rows) -> str:
    if not rows:
        return "0 candles"
    days = sorted({r["date"].date() for r in rows})
    oi = sum(1 for r in rows if r.get("oi"))
    return (f"{len(rows)} candles on {len(days)} day(s), {days[0]} to {days[-1]}"
            + (f", open interest on {oi}" if "oi" in rows[0] else ""))


def main() -> int:
    from kiteconnect import KiteConnect

    from trading.fno.feeds import refuse_secrets_in_env
    refuse_secrets_in_env()
    key, token = os.getenv("KITE_API_KEY"), os.getenv("KITE_ACCESS_TOKEN")
    if not key or not token:
        print("needs the day's login (KITE_API_KEY and KITE_ACCESS_TOKEN): run from `morning`")
        return 2
    kite = KiteConnect(api_key=key)
    kite.set_access_token(token)
    today = date.today()
    print(f"Kite historical API check, {datetime.now():%Y-%m-%d %H:%M}. Counts and dates only; no prices.")

    def hist(label, tok, start, end, interval="minute", **kw):
        _time.sleep(GAP_S)
        try:
            rows = kite.historical_data(tok, f"{start} 09:00:00", f"{end} 15:30:00", interval, **kw)
            print(f"  {label}: {_span(rows)}")
            return rows
        except Exception as exc:                                # noqa: BLE001 - reported, not raised
            print(f"  {label}: refused ({type(exc).__name__}: {str(exc)[:100]})")
            return []

    nse = {r["tradingsymbol"]: r for r in kite.instruments("NSE") if r.get("segment") == "INDICES"}
    print("1. index minute candles, one day a year")
    for sym in ("NIFTY 50", "INDIA VIX"):
        tok = nse.get(sym, {}).get("instrument_token")
        if not tok:
            print(f"  {sym}: not in the instrument list")
            continue
        for d in (date(2015, 6, 16), date(2018, 6, 19), date(2021, 6, 15), date(2024, 6, 18)):
            hist(f"{sym} {d}", tok, d, d)
        hist(f"{sym} last week", tok, today - timedelta(days=7), today - timedelta(days=1))

    nfo = [r for r in kite.instruments("NFO") if str(r.get("name")) == "NIFTY"]
    futs = sorted((r for r in nfo if r.get("instrument_type") == "FUT"), key=lambda r: r["expiry"])
    print("2. NIFTY futures")
    if futs:
        tok = futs[0]["instrument_token"]
        hist("nearest future, last week, with OI", tok, today - timedelta(days=7), today - timedelta(days=1), oi=True)
        hist("continuous day candles 2015-2020", tok, date(2015, 1, 1), date(2020, 6, 1), "day", continuous=True)
        hist("continuous minute candles, 3 days in 2023", tok, date(2023, 6, 13), date(2023, 6, 15), continuous=True)
    else:
        print("  no NIFTY future in the NFO list")

    print("3. NIFTY options listed now (at-the-money calls)")
    spot_rows = []
    if nse.get("NIFTY 50"):
        _time.sleep(GAP_S)
        try:
            spot_rows = kite.historical_data(nse["NIFTY 50"]["instrument_token"], f"{today - timedelta(days=7)} 09:00:00",
                                             f"{today} 15:30:00", "day")
        except Exception:                                       # noqa: BLE001
            spot_rows = []
    calls = [r for r in nfo if r.get("instrument_type") == "CE"]
    expiries = sorted({r["expiry"] for r in calls})
    if spot_rows and expiries:
        spot = spot_rows[-1]["close"]                           # used only to pick the strike; not printed
        for label, exp in (("nearest expiry", expiries[0]), ("farthest expiry", expiries[-1])):
            atm = min((r for r in calls if r["expiry"] == exp), key=lambda r: abs(r["strike"] - spot))
            start, mid = today - timedelta(days=110), today - timedelta(days=55)   # 60 days at most a request
            hist(f"{label} ({exp}), {start} to {mid - timedelta(days=1)}", atm["instrument_token"],
                 start, mid - timedelta(days=1), oi=True)
            hist(f"{label} ({exp}), {mid} to {today}", atm["instrument_token"], mid, today, oi=True)
        print(f"  expiries listed now: {len(expiries)}, {expiries[0]} to {expiries[-1]}")
    else:
        print("  could not pick a strike (no index reading or no listed calls)")

    print("4. a NIFTY option that has expired")
    masters = sorted((REPO / "state" / "fno_explore" / "master").glob("*.json"))
    old = None
    for p in reversed(masters):
        cs = [c for c in json.loads(p.read_text())["contracts"]
              if c["right"] == "CE" and c.get("feed_id") and date.fromisoformat(c["expiry"]) < today]
        if cs:
            last = max(c["expiry"] for c in cs)                 # the most recent expiry; its middle strike
            cs = sorted((c for c in cs if c["expiry"] == last), key=lambda c: c["strike"])
            old = cs[len(cs) // 2]
            break
    if old:
        exp = date.fromisoformat(old["expiry"])
        hist(f"{old['symbol']} (expired {exp}), its last 3 days", int(old["feed_id"]), exp - timedelta(days=2), exp, oi=True)
    else:
        print("  no expired contract in the explorer's saved masters to try")

    DONE.parent.mkdir(parents=True, exist_ok=True)
    DONE.write_text(f"{datetime.now().isoformat(timespec='seconds')}\n")
    print("done. This check runs once; delete logs/kite-history-check.done to run it again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
