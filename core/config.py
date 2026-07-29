import os
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.getenv("PORT", "8006"))
PAPER_TRADING = os.getenv("PAPER_TRADING", "false").lower() == "true"
INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "10000.0"))
LEVERAGE = int(os.getenv("LEVERAGE", "5"))  # 預設槓桿：SYMBOL_LEVERAGE 未列出的幣種使用此值

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

# --- 依實測 ATR% 分級槓桿（取代上面 SYMBOL_LEVERAGE 用市值猜的假設）---
# SYMBOL_LEVERAGE 是憑「市值大小」猜這個幣波動小/大，本身沒有實測依據。
# core/symbol_rotation.py 現在會用已經在抓的 5m K 線順便記錄每個幣種的
# 實際 ATR%，有資料後改用這裡的門檻決定槓桿上限：波動越小給越高槓桿，
# 波動越大給越低槓桿，跟策略本身的 MIN_ATR_PCT(0.15%)~MAX_ATR_PCT(0.6%)
# 可交易區間對齊。還沒累積到資料的幣種，get_dynamic_leverage() 會退回
# 上面的 SYMBOL_LEVERAGE 靜態表，行為不變。
# 上限從 10x/8x/6x 降到 6x/5x/4x：實測 DOT/USDT ATR%=0.22%（落在 <0.30%
# 這一級）用 8x 進場，止損觸發轉市價單滑價 0.66%，換算成 8x 槓桿的虧損
# 放大到 -2.30 USDT（若沒放大槓桿只會是這個數字的一小部分）。「波動率
# 低」不代表「不會有快速的價格跳動」，尤其測試網流動性淺、滑價風險本來
# 就比實盤高，槓桿倍數再放大會把同樣的滑價百分比換算成更大的金額損失。
ATR_LEVERAGE_TIERS = [
    (0.002, 6),    # 實測 ATR% < 0.20% → 6x（原10x）
    (0.003, 5),    # < 0.30% → 5x（原8x）
    (0.0045, 4),   # < 0.45% → 4x（原6x）
    (0.006, 3),    # < 0.60%（MAX_ATR_PCT 邊界）→ 3x
]

def get_atr_based_leverage(atr_pct: float) -> int:
    for threshold, lev in ATR_LEVERAGE_TIERS:
        if atr_pct < threshold:
            return lev
    return 3

TRADE_AMOUNT_USDT = float(os.getenv("TRADE_AMOUNT_USDT", "50.0"))

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET = os.getenv("BINANCE_SECRET", "")

# --- 動態倉位分配 (依帳戶餘額自動計算金額，MAX_SLOTS 限制同時持倉數) ---
# 之前這個變數只有 import 進 engine.py，實際開倉邏輯完全沒有用到它——
# 只要餘額夠、同時間有幾個訊號達標就全部開，導致行情同時觸發多個高度
# 相關的訊號時（同一波市場方向），一次開一堆單，一旦反轉就一次全部
# 停損。現在真的接上：同時最多 MAX_SLOTS 筆持倉，訊號一次多於這個數字
# 時，依評分排序只挑最優的填滿槽位（沿用既有的評分排序邏輯），
# 每筆金額仍依可用餘額動態計算，不固定死。MAX_SLOTS <= 0 表示不限制
# 筆數，只受可用餘額約束（回到原本的行為）。
MAX_SLOTS = int(os.getenv("MAX_SLOTS", "5"))
# MIN_TRADE_USDT: 每筆最低開倉金額，低於此金額不開新倉
MIN_TRADE_USDT = float(os.getenv("MIN_TRADE_USDT", "30.0"))
# TEST_BUDGET_CAP_USDT：測試階段用，把「可用預算」暫時封頂在這個金額，
# 不管 Testnet 帳戶實際餘額有多少（例如帳戶有 4600U，但先假裝只有 150U，
# 觀察在這個額度下實際能開滿幾槽）。設 0（或不設）代表不封頂，直接用
# 帳戶真實可用餘額——正式上線時把這個值改回 0（或整個移除環境變數）即可，
# 不用再改程式碼。
TEST_BUDGET_CAP_USDT = float(os.getenv("TEST_BUDGET_CAP_USDT", "0"))
# STOP_LOSS_MULTIPLIER / TAKE_PROFIT_MULTIPLIER
STOP_LOSS_MULTIPLIER = float(os.getenv("STOP_LOSS_MULTIPLIER", "1.5"))
TAKE_PROFIT_MULTIPLIER = float(os.getenv("TAKE_PROFIT_MULTIPLIER", "3.0"))

