#!/usr/bin/env bash
# The whole trading morning in one command, short enough to type on a phone:
# update the code, today's Zerodha login (by hand), then both paper runners.
#
#   deploy/morning.sh            # or just `morning`, once deploy/setup_server.sh has added the alias
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
  echo
  echo "All set. You can close this window and switch off your laptop or phone."
}

main "$@"
