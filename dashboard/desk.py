"""
The DESK page (state/dashboard/desk.html): the dashboard as a dense live trading terminal, in the look the
holder asked for on 27 Nov 2026 (a post's "Hyperliquid desk" screen). Every moving part is driven by real
data: live trades and prices from Binance's free public feed in the phone's browser (dashboard/desk.js),
and the project's own records embedded here at build time. Nothing is simulated, and the decorations move
only when real trades arrive. Paper only; read-only.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path

# What has been tested, ranked by the evidence (research/INTRADAY_LOG.md is the record; the step is cited).
SCOREBOARD = (
    ("NIFTY brain, BIG day", "NSE", "skill +4.55%, z 4.41", "SKILL", "forecasts only", 24),
    ("Expiry-day straddle", "NSE F&O", "mean R +0.09 to +0.34", "LEAD", "paper, Tuesdays", 33),
    ("Crypto ma50 / mom30, no tax", "Crypto daily", "beat holding 2018-24", "PAPER", "under VDA tax: no", 40),
    ("Crypto futures desk, maker", "Crypto 4h", "net -3.4 bps a trade", "DEAD", "honest fills", 46),
    ("Crypto futures desk (the post)", "Crypto 15m-4h", "net -9.2 to -14.4 bps", "DEAD", "costs 15.8 bps", 44),
    ("Weekly iron condor V-H", "NSE F&O", "won 69.5%, mean R -0.021", "FAILED", "2019-2026", 0),
    ("Opening-range breakout, future", "NSE F&O", "-Rs5.03 lakh, 753 trades", "DEAD", "fees", 12),
    ("Opening-range breakout, option", "NSE F&O", "mean R +0.002", "DEAD", "time decay", 12),
    ("Crypto brain", "Crypto daily", "no skill", "NONE", "", 0),
    ("Equity swing score", "NSE cash", "failed Gate 1", "FAILED", "17 Oct", 0),
)


def esc(x) -> str:
    return html.escape(str(x))


def _records(path: Path, mode: str) -> list[dict]:
    from trading.journal import Journal
    return Journal(path, mode).records() if path.exists() else []


def brain_now(root: Path) -> dict:
    fc = [r["payload"] for r in _records(root / "state" / "brain" / "journal.jsonl", "brain") if r["type"] == "BRAIN_FORECAST"]
    if not fc:
        return {}
    last = max(f["after"] for f in fc)
    return {"after": last, **{f["question"]: {"p": f["p"], "base": f["base"]} for f in fc if f["after"] == last}}


def paper_trades(root: Path, n: int = 6) -> list[dict]:
    out = []
    for r in _records(root / "state" / "crypto" / "paper" / "journal.jsonl", "crypto-paper"):
        if r["type"] != "CP_DAY":
            continue
        for key, a in r["payload"]["accounts"].items():
            if a.get("action"):
                rule, coin = key.split(" ")
                out.append({"t": r["payload"]["day"], "book": "CRYPTO", "what": f"{a['action']} {coin[:-4]}",
                            "note": f"{rule} · ${a.get('price', 0):,.2f}", "net": None})
    for sub, label in (("fno_explore", "EXPLORER"), ("fno_whatif", "WHAT-IF")):
        for r in _records(root / "state" / sub / "journal.jsonl", "fno-explore"):
            if r["type"] == "XP_EXIT":
                p = r["payload"]
                out.append({"t": r.get("asof", ""), "book": label, "what": f"CLOSE {p.get('id', '')}",
                            "note": f"R {p.get('R', 0):+.2f}", "net": p.get("net")})
    return sorted(out, key=lambda x: x["t"], reverse=True)[:n]


def paper_total(root: Path) -> dict:
    from crypto import paper
    rows = paper.summary(root / "state" / "crypto" / "paper")
    if not rows:
        return {}
    start = paper.CAPITAL * len(rows)
    free = sum(r["free"] for r in rows)
    return {"accounts": len(rows), "start": start, "now": free, "since": rows[0]["since"], "day": rows[0]["day"],
            "days": rows[0]["days"], "holding": sum(1 for r in rows if r["in"])}


def research_events(root: Path, n: int = 5) -> list[dict]:
    p = root / "research" / "INTRADAY_LOG.md"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        m = re.match(r"^\| (\d+) \| ([^|]+) \| \*\*(.+?)\*\*", line)
        if m:
            out.append({"step": int(m.group(1)), "t": m.group(2).strip(), "what": m.group(3).strip()})
    return out[-n:][::-1]


def data(root: Path) -> dict:
    from dashboard import build
    out = build.page_data(root)
    for key, fn in (("brain", brain_now), ("trades", paper_trades), ("paper_total", paper_total), ("research", research_events)):
        try:
            out[key] = fn(root)
        except Exception:                                       # noqa: BLE001 - a missing record must not break the page
            out[key] = [] if key in ("trades", "research") else {}
    try:
        from dashboard import themes
        out["rotation"] = themes.rotation(root)
    except Exception:                                           # noqa: BLE001
        out["rotation"] = []
    out["scoreboard"] = [dict(zip(("name", "market", "result", "verdict", "note", "step"), s)) for s in SCOREBOARD]
    return out


PANELS = """
<div class=panel id=p-wallet><h3>Paper desk <span class=tag>PAPER</span></h3><div id=wallet></div></div>
<div class=panel id=p-world><h3>World market clock <span class=r id=world-now></span></h3><canvas id=world height=210></canvas>
<div id=world-list class=legend></div></div>
<div class=panel id=p-flow><h3>Live order flow · <span id=flow-sym>BTC</span> <span class=r id=flow-share></span></h3>
<canvas id=flow height=150></canvas><div class=legend><span class=g>■ buyers</span> <span class=rd>■ sellers</span>
<span class=y>— price</span> · last 5 minutes, per second</div></div>
<div class=panel id=p-depth><h3>Order-book wall · <span id=depth-sym>BTC</span> <span class=r id=depth-imb></span></h3>
<canvas id=depth height=170></canvas><div class=legend>the 10 best bids (green) and asks (red), stacked; updated 10 times a second</div></div>
<div class=panel id=p-heat><h3>Trade arrival · 5-second buckets <span class=r id=heat-rate></span></h3><div id=arrivals></div>
<div class=legend>each cell: real trades in 5 seconds · B buys, S sells</div></div>
<div class=panel id=p-ring><h3>Order-flow ring <span class=r id=ring-sig></span></h3><canvas id=ring height=280></canvas>
<div class=legend>a strand lights for each real trade: green a buyer, red a seller</div></div>
<div class=panel id=p-spec><h3>Trade-size spectrum · <span id=spec-sym>BTC</span> <span class=r id=cvd-now></span></h3>
<canvas id=spectrum height=130></canvas><canvas id=cvd height=80></canvas>
<div class=legend>bars: the last 30 seconds' trades by size, buyers up, sellers down · line: cumulative buying minus selling since you opened the page</div></div>
<div class=panel id=p-liq><h3>Liquidation radar · futures <span class=r id=liq-sum></span></h3><canvas id=radar height=230></canvas>
<div class=legend id=liq-note>each blip a real forced close on Binance futures, any coin: <span class=rd>red</span> longs wiped out,
<span class=g>green</span> shorts; size by money, drifting out as it ages (5 minutes)</div></div>
<div class=panel id=p-fund><h3>Funding board · perpetual futures <span class=r id=fund-note></span></h3><div id=funding></div>
<div class=legend>what leveraged longs pay shorts (or the reverse) every 8 hours; yearly is that rate x 3 x 365</div></div>
<div class=panel id=p-corr><h3>Correlation · last 60 minutes</h3><div id=corr></div>
<div class=legend>of 1-minute returns; 1.00 moves in lockstep, 0 unrelated. High values mean five coins are one bet</div></div>
<div class=panel id=p-chart><h3><span id=chart-sym>BTC-USDT</span> · 1m <span class=r id=chart-last></span></h3>
<div id=desk-chart></div><div class=legend><span class=y>— Kalman level</span> · candles live from Binance</div></div>
<div class=panel id=p-kalman><h3>State-space estimator · Kalman on price <span class=r id=kgain></span></h3>
<canvas id=innov height=110></canvas><div class=legend>bars: innovations (price minus the filter's forecast)</div></div>
<div class=panel id=p-rot><h3>Sector rotation map · NSE <span class=r id=rot-asof></span></h3><canvas id=rot height=320></canvas>
<div class=legend>each sector's strength against NIFTY 50 (across) and its change (up), weekly, with an 8-week trail.
Our own construction (dashboard/themes.py); a description, not a signal</div></div>
<div class=panel id=p-brain><h3>NIFTY brain · next session</h3><div id=brainbox></div></div>
<div class=panel id=p-phase><h3>Market phase portrait · <span id=phase-sym>BTC</span></h3><canvas id=phase height=170></canvas>
<div class=legend>x: last minute's move, y: last 15 minutes' move, in units of volatility; trail: the last hour</div></div>
<div class=panel id=p-score><h3>Strategy scoreboard · ranked by evidence</h3><div id=score></div></div>
<div class=panel id=p-trades><h3>Paper trades</h3><div id=trades></div></div>
<div class=panel id=p-log><h3>Desk log <span class=r><span class=dot></span>live</span></h3><div id=log></div></div>
"""

CSS = """
:root{color-scheme:dark;--bg:#080404;--panel:#110707;--line:#2c1111;--ink:#ecdccf;--muted:#8d7368;--amber:#ff9a3c;
--orange:#ff6a3d;--g:#35d39a;--rd:#ff4d5e;--y:#ffc93c;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}html{background:#080404}body{margin:0;background:radial-gradient(ellipse at top,#1a0808 0,var(--bg) 60%) no-repeat;color:var(--ink);min-height:100vh;
font:11px/1.4 var(--mono)}
.top{display:flex;align-items:center;gap:10px;padding:10px 12px;border-bottom:1px solid var(--line);flex-wrap:wrap;
background:rgba(8,4,4,.9);position:sticky;top:0;z-index:5;backdrop-filter:blur(6px)}
.logo{width:34px;height:34px;border-radius:8px;background:linear-gradient(135deg,#ff7a3d,#b8321e);display:grid;place-items:center;
font-weight:800;color:#1a0503;box-shadow:0 0 18px rgba(255,106,61,.45)}
.title{margin-right:auto}.title b{font-size:15px;color:var(--amber);letter-spacing:.06em}.title div{color:var(--muted);font-size:9.5px}
.stat{text-align:right}.stat div{color:var(--muted);font-size:9px;letter-spacing:.08em}.stat b{font-size:13px;color:var(--y)}
#utc{font-size:14px;color:var(--ink);font-weight:700}
.feed{white-space:nowrap;overflow:hidden;border-bottom:1px solid var(--line);background:#0d0505}
.feed .tag{margin:0 8px}.feed span.run{display:inline-block;padding:5px 0;animation:run 60s linear infinite;color:#d9b8a6}
@keyframes run{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.coins{display:flex;gap:6px;overflow-x:auto;padding:8px 12px;scrollbar-width:none}.coins::-webkit-scrollbar{display:none}
.coin{flex:0 0 auto;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:6px 9px;color:var(--ink);
font:inherit;text-align:left;min-width:98px;transition:background .6s}.coin.on{border-color:var(--amber);box-shadow:0 0 10px rgba(255,154,60,.25)}
.coin b{color:var(--muted);font-size:10px;display:block}.coin .px{font-size:13px;font-weight:700}.coin .ch{font-size:10px}
.grid{display:grid;grid-template-columns:minmax(0,1fr);gap:8px;padding:4px 10px 24px}@media (min-width:700px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}.panel{min-width:0}
.panel{background:linear-gradient(180deg,#160909,var(--panel));border:1px solid var(--line);border-radius:10px;padding:9px 10px;
box-shadow:inset 0 0 30px rgba(255,80,40,.04)}
.panel h3{margin:0 0 7px;font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:#f2d9c6;display:flex;gap:6px;align-items:center}
.panel h3:before{content:"◆";color:var(--orange);font-size:8px}.panel h3 .r{margin-left:auto;color:var(--amber);letter-spacing:.02em}
canvas{width:100%;display:block}.legend{color:var(--muted);font-size:9.5px;margin-top:4px}
.g{color:var(--g)}.rd{color:var(--rd)}.y{color:var(--y)}.am{color:var(--amber)}.mu{color:var(--muted)}
.tag{font-size:9px;padding:1px 6px;border-radius:4px;background:#3a1010;color:#ff8f6a;border:1px solid #5a1a14;letter-spacing:.06em}
.tag.live{background:#0f2a1e;color:var(--g);border-color:#1d4f38}
.big{font-size:26px;font-weight:800;color:var(--g);text-shadow:0 0 16px rgba(53,211,154,.35)}.big.neg{color:var(--rd);text-shadow:0 0 16px rgba(255,77,94,.35)}
.kv{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin-top:6px}.kv div{color:var(--muted);font-size:9px}.kv b{display:block;color:var(--ink);font-size:13px}
.bar{height:5px;background:#2a1010;border-radius:3px;overflow:hidden;margin-top:6px}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--orange),var(--y))}
#arrivals{display:grid;grid-template-columns:44px repeat(24,1fr);gap:2px}#arrivals .lab{color:var(--muted);font-size:9px;line-height:10px}
#arrivals .c{height:10px;border-radius:2px;background:#1e0b0b;transition:background .4s}
table{width:100%;border-collapse:collapse}td,th{padding:4px 3px;border-bottom:1px solid #220d0d;text-align:left;font-size:10px}
th{color:var(--muted);font-weight:500;font-size:9px;letter-spacing:.06em}
.v{font-size:9px;padding:1px 5px;border-radius:3px;font-weight:700}.v.SKILL,.v.LEAD{background:#0f2a1e;color:var(--g)}
.v.PAPER{background:#2c2208;color:var(--y)}.v.DEAD,.v.FAILED{background:#2e0c0f;color:var(--rd)}.v.NONE{background:#1d1414;color:var(--muted)}
.tr{border:1px solid var(--line);border-left:3px solid var(--amber);border-radius:6px;padding:5px 7px;margin:0 0 5px;background:#0d0505}
.tr .t{color:var(--muted);font-size:9px}#log{max-height:220px;overflow:hidden}#log div{padding:2px 0;border-bottom:1px dashed #220d0d;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#log .ts{color:var(--muted);margin-right:6px}.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--g);margin-right:4px;animation:bl 1.2s infinite}
@keyframes bl{50%{opacity:.25}}.foot{color:var(--muted);font-size:9.5px;padding:0 12px 30px;text-align:center}
.flash-up{animation:fu .8s}.flash-down{animation:fd .8s}@keyframes fu{0%{background:rgba(53,211,154,.35)}}@keyframes fd{0%{background:rgba(255,77,94,.35)}}
a{color:var(--amber)}
#corr{display:grid;grid-template-columns:36px repeat(5,1fr);gap:3px}#corr div{text-align:center;padding:7px 0;border-radius:4px;font-size:10px}
#corr .lab{color:var(--muted);background:none;padding:7px 0}
#funding td.num,#funding th.num{text-align:right}.cd{color:var(--muted)}
#world-list span{display:inline-block;margin:2px 10px 2px 0}
"""


def build(root: Path) -> str:
    payload = json.dumps(data(root), separators=(",", ":")).replace("</", "<\\/")
    script = (Path(__file__).resolve().parent / "desk.js").read_text()
    coins = "".join(f"<button class=coin data-sym={s}><b>{s[:-4]}</b><span class=px>—</span> <span class=ch></span></button>"
                    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"))
    return ("<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<meta name=theme-color content='#080404'><title>Market Lab desk</title><style>{CSS}</style></head><body>"
            "<div class=top><div class=logo>ML</div><div class=title><b>MARKET LAB // DESK</b>"
            "<div>live order flow · paper books · ranked evidence</div></div>"
            "<div class=stat><div>BUYERS 60S</div><b id=s-share>—</b></div><div class=stat><div>TRADES/MIN</div><b id=s-rate>—</b></div>"
            "<div class=stat><div>NIFTY P(BIG)</div><b id=s-big>—</b></div><div class=stat><div id=s-status class='tag'>CONNECTING</div>"
            "<b id=utc></b></div></div>"
            "<div class=feed><span class=run id=feed></span></div>"
            f"<div class=coins>{coins}</div><div class=grid>{PANELS}</div>"
            f"<p class=foot>Every number is real: live from Binance's free public feed, or from this project's own records "
            f"(built {datetime.now():%d %b %Y %H:%M}). Paper only, nothing is traded. The decorations move only when real "
            "trades arrive. <a href=index.html>The full report ▸</a> · Charts: TradingView Lightweight Charts</p>"
            f"<script id=data type=application/json>{payload}</script><script src=lwc.js></script><script>{script}</script>"
            "</body></html>")
