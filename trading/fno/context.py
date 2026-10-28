"""
The daily market context, recorded so it can be tested later: who holds what in index
futures and options (FII, DII, pro, client), India VIX, the put-call ratio and the biggest
open-interest strikes of the day's NIFTY options, and the FII/DII cash flows.

DATA ONLY. Nothing here trades, ranks or advises; it keeps a dated, hash-stamped record of
public NSE figures so that, after a few months, "does this predict anything?" can be asked
of real history. A source that cannot be fetched is recorded as unavailable with its reason
and is retried on the next run; no number is ever filled in.

Sources (public, no login):
  participant_oi   nsearchives .../content/nsccl/fao_participant_oi_DDMMYYYY.csv
  index_close      nsearchives .../content/indices/ind_close_all_DDMMYYYY.csv   (India VIX, NIFTY 50)
  fo_bhavcopy      nsearchives .../content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
  fii_dii_cash     nseindia.com/api/fiidiiTradeReact   (the latest session only, and never kept
                                                         by NSE: it is fetched once a run, filed
                                                         under the session it names, and its raw
                                                         reply is kept. The morning run catches
                                                         the previous session's flows)

The small files are kept raw under raw/DAY/, so a parser fixed later can re-read them. The
F&O file is not kept (NSE's archive keeps it); its SHA-256 is.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Callable

from market.calendar import load_calendar
from market.config import IST

ARCHIVE = "https://nsearchives.nseindia.com/content"
URLS = {
    "participant_oi": ARCHIVE + "/nsccl/fao_participant_oi_{d:%d%m%Y}.csv",
    "index_close": ARCHIVE + "/indices/ind_close_all_{d:%d%m%Y}.csv",
    "fo_bhavcopy": ARCHIVE + "/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip",
}
FIIDII_PAGE, FIIDII_API = "https://www.nseindia.com/", "https://www.nseindia.com/api/fiidiiTradeReact"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*"}
SOURCES = ("participant_oi", "index_close", "fo_bhavcopy", "fii_dii_cash")


class Unavailable(Exception):
    """A source that could not be fetched or read; its text becomes the recorded reason."""


# -- fetching --------------------------------------------------------------------------

def fetch_url(url: str, timeout: float = 20.0) -> bytes:
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout).read()
    except Exception as exc:                                    # noqa: BLE001 - recorded as the reason
        raise Unavailable(f"{type(exc).__name__}: {str(exc)[:120]}") from exc


def fetch_fii_dii(timeout: float = 15.0) -> bytes:
    """NSE wants a browser-like visit first (cookies), then the API call with a referer."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    try:
        opener.open(urllib.request.Request(FIIDII_PAGE, headers=HEADERS), timeout=timeout).read()
        return opener.open(urllib.request.Request(FIIDII_API, headers={**HEADERS, "Referer": FIIDII_PAGE}),
                           timeout=timeout).read()
    except Exception as exc:                                    # noqa: BLE001
        raise Unavailable(f"{type(exc).__name__}: {str(exc)[:120]}") from exc


# -- parsing (each takes the raw bytes and returns plain data, or raises Unavailable) ---------

def _num(text) -> float | None:
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return None


def parse_participant_oi(raw: bytes, day: date) -> dict:
    """Open interest by participant: {client: {column: contracts}}. The file's first line is a title."""
    lines = raw.decode("utf-8-sig", errors="replace").splitlines()
    at = next((i for i, ln in enumerate(lines) if ln.strip().lower().startswith("client type")), None)
    if at is None:
        raise Unavailable("no 'Client Type' header in the participant file")
    out = {}
    for row in csv.DictReader(lines[at:]):
        row = {k.strip(): v for k, v in row.items() if k}
        who = (row.get("Client Type") or "").strip()
        if who:
            out[who.upper()] = {k: n for k, v in row.items() if k != "Client Type" and (n := _num(v)) is not None}
    if not {"FII", "DII"} <= set(out):
        raise Unavailable(f"participant file lacks FII and DII rows (has {sorted(out)})")
    return out


def parse_index_close(raw: bytes, day: date) -> dict:
    """India VIX and NIFTY 50 from NSE's all-index close file; the file's own date must be `day`."""
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))))
    if not rows:
        raise Unavailable("empty index close file")
    out = {}
    for row in rows:
        r = {k.strip().lower(): v for k, v in row.items() if k}
        name = (r.get("index name") or "").strip().lower()
        if name not in ("india vix", "nifty 50"):
            continue
        shown = (r.get("index date") or "").strip()
        if shown:
            try:
                got = datetime.strptime(shown, "%d-%m-%Y").date()
            except ValueError:
                got = None
            if got is not None and got != day:
                raise Unavailable(f"the index file is dated {got}, not {day}")
        close = next((_num(v) for k, v in r.items() if k.startswith("closing")), None)
        pct = next((_num(v) for k, v in r.items() if k.startswith("change(%)") or k.startswith("change (%)")), None)
        out["vix" if name == "india vix" else "nifty"] = {"close": close, "change_pct": pct}
    if "vix" not in out or out["vix"]["close"] is None:
        raise Unavailable("India VIX not in the index close file")
    return out


