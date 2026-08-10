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

TRADE_AMOUNT_USDT = float(os.getenv("TRADE_AMOUNT_USDT", "75.0"))
# 每筆預估最大淨虧損（SL距離 + 雙邊taker fee + 單邊滑價）；<=0 表示停用。
MAX_TRADE_RISK_USDT = float(os.getenv("MAX_TRADE_RISK_USDT", "0.50"))

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
MIN_TRADE_USDT = float(os.getenv("MIN_TRADE_USDT", "5.0"))
# TEST_BUDGET_CAP_USDT：測試階段用，把「可用預算」暫時封頂在這個金額，
# 不管 Testnet 帳戶實際餘額有多少（例如帳戶有 4600U，但先假裝只有 150U，
# 觀察在這個額度下實際能開滿幾槽）。設 0（或不設）代表不封頂，直接用
# 帳戶真實可用餘額——正式上線時把這個值改回 0（或整個移除環境變數）即可，
# 不用再改程式碼。
TEST_BUDGET_CAP_USDT = float(os.getenv("TEST_BUDGET_CAP_USDT", "0"))
# STOP_LOSS_MULTIPLIER / TAKE_PROFIT_MULTIPLIER
# 調整為 2.5 ATR：更寬鬆的初始止損距離，減少被掃出的機率
STOP_LOSS_MULTIPLIER = float(os.getenv("STOP_LOSS_MULTIPLIER", "2.5"))
TAKE_PROFIT_MULTIPLIER = float(os.getenv("TAKE_PROFIT_MULTIPLIER", "3.0"))
DISABLE_TAKE_PROFIT = os.getenv("DISABLE_TAKE_PROFIT", "true").lower() == "true"
# 是否在交易所端掛出「初始虧損停損」條件單。停用時仍會在本地保留
# 計算出的 SL 作為觀察線，並由 MAX_ACCEPTABLE_LOSS_PCT 控制最終強制退出；
# 獲利後的移動保本／移動停利不受此開關影響。
ENABLE_EXCHANGE_INITIAL_STOP_LOSS = os.getenv(
    "ENABLE_EXCHANGE_INITIAL_STOP_LOSS", "true"
).lower() == "true"
# 最大可接受虧損百分比：只有虧損超過此值才會觸發停損平倉
# 例如 -0.02 表示允許虧損最多 2%，超過 2% 虧損才會平倉；-0.05 表示允許虧損最多 5%
# 設為負值時代表最大允許虧損；設為 0 時，碰到本地 SL 觀察線就平倉。
MAX_ACCEPTABLE_LOSS_PCT = float(os.getenv("MAX_ACCEPTABLE_LOSS_PCT", "0"))
ENABLE_TREND_FOLLOW_EXIT = os.getenv("ENABLE_TREND_FOLLOW_EXIT", "false").lower() == "true"
ENABLE_STRONG_TRIGGER_AUTO_CLOSE = os.getenv("ENABLE_STRONG_TRIGGER_AUTO_CLOSE", "false").lower() == "true"
MA7_EXIT_TIMEFRAME = os.getenv("MA7_EXIT_TIMEFRAME", "1m")
# MA7 單獨反轉容易在進場後 4~8 分鐘因正常震盪砍倉。只有這一種
# 「非結構性」出場需要先持倉滿10分鐘，且逆向幅度達0.20%或0.5倍 MA7_EXIT_TIMEFRAME ATR
# （取較大者）才執行；EMA20緩衝帶＋前低/前高同時失守仍立即退出。
MA7_EXIT_MIN_HOLD_SEC = float(os.getenv("MA7_EXIT_MIN_HOLD_SEC", "600"))
MA7_EXIT_MIN_ADVERSE_PCT = float(os.getenv("MA7_EXIT_MIN_ADVERSE_PCT", "0.002"))
MA7_EXIT_MIN_ADVERSE_ATR_MULT = float(os.getenv("MA7_EXIT_MIN_ADVERSE_ATR_MULT", "0.5"))
# 單根 K 線在 EMA20 錯誤側停留，並不是新增第二根收盤確認；舊邏輯
# 還可能把止損移到市價後方而等同立即平倉。預設停用，保留開關供影子測試。
ENABLE_SOFT_WARNING_TIGHTEN = os.getenv("ENABLE_SOFT_WARNING_TIGHTEN", "false").lower() == "true"
# 盤中投影在 08/01~08/02 的 7 筆樣本中佔 4 筆，全部都在尚未收線時用極小
# MA7 斜率搶跑，之後不是碰 SL 就是被 5m 反向防線關倉。預設只接受已收盤
# 訊號；若日後影子測試重新開啟，投影也必須有較明顯的 ATR 幅度。
MA7_EARLY_ENTRY_ENABLED = os.getenv("MA7_EARLY_ENTRY_ENABLED", "false").lower() == "true"
MA7_EARLY_MIN_ATR_MULT = float(os.getenv("MA7_EARLY_MIN_ATR_MULT", "0.05"))
MA7_EARLY_CONFIRM_SCANS = int(os.getenv("MA7_EARLY_CONFIRM_SCANS", "2"))
# 已收盤 MA7 必須在峰谷後連續兩根同向，且峰谷到最新值至少移動此 ATR
# 倍數；排除 BABY/NEAR 等只有最後幾個小數位變化的假轉彎。
MA7_REVERSAL_MIN_ATR_MULT = float(os.getenv("MA7_REVERSAL_MIN_ATR_MULT", "0.10"))
# 爆量微拐幅快速入口：仍只使用已收盤K棒，但峰谷後第一根確認即可進場。
# 必須同時有 1.5 倍均量，且拐幅限制在 0.02~0.20 ATR；一般低量訊號仍走
# 上面的兩根收線確認。這只放鬆觸發時機，不改 SL/TP、槓桿或倉位風控。
MA7_FAST_ENTRY_ENABLED = os.getenv("MA7_FAST_ENTRY_ENABLED", "true").lower() == "true"
MA7_FAST_MIN_ATR_MULT = float(os.getenv("MA7_FAST_MIN_ATR_MULT", "0.02"))
MA7_FAST_MAX_ATR_MULT = float(os.getenv("MA7_FAST_MAX_ATR_MULT", "0.20"))
MA7_FAST_MIN_VOLUME_RATIO = float(os.getenv("MA7_FAST_MIN_VOLUME_RATIO", "1.5"))
# 低波動時 MA7 入口會依近 6 小時平均 ATR 動態放寬，但仍保留絕對下限，
# 避免把幾乎沒有波動的價格雜訊誤認成有效轉彎。
MA7_DYNAMIC_ATR_FLOOR_PCT = float(os.getenv("MA7_DYNAMIC_ATR_FLOOR_PCT", "0.0006"))
# MA7仍在回撤/反彈時，不等拐頭便預掛在KC邊緣附近。偏移量讓限價位
# 稍微留在通道內側，同時由策略端保證LONG低於現價、SHORT高於現價。
MA7_BOTTOM_ENTRY_ENABLED = os.getenv("MA7_BOTTOM_ENTRY_ENABLED", "false").lower() == "true"
MA7_BOTTOM_OFFSET_ATR_MULT = float(os.getenv("MA7_BOTTOM_OFFSET_ATR_MULT", "0.05"))
# 底點/頂點預掛是在轉彎前承接，成交後需要時間消化正常回撤。寬限期內
# 屏蔽MA7、5m結構、15m EMA軟退出與軟性收緊；交易所原始SL仍持續有效。
MA7_BOTTOM_MIN_HOLD_SEC = float(os.getenv("MA7_BOTTOM_MIN_HOLD_SEC", "1800"))
# --- 無 MA7 的結構化進出場 ---
STRUCTURED_ENTRY_ENABLED = os.getenv("STRUCTURED_ENTRY_ENABLED", "true").lower() == "true"
# 08/05實績：MOMENTUM_CROSS 18答033%勝率、總損益-4.21U，且輸家平均持倉(21.4分)
# 反而比赏家(7.0分)還長，結構是反的；已正式停用。
ENABLE_MOMENTUM_CROSS_ENTRY = os.getenv("ENABLE_MOMENTUM_CROSS_ENTRY", "false").lower() == "true"
# 08/06實績：BREAKOUT改限價回踩進場後掛單成交率極低（0/6），先專心用
# SUPPORT_PULLBACK，停用 BREAKOUT 訊號產生。
ENABLE_BREAKOUT_ENTRY = os.getenv("ENABLE_BREAKOUT_ENTRY", "true").lower() == "true"
STRUCTURED_VOLUME_MIN_RATIO = float(os.getenv("STRUCTURED_VOLUME_MIN_RATIO", "1.0"))
STRUCTURED_SWING_LOOKBACK = int(os.getenv("STRUCTURED_SWING_LOOKBACK", "20"))
STRUCTURED_SUPPORT_NEAR_ATR = float(os.getenv("STRUCTURED_SUPPORT_NEAR_ATR", "0.40"))
STRUCTURED_SUPPORT_ORDER_TIMEOUT_SEC = float(os.getenv("STRUCTURED_SUPPORT_ORDER_TIMEOUT_SEC", "300"))
STRUCTURED_RSI_LONG_TRIGGER = float(os.getenv("STRUCTURED_RSI_LONG_TRIGGER", "51"))
STRUCTURED_RSI_SHORT_TRIGGER = float(os.getenv("STRUCTURED_RSI_SHORT_TRIGGER", "49"))
# SUPPORT_PULLBACK 必須有足夠實體，並由收線反轉或MACD動能改善確認；
# 成交改用支撐附近的短效 Maker，不在訊號K棒收線後用市價追價。
SUPPORT_PULLBACK_RSI_LONG_MIN = float(os.getenv("SUPPORT_PULLBACK_RSI_LONG_MIN", "51"))
SUPPORT_PULLBACK_RSI_SHORT_MAX = float(os.getenv("SUPPORT_PULLBACK_RSI_SHORT_MAX", "49"))
# 回踩反轉不是追價策略；RSI 已進入極端區時繼續順勢進場，通常是在低點追空
# 或高點追多。上下限同時存在，讓 RSI 必須落在健康動能區間。
SUPPORT_PULLBACK_RSI_LONG_MAX = float(os.getenv("SUPPORT_PULLBACK_RSI_LONG_MAX", "62"))
SUPPORT_PULLBACK_RSI_SHORT_MIN = float(os.getenv("SUPPORT_PULLBACK_RSI_SHORT_MIN", "38"))
SUPPORT_PULLBACK_MIN_BODY_ATR_MULT = float(os.getenv("SUPPORT_PULLBACK_MIN_BODY_ATR_MULT", "0.10"))
SUPPORT_PULLBACK_MAKER_OFFSET_ATR_MULT = float(os.getenv("SUPPORT_PULLBACK_MAKER_OFFSET_ATR_MULT", "0.05"))
SUPPORT_PULLBACK_MIN_VOLUME_RATIO = float(os.getenv("SUPPORT_PULLBACK_MIN_VOLUME_RATIO", "0.30"))
SUPPORT_PULLBACK_MAX_VOLUME_RATIO = float(os.getenv("SUPPORT_PULLBACK_MAX_VOLUME_RATIO", "1.20"))
SUPPORT_PULLBACK_LOCATION_MEMORY_BARS = int(os.getenv("SUPPORT_PULLBACK_LOCATION_MEMORY_BARS", "3"))
SUPPORT_PULLBACK_CONFIRM_MEMORY_BARS = int(os.getenv("SUPPORT_PULLBACK_CONFIRM_MEMORY_BARS", "2"))
# 觸價不等於承接成功：回收確認避免 Maker 限價單專接落刀。
SUPPORT_PULLBACK_RECLAIM_ATR_MULT = float(os.getenv("SUPPORT_PULLBACK_RECLAIM_ATR_MULT", "0.05"))
SUPPORT_PULLBACK_RECLAIM_MIN_SEC = float(os.getenv("SUPPORT_PULLBACK_RECLAIM_MIN_SEC", "10"))
SUPPORT_PULLBACK_MAX_ADVERSE_ATR_MULT = float(os.getenv("SUPPORT_PULLBACK_MAX_ADVERSE_ATR_MULT", "0.35"))
# 紙上模式預設模擬真實 resting Maker：價格穿越即按掛單價成交。只有刻意
# 做「觸價後再確認」對照實驗時才開啟舊回收模式。
PAPER_SUPPORT_PULLBACK_REQUIRE_RECLAIM = os.getenv(
    "PAPER_SUPPORT_PULLBACK_REQUIRE_RECLAIM", "false"
).lower() == "true"
TREND_EXTENSION_MIN_ROOM_PCT = float(os.getenv("TREND_EXTENSION_MIN_ROOM_PCT", "0.012"))
TREND_EXTENSION_MIN_VOLUME_RATIO = float(os.getenv("TREND_EXTENSION_MIN_VOLUME_RATIO", "0.60"))
TREND_EXTENSION_MIN_BODY_ATR_MULT = float(os.getenv("TREND_EXTENSION_MIN_BODY_ATR_MULT", "0.20"))
MIN_ENTRY_PROFIT_ROOM_PCT = float(os.getenv("MIN_ENTRY_PROFIT_ROOM_PCT", "0.0040"))
# 實驗模式可停用 BOUNCE 淨風報比攔截，保留門檻方便之後用實績重新啟用。
STRUCTURED_NET_RR_FILTER_ENABLED = os.getenv(
    "STRUCTURED_NET_RR_FILTER_ENABLED", "false"
).lower() == "true"
STRUCTURED_MIN_NET_REWARD_RISK = float(os.getenv("STRUCTURED_MIN_NET_REWARD_RISK", "1.0"))
BOUNCE_CAPTURE_MIN_RATIO = float(os.getenv("BOUNCE_CAPTURE_MIN_RATIO", "0.75"))
BOUNCE_CAPTURE_MAX_RATIO = float(os.getenv("BOUNCE_CAPTURE_MAX_RATIO", "0.80"))
# 反彈單若長時間連交易成本等級的順向波動都沒有，代表承接／反壓並未延續；
# 在仍處於虧損時提早退出，不等待完整硬停損。
BOUNCE_NO_FOLLOW_THROUGH_SEC = max(0.0, float(os.getenv("BOUNCE_NO_FOLLOW_THROUGH_SEC", "3600")))
BOUNCE_NO_FOLLOW_THROUGH_MIN_MFE_PCT = max(
    0.0, float(os.getenv("BOUNCE_NO_FOLLOW_THROUGH_MIN_MFE_PCT", "0.0023"))
)

