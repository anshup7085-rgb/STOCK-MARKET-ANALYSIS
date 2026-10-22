"""
Section 15 of research/PREREGISTRATION_2.md: every number the round-2 report
carries, binding and reported-only, as one JSON-able dict.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from research.round2 import DEV_LAST, ETFS, N_MONTHS, NotEvaluable, Panel, Schedule, fy_of
from research import strategy_t as st

PERIODS = {"full": slice(0, N_MONTHS), "development": slice(0, DEV_LAST), "holdout": slice(DEV_LAST, N_MONTHS)}


def _targets(name: str, panel: Panel, sch: Schedule) -> np.ndarray | None:
    if name == "T":
        return st.faber_targets(panel.Q, sch)
    if name == "T12":
        return st.tsmom_targets(panel.Q, sch)
    if name == "EXP":
        w = st.faber_targets(panel.Q, sch)
        return np.tile(w.mean(axis=0), (N_MONTHS, 1))
    return None


def run_line(name: str, panel: Panel, sch: Schedule, **kw) -> st.Run:
    w = _targets(name, panel, sch)
    if w is not None:
        return st.simulate("monthly", panel, sch, targets=w, **kw)
    return st.simulate(name, panel, sch, **kw)


def activity(name: str, run: st.Run, w: np.ndarray | None, sch: Schedule, sl: slice) -> dict:
    """
    Amendment 1. On the pre-tax REC price-track run, for holding months t in the
    slice: orders and rupee costs at sessions in [X_a, X_{b+1}); turnover a year =
    buys + sells in that span / mean V_t over the slice / years. share_in and
    switches (w_kt != w_k,t-1 with w_k0 = 0, so an entry at X_1 counts) for T and
    T12 only.
    """
    a, b = sl.start, sl.stop                      # t = a+1 .. b
    years = (b - a) / 12
    lo, hi = sch.X[a], sch.X[b]
    span = [e for e in run.book.log if lo <= e[0] < hi]
    out = {"orders": len(span), "costs_rupees": float(sum(e[4] for e in span)),
           "turnover_per_year": float(sum(e[3] for e in span)) / float(np.mean(run.V[a:b])) / years}
    if w is not None and name in ("T", "T12"):
        prev = np.vstack([np.zeros((1, 2)), w])[a:b]
        out["share_in"] = {k: float((w[a:b, j] > 0).mean()) for j, k in enumerate(ETFS)}
        out["switches_per_year"] = {k: float((w[a:b, j] != prev[:, j]).sum() / years) for j, k in enumerate(ETFS)}
    return out


def after_tax_block(run: st.Run) -> dict:
    return {"wealth": run.after_tax, "cagr": (run.after_tax / st.EQUITY) ** (12 / N_MONTHS) - 1,
            "value_before_liquidation": run.before_liquidation, "tax_by_bucket": dict(run.book.tax_by_bucket),
            "tax_by_fy": run.book.tax_by_fy, "losses_lapsed": run.book.lapsed}


def run_all(panel: Panel, sch: Schedule, stale: bool) -> dict:
    ev = st.evaluate(panel, sch, stale=stale)
    out: dict = {"verdict": ev["verdict"], "criteria": ev["criteria"], "D": ev["D"], "wbar": ev["wbar"],
                 "identity_gap": ev["identity_gap"]}
    P = dict(ev["P"])
    d = np.array(P.pop("d"))
    dstar = np.array(P.pop("dstar"))
    out["P"] = P
    w = ev["targets"]["T"]
    Tr, E = ev["runs"]["T"], ev["runs"]["EXP"]
    out["sleeve_contribution"] = next(c["detail"]["h"] for c in ev["criteria"] if c["id"] == "G2")
    fys = np.array([fy_of(__import__("pandas").Timestamp(year=y, month=m, day=1)) for y, m in sch.hold_month])
    # Amendment 1: the full-window d_t averaged within each FY's holding months (a decomposition of dbar).
    out["dbar_by_fy"] = {f"FY{y}": float(d[fys == y].mean()) for y in sorted(set(fys))}

    lines = {}
    for name in ("T", "EXP", "T12", "BH-N", "MIX"):
        try:
            run = run_line(name, panel, sch, daily=True)
        except NotEvaluable as exc:
            lines[name] = {"error": str(exc)}
            continue
        wt = _targets(name, panel, sch)
        lines[name] = {p: st.line_metrics(run, sch, s) for p, s in PERIODS.items()}
        lines[name]["activity"] = {per: activity(name, run, wt, sch, sl) for per, sl in PERIODS.items()}
        lines[name]["calendar_years"] = st.calendar_returns(run)
        try:
            lines[name]["after_tax"] = after_tax_block(run_line(name, panel, sch, taxed=True))
        except NotEvaluable as exc:
            lines[name]["after_tax"] = {"error": str(exc)}
    out["lines"] = lines

    grid = {}
    for transform in ("REC", "EQ"):
        for track in ("price", "div"):
            p = panel if transform == "REC" else st.eq_transform(panel, sch, div=(track == "div"))
            cell = {}
            for name in ("T", "EXP", "T12", "BH-N", "MIX"):
                try:
                    cell[name] = run_line(name, p, sch, taxed=True, div=(track == "div")).after_tax
                except NotEvaluable as exc:
                    cell[name] = str(exc)
            grid[f"{transform}/{track}"] = cell
    out["after_tax_grid"] = grid

    eqp = grid["EQ/price"]
    if isinstance(eqp.get("MIX"), float) and isinstance(eqp.get("BH-N"), float):
        delta = ((eqp["MIX"] / st.EQUITY) ** (12 / N_MONTHS)) - ((eqp["BH-N"] / st.EQUITY) ** (12 / N_MONTHS))
        out["mix_vs_bhn"] = {"delta_after_tax_cagr_EQ_price": delta,
                             "breakeven_relative_drift": 2 * delta,
                             "note": "MIX roughly matches BH-N after tax while gold grows no more than "
                                     "2*delta a year slower than the Nifty. Arithmetic, not a forecast."}

    sens = {"Zerodha card": {"card": st.ZERODHA}, "Rs10 lakh": {"equity": 1_000_000.0},
            "LTCG exemption": {"exemption": True}, "costs doubled": {"card": st.GROWW.doubled()},
            "cash at 6%": {"cash_rate": 0.06}}
    sout = {}
    for label, kw in sens.items():
        row = {}
        for name in ("T", "EXP", "BH-N", "MIX"):
            try:
                row[name] = run_line(name, panel, sch, taxed=True, **kw).after_tax
            except NotEvaluable as exc:
                row[name] = str(exc)
        if "exemption" not in kw:
            pre = {k: v for k, v in kw.items()}
            Tp = run_line("T", panel, sch, **pre)
            Ep = run_line("EXP", panel, sch, **pre)
            row["P"] = {k: v for k, v in st.primary(w, Tp, Ep, w.mean(axis=0)).items() if k != "d"}
        sout[label] = row
    out["sensitivities"] = sout

    out["reported_only"] = {
        "sharpe_diff_T_vs_EXP_hac_delta": st.sharpe_diff_hac(Tr.R, E.R),
        "regression_T_on_ETFs": st.regression_alpha(Tr.R, Tr.r),
        "stationary_bootstrap_p_on_dstar": st.stationary_bootstrap_p(dstar),
        "t_ge_3": P["t_ge_3"],
    }
    out["schedule"] = {"X1": str(sch.X[0].date()), "X201": str(sch.X[-1].date()), "deferrals": sch.deferrals,
                       "stale": stale}
    return out