def _bhav_rows(raw: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        return list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig"))))


def parse_bhavcopy(raw: bytes, day: date, underlying: str = "NIFTY") -> dict:
    """Put-call ratios and the biggest-OI strikes from the day's NIFTY options, per expiry, plus
    the nearest-expiry future's close and OI."""
    try:
        rows = _bhav_rows(raw)
    except (zipfile.BadZipFile, StopIteration) as exc:
        raise Unavailable(f"not a bhavcopy zip: {exc}") from exc
    opts: dict[str, dict] = {}
    futs = []
    for r in rows:
        if (r.get("TckrSymb") or "").strip().upper() != underlying:
            continue
        kind, exp = (r.get("FinInstrmTp") or "").strip().upper(), (r.get("XpryDt") or "").strip()
        if kind == "IDF":
            futs.append((exp, _num(r.get("ClsPric")), _num(r.get("OpnIntrst"))))
        elif kind == "IDO" and exp >= day.isoformat():
            right, k = (r.get("OptnTp") or "").strip().upper(), _num(r.get("StrkPric"))
            if right in ("CE", "PE") and k is not None:
                e = opts.setdefault(exp, {"CE": {}, "PE": {}, "vol": {"CE": 0.0, "PE": 0.0}})
                e[right][k] = e[right].get(k, 0.0) + (_num(r.get("OpnIntrst")) or 0.0)
                e["vol"][right] += _num(r.get("TtlTradgVol")) or 0.0
    if not opts:
        raise Unavailable(f"no {underlying} option rows expiring on or after {day} in the file")
    out: dict = {"expiries": {}}
    for exp in sorted(opts)[:3]:                                # the three nearest expiries are enough
        e = opts[exp]
        ce, pe = sum(e["CE"].values()), sum(e["PE"].values())
        out["expiries"][exp] = {
            "call_oi": ce, "put_oi": pe, "pcr_oi": round(pe / ce, 4) if ce else None,
            "pcr_volume": round(e["vol"]["PE"] / e["vol"]["CE"], 4) if e["vol"]["CE"] else None,
            "max_call_oi_strike": max(e["CE"], key=e["CE"].get) if e["CE"] else None,
            "max_put_oi_strike": max(e["PE"], key=e["PE"].get) if e["PE"] else None,
        }
    out["nearest_expiry"] = sorted(opts)[0]
    near = sorted(f for f in futs if f[0] >= day.isoformat())
    if near:
        out["future"] = {"expiry": near[0][0], "close": near[0][1], "oi": near[0][2]}
    return out


def parse_fii_dii(raw: bytes) -> tuple[date, dict]:
    """(the session NSE's reply names, its cash-market FII/DII flows in Rs crore). NSE serves only
    its latest session, so the reply is filed under the date it states, never under the run's day."""
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Unavailable(f"not JSON: {str(exc)[:80]}") from exc
    out, seen = {}, set()
    for it in items if isinstance(items, list) else []:
        cat = str(it.get("category", "")).upper()
        who = "fii" if cat.startswith("FII") else "dii" if cat.startswith("DII") else None
        if who is None:
            continue
        try:
            seen.add(datetime.strptime(str(it.get("date")), "%d-%b-%Y").date())
        except ValueError:
            raise Unavailable(f"unreadable date {it.get('date')!r}") from None
        out[who] = {"buy": _num(it.get("buyValue")), "sell": _num(it.get("sellValue")),
                    "net": _num(it.get("netValue"))}
    if not {"fii", "dii"} <= set(out):
        raise Unavailable("FII/DII rows not found")
    if len(seen) != 1:
        raise Unavailable(f"the FII and DII rows name different dates: {sorted(seen)}")
    return seen.pop(), out


PARSERS: dict[str, Callable[[bytes, date], dict]] = {          # the dated archive files
    "participant_oi": parse_participant_oi, "index_close": parse_index_close, "fo_bhavcopy": parse_bhavcopy,
}
KEEP_RAW = {"participant_oi": "csv", "index_close": "csv", "fii_dii_cash": "json"}


# -- the record ----------------------------------------------------------------------------

def context_dir(root: Path) -> Path:
    return Path(root) / "state" / "market_context"


def read_day(root: Path, day: date | str) -> dict | None:
    p = context_dir(root) / f"{day}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _write(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1, sort_keys=True))
    os.replace(tmp, path)


def _keep_raw(root: Path, day: date, name: str, raw: bytes) -> None:
    if name in KEEP_RAW:
        p = context_dir(root) / "raw" / day.isoformat() / f"{name}.{KEEP_RAW[name]}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)


def _ok(raw: bytes, data: dict, now: datetime) -> dict:
    return {"status": "ok", "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            "at": now.isoformat(timespec="seconds"), "data": data}


def record_day(root: Path, day: date, fetch: Callable[[str, date], bytes], now: datetime | None = None,
               flows: tuple | None = None) -> dict:
    """Fetch each archive file not yet recorded OK for `day` and save the day's record. `flows` is this
    run's one FII/DII reply: ("ok", session, data, raw) or ("unavailable", reason), or None if it was not
    fetched. Sources already recorded OK are kept as they are: never refetched, never changed."""
    now = now or datetime.now(IST)
    rec = read_day(root, day) or {"day": day.isoformat(), "sources": {}}
    src = rec["sources"]
    for name, parse in PARSERS.items():
        if src.get(name, {}).get("status") == "ok":
            continue
        try:
            raw = fetch(name, day)
            src[name] = _ok(raw, parse(raw, day), now)
            _keep_raw(root, day, name, raw)
        except Unavailable as exc:
            src[name] = {"status": "unavailable", "reason": str(exc)[:200], "at": now.isoformat(timespec="seconds")}
    if src.get("fii_dii_cash", {}).get("status") != "ok":
        if flows and flows[0] == "ok" and flows[1] == day:
            src["fii_dii_cash"] = _ok(flows[3], flows[2], now)
            _keep_raw(root, day, "fii_dii_cash", flows[3])
        else:
            why = ("not fetched" if flows is None else flows[1] if flows[0] == "unavailable"
                   else f"NSE now serves {flows[1]} only; this day's flows were not caught while served")
            src["fii_dii_cash"] = {"status": "unavailable", "reason": str(why)[:200],
                                   "at": now.isoformat(timespec="seconds")}
    _write(context_dir(root) / f"{day}.json", rec)
    return rec


def default_fetch(name: str, day: date | None) -> bytes:
    return fetch_fii_dii() if name == "fii_dii_cash" else fetch_url(URLS[name].format(d=day))


def trading_days_back(today: date, n: int, cal) -> list[date]:
    """The last `n` trading days up to and including `today`, oldest first."""
    out, d = [], today
    while len(out) < n and (today - d).days < 7 * n + 14:
        if cal.is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def record_recent(root: Path, today: date, days: int = 5, fetch=default_fetch, now: datetime | None = None,
                  calendar=None) -> list[dict]:
    """The last `days` trading days up to today, oldest first. The FII/DII reply is fetched once."""
    span = trading_days_back(today, days, calendar or load_calendar())
    flows = None
    if any((read_day(root, d) or {}).get("sources", {}).get("fii_dii_cash", {}).get("status") != "ok" for d in span):
        try:
            raw = fetch("fii_dii_cash", None)
            session, data = parse_fii_dii(raw)
            flows = ("ok", session, data, raw)
        except Unavailable as exc:
            flows = ("unavailable", str(exc))
    return [record_day(root, d, fetch, now, flows) for d in span]


# -- one screen ------------------------------------------------------------------------------

def _g(v) -> str:
    return "n/a" if v is None else f"{v:g}"


def _signed(v) -> str:
    return "n/a" if v is None else f"{v:+,.0f}"


def summary(rec: dict | None) -> list[str]:
    if not rec:
        return ["not recorded yet"]
    s, out = rec["sources"], []
    for name in SOURCES:
        if s.get(name, {}).get("status") != "ok":
            out.append(f"  {name}: unavailable ({s.get(name, {}).get('reason', 'not tried')[:90]})")
    oi = s.get("participant_oi", {}).get("data")
    if oi:
        f = oi["FII"]
        fut_l, fut_s = f.get("Future Index Long"), f.get("Future Index Short")
        if fut_l is not None and fut_s:
            out.append(f"  FII index futures: long {fut_l:,.0f}, short {fut_s:,.0f}, long share {fut_l / (fut_l + fut_s):.0%}")
    vix = s.get("index_close", {}).get("data", {}).get("vix")
    if vix:
        out.append(f"  India VIX {_g(vix['close'])}" + (f" ({vix['change_pct']:+.2f}%)" if vix["change_pct"] is not None else ""))
    bh = s.get("fo_bhavcopy", {}).get("data")
    if bh:
        near = bh["expiries"][bh["nearest_expiry"]]
        out.append(f"  NIFTY {bh['nearest_expiry']} options: put-call OI {near['pcr_oi']}, "
                   f"biggest call OI at {_g(near['max_call_oi_strike'])}, biggest put OI at {_g(near['max_put_oi_strike'])}")
    fl = s.get("fii_dii_cash", {}).get("data")
    if fl:
        out.append(f"  cash flows Rs crore: FII {_signed(fl['fii']['net'])}, DII {_signed(fl['dii']['net'])}")
    return out or ["  nothing recorded"]
