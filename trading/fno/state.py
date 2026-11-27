"""
V's state, folded from the journal alone (PREREGISTRATION_3.md section 17: the
journal is the record).

The engine rebuilds this on every pass, and `fno evaluate` builds the verdict
from it. No second copy of a trade exists that could disagree with the journal.

Void records (section 17), reported and never evaluated:
  - an FNO_ENTRY for an expiry that already has a trade, or whose cycle was
    already closed, or whose slot was already decided, or written more than five
    minutes after its slot began;
  - an FNO_SETTLED for a trade that is unknown or already settled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from market.calendar import TradingCalendar
from trading.fno.strategy_v import REGISTERED, VParams, expected_slots

ENTRY_LATE_S = 300


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
    switches: list[dict] = field(default_factory=list)
    probe_ok: bool = False
    master_days: list[date] = field(default_factory=list)
    master_sha: dict[date, list[str]] = field(default_factory=dict)
    runs: list[tuple[date, list[date]]] = field(default_factory=list)
    trades: dict[str, dict] = field(default_factory=dict)      # by trade id
    void: list[str] = field(default_factory=list)
    cycles: dict[date, dict] = field(default_factory=dict)     # FNO_CYCLE outcomes, with the record's date
    observed: set[str] = field(default_factory=set)            # slot datetimes decided on market data
    pass_master_sha: list[tuple[date, str | None]] = field(default_factory=list)
    pending_days: dict[str, set[date]] = field(default_factory=dict)
    manual: dict[date, dict] = field(default_factory=dict)
    calendars: list[tuple[date, dict]] = field(default_factory=list)
    rule_breaks: list[str] = field(default_factory=list)
    verdict: dict | None = None
    modes: set[str] = field(default_factory=set)
    inits: int = 0
    last_asof: date | None = None

    @classmethod
    def from_records(cls, recs: list[dict]) -> "State":
        st = cls()
        for r in recs:
            k, p = r.get("type"), r.get("payload") or {}
            st.modes.add(str(r.get("mode")))
            if r.get("asof"):
                st.last_asof = _d(r["asof"])
            if k == "FNO_INIT":
                st.inits += 1
                if st.init is None:
                    st.init, st.start, st.feed = p, _d(r["asof"]), p.get("feed")
                    if "holidays" in p:
                        st.calendars.append((_d(r["asof"]), p["holidays"] or {}))
            elif k == "FNO_PROBE" and p.get("ok"):
                st.probe_ok = True
            elif k == "FNO_FEED_SWITCH":
                st.feed = p["new"]
                st.switches.append(p)
            elif k == "FNO_CALENDAR":
                st.calendars.append((_d(r["asof"]), p.get("holidays") or {}))
            elif k == "FNO_MASTER":
                day = _d(p["date"])
                st.master_days.append(day)
                st.master_sha.setdefault(day, []).append(p.get("sha256"))
                st.runs.append((day, [_d(e) for e in p.get("weekly_run", [])]))
            elif k == "FNO_EXPIRY_MOVED":
                t = st.trades.get(p["trade_id"])
                if t:
                    t["expiry"] = p["new"]
            elif k == "FNO_PASS":
                if p.get("slot"):
                    st.pass_master_sha.append((_d(r["asof"]), p.get("master_sha256")))
                if p.get("outcome") in ("entry", "no_trade") and p.get("slot"):
                    st.observed.add(p["slot"])
            elif k == "FNO_ENTRY":
                st._entry(r, p)
            elif k == "FNO_CYCLE":
                e = _d(p["expiry"])
                if e in st.cycles:
                    st.void.append(f"seq {r['seq']}: second FNO_CYCLE for {e}")
                else:
                    st.cycles[e] = {**p, "asof": r["asof"], "seq": r["seq"]}
            elif k == "FNO_EXPIRED":
                t = st.trades.get(p["trade_id"])
                if t and t["status"] == "OPEN":
                    t["status"] = "AWAITING_SETTLEMENT"
            elif k == "FNO_SETTLEMENT_PENDING":
                st.pending_days.setdefault(p["trade_id"], set()).add(_d(r["asof"]))
            elif k == "FNO_MANUAL_SETTLEMENT_FILE":
                st.manual.setdefault(_d(p["expiry"]), dict(p))
            elif k == "FNO_SETTLED":
                t = st.trades.get(p["trade_id"])
                if t is None or t["status"] == "SETTLED":
                    st.void.append(f"seq {r['seq']}: FNO_SETTLED for {p['trade_id']} that is unknown or already settled")
                    continue
                st._rules(r, p)
                t.update(status="SETTLED", net=p["net"], R=p["R"], settlement=p.get("detail"),
                         closed_seq=r["seq"], closed_asof=r["asof"])
            elif k == "FNO_VERDICT" and st.verdict is None:
                st.verdict = {**p, "seq": r["seq"]}
        return st

    def _rules(self, r: dict, p: dict) -> None:
        """Section 11: the registration's text and parameters may not change during the test."""
        if self.init is None or self.init.get("prereg_sha256") is None:
            return
        for key in ("prereg_sha256", "params_sha256"):
            if p.get(key) != self.init.get(key):
                self.rule_breaks.append(f"seq {r['seq']}: {key} differs from FNO_INIT's")

    def _entry(self, r: dict, p: dict) -> None:
        t = dict(p["trade"])
        e = _d(t["expiry"])
        why = None
        if t["id"] in self.trades or any(_d(x["expiry"]) == e for x in self.trades.values()):
            why = "a trade for this expiry already exists"
        elif e in self.cycles:
            why = "its cycle was already closed"
        elif t.get("slot") in self.observed:
            why = "its slot was already decided"
        elif t.get("slot") and r.get("ts") and r.get("mode") == "fno-paper":
            late = (datetime.fromisoformat(r["ts"]) - datetime.fromisoformat(t["slot"])).total_seconds()
            if late > ENTRY_LATE_S:
                why = f"written {int(late)}s after its slot began"
        if why:
            self.void.append(f"seq {r['seq']}: FNO_ENTRY for {t['id']} is void: {why}")
            return
        self._rules(r, t)
        if t.get("slot"):
            self.observed.add(t["slot"])
        t.update(status="OPEN", net=None, R=None, settlement=None, entry_seq=r["seq"], entry_asof=r["asof"])
        self.trades[t["id"]] = t

    @property
    def known(self) -> set[date]:
        """Every weekly expiry listed, less any that a later master, read on or before it, stopped listing."""
        first: dict[date, date] = {}
        for day, run in self.runs:
            for e in run:
                first.setdefault(e, day)
        dropped = {e for e, since in first.items() for day, run in self.runs
                   if run and since < day <= e <= run[-1] and e not in run}
        return set(first) - dropped

    def trade_for(self, e: date) -> dict | None:
        return next((t for t in self.trades.values() if _d(t["expiry"]) == e), None)

    def counted_cycles(self) -> list[tuple[date, date]]:
        """(E, E_prev) for every known expiry whose previous expiry is on or after the start (section 4)."""
        if self.start is None:
            return []
        xs = sorted(self.known)
        return [(e, prev) for prev, e in zip(xs, xs[1:]) if prev >= self.start]

    def chain_breaks(self, max_gap_days: int = 10) -> list[tuple[date, date]]:
        xs = sorted(x for x in self.known if self.start and x >= self.start)
        return [(a, b) for a, b in zip(xs, xs[1:]) if (b - a).days > max_gap_days]

    def unobserved(self, e: date, e_prev: date, cal: TradingCalendar, tz, before: datetime | None = None,
                   p: VParams = REGISTERED) -> list[datetime]:
        return [s for s in expected_slots(e, e_prev, cal, tz, p)
                if (before is None or s < before) and s.isoformat() not in self.observed]

    def holidays_now(self) -> dict:
        return self.calendars[-1][1] if self.calendars else {}
