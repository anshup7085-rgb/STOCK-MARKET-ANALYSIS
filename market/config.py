"""
Configuration. Secrets come from the environment ONLY.

RISK_POLICY / CLAUDE.md rule: never request or store passwords, OTPs, PINs,
API secrets or private keys in plain text. Nothing in this file holds a secret;
it only reads them from the process environment at runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# NSE regular session (IST)
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)


@dataclass(frozen=True)
class Thresholds:
    """Screening and scoring thresholds. Tune these; do not tune them per-trade."""

    # Liquidity: reject anything thinner than this. Median over lookback.
    min_median_turnover_cr: float = 25.0      # Rs crore/day
    min_median_volume: int = 200_000          # shares/day
    liquidity_lookback: int = 20

    # Data quality gates
    max_bar_staleness_days: int = 4           # EOD data older than this -> reject
    min_bars_required: int = 120              # need history for 100-EMA + ATR

    # Volatility / level construction
    atr_period: int = 14
    stop_atr_multiple: float = 1.5
    target1_r: float = 1.5                    # R-multiples off the stop distance
    target2_r: float = 3.0
    stretch_r: float = 5.0

    # Minimum acceptable reward:risk. Below this -> NO TRADE, per RISK_POLICY.
    min_reward_risk: float = 2.0

    # Score floor for inclusion in the shortlist
    min_score: float = 60.0


@dataclass(frozen=True)
class Settings:
    provider: str = field(default_factory=lambda: os.getenv("MARKET_PROVIDER", "yfinance"))
    kite_api_key: str | None = field(default_factory=lambda: os.getenv("KITE_API_KEY"))
    kite_access_token: str | None = field(default_factory=lambda: os.getenv("KITE_ACCESS_TOKEN"))
    upstox_access_token: str | None = field(default_factory=lambda: os.getenv("UPSTOX_ACCESS_TOKEN"))

    # Portfolio inputs. Left as None deliberately: PROMPT.md says ask for account
    # size rather than invent a rupee amount.
    account_equity: float | None = field(
        default_factory=lambda: _opt_float("ACCOUNT_EQUITY")
    )
    risk_per_trade_pct: float = field(
        default_factory=lambda: float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
    )
    max_portfolio_risk_pct: float = field(
        default_factory=lambda: float(os.getenv("MAX_PORTFOLIO_RISK_PCT", "5.0"))
    )
    max_single_position_pct: float = field(
        default_factory=lambda: float(os.getenv("MAX_SINGLE_POSITION_PCT", "20.0"))
    )

    thresholds: Thresholds = field(default_factory=Thresholds)

    def credential_status(self) -> dict[str, bool]:
        """Report which credentials are present WITHOUT echoing their values."""
        return {
            "kite_api_key": bool(self.kite_api_key),
            "kite_access_token": bool(self.kite_access_token),
            "upstox_access_token": bool(self.upstox_access_token),
        }


def _opt_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError:
        return None


settings = Settings()