# --- 三階段階梯移動停利 / 移動保本配置 ---
# ENABLE_TRAILING_STOP: 是否開啟三階段移動停利機制
ENABLE_TRAILING_STOP = os.getenv("ENABLE_TRAILING_STOP", "true").lower() == "true"
# TIER 1 (保本鎖): 浮盈達 +0.6% 時，將止損移至進場價+0.05%（確保不損保本）
TRAILING_TIER1_TRIGGER_PCT = float(os.getenv("TRAILING_TIER1_TRIGGER_PCT", "0.006"))
# TIER 2 (第一階段鎖利): 浮盈達 +1.2% 時，將止損移至進場價+0.6%（鎖定第一階段獲利）
TRAILING_TIER2_TRIGGER_PCT = float(os.getenv("TRAILING_TIER2_TRIGGER_PCT", "0.012"))
# TIER 3 (高點回撤追蹤): 浮盈達 +1.8% 時啟動，若從最高獲利回撤 30% 即市價平倉
TRAILING_TIER3_TRIGGER_PCT = float(os.getenv("TRAILING_TIER3_TRIGGER_PCT", "0.018"))
TRAILING_TIER3_CALLBACK_RATIO = float(os.getenv("TRAILING_TIER3_CALLBACK_RATIO", "0.30"))
# MIN_SL_DISTANCE_PCT：止損距離下限（佔進場價的比例），不管 ATR 倍數設多寬，
# 波動率本身很低的時候（實測 BTC/LINK/LTC/BNB/XRP 反推 ATR 只有 0.07%~0.21%），
# ATR×倍數算出來的止損距離還是會縮到很窄，一樣容易被雜訊掃出。用這個下限
# 保證止損距離不會低於此比例，止盈距離依 TAKE_PROFIT/STOP_LOSS 倍數比例同步放大。
MIN_SL_DISTANCE_PCT = float(os.getenv("MIN_SL_DISTANCE_PCT", "0.004"))
# DISASTER_STOP_MULTIPLIER：使用者要求「先不要止損，讓利潤有機會回來，
# 由人工判斷要不要平倉」，把原本 1.5x ATR 的緊止損改成一條寬鬆很多的
# 最後防線（乘在 sl_distance 上，止盈維持原本距離不變，只放寬止損）。
# 這不是完全拿掉止損（那樣不在螢幕前時會被無限套牢），而是保留一個
# 極端行情下的最後保護，平常給價格足夠空間回調/反彈，讓使用者自己
# 決定要不要提早手動平倉。
DISASTER_STOP_MULTIPLIER = float(os.getenv("DISASTER_STOP_MULTIPLIER", "2.5"))

