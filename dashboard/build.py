#!/usr/bin/env python3
"""
The dashboard: one read-only web page with everything the project knows today, built from the records
on the server. It changes nothing and holds no secret; it only reads.

    PYTHONPATH=. .venv/bin/python dashboard/build.py        # writes state/dashboard/index.html
    deploy/dashboard.sh                                       # serves it, privately, on the Tailscale network

Sections: the market today; the themes (dashboard/themes.py: sectors and themes over 1, 3 and 12 months);
the brain's forecast and its live score; the paper books: the explorer, the
what-if book and V; the latest research findings; the health of the data. A section whose record does not
exist yet says so. research/BLUEPRINT.md says what will be added (crypto, news, the human desk).
"""

from __future__ import annotations

import html
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(os.environ.get("DASH_ROOT") or Path(__file__).resolve().parents[1])     # the override is for tests
OUT = ROOT / "state" / "dashboard" / "index.html"


def esc(x) -> str:
    return html.escape(str(x))


def rupees(x: float) -> str:
    return ("−" if x < 0 else "+" if x > 0 else "") + f"₹{abs(x):,.0f}"


def records(path: Path, mode: str) -> list[dict]:
    from trading.journal import Journal
    return Journal(path, mode).records() if path.exists() else []


def market(root: Path) -> list[str]:
    from brain import data
    archive, daily = root / "state" / "nse_archive", root / "state" / "market_context" / "raw"
    files = data._files("index_close", archive, daily)
    if not files:
        return ["<p class=muted>No NSE index file yet.</p>"]
    days = list(files)
    last = data.parse_index_file(files[days[-1]].read_bytes())
    prev = data.parse_index_file(files[days[-2]].read_bytes()) if len(days) > 1 else {}
    rows = []
    for name, label in (("nifty 50", "NIFTY 50"), ("nifty bank", "NIFTY BANK"), ("india vix", "India VIX"),
                        ("nifty it", "NIFTY IT"), ("nifty midcap 100", "NIFTY MIDCAP 100")):
        if name in last:
            c = last[name].close
            ch = (c / prev[name].close - 1) if name in prev else None
            cls = "" if ch is None else ("up" if ch > 0 else "down")
            rows.append(f"<tr><td>{label}</td><td class=num>{c:,.2f}</td>"
                        f"<td class='num {cls}'>{'' if ch is None else f'{ch:+.2%}'}</td></tr>")
    fii = data._files("participant_oi", archive, daily)
    extra = ""
    if fii:
        share = data.load_fii_long_share(archive, daily)
        if share:
            d = max(share)
            extra = f"<p>FII long share in index futures: <b>{share[d]:.0%}</b> ({d:%d %b})</p>"
    return [f"<p class=muted>Close of {days[-1]:%A %d %b %Y}, NSE's own file</p>",
            "<table><tr><th>Index</th><th>Close</th><th>Day</th></tr>" + "".join(rows) + "</table>", extra]


def brain(root: Path) -> list[str]:
    recs = records(root / "state" / "brain" / "journal.jsonl", "brain")
    if not recs:
        return ["<p class=muted>No forecast yet: run <code>run_brain.py today --fetch</code> after the close.</p>"]
    fc = [r["payload"] for r in recs if r["type"] == "BRAIN_FORECAST"]
    sc = [r["payload"] for r in recs if r["type"] == "BRAIN_SCORED" and not r["payload"].get("late")]
    out = []
    if fc:
        last = max(f["after"] for f in fc)
        now = {f["question"]: f for f in fc if f["after"] == last}
        words = {"up": "NIFTY closes higher", "big": "A bigger move than usual, either way"}
        out.append(f"<p class=muted>For the session after {esc(last)}</p><table><tr><th>Question</th><th>Brain</th>"
                   "<th>Usually</th></tr>" + "".join(
                       f"<tr><td>{words.get(q, q)}</td><td class=num><b>{f['p']:.0%}</b></td>"
                       f"<td class=num>{f['base']:.0%}</td></tr>" for q, f in sorted(now.items(), key=lambda kv: kv[0] != "up")) + "</table>")
    rows = []
    for q in ("up", "big"):
        mine = [s for s in sc if s["question"] == q]
        if mine:
            right = sum((s["p"] > 0.5) == bool(s["y"]) for s in mine)
            rows.append(f"<tr><td>{q.upper()}</td><td class=num>{len(mine)}</td><td class=num>{right}</td>"
                        f"<td class=num>{right / len(mine):.0%}</td></tr>")
    out.append("<p><b>Live score</b> (forecasts written before their day)</p>" + (
        "<table><tr><th></th><th>Scored</th><th>Right</th><th>Share</th></tr>" + "".join(rows) + "</table>"
        if rows else "<p class=muted>Nothing scored yet.</p>"))
    out.append("<p class=muted>Its 2016–2024 record: skill on BIG (right 58.5% where 48.8% were big), none on UP. "
               "A live score needs months before it means anything.</p>")
    return out


