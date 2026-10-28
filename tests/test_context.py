"""
The daily market-context recorder (trading/fno/context.py), on small synthetic fixtures that follow
the layouts NSE publishes. These check the parsing, the refusals and the record-keeping; they say
nothing about any market. Run: PYTHONPATH=. python tests/test_context.py
"""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path

from market.config import IST
from market.calendar import TradingCalendar
from trading.fno.context import (SOURCES, Unavailable, context_dir, parse_bhavcopy, parse_fii_dii, parse_index_close,
                                 parse_participant_oi, read_day, record_day, record_recent, summary, trading_days_back)

DAY = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 19, 0, tzinfo=IST)

PARTICIPANT = (b"Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Oct 28, 2026\n"
               b"Client Type,Future Index Long,Future Index Short,Option Index Call Long,Option Index Put Long \n"
               b"Client,1000,2000,10,20\nDII,300,900,0,5\nFII,5000,15000,7,8\nPro,100,200,1,2\nTOTAL,6400,18100,18,35\n")
INDEX = (b"Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,"
         b"Points Change,Change(%),Volume\n"
         b"Nifty 50,28-10-2026,22700,22800,22600,22750.5,50.5,0.22,1\n"
         b"India VIX,28-10-2026,13.1,13.6,12.9,13.25,0.15,1.14,0\n")
FIIDII = json.dumps([
    {"category": "DII **", "date": "28-Oct-2026", "buyValue": "9000.5", "sellValue": "7000", "netValue": "2000.5"},
    {"category": "FII/FPI *", "date": "28-Oct-2026", "buyValue": "6000", "sellValue": "8500", "netValue": "-2500"},
]).encode()


