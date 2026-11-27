"""
EXPLORATION AFTER THE FACT, NOT EVIDENCE: the crypto futures desk with limit (maker) orders and realistic
fills. Exploration years only (Jan 2021 to Oct 2024), the same data research/perps_lab.py read.

    PYTHONPATH=. .venv/bin/python research/perps_maker.py

Why: step 44's only positive figure was its maker-fee what-if at H = 240 (+4.09 bps a trade, z 2.03), which
assumed every limit order fills at the close at no cost. Real limit orders fill only when the market comes
to them, and those are often the trades that go wrong. This measures what is left once fills are honest.
It was chosen AFTER that figure was seen, so whatever it shows is a gate for paper trading, never a pass.

EVERYTHING BELOW WAS FIXED ON 27 NOV 2026, BEFORE THIS WAS RUN.

Model     As step 43 at H = 240 (pooled trees per side, walk-forward by quarter from Jan 2022, training on
          brackets entered at the close), except that the funding rate is not a feature: Binance publishes
          funding only in monthly files, so a desk run each day on yesterday's minutes would not have it.
Orders    At each 5-minute decision, a limit order at the close (buy for a long, sell for a short). FILLED
          only if a later minute trades through it (a low below a buy, a high above a sell) within 5
          minutes; a touch is not a fill (others were ahead in the queue). Unfilled: cancelled at the next
          decision. Filled: target 1.5u and stop 1u from the fill price, u as in step 43 (known at the
          decision); 240 minutes from the fill minute. The stop is checked from the fill minute itself, the
          target only from the minute after (which came first inside one minute is unknown). The target is
          a resting limit order, filled only if the market trades through it; the stop is a market order,
          filled at its level or at the minute's open if the open is already past it; a time-out leaves at
          the close by a market order.
Costs     Maker 0.02% plus 18% GST = 0.0236% a side (entry, and a target exit). Taker 0.059% plus the assumed
          0.02% slippage = 0.079% for a stop or a time-out exit. Funding as Binance charged it.
Decision  Expected return = the model's expected gross return - (maker entry + p(target) x maker exit +
          (1 - p(target)) x taker exit); trade the better side if above 0. The desk: one pool, one order or
          position at a time, the highest expected return per expected minute held, as in step 43.

THE READING, FIXED IN ADVANCE (a gate, not a test): ALIVE if the desk has at least 500 filled trades, its mean
net return per filled trade is above 0 and at least 2/3 of the 34 test months are net positive; otherwise DEAD. ALIVE: the model is frozen
(trained on the last 24 exploration months, saved with its SHA-256) and run forward on paper each day on
Binance's daily files. DEAD: the maker idea is closed; realistic fills cost more than the edge.
Beside it, the same model with the ideal fills of step 44's what-if, so the cost of honest fills is visible.
"""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from crypto import minutes as cm
from crypto.store import EXPLORE_TO
from research import perps_lab as pl

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "state" / "crypto" / "perps_lab" / "maker.json"
MODEL_PATH = REPO / "models" / "desk_h240.pkl"
H = 240
WAIT = 5
MAKER = 0.0002 * 1.18
TAKER_SIDE = 0.0005 * 1.18 + 0.0002
DROP = ("funding",)
MIN = pl.MIN
NOFILL, FILLED, BAD = 0, 1, -1
MIN_TRADES = 500


