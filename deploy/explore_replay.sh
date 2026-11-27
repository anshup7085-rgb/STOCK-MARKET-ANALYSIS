#!/usr/bin/env bash
# EXPLORATION: the intraday explorer replayed minute by minute on Dhan's option data, Oct 2021 to
# Oct 2024 (research/explore_replay.py). In the background, about half an hour; safe to close the
# window. The result is at the end of the log.
#
#   deploy/explore_replay.sh             # start
#   tail -40 logs/explore-replay.log     # progress, then the result
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
if pgrep -f "research/explore_replay.py" >/dev/null; then
  echo "the replay is already running:  tail -40 logs/explore-replay.log"
  exit 0
fi
PYTHONPATH=. setsid nohup .venv/bin/python -u research/explore_replay.py "$@" \
  >"logs/explore-replay.log" 2>&1 </dev/null &
echo "replay started in the background. Progress and result:  tail -40 logs/explore-replay.log"
