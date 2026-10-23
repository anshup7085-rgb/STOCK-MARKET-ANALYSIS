"""
Read-only F&O market data: the contract master, option quotes with the touch,
the index value, and the index's official close for settlement.

Paper only. No class here has, or forwards to, a method that places, modifies or
cancels an order. The broker SDKs do have them; nothing here reaches for them.

Credentials follow the project's rule: the access token is minted by hand each
morning and exported into the shell that runs the paper runner. Nothing here
logs in, reads a TOTP seed, or stores a token. A feed refuses to start if a
TOTP seed, password, PIN or API secret is present in the environment.

Kite's quote response (depth, timestamp) follows pykiteconnect 5.2.2. Groww's
quote and option-chain response shapes were NOT independently confirmed when
V was registered, so its parsing accepts the plausible key names and refuses,
by name, anything it does not recognise. Run `python run_trader.py fno probe`
on the first morning: it prints the response shapes so a mismatch is found
before a pass depends on it.
"""

from __future__ import annotations

import math
import os
import re
import time as _time
from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta

import numpy as np

from market.calendar import TradingCalendar, load_calendar
from market.config import IST
from market.providers.base import DataUnavailable
from trading.fno.model import (ContractMaster, IndexQuote, OptionContract, Quote, as_ist,
                               from_groww_frame, from_kite_rows, futures_from_groww_frame,
                               futures_from_kite_rows)

# A broker or API secret, TOTP seed, password or broker PIN. Twilio's API secret is
# the one exception the project allows (deploy/lib.sh): unattended alerts need it.
# Other services' credentials (cloud keys and the like) are not the runner's concern.
_FORBIDDEN_ENV = re.compile(
    r"((KITE|GROWW|ZERODHA|BROKER)\w*SECRET|^API_SECRET$|_API_SECRET$|TOTP|PASSWORD|PASSWD"
    r"|^(KITE|GROWW|ZERODHA)_\w*PIN$|^M?PIN$|LIVE_TRADING_ACK)", re.I)


def refuse_secrets_in_env() -> None:
    bad = sorted(k for k in os.environ if _FORBIDDEN_ENV.search(k) and not k.upper().startswith("TWILIO_"))
    if bad:
        raise DataUnavailable(
            f"refusing to start: {', '.join(bad)} set in the environment. None of these belong "
            "where the paper runner can see them; export only the day's access token.")


class FnoFeed(ABC):
    key = "abstract"
    name = "abstract"
    is_mock = False

    @abstractmethod
    def contract_master(self, underlying: str) -> ContractMaster: ...

    @abstractmethod
    def index_quote(self, underlying: str) -> IndexQuote: ...

    @abstractmethod
    def quotes(self, contracts: list[OptionContract]) -> dict[str, Quote]: ...

    @abstractmethod
    def index_close(self, underlying: str, day: date) -> float | None:
        """The index's official close for `day`, or None if the feed does not have it yet."""

    def basket_margin(self, legs: list[dict], qty: int) -> dict | None:
        """The broker's margin for the four orders as one basket, where its API gives it."""
        return None

    def broker_charges(self, legs: list[dict], qty: int) -> dict | None:
        """The broker's own computed charges per order, where its API gives them."""
        return None

    def fno_master(self, underlying: str) -> ContractMaster:
        """Options and near futures together, for the intraday explorer. V reads contract_master only."""
        raise DataUnavailable(f"{self.name}: no futures listing")

    def probe(self, underlying: str) -> dict:
        """Response shapes for the first-morning check. Values that are prices are not printed."""
        return {}