def maker_trades(g: dict, sig: np.ndarray, idx: np.ndarray, h: int, side: int, wait: int = WAIT,
                 chunk: int = 20_000) -> dict[str, np.ndarray]:
    """For decisions at grid positions idx: status (FILLED, NOFILL, BAD for missing candles), outcome class,
    return in units of u from the fill price, fees (fraction of notional), minutes from the decision to the
    exit (or to the cancel), minutes from the decision to the fill."""
    n = len(g["close"])
    m = len(idx)
    out = {"status": np.full(m, BAD, dtype=np.int8), "cls": np.full(m, -1, dtype=np.int8),
           "ret": np.full(m, np.nan), "fees": np.zeros(m), "dur": np.full(m, wait, dtype=np.int32),
           "fill_at": np.zeros(m, dtype=np.int32)}
    w_steps, h_steps = np.arange(1, wait + 1), np.arange(h)
    for s in range(0, m, chunk):
        d_all = idx[s:s + chunk]
        ok = d_all + wait + h < n
        d = d_all[ok]
        at = np.nonzero(ok)[0] + s
        jw = d[:, None] + w_steps[None, :]
        limit = g["close"][d]
        lo_w, hi_w = g["low"][jw], g["high"][jw]
        through = (lo_w < limit[:, None]) if side > 0 else (hi_w > limit[:, None])
        valid_w = ~np.isnan(lo_w).any(1) & ~np.isnan(hi_w).any(1)
        filled = through.any(1) & valid_w
        out["status"][at] = np.where(valid_w, np.where(filled, FILLED, NOFILL), BAD)
        f = d + 1 + through.argmax(1)
        j = f[:, None] + h_steps[None, :]
        hi, lo, op = g["high"][j], g["low"][j], g["open"][j]
        u = sig[d] * math.sqrt(h)
        if side > 0:
            tp, sl = limit * (1 + pl.TP_U * u), limit * (1 - pl.SL_U * u)
            hit_tp, hit_sl = hi > tp[:, None], lo <= sl[:, None]
        else:
            tp, sl = limit * (1 - pl.TP_U * u), limit * (1 + pl.SL_U * u)
            hit_tp, hit_sl = lo < tp[:, None], hi >= sl[:, None]
        hit_tp[:, 0] = False
        first_tp = np.where(hit_tp.any(1), hit_tp.argmax(1), h)
        first_sl = np.where(hit_sl.any(1), hit_sl.argmax(1), h)
        stop = (first_sl < h) & (first_sl <= first_tp)
        target = (first_tp < h) & ~stop
        k = np.where(stop, first_sl, np.where(target, first_tp, h - 1))
        rows = np.arange(len(d))
        gap = op[rows, k]
        sl_fill = np.minimum(sl, gap) if side > 0 else np.maximum(sl, gap)
        exit_ = np.where(stop, sl_fill, np.where(target, tp, g["close"][f + h - 1]))
        valid_t = ~np.isnan(hi).any(1) & ~np.isnan(lo).any(1) & ~np.isnan(op).any(1) & ~np.isnan(g["close"][f + h - 1])
        good = filled & valid_t
        out["status"][at[filled & ~valid_t]] = BAD
        cls = np.where(stop, pl.SL, np.where(target, pl.TP, pl.TO))
        out["cls"][at[good]] = cls[good]
        out["ret"][at[good]] = (side * (exit_ / limit - 1) / u)[good]
        out["fees"][at[good]] = (MAKER + np.where(target, MAKER, TAKER_SIDE))[good]
        out["dur"][at[good]] = (f + k - d)[good]
        out["fill_at"][at[good]] = (f - d)[good]
    return out


def build(symbols, root=None, log=print) -> tuple[pd.DataFrame, list[dict]]:
    tabs, keeps, btc = [], [], None
    for i, name in enumerate(symbols):
        keep: dict = {}
        tab = pl.coin_table(i, name, root, btc, log=log, horizons=(H,), keep=keep)
        if name == "BTCUSDT":
            btc = tab[["t_close", "ret5", "ret60"]]
        for side, s in (("long", 1), ("short", -1)):
            mk = maker_trades(keep["g"], keep["sig"], keep["idx"], H, s)
            t_close = tab["t_close"].to_numpy()
            fund = pl.funding_paid(keep["fund_t"], keep["fund_r"], t_close + mk["fill_at"].astype(np.int64) * MIN,
                                   t_close + mk["dur"].astype(np.int64) * MIN, np.full(len(tab), s))
            for k in ("status", "cls", "ret", "fees", "dur"):
                tab[f"m{k}_{side}"] = mk[k]
            tab[f"mfund_{side}"] = np.where(mk["status"] == FILLED, fund, 0.0)
        tabs.append(tab)
        keeps.append(keep)
    tab = pd.concat(tabs, ignore_index=True)
    tab["month"] = pd.to_datetime(tab["t_close"], unit="ms").dt.strftime("%Y-%m")
    return tab, keeps


