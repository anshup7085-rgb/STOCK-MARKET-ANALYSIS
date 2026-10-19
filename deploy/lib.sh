# shellcheck shell=bash
# Shared by setup_server.sh and cron_cycle.sh. Sourced, not run.

# Material that must never be on disk: the live ack phrase (typed per session),
# TOTP seeds, passwords, PINs and broker API secrets (tokens are minted by hand).
# Twilio's API secret is allowed: unattended alerts cannot work without it.
FORBIDDEN_ENV='^[[:space:]]*((export|readonly|declare[[:space:]]+-x)[[:space:]]+)?(LIVE_TRADING_ACK|[A-Za-z0-9_]*TOTP[A-Za-z0-9_]*|[A-Za-z0-9_]*PASS(WORD|WD)?|([A-Za-z0-9_]*_)?M?PIN|(KITE|GROWW)_[A-Za-z0-9_]*SECRET)[[:space:]]*='

# Prints the offending variable names; exit 0 if any were found.
forbidden_in() {
  grep -iEo "$FORBIDDEN_ENV" "$1" 2>/dev/null | sed -E 's/^[[:space:]]*((export|readonly|declare[[:space:]]+-x)[[:space:]]+)?//; s/[[:space:]]*=$//' | sort -u | tr '\n' ' '
  grep -iEq "$FORBIDDEN_ENV" "$1" 2>/dev/null
}
