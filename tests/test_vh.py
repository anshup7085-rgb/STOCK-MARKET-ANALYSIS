"""
V-H (research/PREREGISTRATION_4.md): the binding, the arithmetic, and the whole chain from a
fake NSE archive (both file layouts, holidays, a missing index file, a long-dated expiry)
through the downloader to both implementations, which must agree. SYNTHETIC prices only:
nothing here says anything about NIFTY. Run: PYTHONPATH=. python tests/test_vh.py
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import math
import random
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from market.config import IST
from research import vh_check
from trading.fno import vh

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vh_download", ROOT / "tools" / "vh_download.py")
dl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dl)
spec2 = importlib.util.spec_from_file_location("vh_run", ROOT / "tools" / "vh_run.py")
runner = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(runner)


def test_the_bound_registration_passes_and_a_changed_one_is_refused():
    assert vh.check_registration() == vh.REGISTRATION_SHA256
    changed = Path(tempfile.mkdtemp()) / "PREREGISTRATION_4.md"
    changed.write_bytes(vh.REGISTRATION.read_bytes().replace(b"h(x) = max(0.25, 0.01", b"h(x) = max(0.20, 0.01", 1))
    try:
        vh.check_registration(changed)
        raise AssertionError("a changed registration was accepted")
    except vh.RegistrationMismatch:
        pass
    assert "DRAFT" not in vh.REGISTRATION.read_text()


def test_fills_chain_and_eligible_sessions():
    assert vh.fill(84.40, "SELL", vh.penalty_registered) == 83.55        # 84.40 - 0.844 = 83.556, down
    assert vh.fill(43.65, "BUY", vh.penalty_registered) == 44.10         # 43.65 + 0.4365 = 44.0865, up
    assert vh.fill(10.00, "BUY", vh.penalty_registered) == 10.25         # the 0.25 floor
    assert vh.fill(10.00, "SELL", vh.penalty_none) == 10.00
    assert vh_check.tick(84.40, vh.penalty_registered(84.40), False) == 83.55
    exp = {date(2026, 10, 6), date(2026, 10, 13), date(2026, 10, 20), date(2031, 6, 24)}
    assert vh.weekly_chain(exp) == [date(2026, 10, 6), date(2026, 10, 13), date(2026, 10, 20)]
    sessions = [date(2026, 10, d) for d in (6, 7, 8, 9, 12, 13)]
    assert vh.eligible(date(2026, 10, 13), date(2026, 10, 6), sessions) == [date(2026, 10, 7), date(2026, 10, 8)]


def test_the_verdict_follows_the_registered_order():
    def cyc(r, net=100.0):
        c = vh.Cycle(date(2026, 1, 1), outcome="trade")
        c.R, c.net = r, net
        return c
    assert vh.verdict([cyc(0.6) for _ in range(40)])["outcome"] == "PASS"
    assert vh.verdict([cyc(0.6), cyc(-2.5)] + [cyc(0.6) for _ in range(40)])["why"].startswith("bound broken")
    assert vh.verdict([cyc(0.01) for _ in range(30)])["outcome"] == "FAIL"
    assert vh.betting_path([0.3, -1.0, 0.4]) == vh_check.bets([0.3, -1.0, 0.4])


# -- a fake NSE archive ------------------------------------------------------------------------

HOLIDAYS = {date(2023, 12, 25), date(2024, 1, 26), date(2024, 3, 28)}       # 28 Mar 2024 is a Thursday
START, END = date(2023, 11, 1), date(2024, 4, 30)
LEGACY_HEAD = ("INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,"
               "VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,").split(",")
UDIFF_HEAD = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,"
              "StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,"
              "SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,"
              "Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4").split(",")


def _sessions():
    d, out = START, []
    while d <= END:
        if d.weekday() < 5 and d not in HOLIDAYS:
            out.append(d)
        d += timedelta(days=1)
    return out


def _expiries(sessions):
    out, d = [], START
    while d <= END + timedelta(days=40):
        if d.weekday() == 3:
            e = d
            while e in HOLIDAYS:
                e -= timedelta(days=1)                                   # a holiday moves expiry a day earlier
            out.append(e)
        d += timedelta(days=1)
    return out + [date(2028, 12, 28)]                                    # long-dated: not in the chain


def _price(s, k, right, days, vol=0.13):
    t = max(days, 0.5) / 365.0
    sd = vol * math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * sd * sd) / sd
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))                # noqa: E731
    call = s * n(d1) - k * n(d1 - sd)
    v = call if right == "CE" else call - s + k
    return max(0.05, round(round(v / 0.05) * 0.05, 2))


def _world():
    rng = random.Random(7)
    sessions = _sessions()
    spot, path = 21000.0, {}
    for d in sessions:
        spot *= math.exp(rng.gauss(0.0002, 0.008))
        path[d] = round(spot, 2)
    return sessions, path, _expiries(sessions)


def _file(d, path, expiries):
    s = path[d]
    live = [e for e in expiries if e >= d][:6]
    rows = []
    for e in live:
        for k in range(int(s // 50) * 50 - 2500, int(s // 50) * 50 + 2550, 50):
            for right in ("CE", "PE"):
                calm = e.year == 2023 and e.month == 12          # a calm month: credits fall below the floor
                px = _price(s, k, right, (e - d).days, 0.04 if calm else 0.13)
                vol = 0 if abs(k - s) > 1500 else 120
                if d < date(2024, 1, 1):
                    rows.append(["OPTIDX", "NIFTY", e.strftime("%d-%b-%Y").upper(), f"{k:.2f}", right, px, px, px,
                                 px, px, vol, 1, 1, 0, d.strftime("%d-%b-%Y").upper(), ""])
                else:
                    rows.append([d.isoformat(), d.isoformat(), "FO", "NSE", "IDO", "1", "", "NIFTY", "", e.isoformat(),
                                 e.isoformat(), f"{k:.2f}", right, "", px, px, px, px, px, px, s, px, 1, 0, vol * 75,
                                 1, 1, "F1", 75, "", "", "", "", ""])
    noise = (["OPTIDX", "BANKNIFTY", live[0].strftime("%d-%b-%Y").upper(), "45000.00", "CE", 1, 1, 1, 9999, 1, 5, 1,
              1, 0, d.strftime("%d-%b-%Y").upper(), ""] if d < date(2024, 1, 1) else
             [d.isoformat(), d.isoformat(), "FO", "NSE", "IDO", "1", "", "BANKNIFTY", "", live[0].isoformat(),
              live[0].isoformat(), "45000.00", "CE", "", 1, 1, 1, 9999, 1, 1, 45000, 1, 1, 0, 5, 1, 1, "F1", 15,
              "", "", "", "", ""])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(LEGACY_HEAD if d < date(2024, 1, 1) else UDIFF_HEAD)
    w.writerows([noise] + rows)
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("bhav.csv", buf.getvalue())
    return z.getvalue()


def _archive(tmp: Path):
    sessions, path, expiries = _world()
    index_dir = tmp / "index_close"
    for d in sessions:
        if d in (date(2024, 2, 7), date(2023, 11, 22)):                  # no index file: UDiFF falls back, legacy cannot
            continue
        p = index_dir / str(d.year) / f"ind_close_all_{d:%d%m%Y}.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value\n"
                     f"Nifty Bank,{d:%d-%m-%Y},1,1,1,45000\nNifty 50,{d:%d-%m-%Y},1,1,1,{path[d]}\n")
    by_day = {d: _file(d, path, expiries) for d in sessions if d != date(2024, 2, 15)}   # NSE lacks one expiry day

    def fetcher(url):
        for d, blob in by_day.items():
            if (f"{d:%Y%m%d}" in url and d >= date(2024, 1, 1) and "BhavCopy" in url) or \
               (f"fo{d:%d}{d.strftime('%b').upper()}{d:%Y}bhav" in url and d < date(2024, 1, 1)):
                return blob
        raise dl.NotThere("HTTP 404")
    return sessions, path, index_dir, fetcher


def test_the_whole_chain_on_a_fake_archive_and_both_implementations_agree():
    tmp = Path(tempfile.mkdtemp())
    sessions, path, index_dir, fetcher = _archive(tmp)
    res = dl.run(fetcher, lambda s: None, lambda: datetime(2026, 10, 31, 20, 0, tzinfo=IST), tmp / "vh")
    assert not res["stopped"] and res["got"] == len(sessions) - 1
    assert (tmp / "vh" / "download.complete").exists()
    raw = tmp / "vh" / "raw"
    kept = (raw / "2024" / "2024-02-01.csv.gz")
    assert b"BANKNIFTY" not in __import__("gzip").decompress(kept.read_bytes())       # only NIFTY rows kept
    man = [json.loads(x) for x in (tmp / "vh" / "manifest.jsonl").read_text().splitlines()]
    assert {m["layout"] for m in man} == {"legacy", "udiff"} and all(len(m["zip_sha256"]) == 64 for m in man)
    calls = []
    dl.run(lambda u: calls.append(u) or fetcher(u), lambda s: None, lambda: datetime(2026, 10, 31, 20, 0, tzinfo=IST),
           tmp / "vh")
    assert calls == []                                                   # resumable: nothing fetched twice

    loaded = vh.load_sessions(raw)
    a = vh.run(loaded, vh.penalty_registered, index_dir)
    b = vh_check.compute(raw, index_dir, vh.penalty_registered)
    assert runner.compare(a, b) == []
    assert vh.verdict(a)["outcome"] == vh_check.outcome(b)
    trades = [c for c in a if c.outcome == "trade"]
    assert len(trades) >= 10, len(trades)
    assert all(c.expiry != date(2028, 12, 28) for c in a)
    assert any(c.expiry == date(2024, 3, 27) for c in a)                 # the holiday-moved expiry is a cycle
    outcomes = {c.expiry: c.outcome for c in a}
    assert outcomes[date(2024, 2, 15)] == "left out"                     # no NSE file on E
    assert all(outcomes[e] == "no trade" for e in outcomes if e.year == 2023 and e.month == 12)
    assert "credit below the floor" in vh.describe(a)["no_trade_reasons"]
    for c in trades:
        t = c.trade
        assert 20 <= t["credit"] <= 80 and t["max_loss"] <= 5000 and t["lots"] >= 1
        assert c.R == c.net / t["max_loss"]
        sc, sp = t["legs"][0]["strike"], t["legs"][1]["strike"]
        if sp - 100 <= c.settlement["value"] <= sc + 100:
            assert c.R >= -1.0 - 1e-9                                    # inside the wings, never worse than -1R
        assert c.entry < c.expiry and c.entry.weekday() < 5
    # the session with no index file and no fallback is skipped; the UDiFF one falls back on UndrlygPric
    assert vh.nifty_close(date(2023, 11, 22), loaded.rows(date(2023, 11, 22)), index_dir) is None
    assert vh.nifty_close(date(2024, 2, 7), loaded.rows(date(2024, 2, 7)), index_dir) == path[date(2024, 2, 7)]
    d = vh.describe(a)
    assert d["items"] == len(trades) and sum(p["items"] for p in d["periods"].values()) == len(trades)


def test_a_disagreement_records_the_failing_outcome():
    tmp = Path(tempfile.mkdtemp())
    sessions, path, index_dir, fetcher = _archive(tmp)
    dl.run(fetcher, lambda s: None, lambda: datetime(2026, 10, 31, 20, 0, tzinfo=IST), tmp / "vh")
    raw = tmp / "vh" / "raw"
    a = vh.run(vh.load_sessions(raw), vh.penalty_registered, index_dir)
    b = vh_check.compute(raw, index_dir, vh.penalty_registered)
    t = next(r for r in b if r["outcome"] == "trade")
    t["net"] += 5.0
    assert runner.compare(a, b) and "net A" in runner.compare(a, b)[0]
    t["net"] -= 5.0
    t["M"] += 5.0
    assert "maximum loss A" in runner.compare(a, b)[0]


if __name__ == "__main__":
    import sys
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
