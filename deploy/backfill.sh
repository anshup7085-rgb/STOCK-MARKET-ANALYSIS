#!/usr/bin/env bash
# Download NSE's archived market-context files since 2012 (FII, DII, pro and client positions;
# India VIX), in the background, about one request a second: a few hours, once. Safe to close
# the window. Running it again resumes where it stopped.
#
#   deploy/backfill.sh            # start (or say it is already running)
#   tail -5 logs/backfill.log     # progress
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
if tmux has-session -t backfill 2>/dev/null; then
  echo "the backfill is already running:  tail -5 logs/backfill.log"
  exit 0
fi
tmux new-session -d -s backfill -c "$PWD" \
  "PYTHONPATH=. .venv/bin/python -u tools/backfill_context.py 2>&1 | tee -a logs/backfill.log"
echo "backfill started in the background. Progress:  tail -5 logs/backfill.log"