def get_bounce_capture_ratio(score: int) -> float:
    progress = min(1.0, max(0.0, (float(score or 75) - 75.0) / 16.0))
    return BOUNCE_CAPTURE_MIN_RATIO + progress * (
        BOUNCE_CAPTURE_MAX_RATIO - BOUNCE_CAPTURE_MIN_RATIO
    )
# 紙交易沒有真實委託簿，仍保留少量穿透才成交以模擬排隊，但避免 0.05%
# 的舊門檻讓短效 Maker 單過度難成交。
PAPER_MAKER_FILL_PENETRATION_PCT = float(os.getenv("PAPER_MAKER_FILL_PENETRATION_PCT", "0.0001"))
BREAKOUT_HARD_STOP_ATR_MULT = float(os.getenv("BREAKOUT_HARD_STOP_ATR_MULT", "2.0"))  # 放寬至2 ATR避免突破後正常回踩被掃
BREAKOUT_CANDLE_STOP_BUFFER_ATR = float(os.getenv("BREAKOUT_CANDLE_STOP_BUFFER_ATR", "0.1"))
# 連續幾根5m已收盤K棒實體跌回EMA20以下才算KC失敗，防止單根回踩誤觸
BREAKOUT_KC_FAIL_CONFIRM_BARS = int(os.getenv("BREAKOUT_KC_FAIL_CONFIRM_BARS", "2"))
BREAKOUT_TRAILING_ATR_MULT = float(os.getenv("BREAKOUT_TRAILING_ATR_MULT", "1.75"))
BREAKOUT_RR1_TARGET = float(os.getenv("BREAKOUT_RR1_TARGET", "1.5"))
BREAKOUT_RR2_TARGET = float(os.getenv("BREAKOUT_RR2_TARGET", "2.5"))
BREAKOUT_RR_CLOSE_FRACTION = float(os.getenv("BREAKOUT_RR_CLOSE_FRACTION", "0.5"))
STRUCTURED_EXIT_INTERVAL_SEC = float(os.getenv("STRUCTURED_EXIT_INTERVAL_SEC", "15"))
# BREAKOUT 突破後掾限價回踩目標：在 EMA20 + BREAKOUT_PULLBACK_ATR_MULT * ATR 處掾單
# 等奖市場回踩回到中軌附近再進場，避免在突破高點直接市價追買
# 0.5 ATR 表示限價設在 EMA20 上方0.5個ATR（窗口內德一點，避免就在等於 EMA20 時採購反而絢了外軌）
BREAKOUT_PULLBACK_ATR_MULT = float(os.getenv("BREAKOUT_PULLBACK_ATR_MULT", "0.5"))
# BREAKOUT 回踩掾單最長等候時間：超過此時間沒有回踩就撏單
# 突破动能強勁時幾乎不回踩，此時單子成交未必是好做法；分析後可調整
BREAKOUT_PULLBACK_TIMEOUT_SEC = float(os.getenv("BREAKOUT_PULLBACK_TIMEOUT_SEC", "300"))

