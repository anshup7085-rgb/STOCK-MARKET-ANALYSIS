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
ENV_FILE="${NSE_TRADER_ENV:-$HOME/.config/nse-trader/env}"
CONF_DIR="$(dirname "$ENV_FILE")"
PY="${PYTHON:-python3}"
BROKERS=()
EQUITY=""
CRON=0
ALLOW_ROOT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --broker)       BROKERS+=("${2:?--broker needs kite or groww}"); shift 2 ;;
    --paper-equity) EQUITY="${2:?--paper-equity needs an amount}"; shift 2 ;;
    --install-cron) CRON=1; shift ;;
    --allow-root)   ALLOW_ROOT=1; shift ;;
    -h|--help)      sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 64 ;;
  esac
done

say()  { printf '==> %s\n' "$*"; }
warn() { printf '!!  %s\n' "$*" >&2; }
# shellcheck source=deploy/lib.sh
. "$REPO/deploy/lib.sh"
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

# 1. Python, an ordinary account, paths cron can carry
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || { warn "need Python 3.10+; $PY is $("$PY" -V 2>&1)"; exit 1; }
if [ "$(id -u)" -eq 0 ] && [ "$ALLOW_ROOT" -eq 0 ]; then
  warn "refusing to run as root: the book, logs and cron job would all belong to root."
  warn "Run as the ordinary user that will own the trader (or pass --allow-root)."
  exit 1
fi
case "$REPO$ENV_FILE" in *" "*) warn "a path contains a space; cron lines would break: $REPO, $ENV_FILE"; exit 1 ;; esac

# 2. Virtualenv and dependencies. A venv without pip (left by a failed first
#    attempt on a system without python3-venv) is rebuilt, not reused.
if ! "$REPO/.venv/bin/python" -m pip --version >/dev/null 2>&1; then
  say "creating $REPO/.venv"
  if ! "$PY" -m venv --clear "$REPO/.venv"; then
    rm -rf "$REPO/.venv"
    warn "could not create a virtualenv. On Debian/Ubuntu: sudo apt install python3-venv, then re-run."
    exit 1
  fi
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
if bad="$(forbidden_in "$ENV_FILE")"; then
  warn "$ENV_FILE holds: $bad. Remove them: none of these belong on disk"
  exit 1
fi

# 4. The suites, on this machine's Python and packages, in a clean environment
#    so a risk override in your shell cannot fail (or pass) them
say "running the test suites"
for t in tests/test_offline.py tests/test_trading.py tests/test_research.py; do
  printf '    %-26s ' "$t"
  if ! out="$(cd "$REPO" && env -i HOME="$HOME" PATH=/usr/local/bin:/usr/bin:/bin PYTHONPATH=. .venv/bin/python "$t" 2>&1)"; then
    printf 'FAILED\n'
    printf '%s\n' "$out" | tail -n 30 >&2
    warn "tests failed on this machine; no book or cron job was installed"
    exit 1
  fi
  printf '%s\n' "$out" | tail -n 1
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

# 7. Cron, in IST. NSE has no daylight saving; the server must not either, so
#    the zone cron uses (the system one, not your shell's TZ) is read in winter
#    and summer and must give the same IST or UTC offset both times.
jan="$(env -u TZ date -d 2027-01-15T12:00 +%z)"
jul="$(env -u TZ date -d 2027-07-15T12:00 +%z)"
CYCLE=""; VERIFY=""
if [ "$jan" = "$jul" ]; then
  case "$jan" in
    +0530) CYCLE="20 16 * * 1-5"; VERIFY="30 9 * * 6" ;;
    +0000) CYCLE="50 10 * * 1-5"; VERIFY="0 4 * * 6" ;;
  esac
fi
if [ -z "$CYCLE" ]; then
  warn "the server's time zone is UTC$jan in January and UTC$jul in July; cron needs IST or UTC all year."
  warn "Run: sudo timedatectl set-timezone Asia/Kolkata, then re-run."
  [ "$CRON" -eq 0 ] || exit 1
else
  BLOCK="# >>> nse-trader >>> managed by deploy/setup_server.sh; re-run it to change
# paper cycle 16:20 IST Mon-Fri; journal check Saturday 09:30 IST. Paper only.
$CYCLE NSE_TRADER_ENV=$ENV_FILE $REPO/deploy/cron_cycle.sh cycle
$VERIFY NSE_TRADER_ENV=$ENV_FILE $REPO/deploy/cron_cycle.sh verify
# <<< nse-trader <<<"
  if [ "$CRON" -eq 1 ]; then
    command -v crontab >/dev/null || { warn "crontab is not installed (Debian/Ubuntu: sudo apt install cron)"; exit 1; }
    [ -f "$BOOK" ] || { warn "no paper book yet: add --paper-equity AMOUNT"; exit 1; }
    current="$(crontab -l 2>/dev/null || true)"
    starts="$(printf '%s\n' "$current" | grep -c '^# >>> nse-trader >>>' || true)"
    ends="$(printf '%s\n' "$current" | grep -c '^# <<< nse-trader <<<' || true)"
    first_end="$(printf '%s\n' "$current" | grep -n '^# <<< nse-trader <<<' | head -n 1 | cut -d: -f1 || true)"
    first_start="$(printf '%s\n' "$current" | grep -n '^# >>> nse-trader >>>' | head -n 1 | cut -d: -f1 || true)"
    if [ "$starts" -gt 1 ] || [ "$starts" != "$ends" ] || { [ "$starts" -eq 1 ] && [ "$first_end" -lt "$first_start" ]; }; then
      warn "your crontab's nse-trader block is damaged ($starts start / $ends end markers); left untouched."
      warn "Fix it with crontab -e (delete the block), then re-run."
      exit 1
    fi
    # The whole crontab is read before it is written: your other jobs are kept as they are.
    others="$(printf '%s\n' "$current" | sed '/^# >>> nse-trader >>>/,/^# <<< nse-trader <<</d')"
    printf '%s\n%s\n' "$others" "$BLOCK" | sed '/./,$!d' | crontab -
    say "cron installed:"
    crontab -l | sed -n '/^# >>> nse-trader >>>/,/^# <<< nse-trader <<</p' | sed 's/^/    /'
  else
    say "cron not installed (add --install-cron). It would be:"
    printf '%s\n' "$BLOCK" | sed 's/^/    /'
  fi
fi

cat <<EOF

Done. Logs: $REPO/logs/   Journal and book: $REPO/state/paper/
Alerts: fill in $ENV_FILE, then
    set -a; . $ENV_FILE; set +a
    .venv/bin/python run_trader.py alert-test --channel whatsapp
A cron job that fails, or a journal that does not verify, is sent to the same
WhatsApp once alerts are set up; until then it is only in logs/.
Live trading is not set up by this script, and should not be: see TRADING.md.
EOF
