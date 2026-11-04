#!/usr/bin/env python3
"""
Is Dhan's expired-options data good enough to test on? A check before any big download.

    PYTHONPATH=. python tools/dhan_check.py

It asks for your Dhan client ID and access token (both hidden as you paste; never saved, never
printed), then fetches Dhan's minute data for the NIFTY week that expired on 3 Nov 2026 and compares
it, minute by minute, with the same options saved from Zerodha by tools/save_expiring_options.py
(state/option_minutes/2026-11-03/). It prints only counts and match rates, never a price.

Dhan's endpoint (its v2 docs, "Expired Options Data"): POST /v2/charts/rollingoption, strikes chosen
relative to ATM, minute candles with open interest. The expiry-code values are not documented where
we could read them, so it tries 0, 1 and 2 and keeps the one that matches our week.
Raw replies are kept in state/dhan/probe/ (they hold market data only, no credentials).
"""

from __future__ import annotations

import csv
import getpass
import gzip
import json
import re
import sys
import time as _time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from market.config import IST

REPO = Path(__file__).resolve().parents[1]
OURS = REPO / "state" / "option_minutes" / "2026-11-03"
OUT = REPO / "state" / "dhan" / "probe"
URL = "https://api.dhan.co/v2/charts/rollingoption"
NIFTY_ID = 13                         # NIFTY 50 in Dhan's instrument list (IDX_I)
FROM, TO = "2026-10-27", "2026-11-04"  # TO is excluded by Dhan: this covers 27 Oct to 3 Nov
FIELDS = ["open", "high", "low", "close", "volume", "oi", "iv", "strike", "spot"]
SYMBOL = re.compile(r"^NIFTY\d{2}(?:[A-Z]{3}|[1-9OND]\d{2})(\d+)(CE|PE)\.csv\.gz$")


def ask(prompt: str) -> str:
    v = getpass.getpass(prompt).strip()
    if not v:
        raise SystemExit("nothing entered; stopping")
    return v


def call(client: str, token: str, body: dict) -> tuple[int, dict | str]:
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "access-token": token, "client-id": client})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")[:300]
    except Exception as exc:                                    # noqa: BLE001 - a check: report anything
        return 0, f"{type(exc).__name__}: {str(exc)[:200]}"


def body(code: int, strike: str, right: str) -> dict:
    return {"exchangeSegment": "NSE_FNO", "interval": "1", "securityId": NIFTY_ID, "instrument": "OPTIDX",
            "expiryFlag": "WEEK", "expiryCode": code, "strike": strike, "drvOptionType": right,
            "requiredData": FIELDS, "fromDate": FROM, "toDate": TO}


def series(reply: dict, right: str) -> dict | None:
    data = reply.get("data") if isinstance(reply, dict) else None
    if not isinstance(data, dict):
        return None
    s = data.get("ce" if right == "CALL" else "pe")
    return s if isinstance(s, dict) and s.get("timestamp") else None


def ours() -> dict[tuple[float, str], dict[int, tuple[float, float]]]:
    """(strike, CE/PE) -> {epoch second: (close, oi)} from the Zerodha files."""
    out = {}
    for p in sorted(OURS.glob("*.csv.gz")):
        m = SYMBOL.match(p.name)
        if not m:
            continue
        rows = {}
        for r in csv.DictReader(gzip.open(p, "rt")):
            ts = int(datetime.fromisoformat(r["time"]).timestamp())
            rows[ts] = (float(r["close"]), float(r["oi"]) if r.get("oi") else 0.0)
        out[(float(m.group(1)), m.group(2))] = rows
    return out


def compare(s: dict, right: str, mine: dict, shift: int) -> dict:
    kind = "CE" if right == "CALL" else "PE"
    n = found = tick = pct = oi_same = 0
    days: dict[str, int] = {}
    for ts, k, c, oi in zip(s["timestamp"], s.get("strike") or [], s.get("close") or [], s.get("oi") or []):
        n += 1
        t = int(ts) + shift
        day = datetime.fromtimestamp(t, IST).date().isoformat()
        days[day] = days.get(day, 0) + 1
        row = mine.get((float(k), kind), {}).get(t)
        if row is None:
            continue
        found += 1
        diff = abs(float(c) - row[0])
        tick += diff <= 0.05 + 1e-9
        pct += diff <= 0.01 * max(row[0], 0.05) + 1e-9
        oi_same += abs(float(oi) - row[1]) < 1
    return {"points": n, "found_in_ours": found, "close_within_a_tick": tick, "close_within_1pct": pct,
            "oi_equal": oi_same, "minutes_per_day": days}


