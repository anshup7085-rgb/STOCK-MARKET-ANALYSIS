#!/usr/bin/env bash
# What cron runs: one PAPER cycle after the close, or the weekly journal check.
# It never runs live. Live is run by the account holder, in their own shell.
#
#   deploy/cron_cycle.sh cycle      # run_trader.py cycle --mode paper
#   deploy/cron_cycle.sh verify     # run_trader.py verify --mode paper
#
# Exit code is run_trader.py's: 0 ok, 3 alerts were raised (already sent by
# trading/alerts.py when Twilio is configured), anything else a failure.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${NSE_TRADER_ENV:-$HOME/.config/nse-trader/env}"
LOG_DIR="$REPO/logs"

case "${1:-cycle}" in
  cycle)  args=(cycle --mode paper) ;;
  verify) args=(verify --mode paper) ;;
  *) echo "usage: $0 [cycle|verify]" >&2; exit 64 ;;
esac

mkdir -p "$LOG_DIR"
log="$LOG_DIR/${1:-cycle}-$(date +%Y-%m).log"

if [ -f "$ENV_FILE" ]; then
  perm="$(stat -c %a "$ENV_FILE")"
  if [ "${perm: -2}" != "00" ]; then
    echo "$(date -Iseconds) refused: $ENV_FILE is mode $perm; run chmod 600 on it" >>"$log"
    exit 78
  fi
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

{
  echo "=== $(date -Iseconds) run_trader.py ${args[*]}"
  "$REPO/.venv/bin/python" "$REPO/run_trader.py" "${args[@]}"
  rc=$?
  echo "=== exit $rc"
} >>"$log" 2>&1

# Logs are operational noise, not evidence: the journal in state/ is the record.
find "$LOG_DIR" -name '*.log' -mtime +400 -delete 2>/dev/null
exit "$rc"
