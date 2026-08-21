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

SESSION_NAME="binance_bot"

if tmux has-session -t $SESSION_NAME 2>/dev/null; then
  echo "⚠️  TMUX session '$SESSION_NAME' is already running."
  echo "👉 Use 'tmux attach -t $SESSION_NAME' to view the server logs."
  exit 0
fi

echo "🌐 Starting Port ${PORT} Binance Futures Bot 2.0 in TMUX session '$SESSION_NAME'..."
tmux new-session -d -s $SESSION_NAME "export PYTHONPATH=\"${PYTHONPATH}\"; export PORT=\"${PORT}\"; \"$BIN/uvicorn\" services.api:app --host 0.0.0.0 --port \"${PORT}\""

echo "✅ Bot is now running in the background."
echo "👉 Use 'tmux attach -t $SESSION_NAME' to view the console."