def _top(levels) -> tuple[float | None, int | None]:
    """Best level of a depth side: the first with a positive price."""
    for lvl in levels or []:
        try:
            price = float(lvl.get("price") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if price > 0:
            qty = lvl.get("quantity")
            return price, (int(qty) if qty is not None else None)
    return None, None


def _first(d: dict, keys: tuple[str, ...]):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return None


def _shape(obj, depth: int = 0):
    """Keys and types only, never values: what `probe` prints."""
    if depth > 3:
        return "..."
    if isinstance(obj, dict):
        return {str(k): _shape(v, depth + 1) for k, v in list(obj.items())[:40]}
    if isinstance(obj, list):
        return [_shape(obj[0], depth + 1), f"... {len(obj)} items"] if obj else []
    return type(obj).__name__


# -- Zerodha Kite Connect ------------------------------------------------------

class KiteFnoFeed(FnoFeed):
    key = "kite"
    name = "Zerodha Kite Connect (read-only)"
    INDEX = {"NIFTY": ("NSE", "NIFTY 50")}
    BATCH = 200                       # the per-call cap was not confirmed; stay well under it
    QUOTE_GAP_S = 1.05                # Kite is reported to allow one quote request a second

    def __init__(self):
        refuse_secrets_in_env()
        key, token = os.getenv("KITE_API_KEY"), os.getenv("KITE_ACCESS_TOKEN")
        if not key or not token:
            raise DataUnavailable("KITE_API_KEY and KITE_ACCESS_TOKEN must be exported "
                                  "(mint today's token with python tools/kite_session.py)")
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:  # pragma: no cover
            raise DataUnavailable("Run: pip install kiteconnect") from exc
        self._kite = KiteConnect(api_key=key)
        self._kite.set_access_token(token)
        self._index_token: dict[str, int] = {}
        self._last_quote = None

    def _quote(self, keys: list[str]) -> dict:
        """Every /quote call, spaced: a rate-limit refusal at every slot would make every week MISSED."""
        if self._last_quote is not None:
            wait = self._last_quote + self.QUOTE_GAP_S - _time.monotonic()
            if wait > 0:
                _time.sleep(wait)
        try:
            return self._kite.quote(keys)
        finally:
            self._last_quote = _time.monotonic()

    def contract_master(self, underlying: str) -> ContractMaster:
        try:
            rows = self._kite.instruments("NFO")
        except Exception as exc:
            raise DataUnavailable(f"Kite NFO instrument dump failed: {exc}") from exc
        return ContractMaster(from_kite_rows(rows, underlying), self.name, datetime.now(IST))

    def fno_master(self, underlying: str) -> ContractMaster:
        try:
            rows = self._kite.instruments("NFO")
        except Exception as exc:
            raise DataUnavailable(f"Kite NFO instrument dump failed: {exc}") from exc
        futs = futures_from_kite_rows(rows, underlying)
        if not futs:
            raise DataUnavailable(f"Kite's NFO dump lists no {underlying} futures")
        return ContractMaster(from_kite_rows(rows, underlying) + futs, self.name, datetime.now(IST))

    def _index_key(self, underlying: str) -> str:
        exch, sym = self.INDEX[underlying]
        return f"{exch}:{sym}"

    def index_quote(self, underlying: str) -> IndexQuote:
        key = self._index_key(underlying)
        try:
            d = self._quote([key]).get(key)
        except Exception as exc:
            raise DataUnavailable(f"Kite quote for {key} failed: {exc}") from exc
        if not d or not d.get("last_price"):
            raise DataUnavailable(f"Kite returned no value for {key}")
        return IndexQuote(float(d["last_price"]), as_ist(d.get("timestamp")), datetime.now(IST))

    def quotes(self, contracts: list[OptionContract]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for i in range(0, len(contracts), self.BATCH):
            chunk = contracts[i:i + self.BATCH]
            keys = [f"{c.exchange}:{c.symbol}" for c in chunk]
            try:
                data = self._quote(keys)
            except Exception as exc:
                raise DataUnavailable(f"Kite option quotes failed: {exc}") from exc
            now = datetime.now(IST)
            for c, k in zip(chunk, keys):
                d = data.get(k)
                if not d:
                    continue
                depth = d.get("depth") or {}
                bid, bq = _top(depth.get("buy"))
                ask, aq = _top(depth.get("sell"))
                out[c.symbol] = Quote(c.symbol, bid, ask, bq, aq, d.get("last_price"),
                                      as_ist(d.get("timestamp")), now)
        return out

    def index_close(self, underlying: str, day: date) -> float | None:
        exch, sym = self.INDEX[underlying]
        try:
            if underlying not in self._index_token:
                hit = [r for r in self._kite.instruments(exch) if r.get("tradingsymbol") == sym]
                if not hit:
                    return None
                self._index_token[underlying] = int(hit[0]["instrument_token"])
            rows = self._kite.historical_data(self._index_token[underlying],
                                              f"{day} 00:00:00", f"{day} 23:59:59", "day")
        except Exception:
            return None
        for r in rows or []:
            d = r.get("date")
            if d is not None and as_ist(d) is not None and as_ist(d).date() == day and r.get("close"):
                return float(r["close"])
        return None

    def _orders(self, legs: list[dict], qty: int, price_key: str) -> list[dict]:
        return [{"exchange": "NFO", "tradingsymbol": leg["symbol"], "transaction_type": leg["side"],
                 "variety": "regular", "product": "NRML", "order_type": "LIMIT",
                 "quantity": qty, price_key: leg["price"], **({"trigger_price": 0} if price_key == "price" else {}),
                 **({"order_id": f"paper-{leg['role']}"} if price_key == "average_price" else {})}
                for leg in legs]

    def basket_margin(self, legs: list[dict], qty: int) -> dict | None:
        try:
            r = self._kite.basket_order_margins(self._orders(legs, qty, "price"),
                                                consider_positions=False, mode="compact")
        except Exception as exc:
            return {"error": str(exc)[:200]}
        return {"initial": (r.get("initial") or {}).get("total"), "final": (r.get("final") or {}).get("total")}

    def broker_charges(self, legs: list[dict], qty: int) -> dict | None:
        try:
            rows = self._kite.get_virtual_contract_note(self._orders(legs, qty, "average_price"))
        except Exception as exc:
            return {"error": str(exc)[:200]}
        out = {}
        for leg, r in zip(legs, rows or []):
            ch = (r or {}).get("charges") or {}
            out[leg["role"]] = {k: (v.get("total") if isinstance(v, dict) else v) for k, v in ch.items()}
        return out

    def probe(self, underlying: str) -> dict:
        m = self.contract_master(underlying)
        out = {"feed": self.name, "master": m.summary()}
        e = m.nearest_expiry(datetime.now(IST).date())
        if e:
            ks = m.strikes(e, "CE")
            c = m.get(e, ks[len(ks) // 2], "CE")
            key = f"{c.exchange}:{c.symbol}"
            out["option_quote_shape"] = _shape(self._quote([key]).get(key))
        out["index_quote_shape"] = _shape(self._quote([self._index_key(underlying)]).get(self._index_key(underlying)))
        return out


# -- Groww trading API ---------------------------------------------------------

class GrowwFnoFeed(FnoFeed):
    key = "groww"
    name = "Groww trading API (read-only)"
    INDEX = {"NIFTY": "NIFTY"}
    _BID = ("bid_price", "bidPrice", "best_bid_price")
    _ASK = ("offer_price", "ask_price", "offerPrice", "askPrice", "best_ask_price")
    _BIDQ = ("bid_quantity", "bidQuantity", "best_bid_quantity")
    _ASKQ = ("offer_quantity", "ask_quantity", "offerQuantity", "askQuantity", "best_ask_quantity")
    _LTP = ("last_price", "ltp", "lastPrice")
    # A quote's own timestamp only: a last-trade time dates the last trade, not the
    # quote, and is never used for freshness (PREREGISTRATION_3 section 7).
    _TS = ("exchange_timestamp", "quote_timestamp", "timestamp")

    def __init__(self, pause_s: float = 0.15):
        refuse_secrets_in_env()
        token = os.getenv("GROWW_ACCESS_TOKEN")
        if not token:
            raise DataUnavailable("GROWW_ACCESS_TOKEN must be exported (minted by hand; see SETUP.md 3b)")
        try:
            from growwapi import GrowwAPI
        except ImportError as exc:  # pragma: no cover
            raise DataUnavailable("Run: pip install growwapi") from exc
        self._api = GrowwAPI(token)
        self._pause = pause_s
        self._index_symbol: dict[str, str] = {}

    def _instruments(self):
        """Groww's public instrument CSV, fetched here with a timeout (the SDK's own download has none)."""
        import io

        import pandas as pd
        import requests
        try:
            r = requests.get(self._api.INSTRUMENT_CSV_URL, timeout=60)
            r.raise_for_status()
            return pd.read_csv(io.BytesIO(r.content), dtype=str)
        except Exception as exc:
            raise DataUnavailable(f"Groww instrument CSV failed: {exc}") from exc

    def contract_master(self, underlying: str) -> ContractMaster:
        return ContractMaster(from_groww_frame(self._instruments(), underlying), self.name, datetime.now(IST))

    def fno_master(self, underlying: str) -> ContractMaster:
        df = self._instruments()
        futs = futures_from_groww_frame(df, underlying)
        if not futs:
            raise DataUnavailable(f"Groww's instrument CSV lists no {underlying} futures")
        return ContractMaster(from_groww_frame(df, underlying) + futs, self.name, datetime.now(IST))

    def _quote(self, symbol: str, segment: str) -> dict:
        _time.sleep(self._pause)
        try:
            return self._api.get_quote(trading_symbol=symbol, exchange="NSE", segment=segment, timeout=15) or {}
        except Exception as exc:
            raise DataUnavailable(f"Groww quote for {symbol} failed: {exc}") from exc

    def index_quote(self, underlying: str) -> IndexQuote:
        d = self._quote(self.INDEX[underlying], "CASH")
        v = _first(d, self._LTP)
        if not v:
            raise DataUnavailable(f"Groww index quote has none of {self._LTP}; keys {list(d)[:20]}")
        return IndexQuote(float(v), as_ist(_first(d, self._TS)), datetime.now(IST))

    def quotes(self, contracts: list[OptionContract]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for c in contracts:
            d = self._quote(c.symbol, "FNO")
            if not d:
                continue
            depth = d.get("depth") if isinstance(d.get("depth"), dict) else {}
            bid, bq = _top(depth.get("buy"))
            ask, aq = _top(depth.get("sell"))
            if bid is None and _first(d, self._BID):
                bid, bq = float(_first(d, self._BID)), _first(d, self._BIDQ)
            if ask is None and _first(d, self._ASK):
                ask, aq = float(_first(d, self._ASK)), _first(d, self._ASKQ)
            if bid is None and ask is None and not depth:
                raise DataUnavailable(f"Groww quote for {c.symbol} has no recognisable bid/ask; keys {list(d)[:25]}")
            ltp = _first(d, self._LTP)
            out[c.symbol] = Quote(c.symbol, bid, ask, int(bq) if bq is not None else None,
                                  int(aq) if aq is not None else None, float(ltp) if ltp else None,
                                  as_ist(_first(d, self._TS)), datetime.now(IST))
        return out

    @staticmethod
    def daily_close(payload, day: date) -> float | None:
        """
        The close of `day` from a Groww daily-candle payload. Rows are lists
        [ts, o, h, l, c, v] or [ts, o, h, l, c, v, oi], or dicts; an index's
        volume may be null. Epoch timestamps are converted to IST before dating.
        """
        rows = None
        if isinstance(payload, dict):
            for k in ("candles", "candleList", "data", "historicalCandles"):
                if payload.get(k):
                    rows = payload[k]
                    break
        for row in rows or []:
            if isinstance(row, dict):
                ts, close = row.get("timestamp") or row.get("ts") or row.get("time"), row.get("close")
            elif isinstance(row, (list, tuple)) and len(row) >= 5:
                ts, close = row[0], row[4]
            else:
                continue
            when = as_ist(ts)
            try:
                c = float(close)
            except (TypeError, ValueError):
                continue
            if when is not None and when.date() == day and math.isfinite(c) and c > 0:
                return c
        return None

    def index_close(self, underlying: str, day: date) -> float | None:
        try:
            if underlying not in self._index_symbol:
                inst = self._api.get_instrument_by_exchange_and_trading_symbol("NSE", self.INDEX[underlying])
                self._index_symbol[underlying] = str(inst["groww_symbol"])
            payload = self._api.get_historical_candles(
                exchange="NSE", segment="CASH", groww_symbol=self._index_symbol[underlying],
                start_time=f"{day} 00:00:00", end_time=f"{day} 23:59:59", candle_interval="1day", timeout=30)
        except Exception:
            return None
        return self.daily_close(payload, day)

    def basket_margin(self, legs: list[dict], qty: int) -> dict | None:
        orders = [{"trading_symbol": leg["symbol"], "transaction_type": leg["side"], "quantity": qty,
                   "price": leg["price"], "order_type": "LIMIT", "product": "NRML", "exchange": "NSE"}
                  for leg in legs]
        try:
            r = self._api.get_order_margin_details(segment="FNO", orders=orders, timeout=20)
        except Exception as exc:
            return {"error": str(exc)[:200]}
        return {k: r.get(k) for k in ("total_requirement", "span_required", "exposure_required",
                                       "option_buy_premium", "brokerage_and_charges") if k in r} or {"keys": list(r)[:20]}

    def probe(self, underlying: str) -> dict:
        m = self.contract_master(underlying)
        out = {"feed": self.name, "master": m.summary()}
        e = m.nearest_expiry(datetime.now(IST).date())
        if e:
            ks = m.strikes(e, "CE")
            c = m.get(e, ks[len(ks) // 2], "CE")
            out["option_quote_shape"] = _shape(self._quote(c.symbol, "FNO"))
        out["index_quote_shape"] = _shape(self._quote(self.INDEX[underlying], "CASH"))
        try:
            yday = datetime.now(IST).date() - timedelta(days=1)
            if underlying not in self._index_symbol:
                inst = self._api.get_instrument_by_exchange_and_trading_symbol("NSE", self.INDEX[underlying])
                self._index_symbol[underlying] = str(inst["groww_symbol"])
            out["index_daily_candle_shape"] = _shape(self._api.get_historical_candles(
                exchange="NSE", segment="CASH", groww_symbol=self._index_symbol[underlying],
                start_time=f"{yday - timedelta(days=6)} 00:00:00", end_time=f"{yday} 23:59:59",
                candle_interval="1day", timeout=30))
        except Exception as exc:
            out["index_daily_candle_shape"] = f"failed: {type(exc).__name__}: {str(exc)[:160]}"
        return out


# -- NSE's final settlement price ------------------------------------------------

BHAVCOPY_URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
_BHAV_COLS = ("TckrSymb", "FinInstrmTp", "XpryDt", "StrkPric", "OptnTp", "SttlmPric")


def fsp_from_bhavcopy(rows, underlying: str, expiry: date) -> tuple[float | None, dict]:
    """
    The final settlement price implied by NSE's settlement prices for the
    expiring index options: K + S for an in-the-money call, K - S for a put.
    They must agree to the paisa, from at least four contracts; otherwise None.
    No column of NSE's file is assumed: a missing one is reported by name.
    """
    import pandas as pd
    df = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    missing = [c for c in _BHAV_COLS if c not in df.columns]
    if missing:
        return None, {"error": f"bhavcopy lacks {missing}", "columns": list(df.columns)[:40]}
    exp = pd.to_datetime(df["XpryDt"], errors="coerce").dt.date
    sel = df.loc[(df["TckrSymb"].astype(str).str.upper() == underlying)
                 & (df["FinInstrmTp"].astype(str).str.upper() == "IDO") & (exp == expiry)]
    vals, contracts = [], set()
    for r in sel.to_dict("records"):
        try:
            k, sp = float(r["StrkPric"]), float(r["SttlmPric"])
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(k) and math.isfinite(sp)) or sp <= 0:
            continue
        right = str(r["OptnTp"]).strip().upper()
        if right not in ("CE", "PE"):
            continue
        vals.append(round(k + sp if right == "CE" else k - sp, 2))
        contracts.add((k, right))
    if len(contracts) < 4:
        return None, {"error": f"{len(contracts)} in-the-money contracts for {underlying} {expiry}", "rows": len(sel)}
    if len(set(vals)) != 1:
        return None, {"error": f"implied values disagree: {min(vals):.2f} to {max(vals):.2f}", "n": len(contracts)}
    return vals[0], {"n": len(contracts)}


def nse_fsp(underlying: str, expiry: date, timeout: int = 20) -> tuple[float | None, dict]:
    """Read NSE's F&O bhavcopy for the expiry date and derive the final settlement price."""
    import io
    import urllib.request
    import zipfile

    import pandas as pd
    url = BHAVCOPY_URL.format(d=expiry)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            blob = r.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            df = pd.read_csv(z.open(name), dtype=str)
    except Exception as exc:
        return None, {"url": url, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    v, info = fsp_from_bhavcopy(df, underlying, expiry)
    info["url"] = url
    return v, info


# -- Synthetic, for tests and the wiring demo only -----------------------------

def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def black76(f: float, k: float, t: float, vol: float, right: str) -> float:
    if t <= 0 or vol <= 0:
        return max(0.0, f - k) if right == "CE" else max(0.0, k - f)
    sd = vol * math.sqrt(t)
    d1 = (math.log(f / k) + sd * sd / 2) / sd
    d2 = d1 - sd
    if right == "CE":
        return f * _ncdf(d1) - k * _ncdf(d2)
    return k * _ncdf(-d2) - f * _ncdf(-d1)


class MockFnoFeed(FnoFeed):
    """
    SYNTHETIC prices for tests and `fno demo`. Not market data, never journaled
    as paper evidence: the engine refuses a mock feed outside its own scratch
    state directory. The index follows a seeded daily random walk; options are
    Black-76 at a skewed volatility; strikes 50 apart; lot 65 (illustrative).
    """

    key = "mock"
    name = "MOCK (synthetic, not market data)"
    is_mock = True

    def __init__(self, now_fn, seed: int = 7, start: float = 25000.0, base: date = date(2026, 1, 1),
                 daily_vol: float = 0.009, lot: int = 65, calendar: TradingCalendar | None = None,
                 expiry_weekday: int = 1, half_spread: float = 0.005):
        self.now_fn, self.seed, self.start, self.base = now_fn, seed, start, base
        self.daily_vol, self.lot, self.half_spread = daily_vol, lot, half_spread
        self.cal = calendar or load_calendar()
        self.expiry_weekday = expiry_weekday
        self._closes: dict[date, float] = {}
        self.closes_override: dict[date, float] = {}
        self.expiry_moves: dict[date, date] = {}      # original -> moved; symbols keep the original date

    def _ret(self, d: date) -> float:
        return float(np.random.default_rng([self.seed, d.toordinal()]).normal(0, self.daily_vol))

    def close_on(self, d: date) -> float:
        if d in self.closes_override:
            return self.closes_override[d]
        if d <= self.base:
            return self.start
        if d not in self._closes:
            x, s = self.base, self.start
            while x < d:
                x += timedelta(days=1)
                if self.cal.is_trading_day(x):
                    s = self.closes_override.get(x, s * math.exp(self._ret(x)))
                self._closes[x] = s
        return self._closes[d]

    def _spot(self, now: datetime) -> float:
        d = now.date()
        prev = self.close_on(d - timedelta(days=1))
        if not self.cal.is_trading_day(d):
            return prev
        frac = min(1.0, max(0.0, ((now.hour * 60 + now.minute) - (9 * 60 + 15)) / 375))
        return prev * math.exp(math.log(self.close_on(d) / prev) * frac)

    def _expiries(self, today: date) -> list[date]:
        out, d = [], today
        while len(out) < 5:
            if d.weekday() == self.expiry_weekday:
                e = d
                while not self.cal.is_trading_day(e):
                    e -= timedelta(days=1)
                if e >= today:
                    out.append(e)
            d += timedelta(days=1)
        return out

    def contract_master(self, underlying: str) -> ContractMaster:
        now = self.now_fn()
        ref = round(self.close_on(now.date() - timedelta(days=1)) / 50) * 50
        cs = []
        for e in self._expiries(now.date() - timedelta(days=7)):
            moved = self.expiry_moves.get(e, e)
            if moved < now.date():
                continue
            for k in np.arange(ref - 3000, ref + 3001, 50):
                for right in ("CE", "PE"):
                    sym = f"{underlying}{e:%y%m%d}{int(k)}{right}"
                    cs.append(OptionContract(sym, underlying, moved, float(k), right, self.lot, 0.05,
                                             freeze_qty=self.lot * 54))
        return ContractMaster(cs, self.name, now)

    def _monthlies(self, today: date) -> list[date]:
        """The last expiry weekday of each month, moved back over holidays: three, from this month."""
        out, y, m = [], today.year, today.month
        while len(out) < 3:
            d = (date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1))
            while d.weekday() != self.expiry_weekday:
                d -= timedelta(days=1)
            while not self.cal.is_trading_day(d):
                d -= timedelta(days=1)
            if d >= today:
                out.append(d)
            y, m = y + m // 12, m % 12 + 1
        return out

    def fno_master(self, underlying: str) -> ContractMaster:
        now = self.now_fn()
        opts = self.contract_master(underlying)
        listed = set(opts.expiries())
        cs = list(opts.contracts)
        for e in self._monthlies(now.date()):
            cs.append(OptionContract(f"{underlying}{e:%y%b}FUT".upper(), underlying, e, 0.0, "FUT", self.lot, 0.05,
                                     freeze_qty=self.lot * 27))
            if e not in listed:            # the monthly options, as on NSE, expire with the future
                ref = round(self.close_on(now.date() - timedelta(days=1)) / 100) * 100
                for k in np.arange(ref - 3000, ref + 3001, 100):
                    for right in ("CE", "PE"):
                        cs.append(OptionContract(f"{underlying}{e:%y%m%d}{int(k)}{right}", underlying, e, float(k),
                                                 right, self.lot, 0.05, freeze_qty=self.lot * 27))
        return ContractMaster(cs, self.name, now)

    def index_quote(self, underlying: str) -> IndexQuote:
        now = self.now_fn()
        return IndexQuote(round(self._spot(now), 2), now, now)

    def _vol(self, s: float, k: float) -> float:
        return 0.12 + 0.6 * max(0.0, math.log(s / k)) + 0.1 * max(0.0, math.log(k / s))

    def quotes(self, contracts: list[OptionContract]) -> dict[str, Quote]:
        now = self.now_fn()
        s = self._spot(now)
        out = {}
        for c in contracts:
            close_dt = datetime.combine(c.expiry, time(15, 30), IST)
            t = max(0.0, (close_dt - now).total_seconds()) / (365 * 86400)
            if c.right == "FUT":
                mid = s * math.exp(0.065 * t)               # cost of carry at an assumed 6.5%
            else:
                mid = black76(s, c.strike, t, self._vol(s, c.strike), c.right)
            h = max(0.05, (0.00002 if c.right == "FUT" else self.half_spread) * mid)
            bid = math.floor((mid - h) / 0.05) * 0.05
            ask = math.ceil((mid + h) / 0.05) * 0.05
            out[c.symbol] = Quote(c.symbol, round(bid, 2) if bid >= 0.05 else None, round(max(ask, 0.05), 2),
                                  5000, 5000, round(mid, 2), now, now)
        return out

    def index_close(self, underlying: str, day: date) -> float | None:
        now = self.now_fn()
        if day > now.date() or (day == now.date() and now.time() < time(15, 30)):
            return None
        return round(self.close_on(day), 2)


class MockIntradayFeed(MockFnoFeed):
    """
    MockFnoFeed with a day inside it: an opening gap, then a seeded Brownian
    bridge minute by minute to the day's close. SYNTHETIC — for the explorer's
    tests and demo only; no strategy result on it says anything about a market.
    """

    def __init__(self, now_fn, intraday_vol: float = 0.007, gap_vol: float = 0.003, **kw):
        super().__init__(now_fn, **kw)
        self.intraday_vol, self.gap_vol = intraday_vol, gap_vol
        self._paths: dict[date, np.ndarray] = {}

    def _path(self, d: date) -> np.ndarray:
        if d not in self._paths:
            prev, close = self.close_on(d - timedelta(days=1)), self.close_on(d)
            rng = np.random.default_rng([self.seed, d.toordinal(), 1])
            open_ = prev * math.exp(rng.normal(0, self.gap_vol))
            n = 375
            w = np.concatenate([[0.0], np.cumsum(rng.normal(0, self.intraday_vol / math.sqrt(n), n))])
            t = np.linspace(0, 1, n + 1)
            self._paths[d] = open_ * np.exp(t * math.log(close / open_) + w - t * w[-1])
        return self._paths[d]

    def _spot(self, now: datetime) -> float:
        d = now.date()
        if not self.cal.is_trading_day(d):
            return self.close_on(d - timedelta(days=1))
        if now.time() < time(9, 15):
            return self.close_on(d - timedelta(days=1))
        x = min(375.0, ((now.hour * 60 + now.minute) - (9 * 60 + 15)) + now.second / 60)
        path, i = self._path(d), int(x)
        return float(path[i] if i >= 375 else path[i] + (path[i + 1] - path[i]) * (x - i))

