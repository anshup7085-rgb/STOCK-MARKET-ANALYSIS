#!/usr/bin/env bash
# What cron runs: one PAPER cycle after the close, or the weekly journal check.
# It never runs live. Live is run by the account holder, in their own shell.
#
#   deploy/cron_cycle.sh cycle      # run_trader.py cycle --mode paper
#   deploy/cron_cycle.sh verify     # run_trader.py verify --mode paper
#
# Exit code is run_trader.py's: 0 ok, 3 alerts were raised (already sent by
# trading/alerts.py when Twilio is configured), anything else a failure, which
# is also sent to WhatsApp, because cron itself tells nobody.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${NSE_TRADER_ENV:-$HOME/.config/nse-trader/env}"
LOG_DIR="$REPO/logs"
PY="$REPO/.venv/bin/python"
# shellcheck source=deploy/lib.sh
. "$REPO/deploy/lib.sh"

job="${1:-cycle}"
case "$job" in
  cycle)  args=(cycle --mode paper) ;;
  verify) args=(verify --mode paper) ;;
  *) echo "usage: $0 [cycle|verify]" >&2; exit 64 ;;
esac

mkdir -p "$LOG_DIR" 2>/dev/null
log="$LOG_DIR/$job-$(date +%Y-%m).log"
if ! { : >>"$log"; } 2>/dev/null; then
  echo "cannot write $log; logging to stderr instead" >&2
  log=/dev/stderr
fi
note() { echo "$(date -Iseconds) $*" >>"$log"; }

if [ -f "$ENV_FILE" ]; then
  perm="$(stat -L -c %a "$ENV_FILE")"
  owner="$(stat -L -c %u "$ENV_FILE")"
  if [ "${perm: -2}" != "00" ] || [ "$owner" != "$(id -u)" ]; then
    note "refused: $ENV_FILE is mode $perm, owner uid $owner; it must be yours and mode 600 (chmod 600)"
    exit 78
  fi
  if bad="$(forbidden_in "$ENV_FILE")"; then
    note "refused: $ENV_FILE holds $bad; remove them, none of these belong on disk"
    exit 78
  fi
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
else
  note "no env file at $ENV_FILE: running with defaults, alerts off"
fi

rc=70
{
  echo "=== $(date -Iseconds) run_trader.py ${args[*]}"
  "$PY" "$REPO/run_trader.py" "${args[@]}"
  rc=$?
  echo "=== exit $rc"
} >>"$log" 2>&1

if [ "$rc" -ne 0 ] && [ "$rc" -ne 3 ]; then
  what="the paper $job failed (exit $rc)"
  [ "$job" = verify ] && [ "$rc" -eq 2 ] && what="the paper journal does NOT verify"
  "$PY" "$REPO/run_trader.py" notify --mode paper \
    --message "cron on $(hostname): $what; see $log" >>"$log" 2>&1 || true
fi

# Logs are operational noise, not evidence: the journal in state/ is the record.
find "$LOG_DIR" -name '*.log' -mtime +400 -delete 2>/dev/null
exit "$rc"