def crypto(root: Path) -> list[str]:
    from crypto import store
    kroot, live = root / "state" / "crypto" / "klines", root / "state" / "crypto" / "live"
    rows = []
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"):
        years = sorted((kroot / sym / "1d").glob("*.csv.gz"))
        if not years:
            continue
        daily = store._read(years[-1])
        if not daily:
            continue
        last_close = daily[max(daily)].close
        now, when = last_close, "day close"
        files = sorted((live / sym).glob("*.csv"))
        if files:
            tail = files[-1].read_text().strip().splitlines()
            if len(tail) > 1:
                parts = tail[-1].split(",")
                now = float(parts[4])
                when = datetime.fromtimestamp(int(parts[0]) / 1000 + 60).strftime("%H:%M")
        ch = now / last_close - 1
        cls = "up" if ch > 0 else "down" if ch < 0 else ""
        rows.append(f"<tr><td>{sym[:-4]}</td><td class=num>${now:,.2f}</td><td class='num {cls}'>{ch:+.2%}</td>"
                    f"<td class=muted>{esc(when)}</td></tr>")
    if not rows:
        return ["<p class=muted>Not started: <code>deploy/crypto.sh</code> starts the crypto head.</p>"]
    out = ["<table><tr><th>Coin</th><th>Price</th><th>Since 05:30</th><th>As of</th></tr>" + "".join(rows) + "</table>"]
    recs = records(root / "state" / "crypto" / "brain" / "journal.jsonl", "crypto-brain")
    fc = [r["payload"] for r in recs if r["type"] == "CRYPTO_FORECAST"]
    if fc:
        last = max(f["after"] for f in fc)
        now_f = {f["question"]: f for f in fc if f["after"] == last}
        sc = [r["payload"] for r in recs if r["type"] == "CRYPTO_SCORED" and not r["payload"].get("late")]
        words = {"up": "BTC closes higher", "big": "A bigger move than usual"}
        out.append(f"<p class=muted>The crypto brain, for the UTC day after {esc(last)}</p><table><tr><th>Question</th>"
                   "<th>Brain</th><th>Usually</th></tr>" + "".join(
                       f"<tr><td>{words.get(q, q)}</td><td class=num><b>{f['p']:.0%}</b></td>"
                       f"<td class=num>{f['base']:.0%}</td></tr>"
                       for q, f in sorted(now_f.items(), key=lambda kv: kv[0] != "up")) + "</table>")
        for q in ("up", "big"):
            mine = [d for d in sc if d["question"] == q]
            if mine:
                right = sum((d["p"] > 0.5) == bool(d["y"]) for d in mine)
                out.append(f"<p class=muted>Live score {q.upper()}: {right} right of {len(mine)}</p>")
    return out


