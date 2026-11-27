#!/usr/bin/env python3
"""
The dashboard's web server: it serves state/dashboard (the pages) and takes one kind of input, the holder's
daily view for the human desk (brain/human.py). It listens only on the address it is given, which
deploy/dashboard.sh sets to this server's Tailscale address, so only the holder's own devices reach it.

    PYTHONPATH=. .venv/bin/python dashboard/serve.py 100.x.y.z 8080

    GET  /api/state   the session a view written now is about, its deadline, and the view that stands
    POST /api/view    {"up": 0-100, "big": 0-100, "note": "..."}: records a view (either chance may be blank)

It accepts nothing else: no other path is written to, bodies over 4 KB are refused, and every field is
checked (brain/human.py). It holds no secret and places no order.
"""

from __future__ import annotations

import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAGES = REPO / "state" / "dashboard"
MAX_BODY = 4096


class Handler(SimpleHTTPRequestHandler):
    human_root: Path | None = None
    cal = None

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                           # noqa: N802 - the library's name
        if self.path.split("?")[0] == "/api/state":
            from brain import human
            from datetime import datetime
            now = datetime.now(human.IST)
            return self._json(200, {"target": human.target(now, self.cal), "mine": human.latest(now, self.cal, self.human_root)})
        return super().do_GET()

    def do_POST(self):                                          # noqa: N802
        if self.path.split("?")[0] != "/api/view":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = -1
        if size <= 0 or size > MAX_BODY:
            return self._json(413 if size > MAX_BODY else 400, {"ok": False, "error": "body missing or too large"})
        try:
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"ok": False, "error": "not JSON"})
        from brain import human
        try:
            rec = human.submit(data.get("up"), data.get("big"), data.get("note", ""), cal=self.cal, root=self.human_root)
        except human.Invalid as exc:
            return self._json(400, {"ok": False, "error": str(exc)})
        return self._json(200, {"ok": True, "view": rec})

    def log_message(self, fmt, *args):                          # one short line per request, to logs/dashboard.log
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))


def make_server(host: str, port: int, pages: Path = PAGES, human_root: Path | None = None, cal=None) -> ThreadingHTTPServer:
    if cal is None:
        from market.calendar import load_calendar
        cal = load_calendar()
    handler = type("DeskHandler", (Handler,), {"human_root": human_root, "cal": cal})
    return ThreadingHTTPServer((host, port), partial(handler, directory=str(pages)))


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: dashboard/serve.py <address> [port]")
        return 2
    host, port = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 8080
    PAGES.mkdir(parents=True, exist_ok=True)
    srv = make_server(host, port)
    print(f"serving {PAGES} on http://{host}:{port}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
