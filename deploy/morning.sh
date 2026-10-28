#!/usr/bin/env bash
# The whole trading morning in one command, short enough to type on a phone:
# update the code, today's Zerodha login (by hand), then both paper runners.
#
#   deploy/morning.sh            # or just `morning`, once deploy/setup_server.sh has added the alias
#
# If the connection drops while it waits (a phone switching apps), type `morning`
# again: it returns to the same screen.
#
# The login's token goes from this script straight to the runners' tmux sessions:
# it is never shown, and never written to disk. Paper only.
set -euo pipefail

main() {   # parsed whole before it runs, so the pull below cannot change it mid-run
  local repo
  repo="$(cd "$(dirname "$0")/.." && pwd)"
  cd "$repo"
  git pull -q --ff-only 2>/dev/null || echo "(could not update the code; carrying on with what is here)"
  set -a
  # shellcheck disable=SC1090
  . "${NSE_TRADER_ENV:-$HOME/.config/nse-trader/env}"
  set +a
  eval "$(.venv/bin/python tools/kite_session.py --eval)"
  deploy/fno_day.sh "$@"
  # The previous session's FII/DII flows are served only until NSE's next update, so catch them now,
  # in the background (the runners are already up), without the Kite login in its environment.
  mkdir -p logs
  env -u KITE_ACCESS_TOKEN -u KITE_API_KEY PYTHONPATH=. setsid nohup timeout 300 \
    .venv/bin/python tools/record_context.py >>"logs/context-$(date +%F).log" 2>&1 </dev/null &
  # Once: what this Kite app's historical API can fetch (counts and dates only, read-only). It
  # needs today's login; a second login could end the runners' one, so it rides on this one.
  if [ ! -e logs/kite-history-check.done ]; then
    PYTHONPATH=. setsid nohup timeout 600 .venv/bin/python tools/kite_history_check.py \
      >"logs/kite-history-check-$(date +%F).log" 2>&1 </dev/null &
  fi
  echo
  echo "All set. You can close this window and switch off your laptop or phone."
}

# On a phone, switching to the browser to log in drops the terminal, and the
# waiting login with it. So the login runs inside tmux: after a drop, typing
# `morning` again brings back the same waiting screen. It gives up after 30 min.
if [ -z "${TMUX:-}" ] && [ "${1:-}" != "--inside" ] && command -v tmux >/dev/null; then
  self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  exec tmux new-session -A -s login \
    "timeout --foreground 1800 $(printf '%q ' "$self" --inside "$@"); echo; echo 'This window closes in 2 minutes.'; sleep 120"
fi
[ "${1:-}" = "--inside" ] && shift
main "$@"
