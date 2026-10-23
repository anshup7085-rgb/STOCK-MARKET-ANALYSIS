"""
Option contracts, the day's contract master, and quotes.

PREREGISTRATION_3.md section 3: no contract fact is hard-coded. Lot size,
strikes, expiries and tick size come from the feed's instrument dump each day,
because none of the three that matter (lot size, strike interval, expiry
weekday) could be independently confirmed when V was registered.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime

import pandas as pd

from market.config import IST
from market.providers.base import DataUnavailable

RIGHTS = ("CE", "PE")


@dataclass(frozen=True)
class OptionContract:
    symbol: str                  # the exchange trading symbol
    underlying: str
    expiry: date
    strike: float
    right: str                   # "CE" | "PE"
    lot_size: int
    tick_size: float
    freeze_qty: int | None = None
    exchange: str = "NFO"
    feed_id: str | None = None   # Kite instrument_token, Groww groww_symbol

    def as_dict(self) -> dict:
        d = asdict(self)
        d["expiry"] = self.expiry.isoformat()
        return d


def _to_date(v) -> date | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.Timestamp(str(v)).date()
    except (ValueError, TypeError):
        return None


def _to_int(v) -> int | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if pd.notna(f) and f == int(f) else None


def _to_float(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if pd.notna(f) else None


class ContractMaster:
    """One feed's option contracts for one underlying, as listed on one day."""

    def __init__(self, contracts: list[OptionContract], source: str, fetched_at: datetime):
        self.contracts = list(contracts)
        self.source = source
        self.fetched_at = fetched_at
        self._by_key = {(c.expiry, c.strike, c.right): c for c in self.contracts}

    def __len__(self) -> int:
        return len(self.contracts)

    def expiries(self) -> list[date]:
        return sorted({c.expiry for c in self.contracts})

    def nearest_expiry(self, on_or_after: date) -> date | None:
        later = [e for e in self.expiries() if e >= on_or_after]
        return later[0] if later else None

    def get(self, expiry: date, strike: float, right: str) -> OptionContract | None:
        return self._by_key.get((expiry, float(strike), right))

    def strikes(self, expiry: date, right: str | None = None) -> list[float]:
        return sorted({c.strike for c in self.contracts
                       if c.expiry == expiry and (right is None or c.right == right)})

    def summary(self) -> dict:
        """What the master says about the facts V refuses to assume."""
        out = {"source": self.source, "fetched_at": self.fetched_at.isoformat(),
               "contracts": len(self.contracts), "expiries": []}
        for e in self.expiries()[:6]:
            rows = [c for c in self.contracts if c.expiry == e]
            ks = sorted({c.strike for c in rows})
            gaps = sorted({round(b - a, 4) for a, b in zip(ks, ks[1:])})
            out["expiries"].append({
                "expiry": e.isoformat(), "weekday": e.strftime("%A"), "strikes": len(ks),
                "strike_gaps": gaps[:5], "lot_sizes": sorted({c.lot_size for c in rows}),
                "tick_sizes": sorted({c.tick_size for c in rows}),
                "freeze_qty": sorted({c.freeze_qty for c in rows if c.freeze_qty}),
            })
        return out


def from_kite_rows(rows, underlying: str) -> list[OptionContract]:
    """Kite's `instruments("NFO")` rows (pykiteconnect already types the numbers)."""
    out = []
    for r in rows:
        if str(r.get("name", "")).upper() != underlying or r.get("instrument_type") not in RIGHTS:
            continue
        if str(r.get("segment", "NFO-OPT")) != "NFO-OPT":
            continue
        exp, strike = _to_date(r.get("expiry")), _to_float(r.get("strike"))
        lot, tick = _to_int(r.get("lot_size")), _to_float(r.get("tick_size"))
        if not (exp and strike and lot and tick):
            continue
        out.append(OptionContract(
            symbol=str(r["tradingsymbol"]), underlying=underlying, expiry=exp, strike=strike,
            right=r["instrument_type"], lot_size=lot, tick_size=tick,
            exchange=str(r.get("exchange") or "NFO"), feed_id=str(r.get("instrument_token") or "") or None,
        ))
    if not out:
        raise DataUnavailable(f"Kite's NFO dump lists no {underlying} options")
    return out


