import os
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.getenv("PORT", "8006"))
PAPER_TRADING = os.getenv("PAPER_TRADING", "false").lower() == "true"
INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "10000.0"))
LEVERAGE = int(os.getenv("LEVERAGE", "5"))  # 預設槓桿：SYMBOL_LEVERAGE 未列出的幣種使用此值
MAX_SLOTS = int(os.getenv("MAX_SLOTS", "3"))

# --- 依市值/波動性分級槓桿 ---
# 主流大市值幣波動小，給高槓桿；高波動迷因幣給低槓桿，控制風險一致性
SYMBOL_LEVERAGE = {
    "BTC/USDT": 10, "ETH/USDT": 10,
    "SOL/USDT": 8, "AVAX/USDT": 8, "AAVE/USDT": 8, "UNI/USDT": 8,
    "ADA/USDT": 6, "APT/USDT": 6, "DOGE/USDT": 6, "NEAR/USDT": 6,
    "SUI/USDT": 6, "TAO/USDT": 6, "FET/USDT": 6,
    "WIF/USDT": 3, "1000PEPE/USDT": 3,
}

def get_leverage(symbol: str) -> int:
    return SYMBOL_LEVERAGE.get(symbol, LEVERAGE)

# 訊號品質必須同時限制槓桿，避免最低門檻訊號仍套用 ETH 10x 等幣種上限。
SIGNAL_LEVERAGE_CAPS = [
    (90, None),  # 滿分訊號：可使用該幣種原始上限
    (80, 6),     # 次高分：最高 6x
    (70, 3),     # 最低合格分：最高 3x
]

def get_signal_leverage(symbol: str, score: int) -> int:
    symbol_leverage = get_leverage(symbol)
    for threshold, cap in SIGNAL_LEVERAGE_CAPS:
        if score >= threshold:
            return symbol_leverage if cap is None else min(symbol_leverage, cap)
    return 1

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
# 預設採混合模式：70~79分等待回調確認，80分以上可在安全突破距離內立即進場。
FAST_ENTRY_MODE = os.getenv("FAST_ENTRY_MODE", "false").lower() == "true"
MIN_SCORE_THRESHOLD = int(os.getenv("MIN_SCORE_THRESHOLD", "70"))
# 回調訊號只保留 10 分鐘，避免使用已經失效的舊突破。
PULLBACK_TIMEOUT_MINUTES = int(os.getenv("PULLBACK_TIMEOUT_MINUTES", "10"))
# 回調成交區收窄至 KC 軌道 ±0.15%，並須通過成交前二次確認。
PULLBACK_ZONE_PCT = float(os.getenv("PULLBACK_ZONE_PCT", "0.0015"))
PULLBACK_CONFIRM_RSI_LONG = int(os.getenv("PULLBACK_CONFIRM_RSI_LONG", "53"))
PULLBACK_CONFIRM_RSI_SHORT = int(os.getenv("PULLBACK_CONFIRM_RSI_SHORT", "47"))

# --- 品質濾網控制參數 (對齊 7 大條件) ---
KELTNER_ATR_MULTIPLIER = float(os.getenv("KELTNER_ATR_MULTIPLIER", "1.5"))
# close 超過KC軌道即算突破；量能與新鮮度門檻對齊8005。
KELTNER_BREAKOUT_MARGIN_PCT = float(os.getenv("KELTNER_BREAKOUT_MARGIN_PCT", "0.0"))
KELTNER_MIN_VOLUME_RATIO = float(os.getenv("KELTNER_MIN_VOLUME_RATIO", "0.5"))
KELTNER_PARTIAL_VOLUME_RATIO = float(os.getenv("KELTNER_PARTIAL_VOLUME_RATIO", "0.35"))
RSI_LONG_PARTIAL_THRESHOLD = float(os.getenv("RSI_LONG_PARTIAL_THRESHOLD", "50.0"))
RSI_SHORT_PARTIAL_THRESHOLD = float(os.getenv("RSI_SHORT_PARTIAL_THRESHOLD", "50.0"))
# SuperTrend 翻轉最多保留 15 根 5 分鐘 K（約 75 分鐘）。
SUPERTREND_MAX_FLIP_AGE_BARS = int(os.getenv("SUPERTREND_MAX_FLIP_AGE_BARS", "15"))

# --- 動態 RSI 濾網 ---
RSI_LONG_THRESHOLD = int(os.getenv("RSI_LONG_THRESHOLD", "45"))
RSI_SHORT_THRESHOLD = int(os.getenv("RSI_SHORT_THRESHOLD", "55"))

# --- 大週期趨勢總指揮 ---
TREND_FILTER_TIMEFRAME = os.getenv("TREND_FILTER_TIMEFRAME", "1h")
TREND_FILTER_EMA_PERIOD = int(os.getenv("TREND_FILTER_EMA_PERIOD", "50"))

