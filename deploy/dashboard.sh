#!/usr/bin/env bash
# The dashboard, served PRIVATELY: only on this server's Tailscale address, so only your own devices
# (signed in to the same Tailscale account) can open it. Never on the public internet. Read-only.
#
#   deploy/dashboard.sh          # build the page and serve it (again, if it stopped)
#
# Then on your phone, with the Tailscale app on: http://<the address it prints>:8080
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs state/dashboard
PYTHONPATH=. .venv/bin/python dashboard/build.py || exit 1
if ! command -v tailscale >/dev/null; then
  echo "Tailscale is not installed on this server yet: see research/BLUEPRINT.md, 'Seeing the dashboard'."
  echo "The page is built (state/dashboard/index.html) but not served: it is never served publicly."
  exit 0
fi
ip="$(tailscale ip -4 2>/dev/null | head -1)"
if [ -z "$ip" ]; then
  echo "Tailscale is installed but not connected: run  sudo tailscale up  and sign in, then run this again."
  exit 0
fi
if pgrep -f "http.server 8080 --bind $ip" >/dev/null; then
  echo "already serving: http://$ip:8080"
  exit 0
fi
setsid nohup .venv/bin/python -m http.server 8080 --bind "$ip" --directory state/dashboard \
  >>logs/dashboard.log 2>&1 </dev/null &
sleep 1
echo "dashboard: http://$ip:8080  (open it on a device signed in to your Tailscale account)"
