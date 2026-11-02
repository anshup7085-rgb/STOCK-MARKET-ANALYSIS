#!/usr/bin/env python3
"""
Can the server read NSDL's daily FPI (foreign investor) flow report? NSE's own FII/DII API refuses
cloud servers (HTTP 403), so this checks the other public source before building on it.

    PYTHONPATH=. python tools/nsdl_probe.py

Prints, for each candidate page: the HTTP status, the size, and the first table's header cells and
first row, so the layout can be read off before a parser is written. Public data; no login.
"""

from __future__ import annotations

import re
import sys
import urllib.request

PAGES = ("https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx",
         "https://www.fpi.nsdl.co.in/web/Reports/Archive.aspx")
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,*/*"}


def cells(row: str) -> list[str]:
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
            for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, flags=re.S | re.I)]


def main() -> int:
    for url in PAGES:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as r:
                html = r.read().decode("utf-8", errors="replace")
                status = r.status
        except Exception as exc:                                # noqa: BLE001 - a probe: report anything
            print(f"{url}\n  failed: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I)
        print(f"{url}\n  HTTP {status}, {len(html):,} characters, {len(rows)} table rows")
        shown = 0
        for row in rows:
            c = [x for x in cells(row) if x]
            if len(c) >= 3:
                print("  | " + " | ".join(c[:10]))
                shown += 1
            if shown >= 4:
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
