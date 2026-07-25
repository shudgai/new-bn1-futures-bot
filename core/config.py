import os
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.getenv("PORT", "8006"))
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "10000.0"))
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
MAX_SLOTS = int(os.getenv("MAX_SLOTS", "3"))
TRADE_AMOUNT_USDT = float(os.getenv("TRADE_AMOUNT_USDT", "50.0"))

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET = os.getenv("BINANCE_SECRET", "")

STOP_LOSS_MULTIPLIER = float(os.getenv("STOP_LOSS_MULTIPLIER", "2.0"))
TAKE_PROFIT_MULTIPLIER = float(os.getenv("TAKE_PROFIT_MULTIPLIER", "2.5"))
MAX_BREAKOUT_DISTANCE = float(os.getenv("MAX_BREAKOUT_DISTANCE", "0.003")) # 0.3% max chase threshold

DEFAULT_SYMBOLS = [
    "1000PEPE/USDT", "AAVE/USDT", "ADA/USDT", "AVAX/USDT", "BNB/USDT", 
    "BTC/USDT", "DOGE/USDT", "DOT/USDT", "ETH/USDT", "NEAR/USDT",
    "SOL/USDT", "SUI/USDT", "TAO/USDT", "UNI/USDT", "XRP/USDT"
]
