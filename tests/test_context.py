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
from trading.fno.context import (SOURCES, Unavailable, parse_bhavcopy, parse_fii_dii, parse_index_close,
                                 parse_participant_oi, read_day, record_day, summary)

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


def test_fii_dii_cash_is_refused_for_any_other_day():
    d = parse_fii_dii(FIIDII, DAY)
    assert d["fii"]["net"] == -2500 and d["dii"]["net"] == 2000.5
    try:
        parse_fii_dii(FIIDII, date(2026, 10, 27))
        raise AssertionError("served flows for a day the API no longer holds")
    except Unavailable as exc:
        assert "cannot be fetched later" in str(exc)
    try:
        parse_fii_dii(b"<html>blocked</html>", DAY)
        raise AssertionError("accepted a non-JSON page")
    except Unavailable:
        pass


def test_a_day_is_recorded_source_by_source_and_ok_sources_are_never_refetched():
    root = Path(tempfile.mkdtemp())
    calls = []
    files = {"participant_oi": PARTICIPANT, "index_close": INDEX, "fo_bhavcopy": _bhav()}

    def fetch(name, day):
        calls.append(name)
        if name == "fii_dii_cash":
            raise Unavailable("HTTPError: 403")
        return files[name]

    rec = record_day(root, DAY, fetch, NOW)
    assert [rec["sources"][n]["status"] for n in SOURCES] == ["ok", "ok", "ok", "unavailable"]
    assert rec["sources"]["fii_dii_cash"]["reason"] == "HTTPError: 403"
    assert len(rec["sources"]["participant_oi"]["sha256"]) == 64
    first = json.dumps(rec["sources"]["participant_oi"], sort_keys=True)
    calls.clear()
    files["participant_oi"] = b"changed"                                    # would fail to parse if it were refetched
    rec = record_day(root, DAY, lambda n, d: FIIDII if n == "fii_dii_cash" else fetch(n, d), NOW)
    assert calls == [] and rec["sources"]["fii_dii_cash"]["status"] == "ok"
    assert json.dumps(rec["sources"]["participant_oi"], sort_keys=True) == first       # untouched
    assert read_day(root, DAY) == rec


def test_the_summary_reports_what_is_missing_and_never_fills_it_in():
    root = Path(tempfile.mkdtemp())
    rec = record_day(root, DAY, lambda n, d: (_ for _ in ()).throw(Unavailable("down")), NOW)
    text = "\n".join(summary(rec))
    assert text.count("unavailable") == 4 and "VIX" not in text
    assert summary(None) == ["not recorded yet"]
    full = record_day(Path(tempfile.mkdtemp()), DAY,
                      lambda n, d: {"participant_oi": PARTICIPANT, "index_close": INDEX, "fo_bhavcopy": _bhav(),
                                    "fii_dii_cash": FIIDII}[n], NOW)
    s = "\n".join(summary(full))
    assert "long share 25%" in s and "India VIX 13.25" in s and "put-call OI 1.5" in s and "FII -2,500" in s


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