# 非紙上模式下，主網訊號價與執行交易所最佳價偏差超過此比例即拒絕下單。
EXECUTION_PRICE_MAX_DEVIATION_PCT = float(os.getenv("EXECUTION_PRICE_MAX_DEVIATION_PCT", "0.005"))
ENABLE_TRAILING_SL = os.getenv("ENABLE_TRAILING_SL", "false").lower() == "true"
# 移動止損的 ATR 倍數（預設 3 倍 ATR，動態適應市場波動範圍）
TRAILING_SL_ATR_MULT = float(os.getenv("TRAILING_SL_ATR_MULT", "3.0"))
# 扣除進出場 taker 手續費後，止盈淨利 / 止損淨虧損不得低於此值。
MIN_NET_REWARD_RISK = float(os.getenv("MIN_NET_REWARD_RISK", "2.0"))
ENTRY_MIN_QUALITY_BONUS = int(os.getenv("ENTRY_MIN_QUALITY_BONUS", "3"))

# --- 三階段階梯移動停利 / 移動保本配置 ---
# ENABLE_TRAILING_STOP: 是否開啟三階段移動停利機制
ENABLE_TRAILING_STOP = os.getenv("ENABLE_TRAILING_STOP", "true").lower() == "true"
# 觸發門檻改用每筆進場 ATR：2.0 ATR 保本、3.5 ATR 轉 runner 並鎖住
# 1.5 ATR、5 ATR 啟動追蹤。避免正常回踩過早補保本掃掉剛起跑的部位。
TRAILING_TIER1_TRIGGER_ATR_MULT = float(os.getenv("TRAILING_TIER1_TRIGGER_ATR_MULT", "2.0"))
TRAILING_TIER2_TRIGGER_ATR_MULT = float(os.getenv("TRAILING_TIER2_TRIGGER_ATR_MULT", "3.5"))
TRAILING_TIER3_TRIGGER_ATR_MULT = float(os.getenv("TRAILING_TIER3_TRIGGER_ATR_MULT", "5.0"))
TRAILING_TIER2_LOCK_ATR_MULT = float(os.getenv("TRAILING_TIER2_LOCK_ATR_MULT", "2.0"))
# Tier 1 保本價除覆蓋雙邊 taker fee 外，再多留這段緩衝吸收價格精度誤差。
TRAILING_BREAK_EVEN_EXTRA_PCT = float(os.getenv("TRAILING_BREAK_EVEN_EXTRA_PCT", "0.0001"))
TRAILING_TIER3_CALLBACK_RATIO = float(os.getenv("TRAILING_TIER3_CALLBACK_RATIO", "0.30"))

USE_NATIVE_TRAILING_STOP = os.getenv("USE_NATIVE_TRAILING_STOP", "false").lower() == "true"
# CallbackRate 動態計算：callbackRate = atr_pct * 100 * NATIVE_TRAILING_ATR_RATE_FACTOR
# 例：ATR% = 1.0% → callbackRate = 1.0 * 100 * 0.015 * 10 ≈ 1.5%
# 實際公式：max(MIN, min(MAX, round(atr_pct * 100 * FACTOR, 1)))
NATIVE_TRAILING_ATR_RATE_FACTOR = float(os.getenv("NATIVE_TRAILING_ATR_RATE_FACTOR", "1.5"))
# Tier1 (保本起步)：callbackRate 寬一點，讓正常回踩有空間
NATIVE_TRAILING_TIER1_CALLBACK_MIN = float(os.getenv("NATIVE_TRAILING_TIER1_CALLBACK_MIN", "0.3"))
NATIVE_TRAILING_TIER1_CALLBACK_MAX = float(os.getenv("NATIVE_TRAILING_TIER1_CALLBACK_MAX", "3.0"))
# Tier2 (鎖利)：收緊一點，保住更多獲利
NATIVE_TRAILING_TIER2_CALLBACK_MIN = float(os.getenv("NATIVE_TRAILING_TIER2_CALLBACK_MIN", "0.2"))
NATIVE_TRAILING_TIER2_CALLBACK_MAX = float(os.getenv("NATIVE_TRAILING_TIER2_CALLBACK_MAX", "2.0"))
# Tier3 (極致追蹤)：最緊，只允許小幅回撤
NATIVE_TRAILING_TIER3_CALLBACK_MIN = float(os.getenv("NATIVE_TRAILING_TIER3_CALLBACK_MIN", "0.1"))
NATIVE_TRAILING_TIER3_CALLBACK_MAX = float(os.getenv("NATIVE_TRAILING_TIER3_CALLBACK_MAX", "1.5"))

# PROFIT_ALERT_GIVEBACK_RATIO：獲利了結參考提醒（💰⚠️圖示）——上面的三
# 階段移動停利維持原樣、自動執行不受影響，這個是額外疊加的提醒機制。
# 只要目前還有獲利（不管有沒有到 Tier1 門檻），且從進場至今的最高浮盈
# 回吐超過這個比例，就標記警訊，門檻刻意設在比 Tier3 的回撤比例（30%）
# 更早／更敏感一點。紙上交易帳戶（PaperAccount）會在警訊亮起後，抓
# 「下一次浮盈比前一次檢查回升」的那個瞬間立刻平倉——警訊亮起代表利潤
# 正在被侵蝕，與其等它繼續吐回去，不如把握警訊後難得的一次反彈把獲利
# 鎖住；BinanceTestnetAccount 目前這個旗標仍是純顯示，不會自動平倉。
PROFIT_ALERT_GIVEBACK_RATIO = float(os.getenv("PROFIT_ALERT_GIVEBACK_RATIO", "0.2"))
# 小於0.5%的歷史峰值不啟動「回吐後反彈平倉」，避免剛蓋過手續費就把
# 部位關掉；平倉當下另要求至少保留0.10%無槓桿淨空間。
PROFIT_ALERT_MIN_PEAK_PCT = float(os.getenv("PROFIT_ALERT_MIN_PEAK_PCT", "0.005"))
PROFIT_ALERT_MIN_NET_PCT = float(os.getenv("PROFIT_ALERT_MIN_NET_PCT", "0.001"))
# SOFT_WARNING_PERSIST_SEC：持倉持續處於「✗」（現價站上/跌破EMA20，但
# 還沒同時跌破/站上前低前高升級成「⛔」強訊號）超過這個秒數，代表方向
# 持續不利但還沒觸發5m強出場防線，此時把止損往進場價方向收緊一次
# （移到目前止損與進場價的中點，只會變緊不會變鬆），降低風險但不直接
# 平倉——介於「完全不管」跟「5m防線直接關倉」之間的折衷處理。
SOFT_WARNING_PERSIST_SEC = float(os.getenv("SOFT_WARNING_PERSIST_SEC", "300"))
# MIN_SL_DISTANCE_PCT：止損距離下限（佔進場價的比例），不管 ATR 倍數設多寬，
# 波動率本身很低的時候（實測 BTC/LINK/LTC/BNB/XRP 反推 ATR 只有 0.07%~0.21%），
# ATR×倍數算出來的止損距離還是會縮到很窄，一樣容易被雜訊掃出。用這個下限
# 保證止損距離不會低於此比例，止盈距離依 TAKE_PROFIT/STOP_LOSS 倍數比例同步放大。
MIN_SL_DISTANCE_PCT = float(os.getenv("MIN_SL_DISTANCE_PCT", "0.0015"))
# DISASTER_STOP_MULTIPLIER：額外的止損寬鬆倍數（乘以 STOP_LOSS_MULTIPLIER）
# 原本 1.5 表示 1.5x ATR × 1.5 = 2.25 ATR，現改為 1.0 表示只用 STOP_LOSS_MULTIPLIER 的基礎值
# 這樣搭配 STOP_LOSS_MULTIPLIER=2.5 時，總止損距離為 2.5 ATR（不再額外放寬）
DISASTER_STOP_MULTIPLIER = float(os.getenv("DISASTER_STOP_MULTIPLIER", "1.0"))

