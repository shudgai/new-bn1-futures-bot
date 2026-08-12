#!/bin/bash
# start.sh — Launch Binance Futures Bot 2.0 on Port 8006
BIN="$(pwd)/.venv/bin"

export PORT="8006"
export PYTHONPATH="$(pwd)"
export TRADE_AMOUNT_USDT="50.0"
export MAX_TRADE_RISK_USDT="2.0"

echo "🌐 Starting Port 8006 Binance Futures Bot 2.0..."
"$BIN/uvicorn" services.api:app --host 0.0.0.0 --port 8006