def crypto_paper(root: Path) -> list[str]:
    from crypto import paper
    rows = paper.summary(root / "state" / "crypto" / "paper")
    if not rows:
        return ["<p class=muted>Not started yet: it begins by itself at the next 05:36 IST run.</p>"]
    body = "".join(
        f"<tr><td>{esc(r['rule'])} {esc(r['coin'])}</td><td>{'holding' if r['in'] else 'cash'}</td>"
        f"<td class='num {'up' if r['free'] > paper.CAPITAL else 'down' if r['free'] < paper.CAPITAL else ''}'>₹{r['free']:,.0f}</td>"
        f"<td class='num {'up' if r['india'] > paper.CAPITAL else 'down' if r['india'] < paper.CAPITAL else ''}'>₹{r['india']:,.0f}</td>"
        + (f"<td class='num {'up' if r['spec'] > paper.CAPITAL else 'down' if r['spec'] < paper.CAPITAL else ''}'>₹{r['spec']:,.0f}</td>"
           if r.get("spec") is not None else "<td class=muted>–</td>") + "</tr>"
        for r in sorted(rows, key=lambda r: (r["coin"], r["rule"] != "hold", r["rule"])))
    return ["<div class=chart-box><div class=chart-title>Each account's worth, no-tax ledger, ₹</div>"
            "<div id=paper-chart class=chart></div><div id=paper-legend></div></div>",
            f"<p class=muted>Each account began with ₹{paper.CAPITAL:,.0f} on {esc(rows[0]['since'])}; last day "
            f"{esc(rows[0]['day'])} ({rows[0]['days']} days). Paper only.</p>",
            "<table><tr><th>Rule</th><th>Now</th><th>No tax</th><th>VDA tax</th><th>As business</th></tr>" + body + "</table>",
            "<p class=muted>“hold” is the benchmark. ma50 and mom30 beat it on both coins with no tax in 2018–2024; "
            "under India's VDA tax they did not (research log steps 37 and 40). “As business” is the unsettled reading "
            "that crypto futures are speculative business income, at slab rates (step 42).</p>"]


def book(path: Path, title_capital: float | None = None) -> tuple[float, float, int, float]:
    recs = records(path, "fno-explore")
    init = next((r for r in recs if r["type"] == "XP_INIT"), None)
    cap = float((init or {}).get("payload", {}).get("capital") or title_capital or 0)
    nets = [r["payload"]["net"] for r in recs if r["type"] == "XP_EXIT"]
    today = date.today().isoformat()
    t = sum(r["payload"]["net"] for r in recs if r["type"] == "XP_EXIT" and r.get("asof") == today)
    return cap, sum(nets), len(nets), t


def books(root: Path) -> list[str]:
    rows = []
    for label, sub in (("Option-buying book", "fno_small"), ("Explorer (₹5 lakh)", "fno_explore"),
                       ("What-if (every signal)", "fno_whatif")):
        p = root / "state" / sub / "journal.jsonl"
        if not p.exists():
            rows.append(f"<tr><td>{label}</td><td colspan=4 class=muted>not started</td></tr>")
            continue
        cap, total, n, today = book(p)
        cls = "up" if total > 0 else "down" if total < 0 else ""
        rows.append(f"<tr><td>{label}</td><td class=num>{n}</td><td class='num {cls}'>{rupees(total)}</td>"
                    f"<td class=num>{rupees(today)}</td><td class=num>{'' if not cap else f'₹{cap + total:,.0f}'}</td></tr>")
    v = records(root / "state" / "paper_fno" / "journal.jsonl", "fno-paper")
    settled = [r["payload"]["net"] for r in v if r["type"] == "FNO_SETTLED"]
    if v:
        rows.append(f"<tr><td>Strategy V (weekly condor)</td><td class=num>{len(settled)}</td>"
                    f"<td class=num>{rupees(sum(settled))}</td><td></td><td></td></tr>")
    return ["<table><tr><th>Paper book</th><th>Trades</th><th>Total</th><th>Today</th><th>Worth now</th></tr>"
            + "".join(rows) + "</table>",
            "<p class=muted>Paper money only. Exploration, not evidence: nothing here has passed a test.</p>"]