# --- BTC 大盤方向守門員 ---
# BTC_REGIME_FILTER_ENABLED：開啟後，BTC/USDT 1h SuperTrend 方向將作為
# 全體幣種的開倉方向守門——BTC 多頭只允許多單，BTC 空頭只允許空單。
# 這是防止「大盤偏多但 bot 大量開空」最有效的單一機制。
BTC_REGIME_FILTER_ENABLED = os.getenv("BTC_REGIME_FILTER_ENABLED", "false").lower() == "true"
# 預設禁止逆 BTC 1h SuperTrend 開倉；只在明確對照測試時允許扣分半倉。
BTC_REGIME_ALLOW_CONTRARY = os.getenv("BTC_REGIME_ALLOW_CONTRARY", "false").lower() == "true"
# BTC_REGIME_FLIP_BUFFER_BARS：BTC 1h SuperTrend 剛翻轉時，市場方向尚不
# 確定，容易產生假訊號。翻轉後的前 N 根 1h K棒內禁止開新倉，等方向確認。
BTC_REGIME_FLIP_BUFFER_BARS = int(os.getenv("BTC_REGIME_FLIP_BUFFER_BARS", "2"))
# 方向相反不再直接擋單：先扣分，仍達標者以較小倉位等待回踩確認。
BTC_REGIME_SCORE_PENALTY = max(0, int(os.getenv("BTC_REGIME_SCORE_PENALTY", "6")))
BTC_REGIME_ALLOCATION_FACTOR = min(
    1.0, max(0.0, float(os.getenv("BTC_REGIME_ALLOCATION_FACTOR", "0.5")))
)
# SYMBOL_1H_ST_FILTER_ENABLED：個幣 1h SuperTrend 方向過濾。
# 要求 5m SuperTrend 方向必須與該幣自己的 1h SuperTrend 方向一致才允許開倉。
# 這比「price vs EMA50」更準確，因為 1h SuperTrend 翻轉需要較長時間確認。
# 曾經允許高分訊號繞過此過濾（SYMBOL_1H_ST_FILTER_BYPASS_SCORE），但實測
# 繞過後逆勢進場的勝率明顯偏低，已取消繞過機制，不論分數高低一律要求
# 順著1H大方向。已禁用此過濾以增加開倉機會。
SYMBOL_1H_ST_FILTER_ENABLED = os.getenv("SYMBOL_1H_ST_FILTER_ENABLED", "false").lower() == "true"
# ENABLE_1H_EMA50_FILTER：是否啟用 1h EMA50 大週期趨勢過濾
ENABLE_1H_EMA50_FILTER = os.getenv("ENABLE_1H_EMA50_FILTER", "true").lower() == "true"
# 結構進場允許價格在 1h EMA50 附近小幅穿越，避免微小報價雜訊造成方向反覆拒單。
STRUCTURED_1H_EMA50_TOLERANCE_PCT = float(
    os.getenv("STRUCTURED_1H_EMA50_TOLERANCE_PCT", "0.002")
)

# --- 精準狙擊進場門檻 ---
# MIN_SCORE_THRESHOLD：初始突破評分固定為 100 分制：
# KC 30 + 量能 20 + RSI 20 + 新鮮度 18 + 品質 12。
# 不再讓新鮮度跟真正突破同為 30 分，避免只有「方向還沒翻轉」的舊趨勢
# 靠新鮮度灌成 91+ 高分。
MIN_SCORE_THRESHOLD = int(os.getenv("MIN_SCORE_THRESHOLD", "71"))
# STRONG_BREAKOUT_SCORE_THRESHOLD 保留給報表；90+ 試行現價 Post-Only 限價，
# 仍不使用市價單，其餘達標訊號依分數等待回踩。
STRONG_BREAKOUT_SCORE_THRESHOLD = int(os.getenv("STRONG_BREAKOUT_SCORE_THRESHOLD", "78"))
MIN_OPEN_SIGNAL_SCORE = int(os.getenv("MIN_OPEN_SIGNAL_SCORE", "71"))
# 最近交易權重最高；第 n 筆歷史交易權重為 decay**n（交易紀錄本身為新到舊）。
HISTORY_RECENCY_DECAY = min(1.0, max(0.1, float(os.getenv("HISTORY_RECENCY_DECAY", "0.8"))))
# 舊版 StrongBreakout 的 EMA50 限制保留作相容設定，目前不再用來分流市價單。
STRONG_BREAKOUT_EMA50_MAX_ATR_MULT = float(os.getenv("STRONG_BREAKOUT_EMA50_MAX_ATR_MULT", "4.0"))
# 突破候選等待「觸價 + 1m 收盤反轉確認」的最長時間。3 分鐘仍未完成就
# 視為本波時效已過；同方向 KC 突破重置前不得重新建立候選或掛單。
PULLBACK_TIMEOUT_MINUTES = float(os.getenv("PULLBACK_TIMEOUT_MINUTES", "10.0"))
ENTRY_LIMIT_TIMEOUT_SEC = float(os.getenv("ENTRY_LIMIT_TIMEOUT_SEC", "15"))
PULLBACK_TARGET_MAX_DRIFT_ATR = float(os.getenv("PULLBACK_TARGET_MAX_DRIFT_ATR", "0.25"))
# 回踩距離至少為 0.10 ATR；若 KC 到 EMA20 的完整空間仍不足，該突破不建候選。
PULLBACK_TARGET_MIN_ATR_MULT = float(os.getenv("PULLBACK_TARGET_MIN_ATR_MULT", "0.10"))
PULLBACK_RECLAIM_MIN_ATR = float(os.getenv("PULLBACK_RECLAIM_MIN_ATR", "0.05"))
PULLBACK_RETRY_COOLDOWN_SEC = float(os.getenv("PULLBACK_RETRY_COOLDOWN_SEC", "60"))
# 觸價後至少等一根完整 1m K 棒，確認止跌/遇阻再送短效 maker 單。
# 失效或逾時撤單後短暫冷卻，避免同一個失效訊號每 5 秒重建候選/掛單。
# 底層日誌節流仍保留，避免重複洗版。
# PULLBACK_ZONE_PCT：回調到距 KC 通道 ±0.3% 範圍內才觸發進場（稍微放寬以提高成交率）
PULLBACK_ZONE_PCT = float(os.getenv("PULLBACK_ZONE_PCT", "0.003"))
# 試行分層：90+ 不等待回踩，改送現價 Post-Only Maker；其餘分數越高，
# 等待的 KC→EMA20 回踩比例越淺。65–69 暫時保留原本 15%。
PULLBACK_TARGET_DEPTH_TIERS = [
    (90, float(os.getenv("PULLBACK_TARGET_DEPTH_90", "0.00"))),
    (80, float(os.getenv("PULLBACK_TARGET_DEPTH_80", "0.05"))),
    (70, float(os.getenv("PULLBACK_TARGET_DEPTH_70", "0.08"))),
    (MIN_SCORE_THRESHOLD, float(os.getenv("PULLBACK_TARGET_DEPTH_65", "0.15"))),
]

