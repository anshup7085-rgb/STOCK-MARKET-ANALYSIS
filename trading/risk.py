"""
Pre-trade risk engine, circuit breakers and the kill switch.

Every check returns its own verdict and reason, and the full list is journaled
for every candidate — approved or not — so "why did it (not) trade?" always has
an answer in the log rather than in someone's memory.

The engine is deliberately unclever. It does not optimise, net off or "make
room"; a candidate either fits inside every limit as the book stands or it
does not trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from market.config import IST
from trading.book import Book
from trading.config import TradingConfig
from trading.models import TradeIntent


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class RiskDecision:
    approved: bool
    checks: list[Check]

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def summary(self) -> str:
        if self.approved:
            return f"APPROVED ({len(self.checks)} checks passed)"
        return "REJECTED — " + "; ".join(f"{c.name}: {c.detail}" for c in self.failures)


@dataclass
class RiskContext:
    today: str
    equity: float
    available_capital: float
    risk_pct: float
    broker_cash: float | None = None          # live only: a second, independent ceiling
    kill_reason: str | None = None
    is_trading_day: bool = True
    last_close: float | None = None
    live_price: float | None = None           # live only: LTP just before submission
    require_live_price: bool = False          # live: no LTP means no order
    returns: dict[str, pd.Series] = field(default_factory=dict)
    restricted: dict[str, str] = field(default_factory=dict)
    approved_this_cycle: int = 0


class KillSwitch:
    """
    A file. Present means no new entries, anywhere it applies.

    Deliberately crude: a file can be created by a circuit breaker, by the CLI,
    by hand, or by `touch` from a phone over SSH, and nothing has to be running
    to honour it. Engaging never cancels protective stops. Releasing is manual.
    """

    def __init__(self, paths: list[Path]):
        self.paths = list(paths)

    def engaged(self) -> str | None:
        for p in self.paths:
            if p.exists():
                try:
                    why = p.read_text().strip()
                except OSError:
                    why = ""
                return f"{p}: {why or 'no reason recorded'}"
        return None

    def engage(self, reason: str) -> Path:
        p = self.paths[0]
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            fh.write(f"{datetime.now(IST).isoformat()} {reason}\n")
        return p

    def release(self) -> list[Path]:
        gone = []
        for p in self.paths:
            if p.exists():
                p.unlink()
                gone.append(p)
        return gone


class RiskEngine:
    def __init__(self, cfg: TradingConfig):
        self.cfg = cfg

    # -- entries ----------------------------------------------------------

    def check_entry(self, it: TradeIntent, book: Book, ctx: RiskContext) -> RiskDecision:
        cfg = self.cfg
        checks: list[Check] = []

        def add(name: str, ok: bool, detail: str) -> None:
            checks.append(Check(name, bool(ok), detail))

        add("kill_switch", ctx.kill_reason is None, ctx.kill_reason or "not engaged")
        add("trading_day", ctx.is_trading_day,
            "NSE trading day" if ctx.is_trading_day else "not an NSE trading day")
        add("long_only_cash", it.direction == "long" and it.product == "CNC",
            "long CNC" if it.direction == "long" else
            "retail cannot carry a short overnight in the cash segment — shorts need F&O")
        add("score_floor", it.score >= cfg.min_score,
            f"score {it.score:.1f} vs floor {cfg.min_score:.0f}")
        add("coverage_floor", it.coverage >= cfg.min_coverage,
            f"coverage {it.coverage:.0%} vs floor {cfg.min_coverage:.0%}")
        add("net_reward_risk", it.net_rr >= cfg.min_net_rr,
            f"net R:R {it.net_rr:.2f} vs floor {cfg.min_net_rr:.2f} (gross {it.gross_rr:.2f})")
        add("levels_ordered",
            0 < it.stop_limit <= it.stop < it.entry_limit < it.target,
            f"stop-limit {it.stop_limit} <= stop {it.stop} < entry {it.entry_limit} < target {it.target}")
        add("not_restricted", it.symbol not in ctx.restricted,
            ctx.restricted.get(it.symbol, "not on the restricted list"))
        add("no_duplicate", not book.has_active(it.symbol),
            "already holding or working an order in this name"
            if book.has_active(it.symbol) else "no existing position")

        n_active = len(book.active())
        add("max_open_positions", n_active < cfg.max_open_positions,
            f"{n_active} active vs max {cfg.max_open_positions}")
        add("entries_per_cycle", ctx.approved_this_cycle < cfg.max_new_entries_per_cycle,
            f"{ctx.approved_this_cycle} approved this cycle vs max {cfg.max_new_entries_per_cycle}")
        n_orders = book.orders_today(ctx.today)
        add("orders_per_day", n_orders < cfg.max_orders_per_day,
            f"{n_orders} orders today vs max {cfg.max_orders_per_day}")

        budget = ctx.equity * ctx.risk_pct / 100.0
        add("per_trade_risk", it.qty > 0 and it.est_max_loss <= budget * 1.001,
            f"net max loss Rs{it.est_max_loss:,.0f} vs budget Rs{budget:,.0f} ({ctx.risk_pct}% of equity)")

        cap = ctx.equity * cfg.max_single_position_pct / 100.0
        add("position_concentration", it.notional <= cap * 1.001,
            f"position Rs{it.notional:,.0f} vs cap Rs{cap:,.0f} ({cfg.max_single_position_pct}%)")

        port = book.open_risk() + it.qty * it.risk_per_share
        port_cap = ctx.equity * cfg.max_portfolio_risk_pct / 100.0
        add("portfolio_risk", port <= port_cap,
            f"open risk incl. this trade Rs{port:,.0f} vs cap Rs{port_cap:,.0f} "
            f"({cfg.max_portfolio_risk_pct}%)")

        add("capital_available", it.notional <= ctx.available_capital,
            f"needs Rs{it.notional:,.0f}, Rs{ctx.available_capital:,.0f} uncommitted")
        if ctx.broker_cash is not None:
            add("broker_cash", it.notional <= ctx.broker_cash,
                f"needs Rs{it.notional:,.0f}, broker reports Rs{ctx.broker_cash:,.0f} available")

        if ctx.last_close:
            dev = abs(it.entry_limit / ctx.last_close - 1) * 100
            add("price_band", dev <= cfg.price_band_pct,
                f"entry {dev:.1f}% from last close (max {cfg.price_band_pct}%)")
        else:
            add("price_band", False, "no last close to sanity-check the entry against")

        if ctx.live_price is not None:
            add("live_price_above_stop", ctx.live_price > it.stop,
                f"LTP {ctx.live_price} vs stop {it.stop}")
        elif ctx.require_live_price:
            add("live_price_above_stop", False,
                "no last traded price — a live order is never sent blind")

        risk_rupees = it.qty * it.risk_per_share
        drag = it.est_charges / risk_rupees if risk_rupees > 0 else float("inf")
        add("cost_drag", drag <= 0.15,
            f"charges are {drag:.0%} of the rupee risk (max 15%)")

        ok, detail = self._correlation(it.symbol, book, ctx)
        add("correlation", ok, detail)

        return RiskDecision(all(c.passed for c in checks), checks)

    def _correlation(self, symbol: str, book: Book, ctx: RiskContext) -> tuple[bool, str]:
        """
        The scanner ranks names independently and cannot see that two of them
        are one bet. This can: daily-return correlation against every name
        already held or pending.
        """
        held = sorted({t.symbol for t in book.active()} - {symbol})
        if not held:
            return True, "no open positions to correlate with"
        r = ctx.returns.get(symbol)
        if r is None:
            return False, "no return history for the candidate — cannot rule out a doubled bet"
        worst_sym, worst = None, -1.0
        thin = []
        for h in held:
            rh = ctx.returns.get(h)
            if rh is None:
                thin.append(h)
                continue
            joined = pd.concat([r.rename("a"), rh.rename("b")], axis=1).dropna()
            joined = joined.tail(self.cfg.correlation_lookback)
            if len(joined) < 30:
                thin.append(h)
                continue
            c = float(joined["a"].corr(joined["b"]))
            if c != c:          # NaN: a flat or broken series cannot be measured
                thin.append(h)
                continue
            if c > worst:
                worst_sym, worst = h, c
        if thin:
            return False, f"no comparable history for held {', '.join(thin)} — cannot measure"
        if worst_sym is None:
            return True, "nothing measurable"
        return (
            worst <= self.cfg.max_pair_correlation,
            f"highest correlation {worst:.2f} with {worst_sym} (max {self.cfg.max_pair_correlation})",
        )

    # -- account-level breakers -------------------------------------------

    def circuit_breakers(self, book: Book, today: str) -> list[str]:
        cfg = self.cfg
        eq = book.equity()
        trips: list[str] = []
        base = book.baseline_equity(today)
        if base > 0:
            chg = (eq / base - 1) * 100
            if chg <= -cfg.daily_loss_limit_pct:
                trips.append(
                    f"daily loss {chg:.2f}% breached the -{cfg.daily_loss_limit_pct}% limit "
                    f"(equity Rs{eq:,.0f} vs Rs{base:,.0f} at the previous cycle)"
                )
        peak = max(book.peak_equity, eq)
        if peak > 0:
            dd = (eq / peak - 1) * 100
            if dd <= -cfg.max_drawdown_pct:
                trips.append(
                    f"drawdown {dd:.2f}% from peak Rs{peak:,.0f} breached the "
                    f"-{cfg.max_drawdown_pct}% limit"
                )
        return trips
