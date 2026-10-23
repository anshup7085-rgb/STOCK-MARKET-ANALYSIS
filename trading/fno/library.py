"""
Strategy templates for the intraday explorer (trading/fno/explore.py).

Each template is a starting point, not a recommendation: legs in the compact
notation of trading/fno/structures.py, a trigger, an entry window and exits. A
strategy in config/fno_strategies.example.json (or your own gitignored copy,
config/fno_strategies.json) names a template and overrides any field.

Leg notation, one token per leg:  KIND SIGN [RATIO] [@STRIKE-RULE] [/EXPIRY]
  FUT+            buy the near-month future        FUT-   sell it
  CE-@ATM+2       sell the call two strikes above the money (weekly)
  PE+@EM-1.0      buy the put one at-the-money straddle below the index
  CE-@PREM:20     sell the call whose premium is the highest at or below 20
  PE+2@ATM/next_weekly   buy two lots of the at-the-money put, next week's expiry

`legs` is one string for every direction, or a map from the trigger's
direction ("long", "short", "any") to a string.

Exits are fixed at entry, in rupees, from one of:
  {"R": x}            x times the trade's risk (its sized maximum loss)
  {"credit_frac": x}  a target of x times the credit received
  {"credit_mult": x}  a stop at a loss of x times the credit received
  {"points": x}       x points of the structure per lot (index points for a future)
  {"rupees": x}       x rupees per unit of the structure
An undefined-risk structure (a short future, a short straddle, FUT+ CE-) must
carry a stop that is not in R: its risk is the stop.
"""

from __future__ import annotations

ORB = {"type": "orb", "range_end": "09:30", "buffer_pct": 0.05}

TEMPLATES: dict[str, dict] = {
    # -- futures ---------------------------------------------------------------------
    "future_breakout": {
        "about": "Near-month future in the direction of an opening-range breakout. Undefined risk: the stop is the risk.",
        "legs": {"long": "FUT+", "short": "FUT-"},
        "trigger": ORB, "window": ["09:31", "13:30"],
        "exit": {"stop": {"points": 40}, "target": {"R": 1.5}},
    },
    "covered_call": {
        "about": "FUT+ CE- : long future, short out-of-the-money weekly call. The call's premium cushions a small fall "
                 "and caps the gain above its strike. The downside is the future's, so a stop is required.",
        "legs": {"long": "FUT+ CE-@ATM+2"},
        "trigger": ORB, "window": ["09:31", "13:30"],
        "exit": {"stop": {"points": 50}, "target": {"R": 1.0}},
    },
    "covered_put": {
        "about": "FUT- PE- : short future, short out-of-the-money weekly put; the mirror of the covered call. "
                 "Undefined risk on the upside, so a stop is required.",
        "legs": {"short": "FUT- PE-@ATM-2"},
        "trigger": ORB, "window": ["09:31", "13:30"],
        "exit": {"stop": {"points": 50}, "target": {"R": 1.0}},
    },
    "synthetic_put": {
        "about": "CE+ FUT- : short future hedged by a long call, a synthetic put. Defined risk: the call caps the "
                 "loss if the index rises.",
        "legs": {"short": "CE+@ATM FUT-"},
        "trigger": ORB, "window": ["09:31", "13:30"],
        "exit": {"stop": {"R": 0.6}, "target": {"R": 1.0}},
    },
    "protective_put": {
        "about": "FUT+ PE+ : long future hedged by a long put, a synthetic call. Defined risk.",
        "legs": {"long": "FUT+ PE+@ATM"},
        "trigger": ORB, "window": ["09:31", "13:30"],
        "exit": {"stop": {"R": 0.6}, "target": {"R": 1.0}},
    },
    # -- directional option spreads ------------------------------------------------------
    "debit_spread": {
        "about": "Bull call spread on an upside breakout, bear put spread on a downside one. Defined risk.",
        "legs": {"long": "CE+@ATM CE-@ATM+2", "short": "PE+@ATM PE-@ATM-2"},
        "trigger": ORB, "window": ["09:31", "13:00"],
        "exit": {"stop": {"R": 0.5}, "target": {"R": 0.8}},
    },
    "credit_spread": {
        "about": "Bull put spread on an upside breakout, bear call spread on a downside one: sells premium "
                 "behind the move. Defined risk.",
        "legs": {"long": "PE-@ATM-1 PE+@ATM-3", "short": "CE-@ATM+1 CE+@ATM+3"},
        "trigger": ORB, "window": ["09:31", "13:00"],
        "exit": {"stop": {"R": 0.5}, "target": {"credit_frac": 0.5}},
    },
    "long_option": {
        "about": "At-the-money call on an upside breakout, put on a downside one. Risk is the premium.",
        "legs": {"long": "CE+@ATM", "short": "PE+@ATM"},
        "trigger": ORB, "window": ["09:31", "13:00"],
        "exit": {"stop": {"R": 0.4}, "target": {"R": 0.8}},
    },
    # -- non-directional ------------------------------------------------------------------
    "short_straddle": {
        "about": "Sell the at-the-money straddle shortly after the open and hold to the square-off unless the "
                 "combined premium rises 30%. Undefined risk: the stop is the risk.",
        "legs": "CE-@ATM PE-@ATM",
        "trigger": {"type": "at"}, "window": ["09:20", "09:25"],
        "exit": {"stop": {"credit_mult": 0.3}},
    },
    "short_strangle": {
        "about": "Sell a call and a put about half a straddle away from the index. Undefined risk.",
        "legs": "CE-@EM+0.5 PE-@EM-0.5",
        "trigger": {"type": "at"}, "window": ["09:20", "09:25"],
        "exit": {"stop": {"credit_mult": 0.5}},
    },
    "iron_fly": {
        "about": "Short straddle with wings four strikes out, entered only if the index has stayed within 0.4% "
                 "of its open by late morning. Defined risk.",
        "legs": "CE-@ATM PE-@ATM CE+@ATM+4 PE+@ATM-4",
        "trigger": {"type": "range", "pct": 0.4}, "window": ["11:00", "12:30"],
        "exit": {"stop": {"R": 0.5}, "target": {"credit_frac": 0.3}},
    },
    "iron_condor": {
        "about": "Short call and put half a straddle out, wings a full straddle out. Defined risk.",
        "legs": "CE-@EM+0.5 PE-@EM-0.5 CE+@EM+1.0 PE+@EM-1.0",
        "trigger": {"type": "at"}, "window": ["09:30", "09:35"],
        "exit": {"stop": {"R": 0.5}, "target": {"credit_frac": 0.5}},
    },
    "long_straddle": {
        "about": "Buy the at-the-money straddle after a quiet morning, for a move in either direction. Risk is "
                 "the premium; time decay works against it all day.",
        "legs": "CE+@ATM PE+@ATM",
        "trigger": {"type": "range", "pct": 0.25}, "window": ["11:00", "12:00"],
        "exit": {"stop": {"R": 0.3}, "target": {"R": 0.5}},
    },
    "ratio_call_spread": {
        "about": "Buy one at-the-money call, sell two calls four strikes up, on an upside breakout. Undefined "
                 "risk above the short strikes, so a stop is required.",
        "legs": {"long": "CE+@ATM CE-2@ATM+4"},
        "trigger": ORB, "window": ["09:31", "13:00"],
        "exit": {"stop": {"points": 30}, "target": {"R": 0.8}},
    },
}


def template(name: str) -> dict:
    if name not in TEMPLATES:
        raise ValueError(f"unknown template {name!r}; known: {', '.join(sorted(TEMPLATES))}")
    return TEMPLATES[name]