def get_pullback_target_depth(score: int) -> float:
    for threshold, depth in PULLBACK_TARGET_DEPTH_TIERS:
        if score >= threshold:
            return min(max(depth, 0.0), 1.0)
    return PULLBACK_TARGET_DEPTH_TIERS[-1][1]
# PULLBACK_SCORE_THRESHOLD：回調二次確認（confirm_pullback_entry）用的總分門檻。
# 55 -> 48：配合進場門檻放寬，同步調降回踩二次確認分級門檻。
PULLBACK_SCORE_THRESHOLD = int(os.getenv("PULLBACK_SCORE_THRESHOLD", "48"))

# --- 品質濾網控制參數 (對齊 7 大條件) ---
# KELTNER_ATR_MULTIPLIER 調回 1.5：實測最早期(通道確實是1.5倍時)勝率
# 53~63%，通道被誤降到1.0倍之後勝率掉到13~17%——通道太窄代表「真突破」
# 的確認門檻變低，容易讓假突破混進來。進場價不再靠通道變窄來壓低，
# 改由下方 evaluate_signal() 一律用回踩機制決定（見 PULLBACK_TARGET_DEPTH_TIERS）。
KELTNER_ATR_MULTIPLIER = float(os.getenv("KELTNER_ATR_MULTIPLIER", "1.5"))
# KELTNER_BREAKOUT_MARGIN_PCT 改為 0.0：close 超過 KC 上軌即算突破，不再要求額外距離（避免進場點過熱）
KELTNER_BREAKOUT_MARGIN_PCT = float(os.getenv("KELTNER_BREAKOUT_MARGIN_PCT", "0.0"))
KELTNER_MIN_VOLUME_RATIO = float(os.getenv("KELTNER_MIN_VOLUME_RATIO", "0.8"))  # 量能門檻提高至 0.8 倍均量，確保是真實突破
# BREAKOUT_CONFIRM_BARS：KC 突破需要「收盤確認」的防假突破機制。
BREAKOUT_CONFIRM_BARS = int(os.getenv("BREAKOUT_CONFIRM_BARS", "1"))
# POST_BREAKOUT_VOL_SUSTAIN_RATIO：突破後量能持續性確認，用於 confirm_pullback_entry()。
POST_BREAKOUT_VOL_SUSTAIN_RATIO = float(os.getenv("POST_BREAKOUT_VOL_SUSTAIN_RATIO", "0.6"))
# 逆向大實體K搭配爆量先列入觀察，不硬擋單；累積結果後再決定是否升級為硬條件。
ADVERSE_PULLBACK_VOLUME_SPIKE_RATIO = float(os.getenv("ADVERSE_PULLBACK_VOLUME_SPIKE_RATIO", "1.8"))
ADVERSE_PULLBACK_BODY_MIN_ATR_MULT = float(os.getenv("ADVERSE_PULLBACK_BODY_MIN_ATR_MULT", "0.25"))
# FRESHNESS_DECAY_BARS：訊號新鮮度改成連續淡化。
FRESHNESS_DECAY_BARS = int(os.getenv("FRESHNESS_DECAY_BARS", "120"))
# 初始突破的新鮮度只占 18/100；回踩確認仍保留原本 30 分健康度尺度，
# 避免修改初始評分時意外放寬或封死成交前的二次確認。
ENTRY_FRESHNESS_SCORE_MAX = int(os.getenv("ENTRY_FRESHNESS_SCORE_MAX", "18"))
# MIN_FRESHNESS_SCORE：新鮮度子分數（滿分30）低於這個值直接擋單。設 15 分。
MIN_FRESHNESS_SCORE = int(os.getenv("MIN_FRESHNESS_SCORE", "15"))
# TREND_AGREE_EMA_MARGIN_PCT：強化 1h 大週期趨勢過濾的緩衝邊距。
# 現有機制「price < EMA50」就允許空單，當市場橫盤時任何小回調都會讓
# price 暫時低於 EMA50，導致空單被允許開倉。
# 新增此邊距後，改為要求「price < EMA50 × (1 - MARGIN)」才算真正看跌，
# 確保價格需要明顯跌破 EMA50 而不只是輕碰。設 0.001 = 需跌破 EMA50 的 0.1% 以下。
TREND_AGREE_EMA_MARGIN_PCT = float(os.getenv("TREND_AGREE_EMA_MARGIN_PCT", "0.001"))

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
ADX_MANDATORY_MIN = float(os.getenv("ADX_MANDATORY_MIN", "10.0"))  # 硬性最低 ADX 門檻，低於此直接 HOLD
ADX_QUALITY_MIN = float(os.getenv("ADX_QUALITY_MIN", "15"))
ADX_QUALITY_FULL = float(os.getenv("ADX_QUALITY_FULL", "30"))
# WEAK_ENERGY_ADX_THRESHOLD：進場當下 ADX 低於這個門檻（動能偏弱/中等，
# 可能已經在這波行情的末端）時，槓桿不管分數/波動率算出來的上限多高，
# 一律封頂在 WEAK_ENERGY_LEVERAGE_CAP，避免用高槓桿賭一個動能不夠強的
# 訊號。原本用 ADX_QUALITY_MIN(15) 當門檻，但實測 ONDO/USDT ADX=20.0
# 這種「勉強及格但不算強勢」（ADX_QUALITY_FULL是30，20只是剛過及格線）
# 一樣遇到窄幅雜訊盤整停損，故獨立成專屬常數並提高到22，跟只影響評分
# 公式的 ADX_QUALITY_MIN 脫鉤，才能單獨往上調不影響評分。
WEAK_ENERGY_ADX_THRESHOLD = float(os.getenv("WEAK_ENERGY_ADX_THRESHOLD", "22"))
WEAK_ENERGY_LEVERAGE_CAP = int(os.getenv("WEAK_ENERGY_LEVERAGE_CAP", "3"))
# ADX_DECLINE_LOOKBACK_BARS：實測 AAVE/USDT 07/28 14:48 這筆進場，往前
# 回看 8 根 5 分K，ADX 從 19.51 一路降到 14.67 才進場——SuperTrend 方向
# 還沒翻轉、新鮮度分數也還算高，但 ADX 連續下滑代表動能早就在退潮，是
# 典型的「末端趨勢」樣貌，只是新鮮度（看 SuperTrend 翻轉）量不到。這裡
# 額外用「ADX 現在比 N 根K棒前低，且已經低於 ADX_QUALITY_MIN」當強制
# 門檻，專門抓這種「方向沒變但動能已經在衰退」的情況，跟品質加分（軟性
# 只影響排序）分開，是硬性擋單。
ADX_DECLINE_LOOKBACK_BARS = int(os.getenv("ADX_DECLINE_LOOKBACK_BARS", "6"))
ADX_DECLINE_MIN_DROP = float(os.getenv("ADX_DECLINE_MIN_DROP", "2.0"))
ADX_DECLINE_MIN_DROP_RATIO = float(os.getenv("ADX_DECLINE_MIN_DROP_RATIO", "0.08"))
# KC_TOUCH_LOOKBACK_BARS：KC回踩觸碰確認原本只認前3根已收盤K棒（防止拿
# 很久以前的回調來當現在的轉彎），只允許有時效的 KC 回踩支持當前
# 剛確立的 MA7 局部峰谷。
KC_TOUCH_LOOKBACK_BARS = int(os.getenv("KC_TOUCH_LOOKBACK_BARS", "6"))
# ADX_DECLINE_LOOKBACK_BARS_1H：同一套「ADX 現在比 N 根K棒前低，且已經
# 低於 ADX_QUALITY_MIN」邏輯，但改看 1h K線——5分K的新鮮度/ADX檢查只能
# 看到「這根5分K的小趨勢夠不夠新」，看不出「大週期本身是不是也已經在
# 做頭/做底」。用同一批 update_1h_trend_cache() 已經抓到的1h K線重算，
# 不用額外呼叫API。
ADX_DECLINE_LOOKBACK_BARS_1H = int(os.getenv("ADX_DECLINE_LOOKBACK_BARS_1H", "6"))
# EMA_EXTENSION_MAX_ATR_MULT：價格距離 EMA20 太遠（用 ATR 正規化衡量）
# 代表這波已經漲/跌很多才追進場，均值回歸風險高，容易一進場就拉回。
# 將上限收緊為 2.5 倍 ATR，避免在價格已明顯背離短期均線時開倉。
EMA_EXTENSION_MAX_ATR_MULT = float(os.getenv("EMA_EXTENSION_MAX_ATR_MULT", "2.5"))