_GROWW_COLS = {"trading_symbol", "underlying_symbol", "instrument_type", "expiry_date", "strike_price", "lot_size"}


def from_groww_frame(df: pd.DataFrame, underlying: str) -> list[OptionContract]:
    """
    Groww's public instrument CSV. Its F&O columns were not independently
    confirmed (only exchange, trading_symbol, groww_symbol, exchange_token were),
    so a missing column is refused by name rather than guessed around.
    """
    missing = _GROWW_COLS - set(df.columns)
    if missing:
        raise DataUnavailable(f"Groww instrument CSV lacks {sorted(missing)}; columns are {list(df.columns)[:25]}")
    seg = df["segment"].astype(str).str.upper() if "segment" in df.columns else "FNO"
    exch = df["exchange"].astype(str).str.upper() if "exchange" in df.columns else "NSE"
    rows = df.loc[(df["underlying_symbol"].astype(str).str.upper() == underlying)
                  & (df["instrument_type"].astype(str).str.upper().isin(RIGHTS))
                  & (seg == "FNO") & (exch == "NSE")]
    out = []
    for r in rows.to_dict("records"):
        exp, strike = _to_date(r.get("expiry_date")), _to_float(r.get("strike_price"))
        lot, tick = _to_int(r.get("lot_size")), _to_float(r.get("tick_size")) or None
        if not (exp and strike and lot and tick):
            continue
        out.append(OptionContract(
            symbol=str(r["trading_symbol"]), underlying=underlying, expiry=exp, strike=strike,
            right=str(r["instrument_type"]).upper(), lot_size=lot, tick_size=tick,
            freeze_qty=_to_int(r.get("freeze_quantity")), exchange="NSE",
            feed_id=str(r.get("groww_symbol") or "") or None,
        ))
    if not out:
        raise DataUnavailable(f"Groww's instrument CSV lists no usable {underlying} options")
    return out


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float | None
    ask: float | None
    bid_qty: int | None
    ask_qty: int | None
    ltp: float | None
    exchange_ts: datetime | None      # aware, IST; None when the feed gives no timestamp
    fetched_at: datetime

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2

    def as_dict(self) -> dict:
        return {
            "bid": self.bid, "ask": self.ask, "bid_qty": self.bid_qty, "ask_qty": self.ask_qty,
            "ltp": self.ltp, "exchange_ts": self.exchange_ts.isoformat() if self.exchange_ts else None,
            "fetched_at": self.fetched_at.isoformat(),
        }


@dataclass(frozen=True)
class IndexQuote:
    value: float
    exchange_ts: datetime | None
    fetched_at: datetime

    def as_dict(self) -> dict:
        return {"value": self.value,
                "exchange_ts": self.exchange_ts.isoformat() if self.exchange_ts else None,
                "fetched_at": self.fetched_at.isoformat()}


def as_ist(ts) -> datetime | None:
    """A feed timestamp as an aware IST datetime. Naive values are IST (Kite's are)."""
    if ts is None or ts == "" or ts == 0:
        return None
    if isinstance(ts, (int, float)):
        unit = "ms" if ts > 1e11 else "s"
        return pd.Timestamp(ts, unit=unit, tz="UTC").tz_convert(IST).to_pydatetime()
    if isinstance(ts, str):
        try:
            ts = pd.Timestamp(ts).to_pydatetime()
        except (ValueError, TypeError):
            return None
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
    return None


@dataclass
class Flags:
    """Things a record must carry so a reader knows what was assumed."""
    items: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        if msg not in self.items:
            self.items.append(msg)
