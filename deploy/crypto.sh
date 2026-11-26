#!/usr/bin/env bash
# The crypto head's minute recorder (run_crypto.py live), in the background, around the clock. It also
# tops up the price history every hour and, just after midnight UTC (05:30 IST), writes the crypto
# brain's forecast and rebuilds the dashboard. Public data only; it trades nothing.
#
#   deploy/crypto.sh                  # start it if it is not running
#   deploy/crypto.sh --install-cron   # once: keep it (and the dashboard) running after a reboot, and run
#                                     # the NIFTY brain by itself at 19:35 IST on weekdays (no login needed)
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
mkdir -p logs
if [ "${1:-}" = "--install-cron" ]; then
  block="# >>> market-lab >>>
@reboot sleep 90 && $REPO/deploy/crypto.sh >/dev/null 2>&1; $REPO/deploy/dashboard.sh >/dev/null 2>&1
*/15 * * * * $REPO/deploy/crypto.sh >/dev/null 2>&1
35 19 * * 1-5 cd $REPO && PYTHONPATH=. .venv/bin/python run_brain.py today --fetch >>logs/brain.log 2>&1; $REPO/deploy/dashboard.sh >/dev/null 2>&1
# <<< market-lab <<<"
  others="$(crontab -l 2>/dev/null | sed '/^# >>> market-lab >>>/,/^# <<< market-lab <<</d')"
  printf '%s\n%s\n' "$others" "$block" | sed '/./,$!d' | crontab -
  echo "cron installed: the crypto recorder is checked every 15 minutes and restarted after a reboot;"
  echo "the NIFTY brain runs by itself at 19:35 on weekdays (log: logs/brain.log)"
  crontab -l | sed -n '/^# >>> market-lab >>>/,/^# <<< market-lab <<</p' | sed 's/^/    /'
fi
if pgrep -f "run_crypto.py live" >/dev/null; then
  [ -t 1 ] && echo "the crypto recorder is running:  tail -5 logs/crypto-live.log"
  exit 0
fi
if [ ! -d state/crypto/klines/BTCUSDT/1d ]; then
  echo "first, the price history (a few minutes):"
  PYTHONPATH=. .venv/bin/python run_crypto.py download || exit 1
fi
PYTHONPATH=. setsid nohup .venv/bin/python -u run_crypto.py live >>logs/crypto-live.log 2>&1 </dev/null &
echo "the crypto recorder started:  tail -5 logs/crypto-live.log"
