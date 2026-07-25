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

# --- 風控參數 ---
STOP_LOSS_MULTIPLIER = float(os.getenv("STOP_LOSS_MULTIPLIER", "2.5"))   # 擴大止損空間，避免被 5m 小波動洗掉
TAKE_PROFIT_MULTIPLIER = float(os.getenv("TAKE_PROFIT_MULTIPLIER", "2.0"))  # TP 略低於 SL，追求動態追利
MAX_BREAKOUT_DISTANCE = float(os.getenv("MAX_BREAKOUT_DISTANCE", "0.003"))

# --- 品質濾網控制參數 (對齊 7 大條件) ---
KELTNER_BREAKOUT_MARGIN_PCT = float(os.getenv("KELTNER_BREAKOUT_MARGIN_PCT", "0.01"))
KELTNER_MIN_VOLUME_RATIO = float(os.getenv("KELTNER_MIN_VOLUME_RATIO", "0.3"))
SUPERTREND_MAX_FLIP_AGE_BARS = int(os.getenv("SUPERTREND_MAX_FLIP_AGE_BARS", "999"))

# --- 動態 RSI 濾網 ---
RSI_LONG_THRESHOLD = int(os.getenv("RSI_LONG_THRESHOLD", "51"))
RSI_SHORT_THRESHOLD = int(os.getenv("RSI_SHORT_THRESHOLD", "49"))

# --- 大週期趨勢總指揮 ---
TREND_FILTER_TIMEFRAME = os.getenv("TREND_FILTER_TIMEFRAME", "1h")
TREND_FILTER_EMA_PERIOD = int(os.getenv("TREND_FILTER_EMA_PERIOD", "50"))

# --- 動態追蹤止利參數 ---
TRAILING_LOCK_ATR_MULT = float(os.getenv("TRAILING_LOCK_ATR_MULT", "1.2")) # 獲利達 1.2x ATR 才鎖定保本
TRAILING_SL_ATR_MULT = float(os.getenv("TRAILING_SL_ATR_MULT", "2.5"))

# --- 手續費與滑點預留設定 ---
TAKER_FEE_RATE = float(os.getenv("TAKER_FEE_RATE", "0.0004")) # 0.04% 吃單手續費
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.0003"))     # 0.03% 市價單估計滑點預留

DEFAULT_SYMBOLS = [
    "1000PEPE/USDT", "AAVE/USDT", "ADA/USDT", "APT/USDT", "AVAX/USDT", 
    "BTC/USDT", "DOGE/USDT", "ETH/USDT", "FET/USDT", "NEAR/USDT",
    "SOL/USDT", "SUI/USDT", "TAO/USDT", "UNI/USDT", "WIF/USDT"
]
