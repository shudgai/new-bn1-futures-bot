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

DEFAULT_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", 
    "AVAX/USDT", "SUI/USDT", "NEAR/USDT", "DOGE/USDT", "XRP/USDT"
]
