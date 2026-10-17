#!/usr/bin/env bash
# One-time setup of a Linux server for the paper loop. Safe to re-run.
#
#   deploy/setup_server.sh                                 # venv, deps, env file, checks
#   deploy/setup_server.sh --broker kite --broker groww    # also the broker SDKs
#   deploy/setup_server.sh --paper-equity 500000 --install-cron
#
# It never installs a live job, never sets LIVE_TRADING_ACK and never writes an
# execution policy. It writes no secret: the env file it creates holds blank
# lines for you to fill in on the server.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONF_DIR="${NSE_TRADER_CONF:-$HOME/.config/nse-trader}"
ENV_FILE="$CONF_DIR/env"
PY="${PYTHON:-python3}"
BROKERS=()
EQUITY=""
CRON=0

while [ $# -gt 0 ]; do
  case "$1" in
    --broker)       BROKERS+=("${2:?--broker needs kite or groww}"); shift 2 ;;
    --paper-equity) EQUITY="${2:?--paper-equity needs an amount}"; shift 2 ;;
    --install-cron) CRON=1; shift ;;
    -h|--help)      sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 64 ;;
  esac
done

say()  { printf '==> %s\n' "$*"; }
warn() { printf '!!  %s\n' "$*" >&2; }
# Run a command in the repo with the server env loaded, as cron will.
with_env() {
  (
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
    cd "$REPO" && PYTHONPATH=. "$@"
  )
}

# 1. Python, and not as root
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || { warn "need Python 3.10+; $PY is $("$PY" -V 2>&1)"; exit 1; }
[ "$(id -u)" -ne 0 ] || warn "running as root: use an ordinary user account for the trader"
case "$REPO" in *" "*) warn "repo path contains a space; cron lines will break: $REPO"; exit 1 ;; esac

# 2. Virtualenv and dependencies
if [ ! -x "$REPO/.venv/bin/python" ]; then
  say "creating $REPO/.venv"
  "$PY" -m venv "$REPO/.venv"
fi
PIP=("$REPO/.venv/bin/python" -m pip install -q)
"${PIP[@]}" --upgrade pip
"${PIP[@]}" -r "$REPO/requirements.txt"
for b in ${BROKERS[@]+"${BROKERS[@]}"}; do
  case "$b" in
    kite)  say "installing kiteconnect";  "${PIP[@]}" "kiteconnect>=5.2.0,<6" ;;
    groww) say "installing growwapi";     "${PIP[@]}" "growwapi>=1.5.0,<2" ;;
    *) warn "unknown broker '$b' (kite | groww)"; exit 64 ;;
  esac
done

# 3. Environment file: private, outside the repo, no secrets written by this script
umask 077
mkdir -p "$CONF_DIR"
chmod 700 "$CONF_DIR"
if [ ! -f "$ENV_FILE" ]; then
  say "writing $ENV_FILE (mode 600); fill in the alert lines yourself"
  cat >"$ENV_FILE" <<'EOF'
# nse-trader server environment. Mode 600. Never commit it, never paste it into a chat.
MARKET_PROVIDER=yfinance
# CHARGE_SCHEDULE=delivery          # delivery (Zerodha-style) | groww_delivery

# Alerts: WhatsApp, and a call when urgent (TRADING.md, "Alerts").
# ALERT_TO=+91XXXXXXXXXX
# TWILIO_ACCOUNT_SID=
# TWILIO_API_KEY=
# TWILIO_API_SECRET=
# TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
# TWILIO_MESSAGING_SERVICE_SID=
# TWILIO_WHATSAPP_CONTENT_SID=
# TWILIO_VOICE_FROM=+1XXXXXXXXXX

