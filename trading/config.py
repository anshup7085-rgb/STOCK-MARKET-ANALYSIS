"""
Execution-layer settings. Environment-driven, like market/config.py, and like it
holds no secret: broker credentials are read by the broker adapter at the
moment it connects and are never copied here.

Every limit is a ceiling the risk engine enforces, not a suggestion it reports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from market.config import Thresholds

REPO_ROOT = Path(__file__).resolve().parent.parent

# SEBI's retail-algo framework treats order rates above 10/second per exchange
# segment as needing a registered strategy. Staying well under it is a design
# constraint of this system, not a tuning knob — hence the hard ceiling.
EXCHANGE_OPS_THRESHOLD = 10.0


def _f(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    return int(_f(name, default))


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class TradingConfig:
    mode: str = "paper"                      # paper | live | replay
    state_root: Path = field(
        default_factory=lambda: Path(os.getenv("TRADING_STATE_DIR", str(REPO_ROOT / "state")))
    )

    # --- per-trade and portfolio risk -------------------------------------
    risk_per_trade_pct: float = field(default_factory=lambda: _f("RISK_PER_TRADE_PCT", 1.0))
    # ROADMAP Gate 3: small live runs at 0.25% risk per trade until 40 live
    # trades say otherwise. This cap applies in live mode whatever the line above says.
    live_max_risk_per_trade_pct: float = field(
        default_factory=lambda: _f("LIVE_MAX_RISK_PER_TRADE_PCT", 0.25)
    )
    max_portfolio_risk_pct: float = field(default_factory=lambda: _f("MAX_PORTFOLIO_RISK_PCT", 5.0))
    max_single_position_pct: float = field(default_factory=lambda: _f("MAX_SINGLE_POSITION_PCT", 20.0))
    max_open_positions: int = field(default_factory=lambda: _i("MAX_OPEN_POSITIONS", 5))
    max_new_entries_per_cycle: int = field(default_factory=lambda: _i("MAX_NEW_ENTRIES_PER_CYCLE", 3))
    max_pair_correlation: float = field(default_factory=lambda: _f("MAX_PAIR_CORRELATION", 0.75))
    correlation_lookback: int = 60

    # --- circuit breakers (trip the kill switch; release is manual) --------
    daily_loss_limit_pct: float = field(default_factory=lambda: _f("DAILY_LOSS_LIMIT_PCT", 2.0))
    max_drawdown_pct: float = field(default_factory=lambda: _f("MAX_DRAWDOWN_PCT", 10.0))

    # --- order flow ------------------------------------------------------
    max_orders_per_day: int = field(default_factory=lambda: _i("MAX_ORDERS_PER_DAY", 20))
    max_orders_per_second: float = field(
        default_factory=lambda: min(_f("MAX_ORDERS_PER_SECOND", 5.0), EXCHANGE_OPS_THRESHOLD / 2)
    )

    # --- signal quality floors -------------------------------------------
    min_score: float = field(default_factory=lambda: _f("MIN_SCORE", Thresholds().min_score))
    min_coverage: float = 0.55                # CLAUDE.md: below this is "insufficient data"
    min_net_rr: float = field(default_factory=lambda: _f("MIN_NET_RR", 2.0))

    # --- trade construction ----------------------------------------------
    max_hold_bars: int = field(default_factory=lambda: _i("MAX_HOLD_BARS", 20))
    entry_at: str = field(default_factory=lambda: os.getenv("ENTRY_AT", "zone_high"))
    price_band_pct: float = 10.0              # fat-finger guard vs last close
    stop_limit_buffer_pct: float = field(default_factory=lambda: _f("STOP_LIMIT_BUFFER_PCT", 1.0))
    exit_limit_buffer_pct: float = field(default_factory=lambda: _f("EXIT_LIMIT_BUFFER_PCT", 2.0))
    slippage_pct: float = field(default_factory=lambda: _f("SLIPPAGE_PCT", 0.05))
    # Charge schedule key in market/charges.SCHEDULES. CNC delivery is the only
    # thing this engine trades; the key picks whose fee card applies.
    # The live path switches it to "groww_delivery" when the broker is Groww.
    segment: str = field(default_factory=lambda: os.getenv("CHARGE_SCHEDULE", "delivery"))

    # --- live-only ---------------------------------------------------------
    # Enter only while the session is open, so the last traded price can be
    # checked against the stop immediately before the order goes out. An
    # after-market order placed overnight cannot see a gap through its own stop.
    live_entries_in_session_only: bool = field(
        default_factory=lambda: _b("LIVE_ENTRIES_IN_SESSION_ONLY", True)
    )

    restricted_file: Path = field(default_factory=lambda: REPO_ROOT / "data" / "restricted.txt")

    @property
    def state_dir(self) -> Path:
        return self.state_root / self.mode

    @property
    def journal_path(self) -> Path:
        return self.state_dir / "journal.jsonl"

    @property
    def book_path(self) -> Path:
        return self.state_dir / "book.json"

    @property
    def kill_paths(self) -> list[Path]:
        """Mode-specific switch first (where an automatic trip is written), then global."""
        return [self.state_dir / "KILL", self.state_root / "KILL"]

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    def effective_risk_pct(self) -> float:
        if self.is_live:
            return min(self.risk_per_trade_pct, self.live_max_risk_per_trade_pct)
        return self.risk_per_trade_pct


def load_restricted(path: Path) -> dict[str, str]:
    """symbol -> reason, from the user-maintained restricted list."""
    out: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        body, _, comment = line.partition("#")
        sym = body.strip().upper()
        if sym:
            out[sym] = comment.strip() or "listed in restricted file"
    return out
