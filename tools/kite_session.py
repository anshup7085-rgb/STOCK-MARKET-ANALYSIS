#!/usr/bin/env python3
"""
Daily Kite access token — the manual 2FA step, made less fiddly.

    python tools/kite_session.py

1. Prints Zerodha's login URL. You open it, log in on Zerodha's own page with
   your own password and 2FA, and copy `request_token` from the redirect URL.
2. You paste the request token, then your API secret at a hidden prompt.
3. It prints an `export KITE_ACCESS_TOKEN=...` line for you to run.

What it never does: log in for you, read a TOTP seed, store your API secret,
or write anything to disk. The secret lives in this process's memory for one
call and is gone when it exits. SEBI's retail-algo framework expects a human
2FA login each trading day; this keeps that human in the loop.
"""

from __future__ import annotations

import getpass
import os
import sys


def main() -> int:
    api_key = os.getenv("KITE_API_KEY") or input("Kite API key: ").strip()
    if not api_key:
        print("No API key.", file=sys.stderr)
        return 2
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        print("Run: pip install kiteconnect", file=sys.stderr)
        return 2

    kite = KiteConnect(api_key=api_key)
    print("\n1. Open this URL, log in with your own credentials and 2FA:\n")
    print("   " + kite.login_url())
    print("\n2. After login you land on your redirect URL. Copy the request_token parameter.\n")
    request_token = input("request_token: ").strip()
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
    print("\nRun this in the shell that runs the trader (valid until tomorrow morning):\n")
    print(f"   export KITE_API_KEY={api_key}")
    print(f"   export KITE_ACCESS_TOKEN={data['access_token']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