# --- 精準狙擊進場門檻 ---
# MIN_SCORE_THRESHOLD：4 項評分為 30/20/20/30，90 分等於強制要求 4 項全過，訊號極少。
# 71 分 = 至少要 3 項條件通過（基礎70分）再加上品質細分加分至少 1 分才能進場，
# 排除掉品質加分完全是 0 分、勉強壓線過關的最弱訊號。
MIN_SCORE_THRESHOLD = int(os.getenv("MIN_SCORE_THRESHOLD", "65"))
# STRONG_BREAKOUT_SCORE_THRESHOLD：分流機制門檻。
# 當評分 >= 78 時，代表強勢突破，直接觸發 BUY/SELL（市價單進場），不等待回踩。
# 當 65 <= 評分 < 78 時，代表溫和突破，觸發 WAIT_PULLBACK（掛限價單等待回踩）。
STRONG_BREAKOUT_SCORE_THRESHOLD = int(os.getenv("STRONG_BREAKOUT_SCORE_THRESHOLD", "78"))
MIN_OPEN_SIGNAL_SCORE = int(os.getenv("MIN_OPEN_SIGNAL_SCORE", "65"))
# PULLBACK_TIMEOUT_MINUTES：突破後等待回調的最長時間。
# 25 分鐘→90 秒→20 秒：實測真正會成交的掛單，6 筆裡有 5 筆在 10 秒內
# 就成交（7.2~9.7 秒），只有 1 筆例外撐了 67.4 秒；反觀逾時撤單的 22
# 筆，全部卡滿 90~105 秒才放棄——代表 90 秒對「不會成交」的單只是白等，
# 價格早就背離、動能已經轉向。20 秒給最快成交群約 2 倍緩衝，短線動能
# 真的夠強會立刻回踩接到，等不到就代表這次動能偏單邊，繼續等大機率是
# 在賭一個正在遠離的假訊號，不如撤單讓 engine.py 用最新資料重新判斷。
PULLBACK_TIMEOUT_MINUTES = float(os.getenv("PULLBACK_TIMEOUT_MINUTES", "0.3333"))
# 移除連續掛單失敗冷卻（原 PENDING_REPOST_STREAK_LIMIT/PENDING_BACKOFF_
# MINUTES）：原本連續撤單達門檻會強制冷卻一段時間，但這會讓一個
# symbol 剛好在冷卻期間真的出現達標訊號時被錯過。改成不限次數重掛，
# 每次掛單前都要重新通過分數門檻（見 testnet_account.place_limit_entry
# 的 MIN_OPEN_SIGNAL_SCORE 檢查），未成交/條件變差就撤單，下一輪分數
# 夠不夠再決定要不要重掛，用分數本身當唯一守門，不用額外的次數/時間
# 限制。
# PULLBACK_ZONE_PCT：回調到距 KC 通道 ±0.3% 範圍內才觸發進場（稍微放寬以提高成交率）
PULLBACK_ZONE_PCT = float(os.getenv("PULLBACK_ZONE_PCT", "0.003"))
# PULLBACK_TARGET_DEPTH：回調進場目標價，從 KC 上/下軌往 EMA20 均價再靠攏的比例。
# 0.0 = 只回踩到 KC 軌道（進場價最靠近突破點，成交率最高）；
# 1.0 = 回踩到 EMA20 均價才進場（空間最大，但等到的機率最低）。
# 0.5 → 0.2：實測 0.5 太深，突破後價格常無法回踩那麼遠，掛單超時撤單
# 後機會就錯過了。改 0.2 讓掛單位置非常靠近 KC 軌道（只往 EMA20 靠 20%），
# 大幅提升成交率，同時仍比「直接追在突破點」便宜一點點。
PULLBACK_TARGET_DEPTH = float(os.getenv("PULLBACK_TARGET_DEPTH", "0.2"))
# PULLBACK_SCORE_THRESHOLD：回調二次確認（confirm_pullback_entry）用的總分門檻。
# 55 -> 48：配合進場門檻放寬，同步調降回踩二次確認分級門檻。
PULLBACK_SCORE_THRESHOLD = int(os.getenv("PULLBACK_SCORE_THRESHOLD", "48"))

# --- 品質濾網控制參數 (對齊 7 大條件) ---
# KELTNER_ATR_MULTIPLIER 調回 1.5：實測最早期(通道確實是1.5倍時)勝率
# 53~63%，通道被誤降到1.0倍之後勝率掉到13~17%——通道太窄代表「真突破」
# 的確認門檻變低，容易讓假突破混進來。進場價不再靠通道變窄來壓低，
# 改由下方 evaluate_signal() 一律用回踩機制決定（見 PULLBACK_TARGET_DEPTH）。
KELTNER_ATR_MULTIPLIER = float(os.getenv("KELTNER_ATR_MULTIPLIER", "1.5"))
# KELTNER_BREAKOUT_MARGIN_PCT 改為 0.0：close 超過 KC 上軌即算突破，不再要求額外距離（避免進場點過熱）
KELTNER_BREAKOUT_MARGIN_PCT = float(os.getenv("KELTNER_BREAKOUT_MARGIN_PCT", "0.0"))
KELTNER_MIN_VOLUME_RATIO = float(os.getenv("KELTNER_MIN_VOLUME_RATIO", "0.8"))  # 量能門檻提高至 0.8 倍均量，確保是真實突破
# BREAKOUT_CONFIRM_BARS：KC 突破需要「收盤確認」的防假突破機制。
BREAKOUT_CONFIRM_BARS = int(os.getenv("BREAKOUT_CONFIRM_BARS", "1"))
# POST_BREAKOUT_VOL_SUSTAIN_RATIO：突破後量能持續性確認，用於 confirm_pullback_entry()。
POST_BREAKOUT_VOL_SUSTAIN_RATIO = float(os.getenv("POST_BREAKOUT_VOL_SUSTAIN_RATIO", "0.6"))
# FRESHNESS_DECAY_BARS：訊號新鮮度改成連續淡化。
FRESHNESS_DECAY_BARS = int(os.getenv("FRESHNESS_DECAY_BARS", "120"))
# MIN_FRESHNESS_SCORE：新鮮度子分數門檻。從 22 降至 15 分，放寬對趨勢翻轉發生時間點的嚴格限制（允許約 4.5 小時內發生的趨勢）。
MIN_FRESHNESS_SCORE = int(os.getenv("MIN_FRESHNESS_SCORE", "15"))