# --- 動態追蹤止利參數 ---
# TRAILING_LOCK_ATR_MULT: 獲利至少達到 2.0x ATR 才啟動移動止利，
#   確保價格已有足夠的真實波段漲幅才鎖利，避免正常回調就被掃出場。
TRAILING_LOCK_ATR_MULT = float(os.getenv("TRAILING_LOCK_ATR_MULT", "2.0"))
TRAILING_SL_ATR_MULT = float(os.getenv("TRAILING_SL_ATR_MULT", "2.5"))
# BREAKEVEN_LOCK_ATR_MULT: 獲利達到此倍數 ATR 就先鎖利（比 TRAILING_LOCK_ATR_MULT 低很多），
#   避免獲利在還沒到 60% 移動止利門檻前，因不再創新高而一直曝險在原始止損之下。
BREAKEVEN_LOCK_ATR_MULT = float(os.getenv("BREAKEVEN_LOCK_ATR_MULT", "0.8"))
# BREAKEVEN_LOCK_PROFIT_PCT: 這個階段鎖住「已獲利的幾成」，而非只鎖保本線，
#   隨最高/最低價持續上調（每個 tick 都重新評估），直到達到 TRAILING_LOCK_ATR_MULT 門檻改用 60% 鎖利。
BREAKEVEN_LOCK_PROFIT_PCT = float(os.getenv("BREAKEVEN_LOCK_PROFIT_PCT", "0.35"))

# NET_PROFIT_GUARANTEE_BUFFER: 保本線安全帶係數（佔進場價的比例）
#   計算基礎：吃單手續費 0.05% × 2（開+平）= 0.10%
#              滑點預留  0.03% × 2（開+平）= 0.06%
#              安全緩衝  +0.02%（防止恰好在邊緣虧損）
#   合計 ≈ 0.18%，trail_sl 必須高於「進場價 × (1 + 0.0018)」才能真正保本。
NET_PROFIT_GUARANTEE_BUFFER = float(os.getenv("NET_PROFIT_GUARANTEE_BUFFER", "0.0018"))

# --- 手續費與滑點預留設定 ---
TAKER_FEE_RATE = float(os.getenv("TAKER_FEE_RATE", "0.0005")) # 0.05% 吃單手續費（Binance USDM 合約 VIP0 Taker 費率）
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.0003"))     # 0.03% 市價單估計滑點預留（單邊）

# --- 動態倉位分配 (依訊號信心分數調整下單金額) ---
# 只有通過 MIN_SCORE_THRESHOLD 才會進場；分數越高代表 4 項條件符合越多，
# 給予更高倍數的下單金額（以 TRADE_AMOUNT_USDT 為基準，而非總資金比例，避免部位隨餘額增長滾雪球）。
POSITION_SIZE_TIERS = [
    (90, 1.0),  # 滿分訊號仍維持固定基準金額
    (80, 1.0),  # 高分訊號固定基準金額
    (70, 0.5),  # 最低合格訊號只使用半倉，且必須先通過回調二次確認
]

def get_position_multiplier(score: int) -> float:
    for threshold, mult in POSITION_SIZE_TIERS:
        if score >= threshold:
            return mult
    return 0.0

# --- 動態幣種輪替與本機 AI 輔助 ---
SYMBOL_ROTATION_COUNT = int(os.getenv("SYMBOL_ROTATION_COUNT", "12"))
SYMBOL_ROTATION_INTERVAL_SEC = int(os.getenv("SYMBOL_ROTATION_INTERVAL_SEC", "3600"))
DIRECTIONAL_SIDE_COUNT = int(os.getenv("DIRECTIONAL_SIDE_COUNT", "6"))
DIRECTIONAL_MIN_SCORE = float(os.getenv("DIRECTIONAL_MIN_SCORE", "60"))
SYMBOL_MARKET_SCAN_LIMIT = int(os.getenv("SYMBOL_MARKET_SCAN_LIMIT", "40"))
SYMBOL_MIN_QUOTE_VOLUME = float(os.getenv("SYMBOL_MIN_QUOTE_VOLUME", "20000000"))
SYMBOL_ROTATION_MIN_SCORE_GAP = float(os.getenv("SYMBOL_ROTATION_MIN_SCORE_GAP", "0.08"))
AI_ADVISOR_ENABLED = os.getenv("AI_ADVISOR_ENABLED", "true").lower() == "true"
AI_ADVISOR_URL = os.getenv("AI_ADVISOR_URL", "http://127.0.0.1:8888/v1/chat/completions")
AI_ADVISOR_TIMEOUT_SEC = float(os.getenv("AI_ADVISOR_TIMEOUT_SEC", "30"))
# AI 只占 15% 排序權重；量化績效與市場資料仍是主決策。
AI_ADVISOR_WEIGHT = float(os.getenv("AI_ADVISOR_WEIGHT", "0.15"))

# 高流動性候選池。已退場或近期反覆停損的
# TAO/FET/APT/WIF/1000PEPE/ETH 不放回自動候選池。
SYMBOL_CANDIDATE_POOL = [
    "ADA/USDT", "ARB/USDT", "AVAX/USDT", "BCH/USDT", "BNB/USDT",
    "BTC/USDT", "DOGE/USDT", "DOT/USDT", "ETC/USDT", "FIL/USDT",
    "HBAR/USDT", "ICP/USDT", "LINK/USDT", "LTC/USDT", "OP/USDT",
    "SOL/USDT", "SUI/USDT", "TRX/USDT", "XLM/USDT", "XRP/USDT",
]

# 可新開倉牌面：正績效幣種搭配高流動性主流合約，共 12 個候選。
# TAO 與近期反覆停損幣種不列入；已退出牌面的既有持倉仍會被管理。
DEFAULT_SYMBOLS = [
    "ADA/USDT",
    "AVAX/USDT",
    "BCH/USDT",
    "BNB/USDT",
    "BTC/USDT",
    "DOGE/USDT",
    "LINK/USDT",
    "LTC/USDT",
    "SOL/USDT",
    "SUI/USDT",
    "TRX/USDT",
    "XRP/USDT",
]
