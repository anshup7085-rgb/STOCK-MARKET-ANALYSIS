#!/usr/bin/env python3
"""
Daily Kite access token — the manual 2FA step, made less fiddly.

    python tools/kite_session.py
    eval "$(python tools/kite_session.py --eval)"     # sets the token in this shell, prints nothing secret

1. Prints Zerodha's login URL. You open it, log in on Zerodha's own page with
   your own password and 2FA, and copy `request_token` from the redirect URL.
2. You paste the request token, then your API secret at a hidden prompt.
3. It prints an `export KITE_ACCESS_TOKEN=...` line for you to run. With
   --eval, the export lines go only to the shell's `eval`, so the token is
   never shown on screen and there is nothing to copy (or to paste anywhere
   else by mistake).

What it never does: log in for you, read a TOTP seed, store your API secret,
or write anything to disk. The secret lives in this process's memory for one
call and is gone when it exits. SEBI's retail-algo framework expects a human
2FA login each trading day; this keeps that human in the loop.
"""

from __future__ import annotations

import getpass
import os
import shlex
import sys


def _ask(prompt: str) -> str:
    """A prompt on stderr, so `eval "$(...)"` captures only the export lines."""
    print(prompt, end="", file=sys.stderr, flush=True)
    return input().strip()


def main() -> int:
    as_eval = "--eval" in sys.argv[1:]
    out = sys.stderr if as_eval else sys.stdout
    api_key = os.getenv("KITE_API_KEY") or _ask("Kite API key: ")
    if not api_key:
        print("No API key.", file=sys.stderr)
        return 2
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        print("Run: pip install kiteconnect", file=sys.stderr)
        return 2

    kite = KiteConnect(api_key=api_key)
    print("\n1. Open this URL, log in with your own credentials and 2FA:\n", file=out)
    print("   " + kite.login_url(), file=out)
    print("\n2. After login you land on your redirect URL (a page that does not load). Paste its\n"
          "   whole address, or just the request_token in it.\n", file=out)
    request_token = request_token_from(_ask("request_token: "))
    secret = getpass.getpass("API secret (hidden, not stored): ").strip()
    if not request_token or not secret:
        print("Need both the request token and the secret.", file=sys.stderr)
        return 2
    try:
        data = kite.generate_session(request_token, api_secret=secret)
    except Exception as exc:
        print(f"Token exchange failed: {exc}", file=sys.stderr)
        return 2
    finally:
        secret = None  # noqa: F841 — drop the reference
    if as_eval:
        print(f"export KITE_API_KEY={shlex.quote(api_key)}")
        print(f"export KITE_ACCESS_TOKEN={shlex.quote(data['access_token'])}")
        print("\nLogged in: today's token is set in this shell (valid until tomorrow morning).", file=sys.stderr)
        return 0
    print("\nRun this in the shell that runs the trader (valid until tomorrow morning).\n"
          "It is a password for today: never paste it into a chat or a message.\n")
    print(f"   export KITE_API_KEY={api_key}")
    print(f"   export KITE_ACCESS_TOKEN={data['access_token']}\n")
    return 0


def request_token_from(text: str) -> str:
    """The token itself, whether the whole redirect address was pasted or only the token."""
    if "request_token=" in text:
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(text if "://" in text else "http://x/?" + text.split("?", 1)[-1]).query)
        return (q.get("request_token") or [""])[0].strip()
    return text.strip()


if __name__ == "__main__":
    raise SystemExit(main())
