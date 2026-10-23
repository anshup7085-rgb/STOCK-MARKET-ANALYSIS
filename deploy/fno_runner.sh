#!/usr/bin/env bash
# What each tmux session started by deploy/fno_day.sh runs: one paper runner for
# the day, starting its book first if it has none. Paper only.
#
#   deploy/fno_runner.sh explore    # the intraday explorer, to 15:30
#   deploy/fno_runner.sh v          # strategy V, to 17:15
set -uo pipefail
cd "$(dirname "$0")/.."
PY=(.venv/bin/python -u run_trader.py)

case "${1:-}" in
  explore)
    if [ ! -s state/fno_explore/journal.jsonl ]; then
      "${PY[@]}" fno explore init --feed kite || exit 1
    fi
    exec "${PY[@]}" fno explore run --feed kite
    ;;
  v)
    if [ ! -s state/paper_fno/journal.jsonl ]; then
      # V's book starts only on live two-sided quotes (PREREGISTRATION_3 section 3), so on its
      # first day it waits for 09:20; its first entry slot is 10:00.
      wait=$(( $(date -d 'today 09:20' +%s) - $(date +%s) ))
      if [ "$wait" -gt 0 ]; then
        echo "V's book starts at 09:20, when quotes are live: waiting $((wait / 60)) min"
        sleep "$wait"
      fi
      "${PY[@]}" fno init --feed kite || exit 1
    fi
    exec "${PY[@]}" fno run --feed kite
    ;;
  *)
    echo "usage: $0 explore|v" >&2
    exit 64
    ;;
esac
