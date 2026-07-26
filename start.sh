#!/bin/bash
# start.sh — Launch Binance Futures Bot 2.0 on Port 8006
BIN="$(pwd)/.venv/bin"

export PORT="8006"
export PAPER_TRADING="false"
export USE_TESTNET="true"
export FAST_ENTRY_MODE="false"
export PYTHONPATH="$(pwd)"

echo "🌐 Starting Port 8006 Binance Futures Bot 2.0..."
"$BIN/uvicorn" services.api:app --host 0.0.0.0 --port 8006