# Not here, ever: LIVE_TRADING_ACK (typed in your own shell, per session), broker
# access tokens (minted by hand each morning), TOTP seeds, passwords, PINs.
EOF
fi
chmod 600 "$ENV_FILE"
if grep -Eq '^[[:space:]]*(export[[:space:]]+)?(LIVE_TRADING_ACK|[A-Z_]*TOTP[A-Z_]*|[A-Z_]*PASSWORD|[A-Z_]*_PIN)=' "$ENV_FILE"; then
  warn "$ENV_FILE holds LIVE_TRADING_ACK, a TOTP seed, a password or a PIN. Remove it: none of these belong on disk"
  exit 1
fi

# 4. The suites, on this machine's Python and packages
say "running the test suites"
for t in tests/test_offline.py tests/test_trading.py tests/test_research.py; do
  printf '    %-26s ' "$t"
  (cd "$REPO" && PYTHONPATH=. .venv/bin/python "$t" | tail -n 1)
done

# 5. Can this server reach the market feed?
if with_env .venv/bin/python -c '
from market.providers import get_provider
p = get_provider()
b = p.get_bars("TCS", "1d", 5)
print(f"    feed OK: {p.name}, last bar {b.df.index[-1].date()}")
' 2>/dev/null; then :; else
  warn "market feed unreachable from this server; paper cycles would see no data"
fi

# 6. Paper book (simulated money only), at the path cron's cycle will use
BOOK="$(with_env .venv/bin/python -c '
from trading.config import TradingConfig
print(TradingConfig(mode="paper").book_path)')"
if [ -f "$BOOK" ]; then
  say "paper book exists at $BOOK; left as it is"
elif [ -n "$EQUITY" ]; then
  with_env .venv/bin/python run_trader.py init --mode paper --equity "$EQUITY"
fi

# 7. Cron, in IST. NSE has no daylight saving, so a fixed UTC offset is safe;
#    any other clock is refused rather than guessed.
case "$(date +%z)" in
  +0530) CYCLE="20 16 * * 1-5"; VERIFY="30 9 * * 6" ;;
  +0000) CYCLE="50 10 * * 1-5"; VERIFY="0 4 * * 6" ;;
  *)     CYCLE="" ;;
esac
if [ -z "$CYCLE" ]; then
  warn "server clock is UTC$(date +%z); run: sudo timedatectl set-timezone Asia/Kolkata, then re-run"
  [ "$CRON" -eq 0 ] || exit 1
fi
BLOCK="# >>> nse-trader >>> managed by deploy/setup_server.sh; re-run it to change
# paper cycle 16:20 IST Mon-Fri; journal check Saturday 09:30 IST. Paper only.
$CYCLE $REPO/deploy/cron_cycle.sh cycle
$VERIFY $REPO/deploy/cron_cycle.sh verify
# <<< nse-trader <<<"

if [ "$CRON" -eq 1 ]; then
  command -v crontab >/dev/null || { warn "crontab is not installed (Debian/Ubuntu: sudo apt install cron)"; exit 1; }
  [ -f "$BOOK" ] || { warn "no paper book yet: add --paper-equity AMOUNT"; exit 1; }
  # Read the whole crontab before writing it: your other jobs are kept as they are.
  others="$(crontab -l 2>/dev/null | sed '/^# >>> nse-trader >>>/,/^# <<< nse-trader <<</d' || true)"
  printf '%s\n%s\n' "$others" "$BLOCK" | sed '/./,$!d' | crontab -
  say "cron installed:"
  crontab -l | sed -n '/^# >>> nse-trader >>>/,/^# <<< nse-trader <<</p' | sed 's/^/    /'
else
  say "cron not installed (add --install-cron). It would be:"
  printf '%s\n' "$BLOCK" | sed 's/^/    /'
fi

cat <<EOF

Done. Logs: $REPO/logs/   Journal and book: $REPO/state/paper/
Alerts: fill in $ENV_FILE, then
    set -a; . $ENV_FILE; set +a
    .venv/bin/python run_trader.py alert-test --channel whatsapp
Live trading is not set up by this script, and should not be: see TRADING.md.
EOF
