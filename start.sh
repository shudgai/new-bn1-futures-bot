#!/bin/bash
# start.sh — Launch Binance Futures Bot 2.0 on Port 8006
BIN="$(pwd)/.venv/bin"

# Prevent a second supervisor/manual launch from creating a competing Uvicorn
# process that repeatedly fights for the same API port.
exec 9>"/tmp/binance-futures-bot.lock"
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
while true; do
    "$BIN/uvicorn" services.api:app --host 0.0.0.0 --port "$PORT"
    echo "⚠️ Bot process stopped. Auto-reconnecting in 5 seconds..."
    sleep 5
done
