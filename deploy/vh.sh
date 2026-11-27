#!/usr/bin/env bash
# V-H (research/PREREGISTRATION_4.md): download NSE's F&O files for 2019 to Oct 2026 (a few hours,
# politely), then run both implementations once and print the verdict. In the background; safe to
# close the window. Running it again resumes the download and re-runs the same computation.
#
#   deploy/vh.sh             # start
#   tail -30 logs/vh.log     # progress, and the result at the end
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
if tmux has-session -t vh 2>/dev/null; then
  echo "V-H is already running:  tail -30 logs/vh.log"
  exit 0
fi
tmux new-session -d -s vh -c "$PWD" \
  "PYTHONPATH=. .venv/bin/python -u tools/vh_download.py 2>&1 | tee -a logs/vh.log && PYTHONPATH=. .venv/bin/python -u tools/vh_run.py 2>&1 | tee -a logs/vh.log"
echo "V-H started in the background. Progress and result:  tail -30 logs/vh.log"