# --- 動態 RSI 濾網 ---
RSI_LONG_THRESHOLD = int(os.getenv("RSI_LONG_THRESHOLD", "51"))
RSI_SHORT_THRESHOLD = int(os.getenv("RSI_SHORT_THRESHOLD", "49"))
RSI_LONG_MAX = float(os.getenv("RSI_LONG_MAX", "75"))
RSI_SHORT_MIN = float(os.getenv("RSI_SHORT_MIN", "25"))

# --- 大週期趨勢總指揮 ---
TREND_FILTER_TIMEFRAME = os.getenv("TREND_FILTER_TIMEFRAME", "1h")
TREND_FILTER_EMA_PERIOD = int(os.getenv("TREND_FILTER_EMA_PERIOD", "50"))

# --- 以下 TRAILING_* / _PROFIT_TIER_FLOOR 是百分比制移動止利：
# USE_NATIVE_TRAILING_STOP=false 時 BinanceTestnetAccount 用這套，
# PaperAccount（純本地模擬，沒有真實交易所可掛原生Trailing）固定用這套。---
# 小幅獲利曾達0.30%後，若回落至0.20%便平倉；執行時仍以雙邊費用加
# 預估滑點作最低安全線，避免鎖到帳面獲利、實際淨虧。
EARLY_PROFIT_GUARD_TRIGGER_PCT = float(os.getenv("EARLY_PROFIT_GUARD_TRIGGER_PCT", "0.003"))
EARLY_PROFIT_GUARD_EXIT_PCT = float(os.getenv("EARLY_PROFIT_GUARD_EXIT_PCT", "0.002"))
# 結構反彈單的獲利窗口通常較短，較一般單提早保護；退出線仍必須高於
# 雙邊手續費與預估滑價，避免帳面小利實際淨虧。
BOUNCE_EARLY_PROFIT_GUARD_TRIGGER_PCT = float(
    os.getenv("BOUNCE_EARLY_PROFIT_GUARD_TRIGGER_PCT", "0.0023")
)
BOUNCE_EARLY_PROFIT_GUARD_EXIT_PCT = float(
    os.getenv("BOUNCE_EARLY_PROFIT_GUARD_EXIT_PCT", "0.0020")
)
TREND_EXTENSION_GUARD_TRIGGER_PCT = float(os.getenv("TREND_EXTENSION_GUARD_TRIGGER_PCT", "0.0045"))
TREND_EXTENSION_GUARD_EXIT_PCT = float(os.getenv("TREND_EXTENSION_GUARD_EXIT_PCT", "0.0030"))
TREND_EXTENSION_MIN_CAPTURE_RATIO = float(os.getenv("TREND_EXTENSION_MIN_CAPTURE_RATIO", "0.70"))
TRAILING_TRIGGER_PCT = float(os.getenv("TRAILING_TRIGGER_PCT", "0.0060"))
TRAILING_CALLBACK_PCT = float(os.getenv("TRAILING_CALLBACK_PCT", "0.0005"))
# 有 initial_risk 的策略單改用 R 倍數啟動移動停利。到 1.5R 才開始保護，
# 回吐空間保留 0.5R，因此啟動後至少鎖住約 1R；避免舊設定在 +1R 啟動、
# 回吐 0.75R 後只留下約 +0.25R，形成平均贏單遠小於完整 -1R 止損。
TRAILING_TRIGGER_R_MULT = float(os.getenv("TRAILING_TRIGGER_R_MULT", "1.5"))
TRAILING_CALLBACK_R_MULT = float(os.getenv("TRAILING_CALLBACK_R_MULT", "0.5"))
# CONTRARIAN_TRAILING_TRIGGER_PCT：逆勢承接底部買點（MA7_ContrarianBottomBuy）
# 專用、更早啟動的移動停利觸發門檻。
CONTRARIAN_TRAILING_TRIGGER_PCT = float(os.getenv("CONTRARIAN_TRAILING_TRIGGER_PCT", "0.0060"))
# CONTRARIAN_POSITION_SIZE_MULTIPLIER：逆勢承接單的信心水準本來就比一般
# 順勢MA7拐頭低（是在跟SuperTrend/1h趨勢對作），用比較小的倉位承接，
# 就算反彈失敗被打回原趨勢方向，虧損金額也比較小。
CONTRARIAN_POSITION_SIZE_MULTIPLIER = float(os.getenv("CONTRARIAN_POSITION_SIZE_MULTIPLIER", "0.5"))
# TRAILING_MODE: 正式移動停利初期的峰值鎖定比例
#   conservative: 鎖60%（回吐40%）
#   balanced:     鎖55%（回吐45%）
#   aggressive:   鎖50%（回吐50%）
TRAILING_MODE = os.getenv("TRAILING_MODE", "balanced")
_TRAILING_PULLBACK_MAP = {"conservative": 0.60, "balanced": 0.55, "aggressive": 0.50}
TRAILING_PULLBACK_PCT = float(os.getenv("TRAILING_PULLBACK_PCT", str(_TRAILING_PULLBACK_MAP.get(TRAILING_MODE, 0.55))))

# --- 動態止利：依增利速度 + 利潤分級自動選擇回吐比例 ---
# 增利速度快 → aggressive（鎖50%）：給行情更多空間
# 增利速度慢 → conservative（鎖60%）：收緊保護
# SPEED_FAST_THRESHOLD: 增利速度 >= 此值（%/分鐘）判定為快
SPEED_FAST_THRESHOLD = float(os.getenv("SPEED_FAST_THRESHOLD", "0.0005"))   # 0.05%/min
# SPEED_SLOW_THRESHOLD: 增利速度 <= 此值（%/分鐘）判定為慢
SPEED_SLOW_THRESHOLD = float(os.getenv("SPEED_SLOW_THRESHOLD", "0.0001"))  # 0.01%/min

# --- 利潤分級鎖倉：利潤越高，鎖越緊，避免大幅回吐 ---
# 正式觸發（預設0.40%）時先依增利速度鎖50~60%，
# 保留較大的回檔空間，避免剛觸發就被雜訊掃出；觸發點本身
# 不拉高——拉高觸發點只會讓更多小賺的單子連鎖都鎖不到、直接變虧損，
# 讓「贏小賠大」更嚴重。真正該調的是觸發之後的鎖定爬升速度：
# 門檻整體拉開、封頂拉到 3.0%，配合今天拉寬的止損止盈（2.0x/4.0x
# ATR），讓利潤有更多空間往上跑，不要一點漲幅就被鎖死出場。
# (最低利潤%, 最低鎖倉比例) — 從高到低匹配，命中即停
_PROFIT_TIER_FLOOR = [
    (0.0300, 0.95),  # ≥3.00% 無槓桿利潤 → 至少鎖 95%
    (0.0200, 0.88),  # ≥2.00% → 至少鎖 88%
    (0.0150, 0.82),  # ≥1.50% → 至少鎖 82%
    (0.0100, 0.75),  # ≥1.00% → 至少鎖 75%
    (0.0060, 0.70),  # ≥0.60% → 至少鎖 70%
]

# --- 分批止盈參數 ---
# 到達利潤門檻時先平一部分，鎖住已賺的，剩餘繼續跑移動止利
PARTIAL_CLOSE_THRESHOLDS = [
    # (無槓桿利潤%, 平倉比例) — 從低到高依序觸發
    (0.10, 0.30),  # ≥10% → 先平 30%
    (0.20, 0.30),  # ≥20% → 再平 30%（累計60%）
]

