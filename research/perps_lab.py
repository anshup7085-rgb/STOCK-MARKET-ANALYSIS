"""
EXPLORATION, NOT EVIDENCE: the crypto futures desk (Market Lab, crypto head). Can a tree model, ranking
short bracket trades on five perpetual futures by expected profit per minute of capital, earn after real
costs? Exploration years only (Jan 2021 to Oct 2024); the held-back years (Nov 2024 to Oct 2026) stay unread.

    PYTHONPATH=. .venv/bin/python run_crypto.py minutes        # Binance's files, about 450 MB
    PYTHONPATH=. .venv/bin/python research/perps_lab.py

The idea is the one in a post the holder showed on 27 Nov 2026 (a "self-ranking trading desk": a tree
model scoring each trade's chance of reaching its target before its stop, a ranker that gives capital to
the best expected return per unit of time, smoothed prices). That post showed 13.5 hours, 10-20x
leverage and numbers that disagree with each other. This runs the same idea on 34 months it never saw,
with costs, at 1x, by rules fixed before the data is read.

EVERYTHING BELOW WAS FIXED ON 27 NOV 2026, BEFORE ANY OF THIS DATA WAS READ (only three rows of two files
were looked at, to learn the file format).

Market    Binance USD-M perpetuals BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, one-minute candles and
          8-hourly funding, Jan 2021 to Oct 2024 (crypto/minutes.py). Prices on Indian exchanges' perpetuals
          track these closely; their own history is not public.
Trades    Every 5 minutes (candle closes at :00, :05, ...), for each coin and side, a bracket: entry at the
          close; target 1.5u, stop 1u, where u = the standard deviation of one-minute returns over the last
          240 minutes times sqrt(H); out at the close of minute H if neither is touched. A minute touching
          both counts as the stop. A stop gapped through fills at the minute's open; a target at its level.
Horizons  H = 15, 60 and 240 minutes: three variants, so the bar below is Bonferroni-adjusted for three.
Features  Only candles at or before the decision minute: returns over 1, 5, 15, 60, 240 and 1,440 minutes
          in units of u; volatility over 15, 60 and 1,440 minutes against 240; the volatility itself; where
          the price sits in its 60- and 1,440-minute range; distance from the 15-, 60- and 240-minute
          exponential averages (a Kalman filter with a fixed gain, the post's "smoothing", is exactly such an
          average); volume and trade count against their day's average; the taker-buy share over 5 and 60
          minutes; the last funding rate; hour and weekday (UTC); BTC's 5- and 60-minute returns; the coin.
Model     One pooled gradient-boosted tree classifier per horizon and side (scikit-learn's
          HistGradientBoostingClassifier: 200 rounds, rate 0.05, 31 leaves, at least 500 rows a leaf,
          L2 1.0, seed 0), three outcomes: stop, time out, target. Walk-forward: retrained at the start of
          every quarter from Jan 2022 on the 24 months before it (12 at first), training rows every 15
          minutes, rows whose outcome overlaps the quarter dropped.
Score     Expected return = sum over outcomes of p x (the training mean return of that outcome) - costs;
          the capital-turnover rank = expected return / expected minutes held.
Books     At 1x, no leverage. A coin book: one position at a time; when flat, take the side with the higher
          expected return if it is above 0. The desk: one pool for all five coins, one position at a
          time, the highest-ranked positive trade.
Costs     Taker 0.05% a side plus 18% GST = 0.059% (Indian exchanges' base rate: CoinDCX, Delta Exchange
          India; checked 27 Nov 2026 by search, not on their own pages), plus an assumed, unmeasured 0.02%
          of slippage a side: 0.158% a round trip. Funding as Binance charged it at each settlement a trade
          spans (paid by longs when positive). A what-if beside it, never part of the reading: maker fees
          (0.02% + GST a side, no slippage), which assumes every limit order fills, and they do not.

THE READING, FIXED IN ADVANCE. For each horizon, on the test months Jan 2022 to Oct 2024, at taker costs:
  WORTH PAPER    the desk's mean net return per trade is above 0 with z at least 2.94, at least 2/3 of
                 the 34 test months are net positive (a month with no trade is not positive), and at
                 least 3 of the 5 coin books are net positive.
  NO EDGE        anything else.
Beside it, a diagnostic that judges no money: THE MODEL RANKS if, over all test decisions, the realised
gross return of the top tenth by predicted return beats the bottom tenth with z at least 2.94. A model that
ranks but whose desk has NO EDGE has found something smaller than its costs.
What a WORTH PAPER would mean: the desk runs forward on paper on the live minutes, and a test on the
held-back years is registered before they are read. It would not mean money: this is exploration.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from crypto import minutes as cm
from crypto.store import EXPLORE_TO

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "state" / "crypto" / "perps_lab"
HORIZONS = (15, 60, 240)
TP_U, SL_U = 1.5, 1.0
SIGMA_WIN = 240
STEP, TRAIN_STEP = 5, 15
TRAIN_MONTHS, MIN_TRAIN_MONTHS = 24, 12
TEST_FROM = date(2022, 1, 1)
DATA_FROM = cm.FIRST
TAKER = 2 * (0.0005 * 1.18 + 0.0002)                  # 0.158% a round trip
MAKER = 2 * (0.0002 * 1.18)                           # what-if only
Z_NEEDED = 2.94
MIN_SIGMA = 1e-5
MIN = 60_000
SL, TO, TP = 0, 1, 2
MODEL = dict(max_iter=200, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=500,
             l2_regularization=1.0, early_stopping=False, random_state=0)


# ---------------------------------------------------------------- data on a full minute grid

def grid(c: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The candles on an unbroken minute grid (open times), NaN where Binance has no candle."""
    t = c["t"].astype(np.int64)
    t0, t1 = int(t[0]), int(t[-1])
    n = (t1 - t0) // MIN + 1
    pos = (t - t0) // MIN
    out = {"t": t0 + np.arange(n, dtype=np.int64) * MIN}
    for k in ("open", "high", "low", "close", "volume", "trades", "taker_buy"):
        a = np.full(n, np.nan)
        a[pos] = c[k]
        out[k] = a
    return out


