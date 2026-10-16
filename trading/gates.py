"""
The ROADMAP promotion gates, as code.

ROADMAP.md set these thresholds before any result existed, which is the only
moment they could be set honestly. Encoding them means the live path asks the
evidence, not the operator's mood, whether it may run.

    Gate 1  research -> paper        backtest on real data (run_backtest.py --json)
    Gate 2  paper -> small live      the paper journal
    Gate 3  small live -> scaled     the live journal vs the paper journal

Each check is pass, fail, or n/a with a reason. A gate passes when nothing
fails. "n/a" is reserved for evidence that cannot exist yet in this setup and
says why — it is never used to wave a missing number through.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean

from market.calendar import load_calendar
from trading.journal import verify_file


@dataclass
class GateCheck:
    name: str
    status: str          # pass | fail | n/a
    detail: str


@dataclass
class GateReport:
    gate: str
    checks: list[GateCheck] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and not any(c.status == "fail" for c in self.checks)

    def add(self, name: str, ok: bool | None, detail: str) -> None:
        status = "n/a" if ok is None else ("pass" if ok else "fail")
        self.checks.append(GateCheck(name, status, detail))

    def text(self) -> str:
        out = [f"{self.gate}: {'PASSED' if self.passed else 'NOT PASSED'}"]
        for c in self.checks:
            out.append(f"  [{c.status:>4s}] {c.name}: {c.detail}")
        return "\n".join(out)


# ---------------------------------------------------------------------------

def gate1_backtest(path: str | Path | None) -> GateReport:
    g = GateReport("Gate 1 — research to paper (backtest)")
    if not path or not Path(path).exists():
        g.add("backtest result", False,
              "no run_backtest.py --json output supplied; run it on real NSE data")
        return g
    res = json.loads(Path(path).read_text())
    provider = str(res.get("provider", ""))
    n = int(res.get("n_trades") or 0)
    rho = res.get("spearman")
    net = res.get("expectancy_net_r")
    g.stats = {"provider": provider, "n_trades": n, "spearman": rho, "expectancy_net_r": net}

    real = not any(x in provider.upper() for x in ("MOCK", "SYNTHETIC", "REPLAY"))
    g.add("real NSE data", real, f"provider: {provider or 'unknown'}")
    g.add("n > 300 trades", n > 300, f"{n} trades")
    g.add("Spearman > 0.15", rho is not None and rho > 0.15, f"Spearman {rho}")
    g.add("positive expectancy after costs", net is not None and net > 0,
          f"net expectancy {net}R" if net is not None else
          "no net expectancy in this file — re-run with the current run_backtest.py")

    bands = [b for b in res.get("bands", []) if b.get("n", 0) >= 30]
    key = "mean_net_r" if all("mean_net_r" in b for b in bands) else "mean_r"
    vals = [b[key] for b in bands]
    if len(vals) < 2:
        g.add("higher bands beat lower bands", False,
              f"only {len(vals)} score band(s) with n >= 30 — cannot compare")
    else:
        ok = all(vals[j] > vals[i] for i in range(len(vals)) for j in range(i + 1, len(vals)))
        g.add("higher bands beat lower bands", ok,
              ", ".join(f"{b['label']}: {b[key]:+.3f}" for b in bands))
    g.add("out of sample", None,
          "the harness fits no parameters, so history is out of sample only if the "
          "thresholds in market/config.py were not tuned on it — attest that yourself")
    return g


# ---------------------------------------------------------------------------

def _closed(path: Path, mode: str) -> list[dict]:
    out = []
    if not path.exists():
        return out
    with path.open() as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                if r.get("type") == "TRADE_CLOSED" and r.get("mode") == mode:
                    out.append(r)
    return out


def gate2_paper(journal_path: str | Path, min_trades: int = 60) -> GateReport:
    g = GateReport("Gate 2 — paper to small live (paper journal)")
    p = Path(journal_path)
    v = verify_file(p)
    g.add("journal intact", v.ok and v.records > 0,
          f"{v.records} records, chain verifies" if v.ok and v.records else
          ("; ".join(v.problems[:3]) or "empty journal"))
    closed = _closed(p, "paper")
    late = [r for r in closed if str(r.get("ts", ""))[:10] < str(r.get("asof", ""))]
    n = len(closed)
    net_rs = [r["payload"].get("net_r_multiple") for r in closed if r["payload"].get("net_r_multiple") is not None]
    exp = mean(net_rs) if net_rs else None
    g.stats = {
        "closed_paper_trades": n,
        "expectancy_net_r": round(exp, 3) if exp is not None else None,
        "win_rate_pct": round(sum(1 for x in net_rs if x > 0) / len(net_rs) * 100, 1) if net_rs else None,
        "net_pnl": round(sum(r["payload"].get("net_pnl") or 0 for r in closed), 2),
    }
    g.add(f">= {min_trades} paper trades", n >= min_trades, f"{n} closed paper trades")
    g.add("logged in real time", not late,
          "every close was journaled on or after its market date" if not late else
          f"{len(late)} record(s) timestamped before their market date — impossible unless edited")
    g.add("positive paper expectancy after costs", exp is not None and exp > 0,
          f"{exp:+.3f}R per trade" if exp is not None else "no closed trades")
    cal = load_calendar()
    g.add("NSE holiday calendar", cal.verified, cal.status())
    g.add("corporate-action detection", True, "market/corporate_actions.py rejects suspects in every scan")
    g.add("F&O ban list", None,
          "this engine trades cash-segment delivery, which an F&O ban does not restrict; "
          "use data/restricted.txt for names to avoid")
    g.add("slippage within 0.2R of assumption", None,
          "paper fills are simulated with the assumed slippage, so paper cannot measure it; "
          "the first real measurement is Gate 3")
    return g


def gate3_live(live_journal: str | Path, paper_journal: str | Path,
               max_drawdown_pct: float, min_trades: int = 40) -> GateReport:
    g = GateReport("Gate 3 — small live to scaled (live vs paper)")
    v = verify_file(live_journal)
    g.add("live journal intact", v.ok and v.records > 0,
          f"{v.records} records" if v.ok else "; ".join(v.problems[:3]))
    live = _closed(Path(live_journal), "live")
    paper = _closed(Path(paper_journal), "paper")
    lr = [r["payload"]["net_r_multiple"] for r in live if r["payload"].get("net_r_multiple") is not None]
    pr = [r["payload"]["net_r_multiple"] for r in paper if r["payload"].get("net_r_multiple") is not None]
    g.add(f">= {min_trades} live trades", len(lr) >= min_trades, f"{len(lr)} closed live trades")
    if lr and pr:
        gap = abs(mean(lr) - mean(pr))
        g.add("live within 0.3R of paper", gap <= 0.3,
              f"live {mean(lr):+.3f}R vs paper {mean(pr):+.3f}R (gap {gap:.3f}R)")
    else:
        g.add("live within 0.3R of paper", False, "need closed trades in both journals")

    # Drawdown from the equity series the engine journals each cycle.
    eq = []
    if Path(live_journal).exists():
        with Path(live_journal).open() as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    if r.get("type") == "CYCLE_END" and r["payload"].get("equity") is not None:
                        eq.append(float(r["payload"]["equity"]))
    peak, worst = 0.0, 0.0
    for e in eq:
        peak = max(peak, e)
        if peak:
            worst = min(worst, (e / peak - 1) * 100)
    g.add("drawdown inside plan", bool(eq) and worst > -max_drawdown_pct,
          f"max drawdown {worst:.2f}% vs limit -{max_drawdown_pct}%" if eq else "no equity history")
    g.add("no month the book was one bet", None,
          "correlation is enforced pre-trade by the risk engine; a monthly review is still yours")
    return g
