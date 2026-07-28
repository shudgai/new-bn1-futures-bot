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
ATR_LEVERAGE_TIERS = [
    (0.002, 10),   # 實測 ATR% < 0.20% → 10x
    (0.003, 8),    # < 0.30% → 8x
    (0.0045, 6),   # < 0.45% → 6x
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
# STOP_LOSS_MULTIPLIER 拉大回 2.0x ATR：1.2x 太緊，實測太容易被
# Testnet 流動性較淺導致的 MARK_PRICE 瞬間偏離雜訊掃出（例如 SOL
# 進場僅 38 秒就停損），給更多呼吸空間。
STOP_LOSS_MULTIPLIER = float(os.getenv("STOP_LOSS_MULTIPLIER", "2.0"))
# TAKE_PROFIT_MULTIPLIER 拉大到 4.0x：跟止損維持 1:2 風報比，同時
# 讓移動止利有更多空間可以跑，不會太早撞到停利天花板。
TAKE_PROFIT_MULTIPLIER = float(os.getenv("TAKE_PROFIT_MULTIPLIER", "4.0"))
# MIN_SL_DISTANCE_PCT：止損距離下限（佔進場價的比例），不管 ATR 倍數設多寬，
# 波動率本身很低的時候（實測 BTC/LINK/LTC/BNB/XRP 反推 ATR 只有 0.07%~0.21%），
# ATR×倍數算出來的止損距離還是會縮到很窄，一樣容易被雜訊掃出。用這個下限
# 保證止損距離不會低於此比例，止盈距離依 TAKE_PROFIT/STOP_LOSS 倍數比例同步放大。
MIN_SL_DISTANCE_PCT = float(os.getenv("MIN_SL_DISTANCE_PCT", "0.004"))

# --- 精準狙擊進場門檻 ---
# MIN_SCORE_THRESHOLD：4 項評分為 30/20/20/30，90 分等於強制要求 4 項全過，訊號極少。
# 71 分 = 至少要 3 項條件通過（基礎70分）再加上品質細分加分至少 1 分才能進場，
# 排除掉品質加分完全是 0 分、勉強壓線過關的最弱訊號。
MIN_SCORE_THRESHOLD = int(os.getenv("MIN_SCORE_THRESHOLD", "71"))
# PULLBACK_TIMEOUT_MINUTES：突破後等待回調的最長時間（延長至 25 分鐘，給價格充分回踩 KC 的時間）
PULLBACK_TIMEOUT_MINUTES = int(os.getenv("PULLBACK_TIMEOUT_MINUTES", "25"))
# PULLBACK_ZONE_PCT：回調到距 KC 通道 ±0.3% 範圍內才觸發進場（稍微放寬以提高成交率）
PULLBACK_ZONE_PCT = float(os.getenv("PULLBACK_ZONE_PCT", "0.003"))
# PULLBACK_TARGET_DEPTH：回調進場目標價，從 KC 上/下軌往 EMA20 均價再靠攏的比例。
# 0.0 = 只回踩到 KC 軌道（原本行為，等於進場價幾乎沒有比突破點低多少）；
# 1.0 = 回踩到 EMA20 均價才進場（價格更低、空間更大，但等到的機率也更低）。
# 設 0.5 取中間值：進場價往均價方向靠一半，換取更多上漲空間，同時不會太少訊號。
PULLBACK_TARGET_DEPTH = float(os.getenv("PULLBACK_TARGET_DEPTH", "0.5"))

# --- 品質濾網控制參數 (對齊 7 大條件) ---
# KELTNER_ATR_MULTIPLIER 調回 1.5：實測最早期(通道確實是1.5倍時)勝率
# 53~63%，通道被誤降到1.0倍之後勝率掉到13~17%——通道太窄代表「真突破」
# 的確認門檻變低，容易讓假突破混進來。進場價不再靠通道變窄來壓低，
# 改由下方 evaluate_signal() 一律用回踩機制決定（見 PULLBACK_TARGET_DEPTH）。
KELTNER_ATR_MULTIPLIER = float(os.getenv("KELTNER_ATR_MULTIPLIER", "1.5"))
# KELTNER_BREAKOUT_MARGIN_PCT 改為 0.0：close 超過 KC 上軌即算突破，不再要求額外距離（避免進場點過熱）
KELTNER_BREAKOUT_MARGIN_PCT = float(os.getenv("KELTNER_BREAKOUT_MARGIN_PCT", "0.0"))
KELTNER_MIN_VOLUME_RATIO = float(os.getenv("KELTNER_MIN_VOLUME_RATIO", "0.8"))  # 量能門檻提高至 0.8 倍均量，確保是真實突破
# FRESHNESS_DECAY_BARS：訊號新鮮度改成連續淡化，不是硬門檻。
# 原本用「40 根K棒內（8→20→40 根一路調寬）滿分、超過直接 0 分」的硬門檻，
# 但實測發現 core/strategy.py 的 SuperTrend 計算曾經有 bug（第0根 ATR 是
# NaN，遞迴棘輪邏輯遇到 NaN 比較永遠傳染下去，導致方向永遠卡在初始值、
# 從未真正翻轉——187 筆歷史交易 100% 都是多單、新鮮度 0/187 通過），修好
# bug 後用真實資料量測，KC突破/量能/RSI都到齊時，距離上次翻轉常態落在
# 20~40 根，硬門檻不管設多寬都容易卡在「差一點點」的邊界。改成翻轉剛
# 發生給滿分 30 分，隨根數線性淡化，到 FRESHNESS_DECAY_BARS 掃到 0 分，
# 讓「剛翻轉」跟「翻轉很久了」的差異真正反映在分數上，不是全有全無。
FRESHNESS_DECAY_BARS = int(os.getenv("FRESHNESS_DECAY_BARS", "120"))

# --- ADX 趨勢強度濾網（品質加分用，非強制門檻）---
# KC 突破配上低 ADX，是盤整期假突破的常見樣貌；但直接拿來當強制門檻風險
# 較高（可能大幅壓低訊號數量，又還沒有實測數據佐證合適的門檻值），所以
# 先併入 evaluate_signal() 的品質加分（E4），跟 ATR/RSI/量能三項同一套邏輯：
# ADX_QUALITY_MIN 以下不加分，ADX_QUALITY_FULL 以上視為滿分。
ADX_PERIOD = int(os.getenv("ADX_PERIOD", "14"))
ADX_QUALITY_MIN = float(os.getenv("ADX_QUALITY_MIN", "15"))
ADX_QUALITY_FULL = float(os.getenv("ADX_QUALITY_FULL", "30"))

# --- 動態 RSI 濾網 ---
RSI_LONG_THRESHOLD = int(os.getenv("RSI_LONG_THRESHOLD", "51"))
RSI_SHORT_THRESHOLD = int(os.getenv("RSI_SHORT_THRESHOLD", "49"))

# --- 大週期趨勢總指揮 ---
TREND_FILTER_TIMEFRAME = os.getenv("TREND_FILTER_TIMEFRAME", "1h")
TREND_FILTER_EMA_PERIOD = int(os.getenv("TREND_FILTER_EMA_PERIOD", "50"))

# --- ATR 移動停利（chandelier exit，正式上線帳戶 BinanceTestnetAccount 使用）---
# 實測 328 筆歷史交易發現，中位數「進場後最大有利幅度」只有 0.23%，
# 47.5% 連 0.25% 都碰不到、只有 16.7% 能碰到 0.5%——固定百分比門檻對
# 大部分幣種根本啟動不了，本來有一點小獲利的單子鎖不到利，最後反轉
# 坐成不小的停損。改成「從進場後出現過的最高價（多單）/最低價（空單）
# 回吐 CHANDELIER_ATR_MULT 倍 ATR」：不用等固定百分比，只要創新高就有
# 新的止損保護，回吐幅度用該幣種自己的 ATR 衡量，天生就對每個幣的正常
# 波動範圍做了縮放，不會像百分比那樣同一個數字卻對不同幣鬆緊不一。
CHANDELIER_ATR_MULT = float(os.getenv("CHANDELIER_ATR_MULT", "0.5"))

# --- 趨勢反轉收緊止損（不是獨立平倉路徑）---
# 持倉中的幣種 SuperTrend 方向（用已收盤K棒算）反轉時，不會直接市價平倉
# （那樣會變成第二套跟移動止損互搶的出場邏輯），而是算出一個「反轉當下
# 價格 ± REVERSAL_EXIT_ATR_MULT 倍 ATR」的候選止損價，丟進跟保本鎖、
# ATR 移動停利同一套「取最嚴格候選」的邏輯裡一起比。部位還沒獲利、移動
# 停利還沒發揮作用時，這個候選通常最緊，正好補上「虧損單只能等固定止損
# 吃到底」的空窗期；一旦移動停利已經在運作（部位已經獲利、止損推得比它
# 更緊），這個候選就會直接被比下去，不會互相打架。
REVERSAL_EXIT_ATR_MULT = float(os.getenv("REVERSAL_EXIT_ATR_MULT", "0.5"))
# 持倉的 SuperTrend 方向不用跟主迴圈一樣每 5 秒重算一次——5 分K本身
# 每 5 分鐘才會真的變化，用較低頻率檢查即可，避免浪費 API 呼叫。
POSITION_TREND_CHECK_INTERVAL_SEC = float(os.getenv("POSITION_TREND_CHECK_INTERVAL_SEC", "90"))

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

# --- 急殺/急拉辨識：短時間內劇烈波動時，暫停收緊移動止利 ---
# 觀察到多筆持倉在幾分鐘內同時觸發止損，事後價格常常又漲回去——
# 這是「洗盤瞬間把止損位置一次掃過」的典型模式。根因是移動止利想
# 把止損調緊時，價格已經在瞬間急殺/急拉中衝過新止損價，導致保護單
# 被拒（Order would immediately trigger）或直接觸發，等於在最劇烈
# 的那一下被迫出場。偵測到短窗口內的劇烈波動時，暫停「收緊止損」
# 這個動作（原本已設定好的止損不變、不撤銷，只是不再往上調緊），
# 給洗盤留一點緩衝空間，等波動平息再恢復正常移動止利。
# FLASH_MOVE_WINDOW_SEC：觀察窗口（秒）
FLASH_MOVE_WINDOW_SEC = float(os.getenv("FLASH_MOVE_WINDOW_SEC", "60"))
# FLASH_MOVE_THRESHOLD_PCT：窗口內逆勢波動超過此比例，視為急殺/急拉
FLASH_MOVE_THRESHOLD_PCT = float(os.getenv("FLASH_MOVE_THRESHOLD_PCT", "0.015"))

# --- 進場緩衝期：剛進場的短時間內，止損暫時放寬，過了就收緊回正常距離 ---
# 實測 SOL/USDT 一筆進場僅 38 秒就被止損掃出，但畫面追蹤的價格根本沒跌到
# 止損價——保護單觸發用的是 MARK_PRICE，在流動性較淺的環境下容易跟顯示
# 的成交價瞬間出現落差，剛進場時特別容易被這種雜訊掃到。
# 給進場後 ENTRY_GRACE_SECONDS 秒的緩衝，止損距離額外加寬
# ENTRY_GRACE_EXTRA_ATR 倍 ATR，緩衝期一過就收緊回原本設定的正常距離。
# 注意：緩衝期內止損變寬，代表如果是真的走勢不對（不是雜訊），
# 虧損上限會比正常大，是用多承擔一點初期風險換取不被雜訊洗出場。
ENTRY_GRACE_SECONDS = float(os.getenv("ENTRY_GRACE_SECONDS", "60"))
ENTRY_GRACE_EXTRA_ATR = float(os.getenv("ENTRY_GRACE_EXTRA_ATR", "0.5"))

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
# 12→16→18 幣：想增加開倉機會時，擴大掃描範圍（讓更多幣種有機會出現達標
# 訊號），而不是放寬同一批幣的評分門檻（那樣會直接增加假突破機率）。
# API 負擔：報價是一次批次拿全部幣種，不隨幣數增加；K 線只對還沒進場/
# 待命/冷卻的幣種才逐一抓，18 幣比 16 幣每輪只多 2 次請求，遠低於
# Binance 合約 API 額度，ccxt 也開了 enableRateLimit 自動節流。
SYMBOL_ROTATION_COUNT = int(os.getenv("SYMBOL_ROTATION_COUNT", "18"))
SYMBOL_ROTATION_INTERVAL_SEC = int(os.getenv("SYMBOL_ROTATION_INTERVAL_SEC", "3600"))
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