import time as _time

def get_trailing_pullback_pct(peak_profit_pct: float, position_opened_at: float) -> float:
    """根據增利速度 + 利潤分級動態回傳止利回吐比例。

    先依增利速度決定基礎回吐比例，再用利潤分級下限收緊，
    確保高利潤時不會回吐太多。

    Args:
        peak_profit_pct: 歷史最高無槓桿利潤百分比
        position_opened_at: 持倉建立 timestamp（秒），用整段持倉時間計算
            平均增利速度；不能使用剛更新的峰值時間，否則 elapsed 幾乎為0，
            每次都會被誤判為快速增利。
    Returns:
        鎖倉比例（0.85 / 0.80 / 0.75 / 0.70 / 0.60）
    """
    # 1. 依增利速度決定基礎回吐比例
    elapsed_min = (_time.time() - position_opened_at) / 60.0
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

# NET_PROFIT_GUARANTEE_BUFFER: 保本線安全帶係數（佔進場價的比例）。
# 預留雙邊吃單手續費 0.10%、市價滑點與額外安全利潤，維持 0.35%。
NET_PROFIT_GUARANTEE_BUFFER = float(os.getenv("NET_PROFIT_GUARANTEE_BUFFER", "0.006"))

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
# 探索模式以半倉收集樣本，ATR 上限放寬至 0.8%；仍排除更極端的高波動幣。
MAX_ATR_PCT = float(os.getenv("MAX_ATR_PCT", "0.008"))
# MIN_ATR_PCT：探索池下限放寬至 0.05%，讓 ETH、XRP、LINK 等主流幣
# 留在監控範圍；更低波動仍視為缺乏足夠價格空間。
MIN_ATR_PCT = float(os.getenv("MIN_ATR_PCT", "0.0005"))

# --- 主流幣名單：交易範圍限縮 + 量縮背離繞過波動過低限制 ---
# market_candidates() 只會從這份名單裡挑選候選幣（見 symbol_rotation.py）。
# 名單原本是為了避開測試網對冷門幣報價/成交深度常跟真實行情脫節的問題
# （實測 KAITO/RLC 這類幣種曾出現測試網報價凍結、跟訊號偵測用的主網
# 價格差了好幾%的情況）；現在已經切到紙上交易模式，self.exchange 全程
# 都是真實主網行情，這個測試網報價問題不存在了，但「候選池不要太窄」
# 這點仍然成立，故擴大名單掃更多幣種：新增市值/知名度足夠的 ZEC、ONDO、
# ENA、ORDI、XMR、CFX、COTI 共7個，從35個增加到42個。仍然只挑基本面
# 有一定認知度的主流幣，不納入新上市/迷因幣（例如當時24h成交量前段的
# GIGGLE、KOMA、1000RATS、BANK 等），避免價格行為不穩定污染訊號品質。
#
# 同一份名單也用在量縮背離繞過波動過低限制：主流幣在窄幅盤整前常見
# 「價格仍創新高/新低，但成交量明顯萎縮」的量價背離型態——主力收手、
# 動能耗盡準備反轉的訊號，此時 ATR% 雖然偏低，但不是「無真實動能的
# 假突破」，跟 MIN_ATR_PCT 原本要防的雜訊盤整不是同一種情況，故允許
# 繞過波動過低限制（僅此一項，ATR過高/其餘過濾條件不受影響）。
MAINSTREAM_SYMBOLS = {
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT",
    "BCH/USDT", "LTC/USDT", "ARB/USDT", "ATOM/USDT", "NEAR/USDT",
    "TRX/USDT", "ETC/USDT", "FIL/USDT", "OP/USDT", "UNI/USDT",
    "AAVE/USDT", "XLM/USDT", "HBAR/USDT", "INJ/USDT", "SUI/USDT",
    "SEI/USDT", "RENDER/USDT", "WLD/USDT", "1000SHIB/USDT", "GALA/USDT",
    "SAND/USDT", "MANA/USDT", "APE/USDT", "CRV/USDT", "LDO/USDT",
    "ZEC/USDT", "ONDO/USDT", "ENA/USDT", "ORDI/USDT", "XMR/USDT",
    "CFX/USDT", "COTI/USDT",
}
# VOLUME_DIVERGENCE_LOOKBACK_BARS：拆成前後兩段各半，比較兩段的量能與
# 價格極值。
VOLUME_DIVERGENCE_LOOKBACK_BARS = int(os.getenv("VOLUME_DIVERGENCE_LOOKBACK_BARS", "16"))
# VOLUME_DIVERGENCE_MAX_RATIO：後段平均量能 <= 前段平均量能的此比例，才算
# 明顯萎縮（避免量能只是正常小幅波動就誤判為背離）。
VOLUME_DIVERGENCE_MAX_RATIO = float(os.getenv("VOLUME_DIVERGENCE_MAX_RATIO", "0.7"))
# 量縮背離強化過濾（已禁用，回復簡單版本以增加開倉機會）：
# 原本試過加入最後K棒極值、收盤壓力方向、ADX確認等條件，但篩選過嚴會減少開倉。
# 目前只用基礎量縮背離邏輯：價格創新高/低 + 成交量萎縮 = 允許繞過低波動限制。
# VOLUME_DIVERGENCE_LAST_BAR_EXTREME_REQUIRED = os.getenv("VOLUME_DIVERGENCE_LAST_BAR_EXTREME_REQUIRED", "true").lower() == "true"
# VOLUME_DIVERGENCE_CLOSE_PRESSURE_BARS = int(os.getenv("VOLUME_DIVERGENCE_CLOSE_PRESSURE_BARS", "3"))
# VOLUME_DIVERGENCE_MIN_ADX = float(os.getenv("VOLUME_DIVERGENCE_MIN_ADX", "15.0"))

# --- 動態倉位分配 (依訊號信心分數調整下單金額) ---
# 只有通過 MIN_SCORE_THRESHOLD 才會進場；分數越高代表 4 項條件符合越多，
# 給予更高倍數的下單金額（以 TRADE_AMOUNT_USDT 為基準，而非總資金比例，避免部位隨餘額增長滾雪球）。
# 每筆金額硬上限 = TRADE_AMOUNT_USDT（預設75）：原本 90 分以上會給到 1.5x
# （75U），但配合高分訊號常同時給到的高槓桿（6~10x），單筆虧損金額被
# 放大不少。改成滿分也只給 1.0x，槓桿仍照分數/實測波動率分級，但下單
# 本金一律不超過 TRADE_AMOUNT_USDT。
# 最低檔的門檻用 MIN_SCORE_THRESHOLD 本身，不要寫死數字：MIN_SCORE_THRESHOLD
# 之前從 71 調到 65，但這裡最低檔一直停在寫死的 70，導致 65~69 分的訊號
# 落在兩個門檻中間的空隙，get_position_multiplier() 找不到符合的檔位，
# 回傳 0.0 → 下單金額變成 0 → exchange.amount_to_precision() 丟出未捕捉
# 的交易所例外，把整個主迴圈拖垮、每輪重複炸同一個 symbol（實測
# DOGE/USDT 07/29 20:20 這筆就是這樣連續炸了好幾分鐘）。用
# MIN_SCORE_THRESHOLD 本身當最低檔門檻，兩者永遠對齊，以後調
# MIN_SCORE_THRESHOLD 不會再重新打開這個空隙。
POSITION_SIZE_TIERS = [
    (90, 1.0),  # 4 項全過（滿分）：1.0x 基礎倉位（上限，不再額外放大）
    (80, 1.0),  # 高分：1.0x 基礎倉位
    (MIN_SCORE_THRESHOLD, 0.6),  # 剛過門檻：0.6x 基礎倉位，小倉試錯
]

def get_position_multiplier(score: int) -> float:
    for threshold, mult in POSITION_SIZE_TIERS:
        if score >= threshold:
            return mult
    return 0.0

