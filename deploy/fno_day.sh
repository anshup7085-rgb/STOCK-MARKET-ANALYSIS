#!/usr/bin/env bash
# One trading day of F&O paper running: the intraday explorer and strategy V,
# each in its own tmux session, so they keep running when the terminal closes.
#
#   cd ~/STOCK-MARKET-ANALYSIS && set -a && . ~/.config/nse-trader/env && set +a
#   eval "$(.venv/bin/python tools/kite_session.py --eval)"     # today's login, by hand
#   deploy/fno_day.sh                                           # starts both runners
#
#   tmux attach -t fno-explore     # watch one (leave it running: Ctrl-b, then d)
#   tmux ls                        # what is running
#
# The explorer runs to 15:30 and V to 17:15; each session then closes. Their
# output stays in logs/fno-explore-DATE.log and logs/fno-v-DATE.log. Paper only: neither
# runner can place an order. The token is passed to the two sessions only and
# is never written to disk.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
LOGIN='log in first: eval "$(.venv/bin/python tools/kite_session.py --eval)"'
: "${KITE_API_KEY:?$LOGIN}"
: "${KITE_ACCESS_TOKEN:?$LOGIN}"
command -v tmux >/dev/null || { echo "tmux is not installed: sudo apt install -y tmux" >&2; exit 1; }

day="$(date +%F)"
mkdir -p logs
for s in fno-explore fno-v; do
  if tmux has-session -t "$s" 2>/dev/null; then
    echo "$s is already running (tmux attach -t $s). Nothing started." >&2
    exit 1
  fi
done

start() {   # session name, run_trader.py arguments
  local name="$1" args="$2"
  tmux new-session -d -s "$name" -c "$REPO" \
    -e KITE_API_KEY="$KITE_API_KEY" -e KITE_ACCESS_TOKEN="$KITE_ACCESS_TOKEN" -e PYTHONPATH=. \
    "bash -c '.venv/bin/python -u run_trader.py $args 2>&1 | tee -a logs/$name-$day.log'"
  echo "started $name  ->  logs/$name-$day.log"
}

start fno-explore "fno explore run --feed kite"
start fno-v "fno run --feed kite"
echo
echo "Watch:  tmux attach -t fno-explore   (Ctrl-b then d to leave it running)"
echo "Explorer only, close its positions and stop its entries:  touch state/fno_explore/KILL"
echo "(state/KILL stops everything, and makes V's week count as MISSED)"
