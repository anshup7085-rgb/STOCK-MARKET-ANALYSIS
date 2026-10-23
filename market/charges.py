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
from decimal import ROUND_HALF_UP, Decimal


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
    brokerage_min: float = 0.0         # rupee floor per order, applied after the cap
    brokerage_min_pct: float | None = None   # the floor is min(brokerage_min, this % of turnover)

    def unverified(self) -> bool:
        return self.verified_on is None


# Discount-broker style defaults. Verify before trusting a rupee figure.
# Checked against published rate cards on 2026-10-19, not yet against a contract
# note: STT, stamp duty, SEBI fee and GST unchanged; the NSE transaction charge
# is 0.00307% per leg from 2026-03-01 (NSE circular FA73061, 2026-02-27; it was
# 0.00297% plus a Rs10/crore IPFT levy from 2024-10-01); Zerodha delivery
# brokerage Rs0 and DP Rs15.34 per scrip per sell day.
DELIVERY = ChargeSchedule(
    name="discount broker — equity delivery (CNC)",
    segment="delivery",
    brokerage_pct=0.0,                 # many discount brokers are zero here
    brokerage_cap=None,
    stt_buy_pct=0.1,
    stt_sell_pct=0.1,
    exchange_txn_pct=0.00307,
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
    exchange_txn_pct=0.00307,
    sebi_pct=0.0001,
    stamp_duty_buy_pct=0.003,
    gst_pct=18.0,
    dp_charge_per_sell=0.0,
)

# Groww charges brokerage on delivery too: the lower of Rs20 or 0.1% per
# executed order, with a floor of the lower of Rs5 or 2.5% of the order
# (groww.in help pages, checked 2026-10-19). The DP charge is Rs20 + GST =
# Rs23.60 per sell on groww.in/pricing, but Groww's own calculator shows
# Rs18.25 + GST; the higher figure is kept. Check a Groww contract note and set
# verified_on.
GROWW_DELIVERY = ChargeSchedule(
    name="Groww — equity delivery (CNC)",
    segment="delivery",
    brokerage_pct=0.1,
    brokerage_cap=20.0,
    brokerage_min=5.0,
    brokerage_min_pct=2.5,
    stt_buy_pct=0.1,
    stt_sell_pct=0.1,
    exchange_txn_pct=0.00307,
    sebi_pct=0.0001,
    stamp_duty_buy_pct=0.015,
    gst_pct=18.0,
    dp_charge_per_sell=23.6,
)

SCHEDULES = {"delivery": DELIVERY, "intraday": INTRADAY, "groww_delivery": GROWW_DELIVERY}


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
    if turnover > 0 and sched.brokerage_min:
        floor = sched.brokerage_min
        if sched.brokerage_min_pct is not None:
            floor = min(floor, turnover * sched.brokerage_min_pct / 100.0)
        b = max(b, floor)
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


# ---------------------------------------------------------------------------
# Equity derivatives (NSE F&O)
#
# Rates effective 2026-04-01, each confirmed on 2026-10-22 by two independent
# skeptics against search extracts of the primary sources (NSE circulars
# FATAX/73524 and FA/73061, the Income Tax Department's STT page, the brokers'
# own pages). Not yet checked against a contract note, so verified_on is None.
#   STT        options: 0.15% of premium, sell side; 0.15% of intrinsic value on
#              a long option settled in the money. Futures: 0.05%, sell side.
#   NSE txn    options 0.03553% of premium, futures 0.00183% of value, each side
#              (from 2026-03-01; the Rs0.01/crore IPFT is inside these figures)
#   SEBI fee   Rs10/crore (0.0001%) each side: premium for options, value for futures
#   Stamp      options 0.003% of premium, futures 0.002% of value, buy side only
#   GST        18% on brokerage + transaction charge + SEBI fee; not on STT or stamp
#   Brokerage  Rs20 per executed order (Groww both; Zerodha options). Zerodha
#              futures: the lower of 0.03% or Rs20.
# Two settlement charges are NOT established by those sources and are taken the
# way that costs more (research/PREREGISTRATION_3.md, section 9): brokerage on
# each leg settled in the money, and the SEBI fee on its notional value. No
# exchange charge on settlement is known, and none is applied.

