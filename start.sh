#!/bin/bash
# start.sh — Launch Binance Futures Bot 2.0 on Port 8006
cd -- "$(dirname -- "${BASH_SOURCE[0]}")" || exit 1
BIN="$(pwd)/.venv/bin"

# Prevent a second supervisor/manual launch from creating a competing Uvicorn
# process that repeatedly fights for the same API port.
mkdir -p data || exit 1
exec 9>>"$(pwd)/data/binance-futures-bot.lock" || exit 1
if ! flock -n 9; then
  echo "⚠️ Binance bot is already running; refusing duplicate start."
  exit 0
fi

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi

export PORT="${PORT:-8006}"
export PYTHONPATH="$(pwd)"

echo "🌐 Starting Port ${PORT} Binance Futures Bot 2.0 with auto-restart..."
child_pid=""
stop_launcher() {
    trap '' TERM INT
    if [ -n "$child_pid" ]; then
        kill -TERM "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
    exit 0
}
trap stop_launcher TERM INT
while true; do
    "$BIN/python3" tools/serve_bot.py &
    child_pid=$!
    wait "$child_pid"
    result=$?
    child_pid=""
    if [ "$result" -eq 73 ] || [ "$result" -eq 74 ]; then
        exit "$result"
    fi
    echo "⚠️ Bot process stopped. Auto-reconnecting in 5 seconds..."
    sleep 5 &
    child_pid=$!
    wait "$child_pid"
    child_pid=""
done