# --- ADX 趨勢強度濾網 ---
# 1. ADX_MANDATORY_MIN（硬性底線）：低於此值直接 HOLD。從 12.0 降至 10.0，允許微弱起步趨勢進入評分。
ADX_MANDATORY_MIN = float(os.getenv("ADX_MANDATORY_MIN", "10.0"))

# --- ADX 趨勢強度濾網 ---
# 兩層防線分開設計：
# 1. ADX_MANDATORY_MIN（硬性底線）：ADX 低於此值直接 HOLD，連評分都不進入。
#    盤整期 ADX 常落在 10~17，12 以下可確認為「完全無趨勢」，假突破最高發。
#    設 12 而非直接用 ADX_QUALITY_MIN(15) 是刻意保守——只擋極端無動能場景，
#    不大幅壓縮訊號數量；後續實測再視情況調高。
# 2. ADX_QUALITY_MIN/FULL（軟性加分）：12~30 區間內按比例加分，越高越好，
#    但不到最低門檻就加 0 分；超出 ADX_QUALITY_FULL 視為滿分。
# 3. ADX_DECLINE 衰退擋單：ADX 現在比 N 根前低且已低於 ADX_QUALITY_MIN，
#    代表動能在退潮，硬性擋單（見下方）。
ADX_PERIOD = int(os.getenv("ADX_PERIOD", "14"))
ADX_MANDATORY_MIN = float(os.getenv("ADX_MANDATORY_MIN", "12.0"))  # 硬性最低 ADX 門檻，低於此直接 HOLD
ADX_QUALITY_MIN = float(os.getenv("ADX_QUALITY_MIN", "15"))
ADX_QUALITY_FULL = float(os.getenv("ADX_QUALITY_FULL", "30"))
# ADX_DECLINE_LOOKBACK_BARS：實測 AAVE/USDT 07/28 14:48 這筆進場，往前
# 回看 8 根 5 分K，ADX 從 19.51 一路降到 14.67 才進場——SuperTrend 方向
# 還沒翻轉、新鮮度分數也還算高，但 ADX 連續下滑代表動能早就在退潮，是
# 典型的「末端趨勢」樣貌，只是新鮮度（看 SuperTrend 翻轉）量不到。這裡
# 額外用「ADX 現在比 N 根K棒前低，且已經低於 ADX_QUALITY_MIN」當強制
# 門檻，專門抓這種「方向沒變但動能已經在衰退」的情況，跟品質加分（軟性
# 只影響排序）分開，是硬性擋單。
ADX_DECLINE_LOOKBACK_BARS = int(os.getenv("ADX_DECLINE_LOOKBACK_BARS", "6"))
# ADX_DECLINE_LOOKBACK_BARS_1H：同一套「ADX 現在比 N 根K棒前低，且已經
# 低於 ADX_QUALITY_MIN」邏輯，但改看 1h K線——5分K的新鮮度/ADX檢查只能
# 看到「這根5分K的小趨勢夠不夠新」，看不出「大週期本身是不是也已經在
# 做頭/做底」。用同一批 update_1h_trend_cache() 已經抓到的1h K線重算，
# 不用額外呼叫API。
ADX_DECLINE_LOOKBACK_BARS_1H = int(os.getenv("ADX_DECLINE_LOOKBACK_BARS_1H", "6"))
# EMA_EXTENSION_MAX_ATR_MULT：價格距離 EMA20 太遠（用 ATR 正規化衡量）
# 代表這波已經漲/跌很多才追進場，均值回歸風險高，容易一進場就拉回。
# 實測當下多個幣種的正常距離落在 0.75~2.71 倍 ATR，3.5 倍給了足夠緩衝，
# 只擋真正極端拉開的情況。
EMA_EXTENSION_MAX_ATR_MULT = float(os.getenv("EMA_EXTENSION_MAX_ATR_MULT", "3.5"))