@dataclass(frozen=True)
class FnoSchedule:
    name: str
    brokerage_per_order: float = 20.0
    fut_brokerage_pct: float | None = None     # Zerodha futures: lower of this % or the flat fee
    opt_stt_sell_pct: float = 0.15
    opt_stt_exercise_pct: float = 0.15
    opt_exchange_pct: float = 0.03553
    opt_stamp_buy_pct: float = 0.003
    fut_stt_sell_pct: float = 0.05
    fut_exchange_pct: float = 0.00183
    fut_stamp_buy_pct: float = 0.002
    sebi_pct: float = 0.0001
    gst_pct: float = 18.0
    settlement_brokerage: bool = True          # conservative reading, see above
    settlement_sebi: bool = True               # conservative reading, see above
    effective_from: str = "2026-04-01"
    verified_on: str | None = None

    def unverified(self) -> bool:
        return self.verified_on is None


FNO_GROWW = FnoSchedule(name="Groww — equity F&O")
FNO_ZERODHA = FnoSchedule(name="Zerodha — equity F&O", fut_brokerage_pct=0.03)
FNO_SCHEDULES = {"groww": FNO_GROWW, "kite": FNO_ZERODHA, "zerodha": FNO_ZERODHA}


def _paise(x: float) -> float:
    """Half-up to Rs0.01, the rounding PREREGISTRATION_3 section 8 fixes for each charge."""
    return float(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _gst(b: CostBreakdown, sched: FnoSchedule) -> CostBreakdown:
    """GST on brokerage + transaction charge + SEBI fee; then every charge to the paisa, half-up."""
    b.gst = (b.brokerage + b.exchange_txn + b.sebi) * sched.gst_pct / 100.0
    for f in ("brokerage", "stt", "exchange_txn", "sebi", "stamp_duty", "gst"):
        setattr(b, f, _paise(getattr(b, f)))
    return b


def option_order_costs(premium: float, qty: int, side: str, sched: FnoSchedule) -> CostBreakdown:
    """One executed option order: `qty` units (lots x lot size) at `premium` per unit."""
    if qty <= 0:
        return CostBreakdown()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, not {side!r}")
    turnover = premium * qty
    b = CostBreakdown(
        brokerage=sched.brokerage_per_order,
        stt=turnover * sched.opt_stt_sell_pct / 100.0 if side == "SELL" else 0.0,
        exchange_txn=turnover * sched.opt_exchange_pct / 100.0,
        sebi=turnover * sched.sebi_pct / 100.0,
        stamp_duty=turnover * sched.opt_stamp_buy_pct / 100.0 if side == "BUY" else 0.0,
    )
    return _gst(b, sched)


def option_settlement_costs(intrinsic: float, qty: int, long: bool, sched: FnoSchedule,
                            notional_price: float | None = None) -> CostBreakdown:
    """
    A cash-settled option at expiry. Out of the money (intrinsic 0) costs nothing.
    In the money: STT on intrinsic value for the holder only, plus the two
    conservative readings: brokerage per leg, and the SEBI fee on the leg's
    notional value (`notional_price` x qty: PREREGISTRATION_3 uses max(P, K)).
    """
    if qty <= 0 or intrinsic <= 0:
        return CostBreakdown()
    basis = (notional_price if notional_price is not None else intrinsic) * qty
    b = CostBreakdown(
        brokerage=sched.brokerage_per_order if sched.settlement_brokerage else 0.0,
        stt=intrinsic * qty * sched.opt_stt_exercise_pct / 100.0 if long else 0.0,
        sebi=basis * sched.sebi_pct / 100.0 if sched.settlement_sebi else 0.0,
    )
    return _gst(b, sched)
def futures_order_costs(price: float, qty: int, side: str, sched: FnoSchedule) -> CostBreakdown:
    """One executed futures order of `qty` units at `price`."""
    if qty <= 0:
        return CostBreakdown()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, not {side!r}")
    value = price * qty
    brokerage = sched.brokerage_per_order
    if sched.fut_brokerage_pct is not None:
        brokerage = min(brokerage, value * sched.fut_brokerage_pct / 100.0)
    b = CostBreakdown(
        brokerage=brokerage,
        stt=value * sched.fut_stt_sell_pct / 100.0 if side == "SELL" else 0.0,
        exchange_txn=value * sched.fut_exchange_pct / 100.0,
        sebi=value * sched.sebi_pct / 100.0,
        stamp_duty=value * sched.fut_stamp_buy_pct / 100.0 if side == "BUY" else 0.0,
    )
    return _gst(b, sched)
