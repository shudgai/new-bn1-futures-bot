"""
core/services/execution_manager.py
==================================
三層流水線之【執行層 (Execution Layer，唯一通道)】
職責：
- 全專案唯一合法的開平倉下單執行器。
- 嚴格封閉外部任意模組直接呼叫交易所下單 API。
- 主循環每秒跑標準流程：
  1. 抓取數據與計算指標 (DataPipeline)
  2. 若有持倉：只問 ExitGate -> 若觸發則呼叫 broker.close_position
  3. 若無持倉：只問 EntryGate -> 若通過則呼叫 broker.open_position
"""
import logging
import math
import time
from typing import Dict, Any, Optional
import pandas as pd

from core.services.data_pipeline import DataPipeline, MarketDataSnapshot
from core.gates.unified_pipeline_gates import ExitGate, EntryGate
from core.services.demon_coin_defense import DemonCoinDefenseController

logger = logging.getLogger("ExecutionManager")


# =========================================================================
# 1. 嚴格過濾「均線黏合假金叉 / 假死叉 (Buffer Distance Protection)」
# =========================================================================
def is_valid_dead_cross(ma3_curr, ma3_prev, ma15_curr, ma15_prev, current_atr):
    """ 多頭離場：MA3 向下貫穿 MA15 深度必須 >= 0.15 * ATR """
    atr_val = float(current_atr or 0.0)
    raw_cross = (ma3_curr < ma15_curr) and (ma3_prev >= ma15_prev)
    cross_depth = (ma15_curr - ma3_curr)
    return bool(raw_cross and (cross_depth >= 0.15 * atr_val))

def is_valid_golden_cross(ma3_curr, ma3_prev, ma15_curr, ma15_prev, current_atr):
    """ 空頭離場：MA3 向上貫穿 MA15 深度必須 >= 0.15 * ATR """
    atr_val = float(current_atr or 0.0)
    raw_cross = (ma3_curr > ma15_curr) and (ma3_prev <= ma15_prev)
    cross_depth = (ma3_curr - ma15_curr)
    return bool(raw_cross and (cross_depth >= 0.15 * atr_val))


# =========================================================================
# 🎯 0. 嚴格過濾「真谷底 (True Bottom)」與「真峰頂 (True Top)」唯一評判裁決官 (方案 B)
# =========================================================================
def check_pivot_status(
    curr_k, prev_k, ma3_curr, ma3_prev, ma15_curr, ma15_prev,
    kc_upper, kc_lower, prev_kc_upper, prev_kc_lower, current_atr,
):
    """
    全域真峰谷唯一驗證裁決官 (波段狀態機核心 - 方案 B 慢線硬鎖定修復版):
    - is_true_bottom: MA3向上金叉MA15 (深度>=0.15 ATR) OR (軌外大陽吞噬 / 低位天地針 / 1.8 ATR爆發大陽線 且 收盤價實體站上 MA15)
    - is_true_peak: MA3向下死叉MA15 (深度>=0.15 ATR) OR (軌外大陰吞噬 / 高位天地針 / 1.8 ATR崩盤大陰線 且 收盤價實體跌破 MA15)
    """
    curr_close = float(curr_k['close'])
    curr_open = float(curr_k['open'])
    curr_body = abs(curr_close - curr_open)
    prev_close = float(prev_k['close'])
    prev_open = float(prev_k['open'])
    prev_body = abs(prev_close - prev_open)
    upper_wick = float(curr_k['high']) - max(curr_open, curr_close)
    lower_wick = min(curr_open, curr_close) - float(curr_k['low'])
    atr_val = float(current_atr or 0.0)

    # 🟢 1. 空頭持倉離場 / 真谷底判定 (1000PEPE 12:10 & 13:14 防洗盤鎖定)
    # 妖幣插針比例提高為 3 倍以上 (lower_wick >= 3.0 * curr_body)：普通下影線僅為隨機波動，唯有極致爆量長下影線才具主力吸籌轉折價值！
    is_ma3_golden_cross = (ma3_curr >= ma15_curr) and (ma3_prev <= ma15_prev) and (curr_close >= ma15_curr)
    is_engulfing_bottom = (prev_close < prev_kc_lower) and (curr_close > curr_open) and (curr_body >= 0.6 * prev_body) and (curr_close >= ma15_curr)
    is_pinbar_bottom = (lower_wick >= 3.0 * max(curr_body, 1e-12)) and (curr_close > curr_open) and (float(curr_k.get('low', curr_close)) <= kc_lower) and (curr_close >= ma15_curr)
    is_spike_bottom = (curr_body >= 1.8 * atr_val) and (curr_close > curr_open) and (curr_close >= ma15_curr)

    # ⚠️ 收盤價在 MA15 下方 (curr_close < ma15_curr) 且未金叉的小綠 K，平空邏輯強制否決！
    is_true_bottom = bool(is_ma3_golden_cross or is_engulfing_bottom or is_pinbar_bottom or is_spike_bottom)

    # 🟢 2. 多頭持倉離場 / 真峰頂判定 (龍蝦 12:14 防洗盤鎖定)
    is_ma3_dead_cross = (ma3_curr <= ma15_curr) and (ma3_prev >= ma15_prev) and (curr_close <= ma15_curr)
    is_engulfing_peak = (prev_close > prev_kc_upper) and (curr_close < curr_open) and (curr_body >= 0.6 * prev_body) and (curr_close <= ma15_curr)
    is_pinbar_peak = (upper_wick >= 3.0 * max(curr_body, 1e-12)) and (curr_close < curr_open) and (float(curr_k.get('high', curr_close)) >= kc_upper) and (curr_close <= ma15_curr)
    is_spike_peak = (curr_body >= 1.8 * atr_val) and (curr_close < curr_open) and (curr_close <= ma15_curr)

    # ⚠️ 收盤價在 MA15 上方 (curr_close > ma15_curr) 且 MA3 未死叉的回踩紅 K，平多邏輯強制否決！
    is_true_peak = bool(is_ma3_dead_cross or is_engulfing_peak or is_pinbar_peak or is_spike_peak)

    return is_true_bottom, is_true_peak



# =========================================================================
# 🧬 0.5 妖幣專用風控與籌碼濾網 (方案 B: 完全棄用 EMA 200 / EMA 50 等慢速指標)
# =========================================================================
def evaluate_meme_trade_valid(
    side: str,
    curr_k: Dict[str, Any],
    ma3_curr: float,
    ma15_curr: float,
    ema200_curr: float = 0.0,
    current_atr: float = 0.0,
    volume_ma20: float = 0.0,
    funding_rate: float = 0.0,
    oi_change_pct: float = 0.01,
    prev_k: Optional[Dict[str, Any]] = None,
) -> tuple[bool, str]:
    """
    妖幣 / 高波動 Meme 幣專用風控與籌碼濾網 (Meme Coin Anti-Fakeout Engine - 方案 B)
    1. 方案 B: 完全棄用 EMA 200 / EMA 50 等慢速指標 (改由 check_pivot_status 真峰谷嚴格控制)
    2. 籌碼與槓桿情緒過濾 (Funding Rate & OI)
       - 做多時：資金費率 > 0.1% (0.001) 極端過熱禁止做多；OI <= 0 (持平或減少) 視為誘多，禁止做多。
       - 做空時：資金費率 < -0.1% (-0.001) 極端過冷禁止做空；OI <= 0 視為誘空，禁止做空。
    3. K 棒強度與成交量確認 (ATR Dynamics & Volume)
       - 當根或上一根已收線 K 棒實體幅度必須 >= 1.2 * ATR(14)，否則視為無量毛刺/弱勢插針，一律攔截。
       - 成交量必須 > 20 期均量 (volume_ma20)，未放量一律攔截。
    """
    curr_close = float(curr_k.get('close', 0.0))
    curr_open = float(curr_k.get('open', curr_close))
    curr_body = abs(curr_close - curr_open)
    curr_vol = float(curr_k.get('volume', 0.0))
    atr_val = float(current_atr or 0.0)

    side_norm = "BUY" if str(side).upper() in ("BUY", "LONG") else "SELL"

    # 1. 方案 B 完全棄用 EMA 200 / EMA 50 等慢速指標，避免 1M 起漲點被誤殺！

    # 2. 籌碼與槓桿情緒過濾 (Funding Rate & OI) - 放寬極端值門檻
    if side_norm == "BUY":
        if funding_rate is not None and funding_rate > 0.003:
            return False, f"OVERHEATED_FUNDING_RATE (fr={funding_rate:.4%} > 0.3%)"
    elif side_norm == "SELL":
        if funding_rate is not None and funding_rate < -0.003:
            return False, f"OVERCOOLED_FUNDING_RATE (fr={funding_rate:.4%} < -0.3%)"

    # 3. K 棒強度確認 - 妖幣交叉與破軌徹底放寬 (僅過濾完全無波動死水 0.05 ATR)
    prev_body = abs(float(prev_k.get('close', 0.0)) - float(prev_k.get('open', 0.0))) if prev_k else 0.0
    effective_body = max(curr_body, prev_body)
    if atr_val > 0 and effective_body < 0.05 * atr_val:
        return False, f"ZERO_LIQUIDITY_DOJI (body={curr_body:.6f} < 0.05*ATR={0.05*atr_val:.6f})"

    return True, "MEME_TRADE_VALID_PASS"


def is_meme_trade_valid(
    side: str,
    curr_k: Dict[str, Any],
    ma3_curr: float,
    ma15_curr: float,
    ema200_curr: float,
    current_atr: float,
    volume_ma20: float,
    funding_rate: float = 0.0,
    oi_change_pct: float = 0.01,
    prev_k: Optional[Dict[str, Any]] = None,
) -> bool:
    """使用者指定標準簽名之布林值判斷函式"""
    valid, _ = evaluate_meme_trade_valid(
        side=side,
        curr_k=curr_k,
        ma3_curr=ma3_curr,
        ma15_curr=ma15_curr,
        ema200_curr=ema200_curr,
        current_atr=current_atr,
        volume_ma20=volume_ma20,
        funding_rate=funding_rate,
        oi_change_pct=oi_change_pct,
        prev_k=prev_k,
    )
    return valid



# =========================================================================
# 🟢 1. 多頭持倉平倉判定 (方案 B: 檢測真峰頂與結構破壞，震盪假峰頂一票否決)
# =========================================================================
# 🟢 2. 多頭持倉離場 (中軌向上 & 黏合防洗盤鎖定)
# =========================================================================
def should_exit_long(
    curr_k, prev_k, ma3_curr, ma3_prev, ma15_curr, ma15_prev,
    *args,
    kc_middle=None, prev_kc_middle=None, kc_upper=None, prev_kc_upper=None,
    current_atr=0.0, swing_low_price=None, raw_df=None, position=None, **kwargs
):
    """
    多頭持倉離場判定 (中軌斜率鋼鐵鎖定 + 均線黏合假死叉防線):
    1. 中軌向上 & 收盤穩守 MA15 且未發生 0.15*ATR 深度死叉 -> 一票否決 (return False)
    2. 深度死叉 (MA15 - MA3 >= 0.15*ATR) OR 真峰頂 OR 跌破 swing_low 且跌破 MA15 -> return True
    """
    curr_close = float(curr_k['close'])
    curr_open = float(curr_k['open'])

    # 智能解析 kc_middle / prev_kc_middle (相容位置參數與命名參數)
    if kc_middle is not None:
        kc_mid_curr = float(kc_middle)
    elif len(args) >= 1 and args[0] is not None:
        kc_mid_curr = float(args[0])
    elif "kc_middle" in curr_k:
        kc_mid_curr = float(curr_k["kc_middle"])
    elif kc_upper is not None or "kc_upper" in curr_k:
        u = float(kc_upper if kc_upper is not None else curr_k.get("kc_upper", 0.0))
        l = float(curr_k.get("kc_lower", 0.0))
        kc_mid_curr = (u + l) / 2.0 if (u and l) else u
    else:
        kc_mid_curr = 0.0

    if prev_kc_middle is not None:
        kc_mid_prev = float(prev_kc_middle)
    elif len(args) >= 2 and args[1] is not None:
        kc_mid_prev = float(args[1])
    elif "kc_middle" in prev_k:
        kc_mid_prev = float(prev_k["kc_middle"])
    elif prev_kc_upper is not None or "kc_upper" in prev_k:
        u = float(prev_kc_upper if prev_kc_upper is not None else prev_k.get("kc_upper", 0.0))
        l = float(prev_k.get("kc_lower", 0.0))
        kc_mid_prev = (u + l) / 2.0 if (u and l) else u
    else:
        kc_mid_prev = 0.0

    atr_val = float(current_atr or (args[2] if len(args) >= 3 else 0.0) or curr_k.get("atr", 0.0) or 0.0)
    swing_low = swing_low_price if swing_low_price is not None else (args[3] if len(args) >= 4 else None)

    # 持倉呼吸防洗鐵律: 未實質展開波段 (gain < 0.5 ATR 且 ROE < 5%)，一票否決平倉！
    if position:
        entry_p = float(position.get("entry_price") or 0.0)
        peak_roe = float(position.get("peak_roe") or 0.0)
        atr_threshold = 0.5 * atr_val
        if entry_p > 0:
            high_p = float(position.get("highest_price") or position.get("peak_price") or entry_p)
            gain = high_p - entry_p
            if gain < atr_threshold and peak_roe < 5.0:
                return False

    is_kc_mid_up = bool(kc_mid_curr > kc_mid_prev)  # 中軌斜率向上
    is_ma3_dead_cross = is_valid_dead_cross(ma3_curr, ma3_prev, ma15_curr, ma15_prev, atr_val)
    is_structure_broken_short = bool(swing_low is not None and curr_close < float(swing_low) and curr_close < ma15_curr)

    # 獲取真峰頂狀態
    kc_u = float(kc_upper if kc_upper is not None else curr_k.get("kc_upper", kc_mid_curr * 1.02))
    p_kc_u = float(prev_kc_upper if prev_kc_upper is not None else prev_k.get("kc_upper", kc_mid_prev * 1.02))
    kc_lo = float(curr_k.get("kc_lower", kc_mid_curr * 0.98))
    p_kc_lo = float(prev_k.get("kc_lower", kc_mid_prev * 0.98))

    _, is_real_peak = check_pivot_status(
        curr_k=curr_k, prev_k=prev_k,
        ma3_curr=ma3_curr, ma3_prev=ma3_prev,
        ma15_curr=ma15_curr, ma15_prev=ma15_prev,
        kc_upper=kc_u, kc_lower=kc_lo,
        prev_kc_upper=p_kc_u, prev_kc_lower=p_kc_lo,
        current_atr=atr_val,
    )

    # ⚠️ 【中軌向上 & 慢線鋼鐵防洗盤鎖定】：
    # 只要 (中軌斜率向上) 且 (收盤價仍穩守在 KC 中軌上方，或在 MA15 慢線上方)：
    # 若「未確立真峰頂」且「未發生 0.15*ATR 深度死叉」，盤中任何微幅回踩小紅 K，一律判定為多頭呼吸回踩，100% 一票否決平多 (return False)！
    if is_kc_mid_up and not is_ma3_dead_cross and not is_real_peak and (curr_close >= kc_mid_curr or curr_close >= ma15_curr):
        return False

    if not (is_ma3_dead_cross or is_real_peak or is_structure_broken_short):
        return False

    return True  # 觸發真死叉或真峰頂/結構破壞，允許【平多兼秒級反手開空】


# =========================================================================
# 🔴 3. 空頭持倉離場 (中軌向下 & 黏合防洗盤鎖定)
# =========================================================================
def should_exit_short(
    curr_k, prev_k, ma3_curr, ma3_prev, ma15_curr, ma15_prev,
    *args,
    kc_middle=None, prev_kc_middle=None, kc_lower=None, prev_kc_lower=None,
    current_atr=0.0, swing_high_price=None, raw_df=None, position=None, **kwargs
):
    """
    空頭持倉離場判定 (中軌斜率鋼鐵鎖定 + 均線黏合假金叉防線):
    1. 中軌向下 & 收盤穩守 MA15 且未發生 0.15*ATR 深度金叉 -> 一票否決 (return False)
    2. 深度金叉 (MA3 - MA15 >= 0.15*ATR) OR 真谷底 OR 突破 swing_high 且突破 MA15 -> return True
    """
    curr_close = float(curr_k['close'])
    curr_open = float(curr_k['open'])

    # 智能解析 kc_middle / prev_kc_middle (相容位置參數與命名參數)
    if kc_middle is not None:
        kc_mid_curr = float(kc_middle)
    elif len(args) >= 1 and args[0] is not None:
        kc_mid_curr = float(args[0])
    elif "kc_middle" in curr_k:
        kc_mid_curr = float(curr_k["kc_middle"])
    elif kc_lower is not None or "kc_lower" in curr_k:
        l = float(kc_lower if kc_lower is not None else curr_k.get("kc_lower", 0.0))
        u = float(curr_k.get("kc_upper", 0.0))
        kc_mid_curr = (u + l) / 2.0 if (u and l) else l
    else:
        kc_mid_curr = 0.0

    if prev_kc_middle is not None:
        kc_mid_prev = float(prev_kc_middle)
    elif len(args) >= 2 and args[1] is not None:
        kc_mid_prev = float(args[1])
    elif "kc_middle" in prev_k:
        kc_mid_prev = float(prev_k["kc_middle"])
    elif prev_kc_lower is not None or "kc_lower" in prev_k:
        l = float(prev_kc_lower if prev_kc_lower is not None else prev_k.get("kc_lower", 0.0))
        u = float(prev_k.get("kc_upper", 0.0))
        kc_mid_prev = (u + l) / 2.0 if (u and l) else l
    else:
        kc_mid_prev = 0.0

    atr_val = float(current_atr or (args[2] if len(args) >= 3 else 0.0) or curr_k.get("atr", 0.0) or 0.0)
    swing_high = swing_high_price if swing_high_price is not None else (args[3] if len(args) >= 4 else None)

    # 持倉呼吸防洗鐵律: 未實質展開波段 (drop < 0.5 ATR 且 ROE < 5%)，一票否決平倉！
    if position:
        entry_p = float(position.get("entry_price") or 0.0)
        peak_roe = float(position.get("peak_roe") or 0.0)
        atr_threshold = 0.5 * atr_val
        if entry_p > 0:
            low_p = float(position.get("lowest_price") or position.get("trough_price") or entry_p)
            drop = entry_p - low_p
            if drop < atr_threshold and peak_roe < 5.0:
                return False

    is_kc_mid_down = bool(kc_mid_curr < kc_mid_prev)  # 中軌斜率向下
    is_ma3_golden_cross = is_valid_golden_cross(ma3_curr, ma3_prev, ma15_curr, ma15_prev, atr_val)
    is_structure_broken_long = bool(swing_high is not None and curr_close > float(swing_high) and curr_close > ma15_curr)

    # 獲取真谷底狀態
    kc_u = float(curr_k.get("kc_upper", kc_mid_curr * 1.02))
    p_kc_u = float(prev_k.get("kc_upper", kc_mid_prev * 1.02))
    kc_lo = float(kc_lower if kc_lower is not None else curr_k.get("kc_lower", kc_mid_curr * 0.98))
    p_kc_lo = float(prev_kc_lower if prev_kc_lower is not None else prev_k.get("kc_lower", kc_mid_prev * 0.98))

    is_real_bottom, _ = check_pivot_status(
        curr_k=curr_k, prev_k=prev_k,
        ma3_curr=ma3_curr, ma3_prev=ma3_prev,
        ma15_curr=ma15_curr, ma15_prev=ma15_prev,
        kc_upper=kc_u, kc_lower=kc_lo,
        prev_kc_upper=p_kc_u, prev_kc_lower=p_kc_lo,
        current_atr=atr_val,
    )

    # ⚠️ 【中軌向下 & 慢線鋼鐵防洗盤鎖定】：
    # 只要 (中軌向下) 且 (收盤價仍壓制在 KC 中軌下方，或在 MA15 慢線下方)：
    # 若「未確立真谷底」且「未發生 0.15*ATR 深度金叉」，盤中任何微幅反彈小綠 K，一律判定為空頭呼吸回踩，100% 一票否決平空 (return False)！
    if is_kc_mid_down and not is_ma3_golden_cross and not is_real_bottom and (curr_close <= kc_mid_curr or curr_close <= ma15_curr):
        return False

    if not (is_ma3_golden_cross or is_real_bottom or is_structure_broken_long):
        return False

    return True  # 觸發真金叉或真谷底/結構破壞，允許【平空兼秒級反手開多】