def features(g: dict[str, np.ndarray], fund_t: np.ndarray, fund_r: np.ndarray) -> pd.DataFrame:
    """One row per minute of the grid; every value uses candles at or before that minute only."""
    close = pd.Series(g["close"])
    logc = np.log(close)
    r1 = logc.diff()
    sig = r1.rolling(SIGMA_WIN, min_periods=200).std()
    f = {}
    for k in (1, 5, 15, 60, 240, 1440):
        f[f"ret{k}"] = (logc - logc.shift(k)) / (sig * math.sqrt(k))
    for k in (15, 60, 1440):
        f[f"vol{k}"] = r1.rolling(k, min_periods=max(5, k // 2)).std() / sig
    f["sigma"] = np.log(sig)
    hi, lo = pd.Series(g["high"]), pd.Series(g["low"])
    for k in (60, 1440):
        top, bot = hi.rolling(k, min_periods=k // 2).max(), lo.rolling(k, min_periods=k // 2).min()
        f[f"place{k}"] = (close - bot) / (top - bot)
    filled = close.ffill()
    for k in (15, 60, 240):
        ema = filled.ewm(span=k, adjust=False).mean()
        f[f"ema{k}"] = (close / ema - 1) / (sig * math.sqrt(k))
    vol, trades, buy = pd.Series(g["volume"]), pd.Series(g["trades"]), pd.Series(g["taker_buy"])
    f["volume"] = np.log1p(vol.rolling(15, min_periods=10).mean() / vol.rolling(1440, min_periods=720).mean())
    f["trades"] = np.log1p(trades.rolling(15, min_periods=10).mean() / trades.rolling(1440, min_periods=720).mean())
    for k in (5, 60):
        f[f"buy{k}"] = buy.rolling(k, min_periods=1).sum() / vol.rolling(k, min_periods=1).sum() - 0.5
    t_close = g["t"] + MIN
    i = np.searchsorted(fund_t, t_close, side="right") - 1
    f["funding"] = np.where(i >= 0, fund_r[np.clip(i, 0, None)] * 1e4, np.nan) if len(fund_t) else np.nan
    f["hour"] = (t_close // 3_600_000) % 24
    f["weekday"] = ((t_close // 86_400_000) + 3) % 7
    out = pd.DataFrame(f).astype(np.float32)
    out["sig"] = sig.to_numpy()
    out["t_close"] = t_close
    return out


def brackets(g: dict[str, np.ndarray], sig: np.ndarray, idx: np.ndarray, h: int, side: int,
             chunk: int = 20_000) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For decisions at grid positions idx: outcome (SL, TO, TP; -1 where a candle is missing), return in
    units of u, minutes held. side +1 long, -1 short."""
    n = len(g["close"])
    cls = np.full(len(idx), -1, dtype=np.int8)
    ret = np.full(len(idx), np.nan, dtype=np.float32)
    dur = np.zeros(len(idx), dtype=np.int16)
    steps = np.arange(1, h + 1)
    for s in range(0, len(idx), chunk):
        d = idx[s:s + chunk]
        ok = d + h < n
        d = d[ok]
        where = np.nonzero(ok)[0] + s
        j = d[:, None] + steps[None, :]
        hi, lo, op = g["high"][j], g["low"][j], g["open"][j]
        entry = g["close"][d]
        u = sig[d] * math.sqrt(h)
        if side > 0:
            tp, sl = entry * (1 + TP_U * u), entry * (1 - SL_U * u)
            hit_tp, hit_sl = hi >= tp[:, None], lo <= sl[:, None]
        else:
            tp, sl = entry * (1 - TP_U * u), entry * (1 + SL_U * u)
            hit_tp, hit_sl = lo <= tp[:, None], hi >= sl[:, None]
        first_tp = np.where(hit_tp.any(1), hit_tp.argmax(1), h)
        first_sl = np.where(hit_sl.any(1), hit_sl.argmax(1), h)
        valid = ~np.isnan(hi).any(1) & ~np.isnan(lo).any(1) & ~np.isnan(g["close"][d + h]) & (u > 0)
        stop = (first_sl < h) & (first_sl <= first_tp)
        target = (first_tp < h) & ~stop
        k = np.where(stop, first_sl, np.where(target, first_tp, h - 1))
        rows = np.arange(len(d))
        gap = op[rows, k]
        if side > 0:
            sl_fill = np.minimum(sl, np.where(np.isnan(gap), sl, gap))
        else:
            sl_fill = np.maximum(sl, np.where(np.isnan(gap), sl, gap))
        exit_ = np.where(stop, sl_fill, np.where(target, tp, g["close"][d + h]))
        r = side * (exit_ / entry - 1) / u
        c = np.where(stop, SL, np.where(target, TP, TO)).astype(np.int8)
        cls[where] = np.where(valid, c, -1)
        ret[where] = np.where(valid, r, np.nan)
        dur[where] = k + 1
    return cls, ret, dur


def funding_paid(fund_t: np.ndarray, fund_r: np.ndarray, t_in: np.ndarray, t_out: np.ndarray,
                 side: np.ndarray) -> np.ndarray:
    """Funding as a fraction of notional, signed as a return: longs pay positive rates, shorts receive them."""
    if not len(fund_t):
        return np.zeros(len(t_in))
    cum = np.concatenate([[0.0], np.cumsum(fund_r)])
    total = cum[np.searchsorted(fund_t, t_out, side="right")] - cum[np.searchsorted(fund_t, t_in, side="right")]
    return -side * total


# ---------------------------------------------------------------- building the table

def coin_table(sym: int, name: str, root: Path | None, btc: pd.DataFrame | None, log=print) -> pd.DataFrame:
    c = cm.load(name, DATA_FROM, EXPLORE_TO, root=root)
    if not len(c["t"]):
        raise SystemExit(f"no minutes for {name}: run `run_crypto.py minutes` first")
    fund_t, fund_r = cm.load_funding(name, DATA_FROM, EXPLORE_TO, root=root)
    g = grid(c)
    f = features(g, fund_t, fund_r)
    minute = (g["t"] // MIN + 1)
    sig = f["sig"].to_numpy()
    idx = np.nonzero((minute % STEP == 0) & ~np.isnan(g["close"]) & (sig > MIN_SIGMA))[0]
    tab = f.iloc[idx].drop(columns=["sig"]).reset_index(drop=True)
    tab["u"] = (sig[idx]).astype(np.float64)
    tab["coin"] = np.float32(sym)
    tab["train_row"] = (minute[idx] % TRAIN_STEP == 0)
    if btc is not None:
        m = btc.set_index("t_close")[["ret5", "ret60"]]
        tab["btc5"] = m["ret5"].reindex(tab["t_close"]).to_numpy()
        tab["btc60"] = m["ret60"].reindex(tab["t_close"]).to_numpy()
    else:
        tab["btc5"], tab["btc60"] = tab["ret5"], tab["ret60"]
    for h in HORIZONS:
        for side, s in (("long", 1), ("short", -1)):
            cls, ret, dur = brackets(g, sig, idx, h, s)
            tab[f"c_{h}_{side}"], tab[f"r_{h}_{side}"], tab[f"d_{h}_{side}"] = cls, ret, dur
            t_in = tab["t_close"].to_numpy()
            fund = funding_paid(fund_t, fund_r, t_in, t_in + dur.astype(np.int64) * MIN, np.full(len(t_in), s))
            tab[f"f_{h}_{side}"] = fund.astype(np.float32)
    missing = 1 - np.isfinite(g["close"]).mean()
    log(f"  {name}: {len(tab):,} decision times, {pd.Timestamp(int(g['t'][0]), unit='ms'):%Y-%m-%d} to "
        f"{pd.Timestamp(int(g['t'][-1]), unit='ms'):%Y-%m-%d}, {missing:.3%} of minutes missing")
    return tab


def feature_names(tab: pd.DataFrame) -> list[str]:
    skip = ("t_close", "u", "train_row", "month")
    return [c for c in tab.columns if c not in skip and not c[:2] in ("c_", "r_", "d_", "f_")]


# ---------------------------------------------------------------- walk-forward

def quarters(first: date, last: date) -> list[date]:
    out, d = [], first
    while d <= last:
        out.append(d)
        d = date(d.year + (d.month + 2) // 12, (d.month + 2) % 12 + 1, 1)
    return out


def ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def add_months(d: date, k: int) -> date:
    y, m = divmod(d.month - 1 + k, 12)
    return date(d.year + y, m + 1, 1)


def walk_forward(tab: pd.DataFrame, names: list[str], h: int, side: str, log=print) -> pd.DataFrame:
    """Predicted gross return (fraction of notional) and expected minutes held, for every test row."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    cls, ret, dur = tab[f"c_{h}_{side}"].to_numpy(), tab[f"r_{h}_{side}"].to_numpy(), tab[f"d_{h}_{side}"].to_numpy()
    t = tab["t_close"].to_numpy()
    X = tab[names].to_numpy(dtype=np.float32)
    cat = [names.index("coin")]
    ev = np.full(len(tab), np.nan)
    hold = np.full(len(tab), np.nan)
    end = ms(add_months(date(EXPLORE_TO.year, EXPLORE_TO.month, 1), 1))
    for q in quarters(TEST_FROM, EXPLORE_TO):
        q0, q1 = ms(q), min(ms(add_months(q, 3)), end)
        lo = ms(max(add_months(q, -TRAIN_MONTHS), DATA_FROM))
        if (q.year - DATA_FROM.year) * 12 + q.month - DATA_FROM.month < MIN_TRAIN_MONTHS:
            continue
        train = tab["train_row"].to_numpy() & (t >= lo) & (t + h * MIN < q0) & (cls >= 0)
        test = (t >= q0) & (t < q1)
        model = HistGradientBoostingClassifier(categorical_features=cat, **MODEL)
        model.fit(X[train], cls[train])
        p = model.predict_proba(X[test])
        mean_r = {k: float(np.mean(ret[train][cls[train] == k])) for k in model.classes_}
        mean_d = {k: float(np.mean(dur[train][cls[train] == k])) for k in model.classes_}
        u = tab["u"].to_numpy()[test] * math.sqrt(h)
        ev[test] = sum(p[:, i] * mean_r[k] for i, k in enumerate(model.classes_)) * u
        hold[test] = sum(p[:, i] * mean_d[k] for i, k in enumerate(model.classes_))
        log(f"    H{h} {side:<5} {q:%Y-%m}: trained on {int(train.sum()):,} rows, tested {int(test.sum()):,}")
    return pd.DataFrame({"ev": ev, "hold": hold})


# ---------------------------------------------------------------- the books

def stats(net: np.ndarray, gross: np.ndarray, month: np.ndarray, n_months: int) -> dict:
    n = len(net)
    if n == 0:
        return {"trades": 0, "win": 0.0, "gross_bps": 0.0, "net_bps": 0.0, "total_pct": 0.0, "z": 0.0,
                "months_positive": 0, "months": n_months}
    sd = float(np.std(net, ddof=1)) if n > 1 else 0.0
    by_month = pd.Series(net).groupby(month).sum()
    return {"trades": n, "win": float(np.mean(net > 0)), "gross_bps": float(np.mean(gross) * 1e4),
            "net_bps": float(np.mean(net) * 1e4), "total_pct": float(np.sum(net) * 100),
            "z": float(np.mean(net) / sd * math.sqrt(n)) if sd > 0 else 0.0,
            "months_positive": int((by_month > 0).sum()), "months": n_months}


def book(rows: pd.DataFrame, cost: float, h: int, n_months: int, desk: bool) -> tuple[dict, pd.DataFrame]:
    """rows: test rows with ev_long, ev_short, hold_*, r_*, d_*, f_*, u, t_close, coin, month."""
    evl, evs = rows["ev_long"].to_numpy() - cost, rows["ev_short"].to_numpy() - cost
    long_ = evl >= evs
    best = np.where(long_, evl, evs)
    hold = np.where(long_, rows["hold_long"].to_numpy(), rows["hold_short"].to_numpy())
    cls = np.where(long_, rows[f"c_{h}_long"].to_numpy(), rows[f"c_{h}_short"].to_numpy())
    cand = rows.assign(best=best, rank=best / np.maximum(hold, 1.0), long=long_, cls=cls)
    cand = cand[(cand["best"] > 0) & (cand["cls"] >= 0)]
    if desk:
        cand = cand.sort_values(["t_close", "rank"], ascending=[True, False]).drop_duplicates("t_close")
    cand = cand.sort_values("t_close")
    t = cand["t_close"].to_numpy()
    d = np.where(cand["long"], cand[f"d_{h}_long"], cand[f"d_{h}_short"]).astype(np.int64)
    taken: list[int] = []
    free_at: dict = {}
    busy = 0
    coins = cand["coin"].to_numpy()
    for i in range(len(cand)):
        if desk:
            if t[i] >= busy:
                taken.append(i)
                busy = t[i] + d[i] * MIN
        else:
            if t[i] >= free_at.get(coins[i], 0):
                taken.append(i)
                free_at[coins[i]] = t[i] + d[i] * MIN
    tr = cand.iloc[taken]
    lg = tr["long"].to_numpy()
    r = np.where(lg, tr[f"r_{h}_long"], tr[f"r_{h}_short"])
    f = np.where(lg, tr[f"f_{h}_long"], tr[f"f_{h}_short"])
    gross = r * tr["u"].to_numpy() * math.sqrt(h)
    net = gross - cost + f
    out = tr.assign(gross=gross, net=net)
    return stats(net, gross, tr["month"].to_numpy(), n_months), out


def ranks(rows: pd.DataFrame, h: int) -> dict:
    """Top tenth against bottom tenth of predicted gross return, realised gross, both sides pooled."""
    ev = np.concatenate([rows["ev_long"], rows["ev_short"]])
    real = np.concatenate([rows[f"r_{h}_long"] * rows["u"] * math.sqrt(h), rows[f"r_{h}_short"] * rows["u"] * math.sqrt(h)])
    ok = np.isfinite(ev) & np.isfinite(real)
    ev, real = ev[ok], real[ok]
    lo_cut, hi_cut = np.quantile(ev, [0.1, 0.9])
    top, bot = real[ev >= hi_cut], real[ev <= lo_cut]
    se = math.sqrt(top.var(ddof=1) / len(top) + bot.var(ddof=1) / len(bot))
    deciles = pd.Series(real).groupby(pd.qcut(ev, 10, labels=False, duplicates="drop")).mean() * 1e4
    return {"top_bps": float(top.mean() * 1e4), "bottom_bps": float(bot.mean() * 1e4),
            "z": float((top.mean() - bot.mean()) / se) if se > 0 else 0.0,
            "deciles_bps": [round(float(x), 2) for x in deciles], "all_bps": float(real.mean() * 1e4)}


def line(name: str, s: dict) -> str:
    return (f"  {name:<8} {s['trades']:6,} trades, won {s['win']:5.1%}, gross {s['gross_bps']:+6.2f} bps, "
            f"net {s['net_bps']:+6.2f} bps a trade, total {s['total_pct']:+8.1f}%, z {s['z']:+5.2f}, "
            f"months positive {s['months_positive']}/{s['months']}")


def verdict(desk: dict, coins: dict[str, dict]) -> bool:
    return (desk["net_bps"] > 0 and desk["z"] >= Z_NEEDED and desk["months_positive"] * 3 >= desk["months"] * 2
            and sum(s["net_bps"] > 0 for s in coins.values()) >= 3)


# ---------------------------------------------------------------- main

def build(symbols, root: Path | None, log=print) -> pd.DataFrame:
    tabs, btc = [], None
    for i, name in enumerate(symbols):
        tab = coin_table(i, name, root, btc, log=log)
        if name == "BTCUSDT":
            btc = tab[["t_close", "ret5", "ret60"]]
        tabs.append(tab)
    tab = pd.concat(tabs, ignore_index=True)
    tab["month"] = pd.to_datetime(tab["t_close"], unit="ms").dt.strftime("%Y-%m")
    return tab


def run(symbols=cm.SYMBOLS, horizons=HORIZONS, root: Path | None = None, out: Path | None = None, log=print) -> dict:
    symbols = list(symbols)
    if "BTCUSDT" in symbols:                                       # BTC first: the others use its returns
        symbols.remove("BTCUSDT")
        symbols.insert(0, "BTCUSDT")
    log("EXPLORATION, NOT EVIDENCE: the crypto futures desk, Jan 2021 to Oct 2024; rules and reading fixed on "
        "27 Nov 2026, before this data was read")
    tab = build(symbols, root, log)
    names = feature_names(tab)
    test_months = [f"{m:%Y-%m}" for m in cm.months(TEST_FROM, EXPLORE_TO)]
    result = {"written": datetime.now(timezone.utc).isoformat(timespec="seconds"), "symbols": symbols,
              "test_months": len(test_months), "horizons": {}}
    for h in horizons:
        log(f"\nH = {h} minutes: target {TP_U}u, stop {SL_U}u, u = 240-minute volatility x sqrt({h})")
        preds = {}
        for side in ("long", "short"):
            p = walk_forward(tab, names, h, side, log=log)
            preds[f"ev_{side}"], preds[f"hold_{side}"] = p["ev"].to_numpy(), p["hold"].to_numpy()
        rows = tab.assign(**preds)
        rows = rows[np.isfinite(rows["ev_long"]) & np.isfinite(rows["ev_short"])]
        rk = ranks(rows, h)
        log(f"  an average bracket, gross: {rk['all_bps']:+.2f} bps; by predicted return, bottom tenth "
            f"{rk['bottom_bps']:+.2f} bps, top tenth {rk['top_bps']:+.2f} bps (z {rk['z']:+.2f}): "
            f"{'THE MODEL RANKS' if rk['z'] >= Z_NEEDED else 'the model does not rank clearly'}")
        log(f"  realised gross by tenth of predicted return, bps: {rk['deciles_bps']}")
        res = {"ranks": rk}
        for label, cost in (("taker", TAKER), ("maker what-if", MAKER)):
            log(f"  costs: {label} ({cost * 1e4:.1f} bps a round trip)")
            coins = {}
            for i, name in enumerate(symbols):
                s, _ = book(rows[rows["coin"] == i], cost, h, len(test_months), desk=False)
                coins[name] = s
                log(line(name[:-4], s))
            desk, trades = book(rows, cost, h, len(test_months), desk=True)
            log(line("DESK", desk))
            years = trades.groupby(trades["month"].str[:4])["net"].agg(["count", "sum"])
            log("  desk by year: " + ("; ".join(f"{y} {int(r['count'])} trades {r['sum'] * 100:+.1f}%" for y, r in years.iterrows()) or "no trades"))
            res[label] = {"coins": coins, "desk": desk}
        ok = verdict(res["taker"]["desk"], res["taker"]["coins"])
        res["verdict"] = "WORTH PAPER" if ok else "NO EDGE"
        log(f"  by the reading fixed in advance, H = {h}: {res['verdict']}"
            + ("" if ok else " (after taker costs)"))
        result["horizons"][str(h)] = res
    out = out or OUT / "result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    log(f"\nsaved: {out}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="the crypto futures desk, exploration years only")
    ap.add_argument("--symbols", nargs="*", default=list(cm.SYMBOLS))
    ap.add_argument("--horizons", nargs="*", type=int, default=list(HORIZONS))
    a = ap.parse_args()
    run(a.symbols, a.horizons)
    return 0


if __name__ == "__main__":
    sys.exit(main())
