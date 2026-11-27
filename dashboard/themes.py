"""
The themes section of the dashboard: which sectors and themes of the Indian market are rising or falling,
over 1, 3 and 12 months, against NIFTY 50, and whether each is above its 200-day average; and the same for
the five coins. Built from NSE's own daily all-index file (state/nse_archive/index_close and the daily
recorder's copy) and the crypto head's daily candles. Read-only.

It describes; it does not forecast. Rising sectors tending to keep rising for some months (industry
momentum) is a published finding in other markets; it has not been tested here, so nothing on this page is
a signal to trade.

It shows the recent past, which overlaps the held-back years (Nov 2024 to Oct 2026); no test is designed from
it.

Index names are NSE's, checked against its file of 26 Nov 2026 (169 indexes). An index NSE launched
recently has too short a history for the longer windows; its cell shows a dash.
"""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path

SESSIONS = {"1M": 21, "3M": 63, "12M": 252}
DAYS = {"1M": 30, "3M": 90, "12M": 365}
READ = 300                                      # sessions read: 12 months plus room
BROAD = (("Nifty 50", "nifty 50"), ("Next 50", "nifty next 50"), ("Midcap 100", "nifty midcap 100"),
         ("Smallcap 100", "nifty smallcap 100"), ("Microcap 250", "nifty microcap 250"))
SECTORS = (("Auto", "nifty auto"), ("Bank", "nifty bank"), ("Private bank", "nifty private bank"),
           ("PSU bank", "nifty psu bank"), ("Financial services", "nifty financial services"),
           ("NBFC", "nifty nbfc"), ("Capital markets", "nifty capital markets"), ("Insurance", "nifty insurance"),
           ("IT", "nifty it"), ("Pharma", "nifty pharma"), ("Healthcare", "nifty healthcare index"),
           ("Hospitals", "nifty hospitals"), ("FMCG", "nifty fmcg"), ("Consumer durables", "nifty consumer durables"),
           ("Retail", "nifty retail"), ("Metal", "nifty metal"), ("Chemicals", "nifty chemicals"),
           ("Cement", "nifty cement"), ("Realty", "nifty realty"), ("Construction", "nifty construction"),
           ("Capital goods", "nifty capital goods"), ("Energy", "nifty energy"), ("Oil & gas", "nifty oil & gas"),
           ("Power", "nifty power"), ("Telecom", "nifty telecommunications"), ("Media", "nifty media"))
THEMES = (("Defence", "nifty india defence"), ("Railways PSU", "nifty india railways psu"), ("CPSE", "nifty cpse"),
          ("PSE", "nifty pse"), ("Infrastructure", "nifty infrastructure"), ("Manufacturing", "nifty india manufacturing"),
          ("Digital", "nifty india digital"), ("Internet", "nifty india internet"),
          ("EV and new-age auto", "nifty ev & new age automotive"), ("Tourism", "nifty india tourism"),
          ("Housing", "nifty housing"), ("Rural", "nifty rural"), ("Consumption", "nifty india consumption"),
          ("New-age consumption", "nifty india new age consumption"), ("Mobility", "nifty mobility"),
          ("Transport and logistics", "nifty transportation & logistics"), ("Commodities", "nifty commodities"),
          ("MNC", "nifty mnc"), ("IPO", "nifty ipo"), ("AI catalysts", "nifty ai catalysts"),
          ("Sugar and ethanol", "nifty sugar & ethanol"), ("REITs and InvITs", "nifty reits & invits"))
COINS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")


def esc(x) -> str:
    return html.escape(str(x))


def load(root: Path, n: int = READ) -> tuple[list[date], dict[str, dict[date, float]]]:
    """The last n sessions of NSE's all-index file: (sessions, index name -> {session: close})."""
    from brain import data
    files = data._files("index_close", root / "state" / "nse_archive", root / "state" / "market_context" / "raw")
    days = list(files)[-n:]
    out: dict[str, dict[date, float]] = {}
    for d in days:
        for name, bar in data.parse_index_file(files[d].read_bytes()).items():
            out.setdefault(name, {})[d] = bar.close
    return days, out


def measure(series: dict[date, float], nifty: dict[date, float] | None) -> dict:
    days = sorted(series)
    closes = [series[d] for d in days]
    row: dict = {"close": closes[-1] if closes else None, "asof": days[-1] if days else None}
    for k, n in SESSIONS.items():
        if len(closes) > n:
            row[k] = closes[-1] / closes[-1 - n] - 1
            if nifty and days[-1] in nifty and days[-1 - n] in nifty:
                row[f"rel{k}"] = (1 + row[k]) / (nifty[days[-1]] / nifty[days[-1 - n]]) - 1
    if len(closes) >= 200:
        row["above200"] = closes[-1] > sum(closes[-200:]) / 200
    if len(closes) > SESSIONS["12M"]:
        row["from_high"] = closes[-1] / max(closes[-1 - SESSIONS["12M"]:]) - 1
    return row