def _bhav(rows=None) -> bytes:
    head = "TckrSymb,FinInstrmTp,XpryDt,StrkPric,OptnTp,ClsPric,OpnIntrst,TtlTradgVol"
    rows = rows if rows is not None else [
        "NIFTY,IDO,2026-11-03,22700,CE,100,1000,50", "NIFTY,IDO,2026-11-03,22800,CE,60,3000,40",
        "NIFTY,IDO,2026-11-03,22700,PE,90,4000,80", "NIFTY,IDO,2026-11-03,22600,PE,50,2000,20",
        "NIFTY,IDO,2026-11-10,22700,CE,150,500,5", "NIFTY,IDO,2026-11-10,22700,PE,140,1500,15",
        "NIFTY,IDO,2026-10-27,22700,CE,1,9999,9", "BANKNIFTY,IDO,2026-11-03,54000,CE,1,77777,7",
        "NIFTY,IDF,2026-10-27,22750,,22750,1,1", "NIFTY,IDF,2026-11-24,22800,,22800,123456,1",
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bhav.csv", head + "\n" + "\n".join(rows) + "\n")
    return buf.getvalue()


def test_participant_oi_reads_every_client_type():
    oi = parse_participant_oi(PARTICIPANT, DAY)
    assert oi["FII"]["Future Index Long"] == 5000 and oi["FII"]["Future Index Short"] == 15000
    assert oi["DII"]["Option Index Put Long"] == 5                          # a trailing space in the header is stripped
    for bad in (b"nothing here\n", b"Client Type,Future Index Long\nClient,1\n"):
        try:
            parse_participant_oi(bad, DAY)
            raise AssertionError("accepted a file without FII and DII")
        except Unavailable:
            pass


def test_index_close_reads_vix_and_refuses_another_days_file():
    d = parse_index_close(INDEX, DAY)
    assert d["vix"] == {"close": 13.25, "change_pct": 1.14} and d["nifty"]["close"] == 22750.5
    try:
        parse_index_close(INDEX, date(2026, 10, 29))
        raise AssertionError("accepted another day's file")
    except Unavailable as exc:
        assert "dated" in str(exc)
    try:
        parse_index_close(INDEX.replace(b"India VIX", b"Other"), DAY)
        raise AssertionError("accepted a file without VIX")
    except Unavailable:
        pass


def test_bhavcopy_gives_put_call_ratios_and_biggest_strikes_for_nifty_only():
    b = parse_bhavcopy(_bhav(), DAY)
    assert b["nearest_expiry"] == "2026-11-03" and list(b["expiries"]) == ["2026-11-03", "2026-11-10"]   # expired and BANKNIFTY rows ignored
    near = b["expiries"]["2026-11-03"]
    assert near["call_oi"] == 4000 and near["put_oi"] == 6000 and near["pcr_oi"] == 1.5
    assert near["pcr_volume"] == round(100 / 90, 4)
    assert near["max_call_oi_strike"] == 22800 and near["max_put_oi_strike"] == 22700
    assert b["future"] == {"expiry": "2026-11-24", "close": 22800.0, "oi": 123456.0}      # the expired future is skipped
    try:
        parse_bhavcopy(_bhav([]), DAY)
        raise AssertionError("accepted a file with no options")
    except Unavailable:
        pass
    try:
        parse_bhavcopy(b"not a zip", DAY)
        raise AssertionError("accepted junk")
    except Unavailable:
        pass


def test_fii_dii_cash_is_filed_under_the_session_the_reply_names():
    session, d = parse_fii_dii(FIIDII)
    assert session == DAY and d["fii"]["net"] == -2500 and d["dii"]["net"] == 2000.5
    mixed = FIIDII.replace(b'"date": "28-Oct-2026", "buyValue": "6000"', b'"date": "27-Oct-2026", "buyValue": "6000"')
    for bad in (b"<html>blocked</html>", mixed, b"[]"):
        try:
            parse_fii_dii(bad)
            raise AssertionError(f"accepted {bad[:30]!r}")
        except Unavailable:
            pass


ARCHIVE = {"participant_oi": PARTICIPANT, "index_close": INDEX, "fo_bhavcopy": _bhav()}


def test_a_day_is_recorded_source_by_source_and_ok_sources_are_never_refetched():
    root = Path(tempfile.mkdtemp())
    calls = []
    files = dict(ARCHIVE)

    def fetch(name, day):
        calls.append(name)
        return files[name]

    rec = record_day(root, DAY, fetch, NOW, ("unavailable", "HTTPError: 403"))
    assert [rec["sources"][n]["status"] for n in SOURCES] == ["ok", "ok", "ok", "unavailable"]
    assert rec["sources"]["fii_dii_cash"]["reason"] == "HTTPError: 403"
    assert len(rec["sources"]["participant_oi"]["sha256"]) == 64
    raw = context_dir(root) / "raw" / DAY.isoformat()
    assert (raw / "participant_oi.csv").read_bytes() == PARTICIPANT and (raw / "index_close.csv").exists()
    assert not list(raw.glob("fo_bhavcopy*"))                               # NSE's archive keeps that one
    first = json.dumps(rec["sources"]["participant_oi"], sort_keys=True)
    calls.clear()
    files["participant_oi"] = b"changed"                                    # would fail to parse if it were refetched
    rec = record_day(root, DAY, fetch, NOW, ("ok", DAY, parse_fii_dii(FIIDII)[1], FIIDII))
    assert calls == [] and rec["sources"]["fii_dii_cash"]["status"] == "ok"
    assert (raw / "fii_dii_cash.json").read_bytes() == FIIDII
    assert json.dumps(rec["sources"]["participant_oi"], sort_keys=True) == first       # untouched
    assert read_day(root, DAY) == rec


def test_flows_for_another_session_are_never_filed_under_this_day():
    root = Path(tempfile.mkdtemp())
    other = date(2026, 10, 27)
    rec = record_day(root, DAY, lambda n, d: ARCHIVE[n], NOW, ("ok", other, {"fii": {}, "dii": {}}, b"x"))
    assert rec["sources"]["fii_dii_cash"]["status"] == "unavailable" and "2026-10-27" in rec["sources"]["fii_dii_cash"]["reason"]
    assert record_day(root, DAY, lambda n, d: ARCHIVE[n], NOW)["sources"]["fii_dii_cash"]["reason"] == "not fetched"


def test_a_run_fetches_the_flows_once_and_files_them_under_their_session():
    root = Path(tempfile.mkdtemp())
    cal = TradingCalendar(holidays={"2026-10-26": "a holiday"})
    calls = []

    def fetch(name, day):
        calls.append((name, day))
        if name == "fii_dii_cash":
            return FIIDII.replace(b"28-Oct-2026", b"27-Oct-2026")             # the morning after: yesterday's flows
        return ARCHIVE[name].replace(b"28-10-2026", day.strftime("%d-%m-%Y").encode())

    assert trading_days_back(date(2026, 10, 28), 3, cal) == [date(2026, 10, 23), date(2026, 10, 27), date(2026, 10, 28)]
    recs = record_recent(root, date(2026, 10, 28), 3, fetch, NOW, cal)
    assert [r["day"] for r in recs] == ["2026-10-23", "2026-10-27", "2026-10-28"]
    assert sum(1 for n, _ in calls if n == "fii_dii_cash") == 1
    got = {r["day"]: r["sources"]["fii_dii_cash"]["status"] for r in recs}
    assert got == {"2026-10-23": "unavailable", "2026-10-27": "ok", "2026-10-28": "unavailable"}
    calls.clear()
    record_recent(root, date(2026, 10, 28), 3, fetch, NOW, cal)
    assert ("fii_dii_cash", None) in calls and not any(n != "fii_dii_cash" for n, _ in calls)   # archive files done


def test_the_summary_reports_what_is_missing_and_never_fills_it_in():
    root = Path(tempfile.mkdtemp())
    rec = record_day(root, DAY, lambda n, d: (_ for _ in ()).throw(Unavailable("down")), NOW, ("unavailable", "down"))
    text = "\n".join(summary(rec))
    assert text.count("unavailable") == 4 and "VIX" not in text
    assert summary(None) == ["not recorded yet"]
    full = record_day(Path(tempfile.mkdtemp()), DAY, lambda n, d: ARCHIVE[n], NOW,
                      ("ok", DAY, parse_fii_dii(FIIDII)[1], FIIDII))
    s = "\n".join(summary(full))
    assert "long share 25%" in s and "India VIX 13.25" in s and "put-call OI 1.5" in s and "FII -2,500" in s


def _backfill(tmp):
    import importlib.util
    spec = importlib.util.spec_from_file_location("backfill_context", Path(__file__).resolve().parents[1] / "tools" / "backfill_context.py")
    bf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bf)
    bf.ROOT = Path(tmp)
    return bf


