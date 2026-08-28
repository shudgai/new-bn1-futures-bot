#!/bin/bash
# start.sh — Launch Binance Futures Bot 2.0 on Port 8006
BIN="$(pwd)/.venv/bin"

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