def rows(root: Path) -> tuple[list[date], dict[str, list[dict]]]:
    days, ix = load(root)
    nifty = ix.get("nifty 50")
    out: dict[str, list[dict]] = {}
    for group, items in (("broad", BROAD), ("sectors", SECTORS), ("themes", THEMES)):
        out[group] = []
        for label, name in items:
            if name in ix and days and days[-1] in ix[name]:
                out[group].append({"label": label, "name": name, **measure(ix[name], nifty)})
    return days, out


def coin_rows(root: Path) -> list[dict]:
    from crypto import store
    out = []
    for sym in COINS:
        candles = store.load(sym, "1d", root / "state" / "crypto" / "klines")
        if not candles:
            continue
        closes = [c.close for c in candles]
        row: dict = {"label": sym[:-4], "close": closes[-1], "asof": store.utc_day(candles[-1])}
        for k, n in DAYS.items():
            if len(closes) > n:
                row[k] = closes[-1] / closes[-1 - n] - 1
        if len(closes) >= 200:
            row["above200"] = closes[-1] > sum(closes[-200:]) / 200
        if len(closes) > DAYS["12M"]:
            row["from_high"] = closes[-1] / max(closes[-1 - DAYS["12M"]:]) - 1
        out.append(row)
    return out


def pct(x) -> str:
    if x is None:
        return "<td class=num>—</td>"
    cls = "up" if x > 0 else "down" if x < 0 else ""
    return f"<td class='num {cls}'>{x:+.1%}</td>"


def arrow(x) -> str:
    if x is None:
        return ""
    return "<span class=up>▲</span>&nbsp;" if x else "<span class=down>▼</span>&nbsp;"


def table(items: list[dict], rel: bool = True) -> str:
    head = ("<tr><th>Name</th><th class=num>1M</th><th class=num>3M</th><th class=num>12M</th>"
            + ("<th class=num>vs N50</th>" if rel else "") + "<th class='num opt'>From high</th></tr>")
    body = "".join(f"<tr><td>{arrow(r.get('above200'))}{esc(r['label'])}</td>{pct(r.get('1M'))}{pct(r.get('3M'))}"
                   f"{pct(r.get('12M'))}" + (pct(r.get('rel3M')) if rel else "")
                   + pct(r.get('from_high')).replace("class='num", "class='num opt").replace("class=num>", "class='num opt'>")
                   + "</tr>" for r in items)
    return f"<div class=wide><table class=tight>{head}{body}</table></div>"


def by_strength(items: list[dict]) -> list[dict]:
    """Strongest first by 3 months against NIFTY 50; an index without a 3-month figure goes last."""
    return sorted(items, key=lambda r: (r.get("rel3M") is None, -(r.get("rel3M") or 0.0), r["label"]))


def section(root: Path) -> list[str]:
    days, g = rows(root)
    if not days:
        return ["<p class=muted>No NSE index file yet.</p>"]
    ranked = by_strength(g["sectors"] + g["themes"])
    scored = [r for r in ranked if r.get("rel3M") is not None]
    out = [f"<p class=muted>Close of {days[-1]:%A %d %b %Y}, NSE's own file (end of day). 1M, 3M and 12M are 21, 63 "
           "and 252 sessions; vs N50 is the last 3 months against NIFTY 50, strongest first; From high is the fall "
           "from the 12-month high (wide screens). ▲ above its 200-day average, ▼ below.</p>"]
    if len(scored) >= 6:
        lead = ", ".join(f"{esc(r['label'])} {r['rel3M']:+.0%}" for r in scored[:3])
        lag = ", ".join(f"{esc(r['label'])} {r['rel3M']:+.0%}" for r in scored[-3:][::-1])
        out.append(f"<p>Leading NIFTY 50 over 3 months: <b>{lead}</b>. Lagging: <b>{lag}</b>.</p>")
    trended = [r for r in g["sectors"] if r.get("above200") is not None]
    if trended:
        up = sum(r["above200"] for r in trended)
        out.append(f"<p>{up} of {len(trended)} sectors are above their 200-day average.</p>")
    out.append("<div class=keys><button class=heat-key data-key=rel3M>vs NIFTY, 3M</button><button class=heat-key data-key=1M>1M"
               "</button><button class=heat-key data-key=3M>3M</button><button class=heat-key data-key=12M>12M</button></div>"
               "<div id=heat></div>")
    out.append("<details><summary>The full tables</summary>"
               "<h3>The broad market</h3>" + table(g["broad"])
               + "<h3>Sectors</h3>" + table(by_strength(g["sectors"]))
               + "<h3>Themes</h3>" + table(by_strength(g["themes"])) + "</details>")
    coins = coin_rows(root)
    if coins:
        out.append(f"<h3>Crypto</h3><p class=muted>Daily closes (UTC) to {coins[0]['asof']:%d %b %Y}; 1M, 3M and 12M are "
                   "30, 90 and 365 days.</p>" + table(coins, rel=False))
    out.append("<p class=muted>A description of the past, not a forecast. That strong sectors tend to stay strong for "
               "some months is a published finding abroad; it is untested here, so this is not a signal to trade.</p>")
    return out