# --- 動態幣種輪替與本機 AI 輔助 ---
# 12→16→18→24 幣：想增加開倉機會時，擴大掃描範圍（讓更多幣種有機會出現達標
# 訊號），而不是放寬同一批幣的評分門檻（那樣會直接增加假突破機率）。
# API 負擔：報價是一次批次拿全部幣種，不隨幣數增加；K 線只對還沒進場/
# 待命/冷卻的幣種才逐一抓，18 幣比 16 幣每輪只多 2 次請求，遠低於
# Binance 合約 API 額度，ccxt 也開了 enableRateLimit 自動節流。
SYMBOL_ROTATION_COUNT = int(os.getenv("SYMBOL_ROTATION_COUNT", "24"))
SYMBOL_ROTATION_INTERVAL_SEC = int(os.getenv("SYMBOL_ROTATION_INTERVAL_SEC", "900"))
# UNHEALTHY_SYMBOL_CHECK_INTERVAL_SEC：完整輪替（含AI+全池K線）最壞情況要
# 等 SYMBOL_ROTATION_INTERVAL_SEC（預設15分鐘）才會換牌，尚未持倉的候選觀察
# 名單如果在這段期間變得明顯不健康（流動性枯竭、24h暴漲暴跌、波動率長期
# 偏離可交易區間），不用等到下一次整點輪替才處理——只用當下 ticker 資料
# 判斷（不用額外呼叫 AI/抓K線，成本很低），每隔這個秒數就檢查一次，發現
# 就立刻換掉。已經有持倉的幣種不受影響，維持只等SL/TP/24h時間過濾出場。
UNHEALTHY_SYMBOL_CHECK_INTERVAL_SEC = int(os.getenv("UNHEALTHY_SYMBOL_CHECK_INTERVAL_SEC", "300"))
# 跟著 SYMBOL_ROTATION_COUNT 等比放大（24→12 是 1:2），維持多空對稱席次。
DIRECTIONAL_SIDE_COUNT = int(os.getenv("DIRECTIONAL_SIDE_COUNT", "12"))
# 輪替候選池常因 ATR 與方向分數雙重過濾只剩個位數，監控分數降到40；真正進場仍需 MIN_OPEN_SIGNAL_SCORE，
# 因此只會增加持續觀察的幣，不會讓40分訊號直接下單。
DIRECTIONAL_MIN_SCORE = float(os.getenv("DIRECTIONAL_MIN_SCORE", "40"))
SYMBOL_MARKET_SCAN_LIMIT = int(os.getenv("SYMBOL_MARKET_SCAN_LIMIT", "100"))
SYMBOL_MIN_QUOTE_VOLUME = float(os.getenv("SYMBOL_MIN_QUOTE_VOLUME", "35000000"))
SYMBOL_ROTATION_MIN_SCORE_GAP = float(os.getenv("SYMBOL_ROTATION_MIN_SCORE_GAP", "5.0"))
SYMBOL_ROTATION_MAX_CHANGES = int(os.getenv("SYMBOL_ROTATION_MAX_CHANGES", "3"))
SYMBOL_MIN_LISTING_DAYS = int(os.getenv("SYMBOL_MIN_LISTING_DAYS", "7"))
SYMBOL_MAX_24H_CHANGE_PCT = float(os.getenv("SYMBOL_MAX_24H_CHANGE_PCT", "50.0"))
# 最近 20 筆中樣本已足且持續負期望的幣，先退出新倉輪替；這不是永久黑名單，
# 新資料改善或調整環境變數後即可重新入選。
SYMBOL_HISTORY_QUARANTINE_MIN_TRADES = int(os.getenv("SYMBOL_HISTORY_QUARANTINE_MIN_TRADES", "8"))
SYMBOL_HISTORY_QUARANTINE_MAX_AVG_PNL = float(os.getenv("SYMBOL_HISTORY_QUARANTINE_MAX_AVG_PNL", "-0.20"))
SYMBOL_HISTORY_QUARANTINE_MAX_STOP_RATE = float(os.getenv("SYMBOL_HISTORY_QUARANTINE_MAX_STOP_RATE", "0.40"))
# 同幣同方向連續硬停損代表目前市場型態不適合這套入口；短期停止再試，
# 不等累積到完整隔離所需的 8 筆樣本才處理。
CONSECUTIVE_STOP_COOLDOWN_COUNT = max(
    1, int(os.getenv("CONSECUTIVE_STOP_COOLDOWN_COUNT", "2"))
)
CONSECUTIVE_STOP_COOLDOWN_SEC = max(
    0.0, float(os.getenv("CONSECUTIVE_STOP_COOLDOWN_SEC", "43200"))
)
# 樣本不足或方向績效仍為負時不移出監控池，改用較小倉位探索。
EXPLORATION_MIN_DIRECTION_TRADES = int(os.getenv("EXPLORATION_MIN_DIRECTION_TRADES", "3"))
EXPLORATION_POSITION_SIZE_MULTIPLIER = min(
    1.0, max(0.1, float(os.getenv("EXPLORATION_POSITION_SIZE_MULTIPLIER", "0.5")))
)
AI_ADVISOR_ENABLED = os.getenv("AI_ADVISOR_ENABLED", "true").lower() == "true"
AI_ADVISOR_URL = os.getenv("AI_ADVISOR_URL", "http://127.0.0.1:8888/v1/chat/completions")
AI_ADVISOR_TIMEOUT_SEC = float(os.getenv("AI_ADVISOR_TIMEOUT_SEC", "30"))
# 歷史樣本尚少時只讓 AI 微調排序，量化條件仍是主決策。
AI_ADVISOR_WEIGHT = float(os.getenv("AI_ADVISOR_WEIGHT", "0.05"))

# 高流動性候選池。已退場或近期反覆停損的
# TAO/FET/APT/WIF/1000PEPE/ETH 不放回自動候選池。
SYMBOL_CANDIDATE_POOL = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "NEAR/USDT", "AVAX/USDT",
    "SUI/USDT", "ONDO/USDT", "AAVE/USDT", "LINK/USDT", "LTC/USDT",
    "DOGE/USDT", "BCH/USDT", "UNI/USDT", "OP/USDT", "ARB/USDT",
    "BNB/USDT",
]

# 實績已確認為負期望的幣種暫停新倉；既有持倉仍由原 SL/TP 管理。
ENTRY_DISABLED_SYMBOLS = {
    symbol.strip()
    for symbol in os.getenv(
        "ENTRY_DISABLED_SYMBOLS",
        "BNB/USDT,HYPE/USDT,SUI/USDT,SOL/USDT",
    ).split(",")
    if symbol.strip()
}

# 停用幣不可再占候選池名額；環境變數新增的停用幣也同步生效。
SYMBOL_CANDIDATE_POOL[:] = [
    symbol for symbol in SYMBOL_CANDIDATE_POOL if symbol not in ENTRY_DISABLED_SYMBOLS
]

# 可新開倉牌面：正績效幣種搭配高流動性主流合約。
# TAO 與近期反覆停損幣種不列入；已退出牌面的既有持倉仍會被管理。
# 這只是啟動後第一次幣種輪替（約 30 秒內）之前的起始清單，之後會被
# SymbolRotation.rotate() 依 SYMBOL_ROTATION_COUNT（24）覆寫，這裡先湊到
# 24 檔只是讓開機當下的訊號掃描範圍跟輪替後一致。
DEFAULT_SYMBOLS = sorted(MAINSTREAM_SYMBOLS)
DEFAULT_SYMBOLS[:] = [
    symbol for symbol in DEFAULT_SYMBOLS if symbol not in ENTRY_DISABLED_SYMBOLS
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

# --- 分批掛單 (DCA) 配置 ---
ENABLE_DCA_LIMIT = os.getenv("ENABLE_DCA_LIMIT", "false").lower() == "true"
DCA_STAGE_DEPTHS = [float(d) for d in os.getenv("DCA_STAGE_DEPTHS", "0.03,0.05").split(",") if d.strip()]
DCA_LIMIT_TIMEOUT_SEC = float(os.getenv("DCA_LIMIT_TIMEOUT_SEC", "14400.0"))


