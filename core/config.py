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
# MIN_SCORE_THRESHOLD：4 項評分為 30/20/20/30，90 分等於強制要求 4 項全過，訊號極少。
# 降至 70 分 = 任 3 項條件通過即可進場（3 項組合皆 >=70），提高交易頻率同時仍過濾掉太弱的訊號。
MIN_SCORE_THRESHOLD = int(os.getenv("MIN_SCORE_THRESHOLD", "70"))
# PULLBACK_TIMEOUT_MINUTES：突破後等待回調的最長時間（延長至 25 分鐘，給價格充分回踩 KC 的時間）
PULLBACK_TIMEOUT_MINUTES = int(os.getenv("PULLBACK_TIMEOUT_MINUTES", "25"))
# PULLBACK_ZONE_PCT：回調到距 KC 通道 ±0.3% 範圍內才觸發進場（稍微放寬以提高成交率）
PULLBACK_ZONE_PCT = float(os.getenv("PULLBACK_ZONE_PCT", "0.003"))

# --- 品質濾網控制參數 (對齊 7 大條件) ---
KELTNER_ATR_MULTIPLIER = float(os.getenv("KELTNER_ATR_MULTIPLIER", "1.5"))
# KELTNER_BREAKOUT_MARGIN_PCT 改為 0.0：close 超過 KC 上軌即算突破，不再要求額外距離（避免進場點過熱）
KELTNER_BREAKOUT_MARGIN_PCT = float(os.getenv("KELTNER_BREAKOUT_MARGIN_PCT", "0.0"))
KELTNER_MIN_VOLUME_RATIO = float(os.getenv("KELTNER_MIN_VOLUME_RATIO", "0.8"))  # 量能門檻提高至 0.8 倍均量，確保是真實突破
# SUPERTREND_MAX_FLIP_AGE_BARS：允許 20 根 K 棒內（約 100 分鐘）的翻轉訊號
# 8 根太嚴，整理盤下 SuperTrend 翻轉超過 40 分鐘就全部過濾，導致無法開倉
SUPERTREND_MAX_FLIP_AGE_BARS = int(os.getenv("SUPERTREND_MAX_FLIP_AGE_BARS", "20"))

# --- 動態 RSI 濾網 ---
RSI_LONG_THRESHOLD = int(os.getenv("RSI_LONG_THRESHOLD", "51"))
RSI_SHORT_THRESHOLD = int(os.getenv("RSI_SHORT_THRESHOLD", "49"))

# --- 大週期趨勢總指揮 ---
TREND_FILTER_TIMEFRAME = os.getenv("TREND_FILTER_TIMEFRAME", "1h")
TREND_FILTER_EMA_PERIOD = int(os.getenv("TREND_FILTER_EMA_PERIOD", "50"))

# --- 動態追蹤止利參數（百分比制） ---
# TRAILING_TRIGGER_PCT: 無槓桿利潤達到此百分比時啟動移動止利（0.25%）
TRAILING_TRIGGER_PCT = float(os.getenv("TRAILING_TRIGGER_PCT", "0.0025"))
# TRAILING_MODE: 預設止利模式 (conservative / balanced / aggressive)
#   conservative: 回吐 25% 平倉（75%）— 穩健鎖利，適合低波動
#   balanced:     回吐 30% 平倉（70%）— 平衡鎖利與空間
#   aggressive:   回吐 40% 平倉（60%）— 給行情最大呼吸空間
TRAILING_MODE = os.getenv("TRAILING_MODE", "balanced")
_TRAILING_PULLBACK_MAP = {"conservative": 0.75, "balanced": 0.70, "aggressive": 0.60}
TRAILING_PULLBACK_PCT = float(os.getenv("TRAILING_PULLBACK_PCT", str(_TRAILING_PULLBACK_MAP.get(TRAILING_MODE, 0.70))))

# --- 動態止利：依增利速度 + 利潤分級自動選擇回吐比例 ---
# 增利速度快 → aggressive（60%）：給行情更多空間
# 增利速度慢 → conservative（75%）：鎖緊一點
# SPEED_FAST_THRESHOLD: 增利速度 >= 此值（%/分鐘）判定為快
SPEED_FAST_THRESHOLD = float(os.getenv("SPEED_FAST_THRESHOLD", "0.0005"))   # 0.05%/min
# SPEED_SLOW_THRESHOLD: 增利速度 <= 此值（%/分鐘）判定為慢
SPEED_SLOW_THRESHOLD = float(os.getenv("SPEED_SLOW_THRESHOLD", "0.0001"))  # 0.01%/min

# --- 利潤分級鎖倉：利潤越高，鎖越緊，避免大幅回吐 ---
# (最低利潤%, 最低鎖倉比例) — 從高到低匹配，命中即停
_PROFIT_TIER_FLOOR = [
    (0.25, 0.95),  # ≥25% 無槓桿利潤 → 至少鎖 95%（僅回吐5%）
    (0.20, 0.90),  # ≥20% → 至少鎖 90%
    (0.15, 0.85),  # ≥15% → 至少鎖 85%
    (0.10, 0.80),  # ≥10% → 至少鎖 80%
    (0.05, 0.75),  # ≥5% → 至少鎖 75%
]

