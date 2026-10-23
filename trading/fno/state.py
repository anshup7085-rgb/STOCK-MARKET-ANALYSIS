"""
V's state, folded from the journal alone (PREREGISTRATION_3.md section 17: the
journal is the record).

The engine rebuilds this on every pass, and `fno evaluate` builds the verdict
from it. No second copy of a trade exists that could disagree with the journal.
The first FNO_ENTRY for an expiry is the trade, and the first close of a trade is
its close. Any later duplicate is void, and is reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from market.calendar import TradingCalendar
from trading.fno.strategy_v import REGISTERED, VParams, expected_slots


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def contiguous_run(expiries: list[date], max_gap_days: int = 10) -> list[date]:
    """The weekly run from the nearest expiry: stop at the first gap above 10 days (monthlies beyond)."""
    xs = sorted(set(expiries))
    out = xs[:1]
    for e in xs[1:]:
        if (e - out[-1]).days > max_gap_days:
            break
        out.append(e)
    return out


@dataclass
class State:
    init: dict | None = None
    start: date | None = None
    feed: str | None = None
    probe_ok: bool = False
    master_days: list[date] = field(default_factory=list)
    known: set[date] = field(default_factory=set)
    moves: dict[date, date] = field(default_factory=dict)
    trades: dict[str, dict] = field(default_factory=dict)      # by trade id
    void: list[str] = field(default_factory=list)
    cycles: dict[date, dict] = field(default_factory=dict)     # FNO_CYCLE outcomes
    observed: dict[date, set[str]] = field(default_factory=dict)
    pending_days: dict[str, set[date]] = field(default_factory=dict)
    manual: dict[date, dict] = field(default_factory=dict)
    verdict: dict | None = None
    modes: set[str] = field(default_factory=set)
    feeds_seen: set[str] = field(default_factory=set)
    inits: int = 0

    @classmethod
    def from_records(cls, recs: list[dict]) -> "State":
        st = cls()
        for r in recs:
            k, p = r.get("type"), r.get("payload") or {}
            st.modes.add(str(r.get("mode")))
            if p.get("feed"):
                st.feeds_seen.add(str(p["feed"]))
            if k == "FNO_INIT":
                st.inits += 1
                if st.init is None:
                    st.init, st.start, st.feed = p, _d(r["asof"]), p.get("feed")
            elif k == "FNO_PROBE" and p.get("ok"):
                st.probe_ok = True
            elif k == "FNO_FEED_SWITCH":
                st.feed = p["new"]
            elif k == "FNO_MASTER":
                st.master_days.append(_d(p["date"]))
                st.known |= {_d(e) for e in p.get("weekly_run", [])}
            elif k == "FNO_EXPIRY_MOVED":
                old, new = _d(p["old"]), _d(p["new"])
                st.moves[old] = new
                t = st.trades.get(p["trade_id"])
                if t:
                    t["expiry"] = new.isoformat()
            elif k == "FNO_PASS":
                if p.get("outcome") in ("entry", "no_trade"):
                    st.observed.setdefault(_d(p["expiry"]), set()).add(p["slot"])
            elif k == "FNO_ENTRY":
                t = dict(p["trade"])
                e = _d(t["expiry"])
                if t["id"] in st.trades or any(_d(x["expiry"]) == e for x in st.trades.values()):
                    st.void.append(f"seq {r['seq']}: duplicate FNO_ENTRY for {t['id']}")
                    continue
                t.update(status="OPEN", net=None, R=None, settlement=None, entry_seq=r["seq"])
                st.trades[t["id"]] = t
            elif k == "FNO_CYCLE":
                e = _d(p["expiry"])
                if e in st.cycles:
                    st.void.append(f"seq {r['seq']}: second FNO_CYCLE for {e}")
                else:
                    st.cycles[e] = dict(p)
            elif k == "FNO_EXPIRED":
                t = st.trades.get(p["trade_id"])
                if t and t["status"] == "OPEN":
                    t["status"] = "AWAITING_SETTLEMENT"
            elif k == "FNO_SETTLEMENT_PENDING":
                st.pending_days.setdefault(p["trade_id"], set()).add(_d(r["asof"]))
            elif k == "FNO_MANUAL_SETTLEMENT_VALUE":
                st.manual.setdefault(_d(p["expiry"]), dict(p))
            elif k == "FNO_SETTLED":
                t = st.trades.get(p["trade_id"])
                if t is None or t["status"] == "SETTLED":
                    st.void.append(f"seq {r['seq']}: FNO_SETTLED for {p['trade_id']} that is unknown or already settled")
                    continue
                t.update(status="SETTLED", net=p["net"], R=p["R"], settlement=p.get("detail"), closed_seq=r["seq"])
            elif k == "FNO_VERDICT" and st.verdict is None:
                st.verdict = dict(p)
        for old, new in st.moves.items():
            if old in st.known:
                st.known.discard(old)
                st.known.add(new)
        return st

    def trade_for(self, e: date) -> dict | None:
        return next((t for t in self.trades.values() if _d(t["expiry"]) == e), None)

    def counted_cycles(self) -> list[tuple[date, date]]:
        """(E, E_prev) for every known expiry whose previous expiry is on or after the start (section 17)."""
        if self.start is None:
            return []
        xs = sorted(self.known)
        return [(e, prev) for prev, e in zip(xs, xs[1:]) if prev >= self.start]

    def chain_breaks(self, max_gap_days: int = 10) -> list[tuple[date, date]]:
        xs = sorted(x for x in self.known if self.start and x >= self.start)
        return [(a, b) for a, b in zip(xs, xs[1:]) if (b - a).days > max_gap_days]

    def unobserved(self, e: date, e_prev: date, cal: TradingCalendar, tz, before: datetime | None = None,
                   p: VParams = REGISTERED) -> list[datetime]:
        seen = self.observed.get(e, set())
        return [s for s in expected_slots(e, e_prev, cal, tz, p)
                if (before is None or s < before) and s.isoformat() not in seen]
