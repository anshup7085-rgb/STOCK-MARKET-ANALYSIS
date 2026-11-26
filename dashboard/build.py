#!/usr/bin/env python3
"""
The dashboard: one read-only web page with everything the project knows today, built from the records
on the server. It changes nothing and holds no secret; it only reads.

    PYTHONPATH=. .venv/bin/python dashboard/build.py        # writes state/dashboard/index.html
    deploy/dashboard.sh                                       # serves it, privately, on the Tailscale network

Sections: the market today; the brain's forecast and its live score; the paper books: the explorer, the
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
        f"<td class='num {'up' if r['india'] > paper.CAPITAL else 'down' if r['india'] < paper.CAPITAL else ''}'>₹{r['india']:,.0f}</td></tr>"
        for r in sorted(rows, key=lambda r: (r["coin"], r["rule"] != "hold", r["rule"])))
    return [f"<p class=muted>Each account began with ₹{paper.CAPITAL:,.0f} on {esc(rows[0]['since'])}; last day "
            f"{esc(rows[0]['day'])} ({rows[0]['days']} days). Paper only.</p>",
            "<table><tr><th>Rule</th><th>Now</th><th>No tax</th><th>After India's tax</th></tr>" + body + "</table>",
            "<p class=muted>“hold” is the benchmark. ma50 and mom30 beat it on both coins with no tax in 2018–2024; "
            "under India's tax they did not (research log steps 37 and 40).</p>"]


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


CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e4e4df;--up:#1a7f37;--down:#c0392b;--accent:#2f5d8a}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--card:#1e1e1e;--ink:#ececea;--muted:#9a9a94;--line:#333;--up:#4cc76a;--down:#ff6b5b;--accent:#7fb0e0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{padding:20px 16px 8px;max-width:760px;margin:auto}h1{font-size:20px;margin:0}header p{margin:4px 0 0;color:var(--muted);font-size:13px}
main{max-width:760px;margin:auto;padding:0 16px 32px}section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
h2{font-size:15px;margin:0 0 8px;color:var(--accent)}table{width:100%;border-collapse:collapse}td,th{padding:6px 4px;border-bottom:1px solid var(--line);text-align:left;font-size:14px}
th{color:var(--muted);font-weight:500}.num{text-align:right;font-variant-numeric:tabular-nums}.up{color:var(--up)}.down{color:var(--down)}
.muted{color:var(--muted);font-size:13px}code{font-size:12px}ul.log{padding-left:18px;margin:0}ul.log li{margin:0 0 10px}
"""


def build(root: Path = ROOT) -> str:
    sections = [("The market", market), ("The brain: tomorrow, and its score", brain), ("Crypto", crypto),
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
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'><title>Market Lab</title>"
            f"<style>{CSS}</style></head><body><header><h1>Market Lab</h1>"
            f"<p>Built {datetime.now():%d %b %Y, %H:%M}. Read-only. Paper only: no real money is traded.</p></header>"
            f"<main>{''.join(body)}</main></body></html>")


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(build())
    os.replace(tmp, OUT)
    print(f"dashboard built: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