# --- 動態 RSI 濾網 ---
RSI_LONG_THRESHOLD = int(os.getenv("RSI_LONG_THRESHOLD", "51"))
RSI_SHORT_THRESHOLD = int(os.getenv("RSI_SHORT_THRESHOLD", "49"))

# --- 大週期趨勢總指揮 ---
TREND_FILTER_TIMEFRAME = os.getenv("TREND_FILTER_TIMEFRAME", "1h")
TREND_FILTER_EMA_PERIOD = int(os.getenv("TREND_FILTER_EMA_PERIOD", "50"))

# --- 以下 TRAILING_* / _PROFIT_TIER_FLOOR 為舊版百分比制移動止利，
# 只剩 core/paper_account.py（未上線使用的模擬帳戶）在用，
# BinanceTestnetAccount 的正式交易已改用上面的 ATR 移動停利。---
TRAILING_TRIGGER_PCT = float(os.getenv("TRAILING_TRIGGER_PCT", "0.005"))
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
# 剛觸發（0.25%）時不強制套用分級門檻，只看增利速度（60~75%），
# 保留較大的回檔空間，避免一碰到 0.25% 就被雜訊掃出；觸發點本身
# 不拉高——拉高觸發點只會讓更多小賺的單子連鎖都鎖不到、直接變虧損，
# 讓「贏小賠大」更嚴重。真正該調的是觸發之後的鎖定爬升速度：
# 門檻整體拉開、封頂拉到 3.0%，配合今天拉寬的止損止盈（2.0x/4.0x
# ATR），讓利潤有更多空間往上跑，不要一點漲幅就被鎖死出場。
# (最低利潤%, 最低鎖倉比例) — 從高到低匹配，命中即停
_PROFIT_TIER_FLOOR = [
    (TRAILING_TRIGGER_PCT * 12, 0.95),  # ≥3.00% 無槓桿利潤 → 至少鎖 95%（僅回吐5%）
    (TRAILING_TRIGGER_PCT * 8, 0.88),   # ≥2.00% → 至少鎖 88%
    (TRAILING_TRIGGER_PCT * 6, 0.82),   # ≥1.50% → 至少鎖 82%
    (TRAILING_TRIGGER_PCT * 4, 0.75),   # ≥1.00% → 至少鎖 75%
    (TRAILING_TRIGGER_PCT * 2, 0.65),   # ≥0.50% → 至少鎖 65%
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
#              止損滑價緩衝 STOP_LIMIT_SLIPPAGE_GUARD_PCT = 0.20%
#              （原本抓 0.03%×2=0.06% 滑點預留，比實際的滑價緩衝還小，
#              保本鎖觸發後在緩衝邊緣成交時穩定倒虧，實測 SUI/USDT
#              07/28 21:43~21:49 這筆就是精準卡在 0.6791×1.002 的滑價
#              邊緣成交，鎖到的 0.18% 完全蓋不住 0.10%+0.20%）
#              安全緩衝  +0.05%
#   合計 ≈ 0.35%，trail_sl 必須高於「進場價 × (1 + 0.0035)」才能真正保本。
NET_PROFIT_GUARANTEE_BUFFER = float(os.getenv("NET_PROFIT_GUARANTEE_BUFFER", "0.0035"))
# 只剩 core/paper_account.py 在用（BinanceTestnetAccount 已移除開倉後的
# 動態止損調整，改回固定 SL/TP，見 STOP_LOSS_MULTIPLIER 註解）。
# STOP_LIMIT_SLIPPAGE_GUARD_PCT：止損保護單原本是 STOP_MARKET（觸發後
# 直接轉市價單成交，不管當下市價多差都會成交），實測 DOT/USDT 07/28
# 19:42~19:44 這筆，保本鎖正確把止損收緊到 0.7586（理論上該筆已經是
# +0.34 USDT 小賺），但觸發轉市價單時滑價 0.66%，實際卻虧了 -2.30
# USDT——滑價吃掉的金額比原本該賺的還多。改用 STOP（限價止損）：觸發
# 後轉成「觸發價 ± 這個緩衝」的限價單，超過緩衝範圍寧可不成交，也不要
# 用任意差的市價成交。緩衝抓 0.2%，足夠涵蓋正常滑價，又不會大到讓限價
# 單長時間掛不出去。搭配下面的逾時未成交強制平倉機制，避免價格跳空
# 超過緩衝時部位裸奔太久。
STOP_LIMIT_SLIPPAGE_GUARD_PCT = float(os.getenv("STOP_LIMIT_SLIPPAGE_GUARD_PCT", "0.002"))
# STOP_LIMIT_UNFILLED_TIMEOUT_SEC：限價止損觸發後，如果市價已經穿越
# 限價單的緩衝範圍導致遲遲無法成交，超過這個秒數就放棄等待、直接強制
# 市價平倉，避免部位無限期裸奔。
STOP_LIMIT_UNFILLED_TIMEOUT_SEC = float(os.getenv("STOP_LIMIT_UNFILLED_TIMEOUT_SEC", "8"))

# --- 手續費與滑點預留設定 ---
TAKER_FEE_RATE = float(os.getenv("TAKER_FEE_RATE", "0.0005")) # 0.05% 吃單手續費（Binance USDM 合約 VIP0 Taker 費率）
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.0003"))     # 0.03% 市價單估計滑點預留（單邊）

# --- 急升急降過濾：排除短期劇烈波動的幣種 ---
# RAPID_MOVE_WINDOW: 回看幾根5分K（3根=15分鐘）
RAPID_MOVE_WINDOW = int(os.getenv("RAPID_MOVE_WINDOW", "3"))
# RAPID_MOVE_THRESHOLD: 窗口內漲跌幅超過此值（%）則排除
RAPID_MOVE_THRESHOLD = float(os.getenv("RAPID_MOVE_THRESHOLD", "5.0"))

# --- 高波動幣種過濾：ATR 佔價格比例過高就不開倉 ---
# 實測 87 筆平倉交易發現，最大的幾筆虧損（AKE -2.11、ONDO -1.19、LAB -1.16、
# ZAMA -1.03）都集中在 ATR/價格 明顯偏高的幣種：SL/TP 是用 ATR 倍數算的，
# 幣種本身波動率越高，同樣的倉位金額下止損被觸發時虧的錢就越大，
# 而移動止利鎖住的獲利卻不會跟著等比放大，造成贏小賠大。
# 反觀 BTC(0.19%)、HYPE(0.26%)、SOL(0.30%)、AAVE(0.60%) 這類 ATR% 較低的
# 幣種整體是賺錢的，故以 0.6% 為門檻，超過就跳過進場。
MAX_ATR_PCT = float(os.getenv("MAX_ATR_PCT", "0.006"))
# MIN_ATR_PCT：波動率太低也不開倉。實測 BTC/LINK/LTC/BNB/XRP 一批集中在
# 12 分鐘內的止損，反推 ATR 只有 0.07%~0.21%——市場太安靜時的「突破」
# 更可能是假突破（盤整區間的雜訊），沒有真實動能支撐，容易一進場就
# 反轉。跟 MAX_ATR_PCT 一起框出一個「波動適中」的可交易區間。
MIN_ATR_PCT = float(os.getenv("MIN_ATR_PCT", "0.0015"))

# --- 動態倉位分配 (依訊號信心分數調整下單金額) ---
# 只有通過 MIN_SCORE_THRESHOLD 才會進場；分數越高代表 4 項條件符合越多，
# 給予更高倍數的下單金額（以 TRADE_AMOUNT_USDT 為基準，而非總資金比例，避免部位隨餘額增長滾雪球）。
# 每筆金額硬上限 = TRADE_AMOUNT_USDT（預設50）：原本 90 分以上會給到 1.5x
# （75U），但配合高分訊號常同時給到的高槓桿（6~10x），單筆虧損金額被
# 放大不少。改成滿分也只給 1.0x，槓桿仍照分數/實測波動率分級，但下單
# 本金一律不超過 TRADE_AMOUNT_USDT。
POSITION_SIZE_TIERS = [
    (90, 1.0),  # 4 項全過（滿分）：1.0x 基礎倉位（上限，不再額外放大）
    (80, 1.0),  # 高分：1.0x 基礎倉位
    (70, 0.6),  # 剛過門檻：0.6x 基礎倉位，小倉試錯
]

def get_position_multiplier(score: int) -> float:
    for threshold, mult in POSITION_SIZE_TIERS:
        if score >= threshold:
            return mult
    return 0.0

# --- 動態幣種輪替與本機 AI 輔助 ---
# 12→16→18 幣：想增加開倉機會時，擴大掃描範圍（讓更多幣種有機會出現達標
# 訊號），而不是放寬同一批幣的評分門檻（那樣會直接增加假突破機率）。
# API 負擔：報價是一次批次拿全部幣種，不隨幣數增加；K 線只對還沒進場/
# 待命/冷卻的幣種才逐一抓，18 幣比 16 幣每輪只多 2 次請求，遠低於
# Binance 合約 API 額度，ccxt 也開了 enableRateLimit 自動節流。
SYMBOL_ROTATION_COUNT = int(os.getenv("SYMBOL_ROTATION_COUNT", "18"))
SYMBOL_ROTATION_INTERVAL_SEC = int(os.getenv("SYMBOL_ROTATION_INTERVAL_SEC", "3600"))
# UNHEALTHY_SYMBOL_CHECK_INTERVAL_SEC：完整輪替（含AI+全池K線）最壞情況要
# 等 SYMBOL_ROTATION_INTERVAL_SEC（預設1小時）才會換牌，尚未持倉的候選觀察
# 名單如果在這段期間變得明顯不健康（流動性枯竭、24h暴漲暴跌、波動率長期
# 偏離可交易區間），不用等到下一次整點輪替才處理——只用當下 ticker 資料
# 判斷（不用額外呼叫 AI/抓K線，成本很低），每隔這個秒數就檢查一次，發現
# 就立刻換掉。已經有持倉的幣種不受影響，維持只等SL/TP/24h時間過濾出場。
UNHEALTHY_SYMBOL_CHECK_INTERVAL_SEC = int(os.getenv("UNHEALTHY_SYMBOL_CHECK_INTERVAL_SEC", "300"))
# 跟著 SYMBOL_ROTATION_COUNT 等比放大（12→6 是 1:2），維持多空對稱席次。
DIRECTIONAL_SIDE_COUNT = int(os.getenv("DIRECTIONAL_SIDE_COUNT", "9"))
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

# 可新開倉牌面：正績效幣種搭配高流動性主流合約。
# TAO 與近期反覆停損幣種不列入；已退出牌面的既有持倉仍會被管理。
# 這只是啟動後第一次幣種輪替（約 30 秒內）之前的起始清單，之後會被
# SymbolRotation.rotate() 依 SYMBOL_ROTATION_COUNT（18）覆寫，這裡先湊到
# 18 檔只是讓開機當下的訊號掃描範圍跟輪替後一致。
DEFAULT_SYMBOLS = [
    "ADA/USDT",
    "ARB/USDT",
    "AVAX/USDT",
    "BCH/USDT",
    "BNB/USDT",
    "BTC/USDT",
    "DOGE/USDT",
    "DOT/USDT",
    "ETC/USDT",
    "FIL/USDT",
    "LINK/USDT",
    "LTC/USDT",
    "OP/USDT",
    "SOL/USDT",
    "SUI/USDT",
    "TRX/USDT",
    "XLM/USDT",
    "XRP/USDT",
]

# --- 真實/測試網切換 ---
# 預設 true：一律使用 Binance Testnet（set_sandbox_mode），不管 BINANCE_API_KEY
# 是不是正式帳戶的金鑰都不會下真錢單。正式上線當天才手動改為 false，並確認
# .env 裡的 BINANCE_API_KEY/BINANCE_SECRET 已換成正式帳戶的金鑰。
USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "true"

# --- 每日虧損熔斷 ---
# 當日已實現虧損達帳戶餘額（今日起始值）的此比例時，暫停開新倉；
# 既有持倉的止損/止利仍正常運作，不受影響。隔天（台北時區）自動重置。
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "10.0"))

# --- API 認證 ---
# /api/manual_order、/api/manual_close、/api/toggle 等異動端點要求帶
# Authorization: Bearer <API_TOKEN>。未設定時（本機開發階段）不強制驗證，
# 但啟動時會印出警告——對外開放前務必設定。
API_TOKEN = os.getenv("API_TOKEN", "")

# --- Email 警報 ---
# 用 SMTP 寄送重大事件（開倉/平倉失敗、每日熔斷觸發等 DANGER 等級事件）。
# SMTP_USER/SMTP_PASSWORD 未設定時直接跳過通知，不影響交易流程。
# 若用 Gmail 寄信：SMTP_USER 是寄件用的 Gmail 帳號，SMTP_PASSWORD 要用
# Google 帳號「應用程式密碼」（App Password），不是登入密碼本身。
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "shudgai999@gmail.com")