def names_of(tab: pd.DataFrame) -> list[str]:
    return [c for c in pl.feature_names(tab) if c not in DROP and not c.startswith("m")]


def expected(rows: pd.DataFrame, side: str) -> np.ndarray:
    p = rows[f"p_tp_{side}"].to_numpy()
    return rows[f"ev_{side}"].to_numpy() - (MAKER + p * MAKER + (1 - p) * TAKER_SIDE)


def desk(rows: pd.DataFrame, n_months: int, one_pool: bool = True) -> tuple[dict, pd.DataFrame]:
    evl, evs = expected(rows, "long"), expected(rows, "short")
    lg = evl >= evs
    best = np.where(lg, evl, evs)
    hold = np.where(lg, rows["hold_long"], rows["hold_short"])
    status = np.where(lg, rows["mstatus_long"], rows["mstatus_short"])
    cand = rows.assign(best=best, rank=best / np.maximum(hold, 1.0), long=lg, st=status)
    cand = cand[(cand["best"] > 0) & (cand["st"] != BAD)]
    if one_pool:
        cand = cand.sort_values(["t_close", "rank"], ascending=[True, False]).drop_duplicates("t_close")
    cand = cand.sort_values("t_close")
    t = cand["t_close"].to_numpy()
    lgc = cand["long"].to_numpy()
    dur = np.where(lgc, cand["mdur_long"], cand["mdur_short"]).astype(np.int64)
    coins = cand["coin"].to_numpy()
    taken, busy, free_at = [], 0, {}
    for i in range(len(cand)):
        key = 0 if one_pool else coins[i]
        if t[i] >= free_at.get(key, busy if one_pool else 0):
            taken.append(i)
            free_at[key] = t[i] + dur[i] * MIN
    orders = cand.iloc[taken]
    lg2 = orders["long"].to_numpy()
    st = np.where(lg2, orders["mstatus_long"], orders["mstatus_short"])
    tr = orders[st == FILLED]
    lg3 = tr["long"].to_numpy()
    r = np.where(lg3, tr["mret_long"], tr["mret_short"])
    fees = np.where(lg3, tr["mfees_long"], tr["mfees_short"])
    fund = np.where(lg3, tr["mfund_long"], tr["mfund_short"])
    gross = r * tr["u"].to_numpy() * math.sqrt(H)
    net = gross - fees + fund
    s = pl.stats(net, gross, tr["month"].to_numpy(), n_months)
    s.update(orders=len(orders), fill_rate=float(len(tr) / len(orders)) if len(orders) else 0.0,
             fees_bps=float(np.mean(fees) * 1e4) if len(tr) else 0.0)
    return s, tr.assign(net=net)


def line(name: str, s: dict) -> str:
    return (f"  {name:<6} {s['orders']:6,} orders, {s['fill_rate']:5.1%} filled, {s['trades']:5,} trades, won {s['win']:5.1%}, "
            f"gross {s['gross_bps']:+6.2f} bps, fees {s['fees_bps']:5.2f}, net {s['net_bps']:+6.2f} bps a trade, "
            f"total {s['total_pct']:+7.1f}%, z {s['z']:+5.2f}, months positive {s['months_positive']}/{s['months']}")


