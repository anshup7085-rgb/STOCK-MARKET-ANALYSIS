"""
Indian equity transaction costs, and what they do to a trade's arithmetic.

RISK_POLICY.md asks for charges to be accounted for 'where data is available'.
Until now nothing here did, and every max-loss figure the system produced was
optimistic by an unstated amount. This module closes that gap.

Two things it computes, and the distinction matters:

  1. **Costs** — brokerage, STT, exchange transaction charges, SEBI turnover
     fees, stamp duty, GST and DP charges, on the actual round trip. Pure
     arithmetic, no forecasting.

  2. **Breakeven win rate** — given the net reward and net loss after those
     costs, the fraction of trades you must win merely to break even. Also pure
     arithmetic, and it is the honest form of 'expected return': it says what
     the setup demands of you, without pretending to know how often you'll be
     right.

What it deliberately does NOT do is claim an expected return outright. That
needs a win rate, a win rate is an empirical property of the strategy, and
`run_backtest.py` exists to measure it. Until that number exists for a real
feed, any 'expected return' would be a forecast wearing arithmetic's clothes.
`expected_value_at()` will give you the number for a win rate YOU supply, which
keeps the assumption visible instead of buried.

RATE ACCURACY
-------------
The defaults below reflect commonly published retail rates for NSE equities and
are stored with `verified_on = None` on purpose. Statutory rates change — STT,
stamp duty and exchange transaction charges have all moved in recent years, and
brokerage varies by broker and plan. Verify against your broker's current
schedule and your contract note, then set `verified_on`. DATA_SOURCES.md governs
this the same as it governs a price.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChargeSchedule:
    """A broker's cost structure for one segment. All percentages, not decimals."""

    name: str
    segment: str                       # "delivery" | "intraday"
    brokerage_pct: float               # % of turnover, per leg
    brokerage_cap: float | None        # rupee cap per order, None = uncapped
    stt_buy_pct: float
    stt_sell_pct: float
    exchange_txn_pct: float            # per leg
    sebi_pct: float                    # per leg
    stamp_duty_buy_pct: float          # buy leg only
    gst_pct: float                     # on brokerage + exchange + SEBI
    dp_charge_per_sell: float          # flat, delivery sells only, GST-inclusive
    verified_on: str | None = None     # set this once YOU have checked it

    def unverified(self) -> bool:
        return self.verified_on is None


# Discount-broker style defaults. Verify before trusting a rupee figure.
DELIVERY = ChargeSchedule(
    name="discount broker — equity delivery (CNC)",
    segment="delivery",
    brokerage_pct=0.0,                 # many discount brokers are zero here
    brokerage_cap=None,
    stt_buy_pct=0.1,
    stt_sell_pct=0.1,
    exchange_txn_pct=0.00297,
    sebi_pct=0.0001,
    stamp_duty_buy_pct=0.015,
    gst_pct=18.0,
    dp_charge_per_sell=15.34,
)

INTRADAY = ChargeSchedule(
    name="discount broker — equity intraday (MIS)",
    segment="intraday",
    brokerage_pct=0.03,
    brokerage_cap=20.0,
    stt_buy_pct=0.0,
    stt_sell_pct=0.025,
    exchange_txn_pct=0.00297,
    sebi_pct=0.0001,
    stamp_duty_buy_pct=0.003,
    gst_pct=18.0,
    dp_charge_per_sell=0.0,
)

SCHEDULES = {"delivery": DELIVERY, "intraday": INTRADAY}


@dataclass
class CostBreakdown:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange_txn: float = 0.0
    sebi: float = 0.0
    stamp_duty: float = 0.0
    gst: float = 0.0
    dp: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.brokerage + self.stt + self.exchange_txn
            + self.sebi + self.stamp_duty + self.gst + self.dp
        )

    def as_dict(self) -> dict[str, float]:
        d = {
            "brokerage": round(self.brokerage, 2),
            "stt": round(self.stt, 2),
            "exchange_txn": round(self.exchange_txn, 2),
            "sebi": round(self.sebi, 2),
            "stamp_duty": round(self.stamp_duty, 2),
            "gst": round(self.gst, 2),
            "dp": round(self.dp, 2),
        }
        d["total"] = round(self.total, 2)
        return d


def _leg_brokerage(turnover: float, sched: ChargeSchedule) -> float:
    b = turnover * sched.brokerage_pct / 100.0
    if sched.brokerage_cap is not None:
        b = min(b, sched.brokerage_cap)
    return b


def round_trip_costs(
    entry_px: float, exit_px: float, qty: int, sched: ChargeSchedule,
    direction: str = "long",
) -> CostBreakdown:
    """
    Full round-trip cost. Both legs are charged; STT and stamp duty are not
    symmetric, so which leg is the 'buy' depends on direction.
    """
    if qty <= 0:
        return CostBreakdown()

    buy_px, sell_px = (entry_px, exit_px) if direction == "long" else (exit_px, entry_px)
    buy_to, sell_to = buy_px * qty, sell_px * qty

    brokerage = _leg_brokerage(buy_to, sched) + _leg_brokerage(sell_to, sched)
    stt = buy_to * sched.stt_buy_pct / 100.0 + sell_to * sched.stt_sell_pct / 100.0
    exch = (buy_to + sell_to) * sched.exchange_txn_pct / 100.0
    sebi = (buy_to + sell_to) * sched.sebi_pct / 100.0
    stamp = buy_to * sched.stamp_duty_buy_pct / 100.0
    gst = (brokerage + exch + sebi) * sched.gst_pct / 100.0
    dp = sched.dp_charge_per_sell if sched.segment == "delivery" else 0.0

    return CostBreakdown(
        brokerage=brokerage, stt=stt, exchange_txn=exch, sebi=sebi,
        stamp_duty=stamp, gst=gst, dp=dp,
    )


