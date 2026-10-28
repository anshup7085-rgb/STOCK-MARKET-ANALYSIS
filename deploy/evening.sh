#!/usr/bin/env bash
# The day's paper report, to copy into the chat after 17:30 (V's last settlement pass is at 17:00).
#
#   deploy/evening.sh              # today
#   deploy/evening.sh 2026-10-26   # another day
set -uo pipefail
cd "$(dirname "$0")/.."
git pull -q --ff-only 2>/dev/null || true
# the public market context (FII/DII, VIX, put-call ratio); a file not yet published is retried next time
echo "recording the market context (at most 4 minutes)..."
PYTHONPATH=. timeout 240 .venv/bin/python tools/record_context.py 2>/dev/null || echo "(market context not recorded this time; it is retried tomorrow)"
PYTHONPATH=. .venv/bin/python tools/day_report.py "$@"