def freeze(tab: pd.DataFrame, names: list[str], symbols: list[str], path: Path = MODEL_PATH) -> str:
    """Train the forward models on the last 24 exploration months and save them; returns the file's SHA-256."""
    import sklearn
    from sklearn.ensemble import HistGradientBoostingClassifier
    end = pl.ms(pl.add_months(date(EXPLORE_TO.year, EXPLORE_TO.month, 1), 1))
    lo = pl.ms(pl.add_months(date(EXPLORE_TO.year, EXPLORE_TO.month, 1), -(pl.TRAIN_MONTHS - 1)))
    t = tab["t_close"].to_numpy()
    X = tab[names].to_numpy(dtype=np.float32)
    saved = {"h": H, "names": names, "symbols": symbols, "trained_from_ms": lo, "trained_to_ms": end,
             "sklearn": sklearn.__version__, "written": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "sides": {}}
    for side in ("long", "short"):
        cls, ret, dur = tab[f"c_{H}_{side}"].to_numpy(), tab[f"r_{H}_{side}"].to_numpy(), tab[f"d_{H}_{side}"].to_numpy()
        train = tab["train_row"].to_numpy() & (t >= lo) & (t + H * MIN < end) & (cls >= 0)
        model = HistGradientBoostingClassifier(categorical_features=[names.index("coin")], **pl.MODEL)
        model.fit(X[train], cls[train])
        saved["sides"][side] = {"model": model, "rows": int(train.sum()),
                                "mean_r": {int(k): float(np.mean(ret[train][cls[train] == k])) for k in model.classes_},
                                "mean_d": {int(k): float(np.mean(dur[train][cls[train] == k])) for k in model.classes_}}
    path.parent.mkdir(parents=True, exist_ok=True)
    body = pickle.dumps(saved, protocol=5)
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def run(symbols=cm.SYMBOLS, root=None, out: Path | None = None, model_path: Path = MODEL_PATH, log=print) -> dict:
    symbols = list(symbols)
    if "BTCUSDT" in symbols:
        symbols.remove("BTCUSDT")
        symbols.insert(0, "BTCUSDT")
    log("EXPLORATION AFTER THE FACT, NOT EVIDENCE: the crypto futures desk with limit orders and realistic fills, "
        f"H = {H}; rules and reading fixed on 27 Nov 2026, before this run")
    tab, _ = build(symbols, root, log)
    names = names_of(tab)
    preds = {}
    for side in ("long", "short"):
        p = pl.walk_forward(tab, names, H, side, log=log)
        preds[f"ev_{side}"], preds[f"hold_{side}"], preds[f"p_tp_{side}"] = p["ev"].to_numpy(), p["hold"].to_numpy(), p["p_tp"].to_numpy()
    rows = tab.assign(**preds)
    rows = rows[np.isfinite(rows["ev_long"]) & np.isfinite(rows["ev_short"])]
    n_months = len(cm.months(pl.TEST_FROM, EXPLORE_TO))
    ideal, _ = pl.book(rows, 2 * MAKER, H, n_months, desk=True)
    log(f"  ideal fills (step 44's what-if, this model): {ideal['trades']:,} trades, net {ideal['net_bps']:+.2f} bps a trade, "
        f"z {ideal['z']:+.2f}, months positive {ideal['months_positive']}/{ideal['months']}")
    log("  realistic fills:")
    coins = {}
    for i, name in enumerate(symbols):
        s, _ = desk(rows[rows["coin"] == i], n_months, one_pool=False)
        coins[name] = s
        log(line(name[:-4], s))
    d, trades = desk(rows, n_months)
    log(line("DESK", d))
    years = trades.groupby(trades["month"].str[:4])["net"].agg(["count", "sum"])
    log("  desk by year: " + ("; ".join(f"{y} {int(r['count'])} trades {r['sum'] * 100:+.1f}%" for y, r in years.iterrows()) or "no trades"))
    alive = d["trades"] >= MIN_TRADES and d["net_bps"] > 0 and d["months_positive"] * 3 >= d["months"] * 2
    result = {"written": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ideal": ideal, "desk": d,
              "coins": coins, "verdict": "ALIVE" if alive else "DEAD"}
    log(f"  by the reading fixed in advance: {result['verdict']}")
    if alive:
        sha = freeze(tab, names, symbols, model_path)
        result["model_sha256"] = sha
        log(f"  forward model frozen: {model_path} (SHA-256 {sha})")
    out = out or OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    log(f"saved: {out}")
    return result


if __name__ == "__main__":
    run()
    sys.exit(0)
