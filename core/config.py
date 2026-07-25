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

STOP_LOSS_MULTIPLIER = float(os.getenv("STOP_LOSS_MULTIPLIER", "1.8"))
TAKE_PROFIT_MULTIPLIER = float(os.getenv("TAKE_PROFIT_MULTIPLIER", "2.2"))
MAX_BREAKOUT_DISTANCE = float(os.getenv("MAX_BREAKOUT_DISTANCE", "0.003")) # 0.3% max chase threshold

# 品質濾網控制參數
KELTNER_BREAKOUT_MARGIN_PCT = float(os.getenv("KELTNER_BREAKOUT_MARGIN_PCT", "0.05"))
KELTNER_MIN_VOLUME_RATIO = float(os.getenv("KELTNER_MIN_VOLUME_RATIO", "0.5"))
SUPERTREND_MAX_FLIP_AGE_BARS = int(os.getenv("SUPERTREND_MAX_FLIP_AGE_BARS", "5"))

DEFAULT_SYMBOLS = [
    "1000PEPE/USDT", "AAVE/USDT", "ADA/USDT", "APT/USDT", "AVAX/USDT", 
    "BTC/USDT", "DOGE/USDT", "ETH/USDT", "FET/USDT", "NEAR/USDT",
    "SOL/USDT", "SUI/USDT", "TAO/USDT", "UNI/USDT", "WIF/USDT"
]