def findings(root: Path, n: int = 6) -> list[str]:
    p = root / "research" / "INTRADAY_LOG.md"
    if not p.exists():
        return ["<p class=muted>No research log.</p>"]
    rows = []
    for line in p.read_text().splitlines():
        m = re.match(r"^\| (\d+) \| ([^|]+) \| \*\*(.+?)\*\*", line)
        if m:
            rows.append((int(m.group(1)), m.group(2).strip(), m.group(3).strip()))
    items = "".join(f"<li><span class=muted>{esc(d)} · step {k}</span><br>{esc(t)}</li>" for k, d, t in rows[-n:][::-1])
    return [f"<ul class=log>{items}</ul>"]


def health(root: Path) -> list[str]:
    from brain import data
    archive, daily = root / "state" / "nse_archive", root / "state" / "market_context" / "raw"
    rows = []

    def last_of(label, days):
        rows.append(f"<tr><td>{label}</td><td>{max(days):%d %b %Y}</td></tr>" if days else
                    f"<tr><td>{label}</td><td class=muted>none</td></tr>")

    last_of("NSE index files", list(data._files("index_close", archive, daily)))
    last_of("FII positions", list(data._files("participant_oi", archive, daily)))
    mins = root / "state" / "index_minutes" / "NIFTY_50"
    years = sorted(p.name[:4] for p in mins.glob("*.csv.gz"))
    rows.append(f"<tr><td>NIFTY minutes (Kite)</td><td>{years[0] + '–' + years[-1] if years else '<span class=muted>not yet</span>'}</td></tr>")
    dhan = root / "state" / "dhan" / "rolling" / "code1" / "ATM_CALL"
    dd = sorted(p.name[:10] for p in dhan.glob("*.json.gz"))
    rows.append(f"<tr><td>Option minutes (Dhan)</td><td>{dd[0] + ' to ' + dd[-1] if dd else '<span class=muted>none</span>'}</td></tr>")
    try:
        free = shutil.disk_usage(root).free / 1e9
        rows.append(f"<tr><td>Free disk</td><td>{free:.0f} GB</td></tr>")
    except OSError:
        pass
    return ["<table>" + "".join(rows) + "</table>"]