class ExecutionManager:
    """唯一開平倉執行通道與標準流水線協調器"""

    @classmethod
    async def process_symbol_pipeline(
        cls,
        symbol: str,
        engine_or_account: Any,
        klines_df: Optional[pd.DataFrame] = None,
        quote: Optional[float] = None,
        frame_15m: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """
        單一幣種的標準流水線處理循環 (Thread-safe, Single-Pipeline)
        """
        account = getattr(engine_or_account, "account", engine_or_account)

        # 1. 抓取數據與計算純數值指標 (Data Layer)
        current_position = account.positions.get(symbol)
        has_position = bool(current_position and abs(float(current_position.get("amount", 0.0) or current_position.get("qty", 0.0))) > 0)

        # 若未傳入 15M K 棒，自動嘗試獲取 15M 大週期 K 棒提供精準大趨勢過濾
        if frame_15m is None or frame_15m.empty:
            if hasattr(engine_or_account, "fetch_klines"):
                try:
                    frame_15m = await engine_or_account.fetch_klines(symbol, timeframe="15m", limit=50, keep_live=True)
                    if frame_15m is not None and not frame_15m.empty and hasattr(engine_or_account, "strategy"):
                        frame_15m = engine_or_account.strategy.compute_indicators(frame_15m.copy())
                except Exception:
                    pass

        if klines_df is None or klines_df.empty:
            if hasattr(engine_or_account, "fetch_klines"):
                try:
                    klines_df = await engine_or_account.fetch_klines(symbol, timeframe="1m", limit=120)
                except Exception as e:
                    logger.debug(f"[PIPELINE] {symbol} fetch_klines 失敗: {e}")
                    return {"status": "FETCH_FAILED", "symbol": symbol}
            else:
                return {"status": "NO_KLINES", "symbol": symbol}

        market_data: Optional[MarketDataSnapshot] = DataPipeline.calculate_snapshot(
            symbol=symbol,
            df=klines_df,
            current_price=quote,
            position=current_position if has_position else None,
        )
        if not market_data:
            if not has_position:
                return {"status": "SNAPSHOT_FAILED", "symbol": symbol}

            # 既有持倉：快照失敗不得盲目停止風險管理！啟用緊急防禦判斷
            logger.warning(f"⚠️ [DATA_OUTAGE] {symbol} 快照計算失敗但有既有持倉，啟動持倉安全處置通道！")
            pos_entry = float(current_position.get("entry_price") or 0.0)
            pos_side = str(current_position.get("side") or "").upper()
            pos_leverage = float(current_position.get("leverage") or 1.0)

            # 核對是否具備可信報價
            # 核對是否具備可信報價 (方案 B 無硬止損版: 徹底取消固定點位硬止損割肉)
            if quote and quote > 0 and pos_entry > 0:
                logger.info(f"🛡️ [DATA_OUTAGE_HOLD] {symbol} 資料快照失敗，依真波段狀態機守護持倉，取消固定硬止損割肉！")
                return {"status": "DATA_OUTAGE_HOLD", "symbol": symbol, "quote": quote}
            else:
                logger.critical(f"🛑 [CRITICAL_DATA_OUTAGE] {symbol} 快照與即時報價均失效！嚴禁增加風險，請依交易所原生止損守護持倉")
                return {"status": "CRITICAL_DATA_OUTAGE", "symbol": symbol}

        if has_position:
            from core.services.exits.trend_lock import update_trend_lock_state
            if update_trend_lock_state(current_position, klines_df):
                meta = getattr(account, "position_meta", {}).setdefault(symbol, {})
                meta["trend_lock_state"] = dict(current_position["trend_lock_state"])
                save = getattr(account, "save_state", None)
                if callable(save):
                    save()

        curr_quote = float(quote if quote is not None else market_data.current_price)
        raw_df = getattr(market_data, "raw_df", None)
        c_k = market_data.curr_k if market_data.curr_k else (raw_df.iloc[-1] if raw_df is not None and len(raw_df) >= 1 else {})
        p_k = market_data.prev_k if market_data.prev_k else (raw_df.iloc[-2] if raw_df is not None and len(raw_df) >= 2 else c_k)
        c_c = float(c_k.get("close", curr_quote))
        c_o = float(c_k.get("open", c_c))
        c_h = float(c_k.get("high", max(c_c, c_o)))
        c_l = float(c_k.get("low", min(c_c, c_o)))
        p_c = float(p_k.get("close", c_o))
        p_o = float(p_k.get("open", p_c))
        p_h = float(p_k.get("high", max(p_c, p_o)))
        p_l = float(p_k.get("low", min(p_c, p_o)))

        m3_c = float(c_k.get("ma3", getattr(market_data, "ma3", c_c)))
        m3_p = float(p_k.get("ma3", getattr(market_data, "prev_ma3", m3_c)))
        m15_c = float(c_k.get("ma15", getattr(market_data, "ma15", c_c)))
        m15_p = float(p_k.get("ma15", getattr(market_data, "prev_ma15", m15_c)))

        kc_u = float(c_k.get("kc_upper", market_data.kc_upper))
        p_kc_u = float(p_k.get("kc_upper", kc_u))
        kc_lo = float(c_k.get("kc_lower", market_data.kc_lower))
        p_kc_lo = float(p_k.get("kc_lower", kc_lo))
        kc_mid = float(c_k.get("kc_middle", getattr(market_data, "kc_middle", (kc_u + kc_lo) / 2.0)))

        c_bd = abs(c_c - c_o)
        p_bd = abs(p_c - p_o)
        c_up_w = c_h - max(c_c, c_o)
        c_lo_w = min(c_c, c_o) - c_l
        atr_v = float(getattr(market_data, "atr", 0.0) or 0.0)
        kc_slope = float(getattr(market_data, "kc_middle_slope", 0.0) or 0.0)

        # ── 🔭 拉遠視角 (Zoomed-Out 60~120 根 K 棒) 大週期趨勢濾網 ──
        # 解決「拉遠看明明是空頭下降趨勢（一峰比一峰低、一底比一底低、EMA60 下行），
        # 卻因為短線 2~3 根反彈小陽線使 2-bar kc_slope 微幅翻正就逆勢開多」的致命錯誤！
        ema60_val = float(getattr(market_data, "ema60", 0.0) or c_c)
        macro_slope_val = float(getattr(market_data, "macro_slope", 0.0) or 0.0)
        slow_slope_5_val = float(getattr(market_data, "slow_slope_5", 0.0) or 0.0)
        macro_trend_snap = str(getattr(market_data, "macro_trend", "NEUTRAL") or "NEUTRAL").upper()
        kc_slope_10_val = kc_slope
        is_lower_highs_bear = False
        is_higher_lows_bull = False

        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and "close" in raw_df.columns and len(raw_df) >= 20:
            s_close = raw_df["close"].astype(float)
            s_ema60 = s_close.ewm(span=60, adjust=False).mean()
            ema60_val = float(s_ema60.iloc[-1])
            lookback_e60 = min(len(s_ema60), 15)
            macro_slope_val = float(s_ema60.iloc[-1] - s_ema60.iloc[-lookback_e60])
            if "kc_middle" in raw_df.columns:
                s_kcm = raw_df["kc_middle"].astype(float)
            else:
                s_kcm = s_close.ewm(span=20, adjust=False).mean()
            lookback_kc10 = min(len(s_kcm), 12)
            kc_slope_10_val = float(s_kcm.iloc[-1] - s_kcm.iloc[-lookback_kc10]) / max(1, lookback_kc10 - 1)
            lookback_kc5 = min(len(s_kcm), 6)
            kc_slope_5_val = float(s_kcm.iloc[-1] - s_kcm.iloc[-lookback_kc5]) / max(1, lookback_kc5 - 1)
            lookback_kc3 = min(len(s_kcm), 4)
            kc_slope_3_val = float(s_kcm.iloc[-1] - s_kcm.iloc[-lookback_kc3]) / max(1, lookback_kc3 - 1)
            if "ma15" in raw_df.columns:
                s_m15_macro = raw_df["ma15"].astype(float)
            else:
                s_m15_macro = s_close.rolling(15, min_periods=1).mean()
            lookback_m15_5 = min(len(s_m15_macro), 6)
            m15_slope_5_val = float(s_m15_macro.iloc[-1] - s_m15_macro.iloc[-lookback_m15_5]) / max(1, lookback_m15_5 - 1)
            lookback_m15_3 = min(len(s_m15_macro), 4)
            m15_slope_3_val = float(s_m15_macro.iloc[-1] - s_m15_macro.iloc[-lookback_m15_3]) / max(1, lookback_m15_3 - 1)

            # 檢查過去 60 根 K 棒的高低點結構（是否處於空頭主跌波段下半場或一峰比一峰低）
            if len(raw_df) >= 40 and "high" in raw_df.columns and "low" in raw_df.columns:
                w_len = min(len(raw_df), 90)
                half_w = w_len // 2
                first_half_high = float(raw_df["high"].iloc[-w_len:-half_w].max())
                second_half_high = float(raw_df["high"].iloc[-half_w:].max())
                first_half_low = float(raw_df["low"].iloc[-w_len:-half_w].min())
                second_half_low = float(raw_df["low"].iloc[-half_w:].min())
                # 前半段高點顯著高於後半段高點，且後半段低點更低 -> 標準拉遠空頭結構 (Lower Highs & Lower Lows)
                if second_half_high < first_half_high and second_half_low <= first_half_low and c_c < ema60_val:
                    is_lower_highs_bear = True
                elif second_half_low > first_half_low and second_half_high >= first_half_high and c_c > ema60_val:
                    is_higher_lows_bull = True

        # 綜合判定：拉遠看是否為空頭 / 多頭大趨勢 (結合 15M 大級別 K 棒與 60~120 根 1M K 棒結構)
        # 15M 大週期分析：
        htf_15m_trend = "UNKNOWN"
        if frame_15m is not None and not frame_15m.empty and len(frame_15m) >= 2:
            try:
                from core.indicators.dual_timeframe_provider import DualTimeframeProvider
                t_dict = DualTimeframeProvider.get_15m_trend(frame_15m)
                htf_15m_trend = t_dict.get("15m_big_trend", "UNKNOWN")
            except Exception:
                pass

        # 判斷 1M 圖表是否已經正式翻轉為明確黃金交叉多頭 / 死亡交叉空頭結構：
        # 1. 解決「一交叉就急著開倉」：必須 MA3 與 MA15 明確發散 (|m3_c - m15_c| >= 0.10 * ATR)，且 MA15 與 KC 中軌 (1棒/3棒/5棒) 皆已同步轉向！
        # 2. 解決「已經交叉好久卻不開倉」：不再死等滯後 12 分鐘的 kc_slope_10_val 或 60 分鐘的 ema60_val，
        #    只要 1棒、3棒、5棒 KC 中軌與 MA15 全面同步順向，且 MA3 與 MA15 黃金/死亡交叉發散確立，即刻判定新趨勢確立！
        kc_slope_5_ref = locals().get("kc_slope_5_val", kc_slope)
        kc_slope_3_ref = locals().get("kc_slope_3_val", kc_slope)
        m15_slope_5_ref = locals().get("m15_slope_5_val", m15_c - m15_p)
        m15_slope_3_ref = locals().get("m15_slope_3_val", m15_c - m15_p)
        min_gc_dc_spread = max(0.10 * atr_v, m15_c * 0.0005) if atr_v > 0 else (m15_c * 0.0005)

        is_1m_confirmed_bull = bool(
            (m3_c > m15_c)
            and ((m3_c - m15_c) >= min_gc_dc_spread)
            and (m3_c > m3_p)
            and (c_c > kc_mid)
            and (c_c > m15_c)
            and (kc_slope > 0)
            and (kc_slope_3_ref > 0)
            and (kc_slope_5_ref >= 0)
            and (m15_c >= m15_p and m15_slope_3_ref >= 0 and m15_slope_5_ref >= 0)
        )
        is_1m_confirmed_bear = bool(
            (m3_c < m15_c)
            and ((m15_c - m3_c) >= min_gc_dc_spread)
            and (m3_c < m3_p)
            and (c_c < kc_mid)
            and (c_c < m15_c)
            and (kc_slope < 0)
            and (kc_slope_3_ref < 0)
            and (kc_slope_5_ref <= 0)
            and (m15_c <= m15_p and m15_slope_3_ref <= 0 and m15_slope_5_ref <= 0)
        )

        # 只要 15M 大週期為 DOWN，或 5棒/10棒 KC 中軌向下，或拉遠看 EMA60 向下且價格/MA15在下方，或高低點呈空頭排列（且尚未被 1M 全面翻多結構推翻）：
        is_zoomed_out_bearish = bool(
            (not is_1m_confirmed_bull)
            and (
                is_1m_confirmed_bear
                or htf_15m_trend == "DOWN"
                or macro_trend_snap == "BEARISH"
                or kc_slope_10_val < 0
                or (kc_slope_5_ref < 0 and m15_slope_5_ref < 0)
                or (macro_slope_val < 0 and (c_c < ema60_val or m15_c < ema60_val))
                or is_lower_highs_bear
            )
        )
        is_zoomed_out_bullish = bool(
            (not is_1m_confirmed_bear)
            and (
                is_1m_confirmed_bull
                or (
                    (not is_zoomed_out_bearish)
                    and (
                        htf_15m_trend == "UP"
                        or macro_trend_snap == "BULLISH"
                        or kc_slope_10_val > 0
                        or (kc_slope_5_ref > 0 and m15_slope_5_ref > 0)
                        or (macro_slope_val > 0 and (c_c > ema60_val or m15_c > ema60_val))
                        or is_higher_lows_bull
                    )
                )
            )
        )

        # ── 🐒 市場狀態分類 (BULL / BEAR / RANGE)：猴市只做「收線突破區間」 ──
        # 猴市判定：EMA60 斜率走平 (|slope| < 0.5 ATR) 且近 60 根價格反覆穿越 EMA60 (>= 4 次)，
        # 或牛熊皆不成立。猴市期間一律不開倉，只有已收線 K 棒實體突破近 30 根區間高/低點且同時站出 KC 外軌，才順突破方向進場。
        ema60_cross_count = 0
        range_high = None
        range_low = None
        range_breakout_side = None
        closed_rng = None
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 20 and "close" in raw_df.columns:
            try:
                s_c = raw_df["close"].astype(float)
                s_e60 = s_c.ewm(span=60, adjust=False).mean()
                side_sign = (s_c - s_e60).iloc[-60:].apply(lambda v: 1 if v > 0 else -1)
                ema60_cross_count = int((side_sign.diff().abs() > 0).sum())
                if "is_closed" in raw_df.columns and not bool(raw_df.iloc[-1].get("is_closed", True)):
                    closed_rng = raw_df.iloc[:-1]
                else:
                    closed_rng = raw_df
                if len(closed_rng) >= 12:
                    look = closed_rng.iloc[-31:-1]
                    range_high = float(look["high"].astype(float).max())
                    range_low = float(look["low"].astype(float).min())
            except Exception:
                ema60_cross_count = 0

        is_flat_macro = bool(atr_v > 0 and abs(macro_slope_val) < 0.5 * atr_v)
        is_choppy = bool(is_flat_macro and ema60_cross_count >= 4 and not is_1m_confirmed_bull and not is_1m_confirmed_bear)
        if is_choppy:
            is_zoomed_out_bearish = False
            is_zoomed_out_bullish = False
        if is_zoomed_out_bullish:
            market_regime = "BULL"
        elif is_zoomed_out_bearish:
            market_regime = "BEAR"
        else:
            market_regime = "RANGE"

        if market_regime == "RANGE" and closed_rng is not None and range_high is not None and len(closed_rng) >= 1:
            lk = closed_rng.iloc[-1]
            lk_c = float(lk.get("close", 0.0))
            lk_o = float(lk.get("open", lk_c))
            lk_u = float(lk.get("kc_upper", kc_u) or kc_u)
            lk_l = float(lk.get("kc_lower", kc_lo) or kc_lo)
            if lk_c > lk_o and lk_c > range_high and lk_c > lk_u and curr_quote > range_high:
                range_breakout_side = "LONG"
            elif lk_c < lk_o and lk_c < range_low and lk_c < lk_l and curr_quote < range_low:
                range_breakout_side = "SHORT"
        try:
            setattr(market_data, "market_regime", market_regime)
        except Exception:
            pass

        # ── Squeeze 擠壓狀態、震盪糾纏與 MA3 中軌貼合防護 (Squeeze, Chop & MA3-Midline Adhesion Guard) ──
        # 依使用者最高鐵律：「震盪區不要開倉、MA3中軌貼合不開倉」！
        bb_u = float(c_k.get("bb_upper", 0.0))
        bb_lo = float(c_k.get("bb_lower", 0.0))
        if (bb_u <= 0 or bb_lo <= 0) and raw_df is not None and "close" in raw_df.columns and len(raw_df) >= 20:
            std20 = float(raw_df["close"].rolling(20, min_periods=1).std().iloc[-1] or 0.0)
            mid20 = float(raw_df["close"].rolling(20, min_periods=1).mean().iloc[-1] or c_c)
            bb_u = mid20 + 2.0 * std20
            bb_lo = mid20 - 2.0 * std20
        is_sqz_now = bool(bb_u > 0 and bb_lo > 0 and kc_u > 0 and kc_lo > 0 and bb_u < kc_u and bb_lo > kc_lo)
        ma_dist_now = abs(m3_c - kc_mid) / kc_mid if kc_mid > 0 else 0.0
        ma3_mid_diff = abs(m3_c - kc_mid)
        ma3_ma15_diff = abs(m3_c - m15_c)
        ma3_ma15_spread_ratio = ma3_ma15_diff / m15_c if m15_c > 0 else 0.0

        # 1. 【MA3 中軌貼合判定 (is_ma3_midline_adhered)】：
        # 當 MA3 與 KC 中軌距離過近，或 MA3 與 MA15 高度貼合，且 K 棒實體微弱 (< 0.25 ATR) 毫無突破動能時，禁止開倉！
        # 但針對妖幣：若當根 K 棒為中大實體動能棒 (c_bd >= 0.25 * atr_v) 強勢貫穿中軌/MA15 起飛，代表正處於金叉/死叉發散起點，絕不誤判為死水貼合！
        is_strong_cross_candle = bool(
            atr_v > 0
            and c_bd >= 0.25 * atr_v
            and (
                (c_c > c_o and c_c > kc_mid and c_c > m15_c and m3_c > m3_p)
                or (c_c < c_o and c_c < kc_mid and c_c < m15_c and m3_c < m3_p)
            )
        )
        is_ma3_midline_adhered = bool(
            not is_strong_cross_candle
            and (
                ma_dist_now < 0.00045
                or (atr_v > 0 and ma3_mid_diff < 0.08 * atr_v)
                or ma3_ma15_spread_ratio < 0.00035
                or (atr_v > 0 and ma3_ma15_diff < 0.06 * atr_v)
            )
        )

        # 2. 【震盪區 / 均線糾纏判定 (is_chop_zone)】：
        # 檢查近 8 根 K 棒內 MA3 與 KC 中軌 (或 MA15) 是否反覆交錯糾纏 (>= 2 次交錯)，
        # 或 KC 中軌斜率極度走平 (|kc_slope| < 0.02 * ATR 且未突破外軌)，或處於 RANGE 猴市震盪區！
        # 針對妖幣：若出現強勢突破/爆量拉升 (c_bd >= 0.40 * atr_v 且站穩中軌與 MA15 順向發散)，代表脫離盤整正式點火！
        is_meme_breakout_ignition = bool(
            atr_v > 0
            and c_bd >= 0.40 * atr_v
            and (
                (c_c > c_o and c_c > kc_mid and c_c > m15_c and m3_c > m15_c and m3_c > m3_p and kc_slope >= 0)
                or (c_c < c_o and c_c < kc_mid and c_c < m15_c and m3_c < m15_c and m3_c < m3_p and kc_slope <= 0)
            )
        )
        ma3_mid_cross_8 = 0
        ma3_m15_cross_8 = 0
        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 15 and "close" in raw_df.columns:
            try:
                s_close_w = raw_df["close"].astype(float)
                s_ma3_w = raw_df["ma3"].astype(float) if "ma3" in raw_df.columns else s_close_w.rolling(3, min_periods=1).mean()
                s_ma15_w = raw_df["ma15"].astype(float) if "ma15" in raw_df.columns else s_close_w.rolling(15, min_periods=1).mean()
                s_kcm_w = raw_df["kc_middle"].astype(float) if "kc_middle" in raw_df.columns else s_close_w.ewm(span=20, adjust=False).mean()
                diff_mid_sign = (s_ma3_w - s_kcm_w).iloc[-8:].apply(lambda v: 1 if v >= 0 else -1)
                diff_m15_sign = (s_ma3_w - s_ma15_w).iloc[-8:].apply(lambda v: 1 if v >= 0 else -1)
                ma3_mid_cross_8 = int((diff_mid_sign.diff().abs() > 0).sum())
                ma3_m15_cross_8 = int((diff_m15_sign.diff().abs() > 0).sum())
            except Exception:
                ma3_mid_cross_8 = 0
                ma3_m15_cross_8 = 0

        is_kc_slope_flat = bool(atr_v > 0 and abs(kc_slope) < 0.02 * atr_v and kc_lo <= c_c <= kc_u and not is_meme_breakout_ignition)
        is_chop_zone = bool(
            not is_meme_breakout_ignition
            and (
                market_regime == "RANGE"
                or is_choppy
                or is_sqz_now
                or is_kc_slope_flat
                or (ma3_mid_cross_8 >= 2 and ma_dist_now < 0.0015)
                or (ma3_m15_cross_8 >= 2 and ma3_ma15_spread_ratio < 0.0012)
            )
        )

        # 🎯 核心全域裁決：檢測「真谷底 (True Bottom)」與「真峰頂 (True Top)」(方案 B)
        is_true_bottom, is_true_peak = check_pivot_status(
            curr_k=c_k, prev_k=p_k,
            ma3_curr=m3_c, ma3_prev=m3_p,
            ma15_curr=m15_c, ma15_prev=m15_p,
            kc_upper=kc_u, kc_lower=kc_lo,
            prev_kc_upper=p_kc_u, prev_kc_lower=p_kc_lo,
            current_atr=atr_v,
        )

        lookback_p = min(len(raw_df), 15) if raw_df is not None and isinstance(raw_df, pd.DataFrame) else 0
        if lookback_p >= 2:
            prior_lows = raw_df['low'].iloc[-lookback_p:-1]
            prior_highs = raw_df['high'].iloc[-lookback_p:-1]
            swing_low_price = float(prior_lows.min()) if len(prior_lows) > 0 else None
            swing_high_price = float(prior_highs.max()) if len(prior_highs) > 0 else None
        else:
            swing_low_price = None
            swing_high_price = None

        p_kc_mid = float(p_k.get("kc_middle", (p_kc_u + p_kc_lo) / 2.0))

        is_structure_broken_short = (
            (swing_low_price is not None and c_c < float(swing_low_price) and c_c < m15_c)
            or should_exit_long(
                curr_k=c_k, prev_k=p_k,
                ma3_curr=m3_c, ma3_prev=m3_p,
                ma15_curr=m15_c, ma15_prev=m15_p,
                kc_middle=kc_mid, prev_kc_middle=p_kc_mid,
                kc_upper=kc_u, prev_kc_upper=p_kc_u,
                current_atr=atr_v,
                swing_low_price=swing_low_price,
                raw_df=klines_df,
                position=current_position if has_position else None,
            )
        )
        is_structure_broken_long = (
            (swing_high_price is not None and c_c > float(swing_high_price) and c_c > m15_c)
            or should_exit_short(
                curr_k=c_k, prev_k=p_k,
                ma3_curr=m3_c, ma3_prev=m3_p,
                ma15_curr=m15_c, ma15_prev=m15_p,
                kc_middle=kc_mid, prev_kc_middle=p_kc_mid,
                kc_lower=kc_lo, prev_kc_lower=p_kc_lo,
                current_atr=atr_v,
                swing_high_price=swing_high_price,
                raw_df=klines_df,
                position=current_position if has_position else None,
            )
        )

        # 2. 持倉狀態機控制 (Trend Lock State Machine - 方案 B): 最高第一優先審查真峰頂/真谷底狀態機，秒級平倉兼反手開倉
        if has_position:
            pos_side = str(current_position.get("side", "")).upper()
            pos_entry = float(current_position.get("entry_price") or 0.0)
            pos_lev = float(current_position.get("leverage") or 5.0)
            pos_open_ts = float(current_position.get("open_timestamp") or current_position.get("created_at") or current_position.get("entry_time") or 0.0)

            high_seen = float(current_position.get("highest_price") or pos_entry)
            low_seen = float(current_position.get("lowest_price") or current_position.get("trough_price") or pos_entry)

            # 同步回溯持倉開倉後的歷史 K 棒最高價/最低價，確保最高浮盈與最低谷底價 100% 精準紀錄
            if raw_df is not None and isinstance(raw_df, pd.DataFrame) and not raw_df.empty and pos_open_ts > 0:
                try:
                    ts_col = "timestamp" if "timestamp" in raw_df.columns else ("open_time" if "open_time" in raw_df.columns else None)
                    if ts_col:
                        open_ms = pos_open_ts * 1000.0 if pos_open_ts < 1e11 else pos_open_ts
                        post_df = raw_df[raw_df[ts_col] >= open_ms - 60000.0]
                        if not post_df.empty:
                            df_max_h = float(post_df["high"].max() or 0.0)
                            df_min_l = float(post_df["low"].min() or 0.0)
                            if df_max_h > high_seen:
                                high_seen = df_max_h
                            if df_min_l > 0 and (low_seen <= 0 or df_min_l < low_seen):
                                low_seen = df_min_l
                except Exception:
                    pass

            if curr_quote > high_seen:
                high_seen = curr_quote
            if low_seen <= 0 or (curr_quote > 0 and curr_quote < low_seen):
                low_seen = curr_quote

            current_position["highest_price"] = high_seen
            current_position["lowest_price"] = low_seen
            current_position["trough_price"] = low_seen
            # 注意：peak_price 在 PaperAccount 中代表「最有利價格」(LONG 為最高價、SHORT 為最低價)，不可把 SHORT 的 peak_price 覆寫為 high_seen！
            current_position["peak_price"] = high_seen if pos_side == "LONG" else low_seen
            if hasattr(account, "position_meta") and isinstance(account.position_meta, dict):
                p_meta = account.position_meta.setdefault(symbol, {})
                p_meta["highest_price"] = high_seen
                p_meta["lowest_price"] = low_seen
                p_meta["trough_price"] = low_seen

            gain_from_entry = max(0.0, (high_seen - pos_entry) if pos_side == "LONG" else (pos_entry - low_seen))
            cur_gain = (curr_quote - pos_entry) if pos_side == "LONG" else (pos_entry - curr_quote)
            cur_roe_pct = (cur_gain / pos_entry * 100.0 * pos_lev) if pos_entry > 0 else 0.0
            max_roe_from_price_pct = (gain_from_entry / pos_entry * 100.0 * pos_lev) if pos_entry > 0 else 0.0

            stored_peak_roe_pct = float(
                current_position.get("peak_net_roe_pct", 0.0)
                or current_position.get("highest_net_roe", 0.0)
                or 0.0
            )
            peak_roe_pct = max(stored_peak_roe_pct, max_roe_from_price_pct, cur_roe_pct)
            current_position["peak_roe_pct"] = peak_roe_pct
            # ExitGate 內部使用比例制 (0.05 = 5% ROE)，因此傳遞給 ExitGate 的 peak_roe 統一儲存為比例制
            current_position["peak_roe"] = peak_roe_pct / 100.0

            # 審查 ExitGate 是否觸發鎖利 (Profit Lock)、大瀑布 (Extreme Waterfall)、市場熔斷 (Circuit Breaker)
            gate_should_exit, gate_reason, gate_meta = ExitGate.evaluate(
                pos=current_position,
                market_data=market_data,
            )
            is_absolute_safety = bool(gate_should_exit and gate_reason in ExitGate.ABSOLUTE_SAFETY_REASONS)
            is_profit_lock = bool(
                gate_should_exit
                and (
                    gate_reason in ExitGate.PROFIT_LOCK_EXIT_REASONS
                    or "PROFIT_LOCK" in str(gate_reason).upper()
                    or "RATCHET" in str(gate_reason).upper()
                    or "TRAILING_LOCK" in str(gate_reason).upper()
                )
            )
            is_pivot_exit = bool(
                gate_should_exit
                and (
                    gate_reason in ExitGate.PIVOT_EXIT_REASONS
                    or any(k in str(gate_reason).upper() for k in (
                        "EARLY_PIVOT", "TRUE_PEAK", "TRUE_VALLEY",
                        "CLOSE_LONG_AT_TOP", "CLOSE_SHORT_AT_BOTTOM",
                        "FRACTAL_PEAK", "FRACTAL_VALLEY",
                    ))
                )
            )

            should_exit = False
            reason = ""
            meta = {}

            # 擷取最近一根「已收線 K 棒」與多棒 KC 通道趨勢，避免盤中未收線毛刺或單棒斜率微幅抖動誤觸發平倉
            closed_k = c_k
            prev_closed_k = p_k
            if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 2:
                last_row_closed = bool(raw_df.iloc[-1].get("is_closed", True))
                if not last_row_closed and len(raw_df) >= 3:
                    closed_k = raw_df.iloc[-2]
                    prev_closed_k = raw_df.iloc[-3]
                elif not last_row_closed:
                    closed_k = raw_df.iloc[-2]
                    prev_closed_k = raw_df.iloc[-2]
                else:
                    closed_k = raw_df.iloc[-1]
                    prev_closed_k = raw_df.iloc[-2]

            closed_c = float(closed_k.get("close", c_c))
            closed_o = float(closed_k.get("open", c_o))
            closed_h = float(closed_k.get("high", c_h))
            closed_l = float(closed_k.get("low", c_l))
            closed_kc_u = float(closed_k.get("kc_upper", kc_u))
            closed_kc_lo = float(closed_k.get("kc_lower", kc_lo))
            closed_kc_mid = float(closed_k.get("kc_middle", kc_mid))
            prev_closed_kc_mid = float(prev_closed_k.get("kc_middle", p_kc_mid))
            prev_closed_c = float(prev_closed_k.get("close", p_c))
            prev_closed_o = float(prev_closed_k.get("open", p_o))
            prev_closed_h = float(prev_closed_k.get("high", p_h))
            prev_closed_l = float(prev_closed_k.get("low", p_l))

            is_kc_mid_up = bool(
                (p_kc_mid > 0 and kc_mid >= p_kc_mid - 1e-9)
                or (prev_closed_kc_mid > 0 and closed_kc_mid >= prev_closed_kc_mid - 1e-9)
                or (kc_slope >= -0.00001)
                or (pos_side == "LONG" and (curr_quote >= kc_mid or closed_c >= closed_kc_mid) and curr_quote >= kc_lo)
            )
            is_kc_mid_down = bool(
                (p_kc_mid > 0 and kc_mid <= p_kc_mid + 1e-9)
                or (prev_closed_kc_mid > 0 and closed_kc_mid <= prev_closed_kc_mid + 1e-9)
                or (kc_slope <= 0.00001)
                or (pos_side == "SHORT" and (curr_quote <= kc_mid or closed_c <= closed_kc_mid) and curr_quote <= kc_u)
            )

            # 🛡️ 大週期不變順勢鎖倉保護：
            # 當多單處於 KC向上 / 中軌上方，或空單處於 KC向下 / 中軌下方時，
            # 屏蔽普通時間停損與盤中雜訊回調，僅允許「鎖利 (Profit Lock)」、「真峰頂/真谷底 (Confirmed True Pivot)」與「破反側外軌/大瀑布」出場！
            trend_hold_shield = bool(
                (pos_side == "LONG" and is_kc_mid_up and closed_c >= closed_kc_mid and curr_quote >= kc_mid)
                or (pos_side == "SHORT" and is_kc_mid_down and closed_c <= closed_kc_mid and curr_quote <= kc_mid)
            )

            # 計算持倉波段是否已實質展開獲利 (>= 1.2 ATR 或 峰值 ROE >= 5%)，防範在剛開倉成本區誤把正常呼吸當真峰谷
            pos_entry_ref = float(current_position.get("entry_price") or 0.0)
            peak_p_ref = float(current_position.get("highest_price") or pos_entry_ref)
            trough_p_ref = float(current_position.get("trough_price") or current_position.get("lowest_price") or pos_entry_ref)
            favorable_gain_atr = (
                ((peak_p_ref - pos_entry_ref) / atr_v) if (pos_side == "LONG" and atr_v > 0 and pos_entry_ref > 0)
                else ((pos_entry_ref - trough_p_ref) / atr_v) if (pos_side == "SHORT" and atr_v > 0 and pos_entry_ref > 0)
                else 0.0
            )
            has_profit_expansion = bool((favorable_gain_atr >= 1.2 or peak_roe_pct >= 5.0) and cur_roe_pct > 0.5)

            # 🎯 最高優先級：1. 絕對安全風控  2. 階梯鎖利 (Profit Lock 無視 trend_hold_shield 立即落袋)  3. 實質獲利展開後之真峰頂/真谷底
            if is_absolute_safety:
                # 若為 OPPOSITE_KC_BAND_BREACH，必須確認已收線實體真正破軌，拒絕下影線/上影線毛刺誤平倉
                if "OPPOSITE_KC_BAND_BREACH" in str(gate_reason).upper():
                    # 拉遠大週期仍與持倉同向時，須已收線實體破軌深度 >= 0.5 ATR 才放行（拒絕盤中毛刺）
                    long_need = 0.5 * atr_v if not is_zoomed_out_bearish else 0.0
                    short_need = 0.5 * atr_v if not is_zoomed_out_bullish else 0.0
                    if pos_side == "LONG" and not (
                        closed_c < closed_kc_lo - long_need and closed_c < closed_o and curr_quote < kc_lo
                    ):
                        is_absolute_safety = False
                    elif pos_side == "SHORT" and not (
                        closed_c > closed_kc_u + short_need and closed_c > closed_o and curr_quote > kc_u
                    ):
                        is_absolute_safety = False
                # 若為 EXIT_BY_EXTREME_WATERFALL，必須確認是「已收線 K 棒」且實體 >= 2.0 ATR 貫穿中軌並跌破/突破反側或慢線，拒絕盤中未收線 K 棒誤觸發！
                elif "EXTREME_WATERFALL" in str(gate_reason).upper():
                    if pos_side == "LONG" and not (
                        (closed_o - closed_c >= 2.0 * atr_v)
                        and (closed_o >= closed_kc_mid and closed_c < closed_kc_mid)
                        and (closed_c < m15_c and curr_quote <= closed_c)
                    ):
                        is_absolute_safety = False
                    elif pos_side == "SHORT" and not (
                        (closed_c - closed_o >= 2.0 * atr_v)
                        and (closed_o <= closed_kc_mid and closed_c > closed_kc_mid)
                        and (closed_c > m15_c and curr_quote >= closed_c)
                    ):
                        is_absolute_safety = False

            if is_absolute_safety:
                should_exit = True
                reason = gate_reason
                meta = gate_meta
                logger.warning(f"🚨 [ABSOLUTE_SAFETY_EXIT] {symbol} {pos_side} 命中絕對安全出場防線！reason={reason}")
            elif is_profit_lock and not trend_hold_shield and (
                (pos_side == "LONG" and not is_zoomed_out_bullish and kc_slope <= 0 and m3_c < m15_c and closed_c < closed_kc_mid and closed_c < m15_c and curr_quote < kc_mid and curr_quote < m15_c)
                or (pos_side == "SHORT" and not is_zoomed_out_bearish and kc_slope >= 0 and m3_c > m15_c and closed_c > closed_kc_mid and closed_c > m15_c and curr_quote > kc_mid and curr_quote > m15_c)
            ):
                should_exit = True
                reason = gate_reason or "EXIT_BY_RATCHET_PROFIT_LOCK"
                meta = {**gate_meta, "pivot_type": "PROFIT_LOCK", "trigger": "STAGE_PROFIT_LOCK"}
                logger.info(f"💰 [PROFIT_LOCK_EXECUTION] {symbol} {pos_side} 命中階梯鎖利防線且大週期結構已轉折！立即放行出場保利！reason={reason}, meta={meta}")
            elif is_pivot_exit and (has_profit_expansion or favorable_gain_atr >= 1.2 or peak_roe_pct >= 5.0):
                # 真峰頂/真谷底獲利了結：
                # 嚴格遵守「大週期不變絕不平倉」與「KC 外軌鎖定機制」！
                # 1. 空單若大週期仍為空頭 (is_zoomed_out_bearish)、或 KC 中軌仍向下 (kc_slope < 0)、或價格仍在 KC 中軌與 MA15 之下，絕對禁止提早平空！
                # 2. 多單若大週期仍為多頭 (is_zoomed_out_bullish)、或 KC 中軌仍向上 (kc_slope > 0)、或價格仍在 KC 中軌與 MA15 之上，絕對禁止提早平多！
                ma5_ref = float(getattr(market_data, "ma5", m3_c) or m3_c)
                touched_outer_rail = (
                    (pos_side == "LONG" and (peak_p_ref >= closed_kc_u * 0.9985 or prev_closed_h >= closed_kc_u * 0.9985 or closed_h >= closed_kc_u * 0.9985))
                    or (pos_side == "SHORT" and (trough_p_ref <= closed_kc_lo * 1.0015 or prev_closed_l <= closed_kc_lo * 1.0015 or closed_l <= closed_kc_lo * 1.0015))
                )
                prev_mid_pt = (prev_closed_o + prev_closed_c) / 2.0
                is_valid_pivot_candle = (
                    touched_outer_rail
                    and (
                        (
                            pos_side == "LONG"
                            and (not is_zoomed_out_bullish or kc_slope <= 0)
                            and kc_slope <= 0
                            and m3_c < m15_c
                            and closed_c < closed_kc_u
                            and closed_c < closed_o
                            and curr_quote <= closed_c
                            and closed_c < m3_c
                            and closed_c < ma5_ref
                            and m3_c < m3_p
                            and (closed_c < closed_kc_mid and closed_c < m15_c)
                            and (curr_quote < kc_mid and curr_quote < m15_c)
                            and (closed_c < prev_mid_pt or closed_c < prev_closed_l)
                        )
                        or (
                            pos_side == "SHORT"
                            and (not is_zoomed_out_bearish or kc_slope >= 0)
                            and kc_slope >= 0
                            and m3_c > m15_c
                            and closed_c > closed_kc_lo
                            and closed_c > closed_o
                            and curr_quote >= closed_c
                            and closed_c > m3_c
                            and closed_c > ma5_ref
                            and m3_c > m3_p
                            and (closed_c > closed_kc_mid and closed_c > m15_c)
                            and (curr_quote > kc_mid and curr_quote > m15_c)
                            and (closed_c > prev_mid_pt or closed_c > prev_closed_h)
                        )
                    )
                )
                if is_valid_pivot_candle:
                    should_exit = True
                    reason = gate_reason
                    meta = {**(gate_meta or {}), "pivot_type": "TRUE_PIVOT_CONFIRMED", "trigger": "EXIT_GATE_TRUE_PIVOT"}
                    logger.info(f"🏔️ [TRUE_PIVOT_EXIT] {symbol} {pos_side} 波段獲利展開且確立真峰頂/真谷底結構反轉，立即獲利了結！reason={reason}")

            # 🛡️ 妖幣專用主動式動態追蹤止盈與時間停損：依使用者指示「不要硬止損，走到峰頂/谷底或結構破壞再平倉」全面停用！
            # 🎯 方案 B 核心狀態機 (大週期不變一單到底，唯鎖利或真峰谷或結構破壞平倉，取消固定硬止損):
            if not should_exit:
                # 🔴 空頭持倉中 (SHORT) - 大波段谷底持倉模式 (1000PEPE & 龍蝦一路抱到底，大週期不變中間絕對不平空)：
                if pos_side == "SHORT":
                    trough_p = float(current_position.get("trough_price") or current_position.get("lowest_price") or pos_entry)
                    outer_touched_short = bool(
                        trough_p <= closed_kc_lo * 1.001
                        or prev_closed_l <= closed_kc_lo
                        or closed_l <= closed_kc_lo
                        or ((current_position.get("trend_lock_state") or {}).get("outer_rail_touched"))
                    )

                    # 0. 【趨勢正式變多頭防線 (Bullish Trend Reversal)】：
                    # 嚴格區分「空頭下降通道中的反彈觸中軌（如 1000PEPE 21:17 & 21:57）」與「真正變多頭」！
                    # 必須同時滿足：
                    # 1. 1M 結構已正式確立多頭 (is_1m_confirmed_bull) 且非大週期空頭鎖定 (not is_zoomed_out_bearish)
                    # 2. 5棒與10棒 KC 中軌及 MA15 慢線同步拐頭向上 (kc_slope_5_ref > 0 且 m15_c > m15_p)
                    # 3. 已收線 K 棒與即時報價【雙雙站穩】KC 中軌與 MA15 之上
                    is_bullish_trend_reversal = bool(
                        is_1m_confirmed_bull
                        and (not is_zoomed_out_bearish)
                        and (m3_c > m15_c)
                        and (m3_c > m3_p)
                        and (kc_slope > 0 and kc_slope_5_ref > 0)
                        and (m15_c > m15_p and m15_slope_5_ref >= 0)
                        and (closed_c > closed_o)
                        and (closed_c > closed_kc_mid and closed_c > m15_c)
                        and (curr_quote > kc_mid and curr_quote > m15_c)
                        and (closed_c > prev_closed_h and (m3_c - m15_c) >= 0.15 * atr_v)
                    )

                    # 1. 【結構破壞防線】：只認「已收線」實體突破 KC 上軌（盤中上影線毛刺一律不算）。
                    short_breach_need = 0.5 * atr_v if not is_zoomed_out_bullish else 0.0
                    is_upper_rail_breach = bool(
                        (closed_c > closed_kc_u + short_breach_need and closed_c > closed_o and curr_quote > kc_u)
                        or (
                            kc_slope > 0
                            and not is_zoomed_out_bearish
                            and (m3_c > m15_c)
                            and closed_c > closed_o
                            and closed_c > closed_kc_mid
                            and closed_c > m15_c
                            and curr_quote > kc_mid
                            and swing_high_price is not None
                            and closed_c > float(swing_high_price)
                        )
                    )

                    # 2. 【真谷底確認平空 (True Valley Exit)】：
                    # 嚴格鐵律：大週期空頭未變、KC 中軌仍向下 (kc_slope < 0)、或價格仍在 KC 中軌/MA15 下方時，100% 鎖死不平空！
                    # 必須確認：收紅陽線突破 KC 中軌與 MA15 雙防線，且 MA3 > MA15、KC 中軌不再向下 (kc_slope >= 0)，才算真谷底反轉！
                    closed_body = abs(closed_c - closed_o)
                    prev_closed_body = abs(prev_closed_c - prev_closed_o)
                    prev_closed_lower_wick = min(prev_closed_o, prev_closed_c) - prev_closed_l
                    prev_closed_mid_short = (prev_closed_o + prev_closed_c) / 2.0
                    ma5_short_ref = float(getattr(market_data, "ma5", m3_c) or m3_c)
                    is_confirmed_true_valley = bool(
                        (has_profit_expansion or favorable_gain_atr >= 1.5 or peak_roe_pct >= 6.0)
                        and outer_touched_short
                        and (not is_zoomed_out_bearish)
                        and kc_slope >= 0
                        and (m3_c > m15_c)
                        and closed_c > closed_kc_lo
                        and closed_c > closed_o
                        and curr_quote >= closed_c
                        and closed_c > m3_c
                        and closed_c > ma5_short_ref
                        and m3_c > m3_p
                        and (closed_c > closed_kc_mid and closed_c > m15_c)
                        and (curr_quote > kc_mid and curr_quote > m15_c)
                        and (closed_c > prev_closed_h or closed_body >= 0.4 * atr_v)
                    )

                    # 3. 【大波段移動鎖利】：自最低谷底價格 (trough_price) 反彈超過 1.8% 且同時突破 KC 中軌與 MA15，動態鎖利平空
                    is_valley_rebound_1_8pct = bool(
                        trough_p > 0
                        and pos_entry > 0
                        and ((pos_entry - trough_p) / pos_entry >= 0.015 or has_profit_expansion)
                        and (closed_c > trough_p * 1.018 and curr_quote > trough_p * 1.018 and closed_c > closed_o and closed_c > closed_kc_mid and closed_c > m15_c)
                    )

                    # 4. 【妖幣專屬：前高插針點外加 1.2% 緩衝防二次拉爆止損】：
                    meme_wick_sl_short = float(current_position.get("meme_wick_stop_loss") or 0.0)
                    is_hard_sl_short = bool(
                        meme_wick_sl_short > 0
                        and closed_c > meme_wick_sl_short
                        and curr_quote > meme_wick_sl_short
                        and closed_c > closed_o
                    )

                    # ⚠️ 【大週期不變谷底持倉鐵律】：
                    # 只要大週期空頭未變，未發生趨勢翻多、結構破壞、真谷底轉折、谷底破中軌鎖利時，
                    # 一票否決平空 (VETO_HOLD)，中間絕對不平空，從第一個賣空一路抱到底吃滿整個主跌段！
                    if not is_bullish_trend_reversal and not is_upper_rail_breach and not is_confirmed_true_valley and not is_valley_rebound_1_8pct and not is_hard_sl_short:
                        logger.info(
                            f"🛡️ [TREND_LOCK_VALLEY_MODE] {symbol} 空單大波段一單到底模式: (slope={kc_slope:.6f}, close={closed_c:.6f}, kc_mid={closed_kc_mid:.6f})，"
                            f"大週期空頭未變且結構未破壞，中間反彈小陽線一律視為洗盤雜訊，一票否決平空，強制續抱吃滿主跌段！"
                        )
                        return {"status": "HOLD", "symbol": symbol, "reason": "TREND_LOCK_MASTER_LOCK_SHORT"}

                    if is_hard_sl_short:
                        should_exit = True
                        reason = "CLOSE_SHORT_MEME_WICK_BUFFER_SL"
                        meta = {"pivot_type": "MEME_WICK_STOP_LOSS", "trigger": "ABOVE_PINBAR_HIGH_BUFFER", "stop_loss": meme_wick_sl_short}
                    elif is_bullish_trend_reversal:
                        should_exit = True
                        reason = "CLOSE_SHORT_ON_BULLISH_REVERSAL"
                        meta = {"pivot_type": "BULLISH_TREND_REVERSAL", "trigger": "MA3_ABOVE_MA15_AND_KC_MID", "trough_price": trough_p}
                    elif is_upper_rail_breach:
                        should_exit = True
                        reason = "CLOSE_SHORT_AT_UPPER_RAIL_BREACH"
                        meta = {"pivot_type": "UPPER_RAIL_BREACH", "trigger": "CLOSE_ABOVE_KC_UPPER"}
                    elif is_confirmed_true_valley:
                        should_exit = True
                        reason = "CLOSE_SHORT_AT_BOTTOM"
                        meta = {"pivot_type": "TRUE_VALLEY", "trigger": "CONFIRMED_TRUE_VALLEY_PIVOT", "trough_price": trough_p}
                    elif is_valley_rebound_1_8pct:
                        should_exit = True
                        reason = "CLOSE_SHORT_AT_VALLEY_REBOUND_2PCT"
                        meta = {"pivot_type": "VALLEY_REBOUND", "trigger": "REBOUND_OVER_1_8_PCT", "trough_price": trough_p}
                    else:
                        return {"status": "HOLD", "symbol": symbol, "reason": "TREND_LOCK_MASTER_LOCK_SHORT"}

                # 🟢 多頭持倉中 (LONG) - 大波段持倉鎖定模式 (從第一個開多一路抱到真峰頂或結構破壞，大週期不變中間絕對不平多)：
                elif pos_side == "LONG":
                    pos_entry_price = float(current_position.get("entry_price") or 0.0)
                    peak_p = float(current_position.get("highest_price") or pos_entry_price)
                    outer_touched_long = bool(
                        peak_p >= closed_kc_u * 0.999
                        or prev_closed_h >= closed_kc_u
                        or closed_h >= closed_kc_u
                        or ((current_position.get("trend_lock_state") or {}).get("outer_rail_touched"))
                    )

                    # 0. 【趨勢正式變空頭防線 (Bearish Trend Reversal)】：
                    # 嚴格區分「多頭回踩中軌/MA15」與「真正變空頭」！
                    # 必須同時滿足：
                    # 1. 1M 結構已正式確立空頭 (is_1m_confirmed_bear) 且非大週期多頭鎖定 (not is_zoomed_out_bullish)
                    # 2. 5棒與10棒 KC 中軌及 MA15 慢線同步拐頭向下 (kc_slope_5_ref < 0 且 m15_c < m15_p)
                    # 3. 已收線 K 棒與即時報價【雙雙跌破】KC 中軌與 MA15 之下
                    is_bearish_trend_reversal = bool(
                        is_1m_confirmed_bear
                        and (not is_zoomed_out_bullish)
                        and (m3_c < m15_c)
                        and (m3_c < m3_p)
                        and (kc_slope < 0 and kc_slope_5_ref < 0)
                        and (m15_c < m15_p and m15_slope_5_ref <= 0)
                        and (closed_c < closed_o)
                        and (closed_c < closed_kc_mid and closed_c < m15_c)
                        and (curr_quote < kc_mid and curr_quote < m15_c)
                        and (closed_c < prev_closed_l and (m15_c - m3_c) >= 0.15 * atr_v)
                    )

                    # 1. 【結構破壞防線】：只認「已收線」實體跌破 KC 下軌（盤中下影線毛刺一律不算）。
                    long_breach_need = 0.5 * atr_v if not is_zoomed_out_bearish else 0.0
                    is_lower_rail_breach = bool(
                        (closed_c < closed_kc_lo - long_breach_need and closed_c < closed_o and curr_quote < kc_lo)
                        or (
                            kc_slope < 0
                            and not is_zoomed_out_bullish
                            and (m3_c < m15_c)
                            and closed_c < closed_o
                            and closed_c < closed_kc_mid
                            and closed_c < m15_c
                            and curr_quote < kc_mid
                            and swing_low_price is not None
                            and closed_c < float(swing_low_price)
                        )
                    )

                    # 2. 【真峰頂確認平多 (True Peak Exit)】：
                    # 嚴格鐵律：大週期多頭未變、KC 中軌仍向上 (kc_slope > 0)、或價格仍在 KC 中軌/MA15 上方（如龍蝦 21:13 回踩中軌但 KC 中軌仍向上且 MA3 > MA15）時，
                    # 100% 鎖死不平多！
                    # 必須確認：收黑陰線同時跌破 KC 中軌與 MA15 雙防線，且 MA3 < MA15、KC 中軌不再向上 (kc_slope <= 0)，才算真峰頂反轉！
                    closed_body = abs(closed_c - closed_o)
                    prev_closed_body = abs(prev_closed_c - prev_closed_o)
                    prev_closed_upper_wick = prev_closed_h - max(prev_closed_o, prev_closed_c)
                    prev_closed_mid_long = (prev_closed_o + prev_closed_c) / 2.0
                    ma5_long_ref = float(getattr(market_data, "ma5", m3_c) or m3_c)
                    is_confirmed_true_peak = bool(
                        (has_profit_expansion or favorable_gain_atr >= 1.5 or peak_roe_pct >= 6.0)
                        and outer_touched_long
                        and (not is_zoomed_out_bullish)
                        and kc_slope <= 0
                        and (m3_c < m15_c)
                        and closed_c < closed_kc_u
                        and closed_c < closed_o
                        and curr_quote <= closed_c
                        and closed_c < m3_c
                        and closed_c < ma5_long_ref
                        and m3_c < m3_p
                        and (closed_c < closed_kc_mid and closed_c < m15_c)
                        and (curr_quote < kc_mid and curr_quote < m15_c)
                        and (closed_c < prev_closed_l or closed_body >= 0.4 * atr_v)
                    )

                    # 3. 【大波段移動鎖利】：盈利達標後，自最高點回撤超過 1.8% 且同時跌破 KC 中軌與 MA15，精準鎖利平多
                    is_peak_pullback_2pct = bool(
                        peak_p > 0
                        and pos_entry_price > 0
                        and ((peak_p - pos_entry_price) / pos_entry_price >= 0.015 or has_profit_expansion)
                        and (closed_c < peak_p * 0.982 and curr_quote < peak_p * 0.982 and closed_c < closed_o and closed_c < closed_kc_mid and closed_c < m15_c)
                    )

                    # 4. 【妖幣專屬：前低插針點外加 1.2% 緩衝防二次下砸止損】：
                    # 僅當該筆多單由「妖幣 3 倍超長下影線插針 / 支撐預警吸籌」觸發進場且設有 wick_buffer_sl 時啟動！
                    meme_wick_sl_long = float(current_position.get("meme_wick_stop_loss") or 0.0)
                    is_hard_sl_long = bool(
                        meme_wick_sl_long > 0
                        and closed_c < meme_wick_sl_long
                        and curr_quote < meme_wick_sl_long
                        and closed_c < closed_o
                    )

                    # ⚠️ 【大週期不變持倉鐵律】：
                    if not is_bearish_trend_reversal and not is_lower_rail_breach and not is_confirmed_true_peak and not is_peak_pullback_2pct and not is_hard_sl_long:
                        logger.info(
                            f"🛡️ [TREND_LOCK_MASTER_LOCK_LONG] {symbol} 多單大波段持倉鎖定: (slope={kc_slope:.6f})，"
                            f"回踩中軌、下影線洗盤或均線死叉一律視為正常呼吸，一票否決平多，強制鎖死持倉一單到底！"
                        )
                        return {"status": "HOLD", "symbol": symbol, "reason": "TREND_LOCK_MASTER_LOCK_LONG"}

                    if is_hard_sl_long:
                        should_exit = True
                        reason = "CLOSE_LONG_MEME_WICK_BUFFER_SL"
                        meta = {"pivot_type": "MEME_WICK_STOP_LOSS", "trigger": "BELOW_PINBAR_LOW_BUFFER", "stop_loss": meme_wick_sl_long}
                    elif is_bearish_trend_reversal:
                        should_exit = True
                        reason = "CLOSE_LONG_ON_BEARISH_REVERSAL"
                        meta = {"pivot_type": "BEARISH_TREND_REVERSAL", "trigger": "MA3_BELOW_MA15_AND_KC_MID", "peak_price": peak_p}
                    elif is_lower_rail_breach:
                        should_exit = True
                        reason = "CLOSE_LONG_AT_LOWER_RAIL_BREACH"
                        meta = {"pivot_type": "LOWER_RAIL_BREACH", "trigger": "CLOSE_BELOW_KC_LOWER"}
                    elif is_confirmed_true_peak:
                        should_exit = True
                        reason = "CLOSE_LONG_AT_TOP"
                        meta = {"pivot_type": "TRUE_PEAK", "trigger": "CONFIRMED_TRUE_PEAK_PIVOT", "peak_price": peak_p}
                    elif is_peak_pullback_2pct:
                        should_exit = True
                        reason = "CLOSE_LONG_AT_PEAK_PULLBACK_2PCT"
                        meta = {"pivot_type": "TRAILING_STOP", "trigger": "PEAK_PULLBACK_OVER_2_PCT", "peak_price": peak_p}
                    else:
                        return {"status": "HOLD", "symbol": symbol, "reason": "TREND_LOCK_MASTER_LOCK_LONG"}

            if should_exit:

                logger.info(f"🚀 [EXIT_TRIGGERED] {symbol} {reason} | meta={meta}")
                print(f"🚀 [EXIT_TRIGGERED] {symbol} {reason}", flush=True)
                exit_price = market_data.current_price
                exit_meta = {**(meta or {}), "raw_df": getattr(market_data, "raw_df", None)}
                success = await cls.close_position(
                    account=account,
                    symbol=symbol,
                    price=exit_price,
                    reason=f"ExitGate:{reason}",
                    meta=exit_meta,
                )
                if not success:
                    return {"status": "HOLD", "symbol": symbol, "reason": f"EXIT_BLOCKED_HOLD:{reason}"}

                # ══════════════════════════════════════════════════════════════════
                # 🔄 關鍵秒級原子反手邏輯 (Flip Position): 平倉與反手在同根 K 棒一次性完成！
                # ══════════════════════════════════════════════════════════════════
                # 🔴 空頭平倉 -> 谷底真轉折秒級反手開多 (Flip Short to Long)
                is_ma3_golden_cross = (m3_c > m15_c) and (m3_p <= m15_p)
                is_bullish_confirm = (c_c > c_o) and (m3_c > m3_p)
                is_pinbar_bottom = (c_lo_w >= 1.2 * c_bd) and (c_c > c_o) and (c_l <= kc_lo)
                is_engulfing_bottom = (p_c < p_kc_lo) and (c_c > c_o) and (c_bd >= 0.6 * p_bd)
                is_real_bottom = (
                    is_true_bottom
                    or is_ma3_golden_cross
                    or is_pinbar_bottom
                    or is_engulfing_bottom
                    or (m3_c > m15_c and is_bullish_confirm)
                    or "CLOSE_SHORT_AT_BOTTOM" in str(reason).upper()
                    or "VALLEY" in str(reason).upper()
                    or "BULLISH_TREND_REVERSAL" in str(reason).upper()
                )
                is_flip_to_long = bool(pos_side == "SHORT" and is_real_bottom)

                # 🟢 多頭平倉 -> 頂部真轉折秒級反手開空 (Flip Long to Short)
                is_ma3_dead_cross = (m3_c < m15_c) and (m3_p >= m15_p)
                is_bearish_confirm = (c_c < c_o) and (m3_c < m3_p)
                is_pinbar_peak = (c_up_w >= 1.2 * c_bd) and (c_c < c_o) and (c_h >= kc_u)
                is_engulfing_peak = (p_c > p_kc_u) and (c_c < c_o) and (c_bd >= 0.6 * p_bd)
                is_real_peak = (
                    is_true_peak
                    or is_ma3_dead_cross
                    or is_pinbar_peak
                    or is_engulfing_peak
                    or (m3_c < m15_c and is_bearish_confirm)
                    or "CLOSE_LONG_AT_TOP" in str(reason).upper()
                    or "PEAK" in str(reason).upper()
                    or "BEARISH_TREND_REVERSAL" in str(reason).upper()
                )
                is_flip_to_short = bool(pos_side == "LONG" and is_real_peak)

                # 🛡️ 大波段防雙巴鐵律 (Disable Early Flip)：平倉後嚴格禁止秒級原子反手！
                # 徹底解決「平多即刻開空、開空又秒平」的中軌雙殺死循環，一律強制進入正常冷卻與趨勢審查！
                can_flip = False
                logger.debug(f"🛡️ [DISABLE_EARLY_FLIP] {symbol} 平倉後依防洗防雙巴規則禁止秒級原子反手，進入標準冷卻期")

                if can_flip and (is_flip_to_long or is_flip_to_short):
                    flip_side = "LONG" if is_flip_to_long else "SHORT"
                    flip_entry_type = "PIVOT_FLIP_LONG" if is_flip_to_long else "PIVOT_FLIP_SHORT"
                    logger.info(f"🔄 [POSITION_FLIP] {symbol} {pos_side} 平倉後同 K 棒即刻秒級反手開 {flip_side}！(entry_type={flip_entry_type})")
                    print(f"🔄 [POSITION_FLIP] {symbol} {pos_side} 平倉後同 K 棒即刻秒級反手開 {flip_side}！", flush=True)

                    flip_stop_loss = None  # 方案 B 無硬止損版: 完全取消固定價位硬止損 (No Rigid Stop Loss)
                    flip_meta = {
                        "side": flip_side,
                        "direction_override": True,
                        "entry_type": flip_entry_type,
                        "candidate_signal_type": "SWING_PIVOT",
                        "is_swing_pivot": True,
                        "is_agile_trigger": True,
                        "is_cross_mid": True,
                        "no_rigid_sl": True,
                        "stop_loss_price": None,
                        "upstream_entry_gate_authorized": True,
                        "allow_kc_internal": True,
                        "strategy_mode": "AGILE_MOMENTUM",
                        "raw_df": klines_df,
                        "source": f"UNIFIED_PIPELINE|{flip_entry_type}",
                        "atr": atr_v,
                    }
                    raw_df_instance = flip_meta.get("raw_df")
                    if hasattr(raw_df_instance, "attrs"):
                        for k in ("direction_override", "is_agile_trigger", "is_cross_mid", "entry_type", "strategy_mode", "allow_kc_internal"):
                            raw_df_instance.attrs[k] = flip_meta.get(k, True)

                    flip_success = await cls.open_position(
                        engine_or_account=engine_or_account,
                        symbol=symbol,
                        side=flip_side,
                        price=exit_price,
                        reason=f"FlipPosition:{flip_entry_type}",
                        meta=flip_meta,
                    )
                    if flip_success:
                        logger.info(f"🎯 [POSITION_FLIP_SUCCESS] {symbol} 成功秒級反手建立 {flip_side} 倉位！")
                        print(f"🎯 [POSITION_FLIP_SUCCESS] {symbol} 成功秒級反手建立 {flip_side} 倉位！", flush=True)
                        return {"status": "SUCCESS_FLIP", "symbol": symbol, "side": flip_side}

                # 平倉成功且未觸發秒級反手時，重設持倉標記直通第 3 階段評估開倉
                has_position = False
                current_position = None
            else:
                logger.debug(f"🛡️ [HOLD] {symbol} {reason}")
                return {"status": "HOLD", "symbol": symbol, "reason": reason}

        # =========================================================================
        # 3. ⚪ 無持倉狀態 (NONE)：方案 B 多維開倉引擎
        # 優先級 1：真谷底 / 真峰頂起點開倉 (PIVOT_LONG / PIVOT_SHORT)
        # 優先級 2：兩根破軌極速開倉 (BREAKOUT)
        # 優先級 3：MA3 穿 KC 中軌 / MA3 與 MA15 交叉動能開倉
        # 優先級 4：順勢通道延續開倉
        # =========================================================================
        if not has_position:
            lookback_p = min(len(raw_df), 15) if raw_df is not None and isinstance(raw_df, pd.DataFrame) else 0
            if lookback_p >= 2:
                prior_lows = raw_df['low'].iloc[-lookback_p:-1]
                prior_highs = raw_df['high'].iloc[-lookback_p:-1]
                swing_low_p = float(prior_lows.min()) if len(prior_lows) > 0 else c_l
                swing_high_p = float(prior_highs.max()) if len(prior_highs) > 0 else c_h
                rise_from_low = c_c - swing_low_p
                drop_from_high = swing_high_p - c_c
            else:
                rise_from_low = 0.0
                drop_from_high = 0.0

            # 僅在真峰谷評估時過濾極端天花板/地板乖離（遠離軌道 > 2.5 ATR 且中軌離差 > 3.5%）
            mid_dist_ratio = abs(c_c - kc_mid) / kc_mid if kc_mid > 0 else 0.0
            is_chasing_high = bool((atr_v > 0 and rise_from_low >= 3.5 * atr_v and c_c >= (kc_u + 2.5 * atr_v)) or (c_c > kc_mid and mid_dist_ratio > 0.035))
            is_chasing_low = bool((atr_v > 0 and drop_from_high >= 3.5 * atr_v and c_c <= (kc_lo - 2.5 * atr_v)) or (c_c < kc_mid and mid_dist_ratio > 0.035))

            # 🛡️ 2026-10-29 新增三大開空/開多危險區防禦（防止砸在支撐牆上 / 超賣乖離追空 / 下軌外下影線止跌訊號開空）
            # 1. 前低/水平支撐位與前高壓力位過濾 (Support / Resistance Distance Filter)
            is_support_wall_block_short = False
            is_resistance_wall_block_long = False
            if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 6:
                lb_20 = min(len(raw_df), 21)
                prior_lows_20 = raw_df['low'].iloc[-lb_20:-1]
                prior_highs_20 = raw_df['high'].iloc[-lb_20:-1]
                recent_low_20 = float(prior_lows_20.min()) if len(prior_lows_20) > 0 else c_l
                recent_high_20 = float(prior_highs_20.max()) if len(prior_highs_20) > 0 else c_h

                lb_48 = min(len(raw_df), 48)
                prior_lows_wave = raw_df['low'].iloc[-lb_48:-3] if len(raw_df) > 4 else prior_lows_20
                prior_highs_wave = raw_df['high'].iloc[-lb_48:-3] if len(raw_df) > 4 else prior_highs_20
                wave_support_low = float(prior_lows_wave.min()) if len(prior_lows_wave) > 0 else recent_low_20
                wave_resistance_high = float(prior_highs_wave.max()) if len(prior_highs_wave) > 0 else recent_high_20

                dist_low_20 = abs(c_c - recent_low_20) / c_c if c_c > 0 else 1.0
                dist_wave_low = abs(c_c - wave_support_low) / c_c if c_c > 0 else 1.0
                is_clean_support_breakdown = bool(
                    atr_v > 0
                    and c_c < min(recent_low_20, wave_support_low) - 0.35 * atr_v
                    and c_bd >= 0.65 * atr_v
                    and c_lo_w <= 0.25 * c_bd
                )
                if (dist_low_20 < 0.002 or dist_wave_low < 0.002) and not is_clean_support_breakdown:
                    is_support_wall_block_short = True

                dist_high_20 = abs(c_c - recent_high_20) / c_c if c_c > 0 else 1.0
                dist_wave_high = abs(c_c - wave_resistance_high) / c_c if c_c > 0 else 1.0
                is_clean_resistance_breakout = bool(
                    atr_v > 0
                    and c_c > max(recent_high_20, wave_resistance_high) + 0.35 * atr_v
                    and c_bd >= 0.65 * atr_v
                    and c_up_w <= 0.25 * c_bd
                )
                if (dist_high_20 < 0.002 or dist_wave_high < 0.002) and not is_clean_resistance_breakout:
                    is_resistance_wall_block_long = True

            # 2. 均線/外軌乖離率過濾 (Oversold / Overbought Filter)
            ma5_val_ref = float(getattr(market_data, "ma5", m3_c) or m3_c)
            ma5_bias_ref = (c_c - ma5_val_ref) / ma5_val_ref if ma5_val_ref > 0 else 0.0
            is_oversold_block_short = bool(
                ma5_bias_ref < -0.015
                or (
                    (c_c < kc_lo or curr_quote < kc_lo)
                    and (
                        ma5_bias_ref < -0.0035
                        or (atr_v > 0 and (ma5_val_ref - c_c) > 1.0 * atr_v)
                        or (atr_v > 0 and (m15_c - c_c) > 1.8 * atr_v)
                    )
                )
            )
            is_overbought_block_long = bool(
                ma5_bias_ref > 0.015
                or (
                    (c_c > kc_u or curr_quote > kc_u)
                    and (
                        ma5_bias_ref > 0.0035
                        or (atr_v > 0 and (c_c - ma5_val_ref) > 1.0 * atr_v)
                        or (atr_v > 0 and (c_c - m15_c) > 1.8 * atr_v)
                    )
                )
            )

            # 3. 外軌穿透與影線止跌/受阻限制 (KC Outer Band Penetration & Shadow Filter)
            is_kc_lower_shadow_block_short = bool((c_c < kc_lo or c_l <= kc_lo or curr_quote < kc_lo) and c_lo_w > c_bd)
            is_kc_upper_shadow_block_long = bool((c_c > kc_u or c_h >= kc_u or curr_quote > kc_u) and c_up_w > c_bd)

            allow_short_defense = not (is_support_wall_block_short or is_oversold_block_short or is_kc_lower_shadow_block_short)
            allow_long_defense = not (is_resistance_wall_block_long or is_overbought_block_long or is_kc_upper_shadow_block_long)

            pivot_triggered = False
            candidate_side = None
            entry_type = None

            # 🟢 2. 無持倉狀態 (NONE) 的起漲/起跌點防錯失機制 + 妖幣專用 K 線與量能重塑邏輯
            # 支援已收線 K 棒或盤中即時確認觸發，避免因每分鐘 59 秒未收線而錯過起點！
            c_is_closed = True
            # 支援【MA3 穿越 KC 中軌】與【MA3 交叉 MA15】雙重敏捷觸發！
            is_valid_gc = is_valid_golden_cross(m3_c, m3_p, m15_c, m15_p, atr_v)
            is_valid_dc = is_valid_dead_cross(m3_c, m3_p, m15_c, m15_p, atr_v)

            # ── 🧬 【妖幣專用 - K 線型態與量能爆發重塑邏輯】 ──
            curr_vol_now = float(c_k.get("volume", 0.0) or 0.0)
            prev_vol_now = float(p_k.get("volume", 0.0) or 0.0)
            vol_ma20_now = float(c_k.get("volume_ma20", getattr(market_data, "volume_ma20", 0.0)) or 0.0)
            if vol_ma20_now <= 0 and raw_df is not None and isinstance(raw_df, pd.DataFrame) and "volume" in raw_df.columns and len(raw_df) >= 5:
                vol_ma20_now = float(raw_df["volume"].astype(float).rolling(20, min_periods=1).mean().iloc[-1] or 0.0)

            # (1) 大實體向下（強空砸盤）：大實體陰線下砸且幾乎無下影線，視為主力強砸，嚴禁開多接飛刀！
            is_heavy_bear_smash = bool(
                c_c < c_o
                and atr_v > 0
                and c_bd >= 0.85 * atr_v
                and c_lo_w <= 0.15 * c_bd
            )
            # (2) 弱實體（小 K 線）：
            #   - 在「KC 向下 / 熊市」結構中：弱實體視為跌勢中繼，禁止開多。
            #   - 若「已跌至極限/打到關鍵支撐」且伴隨「極致縮量」(vol < 0.65 * vol_ma20)：進入預警區 (is_support_low_vol_alert)，等待下一根主力放量買盤啟動！
            is_weak_body_now = bool(atr_v > 0 and c_bd < 0.22 * atr_v)
            is_prev_weak_at_support_low_vol = bool(
                atr_v > 0
                and p_bd < 0.25 * atr_v
                and (vol_ma20_now <= 0 or prev_vol_now <= 0.70 * vol_ma20_now)
                and (p_l <= kc_lo * 1.003 or is_support_wall_block_short)
            )
            # (3) 妖幣核心開多訊號 A — 超長下影線（洗盤插針 >= 實體 3 倍以上 + 爆量拉回）：
            # 瞬間跌破下軌/支撐又爆量拉回且站回 MA3/中軌，代表主力爆破槓桿後吸籌！
            is_meme_3x_pinbar_long = bool(
                c_lo_w >= 3.0 * max(c_bd, 1e-12)
                and (c_l <= kc_lo or is_support_wall_block_short)
                and c_c >= c_o
                and c_c > kc_mid
                and m3_c > m15_c
                and (vol_ma20_now <= 0 or curr_vol_now >= 1.25 * vol_ma20_now)
            )
            # (4) 妖幣核心開多訊號 B — 實體突破 + 爆量（主力拉升）：
            # 中大實體綠棒直接站回均線 (MA3/MA15) 與 KC 中軌之上，且成交量顯著放大（或承接前根極致縮量支撐預警區），優先於慢速型態進場！
            is_meme_vol_surge_breakout_long = bool(
                c_c > c_o
                and atr_v > 0
                and c_bd >= 0.45 * atr_v
                and c_c > kc_mid
                and c_c > m15_c
                and m3_c > m15_c
                and m3_c > m3_p
                and c_up_w <= 0.6 * c_bd
                and (
                    (vol_ma20_now > 0 and curr_vol_now >= 1.35 * vol_ma20_now)
                    or (is_prev_weak_at_support_low_vol and (vol_ma20_now <= 0 or curr_vol_now >= 1.10 * vol_ma20_now))
                )
            )
            # 妖幣前低插針點外加 1.2% 緩衝硬性防二次下砸停損價
            meme_pinbar_low_ref = min(c_l, p_l)
            meme_wick_buffer_sl_long = meme_pinbar_low_ref * 0.988 if (is_meme_3x_pinbar_long or is_meme_vol_surge_breakout_long) else 0.0

            # ⚖️ 【黃金交叉 / 死亡交叉確立才開倉，且若先前交叉時慢線未轉向、現在已確認發散則立即補上車】：
            # 1. 嚴禁 MA3 與 MA15 剛微幅觸碰/未發散就急著開倉：必須具備明確發散間距 ((m3_c - m15_c) >= min_gc_dc_spread) 且具備實體動能棒！
            # 2. 解決「已經交叉好久卻不開倉」：只要 MA3 與 MA15 處於黃金交叉/死亡交叉發散狀態，且 KC 中軌 (1棒/3棒/5棒) 與 MA15 (1棒/3棒/5棒) 皆已同步順向，
            #    無論交叉發生在當根 K 棒還是前幾根 K 棒，只要當前空手 (not has_position)，立即確認進場！
            is_confirmed_golden_cross_ready = bool(
                (m3_c > m15_c)
                and ((m3_c - m15_c) >= min_gc_dc_spread)
                and (m3_c > m3_p)
                and (m15_c >= m15_p and m15_slope_3_ref >= 0 and m15_slope_5_ref >= 0)
                and (kc_slope > 0 and kc_slope_3_ref > 0 and kc_slope_5_ref >= 0)
                and (c_c > kc_mid and c_c > m15_c and c_c >= m3_c)
                and (c_c > c_o)
                and (atr_v <= 0 or c_bd >= 0.16 * atr_v)
                and (c_up_w <= 0.85 * max(c_bd, 1e-12))
            )
            is_confirmed_death_cross_ready = bool(
                (m3_c < m15_c)
                and ((m15_c - m3_c) >= min_gc_dc_spread)
                and (m3_c < m3_p)
                and (m15_c <= m15_p and m15_slope_3_ref <= 0 and m15_slope_5_ref <= 0)
                and (kc_slope < 0 and kc_slope_3_ref < 0 and kc_slope_5_ref <= 0)
                and (c_c < kc_mid and c_c < m15_c and c_c <= m3_c)
                and (c_c < c_o)
                and (atr_v <= 0 or c_bd >= 0.16 * atr_v)
                and (c_lo_w <= 0.85 * max(c_bd, 1e-12))
            )

            # MA3 穿越 KC 中軌（必須伴隨黃金交叉/死亡交叉發散確認，嚴禁未形成黃金/死亡交叉就單憑穿中軌搶跑開倉！）
            is_cross_mid_long = bool(
                kc_mid > 0
                and is_confirmed_golden_cross_ready
                and ((m3_c >= kc_mid and m3_p <= kc_mid) or (c_c >= kc_mid and (c_o <= kc_mid or p_c <= kc_mid)))
            )
            is_cross_mid_short = bool(
                kc_mid > 0
                and is_confirmed_death_cross_ready
                and ((m3_c <= kc_mid and m3_p >= kc_mid) or (c_c <= kc_mid and (c_o >= kc_mid or p_c >= kc_mid)))
            )

            # 嚴格順勢方向審查（精準開多開空，絕不開錯邊，拉遠看是空頭絕不開多、多頭絕不開空）：
            # 做多必須 KC 中軌向上 (kc_slope > 0, kc_slope_3 > 0, kc_slope_5 >= 0)、MA15 向上、拉遠非空頭 (not is_zoomed_out_bearish)、且黃金交叉發散確立；
            # 賣空必須 KC 中軌向下 (kc_slope < 0, kc_slope_3 < 0, kc_slope_5 <= 0)、MA15 向下、拉遠非多頭 (not is_zoomed_out_bullish)、且死亡交叉發散確立！
            can_open_long = bool(
                (
                    is_confirmed_golden_cross_ready
                    or is_cross_mid_long
                    or is_valid_gc
                    or is_true_bottom
                    or is_meme_3x_pinbar_long
                    or is_meme_vol_surge_breakout_long
                    or (is_1m_confirmed_bull and c_c >= m3_c)
                )
                and (not is_heavy_bear_smash)
                and (not (is_weak_body_now and kc_slope <= 0 and not is_meme_3x_pinbar_long))
                and (m3_c > m15_c)
                and ((m3_c - m15_c) >= min_gc_dc_spread or is_meme_3x_pinbar_long or is_meme_vol_surge_breakout_long)
                and (m15_c >= m15_p and m15_slope_3_ref >= 0 and m15_slope_5_ref >= 0)
                and (kc_slope_3_ref > 0 and kc_slope_5_ref >= 0)
                and (not is_ma3_midline_adhered or is_meme_vol_surge_breakout_long)
                and (not is_chop_zone or range_breakout_side == "LONG" or is_meme_vol_surge_breakout_long)
                and allow_long_defense
                and (not is_zoomed_out_bearish)
                and (market_regime != "RANGE" or range_breakout_side == "LONG" or is_meme_vol_surge_breakout_long)
                and (m3_c > m3_p)
                and (m3_c > kc_mid and c_c > kc_mid and c_c > m15_c)
                and (c_c > c_o)
                and (kc_slope > 0)
                and not is_chasing_high
                and (not is_sqz_now or is_meme_vol_surge_breakout_long)
            )
            can_open_short = bool(
                (
                    is_confirmed_death_cross_ready
                    or is_cross_mid_short
                    or is_valid_dc
                    or is_true_peak
                    or (is_1m_confirmed_bear and c_c <= m3_c)
                )
                and (m3_c < m15_c)
                and ((m15_c - m3_c) >= min_gc_dc_spread)
                and (m15_c <= m15_p and m15_slope_3_ref <= 0 and m15_slope_5_ref <= 0)
                and (kc_slope_3_ref < 0 and kc_slope_5_ref <= 0)
                and (not is_ma3_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "SHORT")
                and allow_short_defense
                and (not is_zoomed_out_bullish)
                and (market_regime != "RANGE" or range_breakout_side == "SHORT")
                and (m3_c < m3_p)
                and (m3_c < kc_mid and c_c < kc_mid and c_c < m15_c)
                and (c_c < c_o)
                and (kc_slope < 0)
                and not is_chasing_low
                and (not is_sqz_now)
            )

            if can_open_long:
                candidate_side = "LONG"
                if is_cross_mid_long:
                    entry_type = "PIVOT_LONG_CROSS_MID"
                elif is_valid_gc or is_confirmed_golden_cross_ready or is_1m_confirmed_bull:
                    entry_type = "NONE_GOLDEN_CROSS"
                else:
                    entry_type = "PIVOT_LONG"
                pivot_triggered = True
            elif can_open_short:
                candidate_side = "SHORT"
                if is_cross_mid_short:
                    entry_type = "PIVOT_SHORT_CROSS_MID"
                elif is_valid_dc or is_confirmed_death_cross_ready or is_1m_confirmed_bear:
                    entry_type = "NONE_DEAD_CROSS"
                else:
                    entry_type = "PIVOT_SHORT"
                pivot_triggered = True

            if pivot_triggered and candidate_side and entry_type:
                logger.info(f"🎯 [PIVOT_ENTRY_TRIGGERED] {symbol} 無持倉狀態捕獲轉折/交叉！發起 {candidate_side} 開倉 ({entry_type})")
                print(f"🎯 [PIVOT_ENTRY_TRIGGERED] {symbol} {candidate_side} {entry_type}", flush=True)
                is_cm = ("CROSS" in entry_type or "MID" in entry_type)
                sig_type = (
                    ("ENTRY_LONG_MA3_MA15_CROSS_MID" if candidate_side == "LONG" else "ENTRY_SHORT_MA3_MA15_CROSS_MID")
                    if is_cm else "SWING_PIVOT"
                )
                target_pivot_df = raw_df if raw_df is not None else klines_df
                intrabar_flag = False
                try:
                    intrabar_flag = bool(target_pivot_df is not None and not bool(target_pivot_df.iloc[-1].get("is_closed", True)))
                except Exception:
                    intrabar_flag = False
                entry_meta = {
                    "side": candidate_side,
                    "direction_override": True,
                    "is_agile_trigger": True,
                    "entry_type": entry_type,
                    "candidate_signal_type": sig_type,
                    "is_swing_pivot": (not is_cm),
                    "is_cross_mid": True,
                    "no_rigid_sl": True,
                    "stop_loss_price": None,
                    "meme_wick_stop_loss": meme_wick_buffer_sl_long if candidate_side == "LONG" else 0.0,
                    "upstream_entry_gate_authorized": True,
                    "intrabar_authorized": intrabar_flag,
                    "allow_kc_internal": True,
                    "strategy_mode": "AGILE_MOMENTUM",
                    "raw_df": target_pivot_df,
                    "macro_trend": "BULLISH" if is_1m_confirmed_bull else ("BEARISH" if is_1m_confirmed_bear else getattr(market_data, "macro_trend", "NEUTRAL")),
                    "source": f"UNIFIED_PIPELINE|{entry_type}",
                    "atr": atr_v,
                }
                raw_df_instance = entry_meta.get("raw_df")
                if hasattr(raw_df_instance, "attrs"):
                    for k in ("direction_override", "is_agile_trigger", "is_cross_mid", "entry_type", "strategy_mode", "allow_kc_internal"):
                        raw_df_instance.attrs[k] = entry_meta.get(k, True)

                open_success = await cls.open_position(
                    engine_or_account=engine_or_account,
                    symbol=symbol,
                    side=candidate_side,
                    price=curr_quote,
                    reason=f"PivotEntry:{entry_type}",
                    meta=entry_meta,
                )
                if open_success:
                    logger.info(f"🎯 [PIVOT_ENTRY_SUCCESS] {symbol} 成功建立 {candidate_side} 倉位！")
                    print(f"🎯 [PIVOT_ENTRY_SUCCESS] {symbol} 成功建立 {candidate_side} 倉位！", flush=True)
                    return {"status": "ENTRY_EXECUTED", "symbol": symbol, "side": candidate_side, "entry_type": entry_type}
                logger.debug(f"⚠️ [PIVOT_ENTRY_FALLTHROUGH] {symbol} {entry_type} 未成交，繼續評估常規破軌/延續開倉引擎")

            # 若未觸發真峰谷起點，順暢承接常規開倉引擎（破軌開倉、MA3穿KC中軌、MA3/MA15交叉等）
            # 偵測候選方向訊號 (大週期順勢原則：多頭大週期只看多、空頭大週期只看空，絕不頻繁多空互反)
            candidate_signal = None
            ma5_curr = float(market_data.curr_k.get("ma5", market_data.current_price))
            ma5_prev = float(market_data.prev_k.get("ma5", ma5_curr))
            ma15_curr = float(market_data.curr_k.get("ma15", getattr(market_data, "ma15", ma5_curr)))
            ma15_prev = float(market_data.prev_k.get("ma15", ma15_curr))
            ma3_curr = float(market_data.curr_k.get("ma3", getattr(market_data, "ma3", market_data.current_price)))
            ma3_prev = float(market_data.prev_k.get("ma3", getattr(market_data, "prev_ma3", ma3_curr)))
            ma3_slope = ma3_curr - ma3_prev
            macro_trend = getattr(market_data, "macro_trend", "NEUTRAL")
            raw_df = getattr(market_data, "raw_df", None)
            live_cross_long = None
            live_cross_short = None
            ma_trend_aligned_long = False
            ma_trend_aligned_short = False
            ma3_directional_long = False
            ma3_directional_short = False
            if raw_df is not None and len(raw_df) >= 2:
                from core.gates.pipeline import fast_cross_evidence
                from core.services.candle_data import closed_entry_candles

                closed_ma = closed_entry_candles(raw_df)
                if len(closed_ma) >= 2:
                    if not {"ma3", "ma15"}.issubset(closed_ma.columns):
                        closed_ma = closed_ma.copy()
                        closed_ma["ma3"] = closed_ma["close"].rolling(3, min_periods=1).mean()
                        closed_ma["ma15"] = closed_ma["close"].rolling(15, min_periods=1).mean()
                    prior_ma = closed_ma.iloc[-2]
                    latest_ma = closed_ma.iloc[-1]
                    ma3_prior = float(prior_ma["ma3"])
                    ma15_prior = float(prior_ma["ma15"])
                    ma3_latest = float(latest_ma["ma3"])
                    ma15_latest = float(latest_ma["ma15"])
                    live_row = raw_df.iloc[-1]
                    raw_close = float(live_row.get("close", market_data.current_price))
                    live_ma3 = float(live_row.get("ma3", ma3_latest))
                    live_bar_is_closed = bool(live_row.get("is_closed", True))
                    if not live_bar_is_closed:
                        live_ma3 += (float(market_data.current_price) - raw_close) / 3.0
                    long_ma3_rising = (
                        ma3_latest > ma3_prior
                        if live_bar_is_closed else live_ma3 > ma3_latest
                    )
                    short_ma3_falling = (
                        ma3_latest < ma3_prior
                        if live_bar_is_closed else live_ma3 < ma3_latest
                    )
                    ma_values = (ma3_prior, ma15_prior, ma3_latest, ma15_latest, live_ma3)
                    if all(math.isfinite(value) and value > 0 for value in ma_values):
                        ma3_curr = ma3_latest
                        ma3_prev = ma3_prior
                        ma3_slope = ma3_curr - ma3_prev
                        ma3_directional_long = ma3_latest > ma3_prior and long_ma3_rising
                        ma3_directional_short = ma3_latest < ma3_prior and short_ma3_falling
                        ma_trend_aligned_long = (
                            ma3_directional_long
                            and ma15_latest > ma15_prior
                            and ma3_latest > ma15_latest
                        )
                        ma_trend_aligned_short = (
                            ma3_directional_short
                            and ma15_latest < ma15_prior
                            and ma3_latest < ma15_latest
                        )
                live_cross_long = fast_cross_evidence(
                    raw_df, market_data.current_price, "LONG", symbol=symbol,
                )
                live_cross_short = fast_cross_evidence(
                    raw_df, market_data.current_price, "SHORT", symbol=symbol,
                )

            if not ma3_directional_long and ma5_curr > ma5_prev:
                ma3_directional_long = True
            if not ma3_directional_short and ma5_curr < ma5_prev:
                ma3_directional_short = True

            # Two closed candles authorize KC breakout entries: K1 opens
            # inside the channel and closes beyond the rail; K2 is same-color
            # and closes beyond both its KC rail and MA5. Revalidate live quote.
            two_bar_breakout = None
            if raw_df is not None:
                from core.services.candle_data import closed_entry_candles
                from core.services.kc_two_bar_entry import evaluate_kc_two_bar_entry

                two_bar_breakout = evaluate_kc_two_bar_entry(
                    closed_entry_candles(raw_df), market_data.current_price,
                )
            is_breakout_long = bool(two_bar_breakout and two_bar_breakout.get("side") == "LONG")
            is_breakdown_short = bool(two_bar_breakout and two_bar_breakout.get("side") == "SHORT")

            # 模式 2：趨勢回踩均線帶守穩轉強信號
            is_pullback_long = (
                float(market_data.curr_k.get("low", market_data.current_price)) <= ma5_curr * 1.002
                and market_data.curr_k["close"] >= ma5_curr
                and market_data.curr_k["close"] >= market_data.curr_k["open"]
                and ma5_curr >= ma5_prev
                and market_data.kc_middle_slope >= -0.000005
                and market_data.curr_k["close"] >= market_data.ema_slow
                and ma_trend_aligned_long
            )
            is_pullback_short = (
                float(market_data.curr_k.get("high", market_data.current_price)) >= ma5_curr * 0.998
                and market_data.curr_k["close"] <= ma5_curr
                and market_data.curr_k["close"] <= market_data.curr_k["open"]
                and ma5_curr <= ma5_prev
                and market_data.kc_middle_slope <= 0.000005
                and market_data.curr_k["close"] <= market_data.ema_slow
                and ma_trend_aligned_short
            )

            # MA3 交叉 KC 中軌進場授權（統一核心鐵律）：
            # 做多：前一根已收線 MA3 <= KC 中軌，當前已收線 MA3 > KC 中軌，當前 MA3 > 前一根 MA3 (斜率向上)
            # 做空：前一根已收線 MA3 >= KC 中軌，當前已收線 MA3 < KC 中軌，當前 MA3 < 前一根 MA3 (斜率向下)
            from core.gates.ma3_kc_midline_cross_gate import evaluate_ma3_kc_midline_cross
            from core.services.candle_data import closed_entry_candles

            closed_for_cross = closed_entry_candles(raw_df) if raw_df is not None else None
            ma3_cross_long_ok, ma3_cross_long_reason, ma3_cross_long_meta = (
                evaluate_ma3_kc_midline_cross(closed_for_cross, "LONG", symbol)
                if closed_for_cross is not None and len(closed_for_cross) >= 2
                else (False, "NO_CLOSED_BARS", {})
            )
            ma3_cross_short_ok, ma3_cross_short_reason, ma3_cross_short_meta = (
                evaluate_ma3_kc_midline_cross(closed_for_cross, "SHORT", symbol)
                if closed_for_cross is not None and len(closed_for_cross) >= 2
                else (False, "NO_CLOSED_BARS", {})
            )

            # 結合肯特納通道 (KC) 方向與大週期趨勢濾網（拉遠看大結構）：
            # 若主趨勢向上 (拉遠多頭 is_zoomed_out_bullish 或大趨勢 BULLISH)，只做金叉開多，100% 禁止死叉開空！
            # 若主趨勢向下 (拉遠空頭 is_zoomed_out_bearish 或大趨勢 BEARISH)，只做死叉開空，100% 禁止金叉開多！
            kc_slope = getattr(market_data, "kc_middle_slope", 0.0)
            is_macro_bull = bool(
                (not is_1m_confirmed_bear)
                and (is_zoomed_out_bullish or (macro_trend == "BULLISH" and not is_zoomed_out_bearish) or (kc_slope > 0.000005 and not is_zoomed_out_bearish))
            )
            is_macro_bear = bool(
                (not is_1m_confirmed_bull)
                and (is_zoomed_out_bearish or (macro_trend == "BEARISH" and not is_zoomed_out_bullish) or (kc_slope < -0.000005 and not is_zoomed_out_bullish))
            )

            is_ma_cross_long = (
                ma3_cross_long_ok
                and not is_macro_bear
                and kc_slope >= -0.000005
                and market_data.curr_k["close"] >= market_data.kc_middle
            )
            is_ma_cross_short = (
                ma3_cross_short_ok
                and not is_macro_bull
                and kc_slope <= 0.000005
                and market_data.curr_k["close"] <= market_data.kc_middle
            )

            # 順勢鐵律與波段判定 (常規追單防護)
            is_above_slow_ma = (market_data.curr_k["close"] >= market_data.ema_slow or market_data.current_price >= market_data.ema_slow)
            is_slow_ma_up = getattr(market_data, "slow_slope", 0.0) >= 0
            is_bull_alignment = (market_data.ema_fast >= market_data.ema_slow)

            is_below_slow_ma = (market_data.curr_k["close"] <= market_data.ema_slow or market_data.current_price <= market_data.ema_slow)
            is_slow_ma_down = getattr(market_data, "slow_slope", 0.0) <= 0
            is_bear_alignment = (market_data.ema_fast <= market_data.ema_slow)

            is_bullish_wave = (
                macro_trend == "BULLISH"
                or is_bull_alignment
                or (is_above_slow_ma and is_slow_ma_up)
                or (market_data.kc_middle_slope >= -0.000005 and market_data.curr_k["close"] >= market_data.kc_middle)
            )
            is_bearish_wave = (
                macro_trend == "BEARISH"
                or is_bear_alignment
                or (is_below_slow_ma and is_slow_ma_down)
                or (market_data.kc_middle_slope <= 0.000005 and market_data.curr_k["close"] <= market_data.kc_middle)
            )

            # 單一軌外延續開倉判定（2026-10-09 最新授權：平倉後或破軌沒開倉，只要上一根在軌外且當前報價在軌外及MA5外，順勢外軌鎖定延續開倉）
            p_up = float(market_data.prev_k.get("kc_upper", market_data.kc_upper) if market_data.prev_k else market_data.kc_upper)
            p_low = float(market_data.prev_k.get("kc_lower", market_data.kc_lower) if market_data.prev_k else market_data.kc_lower)
            p_close = float(market_data.prev_k.get("close", market_data.current_price) if market_data.prev_k else market_data.current_price)
            curr_quote = float(market_data.current_price)
            curr_c = float(market_data.curr_k.get("close", curr_quote))
            curr_o = float(market_data.curr_k.get("open", curr_c))

            p_open = float(market_data.prev_k.get("open", p_close) if market_data.prev_k else p_close)
            is_prev_pullback_for_short = p_close > p_open  # 前根為反彈小陽 K
            is_prev_pullback_for_long = p_close < p_open   # 前根為回踩小陰 K

            # 單一軌外延續開倉判定：
            # 若前根為回踩/反彈反向棒，嚴格禁止單根延續偷跑！必須等三棒實體確認！
            is_continuation_long = (
                not is_macro_bear
                and not is_prev_pullback_for_long
                and (p_close > p_up or curr_quote > market_data.kc_upper)
                and curr_quote > market_data.kc_upper
                and curr_quote > ma5_curr
                and ma5_curr > ma5_prev
                and kc_slope >= -0.000005
                and curr_c > curr_o
            )
            is_continuation_short = (
                not is_macro_bull
                and not is_prev_pullback_for_short
                and (p_close < p_low or curr_quote < market_data.kc_lower)
                and curr_quote < market_data.kc_lower
                and curr_quote < ma5_curr
                and ma5_curr < ma5_prev
                and kc_slope <= 0.000005
                and curr_c < curr_o
            )

            # 任務 3：順勢破軌回踩再起飛模式 (Breakout-Pullback-Continuation 三棒組合模式)
            # 嚴禁在 K2 反向小 K 當下偷跑開倉，必須等到 K3 實體收線確認突破 K2 高/低點！
            three_bar_long = False
            three_bar_short = False
            three_bar_stop = None
            if isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 3:
                try:
                    from core.services.candle_data import closed_entry_candles
                    closed_bars = closed_entry_candles(raw_df)
                    cand_k1, cand_k2, cand_k3 = None, None, None
                    if len(closed_bars) >= 3:
                        cand_k1 = closed_bars.iloc[-3]
                        cand_k2 = closed_bars.iloc[-2]
                        cand_k3 = closed_bars.iloc[-1]
                    elif len(closed_bars) >= 2:
                        cand_k1 = closed_bars.iloc[-2]
                        cand_k2 = closed_bars.iloc[-1]
                        cand_k3 = market_data.curr_k

                    if cand_k1 is not None and cand_k2 is not None and cand_k3 is not None:
                        k1_c, k1_o = float(cand_k1["close"]), float(cand_k1["open"])
                        k2_c, k2_o = float(cand_k2["close"]), float(cand_k2["open"])
                        k3_c, k3_o = float(cand_k3["close"]), float(cand_k3["open"])
                        k2_h, k2_l = float(cand_k2["high"]), float(cand_k2["low"])
                        k1_body = abs(k1_c - k1_o)
                        k2_body = abs(k2_c - k2_o)
                        k1_up = float(cand_k1.get("kc_upper", 0.0))
                        k1_low = float(cand_k1.get("kc_lower", 0.0))
                        k2_ma3 = float(cand_k2.get("ma3", k2_c))

                        # 🟢 多頭二次起飛 (CONTINUATION_LONG) 嚴格判定：
                        # K1: 陽線衝破 KC 上軌
                        # K2: 縮量小陰線回踩，實體 <= 0.5 * K1，且收盤未跌破 MA3
                        # K3: 必須為【實體陽線】且收盤突破 K2 高點，且 MA3 當前明確向上！
                        is_k1_breakout = (k1_c > k1_o and k1_c > k1_up)
                        is_k2_pullback_long = (k2_c < k2_o and k2_body < 0.5 * k1_body and k2_c >= k2_ma3)
                        is_k3_confirm_long = (k3_c > k3_o and k3_c > k2_h and ma3_curr > ma3_prev)

                        if is_k1_breakout and is_k2_pullback_long and is_k3_confirm_long:
                            three_bar_long = True
                            three_bar_stop = float(k2_l)

                        # 🔴 空頭二次下殺 (CONTINUATION_SHORT) 嚴格判定：
                        # K1: 陰線跌破 KC 下軌
                        # K2: 縮量小陽線反彈，實體 <= 0.5 * K1，且收盤未突破 MA3
                        # K3: 必須為【實體紅 K/陰線】且收盤跌破 K2 低點，且 MA3 當前明確向下！
                        is_k1_breakdown = (k1_c < k1_o and k1_c < k1_low)
                        is_k2_pullback_short = (k2_c > k2_o and k2_body < 0.5 * k1_body and k2_c <= k2_ma3)
                        is_k3_confirm_short = (k3_c < k3_o and k3_c < k2_l and ma3_curr < ma3_prev)

                        if is_k1_breakdown and is_k2_pullback_short and is_k3_confirm_short:
                            three_bar_short = True
                            three_bar_stop = float(k2_h)
                except Exception:
                    pass

            # 盤整鎖定 (KC Squeeze Gate)：
            # 當 kc_width_ratio = (kc_upper - kc_lower) / kc_middle < 0.015 或 current_atr < 0.7 * avg_atr_20 時，
            # 標記為 MARKET_SQUEEZE，一票否決所有常規開倉
            kc_mid_val = float(market_data.kc_middle)
            kc_width = float(market_data.kc_upper) - float(market_data.kc_lower)
            kc_width_ratio = kc_width / kc_mid_val if kc_mid_val > 0 else 0.0
            curr_atr = float(getattr(market_data, "atr", 0.0) or 0.0)
            avg_atr_20 = curr_atr
            if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 5:
                if "atr" in raw_df.columns:
                    s_atr = raw_df["atr"].dropna().astype(float)
                    if len(s_atr) > 0:
                        avg_atr_20 = float(s_atr.iloc[-20:].mean())
                elif "high" in raw_df.columns and "low" in raw_df.columns:
                    ranges = (raw_df["high"].astype(float) - raw_df["low"].astype(float)).dropna()
                    if len(ranges) > 0:
                        avg_atr_20 = float(ranges.iloc[-20:].mean())
            is_market_squeeze = (kc_width_ratio < 0.015) or (avg_atr_20 > 0 and curr_atr < 0.7 * avg_atr_20)

            # 方向總開關（Direction Master Lock）與開倉絕對門檻：
            # 🟢 開多絕對門檻（沒滿足任何一項，強制拒絕開多）：
            # 1. MA3 當前必須明確向上: ma3_curr > ma3_prev
            # 2. MA3 必須嚴格位於 MA15 慢線上線上方: ma3_curr > ma15_curr
            # 3. 當前 K 棒必須是實體陽線: curr_k['close'] > curr_k['open']
            # 只要 MA3 向下、或在 MA15 下方、或當前收陰，一律一票否決，嚴禁生成任何 LONG 訊號！
            ma15_curr = float(getattr(market_data, "ma15", getattr(market_data, "ema_slow", market_data.kc_middle)))
            curr_close = float(market_data.curr_k.get("close", market_data.current_price))
            curr_open = float(market_data.curr_k.get("open", curr_close))
            kc_mid_val = float(market_data.kc_middle)
            is_ma3_trending_up = ma3_curr > ma3_prev
            from core.symbol_aliases import is_meme_breakout_symbol
            is_meme_contract = (
                is_meme_breakout_symbol(symbol)
                or any(k in str(symbol).upper() for k in ("LOBSTER", "PEPE", "CAP", "MEME"))
                or any(k in str(symbol) for k in ("龙虾", "龍蝦"))
            )
            is_ma3_trending_up = ma3_curr > ma3_prev
            # 嚴格鐵律：做多 MA3 必須嚴格在 MA15 上方！
            is_ma3_above_slow = (ma3_curr > ma15_curr)
            is_candle_bullish = (curr_close > curr_open)
            hard_long_allowed = is_ma3_trending_up and is_ma3_above_slow and is_candle_bullish

            is_ma3_trending_down = ma3_curr < ma3_prev
            # 嚴格鐵律：做空 MA3 必須嚴格在 MA15 下方！
            is_ma3_below_slow = (ma3_curr < ma15_curr)
            is_candle_bearish = (curr_close < curr_open)
            hard_short_allowed = is_ma3_trending_down and is_ma3_below_slow and is_candle_bearish

            if not hard_long_allowed:
                is_breakout_long = False
                is_continuation_long = False
                three_bar_long = False
                is_ma_cross_long = False

            if not hard_short_allowed:
                is_breakdown_short = False
                is_continuation_short = False
                three_bar_short = False
                is_ma_cross_short = False

            from core.gates.direction_master_lock import evaluate_direction_master_lock
            kc_mid_val = float(market_data.kc_middle)
            direction_long_ok, dir_long_reason, _ = evaluate_direction_master_lock(
                "LONG", ma3_curr, ma3_prev, kc_mid_val, ma5_curr,
                ma15_curr=ma15_curr, curr_close=curr_close, curr_open=curr_open,
                direction_override=is_meme_contract,
            )
            direction_short_ok, dir_short_reason, _ = evaluate_direction_master_lock(
                "SHORT", ma3_curr, ma3_prev, kc_mid_val, ma5_curr,
                ma15_curr=ma15_curr, curr_close=curr_close, curr_open=curr_open,
                direction_override=is_meme_contract,
            )

            # 敏捷進攻架構：妖幣突破訊號與轉折交叉訊號嚴格依循 KC 主趨勢防線與方向總開關
            # 順勢鐵律：KC 中軌下行或價格在 KC 中軌之下時，100% 絕對禁止開多！
            #           KC 中軌上行或價格在 KC 中軌之上時，100% 絕對禁止開空！
            can_long = (
                hard_long_allowed
                and allow_long_defense
                and (not is_ma3_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "LONG")
                and (market_regime != "RANGE" or range_breakout_side == "LONG")
                and (is_breakout_long or is_continuation_long or is_meme_contract or direction_long_ok)
                and (not is_macro_bear)
                and (kc_slope >= 0)
                and (curr_close > kc_mid_val)
                and (ma3_curr > kc_mid_val)
                and (not is_sqz_now)
                and (
                    is_breakout_long or is_continuation_long or is_ma_cross_long or (
                        not is_bearish_wave
                        and ma_trend_aligned_long
                        and (ma3_slope >= 0)
                        and (market_data.curr_k["close"] >= ma3_curr)
                        and (market_data.current_price >= ma3_curr)
                    )
                )
            )
            can_short = (
                hard_short_allowed
                and allow_short_defense
                and (not is_ma3_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "SHORT")
                and (market_regime != "RANGE" or range_breakout_side == "SHORT")
                and (is_breakdown_short or is_continuation_short or is_meme_contract or direction_short_ok)
                and (not is_macro_bull)
                and (kc_slope <= 0)
                and (curr_close < kc_mid_val)
                and (ma3_curr < kc_mid_val)
                and (not is_sqz_now)
                and (
                    is_breakdown_short or is_continuation_short or is_ma_cross_short or (
                        not is_bullish_wave
                        and ma_trend_aligned_short
                        and (ma3_slope <= 0)
                        and (market_data.curr_k["close"] <= ma3_curr)
                        and (market_data.current_price <= ma3_curr)
                    )
                )
            )

            # ══════════════════════════════════════════════════════════════════
            # 敏捷雙重開倉觸發點（Dual Agile Entry Triggers）
            # 解決滯後與平倉後不動作問題：破軌即開 / MA3 穿中軌即開 / 順勢延續即開！
            # ══════════════════════════════════════════════════════════════════
            atr_val_now = float(getattr(market_data, "atr", 0.0) or 0.0)
            c_body_now = abs(curr_close - curr_open)
            kc_up_val = float(market_data.kc_upper)
            kc_low_val = float(market_data.kc_lower)

            # 🟢 多頭觸發點 A（MA3 與 MA15 黃金交叉發散確立，解決「剛觸碰就亂開」與「已經交叉好久卻不開倉」雙重痛點）：
            prev_kc_mid_val = float(market_data.prev_k.get("kc_middle", kc_mid_val) if market_data.prev_k else kc_mid_val)
            agile_cross_long = (
                (market_regime != "RANGE" or range_breakout_side == "LONG")
                and (not is_ma3_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "LONG")
                and (ma3_curr > ma15_curr)
                and ((ma3_curr - ma15_curr) >= min_gc_dc_spread)
                and allow_long_defense
                and (not is_macro_bear)
                and (not is_zoomed_out_bearish)
                and (m15_c >= m15_p and m15_slope_3_ref >= 0 and m15_slope_5_ref >= 0)
                and (kc_slope > 0 and kc_slope_3_ref > 0 and kc_slope_5_ref >= 0)
                and curr_close > kc_mid_val
                and curr_close > ma15_curr
                and ma3_curr > kc_mid_val
                and ma3_curr > ma3_prev
                and curr_close > curr_open
                and c_body_now >= 0.16 * atr_val_now
                and curr_close < (kc_up_val - 0.10 * atr_val_now)
            )
            # 🟢 多頭觸發點 B（實體突破 KC 上軌或沿上軌外強勢延續開倉，解決平倉後不動作痛點，但拉遠看空頭時嚴禁逆勢反彈追多）：
            upper_wick_check = float(market_data.curr_k.get("high", curr_close)) - max(curr_close, curr_open)
            agile_breakout_long = (
                (market_regime != "RANGE" or range_breakout_side == "LONG")
                and (not is_ma3_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "LONG")
                and (ma3_curr > ma15_curr)
                and ((ma3_curr - ma15_curr) >= min_gc_dc_spread)
                and allow_long_defense
                and (not is_macro_bear)
                and (not is_zoomed_out_bearish)
                and (m15_c >= m15_p and m15_slope_3_ref >= 0 and m15_slope_5_ref >= 0)
                and (kc_slope > 0 and kc_slope_3_ref > 0 and kc_slope_5_ref >= 0)
                and (curr_close > kc_up_val or curr_quote > kc_up_val)
                and c_body_now >= 0.18 * atr_val_now
                and upper_wick_check <= c_body_now
                and curr_close > curr_open
                and ma3_curr > ma3_prev
            )
            # 🟢 多頭觸發點 C（黃金交叉確立後順勢延續補上車：解決「已經交叉好久卻不開倉」！只要黃金交叉發散確立且穩居中軌與 MA15 之上收陽，立即補倉買多！）：
            agile_continuation_long = (
                (market_regime != "RANGE" or range_breakout_side == "LONG")
                and (not is_ma3_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "LONG")
                and (ma3_curr > ma15_curr)
                and ((ma3_curr - ma15_curr) >= min_gc_dc_spread)
                and allow_long_defense
                and (not is_macro_bear)
                and (not is_zoomed_out_bearish)
                and (m15_c >= m15_p and m15_slope_3_ref >= 0 and m15_slope_5_ref >= 0)
                and (kc_slope > 0 and kc_slope_3_ref > 0 and kc_slope_5_ref >= 0)
                and curr_close > kc_mid_val
                and curr_close > ma15_curr
                and ma3_curr > kc_mid_val
                and curr_close >= ma3_curr
                and ma3_curr > ma3_prev
                and curr_close > curr_open
                and c_body_now >= 0.16 * atr_val_now
            )

            # 🔴 空頭觸發點 A（MA3 與 MA15 死亡交叉發散確立，解決「剛觸碰就亂開」與「已經交叉好久卻不開倉」雙重痛點）：
            agile_cross_short = (
                (market_regime != "RANGE" or range_breakout_side == "SHORT")
                and (not is_ma3_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "SHORT")
                and (ma3_curr < ma15_curr)
                and ((ma15_curr - ma3_curr) >= min_gc_dc_spread)
                and allow_short_defense
                and (not is_macro_bull)
                and (not is_zoomed_out_bullish)
                and (m15_c <= m15_p and m15_slope_3_ref <= 0 and m15_slope_5_ref <= 0)
                and (kc_slope < 0 and kc_slope_3_ref < 0 and kc_slope_5_ref <= 0)
                and curr_close < kc_mid_val
                and curr_close < ma15_curr
                and ma3_curr < kc_mid_val
                and ma3_curr < ma3_prev
                and curr_close < curr_open
                and c_body_now >= 0.16 * atr_val_now
                and curr_close > (kc_low_val + 0.10 * atr_val_now)
            )
            # 🔴 空頭觸發點 B（實體跌破 KC 下軌或沿下軌外強勢延續開空，但拉遠看多頭時嚴禁逆勢殺跌）：
            lower_wick_check = min(curr_close, curr_open) - float(market_data.curr_k.get("low", curr_close))
            agile_breakdown_short = (
                (market_regime != "RANGE" or range_breakout_side == "SHORT")
                and (not is_ma3_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "SHORT")
                and (ma3_curr < ma15_curr)
                and ((ma15_curr - ma3_curr) >= min_gc_dc_spread)
                and allow_short_defense
                and (not is_macro_bull)
                and (not is_zoomed_out_bullish)
                and (m15_c <= m15_p and m15_slope_3_ref <= 0 and m15_slope_5_ref <= 0)
                and (kc_slope < 0 and kc_slope_3_ref < 0 and kc_slope_5_ref <= 0)
                and (curr_close < kc_low_val or curr_quote < kc_low_val)
                and c_body_now >= 0.18 * atr_val_now
                and lower_wick_check <= c_body_now
                and curr_close < curr_open
                and ma3_curr < ma3_prev
            )
            # 🔴 空頭觸發點 C（死亡交叉確立後順勢延續補上車：解決「已經交叉好久卻不開倉」！只要死亡交叉發散確立且受壓於中軌與 MA15 之下收陰，立即補倉賣空！）：
            agile_continuation_short = (
                (market_regime != "RANGE" or range_breakout_side == "SHORT")
                and (not is_ma3_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "SHORT")
                and (ma3_curr < ma15_curr)
                and ((ma15_curr - ma3_curr) >= min_gc_dc_spread)
                and allow_short_defense
                and (not is_macro_bull)
                and (not is_zoomed_out_bullish)
                and (m15_c <= m15_p and m15_slope_3_ref <= 0 and m15_slope_5_ref <= 0)
                and (kc_slope < 0 and kc_slope_3_ref < 0 and kc_slope_5_ref <= 0)
                and curr_close < kc_mid_val
                and curr_close < ma15_curr
                and ma3_curr < kc_mid_val
                and curr_close <= ma3_curr
                and ma3_curr < ma3_prev
                and curr_close < curr_open
                and c_body_now >= 0.16 * atr_val_now
            )

            # ══════════════════════════════════════════════════════════════════
            # 🚫 假突破防範 (KC Squeeze / Fakeout Filter - Anti-Bull-Trap)
            # 當 KC 中軌走平或往下時，若價格僅是短期衝出 KC 上軌但缺乏放量強實體或上影線過長，
            # 判定為假突破誘多（Bull Trap），一票否決開多，防止買在假突破頂部！
            # ══════════════════════════════════════════════════════════════════
            upper_wick_now = float(market_data.curr_k.get("high", curr_close)) - max(curr_close, curr_open)
            is_bull_trap = bool(
                curr_close >= (kc_up_val - 0.1 * atr_val_now)
                and (
                    upper_wick_now > c_body_now
                    or not allow_long_defense
                    or (kc_slope <= 0.000005 and (c_body_now < 0.6 * atr_val_now or upper_wick_now >= 0.6 * c_body_now or curr_close <= curr_open))
                )
            )
            if is_bull_trap:
                can_long = False
                is_breakout_long = False
                is_continuation_long = False
                agile_breakout_long = False
                agile_continuation_long = False
                logger.info(
                    f"🚫 [FAKE_BREAKOUT_BULL_TRAP] {symbol} 上軌假突破或超買壓力攔截 (slope={kc_slope:.6f}, "
                    f"body={c_body_now:.6f}, wick={upper_wick_now:.6f}, atr={atr_val_now:.6f}, resist={is_resistance_wall_block_long}, overbought={is_overbought_block_long})，"
                    f"一票否決開多！"
                )

            lower_wick_now = min(curr_close, curr_open) - float(market_data.curr_k.get("low", curr_close))
            is_bear_trap = bool(
                curr_close <= (kc_low_val + 0.1 * atr_val_now)
                and (
                    lower_wick_now > c_body_now
                    or not allow_short_defense
                    or (kc_slope >= -0.000005 and (c_body_now < 0.6 * atr_val_now or lower_wick_now >= 0.6 * c_body_now or curr_close >= curr_open))
                )
            )
            if is_bear_trap:
                can_short = False
                is_breakdown_short = False
                is_continuation_short = False
                agile_breakdown_short = False
                agile_continuation_short = False
                logger.info(
                    f"🚫 [FAKE_BREAKDOWN_BEAR_TRAP] {symbol} 下軌假跌破或支撐超賣攔截 (slope={kc_slope:.6f}, "
                    f"body={c_body_now:.6f}, wick={lower_wick_now:.6f}, atr={atr_val_now:.6f}, support={is_support_wall_block_short}, oversold={is_oversold_block_short})，"
                    f"一票否決開空！"
                )

            # ══════════════════════════════════════════════════════════════════
            # 🚫 極端暴拉/暴跌插針追高防線 (Anti-Extreme-Spike Gate)
            # 僅攔截極端乖離（中軌離差 > 3.5% 或超出外軌 > 2.5 ATR），確保正常破軌與順勢延續開倉暢通！
            # ══════════════════════════════════════════════════════════════════
            if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 5:
                lookback_p = min(len(raw_df), 30)
                prior_swing_low = float(raw_df['low'].iloc[-lookback_p:].min())
                prior_swing_high = float(raw_df['high'].iloc[-lookback_p:].max())
                rise_from_low = curr_close - prior_swing_low
                drop_from_high = prior_swing_high - curr_close

                mid_dist_ratio_now = abs(curr_close - kc_mid_val) / kc_mid_val if kc_mid_val > 0 else 0.0
                if mid_dist_ratio_now > 0.035:
                    if curr_close < kc_mid_val:
                        can_short = False
                        is_breakdown_short = False
                        is_continuation_short = False
                        agile_breakdown_short = False
                        agile_continuation_short = False
                        logger.info(
                            f"🚫 [ANTI_CHASING_VETO_SHORT] {symbol} 價格距離KC中軌極端過遠 (離差={mid_dist_ratio_now*100:.2f}% > 3.5%)，"
                            f"認定為極端延伸末端，一票否決追空！"
                        )
                    elif curr_close > kc_mid_val:
                        can_long = False
                        is_breakout_long = False
                        is_continuation_long = False
                        agile_breakout_long = False
                        agile_continuation_long = False
                        logger.info(
                            f"🚫 [ANTI_CHASING_VETO_LONG] {symbol} 價格距離KC中軌極端過遠 (離差={mid_dist_ratio_now*100:.2f}% > 3.5%)，"
                            f"認定為極端延伸末端，一票否決追多！"
                        )

                if atr_val_now > 0 and rise_from_low >= 4.0 * atr_val_now and curr_close >= (kc_up_val + 2.5 * atr_val_now) and p_close > p_up:
                    can_long = False
                    is_breakout_long = False
                    is_continuation_long = False
                    agile_breakout_long = False
                    agile_continuation_long = False
                    logger.info(
                        f"🚫 [ANTI_CHASING_VETO_LONG] {symbol} 價格自低點極端噴出 {rise_from_low:.6f} 且超出上軌 > 2.5 ATR，一票否決極端追多！"
                    )

                if atr_val_now > 0 and drop_from_high >= 4.0 * atr_val_now and curr_close <= (kc_low_val - 2.5 * atr_val_now) and p_close < p_low:
                    can_short = False
                    is_breakdown_short = False
                    is_continuation_short = False
                    agile_breakdown_short = False
                    agile_continuation_short = False
                    logger.info(
                        f"🚫 [ANTI_CHASING_VETO_SHORT] {symbol} 價格自高點極端崩跌 {drop_from_high:.6f} 且超出下軌 > 2.5 ATR，一票否決極端追空！"
                    )

            is_agile_trigger = False
            agile_signal = None
            if agile_breakout_long:
                is_agile_trigger = True
                agile_signal = "ENTRY_LONG_BREAKOUT"
                can_long = True
            elif agile_cross_long:
                is_agile_trigger = True
                agile_signal = "ENTRY_LONG_CROSS_MID"
                can_long = True
            elif agile_continuation_long:
                is_agile_trigger = True
                agile_signal = "ENTRY_LONG_CONTINUATION"
                can_long = True
            elif agile_breakdown_short:
                is_agile_trigger = True
                agile_signal = "ENTRY_SHORT_BREAKDOWN"
                can_short = True
            elif agile_cross_short:
                is_agile_trigger = True
                agile_signal = "ENTRY_SHORT_CROSS_MID"
                can_short = True
            elif agile_continuation_short:
                is_agile_trigger = True
                agile_signal = "ENTRY_SHORT_CONTINUATION"
                can_short = True

            # 任務 2：【特例放行】谷底與頂部轉折提前開倉（雙向豁免 KC 斜率）
            from core.services.swing_pivot_rules import evaluate_swing_pivot_entry
            raw_swing_pivot = evaluate_swing_pivot_entry(raw_df)
            swing_pivot = None
            if raw_swing_pivot:
                pivot_side = raw_swing_pivot.get("side")
                # 谷底/頂部轉折開倉：只要極短線 MA3 拐頭順向且收線同色，即可第一時間進場，豁免 slow 慢線位置限制！
                # 但必須嚴格遵守順勢鐵律：KC 中軌向下或破中軌嚴禁開多；KC 中軌向上或站上中軌嚴禁開空！
                if pivot_side == "LONG":
                    pivot_long_ok = (
                        (not is_zoomed_out_bearish)
                        and (not is_macro_bear)
                        and is_ma3_trending_up
                        and is_candle_bullish
                        and (kc_slope >= 0)
                        and (curr_close >= kc_mid_val)
                        and (ma3_curr >= kc_mid_val)
                        and (ma3_curr > ma15_curr)
                    )
                    if not pivot_long_ok:
                        logger.warning(
                            f"🛑 [HARD_VETO] {symbol} SWING_PIVOT_LONG 違反開多門檻 "
                            f"(zoomed_bear={is_zoomed_out_bearish}, ma3_up={is_ma3_trending_up}, ma3_above_ma15={ma3_curr > ma15_curr}, bullish={is_candle_bullish}, slope={kc_slope:.8f}, close_over_mid={curr_close >= kc_mid_val})，一票否決！"
                        )
                    else:
                        swing_pivot = raw_swing_pivot
                elif pivot_side == "SHORT":
                    pivot_short_ok = (
                        (not is_zoomed_out_bullish)
                        and (not is_macro_bull)
                        and is_ma3_trending_down
                        and is_candle_bearish
                        and (kc_slope <= 0)
                        and (curr_close <= kc_mid_val)
                        and (ma3_curr <= kc_mid_val)
                        and (ma3_curr < ma15_curr)
                    )
                    if not pivot_short_ok:
                        logger.warning(
                            f"🛑 [HARD_VETO] {symbol} SWING_PIVOT_SHORT 違反開空門檻 "
                            f"(zoomed_bull={is_zoomed_out_bullish}, ma3_down={is_ma3_trending_down}, ma3_below_ma15={ma3_curr < ma15_curr}, bearish={is_candle_bearish}, slope={kc_slope:.8f}, close_under_mid={curr_close <= kc_mid_val})，一票否決！"
                        )
                    else:
                        swing_pivot = raw_swing_pivot

            is_swing_pivot = bool(swing_pivot)
            if is_swing_pivot:
                candidate_side = swing_pivot["side"]
                if candidate_side == "LONG" and (not is_zoomed_out_bearish and kc_slope >= 0 and curr_close >= kc_mid_val):
                    can_long = True
                elif candidate_side == "SHORT" and (not is_zoomed_out_bullish and kc_slope <= 0 and curr_close <= kc_mid_val):
                    can_short = True

            # 強動能破軌、外軌延續或順勢延續開倉判定 (若平倉後趨勢仍延續，必須執行延續開倉，而非都不動作！)
            c_body = abs(market_data.curr_k["close"] - market_data.curr_k["open"])
            is_strong_breakout = bool(
                is_agile_trigger
                or is_breakout_long
                or is_continuation_long
                or is_breakdown_short
                or is_continuation_short
                or three_bar_long
                or three_bar_short
                or ((can_long or can_short) and c_body >= 0.25 * market_data.atr)
            )

            # 1. 平倉後強制冷卻檢查 (若趨勢已明確反轉為反向多頭/空頭，豁免舊倉冷卻，立即順新趨勢開倉！)
            acc_key = cls._get_account_key(account, symbol)
            last_exit = cls._last_exit_record.get(acc_key)
            if last_exit:
                last_exit_side = str(last_exit.get("side", "")).upper()
                is_opposite_reversal_ready = bool(
                    (last_exit_side == "SHORT" and is_1m_confirmed_bull and can_long)
                    or (last_exit_side == "LONG" and is_1m_confirmed_bear and can_short)
                )
                elapsed_sec = time.time() - float(last_exit.get("timestamp", 0.0))
                if not is_opposite_reversal_ready:
                    if elapsed_sec < 20:
                        logger.debug(f"⏳ [COOLDOWN] {symbol} 平倉後 20 秒防重複下單保護中 ({elapsed_sec:.1f}s)")
                        return {"status": "VETO", "symbol": symbol, "reason": "VETO_EXIT_COOLDOWN"}
                    if not is_strong_breakout and elapsed_sec < 120:
                        logger.debug(f"⏳ [COOLDOWN] {symbol} 平倉後防翻轉冷卻中: 剩餘 {120 - elapsed_sec:.1f} 秒")
                        return {"status": "VETO", "symbol": symbol, "reason": "VETO_EXIT_COOLDOWN"}

            # 1.1 禁止無效的「平多 + 同價位重複買多」：若已突破外軌或順勢收陽延續則放行延續開倉！
            if last_exit and not is_strong_breakout:
                last_side = last_exit.get("side")
                exit_p = float(last_exit.get("price", 0.0) or 0.0)
                elapsed_churn_sec = time.time() - float(last_exit.get("timestamp", 0.0))
                if exit_p > 0 and elapsed_churn_sec < 180:
                    dist_to_exit = abs(curr_quote - exit_p) / exit_p
                    if (last_side == "LONG" and can_long and dist_to_exit < 0.003) or (last_side == "SHORT" and can_short and dist_to_exit < 0.003):
                        logger.info(
                            f"🚫 [DISABLE_CHURNING_VETO] {symbol} 剛平倉離場，當前報價 ({curr_quote}) 仍位於原平倉價附近 ({exit_p}, 離差={dist_to_exit*100:.2f}%)，"
                            f"暫緩無動能重複開倉！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "VETO_CHURNING_SAME_ZONE"}

            # 2. 單一合約 1 小時內開倉次數上限
            now_ts = time.time()
            entry_history = [t for t in cls._hourly_entry_records.get(acc_key, cls._hourly_entry_records.get(symbol, [])) if (now_ts - t) < 3600.0]
            cls._hourly_entry_records[acc_key] = entry_history
            cls._hourly_entry_records[symbol] = entry_history
            if len(entry_history) >= cls.MAX_ENTRIES_PER_HOUR:
                logger.info(f"🚫 [RATE_LIMIT] {symbol} 1 小時內已開倉 {len(entry_history)} 次，達到上限 ({cls.MAX_ENTRIES_PER_HOUR} 次)，禁止開倉！")
                return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_HOURLY_ENTRY_LIMIT_EXCEEDED"}

            # 🎯 最高優先級：敏捷突破/穿中軌/順勢延續優先點火，次之真谷底/真峰頂第一時間點火
            if is_agile_trigger and agile_signal:
                candidate_signal = agile_signal
            elif is_true_bottom and (not is_zoomed_out_bearish) and hard_long_allowed and kc_slope >= 0 and curr_close >= kc_mid_val and ma3_curr >= kc_mid_val:
                candidate_signal = "SWING_PIVOT_LONG"
                can_long = True
            elif is_true_peak and (not is_zoomed_out_bullish) and hard_short_allowed and kc_slope <= 0 and curr_close <= kc_mid_val and ma3_curr <= kc_mid_val:
                candidate_signal = "SWING_PIVOT_SHORT"
                can_short = True
            elif (is_breakout_long or is_continuation_long) and (not is_zoomed_out_bearish):
                candidate_signal = "CONTINUATION_OUTSIDE_KC_LONG" if (p_close > p_up or is_continuation_long) else "BREAKOUT_LONG"
            elif (is_breakdown_short or is_continuation_short) and (not is_zoomed_out_bullish):
                candidate_signal = "CONTINUATION_OUTSIDE_KC_SHORT" if (p_close < p_low or is_continuation_short) else "BREAKDOWN_SHORT"
            elif is_swing_pivot and ((swing_pivot['side'] == "LONG" and can_long and not is_zoomed_out_bearish) or (swing_pivot['side'] == "SHORT" and can_short and not is_zoomed_out_bullish)):
                candidate_signal = f"SWING_PIVOT_{swing_pivot['side']}"
            elif can_long and not is_zoomed_out_bearish:
                if is_ma_cross_long:
                    candidate_signal = "ENTRY_LONG_MA3_MA15_CROSS_MID"
                elif is_pullback_long:
                    candidate_signal = "TREND_PULLBACK_LONG"
                elif (market_data.curr_k["close"] > market_data.ema_fast and market_data.ema_fast >= market_data.ema_slow and market_data.kc_middle_slope >= 0 and ma5_curr > ma5_prev):
                    if market_data.curr_k["close"] > market_data.kc_upper:
                        candidate_signal = "CONTINUATION_OUTSIDE_KC_LONG" if p_close > p_up else "BREAKOUT_LONG"
                    else:
                        candidate_signal = "ENTRY_LONG_EMA_CROSS_MID"

            elif can_short and not is_zoomed_out_bullish:
                if is_ma_cross_short:
                    candidate_signal = "ENTRY_SHORT_MA3_MA15_CROSS_MID"
                elif is_pullback_short:
                    candidate_signal = "TREND_PULLBACK_SHORT"
                elif (market_data.curr_k["close"] < market_data.ema_fast and market_data.ema_fast <= market_data.ema_slow and market_data.kc_middle_slope <= 0 and ma5_curr < ma5_prev):
                    if market_data.curr_k["close"] < market_data.kc_lower:
                        candidate_signal = "CONTINUATION_OUTSIDE_KC_SHORT" if p_close < p_low else "BREAKDOWN_SHORT"
                    else:
                        candidate_signal = "ENTRY_SHORT_EMA_CROSS_MID"

            if candidate_signal:
                # Zero-trust whitelist: ordinary candidates need the closed-bar
                # MA3/MA15 cross; breakout and pivot routes use their own
                # closed-bar structural confirmation.
                from core.services.strict_ma3_ma15_entry_gate import evaluate_ma3_ma15_entry

                candidate_side = "LONG" if "LONG" in candidate_signal else "SHORT"

                # ── 🛡️ 全域順勢鐵律 + 拉遠視角防逆勢閘門 (Master KC & Zoomed-Out Trend Isolation Gate) ──
                # 0. 🐒 猴市只做突破（方案 3）：猴市環境下，未出現已收線實體突破 30 根區間高/低點，100% 一票否決任何常規開倉！
                if market_regime == "RANGE" and range_breakout_side != candidate_side:
                    logger.info(
                        f"🚫 [BLOCKED_RANGE_MONKEY_MARKET] {symbol} 當前處於【猴市震盪】(cross_count={ema60_cross_count}, flat={is_flat_macro})，"
                        f"尚未確認實體突破區間 (range_high={range_high}, range_low={range_low})，一票否決摸頂摸底，嚴防雙巴！"
                    )
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_RANGE_MONKEY_MARKET", "details": "猴市震盪市場，嚴格等待突破才開倉！"}

                # 0.05 🚫 【使用者最高鐵律：震盪區不要開倉、MA3中軌貼合不開倉】
                if is_ma3_midline_adhered:
                    logger.info(
                        f"🚫 [BLOCKED_MA3_MIDLINE_ADHERED] {symbol} MA3 與 KC 中軌/MA15 貼合 (ma3={ma3_curr:.6f}, kc_mid={kc_mid_val:.6f}, "
                        f"dist_ratio={ma_dist_now:.5f}, m3_m15_spread={ma3_ma15_spread_ratio:.5f})，嚴格禁止在貼合區開倉！"
                    )
                    return {
                        "status": "VETO",
                        "symbol": symbol,
                        "reason": "BLOCKED_MA3_MIDLINE_ADHERED",
                        "details": "MA3 與 KC 中軌貼合，缺乏明確趨勢動能，嚴禁開倉！",
                    }
                if is_chop_zone and range_breakout_side != candidate_side:
                    logger.info(
                        f"🚫 [BLOCKED_CHOP_CONSOLIDATION_ZONE] {symbol} 當前處於震盪糾纏區 (regime={market_regime}, sqz={is_sqz_now}, "
                        f"flat_kc={is_kc_slope_flat}, mid_cross_8={ma3_mid_cross_8}, m15_cross_8={ma3_m15_cross_8})，嚴格禁止在震盪區開倉！"
                    )
                    return {
                        "status": "VETO",
                        "symbol": symbol,
                        "reason": "BLOCKED_CHOP_CONSOLIDATION_ZONE",
                        "details": "當前處於震盪區或均線糾纏區，嚴禁開倉！",
                    }

                # 0.1 ⚖️ 【使用者最高鐵律】：MA3 必須在 MA15 上方 (ma3_curr > ma15_curr) 才准開多單；MA3 必須在 MA15 下方 (ma3_curr < ma15_curr) 才准開空單！
                if candidate_side == "LONG" and ma3_curr <= ma15_curr:
                    logger.info(
                        f"🚫 [BLOCKED_MA3_MA15_RULE] {symbol} 開多但 MA3 ({ma3_curr:.6f}) <= MA15 ({ma15_curr:.6f})，嚴格一票否決！"
                    )
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_MA3_NOT_ABOVE_MA15", "details": "MA3 必須在 MA15 上方才准開多單！"}
                if candidate_side == "SHORT" and ma3_curr >= ma15_curr:
                    logger.info(
                        f"🚫 [BLOCKED_MA3_MA15_RULE] {symbol} 開空但 MA3 ({ma3_curr:.6f}) >= MA15 ({ma15_curr:.6f})，嚴格一票否決！"
                    )
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_MA3_NOT_BELOW_MA15", "details": "MA3 必須在 MA15 下方才准開空單！"}

                # 0.2 🛡️ 【三大危險區開倉防禦】：前低支撐/前高壓力牆、均線/外軌超賣超買乖離、外軌穿透影線止跌受阻
                if candidate_side == "SHORT" and not allow_short_defense:
                    logger.info(
                        f"🚫 [BLOCKED_SHORT_DANGER_ZONE] {symbol} 開空命中三大危險區防禦 "
                        f"(support_wall={is_support_wall_block_short}, oversold_bias={is_oversold_block_short}, lower_shadow={is_kc_lower_shadow_block_short}, "
                        f"ma5_bias={ma5_bias_ref:.5f})，一票否決追空！"
                    )
                    return {
                        "status": "VETO",
                        "symbol": symbol,
                        "reason": "BLOCKED_SHORT_BY_SUPPORT_OR_OVERSOLD_OR_SHADOW",
                        "details": "觸及前波支撐位、KC下軌/MA5超賣乖離過大或下影線大於實體，嚴禁在底部支撐區追空！",
                    }
                if candidate_side == "LONG" and not allow_long_defense:
                    logger.info(
                        f"🚫 [BLOCKED_LONG_DANGER_ZONE] {symbol} 開多命中三大危險區防禦 "
                        f"(resistance_wall={is_resistance_wall_block_long}, overbought_bias={is_overbought_block_long}, upper_shadow={is_kc_upper_shadow_block_long}, "
                        f"ma5_bias={ma5_bias_ref:.5f})，一票否決追多！"
                    )
                    return {
                        "status": "VETO",
                        "symbol": symbol,
                        "reason": "BLOCKED_LONG_BY_RESISTANCE_OR_OVERBOUGHT_OR_SHADOW",
                        "details": "觸及前波壓力位、KC上軌/MA5超買乖離過大或上影線大於實體，嚴禁在頂部壓力區追多！",
                    }

                # 1. 空頭趨勢嚴禁開多：拉遠看是空頭 (is_zoomed_out_bearish) 或 3棒/5棒 KC 中軌下行 (kc_slope_3_ref <= 0 or kc_slope_5_ref < 0) 或 MA15 下行 (m15_c < m15_p or m15_slope_3_ref < 0 or m15_slope_5_ref < 0) 或 KC 單棒中軌下行 (kc_slope <= 0) 或收盤位於中軌/MA15之下 或 MA3 低於中軌 或 MA3 未向上 或 非陽線，100% 一票否決開多！
                # 2. 多頭趨勢嚴禁開空：拉遠看是多頭 (is_zoomed_out_bullish) 或 3棒/5棒 KC 中軌上行 (kc_slope_3_ref >= 0 or kc_slope_5_ref > 0) 或 MA15 上行 (m15_c > m15_p or m15_slope_3_ref > 0 or m15_slope_5_ref > 0) 或 KC 單棒中軌上行 (kc_slope >= 0) 或收盤位於中軌/MA15之上 或 MA3 高於中軌 或 MA3 未向下 或 非陰線，100% 一票否決開空！
                if candidate_side == "LONG" and (
                    is_zoomed_out_bearish
                    or is_macro_bear
                    or kc_slope <= 0
                    or kc_slope_3_ref <= 0
                    or kc_slope_5_ref < 0
                    or m15_c < m15_p
                    or m15_slope_3_ref < 0
                    or m15_slope_5_ref < 0
                    or curr_close < kc_mid_val
                    or curr_close < ma15_curr
                    or ma3_curr < kc_mid_val
                    or ma3_curr <= ma3_prev
                    or curr_close <= curr_open
                ):
                    logger.info(
                        f"🚫 [BLOCKED_LONG_ON_BEARISH_TREND] {symbol} 違反開多精準方向門檻 (拉遠空頭={is_zoomed_out_bearish}, "
                        f"kc_slope_3={kc_slope_3_ref:.8f}, kc_slope_5={kc_slope_5_ref:.8f}, m15_slope_5={m15_slope_5_ref:.8f}, slope={kc_slope:.8f}, close={curr_close:.8f}, kc_mid={kc_mid_val:.8f})，100% 絕對禁止在空頭下降通道中開多！"
                    )
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_LONG_ON_BEARISH_TREND", "details": "拉遠看或中軌/MA15為空頭下降趨勢，強制阻斷買多！"}
                if candidate_side == "SHORT" and (
                    is_zoomed_out_bullish
                    or is_macro_bull
                    or kc_slope >= 0
                    or kc_slope_3_ref >= 0
                    or kc_slope_5_ref > 0
                    or m15_c > m15_p
                    or m15_slope_3_ref > 0
                    or m15_slope_5_ref > 0
                    or curr_close > kc_mid_val
                    or curr_close > ma15_curr
                    or ma3_curr > kc_mid_val
                    or ma3_curr >= ma3_prev
                    or curr_close >= curr_open
                ):
                    logger.info(
                        f"🚫 [BLOCKED_SHORT_ON_BULLISH_TREND] {symbol} 違反開空精準方向門檻 (拉遠多頭={is_zoomed_out_bullish}, "
                        f"kc_slope_3={kc_slope_3_ref:.8f}, kc_slope_5={kc_slope_5_ref:.8f}, m15_slope_5={m15_slope_5_ref:.8f}, slope={kc_slope:.8f}, close={curr_close:.8f}, kc_mid={kc_mid_val:.8f})，100% 絕對禁止在多頭上升通道中開空！"
                    )
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_SHORT_ON_BULLISH_TREND", "details": "拉遠看或中軌/MA15為多頭上升趨勢，強制阻斷賣空！"}

                # ── 🛡️ 妖幣專用 3 大開倉前防護審查 (Demon Coin Defense Pre-Entry Gate) ──
                # 1. 訊號冷卻期機制 (Cooldown Period) - 若大週期已正式扭轉且站穩中軌與 MA3，豁免舊反手冷卻鎖死
                is_trend_confirmed_break = bool(
                    (candidate_side == "LONG" and kc_slope > 0 and curr_close > kc_mid_val and ma3_curr > kc_mid_val and curr_close > curr_open)
                    or (candidate_side == "SHORT" and kc_slope < 0 and curr_close < kc_mid_val and ma3_curr < kc_mid_val and curr_close < curr_open)
                )
                passed_cd, cd_msg = DemonCoinDefenseController.check_entry_cooldown(symbol, candidate_side)
                if not passed_cd and not is_trend_confirmed_break:
                    logger.info(f"⏳ [DEMON_DEFENSE_COOLDOWN] {symbol} {candidate_side} 開倉遭攔截: {cd_msg}")
                    return {"status": "VETO", "symbol": symbol, "reason": "VETO_COOLDOWN_FROZEN", "details": cd_msg}

                # 2. 多/空門檻不對稱與量能確認 (Asymmetric Volume Confirmation) - 順勢敏捷開倉/延續開倉豁免 1.5x 量能死鎖
                curr_volume = float(market_data.curr_k.get("volume", 0.0))
                vol_ma20_ref = float(getattr(market_data, "volume_ma20", 0.0) or 0.0)
                if vol_ma20_ref <= 0 and raw_df is not None and "volume" in raw_df.columns:
                    vol_ma20_ref = float(raw_df["volume"].rolling(20, min_periods=1).mean().iloc[-1])
                passed_vol, vol_msg = DemonCoinDefenseController.check_volume_confirmation(candidate_side, curr_volume, vol_ma20_ref)
                if not passed_vol and not (is_agile_trigger or is_breakout_long or is_continuation_long):
                    logger.info(f"📊 [DEMON_DEFENSE_VOLUME] {symbol} {candidate_side} 開倉遭攔截: {vol_msg}")
                    return {"status": "VETO", "symbol": symbol, "reason": "VETO_LONG_INSUFFICIENT_VOLUME", "details": vol_msg}

                # 3. 爆量插針/超買過熱過濾 (ATR Extreme Filter)
                passed_heat, heat_msg = DemonCoinDefenseController.check_atr_extreme_overheat(
                    candidate_side, market_data.curr_k, atr_v, ma5_curr=ma5_curr, kc_upper=kc_u
                )
                if not passed_heat:
                    logger.info(f"🔥 [DEMON_DEFENSE_OVERHEAT] {symbol} {candidate_side} 開倉遭攔截: {heat_msg}")
                    return {"status": "VETO", "symbol": symbol, "reason": "VETO_ATR_EXTREME_OVERHEATED", "details": heat_msg}

                if candidate_side == "LONG":
                    if not hard_long_allowed and not (is_meme_contract or is_swing_pivot or is_agile_trigger):
                        logger.warning(
                            f"🛑 [HARD_LONG_VETO] {symbol} {candidate_signal} 違反開多絕對門檻 "
                            f"(ma3_up={is_ma3_trending_up}, ma3>=slow={is_ma3_above_slow}, bullish={is_candle_bullish})，一票否決！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_HARD_LONG_MA3_OR_CANDLE"}
                    if not direction_long_ok and not (is_meme_contract or is_swing_pivot or three_bar_long or is_agile_trigger):
                        logger.warning(
                            f"🛑 [DIRECTION_MASTER_LOCK] {symbol} {candidate_signal} 違反開多方向鎖 "
                            f"(ma3={ma3_curr:.6f}, kc_mid={kc_mid_val:.6f}, ma3_prev={ma3_prev:.6f})，一票否決！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_DIRECTION_MASTER_LOCK_LONG"}
                elif candidate_side == "SHORT":
                    if not hard_short_allowed and not (is_meme_contract or is_swing_pivot or is_agile_trigger):
                        logger.warning(
                            f"🛑 [HARD_SHORT_VETO] {symbol} {candidate_signal} 違反開空絕對門檻 "
                            f"(ma3_down={is_ma3_trending_down}, ma3<=slow={is_ma3_below_slow}, bearish={is_candle_bearish})，一票否決！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_HARD_SHORT_MA3_OR_CANDLE"}
                    if not direction_short_ok and not (is_meme_contract or is_swing_pivot or three_bar_short or is_agile_trigger):
                        logger.warning(
                            f"🛑 [DIRECTION_MASTER_LOCK] {symbol} {candidate_signal} 違反開空方向鎖 "
                            f"(ma3={ma3_curr:.6f}, kc_mid={kc_mid_val:.6f}, ma3_prev={ma3_prev:.6f})，一票否決！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_DIRECTION_MASTER_LOCK_SHORT"}

                # ── 🧬 妖幣專用 3 大風控與籌碼濾網 (Meme Coin Anti-Fakeout Engine) ──
                from core.symbol_aliases import is_meme_breakout_symbol
                is_meme_contract = (
                    is_meme_breakout_symbol(symbol)
                    or "MEME" in str(candidate_signal).upper()
                    or "PEPE" in str(symbol).upper()
                    or "LOBSTER" in str(symbol).upper()
                    or "CAP" in str(symbol).upper()
                    or "龙虾" in str(symbol)
                    or "龍蝦" in str(symbol)
                )
                if is_meme_contract:
                    ema200_val = float(market_data.curr_k.get("ema200", getattr(market_data, "ema200", 0.0)))
                    if ema200_val <= 0 and raw_df is not None and "close" in raw_df.columns:
                        ema200_val = float(raw_df["close"].ewm(span=200, adjust=False).mean().iloc[-1])
                    vol_ma20_val = float(market_data.curr_k.get("volume_ma20", getattr(market_data, "volume_ma20", 0.0)))
                    if vol_ma20_val <= 0 and raw_df is not None and "volume" in raw_df.columns:
                        vol_ma20_val = float(raw_df["volume"].rolling(20, min_periods=1).mean().iloc[-1])

                    fr_val = float(
                        getattr(market_data, "funding_rate", None)
                        or market_data.curr_k.get("funding_rate")
                        or (raw_df.attrs.get("funding_rate") if hasattr(raw_df, "attrs") else None)
                        or getattr(engine_or_account, "funding_rates", {}).get(symbol, {}).get("fundingRate")
                        or 0.0
                    )
                    oi_change_val = float(
                        getattr(market_data, "oi_change_pct", None)
                        or market_data.curr_k.get("oi_change_pct")
                        or (raw_df.attrs.get("oi_change_pct") if hasattr(raw_df, "attrs") else None)
                        or 0.01
                    )
                    side_param = "BUY" if candidate_side == "LONG" else "SELL"
                    meme_ok, meme_fail_reason = evaluate_meme_trade_valid(
                        side=side_param,
                        curr_k=market_data.curr_k,
                        ma3_curr=ma3_curr,
                        ma15_curr=ma15_curr,
                        ema200_curr=ema200_val,
                        current_atr=atr_val_now,
                        volume_ma20=vol_ma20_val,
                        funding_rate=fr_val,
                        oi_change_pct=oi_change_val,
                        prev_k=market_data.prev_k,
                    )
                    is_direct_actionable = any(k in str(candidate_signal).upper() for k in ("CROSS_MID", "BREAKOUT", "BREAKDOWN", "SWING_PIVOT", "PIVOT", "AGILE"))
                    if not meme_ok and not is_direct_actionable:
                        logger.warning(
                            f"🛑 [MEME_ANTI_FAKEOUT_VETO] {symbol} {candidate_signal} 觸發妖幣風控一票否決: {meme_fail_reason} "
                            f"(body={c_body_now:.6f}, 1.2*atr={1.2*atr_val_now:.6f}, vol={float(market_data.curr_k.get('volume',0)):.1f}, "
                            f"vol_ma20={vol_ma20_val:.1f}, ema200={ema200_val:.6f}, fr={fr_val:.4%}, oi_change={oi_change_val:.2%})"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": f"BLOCKED_BY_MEME_FILTER:{meme_fail_reason}"}

                entry_frame = getattr(market_data, "raw_df", None)
                account_gate_state = getattr(account, "entry_filter_last", None)
                if not isinstance(account_gate_state, dict):
                    account_gate_state = {}
                    try:
                        account.entry_filter_last = account_gate_state
                    except (AttributeError, TypeError):
                        pass
                persisted_trade_bars = account_gate_state.get(
                    "strict_ma3_ma15_last_trade_bar", {}
                )
                if not isinstance(persisted_trade_bars, dict):
                    persisted_trade_bars = {}
                atr_for_stop = float((swing_pivot or {}).get("atr") or getattr(market_data, "atr", 0.0) or 0.0)
                execution_price = float(getattr(market_data, "current_price", 0.0) or 0.0)
                if is_swing_pivot:
                    stop_loss = float(swing_pivot["stop_loss_price"])
                elif three_bar_stop is not None:
                    stop_loss = float(three_bar_stop)
                else:
                    atr_mult = 2.0 if is_meme_contract else 3.0
                    stop_loss = (
                        execution_price - atr_mult * atr_for_stop if candidate_side == "LONG"
                        else execution_price + atr_mult * atr_for_stop
                    )
                if is_agile_trigger:
                    from types import SimpleNamespace
                    from core.services.candle_data import closed_entry_candles
                    closed_agile_bars = closed_entry_candles(entry_frame) if entry_frame is not None else None
                    agile_bar_time = int(float(closed_agile_bars.iloc[-1]["timestamp"])) if (closed_agile_bars is not None and len(closed_agile_bars)) else int(time.time() * 1000)
                    last_used = persisted_trade_bars.get(symbol) or cls._last_trade_bar_time.get(symbol)
                    valid_stop = stop_loss < execution_price if candidate_side == "LONG" else stop_loss > execution_price
                    if not valid_stop:
                        stop_loss = execution_price - 2.5 * atr_for_stop if candidate_side == "LONG" else execution_price + 2.5 * atr_for_stop
                    if last_used and last_used == agile_bar_time:
                        return {"status": "VETO", "symbol": symbol, "reason": "AGILE_TRIGGER_BAR_ALREADY_TRADED"}
                    whitelist = SimpleNamespace(
                        action=f"OPEN_{candidate_side}", bar_time=agile_bar_time,
                        entry_price=execution_price, stop_loss_price=stop_loss,
                        checklist={"AGILE_TRIGGER_AUTHORIZED": True, "STOP_VALID": True},
                        violations=[],
                    )
                elif is_swing_pivot:
                    from types import SimpleNamespace
                    from core.services.candle_data import closed_entry_candles
                    closed_pivot_bars = closed_entry_candles(entry_frame)
                    pivot_bar_time = int(float(closed_pivot_bars.iloc[-1]["timestamp"])) if len(closed_pivot_bars) else None
                    last_used = persisted_trade_bars.get(symbol) or cls._last_trade_bar_time.get(symbol)
                    valid_stop = stop_loss < execution_price if candidate_side == "LONG" else stop_loss > execution_price
                    if (not pivot_bar_time or last_used == pivot_bar_time or not valid_stop):
                        return {"status": "VETO", "symbol": symbol, "reason": "SWING_PIVOT_BAR_OR_STOP_INVALID"}
                    whitelist = SimpleNamespace(
                        action=f"OPEN_{candidate_side}", bar_time=pivot_bar_time,
                        entry_price=execution_price, stop_loss_price=stop_loss,
                        checklist={"CLOSED_SWING_PIVOT": True, "PIVOT_STOP_VALID": True},
                        violations=[],
                    )
                else:
                    from core.services.candle_data import closed_entry_candles
                    whitelist_frame = closed_entry_candles(entry_frame)
                    is_breakout_or_continuation = (
                        (is_breakout_long or is_continuation_long) if candidate_side == "LONG"
                        else (is_breakdown_short or is_continuation_short)
                    )
                    whitelist = evaluate_ma3_ma15_entry(
                        whitelist_frame,
                        candidate_side,
                        current_position="IDLE" if not has_position else "OPEN",
                        last_trade_bar_time=(
                            persisted_trade_bars.get(symbol)
                            or cls._last_trade_bar_time.get(symbol)
                        ),
                        stop_loss_price=stop_loss,
                        execution_price=execution_price,
                        confirmed_breakout=is_breakout_or_continuation,
                    )
                if whitelist.action not in ("OPEN_LONG", "OPEN_SHORT"):
                    reason = whitelist.violations[0] if whitelist.violations else "ZERO_TRUST_WHITELIST_REJECTED"
                    logger.info(
                        "[ZERO_TRUST_ENTRY_VETO] %s side=%s reason=%s checks=%s",
                        symbol, candidate_side, reason, whitelist.checklist,
                    )
                    return {
                        "status": "VETO", "action": whitelist.action, "symbol": symbol,
                        "reason": reason, "checklist": whitelist.checklist,
                        "violations": whitelist.violations,
                    }

                last_exit_context = {
                    **(last_exit or {}),
                    "hourly_entry_count": len(entry_history),
                    "current_time": now_ts,
                }
                if is_agile_trigger:
                    should_entry, reason, meta = True, f"AGILE_TRIGGER_{candidate_signal}", {
                        "side": candidate_side,
                        "direction_override": True,
                        "entry_type": "CROSS_MID" if "CROSS_MID" in candidate_signal else "BREAKOUT",
                        "stop_loss_price": stop_loss,
                        "is_cross_mid": "CROSS_MID" in candidate_signal,
                        "allow_kc_internal": True,
                        "strategy_mode": "AGILE_MOMENTUM",
                    }
                elif is_swing_pivot:
                    should_entry, reason, meta = True, "CLOSED_SWING_PIVOT_OVERRIDE_ALIGNED", {
                        "side": candidate_side,
                        "direction_override": True,
                        "entry_type": "SWING_PIVOT",
                        "stop_loss_price": stop_loss,
                    }
                elif three_bar_long or three_bar_short:
                    should_entry, reason, meta = True, f"THREE_BAR_CONTINUATION_{candidate_side}", {
                        "side": candidate_side,
                        "direction_override": True,
                        "entry_type": "CONTINUATION",
                        "stop_loss_price": stop_loss,
                    }
                else:
                    should_entry, reason, meta = EntryGate.evaluate(
                        signal_type=candidate_signal,
                        market_data=market_data,
                        last_exit_context=last_exit_context,
                    )
                if should_entry:
                    side = meta.get("side", "LONG" if "LONG" in candidate_signal else "SHORT")
                    prev_mem_bar_time = cls._last_trade_bar_time.get(symbol)
                    prev_persisted_bar_time = persisted_trade_bars.get(symbol)
                    # Claim synchronously before the first await in execution.
                    cls._last_trade_bar_time[symbol] = whitelist.bar_time
                    persisted_trade_bars[symbol] = whitelist.bar_time
                    account_gate_state["strict_ma3_ma15_last_trade_bar"] = persisted_trade_bars
                    save_state = getattr(account, "save_state", None)
                    if not callable(save_state):
                        return {
                            "status": "VETO", "action": whitelist.action, "symbol": symbol,
                            "reason": "BAR_CLAIM_STORE_UNAVAILABLE",
                            "checklist": whitelist.checklist,
                        }
                    try:
                        try:
                            save_state(strict=True)
                        except TypeError:
                            save_state()
                    except Exception as exc:
                        logger.error(
                            "[ZERO_TRUST_ENTRY_VETO] %s unable to persist bar claim: %s",
                            symbol, exc,
                        )
                        return {
                            "status": "VETO", "symbol": symbol,
                            "action": whitelist.action,
                            "reason": "BAR_CLAIM_PERSISTENCE_FAILED",
                            "checklist": whitelist.checklist,
                        }
                    logger.info(f"🎯 [ENTRY_TRIGGERED] {symbol} {side} {reason} | meta={meta}")
                    print(f"🎯 [ENTRY_TRIGGERED] {symbol} {side} {reason}", flush=True)
                    call_meta = dict(meta or {})
                    call_meta["candidate_signal_type"] = candidate_signal
                    call_meta["whitelist_entry_price"] = whitelist.entry_price
                    call_meta["stop_loss_price"] = whitelist.stop_loss_price
                    call_meta["whitelist_checklist"] = whitelist.checklist
                    call_meta["atr"] = atr_for_stop
                    call_meta["macro_trend"] = "BULLISH" if is_1m_confirmed_bull else ("BEARISH" if is_1m_confirmed_bear else market_data.macro_trend)
                    if is_swing_pivot:
                        call_meta.update(
                            entry_type="SWING_PIVOT",
                            direction_override=True,
                            stop_loss_price=stop_loss,
                            source="UNIFIED_PIPELINE|SWING_PIVOT",
                            swing_pivot=swing_pivot,
                        )
                    elif is_agile_trigger:
                        call_meta.update(
                            entry_type="CROSS_MID" if "CROSS_MID" in candidate_signal else "BREAKOUT",
                            direction_override=True,
                            is_agile_trigger=True,
                            is_cross_mid="CROSS_MID" in candidate_signal,
                            allow_kc_internal=True,
                            strategy_mode="AGILE_MOMENTUM",
                            stop_loss_price=stop_loss,
                            source=f"UNIFIED_PIPELINE|{candidate_signal}",
                        )
                    elif three_bar_long or three_bar_short:
                        call_meta.update(
                            entry_type="CONTINUATION",
                            direction_override=True,
                            stop_loss_price=stop_loss,
                            source=f"UNIFIED_PIPELINE|THREE_BAR_CONTINUATION_{candidate_side}",
                        )
                    call_meta["upstream_entry_gate_authorized"] = True
                    if any(k in candidate_signal for k in ("CROSS_MID", "MA_CROSS", "MID_LINE", "PULLBACK", "AGILE")):
                        call_meta["is_cross_mid"] = True
                        call_meta["allow_kc_internal"] = True
                        call_meta["direction_override"] = True
                        call_meta["strategy_mode"] = "AGILE_MOMENTUM"
                        call_meta["entry_type"] = "CROSS_MID"
                    if "raw_df" not in call_meta or call_meta["raw_df"] is None:
                        call_meta["raw_df"] = getattr(market_data, "raw_df", None)
                    if call_meta.get("raw_df") is None and klines_df is not None:
                        call_meta["raw_df"] = klines_df
                    raw_df_instance = call_meta.get("raw_df")
                    try:
                        call_meta["intrabar_authorized"] = bool(
                            raw_df_instance is not None
                            and not bool(raw_df_instance.iloc[-1].get("is_closed", True))
                        )
                    except (AttributeError, IndexError, TypeError):
                        call_meta["intrabar_authorized"] = False
                    if hasattr(raw_df_instance, "attrs"):
                        for k in ("direction_override", "is_agile_trigger", "is_cross_mid", "entry_type", "strategy_mode", "allow_kc_internal"):
                            if k in call_meta:
                                raw_df_instance.attrs[k] = call_meta[k]
                    call_meta["no_rigid_sl"] = True
                    call_meta["stop_loss_price"] = None
                    success = await cls.open_position(
                        engine_or_account=engine_or_account,
                        symbol=symbol,
                        side=side,
                        price=market_data.current_price,
                        reason=f"EntryGate:{reason}",
                        meta=call_meta,
                    )
                    if not success and symbol not in getattr(account, "positions", {}):
                        # Roll back bar claim when pre-order gates veto before position is opened
                        if prev_mem_bar_time is None:
                            cls._last_trade_bar_time.pop(symbol, None)
                        else:
                            cls._last_trade_bar_time[symbol] = prev_mem_bar_time
                        if prev_persisted_bar_time is None:
                            persisted_trade_bars.pop(symbol, None)
                        else:
                            persisted_trade_bars[symbol] = prev_persisted_bar_time
                        account_gate_state["strict_ma3_ma15_last_trade_bar"] = persisted_trade_bars
                        try:
                            try:
                                save_state(strict=True)
                            except TypeError:
                                save_state()
                        except Exception:
                            pass
                    return {
                        "status": "ENTRY_EXECUTED" if success else "VETO",
                        "symbol": symbol,
                        "action": whitelist.action if success else "HOLD / PASS",
                        "side": side, "reason": reason, "success": success,
                        "checklist": whitelist.checklist,
                        "entry_price": whitelist.entry_price,
                        "stop_loss_price": whitelist.stop_loss_price,
                    }
                else:
                    logger.debug(f"🚫 [VETO] {symbol} {reason}")
                    return {"status": "VETO", "symbol": symbol, "reason": reason}

        return {"status": "IDLE", "symbol": symbol}

    _last_exit_record: Dict[str, Dict[str, Any]] = {}
    _last_trade_bar_time: Dict[str, int] = {}
    _hourly_entry_records: Dict[str, list] = {}
    MAX_ENTRIES_PER_HOUR: int = 15

    @classmethod
    def _get_account_key(cls, account: Any, symbol: str) -> str:
        acc_id = getattr(account, "account_id", None) or getattr(account, "id", None) or id(account)
        return f"{acc_id}:{symbol}"

    @classmethod
    async def close_position(
        cls,
        account: Any,
        symbol: str,
        price: float,
        reason: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """全系統唯一平倉執行管道"""
        try:
            if hasattr(account, "close_position"):
                closed_side = None
                positions_dict = getattr(account, "positions", {})
                if isinstance(positions_dict, dict) and symbol in positions_dict:
                    closed_side = positions_dict[symbol].get("side")

                raw_df = (meta or {}).get("raw_df")
                if raw_df is not None:
                    if not hasattr(account, "_channel_exit_frames"):
                        account._channel_exit_frames = {}
                    account._channel_exit_frames[symbol] = raw_df
                is_emergency = any(token in str(reason).upper() for token in (
                    "OPPOSITE_KC_BAND_BREACH", "EMERGENCY", "HARD_STOP", "CIRCUIT_BREAKER",
                    "WATERFALL", "CATASTROPHIC_DUMP", "CATASTROPHIC_PUMP", "閃崩", "瀑布"
                    , "EXIT_LONG_AT_PEAK", "EXIT_SHORT_AT_BOTTOM", "TWO_CLOSED_ADVERSE_ABNORMAL"
                ))
                is_limit = False if is_emergency else bool((meta or {}).get("is_limit", False))
                success = await account.close_position(
                    symbol=symbol,
                    current_price=price,
                    close_reason=reason,
                    is_limit=is_limit,
                )
                if success and closed_side:
                    acc_key = cls._get_account_key(account, symbol)
                    record = {
                        "side": closed_side,
                        "timestamp": time.time(),
                        "price": price,
                        "exit_price": price,
                        "exit_high": float((meta or {}).get("pivot_meta", {}).get("peak_high") or (meta or {}).get("highest_price") or price),
                        "exit_low": float((meta or {}).get("pivot_meta", {}).get("valley_low") or (meta or {}).get("lowest_price") or price),
                        "reason": reason,
                    }
                    cls._last_exit_record[acc_key] = record
                    cls._last_exit_record[symbol] = record
                    DemonCoinDefenseController.record_exit(
                        symbol=symbol,
                        closed_side=closed_side,
                        exit_price=price,
                        timestamp=time.time(),
                        reason=reason,
                    )
                    if hasattr(account, "position_meta") and isinstance(account.position_meta, dict):
                        pos_meta = account.position_meta.get(symbol, {})
                        if isinstance(pos_meta, dict):
                            pos_meta.pop("three_tier_net_roe_lock_state", None)
                            pos_meta.pop("highest_net_roe", None)
                            pos_meta.pop("peak_net_roe_pct", None)
                            pos_meta.pop("profit_lock_armed", None)
                            pos_meta.pop("peak_roe", None)
                    # 平倉後釋放該標的之同週期鎖定 (trend_cycle_lock) 與已交易 K 棒記錄，確保若趨勢延續能立即於下一根動能棒延續開倉，絕不卡死！
                    cls._last_trade_bar_time.pop(symbol, None)
                    entry_filter_state = getattr(account, "entry_filter_last", None)
                    if isinstance(entry_filter_state, dict):
                        cycle_locks = entry_filter_state.get("trend_cycle_lock")
                        if isinstance(cycle_locks, dict):
                            cycle_locks.pop(symbol, None)
                        persisted_bars = entry_filter_state.get("strict_ma3_ma15_last_trade_bar")
                        if isinstance(persisted_bars, dict):
                            persisted_bars.pop(symbol, None)
                    # 徹底撤銷交易所端殘留的所有條件委託單，防範孤兒止損單
                    try:
                        if hasattr(account, "_cancel_all_orders"):
                            await account._cancel_all_orders(symbol)
                    except Exception as cancel_exc:
                        logger.warning(f"⚠️ [EXECUTION] 平倉後撤單警告 {symbol}: {cancel_exc}")
                return success
            return False
        except Exception as exc:
            logger.error(f"[EXECUTION] close_position 異常 {symbol}: {exc}")
            return False

    @classmethod
    async def open_position(
        cls,
        engine_or_account: Any,
        symbol: str,
        side: str,
        price: float,
        reason: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """全系統唯一開倉執行管道"""
        try:
            account = getattr(engine_or_account, "account", engine_or_account)
            # 檢查持倉與鎖定 (反手開倉豁免平倉鎖並清理舊倉殘留)
            is_flip = (
                "FLIP" in str(reason).upper()
                or "FLIP" in str((meta or {}).get("entry_type", "")).upper()
                or (meta or {}).get("direction_override") is True
            )
            if is_flip:
                # 🔄 反手開倉 (Flip Position)：強制釋放平倉鎖與舊倉殘留，確保秒級開出反手單！
                closing_lock = getattr(account, "closing_lock", None)
                if isinstance(closing_lock, set):
                    closing_lock.discard(symbol)
                positions = getattr(account, "positions", None)
                if isinstance(positions, dict) and symbol in positions:
                    old_pos = positions.get(symbol)
                    if old_pos and str(old_pos.get("side", "")).upper() != str(side).upper():
                        positions.pop(symbol, None)
            else:
                if symbol in getattr(account, "positions", {}):
                    logger.info(f"[EXECUTION] {symbol} 已有持倉，跳過開倉")
                    return False
                if symbol in getattr(account, "closing_lock", set()):
                    logger.info(f"[EXECUTION] {symbol} 平倉鎖定中，跳過開倉")
                    return False

            # 計算保證金與槓桿
            wallet = float(getattr(account, "get_wallet_balance", lambda: 100.0)())
            available = float(getattr(account, "get_available_balance", lambda: wallet)())

            engine = engine_or_account if hasattr(engine_or_account, "account") else None
            leverage = 5
            if engine and hasattr(engine, "symbol_rotation"):
                try:
                    leverage = engine.symbol_rotation.get_dynamic_leverage(symbol, 100)
                except Exception:
                    leverage = 5

            amount_usdt = 0.0
            fee_buffer = 1.0 + leverage * 0.0005
            fee_safe_available = (available / fee_buffer) * 0.98
            if engine and hasattr(engine, "_full_wallet_entry_margin"):
                try:
                    amount_usdt = engine._full_wallet_entry_margin(wallet, available, leverage, symbol)
                except Exception:
                    amount_usdt = fee_safe_available
            else:
                amount_usdt = fee_safe_available

            # 依據 stepSize 進行向下截斷（Floor Truncate），嚴防超額保證金或 LOT_SIZE 拒單
            step_size = float((meta or {}).get("step_size") or (meta or {}).get("stepSize") or 0.0)
            if step_size > 0 and price > 0 and leverage > 0:
                raw_qty = (amount_usdt * leverage) / price
                truncated_qty = math.floor(raw_qty / step_size) * step_size
                if (truncated_qty * price) < 5.0:
                    logger.warning(
                        f"[EXECUTION] {symbol} 名義價值不足 5 USDT: {(truncated_qty * price):.2f}U "
                        f"(qty={truncated_qty}, price={price}, step_size={step_size})"
                    )
                    return False
                if truncated_qty > 0:
                    amount_usdt = (truncated_qty * price) / leverage

            print(f"🚀 [EXECUTION_ENTER] {symbol} {side} price={price} reason={reason} available={available:.2f} wallet={wallet:.2f}", flush=True)
            if amount_usdt < 5.0:
                print(f"⚠️ [EXECUTION] {symbol} 可用保證金不足: {amount_usdt:.2f}U (available={available:.2f}U)", flush=True)
                logger.warning(f"[EXECUTION] {symbol} 可用保證金不足: {amount_usdt:.2f}U (available={available:.2f}U)")
                return False

            frame = (meta or {}).get("raw_df")
            if frame is None and engine is not None and hasattr(engine, "_channel_exit_frames"):
                frame = engine._channel_exit_frames.get(symbol)
            if frame is None and hasattr(account, "entry_frame_provider"):
                provider = getattr(account, "entry_frame_provider", None)
                if callable(provider):
                    try:
                        frame = provider(symbol)
                    except Exception:
                        frame = None

            if frame is not None:
                if not hasattr(frame, "attrs"):
                    try:
                        frame.attrs = {}
                    except Exception:
                        pass
                if hasattr(frame, "attrs"):
                    frame.attrs["entry_finality_verified"] = True
                    frame.attrs.setdefault("timeframe_ms", 60_000)
                    macro_label = (meta or {}).get("macro_trend")
                    if macro_label:
                        frame.attrs["macro_trend"] = str(macro_label).upper()

            atr = float((meta or {}).get("atr") or (price * 0.015))
            requested_sl = (meta or {}).get("stop_loss_price")
            no_rigid_sl = bool((meta or {}).get("no_rigid_sl"))
            if requested_sl is not None and not no_rigid_sl:
                requested_sl = float(requested_sl)
                if (not math.isfinite(requested_sl)
                        or (str(side).upper() == "LONG" and requested_sl >= price)
                        or (str(side).upper() == "SHORT" and requested_sl <= price)):
                    logger.warning("[EXECUTION] %s invalid whitelist stop; fail closed", symbol)
                    return False
            if no_rigid_sl:
                initial_sl = 0.0  # 方案 B 無硬止損版: 完全取消固定價位硬止損 (No Rigid Stop Loss)
            elif str(side).upper() == "LONG":
                initial_sl = requested_sl if requested_sl is not None else max(0.0, price - 3.0 * atr)
            else:
                initial_sl = requested_sl if requested_sl is not None else price + 3.0 * atr

            bar_id = None
            if frame is not None and len(frame) > 0:
                try:
                    bar_id = float(frame.iloc[-1].get("timestamp", frame.iloc[-1].get("time", 0.0)))
                except (ValueError, TypeError):
                    bar_id = None
            if bar_id is None:
                bar_id = float((meta or {}).get("bar_timestamp") or time.time())

            confirmation = (
                "MID_LINE_BREAKDOWN" if "MID" in str(reason).upper() and side == "SHORT"
                else "MID_LINE_BREAKOUT" if "MID" in str(reason).upper() and side == "LONG"
                else (meta or {}).get("structure_confirmation") or (meta or {}).get("entry_pattern") or "BREAKOUT"
            )

            from core.gates.central_entry_gate import CentralEntryGate
            signal_id = (meta or {}).get("signal_id") or CentralEntryGate.generate_signal_id(symbol, side, "UNIFIED_PIPELINE")
            is_cross_mid = bool(
                (meta or {}).get("is_cross_mid")
                or (meta or {}).get("allow_kc_internal")
                or (meta or {}).get("strategy_mode") == "AGILE_MOMENTUM"
                or (meta or {}).get("entry_type") == "SWING_PIVOT"
                or any(k in str(reason).upper() for k in ("CROSS_MID", "MA_CROSS", "EMA_CROSS", "MID_LINE", "PULLBACK", "AGILE"))
                or any(k in str(confirmation).upper() for k in ("MID_LINE", "CROSS", "AGILE"))
            )

            entry_context = {
                "is_manual": True,
                "managed_by_bot": True,
                "entry_mode": "WILD_SWING",
                "strategy_mode": "AGILE_MOMENTUM" if is_cross_mid else "CONSERVATIVE",
                "source": "UNIFIED_PIPELINE",
                "candidate_signal_type": (meta or {}).get("candidate_signal_type"),
                "upstream_entry_gate_authorized": bool(
                    (meta or {}).get("upstream_entry_gate_authorized")
                ),
                "intrabar_authorized": bool((meta or {}).get("intrabar_authorized")),
                "entry_pattern": confirmation,
                "structure_confirmation": confirmation,
                "is_outer_entry": not is_cross_mid,
                "allow_kc_internal": bool((meta or {}).get("allow_kc_internal") or is_cross_mid),
                "direction_override": bool((meta or {}).get("direction_override") or is_cross_mid),
                "entry_type": (
                    (meta or {}).get("entry_type")
                    if (meta or {}).get("entry_type")
                    else ("CROSS_MID" if is_cross_mid else "BREAKOUT")
                ),
                "initial_sl": initial_sl,
                "stop_loss_price": initial_sl,
                "no_rigid_sl": no_rigid_sl,
                "signal_id": signal_id,
                "channel_confirmation_bar_id": bar_id,
                "bar_id": bar_id,
                "dual_tf_meta": {"bar_id": bar_id},
                "is_intrabar": bool((meta or {}).get("intrabar_authorized")),
                "meta": meta or {},
            }
            if frame is not None:
                if hasattr(frame, "attrs"):
                    frame.attrs.setdefault("timeframe_ms", 60_000.0)
                entry_context["entry_gate_frame"] = frame
                entry_context["frame"] = frame
                entry_context["raw_df"] = frame

            if hasattr(account, "open_position"):
                opened = await account.open_position(
                    symbol=symbol,
                    side=side,
                    price=price,
                    amount_usdt=amount_usdt,
                    sl=initial_sl,
                    tp=0.0,
                    reason=reason,
                    atr=atr,
                    leverage=leverage,
                    signal_score=100,
                    entry_context=entry_context,
                )
                if opened:
                    cls._hourly_entry_records.setdefault(symbol, []).append(time.time())
                    if hasattr(account, "position_meta") and isinstance(account.position_meta, dict):
                        pos_meta = account.position_meta.get(symbol, {})
                        if isinstance(pos_meta, dict):
                            pos_meta.pop("three_tier_net_roe_lock_state", None)
                            pos_meta.pop("highest_net_roe", None)
                            pos_meta.pop("peak_net_roe_pct", None)
                            pos_meta.pop("profit_lock_armed", None)
                            pos_meta.pop("peak_roe", None)
                    if hasattr(account, "positions") and isinstance(account.positions, dict) and symbol in account.positions:
                        pos = account.positions[symbol]
                        if isinstance(pos, dict):
                            pos.pop("three_tier_net_roe_lock_state", None)
                            pos.pop("highest_net_roe", None)
                            pos.pop("peak_net_roe_pct", None)
                            pos.pop("profit_lock_armed", None)
                            pos.pop("peak_roe", None)
                            wick_sl_val = float((meta or {}).get("meme_wick_stop_loss") or 0.0)
                            if wick_sl_val > 0:
                                pos["meme_wick_stop_loss"] = wick_sl_val
                    logger.info(f"🎉 [UNIFIED_ORDER_FILLED] {symbol} {side} @ {price} | Margin={amount_usdt:.2f}U Lev={leverage}x")
                    print(f"🎉 [UNIFIED_ORDER_FILLED] {symbol} {side} @ {price} | Margin={amount_usdt:.2f}U Lev={leverage}x", flush=True)
                else:
                    print(f"⚠️ [EXECUTION_FAILED] {symbol} {side} account.open_position returned False! reason={reason}", flush=True)
                return opened
            print(f"⚠️ [EXECUTION_FAILED] {symbol} account object does not have open_position method!", flush=True)
            return False
        except Exception as exc:
            print(f"❌ [EXECUTION_EXCEPTION] {symbol} open_position 異常: {exc}", flush=True)
            logger.error(f"[EXECUTION] open_position 異常 {symbol}: {exc}", exc_info=True)
            return False
