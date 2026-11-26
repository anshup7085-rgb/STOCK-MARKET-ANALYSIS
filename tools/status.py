#!/usr/bin/env python3
"""
The whole system in a few lines, to paste into the chat: what ran, when, and what it recorded. Read-only;
it prints counts, dates and paper results, never a token or a secret.

    PYTHONPATH=. .venv/bin/python tools/status.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("STATUS_ROOT") or Path(__file__).resolve().parents[1])     # the override is for tests


def running(pattern: str) -> bool:
    try:
        return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0
    except OSError:
        return False


def journal(sub: str, mode: str) -> list[dict]:
    from trading.journal import Journal
    p = ROOT / "state" / sub / "journal.jsonl"
    return Journal(p, mode).records() if p.exists() else []


def last_asof(recs: list[dict]) -> str:
    days = [r.get("asof") for r in recs if r.get("asof")]
    return max(days) if days else "never"


def lines() -> list[str]:
    out = [f"status at {datetime.now():%Y-%m-%d %H:%M} (server time)"]
    # India: the Kite runners and their paper books
    ex = journal("fno_explore", "fno-explore")
    exits = [r for r in ex if r["type"] == "XP_EXIT"]
    exp = [r for r in exits if str(r["payload"].get("id", "")).startswith("expiry_straddle")]
    out.append(f"India explorer: last active {last_asof(ex)}; {len(exits)} paper trades, net Rs{sum(r['payload']['net'] for r in exits):+,.0f};"
               f" expiry straddle {len(exp)} trade(s), net Rs{sum(r['payload']['net'] for r in exp):+,.0f}")
    sm = journal("fno_small", "fno-explore")
    sx = [r for r in sm if r["type"] == "XP_EXIT"]
    out.append(f"option-buying book: last active {last_asof(sm)}; {len(sx)} trades, net Rs{sum(r['payload']['net'] for r in sx):+,.0f}")
    out.append(f"Kite runners now: {'running' if running('fno explore run') else 'not running'}")
    mins = sorted(p.name[:4] for p in (ROOT / "state" / "index_minutes" / "NIFTY_50").glob("*.csv.gz"))
    out.append(f"NIFTY minutes on disk: {mins[0] + ' to ' + mins[-1] if mins else 'none yet (needs one `morning`)'}")
    # the brains
    for label, sub, mode, kind in (("NIFTY brain", "brain", "brain", "BRAIN"),
                                   ("crypto brain", "crypto/brain", "crypto-brain", "CRYPTO")):
        recs = journal(sub, mode)
        fc = [r["payload"] for r in recs if r["type"] == f"{kind}_FORECAST"]
        sc = [r["payload"] for r in recs if r["type"] == f"{kind}_SCORED" and not r["payload"].get("late")]
        score = "; ".join(f"{q.upper()} {sum((s['p'] > 0.5) == bool(s['y']) for s in sc if s['question'] == q)}/"
                          f"{sum(1 for s in sc if s['question'] == q)}" for q in ("up", "big"))
        out.append(f"{label}: last forecast after {max((f['after'] for f in fc), default='never')}; live score {score}")
    # crypto recorder
    live = sorted((ROOT / "state" / "crypto" / "live" / "BTCUSDT").glob("*.csv"))
    last_min = "none"
    if live:
        tail = live[-1].read_text().strip().splitlines()
        if len(tail) > 1:
            t = int(tail[-1].split(",")[0]) / 1000 + 60
            last_min = f"{datetime.fromtimestamp(t, timezone.utc):%Y-%m-%d %H:%M} UTC"
    out.append(f"crypto recorder: {'running' if running('run_crypto.py live') else 'NOT running'}; last BTC minute {last_min}")
    out.append(f"dashboard: {'serving' if running('http.server 8080') else 'not serving'}")
    try:
        out.append(f"free disk: {shutil.disk_usage(ROOT).free / 1e9:.0f} GB")
    except OSError:
        pass
    return out


def main() -> int:
    print("\n".join(lines()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
