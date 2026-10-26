#!/usr/bin/env bash
# The day's paper report, to copy into the chat after 17:30 (V's last settlement pass is at 17:00).
#
#   deploy/evening.sh              # today
#   deploy/evening.sh 2026-10-26   # another day
set -uo pipefail
cd "$(dirname "$0")/.."
git pull -q --ff-only 2>/dev/null || true
PYTHONPATH=. .venv/bin/python tools/day_report.py "$@"