@dataclass
class TradeEconomics:
    """Everything a trade's arithmetic says, after costs are taken out."""

    qty: int
    notional: float
    schedule: str
    schedule_verified: bool

    gross_reward_risk: float
    net_reward_risk: float

    gross_profit_at_target: float
    net_profit_at_target: float
    gross_loss_at_stop: float
    net_loss_at_stop: float

    cost_at_target: float
    cost_at_stop: float
    cost_drag_r: float             # round-trip cost expressed in units of risk
    breakeven_move_pct: float      # move needed just to cover costs
    breakeven_win_rate_pct: float  # win rate needed to break even at this R:R

    slippage_assumed_pct: float
    warnings: list[str] = field(default_factory=list)

    def expected_value_at(self, win_rate_pct: float) -> float:
        """
        Rupee EV per trade at a win rate YOU supply.

        Not a prediction. The win rate is the input; supplying it is how the
        assumption stays visible. Measure it with run_backtest.py.
        """
        p = win_rate_pct / 100.0
        return p * self.net_profit_at_target - (1 - p) * abs(self.net_loss_at_stop)


def trade_economics(
    entry: float, stop: float, target: float, qty: int,
    *,
    segment: str = "delivery",
    slippage_pct: float = 0.05,
    schedule: ChargeSchedule | None = None,
) -> TradeEconomics | None:
    """
    Net arithmetic for one trade plan.

    Slippage is applied against you on both legs — a worse entry and a worse
    exit — because RISK_POLICY.md says a stop is a trigger, not a guaranteed
    fill, and the same is true of a target in a fast market.
    """
    sched = schedule or SCHEDULES.get(segment, DELIVERY)
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0 or qty <= 0:
        return None

    long = target > entry
    slip = slippage_pct / 100.0

    # Worse fill on the way in, worse fill on the way out.
    eff_entry = entry * (1 + slip) if long else entry * (1 - slip)
    eff_target = target * (1 - slip) if long else target * (1 + slip)
    eff_stop = stop * (1 - slip) if long else stop * (1 + slip)

    sign = 1 if long else -1
    gross_win = (eff_target - eff_entry) * qty * sign
    gross_loss = (eff_stop - eff_entry) * qty * sign          # negative

    direction = "long" if long else "short"
    cost_win = round_trip_costs(eff_entry, eff_target, qty, sched, direction).total
    cost_loss = round_trip_costs(eff_entry, eff_stop, qty, sched, direction).total

    net_win = gross_win - cost_win
    net_loss = gross_loss - cost_loss                          # more negative

    notional = entry * qty
    gross_rr = abs(target - entry) / risk_per_share
    net_rr = net_win / abs(net_loss) if net_loss != 0 else 0.0

    # Breakeven win rate: p*W - (1-p)*|L| = 0  ->  p = |L| / (W + |L|)
    if net_win <= 0:
        be_win_rate = 100.0
    else:
        be_win_rate = abs(net_loss) / (net_win + abs(net_loss)) * 100.0

    avg_cost = (cost_win + cost_loss) / 2
    warnings: list[str] = []
    if sched.unverified():
        warnings.append(
            f"Rates for '{sched.name}' are UNVERIFIED defaults — check your broker's "
            "current schedule and a real contract note before trusting these rupees."
        )
    if net_rr < 2.0:
        warnings.append(
            f"Net R:R {net_rr:.2f} falls below the 2.0 floor once costs and "
            f"{slippage_pct}% slippage are taken out (gross was {gross_rr:.2f})."
        )
    if net_win <= 0:
        warnings.append("Target does not cover costs. This trade cannot make money.")
    if avg_cost > 0.15 * abs(gross_loss):
        warnings.append(
            f"Costs are {avg_cost / abs(gross_loss) * 100:.0f}% of the risk budget — "
            "the position is too small for the fixed charges."
        )

    return TradeEconomics(
        qty=qty,
        notional=round(notional, 2),
        schedule=sched.name,
        schedule_verified=not sched.unverified(),
        gross_reward_risk=round(gross_rr, 2),
        net_reward_risk=round(net_rr, 2),
        gross_profit_at_target=round(gross_win, 2),
        net_profit_at_target=round(net_win, 2),
        gross_loss_at_stop=round(gross_loss, 2),
        net_loss_at_stop=round(net_loss, 2),
        cost_at_target=round(cost_win, 2),
        cost_at_stop=round(cost_loss, 2),
        cost_drag_r=round(avg_cost / (risk_per_share * qty), 3),
        breakeven_move_pct=round(avg_cost / notional * 100, 3),
        breakeven_win_rate_pct=round(be_win_rate, 1),
        slippage_assumed_pct=slippage_pct,
        warnings=warnings,
    )
