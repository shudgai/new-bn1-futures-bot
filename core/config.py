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
# STOP_LOSS_MULTIPLIER 擴大至 2.0x ATR：讓行情有充分呼吸空間，避免被短暫震盪掃損出場
STOP_LOSS_MULTIPLIER = float(os.getenv("STOP_LOSS_MULTIPLIER", "2.0"))
TAKE_PROFIT_MULTIPLIER = float(os.getenv("TAKE_PROFIT_MULTIPLIER", "3.0"))  # TP=1.5x SL，RR 比維持 1:1.5
# MAX_BREAKOUT_DISTANCE 收緊至 0.1%：突破後價格仍在 KC 上軌 0.1% 以內才立即進場，否則強制等回調
MAX_BREAKOUT_DISTANCE = float(os.getenv("MAX_BREAKOUT_DISTANCE", "0.001"))

# --- 精準狙擊進場門檻 ---
# MIN_SCORE_THRESHOLD：提高評分門檻至 90 分，只接受最高品質的訊號
MIN_SCORE_THRESHOLD = int(os.getenv("MIN_SCORE_THRESHOLD", "90"))
# PULLBACK_TIMEOUT_MINUTES：突破後等待回調的最長時間（延長至 25 分鐘，給價格充分回踩 KC 的時間）
PULLBACK_TIMEOUT_MINUTES = int(os.getenv("PULLBACK_TIMEOUT_MINUTES", "25"))
# PULLBACK_ZONE_PCT：回調到距 KC 通道 ±0.3% 範圍內才觸發進場（稍微放寬以提高成交率）
PULLBACK_ZONE_PCT = float(os.getenv("PULLBACK_ZONE_PCT", "0.003"))

# --- 品質濾網控制參數 (對齊 7 大條件) ---
KELTNER_ATR_MULTIPLIER = float(os.getenv("KELTNER_ATR_MULTIPLIER", "1.5"))
# KELTNER_BREAKOUT_MARGIN_PCT 改為 0.0：close 超過 KC 上軌即算突破，不再要求額外距離（避免進場點過熱）
KELTNER_BREAKOUT_MARGIN_PCT = float(os.getenv("KELTNER_BREAKOUT_MARGIN_PCT", "0.0"))
KELTNER_MIN_VOLUME_RATIO = float(os.getenv("KELTNER_MIN_VOLUME_RATIO", "0.8"))  # 量能門檻提高至 0.8 倍均量，確保是真實突破
# SUPERTREND_MAX_FLIP_AGE_BARS 啟用：限制 8 根 K 棒內的新鮮翻轉，過濾趨勢老化訊號
SUPERTREND_MAX_FLIP_AGE_BARS = int(os.getenv("SUPERTREND_MAX_FLIP_AGE_BARS", "8"))

# --- 動態 RSI 濾網 ---
RSI_LONG_THRESHOLD = int(os.getenv("RSI_LONG_THRESHOLD", "51"))
RSI_SHORT_THRESHOLD = int(os.getenv("RSI_SHORT_THRESHOLD", "49"))

# --- 大週期趨勢總指揮 ---
TREND_FILTER_TIMEFRAME = os.getenv("TREND_FILTER_TIMEFRAME", "1h")
TREND_FILTER_EMA_PERIOD = int(os.getenv("TREND_FILTER_EMA_PERIOD", "50"))

# --- 動態追蹤止利參數 ---
# TRAILING_LOCK_ATR_MULT: 獲利至少達到 1.2x ATR 才啟動移動止利，
#   確保價格已有足夠的真實波段漲幅（約 +0.5%~+0.8%），完整 cover 雙向手續費 + 滑點後才鎖利。
TRAILING_LOCK_ATR_MULT = float(os.getenv("TRAILING_LOCK_ATR_MULT", "1.2"))
TRAILING_SL_ATR_MULT = float(os.getenv("TRAILING_SL_ATR_MULT", "2.5"))

# NET_PROFIT_GUARANTEE_BUFFER: 保本線安全帶係數（佔進場價的比例）
#   計算基礎：吃單手續費 0.05% × 2（開+平）= 0.10%
#              滑點預留  0.03% × 2（開+平）= 0.06%
#              安全緩衝  +0.02%（防止恰好在邊緣虧損）
#   合計 ≈ 0.18%，trail_sl 必須高於「進場價 × (1 + 0.0018)」才能真正保本。
NET_PROFIT_GUARANTEE_BUFFER = float(os.getenv("NET_PROFIT_GUARANTEE_BUFFER", "0.0018"))

# --- 手續費與滑點預留設定 ---
TAKER_FEE_RATE = float(os.getenv("TAKER_FEE_RATE", "0.0005")) # 0.05% 吃單手續費（Binance USDM 合約 VIP0 Taker 費率）
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.0003"))     # 0.03% 市價單估計滑點預留（單邊）

DEFAULT_SYMBOLS = [
    "1000PEPE/USDT", "AAVE/USDT", "ADA/USDT", "APT/USDT", "AVAX/USDT", 
    "BTC/USDT", "DOGE/USDT", "ETH/USDT", "FET/USDT", "NEAR/USDT",
    "SOL/USDT", "SUI/USDT", "TAO/USDT", "UNI/USDT", "WIF/USDT"
]