def live(root: Path) -> list[str]:
    ticks = "".join(f"<button class=tick data-sym={sym}><b>{sym[:-4]}</b><span class=px>—</span><span class=ch></span></button>"
                    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"))
    return [f"<div class=ticker>{ticks}</div>",
            "<div class=chart-box><div class=chart-title>BTC / USDT · 1-minute candles</div><div id=live-chart class=chart></div></div>",
            "<p id=live-note class=muted>Real prices from Binance's free public feed, straight to this phone, tick by tick. "
            "Tap a coin for its chart. Prices only: nothing is traded.</p>"]


def nifty(root: Path) -> list[str]:
    return (["<div class=chart-box><div class=chart-title>NIFTY 50 · daily candles, last 6 months</div>"
             "<div id=nifty-chart class=chart></div></div>"] + market(root)
            + ["<p class=muted>Indian prices here are end of day, from NSE's own file. Live Indian prices need the "
               "day's Kite login.</p>"])


def nifty_bars(root: Path, n: int = 130) -> list[dict]:
    from brain import data
    files = data._files("index_close", root / "state" / "nse_archive", root / "state" / "market_context" / "raw")
    out = []
    for d in list(files)[-n:]:
        b = data.parse_index_file(files[d].read_bytes()).get("nifty 50")
        if b:
            c = b.close
            out.append({"time": d.isoformat(), "open": b.open or c, "high": max(b.high or c, c), "low": min(b.low or c, c),
                        "close": c})
    return out


def paper_series(root: Path) -> dict[str, list[dict]]:
    """Each crypto paper account's worth (no-tax ledger) at each day's close."""
    recs = records(root / "state" / "crypto" / "paper" / "journal.jsonl", "crypto-paper")
    out: dict[str, list[dict]] = {}
    for r in recs:
        if r["type"] != "CP_DAY":
            continue
        for key, a in r["payload"]["accounts"].items():
            rule, coin = key.split(" ")
            out.setdefault(f"{rule} {coin[:-4]}", []).append({"time": r["payload"]["day"], "value": round(a["free_value"], 2)})
    return out


def crypto_last(root: Path) -> list[dict]:
    """The server's last recorded minute per coin, and the last daily close as its reference."""
    from crypto import store
    out = []
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"):
        years = sorted((root / "state" / "crypto" / "klines" / sym / "1d").glob("*.csv.gz"))
        daily = store._read(years[-1]) if years else {}
        if not daily:
            continue
        ref = daily[max(daily)].close
        price = ref
        files = sorted((root / "state" / "crypto" / "live" / sym).glob("*.csv"))
        if files:
            tail = files[-1].read_text().strip().splitlines()
            if len(tail) > 1:
                price = float(tail[-1].split(",")[4])
        out.append({"sym": sym, "price": price, "open": ref})
    return out


def page_data(root: Path) -> dict:
    from dashboard import themes
    out: dict = {"built": datetime.now().isoformat(timespec="seconds")}
    for key, fn in (("nifty", nifty_bars), ("paper", paper_series), ("crypto_last", crypto_last)):
        try:
            out[key] = fn(root)
        except Exception:                                       # noqa: BLE001 - a missing chart must not break the page
            out[key] = [] if key != "paper" else {}
    try:
        _, g = themes.rows(root)
        out["themes"] = [{k: r.get(k) for k in ("label", "1M", "3M", "12M", "rel3M")} for r in g["sectors"] + g["themes"]]
    except Exception:                                           # noqa: BLE001
        out["themes"] = []
    return out


CSS = """
:root{color-scheme:dark;--bg:#070a10;--card:#0e131c;--card2:#121926;--ink:#e8edf5;--muted:#7f8a9c;--line:#1c2433;
--up:#16c784;--down:#ea3943;--accent:#4da3ff;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{position:sticky;top:0;z-index:5;background:rgba(7,10,16,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.bar{max-width:820px;margin:auto;padding:10px 14px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
h1{font-size:15px;letter-spacing:.12em;margin:0 auto 0 0;font-weight:700}h1 span{color:var(--accent)}
#clock{font:600 13px var(--mono);color:var(--ink)}
.chip{font:600 11px var(--mono);padding:3px 8px;border-radius:999px;border:1px solid var(--line);color:var(--muted);letter-spacing:.04em}
.chip.live{color:var(--up);border-color:rgba(22,199,132,.45)}.chip.live:before{content:"";display:inline-block;width:7px;height:7px;
border-radius:50%;background:var(--up);margin-right:6px;animation:pulse 1.4s infinite}
.chip.wait{color:#f5a623;border-color:rgba(245,166,35,.4)}.chip.off{color:var(--down);border-color:rgba(234,57,67,.45)}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(22,199,132,.7)}70%{box-shadow:0 0 0 7px rgba(22,199,132,0)}100%{box-shadow:0 0 0 0 rgba(22,199,132,0)}}
main{max-width:820px;margin:auto;padding:4px 12px 40px}
section{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:12px 0}
h2{font-size:12px;margin:0 0 10px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;font-weight:600}
h3{font-size:13px;margin:14px 0 4px;color:var(--ink)}
table{width:100%;border-collapse:collapse}td,th{padding:6px 4px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}
th{color:var(--muted);font-weight:500;font-size:12px}.num{text-align:right;font:13px var(--mono);font-variant-numeric:tabular-nums}
.up{color:var(--up)}.down{color:var(--down)}.muted{color:var(--muted);font-size:12px}code{font-size:12px}
ul.log{padding-left:18px;margin:0}ul.log li{margin:0 0 10px}
.wide{overflow-x:auto}table.tight td,table.tight th{font-size:12px;padding:5px 2px;white-space:nowrap}
table.tight td:first-child{white-space:normal}@media (max-width:520px){.opt{display:none}}
.ticker{display:flex;gap:8px;overflow-x:auto;padding-bottom:4px;scrollbar-width:none}.ticker::-webkit-scrollbar{display:none}
.tick{flex:0 0 auto;min-width:112px;text-align:left;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:8px 10px;
color:var(--ink);display:flex;flex-direction:column;gap:2px;font:inherit;cursor:pointer;transition:background .6s}
.tick.on{border-color:var(--accent)}.tick b{font-size:12px;color:var(--muted)}.tick .px{font:600 15px var(--mono)}.tick .ch{font:12px var(--mono)}
.flash-up{animation:fu .8s}.flash-down{animation:fd .8s}
@keyframes fu{0%{background:rgba(22,199,132,.35)}100%{background:var(--bg)}}@keyframes fd{0%{background:rgba(234,57,67,.35)}100%{background:var(--bg)}}
.chart-box{margin:10px 0 6px;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:8px 6px 4px}
.chart-title{font:600 11px var(--mono);color:var(--muted);padding:0 6px 6px;letter-spacing:.03em}.chart{width:100%}
.keys{display:flex;gap:6px;margin:4px 0 8px}.heat-key{background:var(--bg);border:1px solid var(--line);color:var(--muted);
border-radius:999px;padding:4px 10px;font:600 11px var(--mono)}.heat-key.on{color:var(--ink);border-color:var(--accent)}
#heat{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:4px}
.tile{border-radius:6px;padding:7px 6px;min-height:52px;display:flex;flex-direction:column;justify-content:space-between}
.tile b{font-size:11px;font-weight:600;line-height:1.2;color:#fff}.tile span{font:600 12px var(--mono);color:#fff}
details{margin-top:10px}summary{cursor:pointer;color:var(--accent);font-size:13px}
#paper-legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:11px;color:var(--muted);padding:4px 6px}
#paper-legend i{display:inline-block;width:10px;height:3px;margin-right:5px;vertical-align:middle;border-radius:2px}
"""


def build(root: Path = ROOT) -> str:
    import json
    from dashboard import themes
    sections = [("Live crypto", live), ("NIFTY 50 and the market", nifty), ("Themes: which sectors are moving", themes.section),
                ("The brain: tomorrow, and its score", brain), ("Crypto: the server's record and the crypto brain", crypto),
                ("Crypto paper book", crypto_paper),
                ("Paper books", books),
                ("Latest research findings", findings), ("Data health", health)]
    body = []
    for title, fn in sections:
        try:
            inner = "".join(fn(root))
        except Exception as exc:                                # noqa: BLE001 - one broken section must not hide the rest
            inner = f"<p class=muted>Could not build this section: {esc(type(exc).__name__)}: {esc(str(exc)[:120])}</p>"
        body.append(f"<section><h2>{esc(title)}</h2>{inner}</section>")
    data = json.dumps(page_data(root), separators=(",", ":")).replace("</", "<\\/")
    script = (Path(__file__).resolve().parent / "live.js").read_text()
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'><meta name=theme-color content='#070a10'>"
            f"<title>Market Lab</title><style>{CSS}</style></head><body><header><div class=bar>"
            "<h1>MARKET<span>LAB</span></h1><span id=live-status class='chip wait'>connecting</span>"
            "<span class=chip>PAPER ONLY</span><span id=clock></span></div></header>"
            f"<main>{''.join(body)}<p class=muted>Built {datetime.now():%d %b %Y, %H:%M} from the server's records. "
            "Read-only: no real money is traded. Charts by TradingView Lightweight Charts.</p></main>"
            f"<script id=data type=application/json>{data}</script><script src=lwc.js></script><script>{script}</script>"
            "</body></html>")


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(build())
    os.replace(tmp, OUT)
    lib = Path(__file__).resolve().parent / "vendor" / "lightweight-charts-4.2.0.js"
    if lib.exists():
        shutil.copyfile(lib, OUT.parent / "lwc.js")
    print(f"dashboard built: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