def main() -> int:
    if not OURS.exists():
        print(f"no Zerodha copy of the 3 Nov week at {OURS}: nothing to compare against")
        return 2
    mine = ours()
    print(f"our Zerodha copy: {len(mine)} options of the 3 Nov expiry")
    client = ask("Dhan client ID (hidden): ")
    token = ask("Dhan access token (hidden; paste once, then Enter): ")
    OUT.mkdir(parents=True, exist_ok=True)

    print("\n1. which expiry code is the 3 Nov week (ATM call)")
    best = None
    for code in (0, 1, 2):
        status, reply = call(client, token, body(code, "ATM", "CALL"))
        _time.sleep(0.6)
        if status != 200 or not isinstance(reply, dict):
            print(f"  code {code}: HTTP {status}: {str(reply)[:160]}")
            continue
        (OUT / f"code{code}_ATM_CALL.json").write_text(json.dumps(reply))
        s = series(reply, "CALL")
        if s is None:
            print(f"  code {code}: no data (keys {list(reply)[:6]})")
            continue
        ts = s["timestamp"]
        first, last = (datetime.fromtimestamp(int(x), IST) for x in (ts[0], ts[-1]))
        strikes = [float(k) for k in s.get("strike") or []]
        shift_hits = {sh: compare(s, "CALL", mine, sh)["found_in_ours"] for sh in (0, -60, 60, -19800, 19800)}
        shift = max(shift_hits, key=shift_hits.get)
        r = compare(s, "CALL", mine, shift)
        print(f"  code {code}: {len(ts)} minutes, {first:%d %b %H:%M} to {last:%d %b %H:%M} IST, strikes "
              f"{min(strikes):g}-{max(strikes):g}" if strikes else f"  code {code}: {len(ts)} minutes, no strike field")
        print(f"     matched to our copy: {r['found_in_ours']} of {r['points']} (time shift {shift}s); "
              f"close equal to the tick {r['close_within_a_tick']}, within 1% {r['close_within_1pct']}, OI equal {r['oi_equal']}")
        if r["found_in_ours"] and (best is None or r["found_in_ours"] > best[1]):
            best = (code, r["found_in_ours"], shift)
    if best is None:
        print("\nno expiry code matched our 3 Nov week. Send this output to Claude; do not download anything yet.")
        return 1

    code, _, shift = best
    print(f"\n2. the 3 Nov week, expiry code {code}: calls and puts from ATM-10 to ATM+10, sampled")
    total = {"points": 0, "found_in_ours": 0, "close_within_a_tick": 0, "close_within_1pct": 0, "oi_equal": 0}
    for strike in ("ATM-10", "ATM-2", "ATM-1", "ATM", "ATM+1", "ATM+2", "ATM+10"):
        for right in ("CALL", "PUT"):
            status, reply = call(client, token, body(code, strike, right))
            _time.sleep(0.6)
            if status != 200 or not isinstance(reply, dict) or series(reply, right) is None:
                print(f"  {strike:>6} {right}: HTTP {status}, no data {str(reply)[:100] if status != 200 else ''}")
                continue
            (OUT / f"code{code}_{strike}_{right}.json").write_text(json.dumps(reply))
            r = compare(series(reply, right), right, mine, shift)
            for k in total:
                total[k] += r[k]
            full_days = sum(1 for v in r["minutes_per_day"].values() if v >= 370)
            print(f"  {strike:>6} {right}: {r['points']} minutes on {len(r['minutes_per_day'])} days ({full_days} full); "
                  f"in our copy {r['found_in_ours']}; close to the tick {r['close_within_a_tick']}, "
                  f"within 1% {r['close_within_1pct']}; OI equal {r['oi_equal']}")
    f = max(total["found_in_ours"], 1)
    print(f"\nOVERALL: {total['points']} Dhan minutes; {total['found_in_ours']} found in our Zerodha copy "
          f"({total['found_in_ours'] / max(total['points'], 1):.1%}); of those, close equal to the tick "
          f"{total['close_within_a_tick'] / f:.1%}, within 1% {total['close_within_1pct'] / f:.1%}, "
          f"OI equal {total['oi_equal'] / f:.1%}")
    print("Send this whole output to Claude. Your token was not saved anywhere.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