# --- 分批止盈參數 ---
# 到達利潤門檻時先平一部分，鎖住已賺的，剩餘繼續跑移動止利
PARTIAL_CLOSE_THRESHOLDS = [
    # (無槓桿利潤%, 平倉比例) — 從低到高依序觸發
    (0.10, 0.30),  # ≥10% → 先平 30%
    (0.20, 0.30),  # ≥20% → 再平 30%（累計60%）
]

import time as _time

def get_trailing_pullback_pct(peak_profit_pct: float, peak_updated_at: float) -> float:
    """根據增利速度 + 利潤分級動態回傳止利回吐比例。

    先依增利速度決定基礎回吐比例，再用利潤分級下限收緊，
    確保高利潤時不會回吐太多。

    Args:
        peak_profit_pct: 歷史最高無槓桿利潤百分比
        peak_updated_at: 上次 peak 更新時的 timestamp（秒）
    Returns:
        鎖倉比例（0.85 / 0.80 / 0.75 / 0.70 / 0.60）
    """
    # 1. 依增利速度決定基礎回吐比例
    elapsed_min = (_time.time() - peak_updated_at) / 60.0
    if elapsed_min <= 0 or peak_profit_pct <= 0:
        base = TRAILING_PULLBACK_PCT
    else:
        speed = peak_profit_pct / elapsed_min
        if speed >= SPEED_FAST_THRESHOLD:
            base = _TRAILING_PULLBACK_MAP["aggressive"]
        elif speed <= SPEED_SLOW_THRESHOLD:
            base = _TRAILING_PULLBACK_MAP["conservative"]
        else:
            base = _TRAILING_PULLBACK_MAP["balanced"]

    # 2. 利潤分級下限：利潤越高，鎖越緊
    for tier_min_profit, tier_min_lock in _PROFIT_TIER_FLOOR:
        if peak_profit_pct >= tier_min_profit:
            return max(base, tier_min_lock)

    return base

# NET_PROFIT_GUARANTEE_BUFFER: 保本線安全帶係數（佔進場價的比例）
#   計算基礎：吃單手續費 0.05% × 2（開+平）= 0.10%
#              滑點預留  0.03% × 2（開+平）= 0.06%
#              安全緩衝  +0.02%（防止恰好在邊緣虧損）
#   合計 ≈ 0.18%，trail_sl 必須高於「進場價 × (1 + 0.0018)」才能真正保本。
NET_PROFIT_GUARANTEE_BUFFER = float(os.getenv("NET_PROFIT_GUARANTEE_BUFFER", "0.0018"))

# --- 手續費與滑點預留設定 ---
TAKER_FEE_RATE = float(os.getenv("TAKER_FEE_RATE", "0.0005")) # 0.05% 吃單手續費（Binance USDM 合約 VIP0 Taker 費率）
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.0003"))     # 0.03% 市價單估計滑點預留（單邊）

# --- 急升急降過濾：排除短期劇烈波動的幣種 ---
# RAPID_MOVE_WINDOW: 回看幾根5分K（3根=15分鐘）
RAPID_MOVE_WINDOW = int(os.getenv("RAPID_MOVE_WINDOW", "3"))
# RAPID_MOVE_THRESHOLD: 窗口內漲跌幅超過此值（%）則排除
RAPID_MOVE_THRESHOLD = float(os.getenv("RAPID_MOVE_THRESHOLD", "5.0"))

# --- 動態倉位分配 (依訊號信心分數調整下單金額) ---
# 只有通過 MIN_SCORE_THRESHOLD 才會進場；分數越高代表 4 項條件符合越多，
# 給予更高倍數的下單金額（以 TRADE_AMOUNT_USDT 為基準，而非總資金比例，避免部位隨餘額增長滾雪球）。
POSITION_SIZE_TIERS = [
    (90, 1.5),  # 4 項全過（滿分）：1.5x 基礎倉位
    (80, 1.0),  # 高分：1.0x 基礎倉位
    (70, 0.6),  # 剛過門檻：0.6x 基礎倉位，小倉試錯
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
SYMBOL_ROTATION_MIN_SCORE_GAP = float(os.getenv("SYMBOL_ROTATION_MIN_SCORE_GAP", "5.0"))
SYMBOL_ROTATION_MAX_CHANGES = int(os.getenv("SYMBOL_ROTATION_MAX_CHANGES", "3"))
SYMBOL_MIN_LISTING_DAYS = int(os.getenv("SYMBOL_MIN_LISTING_DAYS", "7"))
SYMBOL_MAX_24H_CHANGE_PCT = float(os.getenv("SYMBOL_MAX_24H_CHANGE_PCT", "30.0"))
AI_ADVISOR_ENABLED = os.getenv("AI_ADVISOR_ENABLED", "true").lower() == "true"
AI_ADVISOR_URL = os.getenv("AI_ADVISOR_URL", "http://127.0.0.1:8888/v1/chat/completions")
AI_ADVISOR_TIMEOUT_SEC = float(os.getenv("AI_ADVISOR_TIMEOUT_SEC", "30"))
# 歷史樣本尚少時只讓 AI 微調排序，量化條件仍是主決策。
AI_ADVISOR_WEIGHT = float(os.getenv("AI_ADVISOR_WEIGHT", "0.05"))

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