AFTER_HOURS = lambda: datetime(2026, 10, 28, 20, 0, tzinfo=IST)            # noqa: E731


def test_backfill_saves_files_lists_what_nse_lacks_and_resumes_without_refetching():
    bf = _backfill(tempfile.mkdtemp())
    calls = []

    def fetcher(path):
        calls.append(path)
        if "02012012" in path:
            raise bf.NotThere("HTTP 404")                               # a holiday
        if "ind_close" in path and "03012012" in path:
            return b"<html>error page</html>"                           # not the file: listed as missing
        return INDEX if "ind_close" in path else PARTICIPANT

    res = bf.run(date(2012, 1, 2), date(2012, 1, 4), False, fetcher, lambda s: None, AFTER_HOURS)
    assert not res["stopped"] and res["got"] == {"participant_oi": 2, "participant_vol": 2, "index_close": 1}
    missing = json.loads((bf.ROOT / "missing.json").read_text())
    assert missing["participant_oi"] == ["2012-01-02"] and missing["index_close"] == ["2012-01-02", "2012-01-03"]
    assert bf.file_path("participant_oi", date(2012, 1, 3)).read_bytes() == PARTICIPANT
    lines = (bf.ROOT / "manifest.jsonl").read_text().splitlines()
    assert len(lines) == 5 and all(len(json.loads(x)["sha256"]) == 64 for x in lines)
    calls.clear()
    bf.run(date(2012, 1, 2), date(2012, 1, 4), False, fetcher, lambda s: None, AFTER_HOURS)
    assert calls == []                                                  # saved and missing days are not asked again
    assert "participant_oi: 2 days, 2012-01-03 to 2012-01-04" in "\n".join(bf.summary())


def test_backfill_stops_after_five_refusals_in_a_row_and_keeps_out_of_vs_settlement_window():
    bf = _backfill(tempfile.mkdtemp())
    waits = []

    def refuse(path):
        raise bf.Refused("HTTP 403")

    res = bf.run(date(2012, 1, 2), date(2012, 1, 2), False, refuse, waits.append, AFTER_HOURS)
    assert res["stopped"] and waits.count(bf.PAUSE_S) == bf.MAX_REFUSALS - 1
    waits.clear()
    bf.quiet_hours(waits.append, lambda: datetime(2026, 11, 3, 16, 0, tzinfo=IST))
    assert waits == [5400.0]                                            # 16:00 -> 17:30
    waits.clear()
    bf.quiet_hours(waits.append, AFTER_HOURS)
    assert waits == []


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
