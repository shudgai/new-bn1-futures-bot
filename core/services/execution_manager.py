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
import asyncio
import logging
import math
import time
from typing import Dict, Any, Optional
import pandas as pd

from core.services.data_pipeline import DataPipeline, MarketDataSnapshot
from core.gates.unified_pipeline_gates import ExitGate, EntryGate
from core.gates.ultimate_short_exit_gatekeeper import (
    KC_MIDDLE_SLOPE_EXIT_REASONS,
    MA5_ANGLE_REVERSAL_EXIT_REASONS,
    SHARP_REVERSAL_EXIT_REASONS,
    UltimateShortExitGatekeeper,
)
from core.services.demon_coin_defense import DemonCoinDefenseController
from core.services.scalping_state_machine import ScalpingStateMachine
from core.services.market_structure_break import (
    check_macro_trend_end,
    find_htf_key_pivot,
    calculate_htf_ma15,
)

logger = logging.getLogger("ExecutionManager")
TRANSIENT_ENTRY_DATA_ERRORS = (
    "MEME_GATE_FRESH_FRAME_UNAVAILABLE",
    "BLOCKED_ENTRY_METHOD_FRAME_UNAVAILABLE",
    "BLOCKED_ENTRY_METHOD_SHORT_LIVE_CANDLE_UNAVAILABLE",
    "BLOCKED_ENTRY_METHOD_SHORT_LIVE_CANDLE_NOT_RED",
    "BLOCKED_ENTRY_METHOD_LIVE_CANDLE_UNAVAILABLE",
    "BLOCKED_ENTRY_METHOD_LONG_LIVE_CANDLE_NOT_GREEN",
)


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
    全域真峰谷唯一驗證裁決官 (波段狀態機核心 - 正宗三點樞紐與外軌轉折):
    - is_true_bottom (空單真谷底獲利了結 / 多單起漲):
      1. MA3 向上金叉 MA15 (ma3_curr >= ma15_curr and ma3_prev <= ma15_prev)
      2. 底部三點樞紐確認: 探底後收紅陽線，突破前棒實體中點，收盤站上 MA3 且 MA3 上拐 (curr_close >= ma3_curr and ma3_curr > ma3_prev)
      3. 下軌強勢陽吞噬: 探至下軌或軌外後收飽滿紅陽線 (curr_body >= 0.5 * prev_body) 且站上 MA3
      4. 低位天地針插針: 下影線 >= 1.5 * 實體且 >= 0.30 ATR，探底觸及下軌，收盤站上 MA3
      5. 單根爆發大陽線: 實體 >= 1.2 ATR 且收陽站上 MA3
    - is_true_peak (多單真峰頂獲利了結 / 空單起跌):
      1. MA3 向下死叉 MA15 (ma3_curr <= ma15_curr and ma3_prev >= ma15_prev)
      2. 頂部三點樞紐確認: 衝頂後收黑陰線，跌破前棒實體中點，收盤跌破 MA3 且 MA3 下拐 (curr_close <= ma3_curr and ma3_curr < ma3_prev)
      3. 上軌強勢陰吞噬: 衝至上軌或軌外後收飽滿黑陰線 (curr_body >= 0.5 * prev_body) 且跌破 MA3
      4. 高位避雷針插針: 上影線 >= 1.5 * 實體且 >= 0.30 ATR，衝頂觸及上軌，收盤跌破 MA3
      5. 單根崩盤大陰線: 實體 >= 1.2 ATR 且收陰跌破 MA3
    """
    curr_close = float(curr_k['close'])
    curr_open = float(curr_k['open'])
    curr_high = float(curr_k.get('high', max(curr_open, curr_close)))
    curr_low = float(curr_k.get('low', min(curr_open, curr_close)))
    curr_body = abs(curr_close - curr_open)
    prev_close = float(prev_k['close'])
    prev_open = float(prev_k['open'])
    prev_high = float(prev_k.get('high', max(prev_open, prev_close)))
    prev_low = float(prev_k.get('low', min(prev_open, prev_close)))
    prev_body = abs(prev_close - prev_open)
    upper_wick = curr_high - max(curr_open, curr_close)
    lower_wick = min(curr_open, curr_close) - curr_low
    atr_val = float(current_atr or 0.0)

    # 🟢 1. 空頭持倉真谷底獲利平倉 / 多單起漲判定
    is_ma3_golden_cross = (ma3_curr >= ma15_curr) and (ma3_prev <= ma15_prev)
    prev_mid_pt = (prev_open + prev_close) / 2.0
    is_three_point_valley = (
        curr_close > curr_open
        and curr_close >= ma3_curr
        and ma3_curr > ma3_prev
        and (curr_close >= prev_mid_pt or curr_close > prev_high)
        and (prev_close <= prev_open or prev_low <= kc_lower * 1.002 or curr_low <= kc_lower * 1.002)
    )
    is_engulfing_bottom = (
        (prev_low <= kc_lower * 1.002 or prev_close < prev_kc_lower or curr_low <= kc_lower * 1.002)
        and (curr_close > curr_open)
        and (curr_body >= 0.5 * max(prev_body, 1e-12) or curr_body >= 0.35 * atr_val)
        and (curr_close >= ma3_curr)
        and (ma3_curr >= ma3_prev)
    )
    is_pinbar_bottom = (
        (lower_wick >= 1.5 * max(curr_body, 1e-12))
        and (lower_wick >= 0.30 * atr_val)
        and (curr_low <= kc_lower * 1.002)
        and (curr_close >= ma3_curr)
    )
    is_spike_bottom = (curr_body >= 1.2 * atr_val) and (curr_close > curr_open) and (curr_close >= ma3_curr)

    is_true_bottom = bool(is_ma3_golden_cross or is_three_point_valley or is_engulfing_bottom or is_pinbar_bottom or is_spike_bottom)

    # 🟢 2. 多頭持倉真峰頂獲利平倉 / 空單起跌判定
    is_ma3_dead_cross = (ma3_curr <= ma15_curr) and (ma3_prev >= ma15_prev)
    is_three_point_peak = (
        curr_close < curr_open
        and curr_close <= ma3_curr
        and ma3_curr < ma3_prev
        and (curr_close <= prev_mid_pt or curr_close < prev_low)
        and (prev_close >= prev_open or prev_high >= kc_upper * 0.998 or curr_high >= kc_upper * 0.998)
    )
    is_engulfing_peak = (
        (prev_high >= kc_upper * 0.998 or prev_close > prev_kc_upper or curr_high >= kc_upper * 0.998)
        and (curr_close < curr_open)
        and (curr_body >= 0.5 * max(prev_body, 1e-12) or curr_body >= 0.35 * atr_val)
        and (curr_close <= ma3_curr)
        and (ma3_curr <= ma3_prev)
    )
    is_pinbar_peak = (
        (upper_wick >= 1.5 * max(curr_body, 1e-12))
        and (upper_wick >= 0.30 * atr_val)
        and (curr_high >= kc_upper * 0.998)
        and (curr_close <= ma3_curr)
    )
    is_spike_peak = (curr_body >= 1.2 * atr_val) and (curr_close < curr_open) and (curr_close <= ma3_curr)

    is_true_peak = bool(is_ma3_dead_cross or is_three_point_peak or is_engulfing_peak or is_pinbar_peak or is_spike_peak)

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

    # 持倉呼吸防洗鐵律: 未實質展開波段 (gain < 0.5 ATR 且 ROE < 5%)，
    # 僅在中軌同向守穩且未發生趨勢反轉時續抱；若中軌已反向轉折 (多單中軌轉跌 kc_mid_curr < kc_mid_prev)，嚴禁死扛！
    if position:
        entry_p = float(position.get("entry_price") or 0.0)
        peak_roe = float(position.get("peak_roe") or 0.0)
        atr_threshold = 0.5 * atr_val
        if entry_p > 0:
            high_p = float(position.get("highest_price") or position.get("peak_price") or entry_p)
            gain = high_p - entry_p
            # 只有在大趨勢與中軌依然同向向上時才判定為呼吸回踩；中軌轉跌或破位時不阻斷平倉
            if gain < atr_threshold and peak_roe < 5.0 and (kc_mid_curr >= kc_mid_prev):
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

    # 持倉呼吸防洗鐵律: 未實質展開波段 (drop < 0.5 ATR 且 ROE < 5%)，
    # 僅在中軌同向守穩且未發生趨勢反轉時續抱；若中軌已反向轉折 (空單中軌轉漲 kc_mid_curr > kc_mid_prev)，嚴禁死扛！
    if position:
        entry_p = float(position.get("entry_price") or 0.0)
        peak_roe = float(position.get("peak_roe") or 0.0)
        atr_threshold = 0.5 * atr_val
        if entry_p > 0:
            low_p = float(position.get("lowest_price") or position.get("trough_price") or entry_p)
            drop = entry_p - low_p
            # 只有在大趨勢與中軌依然同向向下時才判定為呼吸回踩；中軌轉漲或破位時不阻斷平倉
            if drop < atr_threshold and peak_roe < 5.0 and (kc_mid_curr <= kc_mid_prev):
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
    _symbol_macro_trend: Dict[str, str] = {}
    _symbol_key_pivot: Dict[str, float] = {}
    _position_flip_locks: Dict[str, asyncio.Lock] = {}
    _ma5_angle_locks: Dict[str, asyncio.Lock] = {}
    _pv_exit_records: Dict[str, Dict[str, Any]] = {}
    _pv_flip_records: Dict[str, Dict[str, Any]] = {}
    _pv_flip_watch_records: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def _save_dual_trend_reversal_ticket_state(cls, account: Any, symbol: str) -> bool:
        save_state = getattr(account, "save_state", None)
        if not callable(save_state):
            logger.error(
                "DUAL_TREND_REVERSAL_TICKET_PERSISTENCE_UNAVAILABLE symbol=%s",
                symbol,
            )
            return False
        try:
            try:
                save_state(strict=True)
            except TypeError:
                save_state()
        except Exception as exc:
            logger.error(
                "DUAL_TREND_REVERSAL_TICKET_PERSISTENCE_FAILED symbol=%s error=%s",
                symbol, exc,
            )
            return False
        return True

    @classmethod
    def _store_dual_trend_reversal_ticket(
        cls,
        account: Any,
        symbol: str,
        position: Dict[str, Any],
        position_meta: Optional[Dict[str, Any]],
        exit_reason: str,
    ) -> bool:
        state = getattr(account, "entry_filter_last", None)
        if not isinstance(state, dict):
            state = {}
            account.entry_filter_last = state
        tickets = state.get("dual_trend_reversal_pending")
        if not isinstance(tickets, dict):
            tickets = {}
            state["dual_trend_reversal_pending"] = tickets

        def finite_number(value: Any) -> Optional[float]:
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                return None
            return number if math.isfinite(number) else None

        tickets[symbol] = {
            "source_position": {
                "side": str(position.get("side") or "").upper(),
                "open_timestamp": finite_number(
                    position.get("open_timestamp")
                    if position.get("open_timestamp") is not None
                    else (position_meta or {}).get("open_timestamp")
                ),
                "entry_price": finite_number(position.get("entry_price")),
                "qty": finite_number(position.get("qty", position.get("amount"))),
            },
            "source_position_meta": {
                "open_timestamp": finite_number(
                    (position_meta or {}).get("open_timestamp")
                ),
            },
            "exit_reason": str(exit_reason),
            "created_at": time.time(),
        }
        return cls._save_dual_trend_reversal_ticket_state(account, symbol)

    @classmethod
    def _clear_dual_trend_reversal_ticket(cls, account: Any, symbol: str) -> bool:
        state = getattr(account, "entry_filter_last", None)
        tickets = state.get("dual_trend_reversal_pending") if isinstance(state, dict) else None
        if not isinstance(tickets, dict) or symbol not in tickets:
            return True
        tickets.pop(symbol, None)
        if not tickets:
            state.pop("dual_trend_reversal_pending", None)
        return cls._save_dual_trend_reversal_ticket_state(account, symbol)

    @classmethod
    async def _attempt_priority_exit_reversal(
        cls,
        *,
        symbol: str,
        account: Any,
        engine_or_account: Any,
        frame_1m: Any,
        quote: float,
        position: Dict[str, Any],
        exit_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        reason = str(exit_result.get("reason") or "")
        priority = exit_result.get("priority")
        if not (
            priority in (0, 2)
            or reason == "PRIORITY_EXIT_P3_MA5_DIRECTION_REVERSAL"
        ):
            return {"status": "NOT_APPLICABLE"}

        old_side = str(position.get("side") or "").upper()
        target_side = "SHORT" if old_side == "LONG" else "LONG"
        positions = getattr(account, "positions", None)
        if not isinstance(positions, dict) or symbol in positions:
            return {
                "status": "BLOCKED",
                "reason": "REVERSAL_REQUIRES_CONFIRMED_FLAT",
            }

        daily_loss_limit = getattr(account, "daily_loss_limit_hit", None)
        daily_halt = False
        if callable(daily_loss_limit):
            try:
                risk_result = daily_loss_limit()
                daily_halt = bool(
                    risk_result[0]
                    if isinstance(risk_result, (tuple, list)) else risk_result
                )
            except Exception:
                logger.exception(
                    "PRIORITY_EXIT_REVERSAL_DAILY_RISK_CHECK_FAILED symbol=%s",
                    symbol,
                )
                daily_halt = True
        if daily_halt:
            return {
                "status": "BLOCKED",
                "reason": "REVERSAL_BLOCKED_BY_DAILY_RISK",
            }

        from core.services.entry_contract import evaluate_entry_contract

        diagnostics: Dict[str, Any] = {}
        candidate = evaluate_entry_contract(
            frame_1m,
            quote,
            account=account,
            symbol=symbol,
            diagnostics=diagnostics,
        )
        if not isinstance(candidate, dict) or candidate.get("side") != target_side:
            return {
                "status": "BLOCKED",
                "reason": str(
                    diagnostics.get("reason")
                    or "NO_FRESH_OPPOSITE_ENTRY_AUTHORITY"
                ),
            }

        execute_entry = getattr(
            engine_or_account, "_execute_confirmed_channel_break", None,
        )
        if not callable(execute_entry):
            logger.error(
                "PRIORITY_EXIT_REVERSAL_ENTRY_EXECUTOR_UNAVAILABLE symbol=%s",
                symbol,
            )
            return {
                "status": "BLOCKED",
                "reason": "REVERSAL_ENTRY_EXECUTOR_UNAVAILABLE",
            }

        opened = await execute_entry(
            symbol,
            frame_1m,
            quote,
            target_side,
            daily_halt=daily_halt,
            v8_reason=candidate.get("type"),
            candidate_bar_id=candidate.get("confirmation_bar_id"),
        )
        if not opened:
            return {
                "status": "BLOCKED",
                "reason": "REVERSAL_ENTRY_FINAL_GATE_VETO",
            }
        new_position = positions.get(symbol)
        if (
            not isinstance(new_position, dict)
            or str(new_position.get("side") or "").upper() != target_side
        ):
            logger.critical(
                "PRIORITY_EXIT_REVERSAL_OPEN_UNCONFIRMED symbol=%s target=%s",
                symbol, target_side,
            )
            return {
                "status": "UNKNOWN",
                "reason": "REVERSAL_OPEN_REQUIRES_ACCOUNT_RECONCILIATION",
            }
        return {"status": "REVERSED", "side": target_side}

    @classmethod
    def _confirmed_dual_trend_exit_candidate(
        cls,
        position: Dict[str, Any],
        metadata: Optional[Dict[str, Any]],
        frame: Optional[pd.DataFrame],
    ) -> Optional[str]:
        from core.services.exits.authorized_position_exit import dual_trend_exit_block_reason

        block_reason = dual_trend_exit_block_reason(position, frame, metadata)
        if block_reason:
            return None
        return "ACTIVE_EXIT_DUAL_TREND_REVERSAL"

    @classmethod
    async def _evaluate_closed_entry_methods(
        cls,
        *,
        symbol: str,
        account: Any,
        engine_or_account: Any,
        frame: Optional[pd.DataFrame],
        quote: float,
        macro_trend: str,
        only_method: Optional[str] = None,
        exclude_methods: Optional[set[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return None

        from core.services.entry_methods import (
            KC_TWO_BAR_BREAKOUT,
            KC_EARLY_MIDLINE_BREAK,
            MEME_GATE_ENTRY,
            KC_BEARISH_PULLBACK_RESUMPTION,
            KC_OUTER_PIVOT_REVERSAL,
            MA5_MA15_CROSS,
            KC_SINGLE_BAR_BREAKOUT,
            KC_MIDLINE_MOMENTUM,
            KC_DUAL_TREND_LOCATION,
            POST_CLOSE_KC_REENTRY,
            THREE_BAR_PIVOT_REVERSAL,
            evaluate_entry_method,
            revalidate_entry_method,
        )

        account_key = cls._get_account_key(account, symbol)
        last_exit = cls._last_exit_record.get(account_key)
        if last_exit is None:
            last_exit = cls._last_exit_record.get(symbol)
        last_exit_timestamp = (
            float(last_exit["timestamp"])
            if isinstance(last_exit, dict) and last_exit.get("timestamp") is not None
            else None
        )
        # 多空對稱：只要冷卻期滿且出現結構反轉訊號，允許所有核心方法（含普通破軌、峰谷轉折與交叉）
        methods = (
            (only_method,) if only_method is not None else
            tuple(
                m for m in (
                    KC_BEARISH_PULLBACK_RESUMPTION,
                    MEME_GATE_ENTRY,
                    KC_DUAL_TREND_LOCATION,
                    KC_OUTER_PIVOT_REVERSAL,
                    KC_TWO_BAR_BREAKOUT,
                    THREE_BAR_PIVOT_REVERSAL,
                    MA5_MA15_CROSS,
                    KC_SINGLE_BAR_BREAKOUT,
                    KC_MIDLINE_MOMENTUM,
                    POST_CLOSE_KC_REENTRY,
                )
                if not exclude_methods or m not in exclude_methods
            )
        )

        candidates: list[tuple[dict[str, Any], str]] = []
        rejections: list[dict[str, str]] = []
        authorized: tuple[dict[str, Any], str] | None = None
        for method in methods:
            method_last_exit = last_exit_timestamp
            persisted_exit = getattr(account, "last_closed_at", {}).get(symbol)
            if persisted_exit is not None:
                try:
                    persisted_exit = float(persisted_exit)
                    if math.isfinite(persisted_exit) and persisted_exit > 0:
                        method_last_exit = max(method_last_exit or 0, persisted_exit)
                except (TypeError, ValueError, OverflowError):
                    pass
            signal = evaluate_entry_method(
                frame,
                quote=quote,
                symbol=symbol,
                last_exit_timestamp=method_last_exit,
                expected_method=method,
            )
            if signal is None:
                continue
            candidates.append((signal, method))
            allowed, reason = revalidate_entry_method(
                frame,
                symbol=symbol,
                side=signal["side"],
                quote=quote,
                context={
                    "entry_method_authority": method,
                    "entry_method_signal": signal,
                    "entry_method_bar_id": signal["confirmation_bar_id"],
                    "entry_method_event_id": signal["event_id"],
                    "entry_method_last_exit_timestamp": method_last_exit,
                },
            )
            log_decision = logger.info if allowed else logger.warning
            log_decision(
                "ENTRY_METHOD_GATE symbol=%s side=%s method=%s allowed=%s reason=%s event=%s",
                symbol, signal["side"], method, allowed, reason, signal["event_id"],
            )
            if allowed:
                authorized = (signal, method)
                break
            rejections.append({
                "method": method,
                "side": signal["side"],
                "reason": reason,
                "event_id": signal["event_id"],
            })

        if not candidates:
            return None
        if authorized is None:
            return {
                "status": "VETO",
                "symbol": symbol,
                "reason": "BLOCKED_ENTRY_METHOD_MACRO_GATE",
                "entry_rejections": rejections,
            }

        signal, method = authorized
        last_exit_timestamp = signal.get("last_exit_timestamp")
        bar_id = int(signal["confirmation_bar_id"])
        entry_state = getattr(account, "entry_filter_last", None)
        if not isinstance(entry_state, dict):
            entry_state = {}
            account.entry_filter_last = entry_state
        persisted = entry_state.get("strict_ma3_ma15_last_trade_bar", {})
        if not isinstance(persisted, dict):
            persisted = {}
        last_used = persisted.get(symbol) or cls._last_trade_bar_time.get(symbol)
        if last_used is not None and int(float(last_used)) == bar_id:
            return {
                "status": "VETO",
                "symbol": symbol,
                "reason": "ENTRY_METHOD_BAR_ALREADY_TRADED",
            }

        entry_history = [
            stamp for stamp in cls._hourly_entry_records.get(
                account_key, cls._hourly_entry_records.get(symbol, []),
            )
            if time.time() - stamp < 3600.0
        ]
        if len(entry_history) >= cls.MAX_ENTRIES_PER_HOUR:
            return {
                "status": "VETO",
                "symbol": symbol,
                "reason": "BLOCKED_HOURLY_ENTRY_LIMIT_EXCEEDED",
            }

        previous_persisted_claim = persisted.get(symbol)
        persisted[symbol] = bar_id
        entry_state["strict_ma3_ma15_last_trade_bar"] = persisted
        previous_class_claim = cls._last_trade_bar_time.get(symbol)
        cls._last_trade_bar_time[symbol] = bar_id
        save_state = getattr(account, "save_state", None)
        if not callable(save_state):
            return {
                "status": "VETO",
                "symbol": symbol,
                "reason": "ENTRY_METHOD_BAR_CLAIM_STORE_UNAVAILABLE",
            }
        try:
            try:
                save_state(strict=True)
            except TypeError:
                save_state()
        except Exception as exc:
            logger.error(
                "ENTRY_METHOD_BAR_CLAIM_PERSISTENCE_FAILED symbol=%s error=%s",
                symbol, exc,
            )
            return {
                "status": "VETO",
                "symbol": symbol,
                "reason": "ENTRY_METHOD_BAR_CLAIM_PERSISTENCE_FAILED",
            }

        method_context = {
            "entry_method_authority": method,
            "entry_method_signal": signal,
            "entry_method_bar_id": bar_id,
            "entry_method_event_id": signal["event_id"],
            "entry_method_last_exit_timestamp": last_exit_timestamp,
        }
        meta = {
            **method_context,
            "side": signal["side"],
            "entry_type": method,
            "entry_pattern": signal["entry_pattern"],
            "structure_confirmation": signal["structure_confirmation"],
            "structure_confirmed": True,
            "channel_expanding": signal["channel_expanding"],
            "candidate_signal_type": f"ENTRY_METHOD_{method}_{signal['side']}",
            "signal_id": signal["event_id"],
            "bar_timestamp": bar_id,
            "channel_confirmation_bar_id": bar_id,
            "raw_df": frame,
            "atr": signal["entry_atr"],
            "macro_trend": str(macro_trend or "NEUTRAL").upper(),
            "source": f"UNIFIED_PIPELINE|ENTRY_METHOD|{method}",
            "entry_mode": "WILD_SWING",
            "direction_override": False,
            "no_rigid_sl": True,
        }
        cls._last_open_error.pop(symbol, None)
        success = await cls.open_position(
            engine_or_account=engine_or_account,
            symbol=symbol,
            side=signal["side"],
            price=quote,
            reason=f"EntryMethod:{method}",
            meta=meta,
        )
        open_error = cls._last_open_error.pop(symbol, "")
        if not success and (
            method == MEME_GATE_ENTRY
            or any(marker in open_error for marker in TRANSIENT_ENTRY_DATA_ERRORS)
        ):
            # MEME_GATE_ENTRY vetoes (e.g. extension/revalidation) and transient data
            # unavailability did not place an order; free the bar claim so valid
            # channels or retries on the same bar are not wrongly locked out.
            if persisted.get(symbol) == bar_id:
                if previous_persisted_claim is None:
                    persisted.pop(symbol, None)
                else:
                    persisted[symbol] = previous_persisted_claim
            if cls._last_trade_bar_time.get(symbol) == bar_id:
                if previous_class_claim is None:
                    cls._last_trade_bar_time.pop(symbol, None)
                else:
                    cls._last_trade_bar_time[symbol] = previous_class_claim
            try:
                try:
                    save_state(strict=True)
                except TypeError:
                    save_state()
            except Exception as exc:
                logger.error("ENTRY_METHOD_BAR_CLAIM_RELEASE_FAILED symbol=%s error=%s", symbol, exc)
            logger.warning(
                "ENTRY_METHOD_BAR_CLAIM_RELEASED symbol=%s bar=%s error=%s", symbol, bar_id, open_error,
            )
        return {
            "status": "ENTRY_EXECUTED" if success else "VETO",
            "symbol": symbol,
            "side": signal["side"],
            "reason": method if success else "ENTRY_METHOD_FINAL_GATE_VETO",
            "success": success,
            "entry_method": method,
            "event_id": signal["event_id"],
        }

    @classmethod
    async def _process_mtf_kc_state_machine(
        cls,
        *,
        symbol: str,
        account: Any,
        engine_or_account: Any,
        frame_1m: Any,
        frame_15m: Any,
        quote: float,
        position: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        from core.symbol_aliases import is_meme_breakout_symbol
        from core.services.mtf_kc_state_machine import (
            MTF_KC_EXIT_REASON_PREFIX,
            MTF_KC_STATE_MACHINE,
            authorize_mtf_exit,
            close_in_current_live_1m_bar,
            evaluate_htf_kc_state,
            evaluate_ltf_kc_midline_direction,
            evaluate_mtf_exit_confirmation,
            evaluate_mtf_entry,
            is_mtf_managed_position,
            mtf_exit_reason_allowed,
            mtf_cycle_reverse_reentry_ticket_valid,
            update_mtf_position_protection,
        )

        is_meme_symbol = is_meme_breakout_symbol(symbol)
        if not is_meme_symbol and not position:
            return None

        metadata = getattr(account, "position_meta", {}).get(symbol, {})
        if not isinstance(metadata, dict):
            metadata = {}
        if position is not None:
            from core.services.exits.priority_exit_manager import (
                evaluate_and_execute_exit,
            )

            priority_exit = await evaluate_and_execute_exit(
                account,
                symbol,
                position,
                frame_1m,
                frame_15m,
                quote,
                cls.close_position,
            )
            if priority_exit is not None:
                if priority_exit.get("success"):
                    reversal = await cls._attempt_priority_exit_reversal(
                        symbol=symbol,
                        account=account,
                        engine_or_account=engine_or_account,
                        frame_1m=frame_1m,
                        quote=quote,
                        position=position,
                        exit_result=priority_exit,
                    )
                    priority_exit["reversal"] = reversal
                    if reversal.get("status") == "REVERSED":
                        priority_exit["status"] = "EXIT_AND_REVERSE_EXECUTED"
                        priority_exit["reversed_side"] = reversal["side"]
                return priority_exit
        managed_position = bool(
            position and is_mtf_managed_position(position, metadata)
        )
        if managed_position and position is not None:
            from core.services.exits.abnormal_candle_exit import (
                live_adverse_waterfall_evidence,
            )

            waterfall_evidence = live_adverse_waterfall_evidence(
                position, frame_1m, quote,
            )
            if waterfall_evidence is not None:
                waterfall_reason = "EXIT_BY_EXTREME_WATERFALL"
                if not authorize_mtf_exit(
                    account, symbol, waterfall_reason, quote, waterfall_evidence,
                ):
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": "BLOCKED_MTF_EXIT_AUTHORITY_PERSISTENCE",
                        "success": False,
                    }
                closed = await cls.close_position(
                    account,
                    symbol,
                    quote,
                    waterfall_reason,
                    meta={"raw_df": frame_1m, "is_bar_closed": False},
                )
                if closed:
                    entry_state = getattr(account, "entry_filter_last", None)
                    if not isinstance(entry_state, dict):
                        entry_state = {}
                        account.entry_filter_last = entry_state
                    reverse_tickets = entry_state.setdefault(
                        "mtf_cycle_reverse_pending", {},
                    )
                    if isinstance(reverse_tickets, dict):
                        target_side = "SHORT" if side == "LONG" else "LONG"
                        bar_id = None
                        try:
                            bar_id = int(float(frame_1m.iloc[-1]["timestamp"]))
                        except Exception:
                            pass
                        reverse_tickets[symbol] = {
                            "source_side": side,
                            "target_side": target_side,
                            "reason": waterfall_reason,
                            "close_timestamp": getattr(account, "last_closed_at", {}).get(symbol),
                            "bar_id": bar_id,
                        }
                        save_state = getattr(account, "save_state", None)
                        if callable(save_state):
                            try:
                                save_state()
                            except Exception:
                                pass
                return {
                    "status": "EXIT_EXECUTED" if closed else "HOLD",
                    "symbol": symbol,
                    "reason": waterfall_reason,
                    "success": bool(closed),
                }
        htf = (
            evaluate_htf_kc_state(frame_15m)
            if frame_15m is not None
            else {"valid": False, "reason": "BLOCKED_HTF_KC_FRAME_UNAVAILABLE"}
        )

        def persist_exit_confirmation() -> bool:
            save_state = getattr(account, "save_state", None)
            if not callable(save_state):
                return False
            try:
                try:
                    save_state(strict=True)
                except TypeError:
                    save_state()
            except Exception as exc:
                logger.error(
                    "MTF_EXIT_CONFIRMATION_PERSIST_FAILED symbol=%s error=%s",
                    symbol, exc,
                )
                return False
            return True

        if position is not None and htf.get("valid"):
            side = str(position.get("side") or "").upper()
            state = str(htf.get("state") or "").upper()
            opposite_state = (
                side == "SHORT" and state == "UPTREND"
                or side == "LONG" and state == "DOWNTREND"
            )
            if opposite_state:
                expected_ltf_reversal = (
                    "DOWNTREND" if side == "LONG" else "UPTREND"
                )
                ltf_direction = evaluate_ltf_kc_midline_direction(frame_1m)
                if (
                    not ltf_direction.get("valid")
                    or ltf_direction.get("direction") != expected_ltf_reversal
                ):
                    logger.info(
                        "MTF_EXIT_HELD_LTF_MIDLINE_UNCONFIRMED symbol=%s side=%s "
                        "htf_state=%s ltf_direction=%s required=%s",
                        symbol, side, state, ltf_direction.get("direction"),
                        expected_ltf_reversal,
                    )
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": "VETO_MTF_LTF_MIDLINE_DIRECTION_UNCONFIRMED",
                        "success": False,
                    }
                exit_reason = (
                    f"{MTF_KC_EXIT_REASON_PREFIX}{state}_REVERSAL_EXIT"
                )
                metadata.pop("mtf_pending_closed_bar_exit", None)
                exit_evidence = {
                    **htf,
                    "valid": True,
                    "state": state,
                    "forced_state_mismatch": True,
                }
                if not authorize_mtf_exit(
                    account,
                    symbol,
                    exit_reason,
                    quote,
                    exit_evidence,
                    allow_direction_mismatch=True,
                ):
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": "BLOCKED_KC_STATE_MISMATCH_AUTHORITY",
                        "success": False,
                    }
                closed = await cls.close_position(
                    account,
                    symbol,
                    quote,
                    exit_reason,
                    meta={"raw_df": frame_1m, "is_bar_closed": False},
                )
                return {
                    "status": "EXIT_EXECUTED" if closed else "HOLD",
                    "symbol": symbol,
                    "reason": exit_reason,
                    "success": bool(closed),
                }

        if not htf.get("valid"):
            if managed_position and position is not None:
                protection = update_mtf_position_protection(
                    position, metadata, quote,
                )
                protection_reason = protection.get("reason") if protection else None
                if protection_reason and str(protection_reason).startswith("BLOCKED_"):
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": protection_reason,
                        "success": False,
                    }
                if (
                    not protection_reason
                    or not mtf_exit_reason_allowed(protection_reason)
                    or str(protection_reason).startswith(MTF_KC_EXIT_REASON_PREFIX)
                ):
                    logger.warning(
                        "MTF_POSITION_HTF_DATA_UNAVAILABLE symbol=%s reason=%s",
                        symbol, htf.get("reason"),
                    )
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": htf.get("reason"),
                        "success": False,
                    }
                exit_decision = evaluate_mtf_exit_confirmation(
                    frame_1m,
                    position,
                    metadata,
                    protection_reason,
                    protection or {},
                )
                if (
                    exit_decision.get("changed")
                    and not exit_decision.get("allowed")
                    and not persist_exit_confirmation()
                ):
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": "BLOCKED_MTF_EXIT_CONFIRMATION_PERSISTENCE",
                        "success": False,
                    }
                if exit_decision.get("allowed"):
                    protection_reason = str(exit_decision["reason"])
                    if not authorize_mtf_exit(
                        account, symbol, protection_reason, quote,
                        exit_decision.get("evidence") or {},
                    ):
                        return {
                            "status": "HOLD",
                            "symbol": symbol,
                            "reason": "BLOCKED_MTF_EXIT_AUTHORITY_PERSISTENCE",
                            "success": False,
                        }
                    closed = await cls.close_position(
                        account,
                        symbol,
                        quote,
                        protection_reason,
                        meta={"raw_df": frame_1m, "is_bar_closed": False},
                    )
                    return {
                        "status": "EXIT_EXECUTED" if closed else "HOLD",
                        "symbol": symbol,
                        "reason": protection_reason,
                        "success": bool(closed),
                    }
                if exit_decision.get("waiting"):
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": exit_decision.get("reason"),
                        "success": False,
                    }
                logger.warning(
                    "MTF_POSITION_HTF_DATA_UNAVAILABLE symbol=%s reason=%s",
                    symbol, htf.get("reason"),
                )
                return {
                    "status": "HOLD",
                    "symbol": symbol,
                    "reason": htf.get("reason"),
                    "success": False,
                }
            if position or frame_15m is None:
                return None
            return {
                "status": "VETO",
                "symbol": symbol,
                "reason": htf.get("reason"),
                "success": False,
            }
        if position and not managed_position:
            return None

        side = str(position.get("side") or "").upper() if position else ""
        current_bar_id = None
        allow_immediate_mtf_reverse = False
        if isinstance(frame_1m, pd.DataFrame) and not frame_1m.empty:
            try:
                current_bar_id = int(float(frame_1m.iloc[-1]["timestamp"]))
            except (KeyError, TypeError, ValueError, OverflowError):
                current_bar_id = None
        if managed_position:
            metadata["mtf_kc_state"] = htf.get("closed_trend", "FLAT")
            metadata["mtf_kc_live_state"] = htf["state"]
            metadata["mtf_normalized_slope"] = htf["normalized_slope"]
            metadata["mtf_kc_lower_15m"] = htf["kc_lower"]
            metadata["mtf_kc_upper_15m"] = htf["kc_upper"]
            position["mtf_kc_lower_15m"] = htf["kc_lower"]
            position["mtf_kc_upper_15m"] = htf["kc_upper"]

            protection = update_mtf_position_protection(position, metadata, quote)
            exit_reason = protection.get("reason") if protection else None
            exit_evidence = protection or {}
            reverse_after_close = False
            if exit_reason and str(exit_reason).startswith("BLOCKED_"):
                return {
                    "status": "HOLD",
                    "symbol": symbol,
                    "reason": exit_reason,
                    "success": False,
                }
            if not exit_reason:
                from core.services.mtf_kc_state_machine import (
                    _detect_ltf_outer_band_turn_state,
                    _valid_live_frame,
                )
                ltf_pair = _valid_live_frame(frame_1m, 60_000.0)
                ltf_turn = (
                    _detect_ltf_outer_band_turn_state(ltf_pair[0])
                    if ltf_pair is not None
                    else None
                )
                closed_direction = htf.get("closed_trend")
                reversed_direction = "DOWNTREND" if side == "LONG" else "UPTREND"
                if closed_direction == reversed_direction:
                    ltf_direction = evaluate_ltf_kc_midline_direction(frame_1m)
                    if (
                        not ltf_direction.get("valid")
                        or ltf_direction.get("direction") != reversed_direction
                    ):
                        logger.info(
                            "MTF_EXIT_HELD_LTF_MIDLINE_UNCONFIRMED symbol=%s "
                            "side=%s htf_closed_direction=%s ltf_direction=%s",
                            symbol, side, closed_direction,
                            ltf_direction.get("direction"),
                        )
                        return {
                            "status": "HOLD",
                            "symbol": symbol,
                            "reason": "VETO_MTF_LTF_MIDLINE_DIRECTION_UNCONFIRMED",
                            "success": False,
                        }
                    exit_reason = (
                        f"{MTF_KC_EXIT_REASON_PREFIX}"
                        f"{reversed_direction}_REVERSAL_EXIT"
                    )
                    exit_evidence = {
                        **htf,
                        "macro_state": htf["state"],
                        "closed_midline_direction": closed_direction,
                    }
                    reverse_after_close = False
                else:
                    pending_exit = metadata.pop(
                        "mtf_pending_closed_bar_exit", None,
                    )
                    if (
                        pending_exit is not None
                        and not persist_exit_confirmation()
                    ):
                        metadata["mtf_pending_closed_bar_exit"] = pending_exit
                        return {
                            "status": "HOLD",
                            "symbol": symbol,
                            "reason": "BLOCKED_MTF_EXIT_CONFIRMATION_PERSISTENCE",
                            "success": False,
                        }
                    if closed_direction == ("UPTREND" if side == "LONG" else "DOWNTREND"):
                        hold_reason = "MTF_KC_CYCLE_CONTINUES"
                    else:
                        hold_reason = "MTF_KC_REVERSAL_NOT_CONFIRMED"
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": hold_reason,
                        "success": False,
                    }

            exit_decision = evaluate_mtf_exit_confirmation(
                frame_1m,
                position,
                metadata,
                exit_reason,
                exit_evidence,
            )
            if not exit_decision.get("allowed"):
                if exit_decision.get("changed") and not persist_exit_confirmation():
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": "BLOCKED_MTF_EXIT_CONFIRMATION_PERSISTENCE",
                        "success": False,
                    }
                if exit_decision.get("waiting"):
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": exit_decision.get("reason"),
                        "success": False,
                    }
                logger.info(
                    "[大週期狀態: %s] | [標準化斜率: %.4f] | [持倉: %s] | [決策: HOLD]",
                    htf["state"], htf["normalized_slope"], side,
                )
                save_state = getattr(account, "save_state", None)
                if callable(save_state):
                    save_state()
                return {"status": "HOLD", "symbol": symbol, "reason": "MTF_TREND_HOLD"}

            exit_reason = str(exit_decision["reason"])
            exit_evidence = exit_decision.get("evidence") or {}
            if not authorize_mtf_exit(
                account, symbol, str(exit_reason), quote, exit_evidence,
            ):
                return {
                    "status": "HOLD",
                    "symbol": symbol,
                    "reason": "BLOCKED_MTF_EXIT_AUTHORITY_PERSISTENCE",
                    "success": False,
                }
            closed = await cls.close_position(
                account,
                symbol,
                quote,
                str(exit_reason),
                meta={"raw_df": frame_1m, "is_bar_closed": False},
            )
            if not closed:
                return {
                    "status": "HOLD",
                    "symbol": symbol,
                    "reason": exit_reason,
                    "success": False,
                }
            logger.info(
                "[大週期狀態: %s] | [標準化斜率: %.4f] | [平倉: %s] | [決策: CLOSE]",
                htf["state"], htf["normalized_slope"], exit_reason,
            )
            if not reverse_after_close:
                return {
                    "status": "EXIT_EXECUTED",
                    "symbol": symbol,
                    "reason": exit_reason,
                    "success": True,
                }
            entry_state = getattr(account, "entry_filter_last", None)
            if not isinstance(entry_state, dict):
                entry_state = {}
                account.entry_filter_last = entry_state
            reverse_tickets = entry_state.setdefault(
                "mtf_cycle_reverse_pending", {},
            )
            if not isinstance(reverse_tickets, dict):
                return {
                    "status": "EXIT_EXECUTED",
                    "symbol": symbol,
                    "reason": "BLOCKED_MTF_REVERSE_TICKET_STATE_INVALID",
                    "success": True,
                }
            previous_reverse_ticket = reverse_tickets.get(symbol)
            close_timestamp = getattr(account, "last_closed_at", {}).get(symbol)
            reverse_tickets[symbol] = {
                "source_side": side,
                "target_side": "SHORT" if side == "LONG" else "LONG",
                "reason": exit_reason,
                "close_timestamp": close_timestamp,
                "bar_id": current_bar_id,
            }
            save_state = getattr(account, "save_state", None)
            try:
                if not callable(save_state):
                    raise RuntimeError("account state persistence unavailable")
                try:
                    save_state(strict=True)
                except TypeError:
                    save_state()
            except Exception as exc:
                logger.error(
                    "MTF_REVERSE_TICKET_PERSIST_FAILED symbol=%s error=%s",
                    symbol, exc,
                )
                if previous_reverse_ticket is None:
                    reverse_tickets.pop(symbol, None)
                else:
                    reverse_tickets[symbol] = previous_reverse_ticket
                return {
                    "status": "EXIT_EXECUTED",
                    "symbol": symbol,
                    "reason": "BLOCKED_MTF_REVERSE_TICKET_PERSISTENCE",
                    "success": True,
                }
            allow_immediate_mtf_reverse = mtf_cycle_reverse_reentry_ticket_valid(
                account,
                symbol,
                "SHORT" if side == "LONG" else "LONG",
                frame_1m,
            )
            if not allow_immediate_mtf_reverse:
                return {
                    "status": "EXIT_EXECUTED",
                    "symbol": symbol,
                    "reason": "BLOCKED_MTF_REVERSE_TICKET_INVALID",
                    "success": True,
                }

        from core.services.mtf_kc_state_machine import (
            _detect_ltf_outer_band_turn_state,
            _valid_live_frame,
        )
        ltf_entry_pair = _valid_live_frame(frame_1m, 60_000.0)
        ltf_entry_turn = (
            _detect_ltf_outer_band_turn_state(ltf_entry_pair[0])
            if ltf_entry_pair is not None
            else None
        )
        confirmed_trend = ltf_entry_turn or htf.get("closed_trend")
        preferred_side = (
            "LONG" if confirmed_trend == "UPTREND"
            else "SHORT" if confirmed_trend == "DOWNTREND"
            else (
                "LONG" if htf.get("state") == "UPTREND"
                else "SHORT" if htf.get("state") == "DOWNTREND"
                else None
            )
        )
        account_entry_state = getattr(account, "entry_filter_last", None)
        if not isinstance(account_entry_state, dict):
            account_entry_state = {}
            account.entry_filter_last = account_entry_state
        pending_candidates = account_entry_state.get(
            "mtf_kc_pending_candidates", {},
        )
        if not isinstance(pending_candidates, dict):
            pending_candidates = {}
        account_entry_state["mtf_kc_pending_candidates"] = pending_candidates
        pending_candidate = pending_candidates.get(symbol)
        same_bar_close = close_in_current_live_1m_bar(
            frame_1m, getattr(account, "last_closed_at", {}).get(symbol),
        )
        if not allow_immediate_mtf_reverse and preferred_side:
            allow_immediate_mtf_reverse = mtf_cycle_reverse_reentry_ticket_valid(
                account,
                symbol,
                preferred_side,
                frame_1m,
            )
        if same_bar_close is None:
            return {
                "status": "VETO",
                "symbol": symbol,
                "reason": "BLOCKED_MTF_REENTRY_CLOSE_BAR_ID_INVALID",
                "success": False,
            }
        if same_bar_close and not allow_immediate_mtf_reverse:
            return {
                "status": "HOLD",
                "symbol": symbol,
                "reason": "BLOCKED_MTF_REENTRY_AFTER_CLOSE_SAME_1M_BAR",
                "success": False,
            }
        entry = evaluate_mtf_entry(
            frame_1m,
            frame_15m,
            quote=quote,
            side=preferred_side,
            pending_candidate=None if allow_immediate_mtf_reverse else pending_candidate,
            allow_colorless_reverse=allow_immediate_mtf_reverse,
        )
        decision = "OPEN" if entry.get("allowed") else str(entry.get("reason"))
        logger.info(
            "[大週期狀態: %s] | [標準化斜率: %.4f] | [MA5/15: %s/%s] | "
            "[1m現價/中軌: %.8g/%s] | [最終決策: %s]",
            htf["state"], htf["normalized_slope"],
            entry.get("ma5", "N/A"), entry.get("ma15", "N/A"),
            quote, entry.get("kc_middle_1m", "N/A"), decision,
        )
        if not entry.get("allowed"):
            deferred_candidate = entry.get("deferred_candidate")
            if deferred_candidate is not None:
                if current_bar_id is None:
                    return {
                        "status": "VETO",
                        "symbol": symbol,
                        "reason": "BLOCKED_MTF_DEFERRED_ENTRY_BAR_ID_INVALID",
                        "success": False,
                    }
                previous_candidate = pending_candidates.get(symbol)
                pending_candidates[symbol] = dict(deferred_candidate)
                save_state = getattr(account, "save_state", None)
                if not callable(save_state):
                    if previous_candidate is None:
                        pending_candidates.pop(symbol, None)
                    else:
                        pending_candidates[symbol] = previous_candidate
                    logger.error(
                        "MTF_DEFERRED_ENTRY_STORE_UNAVAILABLE symbol=%s bar=%s",
                        symbol, current_bar_id,
                    )
                    return {
                        "status": "VETO",
                        "symbol": symbol,
                        "reason": "BLOCKED_MTF_DEFERRED_ENTRY_STORE_UNAVAILABLE",
                        "success": False,
                    }
                try:
                    try:
                        save_state(strict=True)
                    except TypeError:
                        save_state()
                except Exception as exc:
                    if previous_candidate is None:
                        pending_candidates.pop(symbol, None)
                    else:
                        pending_candidates[symbol] = previous_candidate
                    logger.error(
                        "MTF_DEFERRED_ENTRY_PERSIST_FAILED symbol=%s bar=%s error=%s",
                        symbol, current_bar_id, exc,
                    )
                    return {
                        "status": "VETO",
                        "symbol": symbol,
                        "reason": "BLOCKED_MTF_DEFERRED_ENTRY_PERSIST_FAILED",
                        "success": False,
                    }
            elif pending_candidate is not None and (
                confirmed_trend not in ("UPTREND", "DOWNTREND")
                or current_bar_id is not None
            ):
                try:
                    source_bar_id = int(float(pending_candidate["source_bar_id"]))
                except (KeyError, TypeError, ValueError, OverflowError):
                    source_bar_id = (
                        current_bar_id - 120_000
                        if current_bar_id is not None
                        else 0
                    )
                if (
                    confirmed_trend not in ("UPTREND", "DOWNTREND")
                    or current_bar_id is not None
                    and current_bar_id > source_bar_id + 60_000
                ):
                    previous_candidate = pending_candidates.pop(symbol, None)
                    save_state = getattr(account, "save_state", None)
                    if callable(save_state):
                        try:
                            try:
                                save_state(strict=True)
                            except TypeError:
                                save_state()
                        except Exception as exc:
                            if previous_candidate is not None:
                                pending_candidates[symbol] = previous_candidate
                            logger.error(
                                "MTF_DEFERRED_ENTRY_EXPIRY_PERSIST_FAILED symbol=%s bar=%s error=%s",
                                symbol, current_bar_id, exc,
                            )
            return {
                "status": "HOLD",
                "symbol": symbol,
                "reason": entry.get("reason"),
                "success": False,
            }

        if current_bar_id is None:
            return {
                "status": "VETO",
                "symbol": symbol,
                "reason": "BLOCKED_MTF_ENTRY_BAR_ID_INVALID",
                "success": False,
            }
        persisted_bars = account_entry_state.get(
            "strict_ma3_ma15_last_trade_bar", {},
        )
        if not isinstance(persisted_bars, dict):
            persisted_bars = {}
        last_used = persisted_bars.get(symbol) or cls._last_trade_bar_time.get(symbol)
        if last_used is not None:
            try:
                if (
                    int(float(last_used)) == current_bar_id
                    and not allow_immediate_mtf_reverse
                ):
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": "ENTRY_METHOD_BAR_ALREADY_TRADED",
                        "success": False,
                    }
            except (TypeError, ValueError, OverflowError):
                return {
                    "status": "VETO",
                    "symbol": symbol,
                    "reason": "BLOCKED_MTF_ENTRY_BAR_CLAIM_INVALID",
                    "success": False,
                }
        account_key = cls._get_account_key(account, symbol)
        recent_entries = [
            stamp for stamp in cls._hourly_entry_records.get(
                account_key, cls._hourly_entry_records.get(symbol, []),
            )
            if time.time() - stamp < 3600.0
        ]
        if len(recent_entries) >= cls.MAX_ENTRIES_PER_HOUR:
            return {
                "status": "VETO",
                "symbol": symbol,
                "reason": "BLOCKED_HOURLY_ENTRY_LIMIT_EXCEEDED",
                "success": False,
            }
        save_state = getattr(account, "save_state", None)
        if not callable(save_state):
            return {
                "status": "VETO",
                "symbol": symbol,
                "reason": "BLOCKED_MTF_ENTRY_CLAIM_STORE_UNAVAILABLE",
                "success": False,
            }
        previous_persisted = persisted_bars.get(symbol)
        previous_class_claim = cls._last_trade_bar_time.get(symbol)
        persisted_bars[symbol] = current_bar_id
        account_entry_state["strict_ma3_ma15_last_trade_bar"] = persisted_bars
        cls._last_trade_bar_time[symbol] = current_bar_id
        try:
            try:
                save_state(strict=True)
            except TypeError:
                save_state()
        except Exception as exc:
            if previous_persisted is None:
                persisted_bars.pop(symbol, None)
            else:
                persisted_bars[symbol] = previous_persisted
            if previous_class_claim is None:
                cls._last_trade_bar_time.pop(symbol, None)
            else:
                cls._last_trade_bar_time[symbol] = previous_class_claim
            logger.error(
                "MTF_ENTRY_BAR_CLAIM_PERSIST_FAILED symbol=%s error=%s",
                symbol, exc,
            )
            return {
                "status": "VETO",
                "symbol": symbol,
                "reason": "BLOCKED_MTF_ENTRY_CLAIM_PERSIST_FAILED",
                "success": False,
            }

        bar_id = current_bar_id
        event_id = (
            f"MTF_KC|{symbol}|{entry['side']}|{bar_id}|"
            f"{int(float(htf['bar_timestamp']))}"
        )
        entry_meta = {
            "entry_method_authority": MTF_KC_STATE_MACHINE,
            "entry_method_allow_same_bar_after_close": allow_immediate_mtf_reverse,
            "mtf_cycle_reverse_after_close": allow_immediate_mtf_reverse,
            "mtf_colorless_reverse": (
                allow_immediate_mtf_reverse
                and entry.get("side") == preferred_side
            ),
            "entry_method_signal": {
                "method": MTF_KC_STATE_MACHINE,
                "side": entry["side"],
                "event_id": event_id,
                "confirmation_bar_id": bar_id,
                "bar_timestamp": bar_id,
                "structure_confirmed": True,
                "entry_phase": entry["reason"],
            },
            "entry_method_bar_id": bar_id,
            "entry_method_event_id": event_id,
            "entry_method_last_exit_timestamp": getattr(
                account, "last_closed_at", {},
            ).get(symbol),
            "candidate_signal_type": f"MTF_KC_STATE_MACHINE_{entry['side']}",
            "entry_type": MTF_KC_STATE_MACHINE,
            "entry_pattern": "TREND",
            "structure_confirmation": entry["reason"],
            "intrabar_authorized": True,
            "upstream_entry_gate_authorized": True,
            "direction_override": True,
            "allow_kc_internal": True,
            "entry_mode": "WILD_SWING",
            "no_rigid_sl": True,
            "raw_df": frame_1m,
            "frame_15m": frame_15m,
            "atr": entry["ltf_atr"],
            "mtf_kc_state_machine": True,
            "mtf_ltf_atr": entry["ltf_atr"],
            "mtf_kc_lower_15m": htf["kc_lower"],
            "mtf_kc_upper_15m": htf["kc_upper"],
            "mtf_kc_state": confirmed_trend,
            "mtf_kc_live_state": htf["state"],
            "mtf_normalized_slope": htf["normalized_slope"],
            "mtf_pending_candidate": entry.get("deferred_candidate"),
            "source": "UNIFIED_PIPELINE|MTF_KC_STATE_MACHINE",
        }
        opened = await cls.open_position(
            engine_or_account, symbol, entry["side"], quote,
            f"MTF_KC_STATE_MACHINE:{entry['reason']}", entry_meta,
        )
        if not opened:
            if persisted_bars.get(symbol) == bar_id:
                if previous_persisted is None:
                    persisted_bars.pop(symbol, None)
                else:
                    persisted_bars[symbol] = previous_persisted
            if cls._last_trade_bar_time.get(symbol) == bar_id:
                if previous_class_claim is None:
                    cls._last_trade_bar_time.pop(symbol, None)
                else:
                    cls._last_trade_bar_time[symbol] = previous_class_claim
            try:
                try:
                    save_state(strict=True)
                except TypeError:
                    save_state()
            except Exception as exc:
                logger.error(
                    "MTF_ENTRY_BAR_CLAIM_RELEASE_FAILED symbol=%s error=%s",
                    symbol, exc,
                )
        else:
            if symbol in pending_candidates:
                pending_candidates.pop(symbol, None)
                try:
                    try:
                        save_state(strict=True)
                    except TypeError:
                        save_state()
                except Exception as exc:
                    logger.error(
                        "MTF_DEFERRED_ENTRY_CLEAR_AFTER_OPEN_FAILED symbol=%s bar=%s error=%s",
                        symbol, bar_id, exc,
                    )
            if allow_immediate_mtf_reverse:
                reverse_tickets = account_entry_state.get(
                    "mtf_cycle_reverse_pending", {},
                )
                if isinstance(reverse_tickets, dict):
                    reverse_tickets.pop(symbol, None)
                try:
                    try:
                        save_state(strict=True)
                    except TypeError:
                        save_state()
                except Exception as exc:
                    logger.error(
                        "MTF_REVERSE_TICKET_CLEAR_AFTER_OPEN_FAILED symbol=%s error=%s",
                        symbol, exc,
                    )
        return {
            "status": "ENTRY_EXECUTED" if opened else "VETO",
            "symbol": symbol,
            "side": entry["side"],
            "reason": entry["reason"] if opened else "MTF_ENTRY_FINAL_GATE_VETO",
            "success": bool(opened),
        }

    @classmethod
    async def process_symbol_pipeline(
        cls,
        symbol: str,
        engine_or_account: Any,
        klines_df: Optional[pd.DataFrame] = None,
        quote: Optional[float] = None,
        frame_15m: Optional[pd.DataFrame] = None,
        frame_5m: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """
        單一幣種的標準流水線處理循環 (Thread-safe, Single-Pipeline)
        """
        if hasattr(engine_or_account, "positions"):
            account = engine_or_account
        else:
            account = getattr(engine_or_account, "account", engine_or_account)

        # 1. 抓取數據與計算純數值指標 (Data Layer)
        current_position = account.positions.get(symbol)
        has_position = bool(current_position and abs(float(current_position.get("amount", 0.0) or current_position.get("qty", 0.0))) > 0)
        if has_position:
            cls._clear_dual_trend_reversal_ticket(account, symbol)

        if klines_df is None or klines_df.empty:
            if hasattr(engine_or_account, "fetch_klines"):
                try:
                    klines_df = await engine_or_account.fetch_klines(symbol, timeframe="1m", limit=180)
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
        if isinstance(raw_df, pd.DataFrame):
            exit_frames = getattr(account, "_channel_exit_frames", None)
            if not isinstance(exit_frames, dict):
                exit_frames = account._channel_exit_frames = {}
            exit_frames[symbol] = raw_df
        if isinstance(frame_15m, pd.DataFrame) and (
            frame_15m.empty
            or getattr(frame_15m, "attrs", {}).get("timeframe_ms") == 900_000
        ):
            macro_frames = getattr(account, "_macro_trend_frame_15m", None)
            if not isinstance(macro_frames, dict):
                macro_frames = account._macro_trend_frame_15m = {}
            macro_frames[symbol] = frame_15m
        mtf_result = await cls._process_mtf_kc_state_machine(
            symbol=symbol,
            account=account,
            engine_or_account=engine_or_account,
            frame_1m=raw_df if isinstance(raw_df, pd.DataFrame) else klines_df,
            frame_15m=frame_15m,
            quote=curr_quote,
            position=current_position if has_position else None,
        )
        if mtf_result is not None and (
            has_position
            or mtf_result.get("status") == "ENTRY_EXECUTED"
            or (
                isinstance(frame_15m, pd.DataFrame)
                and (
                    frame_15m.empty
                    or getattr(frame_15m, "attrs", {}).get("timeframe_ms") == 900_000
                )
            )
        ):
            return mtf_result
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
        m15_curr = m15_c
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

        # Retain historical slopes for legacy exit diagnostics, not entry direction.
        macro_slope_val = float(getattr(market_data, "macro_slope", 0.0) or 0.0)
        kc_slope_10_val = kc_slope

        if raw_df is not None and isinstance(raw_df, pd.DataFrame) and "close" in raw_df.columns and len(raw_df) >= 20:
            s_close = raw_df["close"].astype(float)
            s_ema60 = s_close.ewm(span=60, adjust=False).mean()
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

        kc_slope_5_ref = locals().get("kc_slope_5_val", kc_slope)
        kc_slope_3_ref = locals().get("kc_slope_3_val", kc_slope)
        m15_slope_5_ref = locals().get("m15_slope_5_val", m15_c - m15_p)
        m15_slope_3_ref = locals().get("m15_slope_3_val", m15_c - m15_p)
        min_gc_dc_spread = max(0.08 * atr_v, m15_c * 0.0004) if atr_v > 0 else (m15_c * 0.0004)

        from core.services.candle_data import confirmed_kc_midline_slope

        entry_kc_slope = confirmed_kc_midline_slope(raw_df)
        entry_kc_up = entry_kc_slope is not None and entry_kc_slope > 0
        entry_kc_down = entry_kc_slope is not None and entry_kc_slope < 0
        macro_trend = "BULLISH" if entry_kc_up else ("BEARISH" if entry_kc_down else "NEUTRAL")
        # Legacy downstream predicates now share the same completed KC direction.
        is_zoomed_out_bullish = is_macro_htf_bullish = is_3bar_bull_slope_ready = entry_kc_up
        is_zoomed_out_bearish = is_macro_htf_bearish = is_3bar_bear_slope_ready = entry_kc_down
        cls._symbol_macro_trend[symbol] = macro_trend
        if hasattr(raw_df, "attrs"):
            raw_df.attrs["macro_trend_htf"] = getattr(market_data, "macro_trend", None)
            raw_df.attrs["macro_trend"] = macro_trend
        entry_m5_c = float(c_k.get("ma5", math.nan))
        entry_m5_p = float(p_k.get("ma5", math.nan))
        is_1m_confirmed_bull = bool(
            entry_kc_up and entry_m5_c > entry_m5_p and entry_m5_c > m15_c
            and c_c > kc_mid and c_c > m15_c
        )
        is_1m_confirmed_bear = bool(
            entry_kc_down and entry_m5_c < entry_m5_p and entry_m5_c < m15_c
            and c_c < kc_mid and c_c < m15_c
        )

        # MTF 四大情境狀態標記（情況 A: WAIT_REBOUND / 情況 B: OPEN_SHORT / 情況 C: WAIT_PULLBACK / 情況 D: OPEN_LONG）
        if is_zoomed_out_bearish:
            mtf_signal_state = "OPEN_SHORT" if (entry_m5_c < m15_c) else "WAIT_REBOUND"
        elif is_zoomed_out_bullish:
            mtf_signal_state = "OPEN_LONG" if (entry_m5_c > m15_c) else "WAIT_PULLBACK"
        else:
            mtf_signal_state = "NEUTRAL"

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

            # 🎯 Phase S2 階梯動態鎖利：Stage 1 (Peak Net ROE >= 10% -> 鎖 70%)、Stage 2 (Peak Net ROE >= 20% -> 鎖 85%)
            from core.services.exits.staged_profit_lock import (
                check_exit_conditions,
                evaluate_staged_profit_lock,
            )
            curr_profit_margin = ((curr_quote - pos_entry) / pos_entry) if pos_side == "LONG" else ((pos_entry - curr_quote) / pos_entry) if pos_entry > 0 else 0.0
            stored_highest_margin = float(current_position.get("highest_profit_margin", 0.0) or 0.0)
            highest_profit_margin = max(stored_highest_margin, curr_profit_margin)
            current_position["highest_profit_margin"] = round(highest_profit_margin, 6)

            staged_lock_hit, staged_lock_reason, staged_lock_meta = evaluate_staged_profit_lock(
                position=current_position,
                current_price=curr_quote,
                side=pos_side,
                current_ma3=m3_c,
                current_ma15=m15_c,
            )
            scheme1_action = check_exit_conditions(
                position=pos_side,
                entry_price=pos_entry,
                current_price=curr_quote,
                highest_profit_margin=highest_profit_margin,
                current_ma3=m3_c,
                current_ma15=m15_c,
                highest_roe_pct=peak_roe_pct,
                current_roe_pct=cur_roe_pct,
                tier1_start_roe_pct=10.0,
                tier1_drawback_pct=0.20,
            )

            pv_pos_meta = getattr(account, "position_meta", {}).get(symbol, {})
            dual_trend_exit_candidate = cls._confirmed_dual_trend_exit_candidate(
                current_position,
                pv_pos_meta if isinstance(pv_pos_meta, dict) else None,
                raw_df,
            )
            gate_should_exit, gate_reason, gate_meta = ExitGate.evaluate(
                pos=current_position,
                market_data=market_data,
                account=account,
            )
            if (
                gate_should_exit
                and gate_reason in ExitGate.ABSOLUTE_SAFETY_REASONS
            ):
                success = await cls.close_position(
                    account=account,
                    symbol=symbol,
                    price=curr_quote,
                    reason=gate_reason,
                    meta={**(gate_meta or {}), "raw_df": raw_df},
                )
                return {
                    "status": "EXIT_EXECUTED" if success else "HOLD",
                    "symbol": symbol,
                    "reason": (
                        gate_reason
                        if success else f"EXIT_BLOCKED_HOLD:{gate_reason}"
                    ),
                    "success": success,
                }
            from core.services.exits.strict_true_pivot import PROFIT_PIVOT_REASONS
            if (
                gate_should_exit
                and gate_reason in PROFIT_PIVOT_REASONS
                and dual_trend_exit_candidate
            ):
                success = await cls.close_position(
                    account=account,
                    symbol=symbol,
                    price=curr_quote,
                    reason=gate_reason,
                    meta={**gate_meta, "raw_df": raw_df},
                )
                return {
                    "status": "EXIT_EXECUTED" if success else "HOLD",
                    "symbol": symbol,
                    "reason": gate_reason if success else "EXIT_BLOCKED_HOLD:" + gate_reason,
                    "success": success,
                }

            # 🎯 峰谷轉向低延遲即時平倉與條件式即時反手引擎（統一接管所有持倉，含舊通道開的單）
            from core.symbol_aliases import is_meme_breakout_symbol as _is_meme_exit_symbol

            if isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 3:
                from core.services.low_latency_exit import (
                    evaluate_peak_valley_exit,
                    execute_peak_valley_exit_and_conditional_flip,
                )

                acc_key_pv = cls._get_account_key(account, symbol)
                cls._pv_flip_watch_records.pop(acc_key_pv, None)
                last_flip_rec = cls._pv_flip_records.get(acc_key_pv) or {}
                pv_exit = evaluate_peak_valley_exit(
                    raw_df,
                    position=current_position,
                    live_price=curr_quote,
                    metadata=pv_pos_meta if isinstance(pv_pos_meta, dict) else None,
                    last_flip_bar_id=last_flip_rec.get("flip_bar_id"),
                    symbol=symbol,
                )
                for state_k, state_v in (pv_exit.get("state_updates") or {}).items():
                    current_position[state_k] = state_v
                    if isinstance(pv_pos_meta, dict):
                        pv_pos_meta[state_k] = state_v

                if (
                    pv_exit.get("reason") == "HOLD_SAME_BAR_FLIP_EXIT_LOCKED"
                    and not dual_trend_exit_candidate
                ):
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": "HOLD_SAME_BAR_FLIP_EXIT_LOCKED",
                        "trigger_level": "LOCKED",
                        "anti_whipsaw": pv_exit.get("anti_whipsaw"),
                        "should_flip": False,
                        "flipped": False,
                        "success": False,
                    }

                if pv_exit.get("should_exit") and not dual_trend_exit_candidate:
                    flip_lock = cls._position_flip_locks.setdefault(symbol, asyncio.Lock())
                    async with flip_lock:
                        exit_result = await execute_peak_valley_exit_and_conditional_flip(
                            account=account,
                            engine_or_account=engine_or_account,
                            symbol=symbol,
                            df=raw_df,
                            live_price=curr_quote,
                            pv_exit=pv_exit,
                            closed_side=pos_side,
                            close_fn=cls.close_position,
                            open_fn=cls.open_position,
                            flip_bar_registry=cls._pv_flip_records,
                            exit_record_registry=cls._pv_exit_records,
                            flip_watch_registry=cls._pv_flip_watch_records,
                            account_key=acc_key_pv,
                            macro_trend=macro_trend,
                            atr=atr_v,
                        )
                        if (
                            exit_result.get("success")
                            and exit_result.get("position_zero_confirmed")
                            and symbol not in getattr(account, "positions", {})
                            and not exit_result.get("flipped")
                        ):
                            from core.services.exits.authorized_position_exit import (
                                dual_trend_exit_block_reason,
                                is_dual_trend_emergency_exit,
                            )

                            exit_reason = str(
                                exit_result.get("reason") or pv_exit.get("reason") or ""
                            )
                            position_meta = (
                                pv_pos_meta if isinstance(pv_pos_meta, dict) else None
                            )
                            if (
                                not is_dual_trend_emergency_exit(exit_reason)
                                and dual_trend_exit_block_reason(
                                    current_position, raw_df, position_meta,
                                ) is None
                            ):
                                if not cls._store_dual_trend_reversal_ticket(
                                    account,
                                    symbol,
                                    current_position,
                                    position_meta,
                                    exit_reason,
                                ):
                                    exit_result["dual_trend_flip_status"] = (
                                        "PERSISTENCE_BLOCKED"
                                    )
                                else:
                                    dual_flip = await cls._try_structure_position_flip(
                                        account=account,
                                        symbol=symbol,
                                        position_side=pos_side,
                                        frame=raw_df,
                                        quote=curr_quote,
                                        macro_trend=getattr(market_data, "macro_trend", None) or macro_trend,
                                        exit_reason=exit_reason,
                                        position=current_position,
                                        position_meta=position_meta,
                                    )
                                    if dual_flip and dual_flip.get("success"):
                                        if not cls._clear_dual_trend_reversal_ticket(
                                            account, symbol,
                                        ):
                                            logger.critical(
                                                "DUAL_TREND_REVERSAL_TICKET_CLEAR_FAILED_AFTER_OPEN symbol=%s",
                                                symbol,
                                            )
                                        return {
                                            "status": "SUCCESS_FLIP",
                                            "symbol": symbol,
                                            "side": dual_flip["side"],
                                            "reason": dual_flip["reason"],
                                            "success": True,
                                            "close_result": exit_result,
                                        }
                                    exit_result["dual_trend_flip_status"] = "PENDING"
                                    exit_result["dual_trend_flip_reason"] = (
                                        dual_flip.get("reason")
                                        if isinstance(dual_flip, dict)
                                        else "REVERSAL_ENTRY_NOT_AUTHORIZED"
                                    )
                                    exit_result["dual_trend_flip_retry"] = True
                        return exit_result

                # The layered hold must not mask a separately confirmed KC + HTF reversal.
                if pv_exit.get("exit_policy") == "KC_LAYERED_3TIER_V1":
                    wf_hit, wf_reason, wf_meta = (
                        UltimateShortExitGatekeeper.check_waterfall_emergency_exit(
                            symbol,
                            current_position,
                            pv_pos_meta if isinstance(pv_pos_meta, dict) else {},
                            curr_quote,
                            frame=raw_df,
                        )
                    )
                    if wf_hit:
                        success = await cls.close_position(
                            account=account,
                            symbol=symbol,
                            price=curr_quote,
                            reason=wf_reason,
                            meta={**wf_meta, "raw_df": raw_df},
                        )
                        return {
                            "status": "EXIT_EXECUTED" if success else "HOLD",
                            "symbol": symbol,
                            "reason": wf_reason if success else "EXIT_BLOCKED_HOLD:" + wf_reason,
                            "success": success,
                        }
                    if dual_trend_exit_candidate:
                        logger.info(
                            "DUAL_TREND_EXIT_OVERRIDES_LAYERED_HOLD symbol=%s reason=%s",
                            symbol, dual_trend_exit_candidate,
                        )
                    else:
                        trailing_stop_p = float(current_position.get("trailing_stop_price", 0.0) or 0.0)
                        if trailing_stop_p > 0 and account is not None:
                            from core.services.ExchangeOrderManager import ExchangeOrderManager
                            try:
                                asyncio.create_task(
                                    ExchangeOrderManager.place_or_update_native_stop_order(
                                        account=account,
                                        symbol=symbol,
                                        position=current_position,
                                        target_stop_price=trailing_stop_p,
                                        atr=atr_v,
                                    )
                                )
                            except Exception as err:
                                logger.warning(f"⚠️ [NATIVE_STOP_TRIGGER_ERR] {symbol}: {err}")
                        return {
                            "status": "HOLD",
                            "symbol": symbol,
                            "reason": pv_exit.get("reason", "HOLD_KC_TREND_INTACT"),
                            "trigger_level": pv_exit.get("trigger_level", "NONE"),
                            "exit_policy": "KC_LAYERED_3TIER_V1",
                            "metrics": pv_exit.get("metrics"),
                            "should_flip": False,
                            "flipped": False,
                            "success": False,
                        }

            # 🛡️ 妖幣平倉唯一結構出口：波段方向鎖定（KC中軌未轉向不平倉）/ 完整結構破壞 / 緊急斷路器
            if _is_meme_exit_symbol(symbol):
                # 🚀 優先評估波段方向鎖平倉機制 (evaluate_exit_with_trend_lock: 中軌方向沒變一律不平倉，跌破中軌或極端風控才離場)
                from core.strategies.true_peak_trough_strategy import evaluate_exit_with_trend_lock, PositionState as _TruePosState
                position_meta = getattr(account, "position_meta", {}).get(symbol, {})
                structure_reason = UltimateShortExitGatekeeper.sharp_reversal_exit_candidate(
                    current_position,
                    raw_df,
                    position_meta,
                )
                if not structure_reason:
                    structure_reason = UltimateShortExitGatekeeper.ma5_angle_exit_candidate(
                        current_position, raw_df, position_meta, quote=curr_quote,
                    )
                if not structure_reason:
                    structure_reason = UltimateShortExitGatekeeper.kc_middle_slope_exit_candidate(
                        current_position, raw_df, position_meta,
                    )
                _active_exit_res = None
                if (
                    not structure_reason
                    and raw_df is not None
                    and isinstance(raw_df, pd.DataFrame)
                    and len(raw_df) >= 5
                ):
                    try:
                        _active_exit_res = evaluate_exit_with_trend_lock(
                            df=raw_df,
                            current_position=_TruePosState(pos_side),
                            entry_price=pos_entry,
                        )
                    except Exception as _ex:
                        logger.warning(f"[evaluate_exit_with_trend_lock] error: {_ex}")
                        _active_exit_res = None

                if structure_reason:
                    logger.info(
                        "STRUCTURE_EXIT_TRIGGERED symbol=%s side=%s reason=%s",
                        symbol, pos_side, structure_reason,
                    )
                elif _active_exit_res and _active_exit_res.get("action") in ("CLOSE_LONG", "CLOSE_SHORT", "FLIP_TO_LONG", "FLIP_TO_SHORT", "EMERGENCY_CLOSE"):
                    structure_reason = f"ACTIVE_EXIT_{_active_exit_res['action']}|{_active_exit_res.get('reason', '')}"
                    logger.info(f"⚡ [ACTIVE_EXIT_TRIGGERED] {symbol} {pos_side} 觸發波段反轉/風控平倉: {structure_reason}")
                elif _active_exit_res and _active_exit_res.get("action") == "HOLD":
                    structure_reason = dual_trend_exit_candidate
                    if structure_reason:
                        logger.info(
                            "DUAL_TREND_EXIT_RELEASED_FROM_TREND_LOCK symbol=%s reason=%s",
                            symbol, structure_reason,
                        )
                    else:
                        logger.info(
                            "🛡️ [TREND_LOCK_HOLD] %s %s %s，阻斷任何普通平倉！",
                            symbol, pos_side, _active_exit_res.get("reason", ""),
                        )
                else:
                    # 🛡️ 中軌方向鐵律：只有 KC 中軌確實反向（翻向），才允許觸發結構性出場
                    # 多單持倉：中軌向下才算翻向；空單持倉：中軌向上才算翻向
                    # 中軌走平（kc_mid == p_kc_mid）視為「方向未變」→ 一票否決 structure_exit_candidate
                    _kc_mid_curr = kc_mid  # 當前 KC 中軌（第 773 行已計算）
                    _kc_mid_prev = float(p_k.get("kc_middle", _kc_mid_curr))  # 前根 KC 中軌
                    _kc_direction_flipped = (
                        (pos_side == "LONG" and _kc_mid_curr < _kc_mid_prev)
                        or (pos_side == "SHORT" and _kc_mid_curr > _kc_mid_prev)
                    )
                    if _kc_direction_flipped:
                        structure_reason = UltimateShortExitGatekeeper.structure_exit_candidate(
                            symbol, current_position,
                            getattr(account, "position_meta", {}).get(symbol, {}),
                            curr_quote, raw_df,
                        )
                    else:
                        # KC 中軌方向未翻向（走平或與持倉同向）→ 絕對禁止非峰谷出場
                        structure_reason = None
                        logger.info(
                            f"🛡️ [KC_DIRECTION_HOLD] {symbol} {pos_side} "
                            f"KC 中軌方向未翻向 (curr={_kc_mid_curr:.6f} vs prev={_kc_mid_prev:.6f})，"
                            "一般結構出口維持阻擋；已確認 KC＋HTF 同向反轉例外放行。"
                        )
                if not structure_reason and dual_trend_exit_candidate:
                    structure_reason = dual_trend_exit_candidate
                is_emerg_bar_open = bool(
                    raw_df is not None
                    and isinstance(raw_df, pd.DataFrame)
                    and len(raw_df) >= 2
                    and not bool(raw_df.iloc[-1].get("is_closed", True))
                )
                if not structure_reason and is_emerg_bar_open:
                    wf_hit, wf_reason, _ = UltimateShortExitGatekeeper.check_waterfall_emergency_exit(
                        symbol,
                        current_position,
                        getattr(account, "position_meta", {}).get(symbol, {}),
                        curr_quote,
                        frame=raw_df,
                    )
                    if wf_hit:
                        structure_reason = wf_reason
                if structure_reason:
                    is_closed_flag = not (
                        structure_reason == "EXIT_BY_EXTREME_WATERFALL"
                        and is_emerg_bar_open
                    ) and structure_reason not in MA5_ANGLE_REVERSAL_EXIT_REASONS.values()
                    success = await cls.close_position(
                        account=account, symbol=symbol, price=curr_quote,
                        reason=structure_reason, meta={"raw_df": raw_df, "is_bar_closed": is_closed_flag},
                    )
                    flip = None
                    dual_trend_ready = False
                    if success and symbol not in getattr(account, "positions", {}):
                        acc_key = cls._get_account_key(account, symbol)
                        cls._last_exit_record.pop(acc_key, None)
                        cls._last_exit_record.pop(symbol, None)
                        cls._last_trade_bar_time.pop(symbol, None)
                        from core.services.exits.authorized_position_exit import (
                            dual_trend_exit_block_reason,
                            is_dual_trend_emergency_exit,
                        )
                        position_meta = (
                            pv_pos_meta if isinstance(pv_pos_meta, dict) else None
                        )
                        dual_trend_ready = bool(
                            not is_dual_trend_emergency_exit(structure_reason)
                            and dual_trend_exit_block_reason(
                                current_position, raw_df, position_meta,
                            ) is None
                        )
                        if dual_trend_ready:
                            if cls._store_dual_trend_reversal_ticket(
                                account,
                                symbol,
                                current_position,
                                position_meta,
                                structure_reason,
                            ):
                                flip = await cls._try_structure_position_flip(
                                    account=account,
                                    symbol=symbol,
                                    position_side=pos_side,
                                    frame=raw_df,
                                    quote=curr_quote,
                                    macro_trend=getattr(market_data, "macro_trend", None) or macro_trend,
                                    exit_reason=structure_reason,
                                    position=current_position,
                                    position_meta=position_meta,
                                )
                            else:
                                flip = {
                                    "success": False,
                                    "reason": "DUAL_TREND_REVERSAL_PERSISTENCE_BLOCKED",
                                }
                        elif structure_reason in MA5_ANGLE_REVERSAL_EXIT_REASONS.values():
                            angle_signal = UltimateShortExitGatekeeper.ma5_angle_opportunity(
                                raw_df, curr_quote,
                            )
                            if angle_signal and await cls._claim_ma5_angle_latch(
                                account, symbol, angle_signal[0], angle_signal[2],
                            ):
                                flip = await cls._try_structure_position_flip(
                                    account=account,
                                    symbol=symbol,
                                    position_side=pos_side,
                                    frame=raw_df,
                                    quote=curr_quote,
                                    macro_trend=getattr(market_data, "macro_trend", None) or macro_trend,
                                    exit_reason=structure_reason,
                                    position=current_position,
                                    position_meta=pv_pos_meta,
                                )
                        else:
                            flip = await cls._try_structure_position_flip(
                                account=account,
                                symbol=symbol,
                                position_side=pos_side,
                                frame=raw_df,
                                quote=curr_quote,
                                macro_trend=getattr(market_data, "macro_trend", None) or macro_trend,
                                exit_reason=structure_reason,
                                position=current_position,
                                position_meta=pv_pos_meta,
                            )
                    if flip and flip.get("success"):
                        if dual_trend_ready and not cls._clear_dual_trend_reversal_ticket(
                            account, symbol,
                        ):
                            logger.critical(
                                "DUAL_TREND_REVERSAL_TICKET_CLEAR_FAILED_AFTER_OPEN symbol=%s",
                                symbol,
                            )
                        return {
                            "status": "SUCCESS_FLIP",
                            "symbol": symbol,
                            "side": flip["side"],
                            "reason": flip["reason"],
                            "success": True,
                        }
                    return {
                        "status": "EXIT_EXECUTED" if success else "HOLD",
                        "symbol": symbol,
                        "reason": structure_reason if success else "EXIT_BLOCKED_HOLD:" + structure_reason,
                        "success": success,
                        **(
                            {"flip_status": "VETO", "flip_reason": flip["reason"]}
                            if flip else {}
                        ),
                        **(
                            {"dual_trend_flip_retry": True}
                            if dual_trend_ready and flip and not flip.get("success")
                            else {}
                        ),
                    }

            # 審查 ExitGate 是否觸發鎖利 (Profit Lock)、大瀑布 (Extreme Waterfall)、市場熔斷 (Circuit Breaker)
            if gate_should_exit and (
                gate_reason in PROFIT_PIVOT_REASONS
                or (
                    gate_reason in ExitGate.ABSOLUTE_SAFETY_REASONS
                    and not (
                        _is_meme_exit_symbol(symbol)
                        and gate_reason == "EXIT_BY_EXTREME_WATERFALL"
                    )
                )
            ):
                success = await cls.close_position(
                    account=account,
                    symbol=symbol,
                    price=curr_quote,
                    reason=gate_reason,
                    meta={**gate_meta, "raw_df": raw_df},
                )
                return {
                    "status": "EXIT_EXECUTED" if success else "HOLD",
                    "symbol": symbol,
                    "reason": gate_reason if success else "EXIT_BLOCKED_HOLD:" + gate_reason,
                    "success": success,
                }
            is_absolute_safety = bool(gate_should_exit and gate_reason in ExitGate.ABSOLUTE_SAFETY_REASONS)
            is_profit_lock = bool(
                (gate_should_exit and (
                    gate_reason in ExitGate.PROFIT_LOCK_EXIT_REASONS
                    or "PROFIT_LOCK" in str(gate_reason).upper()
                    or "RATCHET" in str(gate_reason).upper()
                    or "TRAILING_LOCK" in str(gate_reason).upper()
                ))
                or (scheme1_action in ("CLOSE_SHORT", "CLOSE_LONG"))
            )
            if not is_absolute_safety:
                return {
                    "status": "HOLD",
                    "symbol": symbol,
                    "reason": "HOLD_UNTIL_PROFIT_CONFIRMED_TRUE_PIVOT",
                    "suppressed_exit_reason": gate_reason,
                }
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
                or (kc_slope_5_ref > 0)
                or (kc_slope_10_val > 0)
                or (m15_slope_5_ref > 0)
                or (pos_side == "LONG" and (curr_quote >= kc_mid or closed_c >= closed_kc_mid or closed_c >= m15_c) and curr_quote >= kc_lo)
            )
            is_kc_mid_down = bool(
                (p_kc_mid > 0 and kc_mid <= p_kc_mid + 1e-9)
                or (prev_closed_kc_mid > 0 and closed_kc_mid <= prev_closed_kc_mid + 1e-9)
                or (kc_slope <= 0.00001)
                or (kc_slope_5_ref < 0)
                or (kc_slope_10_val < 0)
                or (m15_slope_5_ref < 0)
                or (pos_side == "SHORT" and (curr_quote <= kc_mid or closed_c <= closed_kc_mid or closed_c <= m15_c) and curr_quote <= kc_u)
            )

            # 🛡️ 大週期不變順勢鎖倉保護（多單與空單完全對稱鋼鐵鎖定）：
            # 1. 多單持倉中：只要 5棒/10棒 KC 中軌仍向上 (kc_slope_5_ref >= 0 or kc_slope_10_val > 0) 或 MA15 慢線仍向上 (m15_c >= m15_p or m15_slope_5_ref >= 0)，
            #    或收盤價仍在 KC 中軌/MA15 上方，中間任何 1~2 根急殺洗盤紅棒一律視為多頭通道內的正常回踩洗盤，100% 鎖死不平多！
            # 2. 空單持倉中：只要 5棒/10棒 KC 中軌仍向下 (kc_slope_5_ref <= 0 or kc_slope_10_val < 0) 或 MA15 慢線仍向下 (m15_c <= m15_p or m15_slope_5_ref <= 0)，
            #    或收盤價仍在 KC 中軌/MA15 下方，中間任何 1~2 根反彈洗盤綠棒一律視為空頭通道內的正常反彈洗盤，100% 鎖死不平空！
            trend_hold_shield = bool(
                (
                    pos_side == "LONG"
                    and (
                        (is_kc_mid_up and (closed_c >= closed_kc_mid or closed_c >= m15_c) and (curr_quote >= kc_mid or curr_quote >= m15_c))
                        or (kc_slope_5_ref > 0 and m15_slope_5_ref >= 0 and closed_c >= closed_kc_lo)
                        or (kc_slope_10_val > 0 and m15_c >= m15_p and closed_c >= closed_kc_lo)
                    )
                )
                or (
                    pos_side == "SHORT"
                    and (
                        (is_kc_mid_down and (closed_c <= closed_kc_mid or closed_c <= m15_c) and (curr_quote <= kc_mid or curr_quote <= m15_c))
                        or (kc_slope_5_ref < 0 and m15_slope_5_ref <= 0 and closed_c <= closed_kc_u)
                        or (kc_slope_10_val < 0 and m15_c <= m15_p and closed_c <= closed_kc_u)
                    )
                )
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

            # 🛑 【K棒陰陽同向平倉鐵律：出紅K（下跌陰線 close <= open）絕不平空、出綠K（上漲陽線 close >= open）絕不平多】：
            # 依使用者最高指示：「K線若不是大瀑布、異常K，就要讓他走完，不要一開始就平倉；開空後來會變回紅K線就可以不平倉，多空都要改」！
            # 1. 空單平倉：已收線棒必須為上漲陽線 (closed_c > closed_o)，且當前 live 棒嚴禁為順勢下跌紅 K (即一旦變回紅 K curr_quote <= c_o 絕不平空)！
            # 2. 多單平倉：已收線棒必須為下跌陰線 (closed_c < closed_o)，且當前 live 棒嚴禁為順勢上漲綠 K (即一旦變回綠 K curr_quote >= c_o 絕不平多)！
            can_exit_short_on_candle = bool(
                closed_c > closed_o
                and curr_quote > c_o
            )
            can_exit_long_on_candle = bool(
                closed_c < closed_o
                and curr_quote < c_o
            )

            # 🎯 最高優先級：1. 絕對安全風控  2. 階梯鎖利 (Profit Lock 無視 trend_hold_shield 立即落袋)  3. 實質獲利展開後之真峰頂/真谷底
            if is_absolute_safety:
                # 若為 OPPOSITE_KC_BAND_BREACH，必須確認已收線實體真正破軌，拒絕下影線/上影線毛刺誤平倉
                if "OPPOSITE_KC_BAND_BREACH" in str(gate_reason).upper():
                    # 拉遠大週期仍與持倉同向時，須已收線實體破軌深度 >= 0.5 ATR 才放行（拒絕盤中毛刺）
                    long_need = 0.5 * atr_v if not is_zoomed_out_bearish else 0.0
                    short_need = 0.5 * atr_v if not is_zoomed_out_bullish else 0.0
                    if pos_side == "LONG" and not (
                        can_exit_long_on_candle and closed_c < closed_kc_lo - long_need and curr_quote < kc_lo
                    ):
                        is_absolute_safety = False
                    elif pos_side == "SHORT" and not (
                        can_exit_short_on_candle and closed_c > closed_kc_u + short_need and curr_quote > kc_u
                    ):
                        is_absolute_safety = False
            if is_absolute_safety:
                should_exit = True
                reason = gate_reason
                meta = gate_meta
                logger.warning(f"🚨 [ABSOLUTE_SAFETY_EXIT] {symbol} {pos_side} 命中絕對安全出場防線！reason={reason}")
            elif is_profit_lock and (
                (
                    pos_side == "LONG"
                    and can_exit_long_on_candle
                    and pos_entry > 0
                    and curr_quote > pos_entry * 1.0015
                    and (
                        # (A) 二階大波段鎖利 (peak_roe >= 15%)：無論大週期為何，一旦達到 15% ROE 且跌破 MA3/MA5 或跌破 KC 中軌，立即高點鎖利落袋，絕不坐過山車抱回起漲點！
                        (peak_roe_pct >= 15.0 and ((closed_c < m3_c and curr_quote < m3_c and m3_c < m3_p) or (closed_c < closed_kc_mid and curr_quote < kc_mid)))
                        # (B) 階梯鎖利：達標 10% ROE 回吐跌破 MA3 即刻放行，大週期不可阻擋實質獲利落袋
                        or (scheme1_action == "CLOSE_LONG")
                        or (peak_roe_pct >= 10.0 and closed_c < closed_o and closed_c < m3_c and curr_quote < m3_c and m3_c < m3_p)
                    )
                )
                or (
                    pos_side == "SHORT"
                    and can_exit_short_on_candle
                    and pos_entry > 0
                    and curr_quote < pos_entry * 0.9985
                    and (
                        # (A) 二階大波段鎖利 (peak_roe >= 15%)：無論大週期為何，一旦達到 15% ROE 且突破 MA3/MA5 或突破 KC 中軌，立即低點鎖利落袋，絕不坐過山車抱回起跌點！
                        (peak_roe_pct >= 15.0 and ((closed_c > m3_c and curr_quote > m3_c and m3_c > m3_p) or (closed_c > closed_kc_mid and curr_quote > kc_mid)))
                        # (B) 階梯鎖利：達標 10% ROE 回吐突破 MA3 即刻放行，大週期不可阻擋實質獲利落袋
                        or (scheme1_action == "CLOSE_SHORT")
                        or (peak_roe_pct >= 10.0 and closed_c > closed_o and closed_c > m3_c and curr_quote > m3_c and m3_c > m3_p)
                    )
                )
            ):
                should_exit = True
                reason = "EXIT_BY_SCHEME1_PROFIT_LOCK_MA3_MA15" if scheme1_action in ("CLOSE_SHORT", "CLOSE_LONG") else (gate_reason or "EXIT_BY_RATCHET_PROFIT_LOCK")
                meta = {**gate_meta, "pivot_type": "PROFIT_LOCK", "trigger": "STAGE_PROFIT_LOCK_SCHEME1", "highest_profit_margin": highest_profit_margin}
                logger.info(f"💰 [PROFIT_LOCK_EXECUTION] {symbol} {pos_side} 命中方案1鎖利防線且確認維持正淨利區間，立即放行出場保利！reason={reason}, meta={meta}")
            elif (has_profit_expansion or favorable_gain_atr >= 1.2 or peak_roe_pct >= 10.0) and (
                (
                    pos_side == "LONG"
                    and (is_true_peak or (is_pivot_exit and "PEAK" in str(gate_reason).upper()))
                    and (closed_c < m15_c or closed_c < closed_kc_mid or peak_roe_pct >= 15.0)
                )
                or (
                    pos_side == "SHORT"
                    and (is_true_bottom or (is_pivot_exit and "VALLEY" in str(gate_reason).upper()))
                    and (closed_c > m15_c or closed_c > closed_kc_mid or peak_roe_pct >= 15.0)
                )
            ):
                # 🎯 【方案A真峰頂/真谷底獲利了結鐵律（AGENTS.md最高授權）】：
                # 開倉波段實質展開獲利（peak_roe >= 10% 或 >= 1.2 ATR）：
                # 1. 多單真峰頂：已衝頂收黑且實體跌破 MA15/KC中軌，或達 15% ROE 大波段鎖利，立即市價平多！
                # 2. 空單真谷底：已探底收紅且實體突破 MA15/KC中軌，或達 15% ROE 大波段鎖利，立即市價平空！
                # 3. 守穩中軌續抱鐵律：只要多單仍穩守 MA15/KC 中軌上方、空單仍壓制在 MA15/KC 中軌下方，一票否決提早平倉，死抱主升/主跌浪！
                should_exit = True
                reason = "EARLY_PIVOT_PEAK_EXIT" if pos_side == "LONG" else "EARLY_PIVOT_VALLEY_EXIT"
                meta = {
                    **(gate_meta or {}),
                    "pivot_type": "TRUE_PIVOT_PROFIT_TAKE",
                    "trigger": "TRUE_PIVOT_EXIT_MANDATORY",
                    "peak_roe": peak_roe_pct,
                    "cur_roe": cur_roe_pct,
                }
                logger.info(f"🏔️ [TRUE_PIVOT_EXIT] {symbol} {pos_side} 波段獲利展開 (peak_roe={peak_roe_pct:.2f}%) 且確認結構轉弱，立即獲利了結！reason={reason}")

            # 🚀 高靈敏度解耦動態出場引擎 (Agile Decoupled Exit Engine)：
            # 1. 階梯保本 (Breakeven Lock): 浮盈曾達 +8% ROE，價格跌回成本價(+0.3%覆蓋手續費)立即強制平倉保本
            # 2. 5% 回吐移動鎖利 (Staged Trailing Lock): 浮盈曾達 +20% ROE，回吐達 5% 立即止盈
            # 3. 逆勢單快平 (Counter-Trend Scalp Exit): 逆勢多單觸及 KC 上軌、破前低 Low[1]、長上影線力竭或首根陰線立即平倉
            if not should_exit and pos_entry > 0:
                is_counter_trend = (
                    (pos_side == "LONG" and is_zoomed_out_bearish)
                    or (pos_side == "SHORT" and is_zoomed_out_bullish)
                )

                # 🚀 1000PEPE & 龍蝦「多階段階梯保本 + 動態移動止盈 (Trailing Stop)」專用裁決引擎
                from core.services.exits.staged_trailing_exit_engine import evaluate_staged_trailing_exit
                staged_exit_needed, staged_exit_reason, staged_exit_meta = evaluate_staged_trailing_exit(
                    position=current_position,
                    current_price=curr_quote,
                    macro_trend="BULLISH" if is_zoomed_out_bullish else ("BEARISH" if is_zoomed_out_bearish else macro_trend),
                )

                # 🛡️ 交易所端原生條件單同步掛載 (Exchange Native Stop Order Sync)：
                # 當持倉出現有效鎖利防守價時，立即調用 ExchangeOrderManager 掛載或推進交易所端 Native Stop 條件單！
                trailing_stop_p = float(current_position.get("trailing_stop_price", 0.0) or 0.0)
                if trailing_stop_p > 0 and account is not None:
                    from core.services.ExchangeOrderManager import ExchangeOrderManager
                    try:
                        asyncio.create_task(
                            ExchangeOrderManager.place_or_update_native_stop_order(
                                account=account,
                                symbol=symbol,
                                position=current_position,
                                target_stop_price=trailing_stop_p,
                                atr=atr_v,
                            )
                        )
                    except Exception as err:
                        logger.warning(f"⚠️ [NATIVE_STOP_TRIGGER_ERR] {symbol}: {err}")

                if staged_exit_needed and not should_exit:
                    # ⚠️ 順勢大週期一單到底與獲利鎖定平衡：
                    # 大週期熊市持有空單、或大週期牛市持有多單時，僅一票否決 Stage 1 (零利潤成本保本)，防止在剛開倉呼吸時被震下車！
                    # 一旦達到 Stage 2 (+4% ROE)、Stage 3 (+8% ROE)、Stage 4 (+12% ROE / Trailing Stop)，屬於實質波段獲利，必須及時放行平倉落袋！
                    is_macro_aligned = (pos_side == "SHORT" and is_zoomed_out_bearish) or (pos_side == "LONG" and is_zoomed_out_bullish)
                    is_stage1_only = "STAGE1" in str(staged_exit_reason)
                    if is_macro_aligned and is_stage1_only:
                        logger.info(f"🛡️ [VETO_STAGED_LADDER] {symbol} {pos_side} 大週期順勢持倉，一票否決 {staged_exit_reason} 成本零利潤保本平倉，拒絕被洗下車！")
                    else:
                        should_exit = True
                        reason = staged_exit_reason
                        meta = {**staged_exit_meta, "trigger": "STAGED_TRAILING_LADDER_EXIT"}
                        logger.info(f"💎 [STAGED_TRAILING_EXIT] {symbol} {pos_side} 命中多階段階梯保本/移動止盈！reason={reason}, meta={meta}")

                # (1) 保本防線：浮盈曾達 8% ROE，回踩成本線 (0.3% 手續費與滑價緩衝) 保本平倉
                # ⚠️ 順勢大週期一單到底鐵律：大週期牛市多單或熊市空單，絕不在成本回踩震下車！
                if peak_roe_pct >= 8.0:
                    if pos_side == "LONG" and curr_quote <= pos_entry * 1.003 and (not is_zoomed_out_bullish):
                        should_exit = True
                        reason = "EXIT_BY_BREAKEVEN_PROTECTION_LONG"
                        meta = {"peak_roe_pct": peak_roe_pct, "cur_roe_pct": cur_roe_pct, "entry_price": pos_entry, "be_floor": pos_entry * 1.003}
                    elif pos_side == "SHORT" and curr_quote >= pos_entry * 0.997 and (not is_zoomed_out_bearish):
                        should_exit = True
                        reason = "EXIT_BY_BREAKEVEN_PROTECTION_SHORT"
                        meta = {"peak_roe_pct": peak_roe_pct, "cur_roe_pct": cur_roe_pct, "entry_price": pos_entry, "be_floor": pos_entry * 0.997}

                # (2) 二階鎖利：浮盈曾達 20% ROE，自高點回吐達 5% 立刻止盈
                if not should_exit and peak_roe_pct >= 20.0:
                    drawback_roe = peak_roe_pct - cur_roe_pct
                    if drawback_roe >= 5.0:
                        should_exit = True
                        reason = "EXIT_BY_STAGED_TRAILING_PROFIT_LOCK_5PCT"
                        meta = {"peak_roe_pct": peak_roe_pct, "cur_roe_pct": cur_roe_pct, "drawback_roe": drawback_roe}

                # (3) 逆勢單極速快平（熊市做多或牛市做空）
                if not should_exit and is_counter_trend:
                    if pos_side == "LONG":
                        # A. 觸及 KC 上軌即止盈
                        if curr_quote >= closed_kc_u or closed_h >= closed_kc_u:
                            should_exit = True
                            reason = "EXIT_COUNTER_TREND_TP_HIT_KC_UPPER"
                            meta = {"curr_quote": curr_quote, "kc_upper": closed_kc_u}
                        # B. 跌破前一根 K 線低點 (Low[1])
                        elif closed_c < prev_closed_l:
                            should_exit = True
                            reason = "EXIT_COUNTER_TREND_BREAK_PREV_LOW"
                            meta = {"closed_close": closed_c, "prev_low": prev_closed_l}
                        # C. 處於浮盈區時出現首根收黑陰線閃人
                        elif closed_c < closed_o and curr_quote > pos_entry * 1.0015:
                            should_exit = True
                            reason = "EXIT_COUNTER_TREND_FIRST_RED_CANDLE"
                            meta = {"closed_close": closed_c, "closed_open": closed_o}
                        # D. 高位長上影線力竭 (上影線 >= 1.5 倍實體且 >= 0.3 ATR)
                        else:
                            c_body = abs(closed_c - closed_o)
                            upper_wick = closed_h - max(closed_o, closed_c)
                            if upper_wick >= 1.5 * max(c_body, 0.1 * atr_v) and upper_wick >= 0.3 * atr_v and curr_quote > pos_entry:
                                should_exit = True
                                reason = "EXIT_COUNTER_TREND_EXHAUSTION_UPPER_WICK"
                                meta = {"upper_wick": upper_wick, "body": c_body, "atr": atr_v}
                    elif pos_side == "SHORT":
                        # 做空對稱鏡像
                        if curr_quote <= closed_kc_lo or closed_l <= closed_kc_lo:
                            should_exit = True
                            reason = "EXIT_COUNTER_TREND_TP_HIT_KC_LOWER"
                            meta = {"curr_quote": curr_quote, "kc_lower": closed_kc_lo}
                        elif closed_c > prev_closed_h:
                            should_exit = True
                            reason = "EXIT_COUNTER_TREND_BREAK_PREV_HIGH"
                            meta = {"closed_close": closed_c, "prev_high": prev_closed_h}
                        elif closed_c > closed_o and curr_quote < pos_entry * 0.9985:
                            should_exit = True
                            reason = "EXIT_COUNTER_TREND_FIRST_GREEN_CANDLE"
                            meta = {"closed_close": closed_c, "closed_open": closed_o}
                        else:
                            c_body = abs(closed_c - closed_o)
                            lower_wick = min(closed_o, closed_c) - closed_l
                            if lower_wick >= 1.5 * max(c_body, 0.1 * atr_v) and lower_wick >= 0.3 * atr_v and curr_quote < pos_entry:
                                should_exit = True
                                reason = "EXIT_COUNTER_TREND_EXHAUSTION_LOWER_WICK"
                                meta = {"lower_wick": lower_wick, "body": c_body, "atr": atr_v}

            # 🛡️ 【主中軌走平動能枯竭微虧撤退機制已停用】：依使用者最高指示「不要硬止損，只留帳戶硬止損，大瀑布及市場熔斷！」
            # 嚴禁在無利潤/微虧區間因中軌走平而主動割肉平倉！

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

                    # 0. 【V 轉快速平空防線 (V-Reversal Fast Close) 與 趨勢翻多防線】：
                    # 依 2026-10-30 規格升級授權 (Rule 5)：
                    # ⚠️ 【大週期熊市防誤平鐵律】：大週期空頭 (is_zoomed_out_bearish=True) 持空單時，
                    # 1m 小幅反彈過 MA15 一律視為正常洗盤雜訊，嚴禁單純收盤 > MA15 就平空！
                    # 必須同時滿足：①KC 中軌斜率已轉正向上(kc_slope > 0) 且 ②MA3 已站上 MA15(m3_c > m15_c)
                    # 方可判定為真正的牛市反轉，否則 100% 一票否決平空。
                    # 非大週期熊市時（BULLISH or NEUTRAL）：維持原敏感平空邏輯，迅速平空保住獲利。
                    is_fast_v_reversal_close = bool(
                        can_exit_short_on_candle
                        and closed_c > m15_c
                        and curr_quote > m15_c
                        and closed_c > closed_o
                        and (
                            # 大週期熊市：需要KC斜率向上 + MA3站上MA15 雙重確認，防止弱勢反彈誤平空
                            (not is_zoomed_out_bearish)
                            or (
                                is_zoomed_out_bearish
                                and kc_slope > 0
                                and kc_slope_3_ref > 0
                                and m3_c > m15_c
                                and (m3_c - m15_c) >= min_gc_dc_spread
                            )
                        )
                    )
                    is_bullish_trend_reversal = bool(
                        is_fast_v_reversal_close
                        or (
                            can_exit_short_on_candle
                            and (m3_c > m15_c)
                            and (m3_c > m3_p or (m3_c - m15_c) >= min_gc_dc_spread)
                            and (closed_c > closed_o)
                            and (closed_c > closed_kc_mid and closed_c > m15_c)
                            and (curr_quote > m15_c)
                            and (
                                # 大週期熊市：額外要求KC斜率也要向上，才能宣告牛市反轉
                                (not is_zoomed_out_bearish)
                                or (is_zoomed_out_bearish and kc_slope > 0 and kc_slope_3_ref > 0)
                            )
                        )
                    )

                    # 1. 【結構破壞防線】：只認「已收線」實體突破 KC 上軌（盤中上影線毛刺一律不算），或 3棒/5棒中軌與慢線全面轉上且突破前高，且當根非紅K下跌陰線。
                    short_breach_need = 0.5 * atr_v if not is_zoomed_out_bullish else 0.0
                    is_upper_rail_breach = bool(
                        can_exit_short_on_candle
                        and (
                            (closed_c > closed_kc_u + short_breach_need and closed_c > closed_o and curr_quote > kc_u)
                            or (
                                kc_slope > 0
                                and kc_slope_3_ref > 0
                                and kc_slope_5_ref >= 0
                                and m15_c >= m15_p
                                and m15_slope_5_ref >= 0
                                and not is_zoomed_out_bearish
                                and (m3_c > m15_c and (m3_c - m15_c) >= min_gc_dc_spread)
                                and closed_c > closed_o
                                and closed_c > closed_kc_mid
                                and closed_c > m15_c
                                and curr_quote > kc_mid
                                and swing_high_price is not None
                                and closed_c > float(swing_high_price)
                            )
                        )
                    )

                    # 2. 【真谷底確認平空 (True Valley Exit)】：
                    # 嚴格鐵律：大週期空頭未變、3棒/5棒 KC 中軌仍向下 (kc_slope_5_ref < 0)、MA15 慢線仍向下、且價格仍在 KC 下軌外或尚未出現真谷底吞噬時，100% 鎖死不平空！
                    # 但一旦空單已觸及 KC 下軌 (outer_touched_short) 並展開獲利 (peak_roe_pct >= 6.0%)：
                    # (A) 若在下軌附近出現確定性三點谷底反轉（已收陽線收回 KC 下軌內、突破前棒高點或實體中點且站上 MA3 與 MA5，同時 MA3 上拐且當根非紅K下跌陰線），立即在谷底低點獲利平空！
                    # (B) 或已收陽線突破 KC 中軌與 MA15 雙防線且均線金叉轉上。
                    closed_body = abs(closed_c - closed_o)
                    prev_closed_body = abs(prev_closed_c - prev_closed_o)
                    prev_closed_lower_wick = min(prev_closed_o, prev_closed_c) - prev_closed_l
                    prev_closed_mid_short = (prev_closed_o + prev_closed_c) / 2.0
                    ma5_short_ref = float(getattr(market_data, "ma5", m3_c) or m3_c)
                    is_confirmed_true_valley = bool(
                        can_exit_short_on_candle
                        and (has_profit_expansion or favorable_gain_atr >= 1.5 or peak_roe_pct >= 6.0)
                        and (pos_entry <= 0 or curr_quote <= pos_entry * 0.9985)
                        and outer_touched_short
                        and closed_c >= closed_kc_lo * 0.998
                        and closed_c > closed_o
                        and curr_quote >= closed_c * 0.998
                        and closed_c > m3_c
                        and closed_c > ma5_short_ref
                        and curr_quote > m3_c
                        and m3_c > m3_p
                        and (
                            # 路徑 A：下軌真谷底及時止盈（峰值 ROE >= 8%，已收飽滿陽線突破前棒高點與 MA3/MA5 收回軌道內，直接平在谷底最低區！）
                            (
                                peak_roe_pct >= 8.0
                                and (closed_c > prev_closed_h or (closed_c > prev_closed_mid_short and closed_body >= 0.35 * atr_v))
                                and (prev_closed_l <= closed_kc_lo * 1.0015 or closed_l <= closed_kc_lo * 1.0015)
                            )
                            # 路徑 B：中軌結構確認反轉止盈
                            or (
                                (not is_zoomed_out_bearish)
                                and kc_slope > 0
                                and kc_slope_3_ref > 0
                                and kc_slope_5_ref >= 0
                                and m15_c >= m15_p
                                and m15_slope_5_ref >= 0
                                and (m3_c > m15_c and (m3_c - m15_c) >= min_gc_dc_spread)
                                and (closed_c > closed_kc_mid and closed_c > m15_c)
                                and (curr_quote > kc_mid and curr_quote > m15_c)
                                and (closed_c > prev_closed_h or closed_body >= 0.4 * atr_v)
                            )
                        )
                    )

                    # 3. 【大波段移動鎖利 & 保本鎖利防線（嚴格遵守 MTF 情況 A 大週期空頭反彈不平空 & 出紅K絕不平空）】：
                    # (A) 自最低谷底價格 (trough_price) 反彈超過 1.5%~1.8% 且同時突破 MA3/MA5 與 KC 中軌/MA15，且當根非紅K下跌陰線 (can_exit_short_on_candle)，且嚴格維持正淨利區間 (curr_quote <= pos_entry * 0.9985)
                    # (B) 若大週期已非空頭鎖定 (not is_zoomed_out_bearish) 且曾達到 peak_roe_pct >= 8.0%，回吐至中軌上方且當根持續上漲時保本鎖利出場
                    is_valley_rebound_1_8pct = bool(
                        can_exit_short_on_candle
                        and trough_p > 0
                        and pos_entry > 0
                        and curr_quote <= pos_entry * 0.9985
                        and (
                            (
                                ((pos_entry - trough_p) / pos_entry >= 0.015 or peak_roe_pct >= 10.0)
                                and (not is_zoomed_out_bearish or peak_roe_pct >= 15.0)
                                and closed_c > closed_o
                                and closed_c > m3_c
                                and closed_c > ma5_short_ref
                                and curr_quote > m3_c
                                and curr_quote > ma5_short_ref
                                and m3_c > m3_p
                                and (
                                    (closed_c > trough_p * 1.015 and curr_quote > trough_p * 1.015 and closed_c > closed_kc_mid and curr_quote > kc_mid and (not is_zoomed_out_bearish))
                                    or (closed_c > trough_p * 1.018 and curr_quote > trough_p * 1.018 and closed_c > closed_kc_mid and closed_c > m15_c and curr_quote > kc_mid and curr_quote > m15_c)
                                )
                            )
                            or (
                                peak_roe_pct >= 8.0
                                and (not is_zoomed_out_bearish)
                                and (kc_slope > 0 and kc_slope_3_ref > 0 and m15_c >= m15_p and m3_c > m15_c)
                                and closed_c > closed_o
                                and closed_c > closed_kc_mid
                                and closed_c > m15_c
                                and curr_quote > kc_mid
                                and curr_quote > m15_c
                            )
                        )
                    )

                    # 4. 【妖幣專屬插針止損已全面停用】：依使用者最高指示「不要硬止損，只留帳戶硬止損，大瀑布及市場熔斷！」
                    is_hard_sl_short = False

                    # ⚠️ 順勢下殺嚴禁提前平空鐵律 (2026-10-30 核心授權)：
                    # 在大週期下殺段中，只要價格持續受壓於 MA15 下方 (closed_c < m15_c 且 curr_quote < m15_c)，
                    # 中間所有微幅觸碰中軌的小陽線一律視為誘多，嚴禁發出任何 CLOSE_SHORT 指令，必須抱緊空單直至底部結構破壞！
                    is_under_ma15_short_trend = bool(closed_c < m15_c and curr_quote < m15_c)
                    if is_under_ma15_short_trend and (not is_confirmed_true_valley) and (not is_hard_sl_short):
                        logger.info(
                            f"🛡️ [UNDER_MA15_DOWN_HOLD] {symbol} 順勢下殺段受壓於 MA15 (close={closed_c:.6f} < m15={m15_c:.6f})，"
                            f"過中軌小陽線一律視為誘多雜訊，100% 嚴禁平空，死抱空單直達底部！"
                        )
                        return {"status": "HOLD", "symbol": symbol, "reason": "UNDER_MA15_DOWN_HOLD"}

                    # ⚠️ 方案 B 鐵律 (2026-10-30 核心授權)：
                    # 如果大週期依然是熊市 (is_zoomed_out_bearish=True)，價格反彈衝到 KC 上軌或均線金叉只是「反彈回抽/洗盤」，
                    # 100% 一票否決平空，讓空單繼續抱到底，杜絕割肉在反彈高點！
                    if is_zoomed_out_bearish:
                        is_upper_rail_breach = False
                        is_bullish_trend_reversal = False
                        is_fast_v_reversal_close = False
                        is_valley_rebound_1_8pct = False

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

                    # 0. 【趨勢正式變空頭及時平多防線 (Bearish Trend Reversal) 與 跌破 MA15 快速平多】：
                    # 依 2026-10-30 規格升級授權 (Rule 5)：
                    # ⚠️ 【大週期牛市防誤平鐵律】：大週期多頭 (is_zoomed_out_bullish=True) 持多單時，
                    # 1m 小幅回踩跌破 MA15 一律視為正常牛市洗盤，嚴禁單純收盤 < MA15 就平多！
                    # 必須同時滿足：①KC 中軌斜率已轉負向下(kc_slope < 0) 且 ②MA3 已跌破 MA15(m3_c < m15_c)
                    # 方可判定為真正的熊市反轉，否則 100% 一票否決平多。
                    # 非大週期牛市時（BEARISH or NEUTRAL）：維持原敏感平多邏輯，迅速平多保住獲利。
                    is_fast_bear_reversal_close = bool(
                        can_exit_long_on_candle
                        and closed_c < m15_c
                        and curr_quote < m15_c
                        and closed_c < closed_o
                        and (
                            # 大週期牛市：需要KC斜率向下 + MA3跌破MA15 雙重確認，防止正常回踩誤平多
                            (not is_zoomed_out_bullish)
                            or (
                                is_zoomed_out_bullish
                                and kc_slope < 0
                                and kc_slope_3_ref < 0
                                and m3_c < m15_c
                                and (m15_c - m3_c) >= min_gc_dc_spread
                            )
                        )
                    )
                    is_bearish_trend_reversal = bool(
                        is_fast_bear_reversal_close
                        or (
                            can_exit_long_on_candle
                            and (m3_c < m15_c)
                            and (m3_c < m3_p or (m15_c - m3_c) >= min_gc_dc_spread)
                            and (closed_c < closed_o)
                            and (closed_c < closed_kc_mid and closed_c < m15_c)
                            and (curr_quote < m15_c)
                            and (
                                # 大週期牛市：額外要求KC斜率也要向下，才能宣告熊市反轉
                                (not is_zoomed_out_bullish)
                                or (is_zoomed_out_bullish and kc_slope < 0 and kc_slope_3_ref < 0)
                            )
                        )
                    )

                    # 1. 【結構破壞防線】：只認「已收線」實體跌破 KC 下軌（盤中下影線毛刺一律不算），或 3棒/5棒中軌與慢線全面轉下且跌破前低，且當根非綠K上漲陽線。
                    long_breach_need = 0.5 * atr_v if not is_zoomed_out_bearish else 0.0
                    is_lower_rail_breach = bool(
                        can_exit_long_on_candle
                        and (
                            (closed_c < closed_kc_lo - long_breach_need and closed_c < closed_o and curr_quote < kc_lo)
                            or (
                                kc_slope < 0
                                and kc_slope_3_ref < 0
                                and kc_slope_5_ref <= 0
                                and m15_c <= m15_p
                                and m15_slope_5_ref <= 0
                                and not is_zoomed_out_bullish
                                and (m3_c < m15_c and (m15_c - m3_c) >= min_gc_dc_spread)
                                and closed_c < closed_o
                                and closed_c < closed_kc_mid
                                and closed_c < m15_c
                                and curr_quote < kc_mid
                                and swing_low_price is not None
                                and closed_c < float(swing_low_price)
                            )
                        )
                    )

                    # 2. 【真峰頂確認平多 (True Peak Exit)】：
                    # 嚴格鐵律：大週期多頭未變、3棒/5棒 KC 中軌仍向上 (kc_slope_5_ref > 0)、MA15 慢線仍向上、且價格仍在 KC 上軌外或尚未出現真峰頂吞噬時（如龍蝦 21:13 回踩中軌但 KC 中軌仍向上且 MA3 > MA15），
                    # 100% 鎖死不平多！
                    # 但一旦多單已觸及 KC 上軌 (outer_touched_long) 並展開獲利 (peak_roe_pct >= 6.0%)：
                    # (A) 若在上軌附近出現確定性三點峰頂反轉（已收陰線跌回 KC 上軌內、跌破前棒低點或實體中點且跌破 MA3 與 MA5，同時 MA3 下拐且當根非綠K上漲陽線），立即在峰頂高點獲利平多！
                    # (B) 或已收陰線跌破 KC 中軌與 MA15 雙防線且均線死叉轉下。
                    closed_body = abs(closed_c - closed_o)
                    prev_closed_body = abs(prev_closed_c - prev_closed_o)
                    prev_closed_upper_wick = prev_closed_h - max(prev_closed_o, prev_closed_c)
                    prev_closed_mid_long = (prev_closed_o + prev_closed_c) / 2.0
                    ma5_long_ref = float(getattr(market_data, "ma5", m3_c) or m3_c)
                    is_confirmed_true_peak = bool(
                        can_exit_long_on_candle
                        and (has_profit_expansion or favorable_gain_atr >= 1.5 or peak_roe_pct >= 6.0)
                        and (pos_entry_price <= 0 or curr_quote >= pos_entry_price * 1.0015)
                        and outer_touched_long
                        and closed_c <= closed_kc_u * 1.002
                        and closed_c < closed_o
                        and curr_quote <= closed_c * 1.002
                        and closed_c < m3_c
                        and closed_c < ma5_long_ref
                        and curr_quote < m3_c
                        and m3_c < m3_p
                        and (
                            # 路徑 A：上軌真峰頂及時止盈（峰值 ROE >= 8%，已收飽滿陰線跌破前棒低點與 MA3/MA5 跌回軌道內，直接平在峰頂最高區！）
                            (
                                peak_roe_pct >= 8.0
                                and (closed_c < prev_closed_l or (closed_c < prev_closed_mid_long and closed_body >= 0.35 * atr_v))
                                and (prev_closed_h >= closed_kc_u * 0.9985 or closed_h >= closed_kc_u * 0.9985)
                            )
                            # 路徑 B：中軌結構確認反轉止盈
                            or (
                                (not is_zoomed_out_bullish)
                                and kc_slope < 0
                                and kc_slope_3_ref < 0
                                and kc_slope_5_ref <= 0
                                and m15_c <= m15_p
                                and m15_slope_5_ref <= 0
                                and (m3_c < m15_c and (m15_c - m3_c) >= min_gc_dc_spread)
                                and (closed_c < closed_kc_mid and closed_c < m15_c)
                                and (curr_quote < kc_mid and curr_quote < m15_c)
                                and (closed_c < prev_closed_l or closed_body >= 0.4 * atr_v)
                            )
                        )
                    )

                    # 3. 【大波段移動鎖利 & 保本鎖利防線（嚴格遵守 MTF 情況 C 大週期多頭回檔不平多 & 出綠K絕不平多）】：
                    # (A) 盈利達標後，自最高點回撤超過 1.5%~1.8% 且同時跌破 MA3/MA5 與 KC 中軌/MA15，且當根非綠K上漲陽線 (can_exit_long_on_candle)，且嚴格維持正淨利區間 (curr_quote >= pos_entry_price * 1.0015)
                    # (B) 若大週期已非多頭鎖定 (not is_zoomed_out_bullish) 且曾達到 peak_roe_pct >= 8.0%，回吐至中軌下方且當根持續下跌時保本鎖利出場
                    is_peak_pullback_2pct = bool(
                        can_exit_long_on_candle
                        and peak_p > 0
                        and pos_entry_price > 0
                        and curr_quote >= pos_entry_price * 1.0015
                        and (
                            (
                                ((peak_p - pos_entry_price) / pos_entry_price >= 0.015 or peak_roe_pct >= 10.0)
                                and (not is_zoomed_out_bullish or peak_roe_pct >= 15.0)
                                and closed_c < closed_o
                                and closed_c < m3_c
                                and closed_c < ma5_long_ref
                                and curr_quote < m3_c
                                and curr_quote < ma5_long_ref
                                and m3_c < m3_p
                                and (
                                    (closed_c < peak_p * 0.985 and curr_quote < peak_p * 0.985 and closed_c < closed_kc_mid and curr_quote < kc_mid and (not is_zoomed_out_bullish))
                                    or (closed_c < peak_p * 0.982 and curr_quote < peak_p * 0.982 and closed_c < closed_kc_mid and closed_c < m15_c and curr_quote < kc_mid and curr_quote < m15_c)
                                )
                            )
                            or (
                                peak_roe_pct >= 8.0
                                and (not is_zoomed_out_bullish)
                                and (kc_slope < 0 and kc_slope_3_ref < 0 and m15_c <= m15_p and m3_c < m15_c)
                                and closed_c < closed_o
                                and closed_c < closed_kc_mid
                                and closed_c < m15_c
                                and curr_quote < kc_mid
                                and curr_quote < m15_c
                            )
                        )
                    )

                    # 4. 【妖幣專屬插針止損已全面停用】：依使用者最高指示「不要硬止損，只留帳戶硬止損，大瀑布及市場熔斷！」
                    is_hard_sl_long = False

                    # ⚠️ 順勢拉升嚴禁提前平多鐵律 (2026-10-30 核心授權)：
                    # 在大週期拉升段中，只要價格持續穩守於 MA15 上方 (closed_c > m15_c 且 curr_quote > m15_c)，
                    # 中間所有微幅觸碰中軌的小陰線一律視為正常洗盤，嚴禁發出任何 CLOSE_LONG 指令，必須抱緊多單直至頂部結構破壞！
                    is_above_ma15_long_trend = bool(closed_c > m15_c and curr_quote > m15_c)
                    if is_above_ma15_long_trend and (not is_confirmed_true_peak) and (not is_hard_sl_long):
                        logger.info(
                            f"🛡️ [ABOVE_MA15_UP_HOLD] {symbol} 順勢拉升段穩守於 MA15 (close={closed_c:.6f} > m15={m15_c:.6f})，"
                            f"過中軌小陰線一律視為誘空雜訊，100% 嚴禁平多，死抱多單直達頂部！"
                        )
                        return {"status": "HOLD", "symbol": symbol, "reason": "ABOVE_MA15_UP_HOLD"}

                    # ⚠️ 方案 B 鐵律 (2026-10-30 核心授權)：
                    # 如果大週期依然是牛市 (is_zoomed_out_bullish=True)，價格回踩跌破 KC 下軌或均線死叉只是「正常呼吸回踩/洗盤」，
                    # 100% 一票否決平多，讓多單繼續抱到底，杜絕割肉在回調低點！
                    if is_zoomed_out_bullish:
                        is_lower_rail_breach = False
                        is_bearish_trend_reversal = False
                        is_fast_bear_reversal_close = False
                        is_peak_pullback_2pct = False

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

            # 🛡️ 【持倉呼吸與結構破位鐵律（落實 2026-10-31/11-01 最新平倉硬性條件）】：
            # 1. 強勢續抱區：多單守在 MA15 與 KC 中軌之上、空單壓在 MA15 與 KC 中軌之下時，堅定續抱！
            # 2. 成本區間正常回踩：只有在價格守穩 MA15/KC 中軌同側時的小陰小陽，方視為呼吸回踩。
            # 3. 破位平倉鐵律（防洗盤：回踩不要平倉！）：
            #    - 普通小回踩（實體 < 0.7 ATR 或尚未實質獲利展開催生真峰谷時）絕不提早割肉！
            #    - 只有在收盤實體明確貫穿破位（實體 >= 0.7 ATR）或真峰谷確立時才放行 EXIT_BY_TREND_LINE_BROKEN！
            is_counter_reversal_bar = bool(
                (pos_side == "LONG" and (closed_o - closed_c >= 0.70 * atr_v or curr_quote < m15_c - 0.20 * atr_v))
                or (pos_side == "SHORT" and (closed_c - closed_o >= 0.70 * atr_v or curr_quote > m15_c + 0.20 * atr_v))
            )
            is_line_broken = bool(
                is_counter_reversal_bar
                and (
                    (pos_side == "LONG" and (closed_c < m15_c or closed_c < closed_kc_mid) and (m3_c <= m3_p or macro_trend == "BEARISH"))
                    or (pos_side == "SHORT" and (closed_c > m15_c or closed_c > closed_kc_mid) and (m3_c >= m3_p or macro_trend == "BULLISH"))
                )
            )

            if is_line_broken:
                should_exit = True
                reason = "EXIT_BY_TREND_LINE_BROKEN"
                meta = {
                    **(meta or {}),
                    "cause": f"{pos_side} 觸發硬性線型結構實體破位 (close={closed_c:.6f}, ma15={m15_c:.6f}, mid={closed_kc_mid:.6f}, macro={macro_trend})，及時離場避險或翻單！",
                }

            if should_exit and not is_absolute_safety and not is_line_broken:
                is_stagnation_exit = bool(reason == "EXIT_BY_KC_MIDDLE_FLAT_EXHAUSTION")
                if not is_stagnation_exit:
                    is_profit_unexpanded = bool(peak_roe_pct < 5.0 and favorable_gain_atr < 0.5)
                    current_loss_pct = ((pos_entry - curr_quote) / pos_entry) if (pos_side == "LONG" and pos_entry > 0) else (((curr_quote - pos_entry) / pos_entry) if (pos_side == "SHORT" and pos_entry > 0) else 0.0)
                    is_within_normal_breathing = bool(current_loss_pct < 0.010 and (closed_c >= m15_c if pos_side == "LONG" else closed_c <= m15_c))
                    if is_profit_unexpanded and is_within_normal_breathing:
                        logger.info(
                            f"🛡️ [VETO_PULLBACK_HOLD] {symbol} 守穩 MA15 成本區正常呼吸回踩，一票否決平倉請求 ({reason})！"
                        )
                        return {"status": "HOLD", "symbol": symbol, "reason": "VETO_HOLD_COST_PULLBACK_BREATHING"}

                    # 守穩結構續抱 (依 2026-11-01 最新指示：一旦確立真峰谷或出現大長K/動能衰竭/反轉吞噬，一票否決續抱，立即平倉獲利了結)
                    is_structure_intact = bool(
                        (pos_side == "LONG" and (closed_c >= m15_c or closed_c >= closed_kc_mid) and peak_roe_pct < 15.0)
                        or (pos_side == "SHORT" and (closed_c <= m15_c or closed_c <= closed_kc_mid) and peak_roe_pct < 15.0)
                    )
                    # ⚠️ 依據使用者鐵律：「真峰谷確立或反轉異常大K出現時，一票否決健康回踩/結構續抱，絕不允許坐過山車回到成本價」
                    if is_structure_intact and reason in ("EARLY_PIVOT_PEAK_EXIT", "EARLY_PIVOT_VALLEY_EXIT", "CLOSE_LONG_AT_TOP", "CLOSE_SHORT_AT_BOTTOM"):
                        # 若出現大長K吞噬反轉 (實體 >= 0.7 ATR) 或波段已展開反轉，絕不否決，立即平倉！
                        is_strong_reversal_bar = bool(
                            (pos_side == "LONG" and (closed_o - closed_c >= 0.7 * atr_v or closed_c < m3_c))
                            or (pos_side == "SHORT" and (closed_c - closed_o >= 0.7 * atr_v or closed_c > m3_c))
                        )
                        if not is_strong_reversal_bar:
                            logger.info(
                                f"🛡️ [VETO_STRUCTURE_INTACT_HOLD] {symbol} {pos_side} 價格仍守穩於 MA15/KC中軌同側 (close={closed_c:.6f})，"
                                f"趨勢結構健全，一票否決微型峰谷平倉 ({reason})，堅定續抱主升/主跌段！"
                            )
                            return {"status": "HOLD", "symbol": symbol, "reason": "VETO_HOLD_STRUCTURE_INTACT"}

            # 🛑 開倉當根一般出口仍防洗；帳戶硬止損、瀑布、熔斷等絕對安全出口不受此限制。
            from core.symbol_aliases import is_meme_breakout_symbol
            if should_exit and not is_meme_breakout_symbol(symbol):
                from core.gates.holding_protection_gate import is_same_bar_as_entry
                ma3_m15_cross_8 = 0
                if isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 15:
                    try:
                        closes = raw_df["close"].astype(float)
                        fast = raw_df["ma3"].astype(float) if "ma3" in raw_df else closes.rolling(3, min_periods=1).mean()
                        slow = raw_df["ma15"].astype(float) if "ma15" in raw_df else closes.rolling(15, min_periods=1).mean()
                        signs = (fast - slow).iloc[-8:].apply(lambda value: 1 if value >= 0 else -1)
                        ma3_m15_cross_8 = int((signs.diff().abs() > 0).sum())
                    except (KeyError, TypeError, ValueError, OverflowError) as exc:
                        logger.warning("EXIT_MA3_CROSS_DATA_INVALID symbol=%s error=%s", symbol, exc)
                        ma3_m15_cross_8 = 0
                from core.gates.master_gate_funnel import MasterGateFunnel, MarketSnapshot, PositionSnapshot

                is_entry_same_bar = is_same_bar_as_entry(current_position, getattr(market_data, "raw_df", None))
                if is_entry_same_bar and not is_absolute_safety and not is_profit_lock and not is_pivot_exit:
                    logger.info(
                        f"🛡️ [VETO_SAME_BAR_HARD_SL] {symbol} 處於開倉進場當根 K 棒 (Same-Bar)，"
                        f"一票否決當根硬止損與提前平倉請求 ({reason})，堅決持倉防洗！"
                    )
                    return {"status": "HOLD", "symbol": symbol, "reason": "VETO_SAME_BAR_HOLD"}

                # 透過 MasterGateFunnel 審查平倉請求（含最低持倉保護、動能寬容度 Exit Buffer 與紅綠K禁令）
                p_snap = PositionSnapshot(
                    symbol=symbol,
                    side=pos_side,
                    entry_price=pos_entry,
                    open_timestamp_ms=pos_open_ts * 1000.0 if pos_open_ts < 1e11 else pos_open_ts,
                    bars_held=int(getattr(market_data, "bars_held", 0) or 0),
                    peak_roe_pct=peak_roe_pct,
                    has_confirmed_pivot=bool(is_pivot_exit),
                    lowest_price_since_entry=float(current_position.get("lowest_price") or current_position.get("trough_price") or pos_entry),
                    highest_price_since_entry=float(current_position.get("highest_price") or pos_entry),
                )
                m_snap_exit = MarketSnapshot(
                    symbol=symbol,
                    quote=curr_quote,
                    open_price=c_o,
                    close_price=c_c,
                    high_price=c_h,
                    low_price=c_l,
                    ma3_curr=m3_c,
                    ma3_prev=m3_p,
                    ma5_curr=float(market_data.curr_k.get("ma5", m3_c) or m3_c),
                    ma5_prev=float(market_data.prev_k.get("ma5", m3_p) or m3_p),
                    ma15_curr=m15_c,
                    ma15_prev=m15_p,
                    kc_upper=kc_u,
                    kc_middle=kc_mid,
                    kc_middle_prev=p_kc_mid,
                    kc_lower=kc_lo,
                    atr=atr_v,
                    macro_trend=macro_trend,
                    ma_cross_count_8=ma3_m15_cross_8,
                )
                exit_res = MasterGateFunnel.evaluate_position_exit(
                    p_snap, m_snap_exit, candidate_exit_reason=reason, current_time_ms=time.time() * 1000.0,
                )
                if not exit_res.can_execute:
                    logger.info(f"🛡️ [MASTER_GATE_EXIT_VETO] {symbol} 駁回平倉請求: {exit_res.reason}")
                    return {"status": "HOLD", "symbol": symbol, "reason": exit_res.reason}

            # 🛑 【終極防呆總閘門：出紅K（下跌陰線）100% 嚴禁平空、出綠K（上漲陽線）100% 嚴禁平多】：
            if should_exit:
                if pos_side == "SHORT" and not can_exit_short_on_candle:
                    logger.info(
                        f"🛑 [VETO_EXIT_SHORT_ON_RED_CANDLE] {symbol} 攔截紅K（下跌陰線）平空請求 ({reason}): "
                        f"curr_quote={curr_quote:.6f}, c_o={c_o:.6f}, closed_c={closed_c:.6f}, closed_o={closed_o:.6f} ➔ 空頭正在順勢下殺，強制續抱！"
                    )
                    return {"status": "HOLD", "symbol": symbol, "reason": "VETO_EXIT_SHORT_ON_RED_BEARISH_CANDLE"}
                if pos_side == "LONG" and not can_exit_long_on_candle:
                    logger.info(
                        f"🛑 [VETO_EXIT_LONG_ON_GREEN_CANDLE] {symbol} 攔截綠K（上漲陽線）平多請求 ({reason}): "
                        f"curr_quote={curr_quote:.6f}, c_o={c_o:.6f}, closed_c={closed_c:.6f}, closed_o={closed_o:.6f} ➔ 多頭正在順勢上攻，強制續抱！"
                    )
                    return {"status": "HOLD", "symbol": symbol, "reason": "VETO_EXIT_LONG_ON_GREEN_BULLISH_CANDLE"}

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
                    or (c_c > m15_c and c_c > c_o)
                    or "CLOSE_SHORT_AT_BOTTOM" in str(reason).upper()
                    or "VALLEY" in str(reason).upper()
                    or "BULLISH_TREND_REVERSAL" in str(reason).upper()
                    or "EXIT_SHORT_TREND_REVERSAL" in str(reason).upper()
                    or "TREND_REVERSAL" in str(reason).upper()
                    or "V_REVERSAL" in str(reason).upper()
                    or "BOTTOM_GREEN_CANDLE" in str(reason).upper()
                    or "BOTTOM" in str(reason).upper()
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
                    or (c_c < m15_c and c_c < c_o)
                    or "CLOSE_LONG_AT_TOP" in str(reason).upper()
                    or "PEAK" in str(reason).upper()
                    or "BEARISH_TREND_REVERSAL" in str(reason).upper()
                    or "EXIT_LONG_TREND_REVERSAL" in str(reason).upper()
                    or "TREND_REVERSAL" in str(reason).upper()
                )
                is_flip_to_short = bool(pos_side == "LONG" and is_real_peak)

                # 🟢 斷頭與谷底轉折反手授權（Position Flip）：
                # 當收線出現確定性真峰谷或結構貫穿反轉時，授權秒級反手，但必須嚴格遵守 KC 中軌順勢鐵律！
                from core.symbol_aliases import is_meme_breakout_symbol
                if is_flip_to_long and kc_slope <= 0:
                    logger.info(f"🚫 [BLOCKED_FLIP_LONG_ON_BEARISH_TREND] {symbol} KC 中軌下行或走平 (kc_slope={kc_slope:.6f})，嚴禁平空後逆勢反手開多！")
                    is_flip_to_long = False
                if is_flip_to_short and kc_slope >= 0:
                    logger.info(f"🚫 [BLOCKED_FLIP_SHORT_ON_BULLISH_TREND] {symbol} KC 中軌上行或走平 (kc_slope={kc_slope:.6f})，嚴禁平多後逆勢反手開空！")
                    is_flip_to_short = False
                can_flip = is_meme_breakout_symbol(symbol) and (is_flip_to_long or is_flip_to_short)

                if can_flip and (is_flip_to_long or is_flip_to_short):
                    flip_side = "LONG" if is_flip_to_long else "SHORT"
                    flip_entry_type = "PIVOT_FLIP_LONG" if is_flip_to_long else "PIVOT_FLIP_SHORT"
                    logger.info(f"🔄 [POSITION_FLIP] {symbol} {pos_side} 平倉後同 K 棒即刻秒級反手開 {flip_side}！(entry_type={flip_entry_type})")
                    print(f"🔄 [POSITION_FLIP] {symbol} {pos_side} 平倉後同 K 棒即刻秒級反手開 {flip_side}！", flush=True)

                    flip_stop_loss = None  # 方案 B 無硬止損版: 完全取消固定價位硬止損 (No Rigid Stop Loss)
                    flip_action_name = "CLOSE_SHORT_AND_OPEN_LONG" if flip_side == "LONG" else "CLOSE_LONG_AND_OPEN_SHORT"
                    flip_meta = {
                        "side": flip_side,
                        "direction_override": True,
                        "entry_type": flip_entry_type,
                        "candidate_signal_type": "ENTRY_LONG_MA5_MA15_CROSS_MID" if flip_side == "LONG" else "ENTRY_SHORT_MA5_MA15_CROSS_MID",
                        "is_swing_pivot": False,
                        "is_flip": True,
                        "is_agile_trigger": True,
                        "is_cross_mid": True,
                        "no_rigid_sl": True,
                        "stop_loss_price": None,
                        "upstream_entry_gate_authorized": True,
                        "allow_kc_internal": True,
                        "strategy_mode": "AGILE_MOMENTUM",
                        "raw_df": klines_df,
                        "source": f"UNIFIED_PIPELINE|{flip_action_name}",
                        "atr": atr_v,
                    }
                    raw_df_instance = flip_meta.get("raw_df")
                    if hasattr(raw_df_instance, "attrs"):
                        for k in ("direction_override", "is_agile_trigger", "is_cross_mid", "entry_type", "strategy_mode", "allow_kc_internal", "is_flip"):
                            raw_df_instance.attrs[k] = flip_meta.get(k, True)

                    flip_success = await cls.open_position(
                        engine_or_account=engine_or_account,
                        symbol=symbol,
                        side=flip_side,
                        price=exit_price,
                        reason=f"FlipPosition:{flip_action_name}",
                        meta=flip_meta,
                    )
                    if flip_success:
                        logger.info(f"🎯 [POSITION_FLIP_SUCCESS] {symbol} 成功秒級反手建立 {flip_side} 倉位！")
                        print(f"🎯 [POSITION_FLIP_SUCCESS] {symbol} 成功秒級反手建立 {flip_side} 倉位！", flush=True)
                        return {"status": "SUCCESS_FLIP", "symbol": symbol, "side": flip_side}

                # 🛑 2026-10-30 核心鐵律：平倉離場後【絕對禁止自動重新開倉】，必須等待系統下一次標準開倉訊號！
                if any(k in str(reason).upper() for k in ("STAGE", "TRAILING", "LADDER", "BREAKEVEN", "PROFIT_LOCK", "REVERSAL", "PULLBACK")):
                    logger.info(f"🛑 [EXIT_COMPLETE_NO_REOPEN] {symbol} 平倉離場成功，依紀律絕不無腦自動重新開倉，等待下一週期標準訊號！")
                    return {"status": "EXIT_EXECUTED", "symbol": symbol, "side": pos_side, "reason": reason}

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
        # 優先級 3：MA5 穿 KC 中軌 / MA5 與 MA15 交叉動能開倉
        # 優先級 4：順勢通道延續開倉
        # =========================================================================
        method_rejection: Optional[Dict[str, Any]] = None
        if not has_position:
            state = getattr(account, "entry_filter_last", None)
            pending_tickets = (
                state.get("dual_trend_reversal_pending")
                if isinstance(state, dict) else None
            )
            pending_ticket = (
                pending_tickets.get(symbol)
                if isinstance(pending_tickets, dict) else None
            )
            if pending_ticket is not None:
                from core.services.exits.authorized_position_exit import (
                    dual_trend_exit_block_reason,
                )

                source_position = pending_ticket.get("source_position")
                source_meta = pending_ticket.get("source_position_meta")
                if (
                    not isinstance(source_position, dict)
                    or str(source_position.get("side") or "").upper() not in ("LONG", "SHORT")
                ):
                    logger.error(
                        "DUAL_TREND_REVERSAL_TICKET_INVALID symbol=%s",
                        symbol,
                    )
                    if not cls._clear_dual_trend_reversal_ticket(account, symbol):
                        return {
                            "status": "DUAL_TREND_REVERSAL_STATE_ERROR",
                            "symbol": symbol,
                            "reason": "INVALID_TICKET_CLEAR_FAILED",
                        }
                    pending_ticket = None
                else:
                    trend_block = dual_trend_exit_block_reason(
                        source_position,
                        raw_df,
                        source_meta if isinstance(source_meta, dict) else None,
                    )
                    if trend_block in (
                        "VETO_EXIT_KC_TREND_NOT_REVERSED",
                        "VETO_EXIT_MACRO_TREND_NOT_REVERSED",
                    ):
                        if not cls._clear_dual_trend_reversal_ticket(account, symbol):
                            return {
                                "status": "DUAL_TREND_REVERSAL_STATE_ERROR",
                                "symbol": symbol,
                                "reason": "REVERSAL_INVALIDATION_PERSISTENCE_FAILED",
                            }
                        pending_ticket = None
                    elif trend_block is not None:
                        return {
                            "status": "DUAL_TREND_REVERSAL_WAITING_DATA",
                            "symbol": symbol,
                            "reason": trend_block,
                        }
                    else:
                        flip = await cls._try_structure_position_flip(
                            account=account,
                            symbol=symbol,
                            position_side=source_position["side"],
                            frame=raw_df,
                            quote=curr_quote,
                            macro_trend=getattr(market_data, "macro_trend", None) or macro_trend,
                            exit_reason=str(
                                pending_ticket.get("exit_reason") or "DUAL_TREND_REVERSAL"
                            ),
                            position=source_position,
                            position_meta=source_meta if isinstance(source_meta, dict) else None,
                        )
                        if flip and flip.get("success"):
                            if not cls._clear_dual_trend_reversal_ticket(account, symbol):
                                logger.critical(
                                    "DUAL_TREND_REVERSAL_TICKET_CLEAR_FAILED_AFTER_OPEN symbol=%s",
                                    symbol,
                                )
                            return {
                                "status": "SUCCESS_FLIP",
                                "symbol": symbol,
                                "side": flip["side"],
                                "reason": flip["reason"],
                                "success": True,
                            }
                        return {
                            "status": "DUAL_TREND_REVERSAL_PENDING",
                            "symbol": symbol,
                            "reason": (
                                flip.get("reason") if isinstance(flip, dict)
                                else "REVERSAL_ENTRY_NOT_AUTHORIZED"
                            ),
                        }

            acc_key_watch = cls._get_account_key(account, symbol)
            active_watch = cls._pv_flip_watch_records.get(acc_key_watch)
            if active_watch and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 3:
                from core.services.low_latency_exit import (
                    build_live_flip_frame,
                    evaluate_flip_watch_entry,
                    extract_entry_structure_context,
                    resolve_peak_valley_exit_config,
                )

                watch_df_5m = frame_5m
                if watch_df_5m is None and hasattr(engine_or_account, "fetch_klines"):
                    try:
                        watch_df_5m = await engine_or_account.fetch_klines(
                            symbol, timeframe="5m", limit=50, keep_live=True,
                        )
                    except Exception:
                        watch_df_5m = None

                watch_cfg = resolve_peak_valley_exit_config(symbol)
                watch_eval = evaluate_flip_watch_entry(
                    raw_df,
                    watch_state=active_watch,
                    live_price=curr_quote,
                    df_5m=watch_df_5m,
                    symbol=symbol,
                    config=watch_cfg,
                )
                if watch_eval.get("expired"):
                    cls._pv_flip_watch_records.pop(acc_key_watch, None)
                    logger.info(
                        "⌛ [PV_FLIP_WATCH_EXPIRED] %s side=%s reason=%s elapsed=%s/%s",
                        symbol,
                        watch_eval.get("side"),
                        watch_eval.get("reason"),
                        watch_eval.get("elapsed_bars"),
                        watch_eval.get("window_bars"),
                    )
                elif watch_eval.get("should_enter") and watch_eval.get("side") in ("LONG", "SHORT"):
                    flip_side = str(watch_eval["side"]).upper()
                    live_flip_df = build_live_flip_frame(raw_df, float(curr_quote))
                    flip_struct = extract_entry_structure_context(live_flip_df, flip_side, config=watch_cfg)
                    current_watch_bar_id = watch_eval.get("current_bar_id")
                    flip_meta: Dict[str, Any] = {
                        "symbol": symbol,
                        "side": flip_side,
                        "direction_override": True,
                        "is_agile_trigger": True,
                        "entry_type": f"PEAK_VALLEY_FLIP_{flip_side}",
                        "candidate_signal_type": f"PEAK_VALLEY_FLIP_{flip_side}",
                        "is_peak_valley_inflection": True,
                        "is_flip": True,
                        "is_pv_flip": True,
                        "pv_flip_bar_id": current_watch_bar_id,
                        "pv_flip_origin_exit_reason": active_watch.get("origin_exit_reason"),
                        "pv_flip_watch_state": dict(active_watch),
                        "frame_5m": watch_df_5m,
                        "entry_swing_low": flip_struct.get("entry_swing_low"),
                        "entry_swing_high": flip_struct.get("entry_swing_high"),
                        "entry_kc_width_pct": flip_struct.get("entry_kc_width_pct"),
                        "entry_pattern": "REVERSAL",
                        "is_swing_pivot": False,
                        "is_cross_mid": True,
                        "no_rigid_sl": True,
                        "stop_loss_price": None,
                        "upstream_entry_gate_authorized": True,
                        "intrabar_authorized": not bool(live_flip_df.iloc[-1].get("is_closed", True)),
                        "allow_kc_internal": True,
                        "strategy_mode": "AGILE_MOMENTUM",
                        "raw_df": live_flip_df,
                        "macro_trend": macro_trend,
                        "source": f"UNIFIED_PIPELINE|PEAK_VALLEY_FLIP_{flip_side}",
                        "atr": atr_v,
                    }
                    if hasattr(live_flip_df, "attrs"):
                        from core.services.low_latency_exit import wrap_frame_ref as _wrap_frame_ref
                        live_flip_df.attrs["symbol"] = symbol
                        for k in (
                            "direction_override",
                            "is_agile_trigger",
                            "is_cross_mid",
                            "entry_type",
                            "strategy_mode",
                            "allow_kc_internal",
                            "is_peak_valley_inflection",
                            "is_pv_flip",
                            "pv_flip_bar_id",
                            "pv_flip_origin_exit_reason",
                            "pv_flip_watch_state",
                            "frame_5m",
                        ):
                            if flip_meta.get(k) is not None:
                                v_attr = flip_meta.get(k)
                                live_flip_df.attrs[k] = _wrap_frame_ref(v_attr) if k == "frame_5m" else v_attr

                    flip_opened = await cls.open_position(
                        engine_or_account=engine_or_account,
                        symbol=symbol,
                        side=flip_side,
                        price=float(curr_quote),
                        reason=f"PV_FLIP_WATCH:{watch_eval['reason']}",
                        meta=flip_meta,
                    )
                    if flip_opened:
                        cls._pv_flip_watch_records.pop(acc_key_watch, None)
                        if current_watch_bar_id is not None:
                            cls._pv_flip_records[acc_key_watch] = {
                                "flip_bar_id": int(current_watch_bar_id),
                                "flip_side": flip_side,
                                "origin_exit_reason": active_watch.get("origin_exit_reason"),
                                "timestamp": time.time(),
                            }
                        pos_dict = getattr(account, "positions", None)
                        if isinstance(pos_dict, dict) and symbol in pos_dict and isinstance(pos_dict[symbol], dict):
                            pos_dict[symbol]["pv_flip_bar_id"] = current_watch_bar_id
                            pos_dict[symbol]["is_pv_flip"] = True
                            pos_dict[symbol]["pv_flip_origin_exit_reason"] = active_watch.get("origin_exit_reason")
                        pm_dict = getattr(account, "position_meta", None)
                        if isinstance(pm_dict, dict) and symbol in pm_dict and isinstance(pm_dict[symbol], dict):
                            pm_dict[symbol]["pv_flip_bar_id"] = current_watch_bar_id
                            pm_dict[symbol]["is_pv_flip"] = True
                            pm_dict[symbol]["pv_flip_origin_exit_reason"] = active_watch.get("origin_exit_reason")
                        logger.info(
                            "🎯 [PV_FLIP_WATCH_CONFIRMED] %s 開出結構確認反手單 side=%s elapsed=%s/%s @ %s",
                            symbol,
                            flip_side,
                            watch_eval.get("elapsed_bars"),
                            watch_eval.get("window_bars"),
                            curr_quote,
                        )
                        return {
                            "status": "FLIP_WATCH_ENTRY_EXECUTED",
                            "symbol": symbol,
                            "side": flip_side,
                            "reason": watch_eval.get("reason"),
                            "flip_watch": watch_eval,
                            "success": True,
                        }
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": cls._last_open_error.get(symbol) or "FLIP_WATCH_OPEN_VETOED",
                        "flip_watch": watch_eval,
                        "success": False,
                    }
                else:
                    logger.debug(
                        "👁️ [PV_FLIP_WATCH_HOLD] %s target=%s reason=%s elapsed=%s/%s",
                        symbol,
                        watch_eval.get("side"),
                        watch_eval.get("reason"),
                        watch_eval.get("elapsed_bars"),
                        watch_eval.get("window_bars"),
                    )
                    return {
                        "status": "HOLD",
                        "symbol": symbol,
                        "reason": watch_eval.get("reason"),
                        "flip_watch": watch_eval,
                        "success": False,
                    }

            _pv_cached_eval: Optional[tuple[Optional[Dict[str, Any]], bool, str]] = None

            def _get_pv_inflection_status() -> tuple[Optional[Dict[str, Any]], bool, str]:
                nonlocal _pv_cached_eval
                if _pv_cached_eval is not None:
                    return _pv_cached_eval
                if not isinstance(raw_df, pd.DataFrame) or len(raw_df) < 15:
                    _pv_cached_eval = (None, False, "PV_FRAME_TOO_SHORT")
                    return _pv_cached_eval
                from core.services.low_latency_entry import evaluate_peak_valley_inflection_entry
                from core.services.low_latency_exit import can_reenter_after_pv_exit

                decision = evaluate_peak_valley_inflection_entry(
                    raw_df,
                    live_price=curr_quote,
                    symbol=symbol,
                    df_5m=frame_5m,
                    require_directional_candle=True,
                )
                ready = bool(
                    decision.get("action") in ("OPEN_LONG", "OPEN_SHORT")
                    and not decision.get("is_consolidation")
                )
                reentry_reason = "NOT_TRIGGERED"
                if ready:
                    acc_key_pv = cls._get_account_key(account, symbol)
                    pv_bar_id = None
                    try:
                        pv_bar_id = int(float(raw_df.iloc[-1].get("timestamp", 0) or 0))
                    except (TypeError, ValueError, OverflowError):
                        pv_bar_id = None
                    reentry_ok, reentry_reason = can_reenter_after_pv_exit(
                        cls._pv_exit_records.get(acc_key_pv),
                        current_bar_id=pv_bar_id,
                        candidate_side=decision["side"],
                    )
                    if not reentry_ok:
                        ready = False
                _pv_cached_eval = (decision, ready, reentry_reason)
                return _pv_cached_eval

            from core.services.entry_methods import (
                KC_OUTER_PIVOT_REVERSAL,
                KC_TWO_BAR_BREAKOUT,
                evaluate_entry_method,
            )
            if evaluate_entry_method(
                raw_df, quote=curr_quote, symbol=symbol,
                expected_method=KC_OUTER_PIVOT_REVERSAL,
            ) is not None:
                pivot_result = await cls._evaluate_closed_entry_methods(
                    symbol=symbol, account=account, engine_or_account=engine_or_account,
                    frame=raw_df, quote=curr_quote, macro_trend=macro_trend,
                    only_method=KC_OUTER_PIVOT_REVERSAL,
                )
                if pivot_result is not None:
                    return pivot_result
            two_bar_breakout_signal = evaluate_entry_method(
                raw_df,
                quote=curr_quote,
                symbol=symbol,
                expected_method=KC_TWO_BAR_BREAKOUT,
            )
            if two_bar_breakout_signal is not None:
                breakout_result = await cls._evaluate_closed_entry_methods(
                    symbol=symbol,
                    account=account,
                    engine_or_account=engine_or_account,
                    frame=raw_df,
                    quote=curr_quote,
                    macro_trend=macro_trend,
                    only_method=KC_TWO_BAR_BREAKOUT,
                )
                if breakout_result is not None:
                    return breakout_result
                return {
                    "status": "VETO",
                    "symbol": symbol,
                    "reason": "BLOCKED_ENTRY_TWO_BAR_BREAKOUT_AUTHORITY",
                    "success": False,
                }
            from core.services.entry_methods import MEME_GATE_ENTRY
            meme_final_gate_vetoed = False
            meme_signal = evaluate_entry_method(
                raw_df, symbol=symbol, quote=curr_quote, expected_method=MEME_GATE_ENTRY,
            )
            if meme_signal is not None:
                meme_result = await cls._evaluate_closed_entry_methods(
                    symbol=symbol, account=account, engine_or_account=engine_or_account,
                    frame=raw_df, quote=curr_quote,
                    macro_trend=(
                        market_data.macro_trend if meme_signal.get("side") == "LONG"
                        else macro_trend
                    ),
                    only_method=MEME_GATE_ENTRY,
                )
                if meme_result is not None:
                    if (
                        meme_result.get("status") == "VETO"
                        and meme_result.get("reason") == "ENTRY_METHOD_FINAL_GATE_VETO"
                    ):
                        meme_final_gate_vetoed = True
                        logger.info(
                            "MEME_GATE_ENTRY_FINAL_VETO_FALLTHROUGH symbol=%s side=%s event=%s",
                            symbol, meme_result.get("side"), meme_result.get("event_id"),
                        )
                    elif two_bar_breakout_signal is not None:
                        logger.info(
                            "ENTRY_KC_DIRECTION_FLAT_BREAKOUT_BYPASS symbol=%s side=%s event=%s",
                            symbol,
                            two_bar_breakout_signal.get("side"),
                            two_bar_breakout_signal.get("event_id"),
                        )
                    else:
                        return meme_result
            if not entry_kc_up and not entry_kc_down:
                pv_flat_decision, pv_flat_ready, _ = _get_pv_inflection_status()
                if entry_kc_slope is not None and pv_flat_ready and pv_flat_decision is not None:
                    logger.info(
                        "ENTRY_KC_DIRECTION_FLAT_PV_BYPASS symbol=%s side=%s trigger=%s",
                        symbol,
                        pv_flat_decision.get("side"),
                        pv_flat_decision.get("trigger_mode"),
                    )
                else:
                    reason = (
                        "BLOCKED_ENTRY_KC_DIRECTION_UNAVAILABLE"
                        if entry_kc_slope is None else "BLOCKED_ENTRY_KC_DIRECTION_FLAT"
                    )
                    logger.info("ENTRY_KC_DIRECTION_VETO symbol=%s reason=%s", symbol, reason)
                    return {"status": "VETO", "symbol": symbol, "reason": reason}
            kc_slope = entry_kc_slope if entry_kc_slope is not None else 0.0
            try:
                m5_c = float(c_k.get("ma5", math.nan))
                m5_p = float(p_k.get("ma5", math.nan))
            except (TypeError, ValueError, OverflowError) as exc:
                logger.warning("ENTRY_MA5_DATA_INVALID symbol=%s error=%s", symbol, exc)
                return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_ENTRY_MA5_DATA_INVALID"}
            if not all(math.isfinite(value) and value > 0 for value in (m5_c, m5_p)):
                return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_ENTRY_MA5_DATA_INVALID"}
            # ── Squeeze 擠壓狀態、震盪糾纏與 MA5 中軌貼合防護 (Squeeze, Chop & MA5-Midline Adhesion Guard) ──
            # 依使用者最高鐵律：「震盪區不要開倉、MA5中軌貼合不開倉」！
            bb_u = float(c_k.get("bb_upper", 0.0))
            bb_lo = float(c_k.get("bb_lower", 0.0))
            if (bb_u <= 0 or bb_lo <= 0) and raw_df is not None and "close" in raw_df.columns and len(raw_df) >= 20:
                std20 = float(raw_df["close"].rolling(20, min_periods=1).std().iloc[-1] or 0.0)
                mid20 = float(raw_df["close"].rolling(20, min_periods=1).mean().iloc[-1] or c_c)
                bb_u = mid20 + 2.0 * std20
                bb_lo = mid20 - 2.0 * std20
            is_sqz_now = bool(bb_u > 0 and bb_lo > 0 and kc_u > 0 and kc_lo > 0 and bb_u < kc_u and bb_lo > kc_lo)
            ma_dist_now = abs(m5_c - kc_mid) / kc_mid if kc_mid > 0 else 0.0
            entry_ma5_mid_diff = abs(m5_c - kc_mid)
            entry_ma5_ma15_diff = abs(m5_c - m15_c)
            entry_ma5_ma15_spread_ratio = entry_ma5_ma15_diff / m15_c if m15_c > 0 else 0.0

            # 1. 【MA5 中軌貼合判定 (is_entry_ma5_midline_adhered)】：
            # 當 MA5 與 KC 中軌距離過近，或 MA5 與 MA15 高度貼合，且 K 棒實體微弱 (< 0.25 ATR) 毫無突破動能時，禁止開倉！
            # 但針對妖幣：若當根 K 棒為中大實體動能棒 (c_bd >= 0.25 * atr_v) 強勢貫穿中軌/MA15 起飛，代表正處於金叉/死叉發散起點，絕不誤判為死水貼合！
            is_strong_cross_candle = bool(
                atr_v > 0
                and c_bd >= 0.25 * atr_v
                and (
                    (c_c > c_o and c_c > kc_mid and c_c > m15_c and m5_c > m5_p)
                    or (c_c < c_o and c_c < kc_mid and c_c < m15_c and m5_c < m5_p)
                )
            )
            is_entry_ma5_midline_adhered = bool(
                not is_strong_cross_candle
                and (
                    ma_dist_now < 0.0010
                    or (atr_v > 0 and entry_ma5_mid_diff < 0.15 * atr_v)
                    or entry_ma5_ma15_spread_ratio < 0.0008
                    or (atr_v > 0 and entry_ma5_ma15_diff < 0.10 * atr_v)
                )
            )

            # 2. 【震盪區 / 均線糾纏判定 (is_chop_zone)】：
            # 檢查近 8 根 K 棒內 MA5 與 KC 中軌 (或 MA15) 是否反覆交錯糾纏 (>= 2 次交錯)，
            # 或 KC 中軌斜率極度走平 (|kc_slope| < 0.02 * ATR 且未突破外軌)，或處於 RANGE 猴市震盪區！
            # 針對妖幣：只有破軌或大實體（c_bd >= 0.70 * atr_v）且順向發散才算點火，普通小K絕不可解除震盪鎖定！
            is_meme_breakout_ignition = bool(
                atr_v > 0
                and c_bd >= 0.70 * atr_v
                and (
                    (c_c > c_o and c_c > kc_mid and c_c > m15_c and m5_c > m15_c and m5_c > m5_p and kc_slope >= 0.05 * atr_v)
                    or (c_c < c_o and c_c < kc_mid and c_c < m15_c and m5_c < m15_c and m5_c < m5_p and kc_slope <= -0.05 * atr_v)
                )
            )
            entry_ma5_mid_cross_8 = 0
            entry_ma5_m15_cross_8 = 0
            if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 15 and "close" in raw_df.columns:
                try:
                    s_close_w = raw_df["close"].astype(float)
                    s_entry_ma5_w = raw_df["ma5"].astype(float) if "ma5" in raw_df.columns else s_close_w.rolling(5, min_periods=5).mean()
                    s_ma15_w = raw_df["ma15"].astype(float) if "ma15" in raw_df.columns else s_close_w.rolling(15, min_periods=1).mean()
                    s_kcm_w = raw_df["kc_middle"].astype(float) if "kc_middle" in raw_df.columns else s_close_w.ewm(span=20, adjust=False).mean()
                    diff_mid_sign = (s_entry_ma5_w - s_kcm_w).iloc[-8:].apply(lambda v: 1 if v >= 0 else -1)
                    diff_m15_sign = (s_entry_ma5_w - s_ma15_w).iloc[-8:].apply(lambda v: 1 if v >= 0 else -1)
                    entry_ma5_mid_cross_8 = int((diff_mid_sign.diff().abs() > 0).sum())
                    entry_ma5_m15_cross_8 = int((diff_m15_sign.diff().abs() > 0).sum())
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    logger.warning("ENTRY_MA5_CHOP_DATA_INVALID symbol=%s error=%s", symbol, exc)
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_ENTRY_MA5_CHOP_DATA_INVALID"}

            is_kc_slope_flat = bool(atr_v > 0 and abs(kc_slope) < 0.02 * atr_v and kc_lo <= c_c <= kc_u and not is_meme_breakout_ignition)
            is_chop_zone = bool(
                not is_meme_breakout_ignition
                and (
                    market_regime == "RANGE"
                    or is_choppy
                    or is_sqz_now
                    or is_kc_slope_flat
                    or (entry_ma5_mid_cross_8 >= 2 and ma_dist_now < 0.0015)
                    or (entry_ma5_m15_cross_8 >= 2 and entry_ma5_ma15_spread_ratio < 0.0012)
                )
            )

            is_true_bottom, is_true_peak = check_pivot_status(
                c_k, p_k, m5_c, m5_p, m15_c, m15_p,
                kc_u, kc_lo, p_kc_u, p_kc_lo, atr_v,
            )
            method_result = await cls._evaluate_closed_entry_methods(
                symbol=symbol,
                account=account,
                engine_or_account=engine_or_account,
                frame=raw_df,
                quote=curr_quote,
                macro_trend=macro_trend,
                exclude_methods={MEME_GATE_ENTRY} if meme_final_gate_vetoed else None,
            )
            if method_result is not None:
                rejections = method_result.get("entry_rejections", [])
                location_only_rejection = (
                    method_result.get("status") == "VETO"
                    and method_result.get("reason") == "BLOCKED_ENTRY_METHOD_MACRO_GATE"
                    and two_bar_breakout_signal is None
                    and bool(rejections)
                    and all(
                        rejection.get("reason")
                        == "BLOCKED_ENTRY_METHOD_MACRO_KC_RELATIVE_LOCATION"
                        for rejection in rejections
                    )
                )
                if not location_only_rejection:
                    return method_result
                method_rejection = method_result
                logger.info(
                    "ENTRY_METHOD_ALTERNATIVE_EVALUATION symbol=%s rejections=%s",
                    symbol, rejections,
                )

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
            ma5_val_ref = float(getattr(market_data, "ma5", m5_c) or m5_c)
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
            # 支援【MA5 穿越 KC 中軌】與【MA5 交叉 MA15】雙重敏捷觸發！
            is_valid_gc = is_valid_golden_cross(m5_c, m5_p, m15_c, m15_p, atr_v)
            is_valid_dc = is_valid_dead_cross(m5_c, m5_p, m15_c, m15_p, atr_v)

            # ── 🧬 【妖幣專用 - K 線型態與量能爆發重塑邏輯】 ──
            curr_vol_now = float(c_k.get("volume", 0.0) or 0.0)
            prev_vol_now = float(p_k.get("volume", 0.0) or 0.0)
            vol_ma20_now = float(c_k.get("volume_ma20", getattr(market_data, "volume_ma20", 0.0)) or 0.0)
            if vol_ma20_now <= 0 and raw_df is not None and isinstance(raw_df, pd.DataFrame) and "volume" in raw_df.columns and len(raw_df) >= 5:
                vol_ma20_now = float(raw_df["volume"].astype(float).rolling(20, min_periods=1).mean().iloc[-1] or 0.0)

            # (1) 大實體向下/向上（強空砸盤 / 強多拉升）：
            #   - 大實體陰線下砸且幾乎無下影線，視為主力強砸，嚴禁開多接飛刀！
            #   - 大實體陽線暴拉且幾乎無上影線，視為主力強拉，嚴禁開空擋火車！
            is_heavy_bear_smash = bool(
                c_c < c_o
                and atr_v > 0
                and c_bd >= 0.85 * atr_v
                and c_lo_w <= 0.15 * c_bd
            )
            is_heavy_bull_surge = bool(
                c_c > c_o
                and atr_v > 0
                and c_bd >= 0.85 * atr_v
                and c_up_w <= 0.15 * c_bd
            )
            # (2) 弱實體（小 K 線）：
            #   - 在「KC 向下 / 熊市」結構中：弱實體視為跌勢中繼，禁止開多。
            #   - 在「KC 向上 / 牛市」結構中：弱實體視為漲勢中繼，禁止開空。
            #   - 若「已跌至極限/打到關鍵支撐」且伴隨「極致縮量」(vol < 0.65 * vol_ma20)：進入預警區 (is_support_low_vol_alert)，等待下一根主力放量買盤啟動！
            is_weak_body_now = bool(atr_v > 0 and c_bd < 0.22 * atr_v)
            is_prev_weak_at_support_low_vol = bool(
                atr_v > 0
                and p_bd < 0.25 * atr_v
                and (vol_ma20_now <= 0 or prev_vol_now <= 0.70 * vol_ma20_now)
                and (p_l <= kc_lo * 1.003 or is_support_wall_block_short)
            )
            is_prev_weak_at_resistance_low_vol = bool(
                atr_v > 0
                and p_bd < 0.25 * atr_v
                and (vol_ma20_now <= 0 or prev_vol_now <= 0.70 * vol_ma20_now)
                and (p_h >= kc_u * 0.997 or is_resistance_wall_block_long)
            )
            # (3) 妖幣核心開多/開空訊號 A — 超長下影線/上影線（洗盤插針 >= 實體 3 倍以上 + 爆量拉回/砸回）：
            is_meme_3x_pinbar_long = bool(
                c_lo_w >= 3.0 * max(c_bd, 1e-12)
                and (c_l <= kc_lo or is_support_wall_block_short)
                and c_c >= c_o
                and c_c > kc_mid
                and m5_c > m15_c
                and (vol_ma20_now <= 0 or curr_vol_now >= 1.25 * vol_ma20_now)
            )
            is_meme_3x_pinbar_short = bool(
                c_up_w >= 3.0 * max(c_bd, 1e-12)
                and (c_h >= kc_u or is_resistance_wall_block_long)
                and c_c <= c_o
                and c_c < kc_mid
                and m5_c < m15_c
                and (vol_ma20_now <= 0 or curr_vol_now >= 1.25 * vol_ma20_now)
            )
            # (4) 妖幣核心開多/開空訊號 B — 實體突破/跌破 + 爆量（主力拉升/主力砸盤）：
            is_meme_vol_surge_breakout_long = bool(
                c_c > c_o
                and atr_v > 0
                and c_bd >= 0.45 * atr_v
                and c_c > kc_mid
                and c_c > m15_c
                and m5_c > m15_c
                and m5_c > m5_p
                and c_up_w <= 0.6 * c_bd
                and (
                    (vol_ma20_now > 0 and curr_vol_now >= 1.35 * vol_ma20_now)
                    or (is_prev_weak_at_support_low_vol and (vol_ma20_now <= 0 or curr_vol_now >= 1.10 * vol_ma20_now))
                )
            )
            is_meme_vol_surge_breakdown_short = bool(
                c_c < c_o
                and atr_v > 0
                and c_bd >= 0.45 * atr_v
                and c_c < kc_mid
                and c_c < m15_c
                and m5_c < m15_c
                and m5_c < m5_p
                and c_lo_w <= 0.6 * c_bd
                and (
                    (vol_ma20_now > 0 and curr_vol_now >= 1.35 * vol_ma20_now)
                    or (is_prev_weak_at_resistance_low_vol and (vol_ma20_now <= 0 or curr_vol_now >= 1.10 * vol_ma20_now))
                )
            )
            # 妖幣前低/前高插針點外加 1.2% 緩衝硬性防二次下砸/拉爆停損價
            meme_pinbar_low_ref = min(c_l, p_l)
            meme_pinbar_high_ref = max(c_h, p_h)
            meme_wick_buffer_sl_long = meme_pinbar_low_ref * 0.988 if (is_meme_3x_pinbar_long or is_meme_vol_surge_breakout_long) else 0.0
            meme_wick_buffer_sl_short = meme_pinbar_high_ref * 1.012 if (is_meme_3x_pinbar_short or is_meme_vol_surge_breakdown_short) else 0.0

            # ⚖️ 【黃金交叉 / 死亡交叉確立才開倉 + 通道獲利空間 (Room Ready) + 已收線確認防毛刺】：
            # 1. 嚴禁 MA5 與 MA15 剛微幅觸碰/未發散就急著開倉：必須具備明確發散間距 ((m5_c - m15_c) >= min_gc_dc_spread) 且具備實體動能棒！
            # 2. 嚴禁盤中未收線毛刺搶跑與貼軌追高追低：
            #    - 通道獲利空間 (Room Ready)：做多距離 KC 上軌剩餘空間至少 25% 上半通道寬度且距中軌 <= 1.25 ATR；做空距離 KC 下軌剩餘空間至少 25% 下半通道寬度且距中軌 <= 1.25 ATR！
            #    - 收線確認：最近一根已收線 1m K 棒必須已站上/跌破 KC 中軌（或爆量突破），嚴禁上一根還在反側時因盤中前 15~45 秒毛刺衝過中軌就提前買在最高點/空在最低點！
            # 3. 解決「已經交叉好久卻不開倉」：只要黃金交叉/死亡交叉發散狀態確立、通道空間充足且已收線確認順向，無論交叉發生在當根或前幾根 K 棒，空手即刻確認進場！
            closed_k_entry = c_k
            prev_closed_k_entry = p_k
            if raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 2:
                if not bool(raw_df.iloc[-1].get("is_closed", True)):
                    closed_k_entry = raw_df.iloc[-2]
                    prev_closed_k_entry = raw_df.iloc[-3] if len(raw_df) >= 3 else raw_df.iloc[-2]
                else:
                    closed_k_entry = raw_df.iloc[-1]
                    prev_closed_k_entry = raw_df.iloc[-2]
            closed_c_entry = float(closed_k_entry.get("close", c_c))
            closed_o_entry = float(closed_k_entry.get("open", c_o))
            closed_h_entry = float(closed_k_entry.get("high", c_h))
            closed_l_entry = float(closed_k_entry.get("low", c_l))
            closed_kcu_entry = float(closed_k_entry.get("kc_upper", kc_u))
            closed_kcl_entry = float(closed_k_entry.get("kc_lower", kc_lo))
            prev_closed_h_entry = float(prev_closed_k_entry.get("high", p_h))
            prev_closed_l_entry = float(prev_closed_k_entry.get("low", p_l))
            closed_kcm_entry = float(closed_k_entry.get("kc_middle", kc_mid))
            closed_m15_entry = float(closed_k_entry.get("ma15", m15_c))

            upper_half_w = max(kc_u - kc_mid, 1e-12)
            lower_half_w = max(kc_mid - kc_lo, 1e-12)
            # 使用者核心指令：離中軌太遠不開倉的規定已全面去除，不再限制 (c_c - kc_mid) <= 1.25 * atr_v
            has_room_to_upper_rail = bool((kc_u - c_c) >= 0.25 * upper_half_w)
            has_room_to_lower_rail = bool((c_c - kc_lo) >= 0.25 * lower_half_w)
            is_closed_bar_bull_confirmed = bool(
                (closed_c_entry >= closed_kcm_entry and closed_c_entry >= closed_m15_entry and closed_c_entry >= closed_o_entry)
                or (
                    c_c > c_o
                    and atr_v > 0
                    and c_bd >= 0.35 * atr_v
                    and c_up_w <= 0.35 * c_bd
                    and c_c > kc_mid
                    and c_c > m15_c
                    and (m5_c - m15_c) >= min_gc_dc_spread
                    and has_room_to_upper_rail
                )
                or is_meme_vol_surge_breakout_long
                or is_meme_3x_pinbar_long
            )
            is_closed_bar_bear_confirmed = bool(
                (closed_c_entry <= closed_kcm_entry and closed_c_entry <= closed_m15_entry and closed_c_entry <= closed_o_entry)
                or (
                    c_c < c_o
                    and atr_v > 0
                    and c_bd >= 0.35 * atr_v
                    and c_lo_w <= 0.35 * c_bd
                    and c_c < kc_mid
                    and c_c < m15_c
                    and (m15_c - m5_c) >= min_gc_dc_spread
                    and has_room_to_lower_rail
                )
                or is_meme_vol_surge_breakdown_short
                or is_meme_3x_pinbar_short
            )

            is_confirmed_golden_cross_ready = bool(
                (m5_c > m15_c)
                and ((m5_c - m15_c) >= min_gc_dc_spread)
                and (m5_c > m5_p)
                and is_3bar_bull_slope_ready
                and (c_c > kc_mid and c_c > m15_c and c_c >= m5_c)
                and (c_c > c_o)
                and (atr_v <= 0 or c_bd >= 0.16 * atr_v)
                and (c_up_w <= 0.85 * max(c_bd, 1e-12))
                and has_room_to_upper_rail
                and is_closed_bar_bull_confirmed
            )
            is_confirmed_death_cross_ready = bool(
                (m5_c < m15_c)
                and ((m15_c - m5_c) >= min_gc_dc_spread)
                and (m5_c < m5_p)
                and is_3bar_bear_slope_ready
                and (c_c < kc_mid and c_c < m15_c and c_c <= m5_c)
                and (c_c < c_o)
                and (atr_v <= 0 or c_bd >= 0.16 * atr_v)
                and (c_lo_w <= 0.85 * max(c_bd, 1e-12))
                and has_room_to_lower_rail
                and is_closed_bar_bear_confirmed
            )

            # MA5 穿越 KC 中軌（必須伴隨黃金交叉/死亡交叉發散確認，嚴禁未形成黃金/死亡交叉就單憑穿中軌搶跑開倉！）
            is_cross_mid_long = bool(
                kc_mid > 0
                and is_confirmed_golden_cross_ready
                and ((m5_c >= kc_mid and m5_p <= kc_mid) or (c_c >= kc_mid and (c_o <= kc_mid or p_c <= kc_mid)))
            )
            is_cross_mid_short = bool(
                kc_mid > 0
                and is_confirmed_death_cross_ready
                and ((m5_c <= kc_mid and m5_p >= kc_mid) or (c_c <= kc_mid and (c_o >= kc_mid or p_c >= kc_mid)))
            )

            # 平倉後防秒級連環反手與同區間雙巴冷卻門檻（Stage 3.1 與 Stage 3.2 共用）：
            acc_key_stage3 = cls._get_account_key(account, symbol)
            last_exit_stage3 = cls._last_exit_record.get(acc_key_stage3)
            pivot_cooldown_blocked = False
            is_macro_bull_follow = bool((is_zoomed_out_bullish or macro_trend == "BULLISH") and m5_c > m15_c and curr_quote > m15_c)
            is_macro_bear_follow = bool((is_zoomed_out_bearish or macro_trend == "BEARISH") and m5_c < m15_c and curr_quote < m15_c)
            if last_exit_stage3:
                elapsed_s3 = time.time() - float(last_exit_stage3.get("timestamp", 0.0))
                last_exit_reason_s3 = str(last_exit_stage3.get("reason", "")).upper()
                is_loss_or_breach_exit = any(
                    k in last_exit_reason_s3
                    for k in ("OPPOSITE_KC_BAND_BREACH", "REVERSAL", "RAIL_BREACH", "STOP_LOSS", "WATERFALL")
                )
                min_wait_s3 = 60.0 if is_loss_or_breach_exit else 45.0
                # 順勢多頭排列且站穩 MA15 或 大週期牛市突破時豁免冷卻，防止平空後錯失噴出浪
                is_bull_breakout_exempt = bool((is_zoomed_out_bullish or macro_trend == "BULLISH") and (curr_quote > m15_c or c_c > kc_u))
                is_bear_breakdown_exempt = bool((is_zoomed_out_bearish or macro_trend == "BEARISH") and (curr_quote < m15_c or c_c < kc_lo))
                if elapsed_s3 < min_wait_s3 and not (is_macro_bull_follow or is_macro_bear_follow or is_bull_breakout_exempt or is_bear_breakdown_exempt):
                    pivot_cooldown_blocked = True
                    logger.debug(
                        f"⏳ [PIVOT_COOLDOWN] {symbol} 剛平倉 ({last_exit_reason_s3}) 僅 {elapsed_s3:.1f}s < {min_wait_s3:.0f}s，"
                        f"禁止秒級搶跑反手或連環追單！"
                    )

            # 嚴格順勢方向審查（精準開多開空，絕不開錯邊，拉遠看是空頭絕不開多、多頭絕不開空）：
            # 做多必須 KC 中軌向上 (is_3bar_bull_slope_ready)、MA15 向上、拉遠非空頭 (not is_zoomed_out_bearish)、且黃金交叉發散確立；
            # 賣空必須 KC 中軌向下 (is_3bar_bear_slope_ready)、MA15 向下、拉遠非多頭 (not is_zoomed_out_bullish)、且死亡交叉發散確立！
            # ⚡ 起漲/起跌點 100% 秒開倉 (Zero-Delay Entry)：
            # 當大趨勢為 KC 同向，當根 1m 收線瞬間出現「實體綠K/紅K (Body >= 0.35 * ATR) 站上/跌破 MA5、MA15 與 KC 中軌」且 MA5 拐頭同向時，
            # 當根收線第 1 毫秒立即授權進場，嚴禁傻等 MA5/MA15 大幅張口才追在天花板/地板！
            is_zero_delay_long_ready = bool(
                is_closed_bar_bull_confirmed
                and (c_c > c_o)
                and (atr_v > 0 and c_bd >= 0.35 * atr_v)
                and (c_c >= m5_c and c_c > m15_c and c_c > kc_mid)
                and (m5_c > m5_p)
                and (m5_c > m15_c)
                and (kc_slope > 0)
                and is_3bar_bull_slope_ready
                and has_room_to_upper_rail
            )
            is_zero_delay_short_ready = bool(
                is_closed_bar_bear_confirmed
                and (c_c < c_o)
                and (atr_v > 0 and c_bd >= 0.35 * atr_v)
                and (c_c <= m5_c and c_c < m15_c and c_c < kc_mid)
                and (m5_c < m5_p)
                and (m5_c < m15_c)
                and (kc_slope < 0)
                and is_3bar_bear_slope_ready
                and has_room_to_lower_rail
            )

            can_open_long = bool(
                (not pivot_cooldown_blocked)
                and (
                    is_confirmed_golden_cross_ready
                    or is_zero_delay_long_ready
                    or is_cross_mid_long
                    or (is_valid_gc and has_room_to_upper_rail and is_closed_bar_bull_confirmed)
                    or is_true_bottom
                    or is_meme_3x_pinbar_long
                    or is_meme_vol_surge_breakout_long
                    or (is_1m_confirmed_bull and c_c >= m5_c and has_room_to_upper_rail and is_closed_bar_bull_confirmed)
                )
                and (has_room_to_upper_rail or is_meme_vol_surge_breakout_long or is_meme_3x_pinbar_long)
                and is_closed_bar_bull_confirmed
                and (not is_heavy_bear_smash)
                and (not (is_weak_body_now and kc_slope <= 0 and not is_meme_3x_pinbar_long))
                and (m5_c > m15_c)
                and ((m5_c - m15_c) >= min_gc_dc_spread or is_zero_delay_long_ready or is_meme_3x_pinbar_long or is_meme_vol_surge_breakout_long)
                and is_3bar_bull_slope_ready
                and (not is_entry_ma5_midline_adhered or is_zero_delay_long_ready or is_meme_vol_surge_breakout_long)
                and (not is_chop_zone or range_breakout_side == "LONG" or is_meme_vol_surge_breakout_long)
                and allow_long_defense
                and (not is_zoomed_out_bearish)
                and (market_regime != "RANGE" or range_breakout_side == "LONG" or is_meme_vol_surge_breakout_long)
                and (m5_c > m5_p)
                and (m5_c > kc_mid and c_c > kc_mid and c_c > m15_c)
                and (c_c > c_o)
                and (kc_slope > 0)
                and not is_chasing_high
                and (not is_sqz_now or is_meme_vol_surge_breakout_long)
            )
            can_open_short = bool(
                (not pivot_cooldown_blocked)
                and (
                    is_confirmed_death_cross_ready
                    or is_zero_delay_short_ready
                    or is_cross_mid_short
                    or (is_valid_dc and has_room_to_lower_rail and is_closed_bar_bear_confirmed)
                    or is_true_peak
                    or is_meme_3x_pinbar_short
                    or is_meme_vol_surge_breakdown_short
                    or (is_1m_confirmed_bear and c_c <= m5_c and has_room_to_lower_rail and is_closed_bar_bear_confirmed)
                )
                and (has_room_to_lower_rail or is_meme_vol_surge_breakdown_short or is_meme_3x_pinbar_short)
                and is_closed_bar_bear_confirmed
                and (not is_heavy_bull_surge)
                and (not (is_weak_body_now and kc_slope >= 0 and not is_meme_3x_pinbar_short))
                and (m5_c < m15_c)
                and ((m15_c - m5_c) >= min_gc_dc_spread or is_zero_delay_short_ready or is_meme_3x_pinbar_short or is_meme_vol_surge_breakdown_short)
                and is_3bar_bear_slope_ready
                and (not is_entry_ma5_midline_adhered or is_zero_delay_short_ready or is_meme_vol_surge_breakdown_short)
                and (not is_chop_zone or range_breakout_side == "SHORT" or is_meme_vol_surge_breakdown_short)
                and allow_short_defense
                and (not is_zoomed_out_bullish)
                and (market_regime != "RANGE" or range_breakout_side == "SHORT" or is_meme_vol_surge_breakdown_short)
                and (m5_c < m5_p)
                and (m5_c < kc_mid and c_c < kc_mid and c_c < m15_c)
                and (c_c < c_o)
                and (kc_slope < 0)
                and not is_chasing_low
                and (not is_sqz_now or is_meme_vol_surge_breakdown_short)
            )

            # ══════════════════════════════════════════════════════════════════
            # 🏛️ MTF 雙重過濾與下單控制（情況 A/B/C/D 絕對執行）：
            # - 若大週期為空頭 (is_zoomed_out_bearish)：
            #   * 強制鎖死開多功能 (can_open_long = False)！
            #   * 若小週期 MA5 > MA15 (情況 A)：僅視為 WAIT_REBOUND (弱勢反彈拉高)，維持觀望等待高空點，絕不開多！
            #   * 僅當小週期 MA5 < MA15 (情況 B)：順勢開空 (OPEN_SHORT)！
            # - 若大週期為多頭 (is_zoomed_out_bullish)：
            #   * 強制鎖死開空功能 (can_open_short = False)！
            #   * 若小週期 MA5 < MA15 (情況 C)：僅視為 WAIT_PULLBACK (回檔拉回)，維持觀望等待低多點，絕不開空！
            #   * 僅當小週期 MA5 > MA15 (情況 D)：順勢開多 (OPEN_LONG)！
            # ══════════════════════════════════════════════════════════════════
            if is_zoomed_out_bearish:
                can_open_long = False
                if m5_c >= m15_c:
                    can_open_short = False
                    logger.debug(
                        f"🛡️ [MTF_WAIT_REBOUND] {symbol} 情況 A (大週期空頭 + 小週期 MA5>MA15 金叉): "
                        f"僅視為反彈高點準備區 (WAIT_REBOUND)，強制鎖死 can_open_long=False，等待 MA5 重新死叉 MA15 再開空！"
                    )
            elif is_zoomed_out_bullish:
                can_open_short = False
                if m5_c <= m15_c:
                    can_open_long = False
                    logger.debug(
                        f"🛡️ [MTF_WAIT_PULLBACK] {symbol} 情況 C (大週期多頭 + 小週期 MA5<MA15 死叉): "
                        f"僅視為回檔低點準備區 (WAIT_PULLBACK)，強制鎖死 can_open_short=False，等待 MA5 重新金叉 MA15 再開多！"
                    )

            # 🚨 規則：震盪行情硬性禁止開倉 (Sideways No-Trade Zone)
            # 1. KC 通道縮口: (KC上軌 - KC下軌) / 當前價格 < 1.0% (迷因幣放大至 1.0% 防橫盤追單)
            # 2. 均線走平纏繞: 近 8~10 根 K 棒內 MA5 與 MA15 交叉次數 >= 2 且間距極小
            # 3. KC 中軌斜率與 MA15 斜率過小 (無趨勢傾角) 且 K 棒實體微小
            # 4. 針對破軌行情豁免：若價格已破 KC 上軌或下軌且收同向實體 K 棒，代表破軌爆發，不被震盪鎖定阻斷！
            is_breakout_candle_long = bool(c_c > kc_u and c_c > c_o and (c_c - c_o) >= 0.15 * atr_v)
            is_breakout_candle_short = bool(c_c < kc_lo and c_c < c_o and (c_o - c_c) >= 0.15 * atr_v)
            is_any_rail_breakout = bool(is_breakout_candle_long or is_breakout_candle_short)

            kc_width_ratio = (kc_u - kc_lo) / c_c if c_c > 0 else 0.0
            is_kc_narrow_squeeze = bool(kc_width_ratio > 0 and kc_width_ratio < 0.010)
            is_ma_entangled = bool(entry_ma5_m15_cross_8 >= 2 and entry_ma5_ma15_spread_ratio < 0.0018)
            is_slope_flat_chop = bool(atr_v > 0 and abs(kc_slope) < 0.05 * atr_v and abs(m15_c - m15_p) < 0.05 * atr_v)
            is_sideways_market = bool(
                (is_kc_narrow_squeeze or is_ma_entangled or is_slope_flat_chop or is_chop_zone)
                and not is_meme_breakout_ignition
                and not is_any_rail_breakout
            )

            if is_sideways_market and not has_position:
                logger.info(
                    f"🚫 [SIDEWAYS_NO_TRADE_ZONE] {symbol} 處於垃圾時間窄幅震盪 "
                    f"(kc_width={kc_width_ratio:.2%}, sqz={is_kc_narrow_squeeze}, entangled={is_ma_entangled}, flat={is_slope_flat_chop})，"
                    f"100% 一票否決任何開倉指令，維持震盪觀望 (HOLD)！"
                )
                can_open_long = False
                can_open_short = False
                pivot_triggered = False

            # 🚀 2026-10-30 核心授權：牛市/熊市早期開倉機制與雙重一票否決
            # 🛑 2026-10-31 猴市/震盪區鐵律：猴市（RANGE）或震盪市場（is_sideways_market）100% 嚴禁開倉！
            if (not has_position) and (not is_sideways_market) and (market_regime != "RANGE") and (is_zoomed_out_bullish or macro_trend == "BULLISH") and (not can_open_long):
                # 🛑 規則四.3：平多後進場冷卻 (Cooldown 2 根 K 棒 / 120 秒)
                last_closed_time = float(getattr(account, "last_closed_at", {}).get(symbol, 0.0) or 0.0)
                last_closed_side = str(getattr(account, "last_closed_side", {}).get(symbol, "") or "").upper()
                is_in_long_cooldown = bool(last_closed_side == "LONG" and (time.time() - last_closed_time) < 120.0)

                # 🛑 規則二.2：做多審查 (位置否決: MA5 < MA15; 斜率否決: MA5 斜率走平或向下 <= 0; KC斜率否決: KC向下或走平)
                p_kc_mid = float(p_k.get("kc_middle", kc_mid))
                kc_mid_slope = (kc_mid - p_kc_mid) if (kc_mid > 0 and p_kc_mid > 0) else kc_slope
                is_kc_slope_not_up = bool(kc_slope <= 0 or kc_mid_slope <= 0)
                is_entry_ma5_slope_not_up = bool(m5_c <= m5_p)  # 走平或向下一票否決
                is_ma15_divergence_too_high = bool(
                    (c_c - m15_c > 1.2 * atr_v)
                    or (m15_c > 0 and (c_c - m15_c) / m15_c >= 0.012)
                )
                is_kc_upper_exhausted = bool(
                    (c_h >= kc_u * 0.999 or closed_h_entry >= closed_kcu_entry * 0.999 or prev_closed_h_entry >= closed_kcu_entry * 0.999)
                    and (c_c < m5_c or c_c < c_o or curr_quote < c_o)
                )
                is_anti_chase_long_veto = bool(is_ma15_divergence_too_high or is_kc_upper_exhausted or is_chasing_high)
                is_long_vetoed = bool(m5_c <= m15_c or is_entry_ma5_slope_not_up or is_kc_slope_not_up or is_anti_chase_long_veto or is_entry_ma5_midline_adhered)

                # 結束了後面有走勢要延續開倉（強制上車）：
                # 若價格在 KC 上軌外或大實體突破且均線發散向上，豁免冷卻期強制上車！
                is_long_continuation_momentum = bool(
                    (curr_quote > kc_u or c_c > kc_u or (c_c > m5_c and m5_c > m15_c and (c_c - c_o) >= 0.35 * atr_v))
                    and m5_c > m5_p
                    and (kc_mid_slope > 0 or macro_trend == "BULLISH")
                )
                if is_in_long_cooldown and is_long_continuation_momentum:
                    logger.info(f"⚡ [CONTINUATION_ENTRY_EXEMPT_COOLDOWN] {symbol} 命中多頭強勢延續走勢，豁免冷卻期立即強制上車！")
                    is_in_long_cooldown = False

                if is_in_long_cooldown:
                    logger.info(
                        f"⏳ [LONG_EXIT_COOLDOWN] {symbol} 平多後 {time.time() - last_closed_time:.1f}s 處於 2 根 K 棒冷卻期 (< 120s)，"
                        f"嚴禁在回調初期反覆追多！"
                    )
                elif is_long_vetoed:
                    if is_anti_chase_long_veto:
                        logger.info(
                            f"🛑 [BLOCKED_LONG_ANTI_CHASE] {symbol} 追高開多三重一票否決: "
                            f"(divergence_high={is_ma15_divergence_too_high}, kc_exhausted={is_kc_upper_exhausted}, chasing={is_chasing_high})，"
                            f"價格嚴重偏離MA15或觸上軌回落，100% 嚴禁頂點追多！"
                        )
                    elif is_entry_ma5_slope_not_up:
                        logger.info(
                            f"🛑 [BLOCKED_LONG_MA5_SLOPE_NOT_UP] {symbol} MA5 斜率走平或向下 "
                            f"(m5_curr={m5_c:.6f} <= m5_prev={m5_p:.6f})，動能衰退 100% 一票否決開多！"
                        )
                    elif is_kc_slope_not_up:
                        logger.info(
                            f"🛑 [BLOCKED_LONG_KC_SLOPE_NOT_UP] {symbol} KC 通道中軌斜率走平或向下 "
                            f"(kc_slope={kc_slope:.8f}, mid_slope={kc_mid_slope:.8f})，中軌未明確向上 100% 嚴禁開多！"
                        )
                    else:
                        logger.info(
                            f"🛑 [LONG_DOUBLE_VETO] {symbol} 做多位置否決 (m3={m5_c:.6f} < m15={m15_c:.6f})，空頭排列嚴防下殺接飛刀！"
                        )
                else:
                    is_early_ma15_breakout = bool(curr_quote > m15_c and c_c > m15_c and c_c > c_o and closed_c_entry > closed_o_entry and (not is_heavy_bear_smash))
                    is_lower_rail_v_reversal = bool(c_l <= kc_lo * 1.002 and c_c > m5_c and m5_c > m5_p and c_c > c_o and closed_c_entry > closed_o_entry)
                    is_green_candle_now = bool(c_c > c_o and closed_c_entry > closed_o_entry)
                    is_entry_ma5_up_now = bool(m5_c > m5_p)
                    if (is_early_ma15_breakout or is_lower_rail_v_reversal or (m5_c > m15_c and curr_quote > m15_c)) and (not is_anti_chase_long_veto) and is_green_candle_now and is_entry_ma5_up_now:
                        can_open_long = True
                        is_cross_mid_long = False
                        is_valid_gc = False
                        is_confirmed_golden_cross_ready = False
                        is_1m_confirmed_bull = False
                        entry_type = "EARLY_BULL_BREAKOUT_LONG" if is_early_ma15_breakout else ("LOWER_RAIL_V_LONG" if is_lower_rail_v_reversal else "MOMENTUM_FOLLOW_LONG")
                        logger.info(f"⚡ [{entry_type}] {symbol} 命中牛市提早開倉機制 ({entry_type})，通過雙重審查，立即鎖定起漲點開多！")
            elif (not has_position) and (not is_sideways_market) and (market_regime != "RANGE") and (is_zoomed_out_bearish or macro_trend == "BEARISH") and (not can_open_short):
                # 🛑 規則四.3：平空後進場冷卻 (Cooldown 2 根 K 棒 / 120 秒)
                last_closed_time = float(getattr(account, "last_closed_at", {}).get(symbol, 0.0) or 0.0)
                last_closed_side = str(getattr(account, "last_closed_side", {}).get(symbol, "") or "").upper()
                is_in_short_cooldown = bool(last_closed_side == "SHORT" and (time.time() - last_closed_time) < 120.0)

                # 🛑 規則三.2：做空審查 (位置否決: MA5 > MA15; 斜率否決: MA5 斜率走平或向上 >= 0; KC斜率否決: KC向上或走平)
                p_kc_mid = float(p_k.get("kc_middle", kc_mid))
                kc_mid_slope = (kc_mid - p_kc_mid) if (kc_mid > 0 and p_kc_mid > 0) else kc_slope
                is_kc_slope_not_down = bool(kc_slope >= 0 or kc_mid_slope >= 0)
                is_entry_ma5_slope_not_down = bool(m5_c >= m5_p)  # 走平或向上一票否決
                is_ma15_divergence_too_low = bool(
                    (m15_c - c_c > 1.2 * atr_v)
                    or (m15_c > 0 and (m15_c - c_c) / m15_c >= 0.012)
                )
                is_kc_lower_exhausted = bool(
                    (c_l <= kc_lo * 1.001 or closed_l_entry <= closed_kcl_entry * 1.001 or prev_closed_l_entry <= closed_kcl_entry * 1.001)
                    and (c_c > m5_c or c_c > c_o or curr_quote > c_o)
                )
                is_anti_chase_short_veto = bool(is_ma15_divergence_too_low or is_kc_lower_exhausted or is_chasing_low)
                is_short_vetoed = bool(m5_c >= m15_c or is_entry_ma5_slope_not_down or is_kc_slope_not_down or is_anti_chase_short_veto or is_entry_ma5_midline_adhered)

                # 結束了後面有走勢要延續開倉（強制上車）：
                # 若價格在 KC 下軌外或大實體跌破且均線發散向下，豁免冷卻期強制上車！
                is_short_continuation_momentum = bool(
                    (curr_quote < kc_lo or c_c < kc_lo or (c_c < m5_c and m5_c < m15_c and (c_o - c_c) >= 0.35 * atr_v))
                    and m5_c < m5_p
                    and (kc_mid_slope < 0 or macro_trend == "BEARISH")
                )
                if is_in_short_cooldown and is_short_continuation_momentum:
                    logger.info(f"⚡ [CONTINUATION_ENTRY_EXEMPT_COOLDOWN] {symbol} 命中空頭強勢延續走勢，豁免冷卻期立即強制上車！")
                    is_in_short_cooldown = False

                if is_in_short_cooldown:
                    logger.info(
                        f"⏳ [SHORT_EXIT_COOLDOWN] {symbol} 平空後 {time.time() - last_closed_time:.1f}s 處於 2 根 K 棒冷卻期 (< 120s)，"
                        f"嚴禁在觸底反彈初期反覆追空！"
                    )
                elif is_short_vetoed:
                    if is_anti_chase_short_veto:
                        logger.info(
                            f"🛑 [BLOCKED_SHORT_ANTI_CHASE] {symbol} 追低開空三重一票否決: "
                            f"(divergence_low={is_ma15_divergence_too_low}, kc_exhausted={is_kc_lower_exhausted}, chasing={is_chasing_low})，"
                            f"價格嚴重偏離MA15或觸下軌反彈，100% 嚴禁阿呆谷追空！"
                        )
                    elif is_entry_ma5_slope_not_down:
                        logger.info(
                            f"🛑 [BLOCKED_SHORT_MA5_SLOPE_NOT_DOWN] {symbol} MA5 斜率走平或向上 "
                            f"(m5_curr={m5_c:.6f} >= m5_prev={m5_p:.6f})，動能減弱 100% 一票否決開空！"
                        )
                    elif is_kc_slope_not_down:
                        logger.info(
                            f"🛑 [BLOCKED_SHORT_KC_SLOPE_NOT_DOWN] {symbol} KC 通道中軌斜率走平或向上 "
                            f"(kc_slope={kc_slope:.8f}, mid_slope={kc_mid_slope:.8f})，中軌未明確向下 100% 嚴禁開空！"
                        )
                    else:
                        logger.info(
                            f"🛑 [SHORT_DOUBLE_VETO] {symbol} 做空位置否決 (m3={m5_c:.6f} > m15={m15_c:.6f})，多頭排列嚴防逆勢賣空！"
                        )
                else:
                    is_early_ma15_breakdown = bool(curr_quote < m15_c and c_c < m15_c and c_c < c_o and closed_c_entry < closed_o_entry and (not is_heavy_bull_surge))
                    is_trend_reentry_bear = bool(m5_c < m15_c and curr_quote < m15_c and c_c < m15_c and closed_c_entry < closed_o_entry)
                    is_red_candle_now = bool(c_c < c_o and closed_c_entry < closed_o_entry)
                    is_entry_ma5_down_now = bool(m5_c < m5_p)
                    if (is_early_ma15_breakdown or is_trend_reentry_bear) and (not is_anti_chase_short_veto) and is_red_candle_now and is_entry_ma5_down_now:
                        can_open_short = True
                        is_cross_mid_short = False
                        is_valid_dc = False
                        is_confirmed_death_cross_ready = False
                        is_1m_confirmed_bear = False
                        entry_type = "EARLY_BEAR_BREAKDOWN_SHORT" if is_early_ma15_breakdown else "MOMENTUM_FOLLOW_SHORT"
                        logger.info(f"⚡ [{entry_type}] {symbol} 處於大週期熊市且跌破 MA15，通過雙重審查，觸發順勢開空/補單！")

            if can_open_long:
                candidate_side = "LONG"
                if entry_type in ("MOMENTUM_FOLLOW_LONG", "EARLY_BULL_BREAKOUT_LONG", "LOWER_RAIL_V_LONG"):
                    pass
                elif is_cross_mid_long:
                    entry_type = "PIVOT_LONG_CROSS_MID"
                elif is_valid_gc or is_confirmed_golden_cross_ready or is_1m_confirmed_bull:
                    entry_type = "NONE_GOLDEN_CROSS"
                else:
                    entry_type = "PIVOT_LONG"
                pivot_triggered = True
            elif can_open_short:
                # 🛑 2026-10-30 核心防呆：V 轉強勢反彈硬性一票否決開空（防阿呆谷追空）
                # 無論大週期標籤為何，只要當前 K 棒出現實體綠棒 (close > open) 且價格已站上 MA5 或 MA15，
                # 代表正在進行 V 轉反彈，強制一票否決任何 OPEN_SHORT 請求！
                is_v_reversal_bounce = bool(
                    (c_c > c_o and (c_c >= m5_c or curr_quote >= m5_c or c_c >= m15_c or curr_quote >= m15_c))
                    or (c_c > m15_c or curr_quote > m15_c)
                    or (c_c > c_o and (c_c - c_o) >= 0.20 * atr_v)
                )
                if is_v_reversal_bounce:
                    logger.info(
                        f"🚫 [VETO_V_REVERSAL_SHORT] {symbol} 正在進行 V 轉反彈 (收陽且價格站上 MA5/MA15，"
                        f"c={c_c:.6f}, o={c_o:.6f}, m3={m5_c:.6f}, m15={m15_c:.6f})，強制一票否決任何 OPEN_SHORT 請求！"
                    )
                    can_open_short = False
                else:
                    candidate_side = "SHORT"
                    if entry_type in ("MOMENTUM_FOLLOW_SHORT", "EARLY_BEAR_BREAKDOWN_SHORT"):
                        pass
                    elif is_cross_mid_short:
                        entry_type = "PIVOT_SHORT_CROSS_MID"
                    elif is_valid_dc or is_confirmed_death_cross_ready or is_1m_confirmed_bear:
                        entry_type = "NONE_DEAD_CROSS"
                    else:
                        entry_type = "PIVOT_SHORT"
                    pivot_triggered = True

            # ── 峰谷轉向雙向即時開倉引擎（解除 KC 中軌單邊方向鎖死 + 三因子盤整嚴格濾網）──
            is_peak_valley_inflection = False
            inflection_decision: Optional[Dict[str, Any]] = None
            pv_entry_struct: Dict[str, Any] = {}
            if (not has_position) and (not pivot_cooldown_blocked) and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 15:
                from core.services.low_latency_exit import extract_entry_structure_context
                inflection_decision, pv_ready, pv_reentry_reason = _get_pv_inflection_status()
                if (
                    not pivot_triggered
                    and inflection_decision is not None
                    and inflection_decision.get("action") in ("OPEN_LONG", "OPEN_SHORT")
                    and not inflection_decision.get("is_consolidation")
                ):
                    if not pv_ready:
                        logger.info(
                            "🚫 [PV_EXIT_ANTI_WHIPSAW] %s side=%s reason=%s",
                            symbol, inflection_decision["side"], pv_reentry_reason,
                        )
                    else:
                        candidate_side = inflection_decision["side"]
                        entry_type = f"PEAK_VALLEY_INFLECTION_{candidate_side}"
                        pivot_triggered = True
                        is_peak_valley_inflection = True
                        pv_entry_struct = extract_entry_structure_context(raw_df, candidate_side)
                        logger.info(
                            "🎯 [PEAK_VALLEY_INFLECTION_%s] %s trigger=%s kc_dir=%s width=%.4f spread=%.4f adx=%s",
                            candidate_side,
                            symbol,
                            inflection_decision.get("trigger_mode"),
                            inflection_decision.get("kc_direction"),
                            float(inflection_decision.get("kc_width_pct") or 0.0),
                            float(inflection_decision.get("ma_spread_pct") or 0.0),
                            inflection_decision.get("adx"),
                        )

            # 🚫 一般趨勢單仍保留防逆勢檢查；唯已確立之峰谷轉向訊號解除 KC 中軌方向鎖死
            if pivot_triggered and candidate_side == "SHORT" and not is_peak_valley_inflection:
                if is_zoomed_out_bullish or is_macro_htf_bullish or macro_trend == "BULLISH":
                    logger.info(
                        f"🚫 [BLOCKED_SHORT_ON_BULLISH_TREND] {symbol} 拉遠看是大週期多頭趨勢 "
                        f"(zoomed_bull={is_zoomed_out_bullish}, htf_bull={is_macro_htf_bullish}, macro={macro_trend})，"
                        f"100% 一票否決開空，嚴防逆勢摸頂被軋空！"
                    )
                    pivot_triggered = False
                    candidate_side = None

            if pivot_triggered and candidate_side == "LONG" and not is_peak_valley_inflection:
                if is_zoomed_out_bearish or is_macro_htf_bearish or macro_trend == "BEARISH":
                    logger.info(
                        f"🚫 [BLOCKED_LONG_ON_BEARISH_TREND] {symbol} 拉遠看是大週期空頭趨勢 "
                        f"(zoomed_bear={is_zoomed_out_bearish}, htf_bear={is_macro_htf_bearish}, macro={macro_trend})，"
                        f"100% 一票否決開多，嚴防逆勢摸底接飛刀！"
                    )
                    pivot_triggered = False
                    candidate_side = None

            # 🚀 妖幣優先通道（多空對稱）：頂部死叉/底部金叉、大實體破軌，豁免 KC 中軌滯後斜率與平倉冷卻
            meme_priority_entry = None
            from core.symbol_aliases import is_meme_breakout_symbol as _is_meme_priority_symbol
            if (not has_position) and _is_meme_priority_symbol(symbol):
                from core.gates.master_gate_funnel import MasterGateFunnel as _PriorityFunnel
                from core.gates.master_gate_funnel import market_snapshot_from_frame as _priority_snapshot
                try:
                    _p_snap = _priority_snapshot(raw_df, symbol, curr_quote, macro_trend)
                except (KeyError, TypeError, ValueError, OverflowError, AttributeError, IndexError):
                    _p_snap = None
                if _p_snap is not None:
                    for _p_side in ("SHORT", "LONG"):
                        _p_trigger = _PriorityFunnel.priority_channel(_p_snap, _p_side)
                        if _p_trigger:
                            meme_priority_entry = _p_trigger
                            candidate_side = _p_side
                            entry_type = "MEME_" + _p_trigger.replace("TRIGGER_", "") + "_" + _p_side
                            pivot_triggered = True
                            is_peak_valley_inflection = False
                            logger.info(
                                f"⚡ [MEME_PRIORITY_CHANNEL] {symbol} {_p_side} {_p_trigger} 命中優先通道，"
                                f"豁免 KC 中軌滯後斜率與平倉冷卻，交由 MasterGateFunnel 終審"
                            )
                            break

            ma5_angle_opportunity = None
            if not has_position:
                ma5_angle_opportunity = UltimateShortExitGatekeeper.ma5_angle_opportunity(
                    raw_df, curr_quote,
                )
                if ma5_angle_opportunity:
                    angle_side, angle_value, angle_candle_timestamp_ms = ma5_angle_opportunity
                    if cls._ma5_angle_latched(
                        account, symbol, angle_candle_timestamp_ms,
                    ):
                        logger.info(
                            "MA5_ANGLE_SIGNAL_LATCHED symbol=%s side=%s candle_timestamp_ms=%s",
                            symbol, angle_side, angle_candle_timestamp_ms,
                        )
                        ma5_angle_opportunity = None
                    elif pivot_triggered and candidate_side != angle_side:
                        logger.warning(
                            "ENTRY_AUTHORITY_CONFLICT symbol=%s existing_side=%s ma5_angle_side=%s action=BLOCK",
                            symbol, candidate_side, angle_side,
                        )
                        pivot_triggered = False
                        candidate_side = None
                        meme_priority_entry = None
                    elif not pivot_triggered:
                        candidate_side = angle_side
                        entry_type = f"MA5_75_DEGREE_{angle_side}"
                        pivot_triggered = True
                        logger.info(
                            "MA5_ANGLE_ENTRY_CANDIDATE symbol=%s side=%s degrees=%.4f",
                            symbol, angle_side, angle_value,
                        )

            # 🛑 2026-11-01 最高鐵律：開倉必須通過 evaluate_direction_master_lock 與 MasterGateFunnel
            # 做空：MA5 必須向下且小於 MA15，當前必須是紅K陰線 (curr_close < curr_open)，否則 100% 嚴禁開空！
            # 做多：MA5 必須向上且大於 MA15，當前必須是綠K陽線 (curr_close > curr_open)，否則 100% 嚴禁開多！
            if pivot_triggered and candidate_side:
                from core.gates.direction_master_lock import evaluate_direction_master_lock
                from core.gates.master_gate_funnel import MasterGateFunnel, market_snapshot_from_frame

                # 1. 執行 Direction Master Lock 檢查（妖幣優先通道改由 MasterGateFunnel 同側完整重驗；峰谷轉向通道由 CentralEntryGate 實盤雙重核驗）
                if meme_priority_entry:
                    p_dir_ok, p_dir_reason, p_dir_details = True, "MEME_PRIORITY_CHANNEL", {}
                elif is_peak_valley_inflection:
                    p_dir_ok, p_dir_reason, p_dir_details = True, "PEAK_VALLEY_INFLECTION_CHANNEL", {}
                else:
                    p_dir_ok, p_dir_reason, p_dir_details = evaluate_direction_master_lock(
                        candidate_side,
                        ma5_curr=m5_c,
                        ma5_prev=m5_p,
                        kc_middle=kc_mid,
                        direction_override=False,
                        ma15_curr=m15_c,
                        curr_close=closed_c_entry if (closed_c_entry is not None and math.isfinite(closed_c_entry)) else c_c,
                        curr_open=closed_o_entry if (closed_o_entry is not None and math.isfinite(closed_o_entry)) else c_o,
                    )
                if not p_dir_ok:
                    logger.info(
                        f"🚫 [BLOCKED_PIVOT_DIRECTION_LOCK] {symbol} 轉折開倉 ({candidate_side} {entry_type}) "
                        f"遭方向總開關攔截: {p_dir_reason} (m5_c={m5_c:.6f}, m5_p={m5_p:.6f}, m15={m15_c:.6f}, c={c_c:.6f}, o={c_o:.6f})"
                    )
                    pivot_triggered = False
                    candidate_side = None
                elif not is_peak_valley_inflection:
                    # 2. 執行 MasterGateFunnel 兩階段漏斗與黑名單審查
                    try:
                        m_snap = market_snapshot_from_frame(raw_df, symbol, curr_quote, macro_trend)
                    except (KeyError, TypeError, ValueError, OverflowError, AttributeError, IndexError) as exc:
                        logger.warning("MASTER_GATE_CLOSED_DATA_REQUIRED symbol=%s error=%s", symbol, exc)
                        return {"status": "VETO", "symbol": symbol, "reason": "MASTER_GATE_CLOSED_DATA_REQUIRED"}
                    if candidate_side == "LONG":
                        funnel_res = MasterGateFunnel.evaluate_open_long(m_snap)
                    else:
                        funnel_res = MasterGateFunnel.evaluate_open_short(m_snap)

                    if funnel_res.can_execute and entry_type.startswith("MA5_75_DEGREE_"):
                        from core.gates.macro_candle_structure_gate import MacroCandleStructureGate
                        from core.services.candle_data import closed_entry_candles

                        closed_frame = closed_entry_candles(raw_df)
                        if len(closed_frame) < 1:
                            macro_allowed, macro_reason = False, "VETO_CLOSED_CANDLE_UNAVAILABLE"
                        else:
                            macro_bar = closed_frame.iloc[-1].to_dict()
                            macro_indicators = {
                                "kc_upper": m_snap.kc_upper,
                                "kc_lower": m_snap.kc_lower,
                                "kc_middle": m_snap.kc_middle,
                                "ma15": m_snap.ma15_curr,
                                "atr": m_snap.atr,
                                "kc_middle_previous": m_snap.kc_middle_prev,
                                "ma15_previous": m_snap.ma15_prev,
                                "ma5_current": m_snap.ma5_curr,
                            }
                            macro_allowed, macro_reason = MacroCandleStructureGate(
                                max_bias_atr=1.2,
                            ).can_open(
                                candidate_side,
                                macro_bar,
                                macro_indicators,
                                symbol=symbol,
                                entry_pattern="REVERSAL",
                            )
                        if not macro_allowed:
                            logger.info(
                                "MA5_ANGLE_MACRO_VETO symbol=%s side=%s reason=%s",
                                symbol, candidate_side, macro_reason,
                            )
                            pivot_triggered = False
                            candidate_side = None

                    if not funnel_res.can_execute:
                        logger.info(
                            f"🚫 [BLOCKED_BY_MASTER_GATE_FUNNEL] {symbol} {candidate_side} 未通過 MasterGateFunnel 審查: "
                            f"{funnel_res.reason} | details={funnel_res.details}"
                        )
                        pivot_triggered = False
                        candidate_side = None

            if pivot_triggered and candidate_side and entry_type:
                logger.info(f"🎯 [PIVOT_ENTRY_TRIGGERED] {symbol} 無持倉狀態捕獲轉折/交叉！發起 {candidate_side} 開倉 ({entry_type})")
                print(f"🎯 [PIVOT_ENTRY_TRIGGERED] {symbol} {candidate_side} {entry_type}", flush=True)
                is_swing = entry_type in ("PIVOT_LONG", "PIVOT_SHORT", "SWING_PIVOT")
                is_ma5_angle_entry = entry_type.startswith("MA5_75_DEGREE_")
                if is_ma5_angle_entry and not await cls._claim_ma5_angle_latch(
                    account,
                    symbol,
                    candidate_side,
                    ma5_angle_opportunity[2],
                ):
                    return {
                        "status": "VETO",
                        "symbol": symbol,
                        "reason": "MA5_ANGLE_SIGNAL_ALREADY_CLAIMED_OR_LATCH_PERSIST_FAILED",
                    }
                is_cm = ("CROSS" in entry_type or "MID" in entry_type or "V_LONG" in entry_type)
                is_breakdown = ("BREAKDOWN" in entry_type or "BREAKOUT" in entry_type)
                is_momentum = ("MOMENTUM" in entry_type or "CONTINUATION" in entry_type)

                if is_swing:
                    sig_type = "SWING_PIVOT"
                elif is_breakdown:
                    sig_type = "ENTRY_SHORT_BREAKDOWN" if candidate_side == "SHORT" else "ENTRY_LONG_BREAKOUT"
                elif is_momentum:
                    sig_type = "CONTINUATION_OUTSIDE_KC_SHORT" if candidate_side == "SHORT" else "CONTINUATION_OUTSIDE_KC_LONG"
                elif is_cm:
                    sig_type = "ENTRY_SHORT_MA5_MA15_CROSS_MID" if candidate_side == "SHORT" else "ENTRY_LONG_MA5_MA15_CROSS_MID"
                else:
                    sig_type = "ENTRY_SHORT_BREAKDOWN" if candidate_side == "SHORT" else "ENTRY_LONG_BREAKOUT"

                target_pivot_df = raw_df if raw_df is not None else klines_df
                intrabar_flag = False
                try:
                    intrabar_flag = bool(target_pivot_df is not None and not bool(target_pivot_df.iloc[-1].get("is_closed", True)))
                except Exception:
                    intrabar_flag = False
                entry_meta = {
                    "side": candidate_side,
                    "direction_override": not is_ma5_angle_entry,
                    "is_agile_trigger": not is_ma5_angle_entry,
                    "entry_type": entry_type,
                    "is_peak_valley_inflection": bool(is_peak_valley_inflection),
                    "frame_5m": frame_5m if is_peak_valley_inflection else None,
                    "entry_swing_low": pv_entry_struct.get("entry_swing_low"),
                    "entry_swing_high": pv_entry_struct.get("entry_swing_high"),
                    "entry_kc_width_pct": pv_entry_struct.get("entry_kc_width_pct"),
                    "candidate_signal_type": (
                        f"ENTRY_MA5_75_DEGREE_REVERSAL_{candidate_side}"
                        if is_ma5_angle_entry else sig_type
                    ),
                    "is_swing_pivot": is_swing,
                    "is_cross_mid": bool(is_cm or is_breakdown or is_momentum),
                    "entry_pattern": "REVERSAL" if (is_ma5_angle_entry or is_peak_valley_inflection) else None,
                    "trigger": "TRIGGER_MA5_75_DEGREE" if is_ma5_angle_entry else None,
                    "no_rigid_sl": True,
                    "stop_loss_price": None,
                    "meme_wick_stop_loss": meme_wick_buffer_sl_long if candidate_side == "LONG" else meme_wick_buffer_sl_short,
                    "upstream_entry_gate_authorized": not is_ma5_angle_entry,
                    "intrabar_authorized": intrabar_flag,
                    "allow_kc_internal": not is_ma5_angle_entry,
                    "strategy_mode": "CONSERVATIVE" if is_ma5_angle_entry else "AGILE_MOMENTUM",
                    "raw_df": target_pivot_df,
                    "macro_trend": macro_trend,
                    "source": f"UNIFIED_PIPELINE|{entry_type}",
                    "atr": atr_v,
                }
                raw_df_instance = entry_meta.get("raw_df")
                if hasattr(raw_df_instance, "attrs"):
                    raw_df_instance.attrs["symbol"] = symbol
                    for k in ("direction_override", "is_agile_trigger", "is_cross_mid", "entry_type", "strategy_mode", "allow_kc_internal", "is_peak_valley_inflection"):
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
                if is_ma5_angle_entry:
                    return {
                        "status": "VETO",
                        "symbol": symbol,
                        "side": candidate_side,
                        "reason": "MA5_ANGLE_ENTRY_ATTEMPT_REJECTED",
                        "success": False,
                    }
                if method_rejection is not None:
                    logger.warning(
                        "ENTRY_METHOD_ALTERNATIVE_FINAL_VETO symbol=%s side=%s entry_type=%s",
                        symbol, candidate_side, entry_type,
                    )
                    return {
                        "status": "VETO", "symbol": symbol, "side": candidate_side,
                        "reason": "PIVOT_ENTRY_FINAL_GATE_VETO", "success": False,
                        "entry_rejections": method_rejection["entry_rejections"],
                    }
                logger.debug(f"⚠️ [PIVOT_ENTRY_FALLTHROUGH] {symbol} {entry_type} 未成交，繼續評估常規破軌/延續開倉引擎")

            # 若未觸發真峰谷起點，順暢承接常規開倉引擎（破軌開倉、MA5穿KC中軌、MA5/MA15交叉等）
            # 偵測候選方向訊號 (大週期順勢原則：多頭大週期只看多、空頭大週期只看空，絕不頻繁多空互反)
            candidate_signal = None
            ma5_curr = m5_c
            ma5_prev = m5_p
            ma15_curr = float(market_data.curr_k.get("ma15", getattr(market_data, "ma15", ma5_curr)))
            ma15_prev = float(market_data.prev_k.get("ma15", ma15_curr))
            entry_ma5_curr = ma5_curr
            entry_ma5_prev = ma5_prev
            entry_ma5_slope = entry_ma5_curr - entry_ma5_prev
            raw_df = getattr(market_data, "raw_df", None)
            live_cross_long = None
            live_cross_short = None
            ma_trend_aligned_long = False
            ma_trend_aligned_short = False
            entry_ma5_directional_long = False
            entry_ma5_directional_short = False
            if raw_df is not None and len(raw_df) >= 2:
                from core.gates.pipeline import fast_cross_evidence
                from core.services.candle_data import closed_entry_candles

                closed_ma = closed_entry_candles(raw_df)
                if len(closed_ma) >= 2:
                    if not {"ma5", "ma15"}.issubset(closed_ma.columns):
                        closed_ma = closed_ma.copy()
                        closed_ma["ma5"] = closed_ma["close"].rolling(5, min_periods=5).mean()
                        closed_ma["ma15"] = closed_ma["close"].rolling(15, min_periods=1).mean()
                    prior_ma = closed_ma.iloc[-2]
                    latest_ma = closed_ma.iloc[-1]
                    entry_ma5_prior = float(prior_ma["ma5"])
                    ma15_prior = float(prior_ma["ma15"])
                    entry_ma5_latest = float(latest_ma["ma5"])
                    ma15_latest = float(latest_ma["ma15"])
                    live_row = raw_df.iloc[-1]
                    raw_close = float(live_row.get("close", market_data.current_price))
                    live_entry_ma5 = float(live_row.get("ma5", entry_ma5_latest))
                    live_bar_is_closed = bool(live_row.get("is_closed", True))
                    if not live_bar_is_closed:
                        live_entry_ma5 += (float(market_data.current_price) - raw_close) / 5.0
                    long_entry_ma5_rising = (
                        entry_ma5_latest > entry_ma5_prior
                        if live_bar_is_closed else live_entry_ma5 > entry_ma5_latest
                    )
                    short_entry_ma5_falling = (
                        entry_ma5_latest < entry_ma5_prior
                        if live_bar_is_closed else live_entry_ma5 < entry_ma5_latest
                    )
                    ma_values = (entry_ma5_prior, ma15_prior, entry_ma5_latest, ma15_latest, live_entry_ma5)
                    if all(math.isfinite(value) and value > 0 for value in ma_values):
                        entry_ma5_curr = entry_ma5_latest
                        entry_ma5_prev = entry_ma5_prior
                        entry_ma5_slope = entry_ma5_curr - entry_ma5_prev
                        entry_ma5_directional_long = entry_ma5_latest > entry_ma5_prior and long_entry_ma5_rising
                        entry_ma5_directional_short = entry_ma5_latest < entry_ma5_prior and short_entry_ma5_falling
                        ma_trend_aligned_long = (
                            entry_ma5_directional_long
                            and ma15_latest > ma15_prior
                            and entry_ma5_latest > ma15_latest
                        )
                        ma_trend_aligned_short = (
                            entry_ma5_directional_short
                            and ma15_latest < ma15_prior
                            and entry_ma5_latest < ma15_latest
                        )
                live_cross_long = fast_cross_evidence(
                    raw_df, market_data.current_price, "LONG", symbol=symbol,
                )
                live_cross_short = fast_cross_evidence(
                    raw_df, market_data.current_price, "SHORT", symbol=symbol,
                )

            if not entry_ma5_directional_long and ma5_curr > ma5_prev:
                entry_ma5_directional_long = True
            if not entry_ma5_directional_short and ma5_curr < ma5_prev:
                entry_ma5_directional_short = True

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

            # MA5 交叉 KC 中軌進場授權（統一核心鐵律）：
            # 做多：前一根已收線 MA5 <= KC 中軌，當前已收線 MA5 > KC 中軌，當前 MA5 > 前一根 MA5 (斜率向上)
            # 做空：前一根已收線 MA5 >= KC 中軌，當前已收線 MA5 < KC 中軌，當前 MA5 < 前一根 MA5 (斜率向下)
            from core.gates.ma3_kc_midline_cross_gate import evaluate_ma5_kc_midline_cross
            from core.services.candle_data import closed_entry_candles

            closed_for_cross = closed_entry_candles(raw_df) if raw_df is not None else None
            entry_ma5_cross_long_ok, entry_ma5_cross_long_reason, entry_ma5_cross_long_meta = (
                evaluate_ma5_kc_midline_cross(closed_for_cross, "LONG", symbol)
                if closed_for_cross is not None and len(closed_for_cross) >= 2
                else (False, "NO_CLOSED_BARS", {})
            )
            entry_ma5_cross_short_ok, entry_ma5_cross_short_reason, entry_ma5_cross_short_meta = (
                evaluate_ma5_kc_midline_cross(closed_for_cross, "SHORT", symbol)
                if closed_for_cross is not None and len(closed_for_cross) >= 2
                else (False, "NO_CLOSED_BARS", {})
            )

            is_macro_bull = entry_kc_up
            is_macro_bear = entry_kc_down

            is_ma_cross_long = (
                entry_ma5_cross_long_ok
                and not is_macro_bear
                and kc_slope >= -0.000005
                and market_data.curr_k["close"] >= market_data.kc_middle
            )
            is_ma_cross_short = (
                entry_ma5_cross_short_ok
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
                        k2_entry_ma5 = float(cand_k2.get("ma5", k2_c))

                        # 🟢 多頭二次起飛 (CONTINUATION_LONG) 嚴格判定：
                        # K1: 陽線衝破 KC 上軌
                        # K2: 縮量小陰線回踩，實體 <= 0.5 * K1，且收盤未跌破 MA5
                        # K3: 必須為【實體陽線】且收盤突破 K2 高點，且 MA5 當前明確向上！
                        is_k1_breakout = (k1_c > k1_o and k1_c > k1_up)
                        is_k2_pullback_long = (k2_c < k2_o and k2_body < 0.5 * k1_body and k2_c >= k2_entry_ma5)
                        is_k3_confirm_long = (k3_c > k3_o and k3_c > k2_h and entry_ma5_curr > entry_ma5_prev)

                        if is_k1_breakout and is_k2_pullback_long and is_k3_confirm_long:
                            three_bar_long = True
                            three_bar_stop = float(k2_l)

                        # 🔴 空頭二次下殺 (CONTINUATION_SHORT) 嚴格判定：
                        # K1: 陰線跌破 KC 下軌
                        # K2: 縮量小陽線反彈，實體 <= 0.5 * K1，且收盤未突破 MA5
                        # K3: 必須為【實體紅 K/陰線】且收盤跌破 K2 低點，且 MA5 當前明確向下！
                        is_k1_breakdown = (k1_c < k1_o and k1_c < k1_low)
                        is_k2_pullback_short = (k2_c > k2_o and k2_body < 0.5 * k1_body and k2_c <= k2_entry_ma5)
                        is_k3_confirm_short = (k3_c < k3_o and k3_c < k2_l and entry_ma5_curr < entry_ma5_prev)

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
            # 1. MA5 當前必須明確向上: entry_ma5_curr > entry_ma5_prev
            # 2. MA5 必須嚴格位於 MA15 慢線上線上方: entry_ma5_curr > ma15_curr
            # 3. 當前 K 棒必須是實體陽線: curr_k['close'] > curr_k['open']
            # 只要 MA5 向下、或在 MA15 下方、或當前收陰，一律一票否決，嚴禁生成任何 LONG 訊號！
            ma15_curr = float(getattr(market_data, "ma15", getattr(market_data, "ema_slow", market_data.kc_middle)))
            curr_close = float(market_data.curr_k.get("close", market_data.current_price))
            curr_open = float(market_data.curr_k.get("open", curr_close))
            kc_mid_val = float(market_data.kc_middle)
            is_entry_ma5_trending_up = entry_ma5_curr > entry_ma5_prev
            from core.symbol_aliases import is_meme_breakout_symbol
            is_meme_contract = (
                is_meme_breakout_symbol(symbol)
                or any(k in str(symbol).upper() for k in ("LOBSTER", "PEPE", "CAP", "MEME"))
                or any(k in str(symbol) for k in ("龙虾", "龍蝦"))
            )
            is_entry_ma5_trending_up = entry_ma5_curr > entry_ma5_prev
            # 嚴格鐵律：做多 MA5 必須嚴格在 MA15 上方！
            is_entry_ma5_above_slow = (entry_ma5_curr > ma15_curr)
            is_candle_bullish = (curr_close > curr_open)
            hard_long_allowed = is_entry_ma5_trending_up and is_entry_ma5_above_slow and is_candle_bullish

            is_entry_ma5_trending_down = entry_ma5_curr < entry_ma5_prev
            # 嚴格鐵律：做空 MA5 必須嚴格在 MA15 下方！
            is_entry_ma5_below_slow = (entry_ma5_curr < ma15_curr)
            is_candle_bearish = (curr_close < curr_open)
            hard_short_allowed = is_entry_ma5_trending_down and is_entry_ma5_below_slow and is_candle_bearish

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
                "LONG", entry_ma5_curr, entry_ma5_prev, kc_mid_val,
                ma15_curr=ma15_curr, curr_close=curr_close, curr_open=curr_open,
                direction_override=False,
            )
            direction_short_ok, dir_short_reason, _ = evaluate_direction_master_lock(
                "SHORT", entry_ma5_curr, entry_ma5_prev, kc_mid_val,
                ma15_curr=ma15_curr, curr_close=curr_close, curr_open=curr_open,
                direction_override=False,
            )

            # 敏捷進攻架構：妖幣突破訊號與轉折交叉訊號嚴格依循 KC 主趨勢防線與方向總開關
            # 順勢鐵律：KC 中軌下行或價格在 KC 中軌之下時，100% 絕對禁止開多！
            #           KC 中軌上行或價格在 KC 中軌之上時，100% 絕對禁止開空！
            can_long = (
                hard_long_allowed
                and allow_long_defense
                and (not is_entry_ma5_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "LONG")
                and (market_regime != "RANGE" or range_breakout_side == "LONG")
                and (is_breakout_long or is_continuation_long or is_meme_contract or direction_long_ok)
                and (not is_macro_bear)
                and (kc_slope >= 0)
                and (curr_close > kc_mid_val)
                and (entry_ma5_curr > kc_mid_val)
                and (not is_sqz_now)
                and (
                    is_breakout_long or is_continuation_long or is_ma_cross_long or (
                        not is_bearish_wave
                        and ma_trend_aligned_long
                        and (entry_ma5_slope >= 0)
                        and (market_data.curr_k["close"] >= entry_ma5_curr)
                        and (market_data.current_price >= entry_ma5_curr)
                    )
                )
            )
            can_short = (
                hard_short_allowed
                and allow_short_defense
                and (not is_entry_ma5_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "SHORT")
                and (market_regime != "RANGE" or range_breakout_side == "SHORT")
                and (is_breakdown_short or is_continuation_short or is_meme_contract or direction_short_ok)
                and (not is_macro_bull)
                and (kc_slope <= 0)
                and (curr_close < kc_mid_val)
                and (entry_ma5_curr < kc_mid_val)
                and (not is_sqz_now)
                and (
                    is_breakdown_short or is_continuation_short or is_ma_cross_short or (
                        not is_bullish_wave
                        and ma_trend_aligned_short
                        and (entry_ma5_slope <= 0)
                        and (market_data.curr_k["close"] <= entry_ma5_curr)
                        and (market_data.current_price <= entry_ma5_curr)
                    )
                )
            )

            # ══════════════════════════════════════════════════════════════════
            # 敏捷雙重開倉觸發點（Dual Agile Entry Triggers）
            # 解決滯後與平倉後不動作問題：破軌即開 / MA5 穿中軌即開 / 順勢延續即開！
            # ══════════════════════════════════════════════════════════════════
            atr_val_now = float(getattr(market_data, "atr", 0.0) or 0.0)
            c_body_now = abs(curr_close - curr_open)
            kc_up_val = float(market_data.kc_upper)
            kc_low_val = float(market_data.kc_lower)

            # 🟢 多頭觸發點 A（MA5 與 MA15 黃金交叉發散確立，解決「剛觸碰就亂開」與「已經交叉好久卻不開倉」雙重痛點）：
            prev_kc_mid_val = float(market_data.prev_k.get("kc_middle", kc_mid_val) if market_data.prev_k else kc_mid_val)
            agile_cross_long = (
                (market_regime != "RANGE" or range_breakout_side == "LONG")
                and (not is_entry_ma5_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "LONG")
                and (entry_ma5_curr > ma15_curr)
                and ((entry_ma5_curr - ma15_curr) >= min_gc_dc_spread)
                and allow_long_defense
                and (not is_macro_bear)
                and (not is_zoomed_out_bearish)
                and is_3bar_bull_slope_ready
                and curr_close > kc_mid_val
                and curr_close > ma15_curr
                and entry_ma5_curr > kc_mid_val
                and entry_ma5_curr > entry_ma5_prev
                and curr_close > curr_open
                and c_body_now >= 0.16 * atr_val_now
                and has_room_to_upper_rail
                and is_closed_bar_bull_confirmed
                and curr_close < (kc_up_val - 0.10 * atr_val_now)
            )
            # 🟢 多頭觸發點 B（實體突破 KC 上軌或沿上軌外強勢延續開倉，解決平倉後不動作痛點，但拉遠看空頭時嚴禁逆勢反彈追多）：
            upper_wick_check = float(market_data.curr_k.get("high", curr_close)) - max(curr_close, curr_open)
            agile_breakout_long = (
                (market_regime != "RANGE" or range_breakout_side == "LONG")
                and (not is_entry_ma5_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "LONG")
                and (entry_ma5_curr > ma15_curr)
                and ((entry_ma5_curr - ma15_curr) >= min_gc_dc_spread)
                and allow_long_defense
                and (not is_macro_bear)
                and (not is_zoomed_out_bearish)
                and is_3bar_bull_slope_ready
                and (curr_close > kc_up_val or curr_quote > kc_up_val)
                and c_body_now >= 0.18 * atr_val_now
                and upper_wick_check <= c_body_now
                and curr_close > curr_open
                and entry_ma5_curr > entry_ma5_prev
                and is_closed_bar_bull_confirmed
            )
            # 🟢 多頭觸發點 C（黃金交叉確立後順勢延續補上車：解決「已經交叉好久卻不開倉」！只要黃金交叉發散確立且穩居中軌與 MA15 之上收陽，立即補倉買多！）：
            agile_continuation_long = (
                (market_regime != "RANGE" or range_breakout_side == "LONG")
                and (not is_entry_ma5_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "LONG")
                and (entry_ma5_curr > ma15_curr)
                and ((entry_ma5_curr - ma15_curr) >= min_gc_dc_spread)
                and allow_long_defense
                and (not is_macro_bear)
                and (not is_zoomed_out_bearish)
                and is_3bar_bull_slope_ready
                and curr_close > kc_mid_val
                and curr_close > ma15_curr
                and entry_ma5_curr > kc_mid_val
                and curr_close >= entry_ma5_curr
                and entry_ma5_curr > entry_ma5_prev
                and curr_close > curr_open
                and c_body_now >= 0.16 * atr_val_now
                and has_room_to_upper_rail
                and is_closed_bar_bull_confirmed
            )

            # 🔴 空頭觸發點 A（MA5 與 MA15 死亡交叉發散確立，解決「剛觸碰就亂開」與「已經交叉好久卻不開倉」雙重痛點）：
            agile_cross_short = (
                (market_regime != "RANGE" or range_breakout_side == "SHORT")
                and (not is_entry_ma5_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "SHORT")
                and (entry_ma5_curr < ma15_curr)
                and ((ma15_curr - entry_ma5_curr) >= min_gc_dc_spread)
                and allow_short_defense
                and (not is_macro_bull)
                and (not is_zoomed_out_bullish)
                and is_3bar_bear_slope_ready
                and curr_close < kc_mid_val
                and curr_close < ma15_curr
                and entry_ma5_curr < kc_mid_val
                and entry_ma5_curr < entry_ma5_prev
                and curr_close < curr_open
                and c_body_now >= 0.16 * atr_val_now
                and has_room_to_lower_rail
                and is_closed_bar_bear_confirmed
                and curr_close > (kc_low_val + 0.10 * atr_val_now)
            )
            # 🔴 空頭觸發點 B（實體跌破 KC 下軌或沿下軌外強勢延續開空，但拉遠看多頭時嚴禁逆勢殺跌）：
            lower_wick_check = min(curr_close, curr_open) - float(market_data.curr_k.get("low", curr_close))
            agile_breakdown_short = (
                (market_regime != "RANGE" or range_breakout_side == "SHORT")
                and (not is_entry_ma5_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "SHORT")
                and (entry_ma5_curr < ma15_curr)
                and ((ma15_curr - entry_ma5_curr) >= min_gc_dc_spread)
                and allow_short_defense
                and (not is_macro_bull)
                and (not is_zoomed_out_bullish)
                and is_3bar_bear_slope_ready
                and (curr_close < kc_low_val or curr_quote < kc_low_val)
                and c_body_now >= 0.18 * atr_val_now
                and lower_wick_check <= c_body_now
                and curr_close < curr_open
                and entry_ma5_curr < entry_ma5_prev
                and is_closed_bar_bear_confirmed
            )
            # 🔴 空頭觸發點 C（死亡交叉確立後順勢延續補上車：解決「已經交叉好久卻不開倉」！只要死亡交叉發散確立且受壓於中軌與 MA15 之下收陰，立即補倉賣空！）：
            agile_continuation_short = (
                (market_regime != "RANGE" or range_breakout_side == "SHORT")
                and (not is_entry_ma5_midline_adhered)
                and (not is_chop_zone or range_breakout_side == "SHORT")
                and (entry_ma5_curr < ma15_curr)
                and ((ma15_curr - entry_ma5_curr) >= min_gc_dc_spread)
                and allow_short_defense
                and (not is_macro_bull)
                and (not is_zoomed_out_bullish)
                and is_3bar_bear_slope_ready
                and curr_close < kc_mid_val
                and curr_close < ma15_curr
                and entry_ma5_curr < kc_mid_val
                and curr_close <= entry_ma5_curr
                and entry_ma5_curr < entry_ma5_prev
                and curr_close < curr_open
                and c_body_now >= 0.16 * atr_val_now
                and has_room_to_lower_rail
                and is_closed_bar_bear_confirmed
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

                # 價格距離中軌過遠不開倉的規定已全面去除（依使用者核心指令，順勢波段行情不因離中軌過遠而阻斷開倉）
                mid_dist_ratio_now = abs(curr_close - kc_mid_val) / kc_mid_val if kc_mid_val > 0 else 0.0

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
                # 谷底/頂部轉折開倉：只要極短線 MA5 拐頭順向且收線同色，即可第一時間進場，豁免 slow 慢線位置與 KC 中軌位置限制！
                # 谷底做多：只要非極端拉遠空頭、MA5 明顯向上拐頭、收陽線、未被前波阻力或超買壓制
                # 頂部做空：只要非極端拉遠多頭、MA5 明顯向下拐頭、收陰線、未被前波支撐或超賣壓制
                if pivot_side == "LONG":
                    # 妖幣谷底轉折開多：只要極短線 MA5 轉上（向上拐頭或比前根高，或當前已收陽站上 MA5）且當前收陽，100% 豁免大週期/中軌限制立即進場！
                    is_ma5_bull_turn = is_entry_ma5_trending_up or (curr_close > entry_ma5_curr and curr_close > curr_open) or (entry_ma5_curr > entry_ma5_prev)
                    pivot_long_ok = (
                        is_ma5_bull_turn
                        and is_candle_bullish
                        and (is_meme_contract or ((not is_zoomed_out_bearish) and (not is_macro_bear) and allow_long_defense))
                    )
                    if not pivot_long_ok:
                        logger.warning(
                            f"🛑 [HARD_VETO] {symbol} SWING_PIVOT_LONG 違反轉折開多門檻 "
                            f"(zoomed_bear={is_zoomed_out_bearish}, entry_ma5_up={is_entry_ma5_trending_up}, is_ma5_bull_turn={is_ma5_bull_turn}, bullish={is_candle_bullish})，一票否決！"
                        )
                    else:
                        swing_pivot = raw_swing_pivot
                elif pivot_side == "SHORT":
                    # 妖幣頂部轉折開空：極短線 MA5 轉下、或當前收陰、或滿足斷頭急砸特徵，100% 豁免大週期/中軌限制立即進場！
                    is_ma5_bear_turn = (
                        is_entry_ma5_trending_down
                        or (curr_close < entry_ma5_curr and curr_close < curr_open)
                        or (entry_ma5_curr < entry_ma5_prev)
                        or is_candle_bearish
                        or (raw_df is not None and len(raw_df) >= 2 and float(raw_df['close'].iloc[-1]) < float(raw_df['open'].iloc[-1]))
                    )
                    pivot_short_ok = (
                        (is_ma5_bear_turn or is_candle_bearish)
                        and (is_meme_contract or ((not is_zoomed_out_bullish) and (not is_macro_bull) and allow_short_defense))
                    )
                    if not pivot_short_ok:
                        logger.warning(
                            f"🛑 [HARD_VETO] {symbol} SWING_PIVOT_SHORT 違反轉折開空門檻 "
                            f"(zoomed_bull={is_zoomed_out_bullish}, entry_ma5_down={is_entry_ma5_trending_down}, is_ma5_bear_turn={is_ma5_bear_turn}, bearish={is_candle_bearish})，一票否決！"
                        )
                    else:
                        swing_pivot = raw_swing_pivot

            is_swing_pivot = bool(swing_pivot)
            if is_swing_pivot:
                candidate_side = swing_pivot["side"]
                if candidate_side == "LONG":
                    can_long = True
                elif candidate_side == "SHORT":
                    can_short = True

            # 🏛️ Stage 3.2 MTF 雙重過濾硬鎖定（情況 A/B/C/D 絕對執行）：
            # 大週期為空頭時：100% 鎖死開多（除非是確診谷底轉折反轉），且當小週期 MA5 > MA15 時視為 WAIT_REBOUND 觀望！
            # 大週期為多頭時：100% 鎖死開空（除非是確診頂部轉折反轉），且當小週期 MA5 < MA15 時視為 WAIT_PULLBACK 觀望！
            if is_zoomed_out_bearish:
                if not (is_swing_pivot and candidate_side == "LONG"):
                    can_long = False
                is_breakout_long = False
                is_continuation_long = False
                agile_breakout_long = False
                agile_cross_long = False
                agile_continuation_long = False
                if agile_signal and "LONG" in str(agile_signal):
                    is_agile_trigger = False
                    agile_signal = None
                if entry_ma5_curr >= ma15_curr and not (is_swing_pivot and candidate_side == "SHORT"):
                    can_short = False
            elif is_zoomed_out_bullish:
                if not (is_swing_pivot and candidate_side == "SHORT"):
                    can_short = False
                is_breakdown_short = False
                is_continuation_short = False
                agile_breakdown_short = False
                agile_cross_short = False
                agile_continuation_short = False
                if agile_signal and "SHORT" in str(agile_signal):
                    is_agile_trigger = False
                    agile_signal = None
                if entry_ma5_curr <= ma15_curr and not (is_swing_pivot and candidate_side == "LONG"):
                    can_long = False

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

            # 1. 平倉後強制冷卻檢查：嚴防秒級搶跑反手或同區間雙巴連環虧損
            acc_key = cls._get_account_key(account, symbol)
            last_exit = cls._last_exit_record.get(acc_key)
            if last_exit:
                last_exit_side = str(last_exit.get("side", "")).upper()
                last_exit_reason = str(last_exit.get("reason", "")).upper()
                is_loss_or_breach_exit = any(
                    k in last_exit_reason
                    for k in ("OPPOSITE_KC_BAND_BREACH", "REVERSAL", "RAIL_BREACH", "STOP_LOSS", "WATERFALL")
                )
                is_opposite_reversal_ready = bool(
                    (last_exit_side == "SHORT" and is_1m_confirmed_bull and is_closed_bar_bull_confirmed and has_room_to_upper_rail and can_long)
                    or (last_exit_side == "LONG" and is_1m_confirmed_bear and is_closed_bar_bear_confirmed and has_room_to_lower_rail and can_short)
                )
                elapsed_sec = time.time() - float(last_exit.get("timestamp", 0.0))
                min_cooldown_sec = 60.0 if is_loss_or_breach_exit else 45.0
                # 順勢突破或多頭/空頭排列時豁免平倉冷卻
                is_bull_breakout_stage32 = bool((is_zoomed_out_bullish or macro_trend == "BULLISH") and (curr_quote > m15_curr or market_data.curr_k.get("close", 0) > market_data.kc_upper))
                is_bear_breakdown_stage32 = bool((is_zoomed_out_bearish or macro_trend == "BEARISH") and (curr_quote < m15_curr or market_data.curr_k.get("close", 0) < market_data.kc_lower))
                has_meme_priority_entry = bool(meme_priority_entry)
                if elapsed_sec < min_cooldown_sec and not (
                    is_bull_breakout_stage32 or is_bear_breakdown_stage32
                    or has_meme_priority_entry
                ):
                    logger.debug(f"⏳ [COOLDOWN] {symbol} 平倉後 {min_cooldown_sec:.0f} 秒防雙巴保護中 ({elapsed_sec:.1f}s)")
                    return {"status": "VETO", "symbol": symbol, "reason": "VETO_EXIT_COOLDOWN"}
                if (
                    not is_opposite_reversal_ready
                    and not is_strong_breakout
                    and not (is_bull_breakout_stage32 or is_bear_breakdown_stage32)
                    and not has_meme_priority_entry
                    and elapsed_sec < 120
                ):
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

            # 🎯 最高優先級：頂部斷頭 (Top Pivot Breakdown) / 底部V轉 (Bottom Pivot Reversal) 極速點火
            from core.symbol_aliases import is_meme_breakout_symbol as _is_meme_check_symbol
            from core.gates.master_gate_funnel import market_snapshot_from_frame, MasterGateFunnel
            is_meme_sym = _is_meme_check_symbol(symbol)
            snap_for_pivot = None
            if is_meme_sym and raw_df is not None and isinstance(raw_df, pd.DataFrame) and len(raw_df) >= 2:
                try:
                    snap_for_pivot = market_snapshot_from_frame(raw_df, symbol, curr_quote, macro_trend)
                except Exception:
                    snap_for_pivot = None

            is_top_pivot_candidate = bool(snap_for_pivot and MasterGateFunnel.is_top_pivot_short(snap_for_pivot))
            is_bottom_pivot_candidate = bool(snap_for_pivot and MasterGateFunnel.is_bottom_pivot_long(snap_for_pivot))

            if is_top_pivot_candidate:
                candidate_signal = "SWING_PIVOT_SHORT"
                can_short = True
            elif is_bottom_pivot_candidate:
                candidate_signal = "SWING_PIVOT_LONG"
                can_long = True
            elif is_agile_trigger and agile_signal:
                candidate_signal = agile_signal
            elif is_true_bottom and (not is_zoomed_out_bearish) and hard_long_allowed and kc_slope >= 0 and curr_close >= kc_mid_val and entry_ma5_curr >= kc_mid_val:
                candidate_signal = "SWING_PIVOT_LONG"
                can_long = True
            elif is_true_peak and (not is_zoomed_out_bullish) and hard_short_allowed and kc_slope <= 0 and curr_close <= kc_mid_val and entry_ma5_curr <= kc_mid_val:
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
                    candidate_signal = "ENTRY_LONG_MA5_MA15_CROSS_MID"
                elif is_pullback_long:
                    candidate_signal = "TREND_PULLBACK_LONG"
                elif (market_data.curr_k["close"] > market_data.ema_fast and market_data.ema_fast >= market_data.ema_slow and market_data.kc_middle_slope >= 0 and ma5_curr > ma5_prev):
                    if market_data.curr_k["close"] > market_data.kc_upper:
                        candidate_signal = "CONTINUATION_OUTSIDE_KC_LONG" if p_close > p_up else "BREAKOUT_LONG"
                    else:
                        candidate_signal = "ENTRY_LONG_EMA_CROSS_MID"

            elif can_short and not is_zoomed_out_bullish:
                if is_ma_cross_short:
                    candidate_signal = "ENTRY_SHORT_MA5_MA15_CROSS_MID"
                elif is_pullback_short:
                    candidate_signal = "TREND_PULLBACK_SHORT"
                elif (market_data.curr_k["close"] < market_data.ema_fast and market_data.ema_fast <= market_data.ema_slow and market_data.kc_middle_slope <= 0 and ma5_curr < ma5_prev):
                    if market_data.curr_k["close"] < market_data.kc_lower:
                        candidate_signal = "CONTINUATION_OUTSIDE_KC_SHORT" if p_close < p_low else "BREAKDOWN_SHORT"
                    else:
                        candidate_signal = "ENTRY_SHORT_EMA_CROSS_MID"

            if candidate_signal:
                # Zero-trust whitelist: ordinary candidates need the closed-bar
                # MA5/MA15 cross; breakout and pivot routes use their own
                # closed-bar structural confirmation.
                from core.services.strict_ma3_ma15_entry_gate import evaluate_ma5_ma15_entry

                candidate_side = "LONG" if "LONG" in candidate_signal else "SHORT"

                # ── 🛡️ 全域順勢鐵律 + 拉遠視角防逆勢閘門 (Master KC & Zoomed-Out Trend Isolation Gate) ──
                # 0. 綠 K 實體開多、紅 K 實體開空鐵律（最高準則：嚴禁收陰開多、嚴禁收陽開空）
                if candidate_side == "LONG" and not is_candle_bullish:
                    logger.warning(
                        f"🛑 [DIRECTIONAL_CANDLE_VETO] {symbol} {candidate_signal} 欲開多但非綠 K 實體 (close={curr_close:.6f} <= open={curr_open:.6f})，一票否決！"
                    )
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_LONG_DIRECTIONAL_CANDLE", "details": "要綠 K 實體才能開多單！"}
                if candidate_side == "SHORT" and not is_candle_bearish:
                    logger.warning(
                        f"🛑 [DIRECTIONAL_CANDLE_VETO] {symbol} {candidate_signal} 欲開空但非紅 K 實體 (close={curr_close:.6f} >= open={curr_open:.6f})，一票否決！"
                    )
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_SHORT_DIRECTIONAL_CANDLE", "details": "要紅 K 實體才能開空單！"}

                # 0. 🐒 猴市只做突破（方案 3）：猴市環境下，未出現已收線實體突破 30 根區間高/低點，100% 一票否決任何常規開倉！
                if market_regime == "RANGE" and range_breakout_side != candidate_side:
                    logger.info(
                        f"🚫 [BLOCKED_RANGE_MONKEY_MARKET] {symbol} 當前處於【猴市震盪】(cross_count={ema60_cross_count}, flat={is_flat_macro})，"
                        f"尚未確認實體突破區間 (range_high={range_high}, range_low={range_low})，一票否決摸頂摸底，嚴防雙巴！"
                    )
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_RANGE_MONKEY_MARKET", "details": "猴市震盪市場，嚴格等待突破才開倉！"}

                # 0.05 🚫 【使用者最高鐵律：震盪區不要開倉、MA5中軌貼合不開倉】
                if is_entry_ma5_midline_adhered:
                    logger.info(
                        f"🚫 [BLOCKED_MA5_MIDLINE_ADHERED] {symbol} MA5 與 KC 中軌/MA15 貼合 (entry_ma5={entry_ma5_curr:.6f}, kc_mid={kc_mid_val:.6f}, "
                        f"dist_ratio={ma_dist_now:.5f}, m5_m15_spread={entry_ma5_ma15_spread_ratio:.5f})，嚴格禁止在貼合區開倉！"
                    )
                    return {
                        "status": "VETO",
                        "symbol": symbol,
                        "reason": "BLOCKED_MA5_MIDLINE_ADHERED",
                        "details": "MA5 與 KC 中軌貼合，缺乏明確趨勢動能，嚴禁開倉！",
                    }
                if is_chop_zone and range_breakout_side != candidate_side:
                    logger.info(
                        f"🚫 [BLOCKED_CHOP_CONSOLIDATION_ZONE] {symbol} 當前處於震盪糾纏區 (regime={market_regime}, sqz={is_sqz_now}, "
                        f"flat_kc={is_kc_slope_flat}, mid_cross_8={entry_ma5_mid_cross_8}, m15_cross_8={entry_ma5_m15_cross_8})，嚴格禁止在震盪區開倉！"
                    )
                    return {
                        "status": "VETO",
                        "symbol": symbol,
                        "reason": "BLOCKED_CHOP_CONSOLIDATION_ZONE",
                        "details": "當前處於震盪區或均線糾纏區，嚴禁開倉！",
                    }

                # 0.1 ⚖️ 【使用者最高鐵律】：MA5 必須在 MA15 上方 (entry_ma5_curr > ma15_curr) 才准開多單；MA5 必須在 MA15 下方 (entry_ma5_curr < ma15_curr) 才准開空單！
                # 轉折特例 (is_swing_pivot) 豁免慢線限制，搶佔谷底/峰頂先機
                if not is_swing_pivot:
                    if candidate_side == "LONG" and entry_ma5_curr <= ma15_curr:
                        logger.info(
                            f"🚫 [BLOCKED_MA5_MA15_RULE] {symbol} 開多但 MA5 ({entry_ma5_curr:.6f}) <= MA15 ({ma15_curr:.6f})，嚴格一票否決！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_MA5_NOT_ABOVE_MA15", "details": "MA5 必須在 MA15 上方才准開多單！"}
                    if candidate_side == "SHORT" and entry_ma5_curr >= ma15_curr:
                        logger.info(
                            f"🚫 [BLOCKED_MA5_MA15_RULE] {symbol} 開空但 MA5 ({entry_ma5_curr:.6f}) >= MA15 ({ma15_curr:.6f})，嚴格一票否決！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_MA5_NOT_BELOW_MA15", "details": "MA5 必須在 MA15 下方才准開空單！"}

                # 0.12 🛑 【使用者最高鐵律：中軌走平、MA5走平、KC變小、KC喇叭口過度擴張一律不開倉】
                is_kc_mid_flat = bool((atr_v > 0 and abs(kc_slope) < 0.03 * atr_v) or abs(kc_slope) < 1e-6)
                if is_kc_mid_flat:
                    logger.info(f"🚫 [BLOCKED_KC_MIDDLE_FLAT] {symbol} KC 中軌斜率走平 ({kc_slope:.8f})，缺乏主趨勢傾角，100% 一票否決開倉！")
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_KC_MIDDLE_FLAT", "details": "中軌走平，嚴禁開倉！"}

                if candidate_side == "LONG" and entry_ma5_curr <= entry_ma5_prev:
                    logger.info(f"🚫 [BLOCKED_MA5_FLAT_LONG] {symbol} 開多但 MA5 走平或下拐 ({entry_ma5_curr:.6f} <= {entry_ma5_prev:.6f})，動能衰竭一票否決！")
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_MA5_FLAT_LONG", "details": "MA5 走平或下拐，嚴禁開多！"}

                if candidate_side == "SHORT" and entry_ma5_curr >= entry_ma5_prev:
                    logger.info(f"🚫 [BLOCKED_MA5_FLAT_SHORT] {symbol} 開空但 MA5 走平或上拐 ({entry_ma5_curr:.6f} >= {entry_ma5_prev:.6f})，動能減弱一票否決！")
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_MA5_FLAT_SHORT", "details": "MA5 走平或上拐，嚴禁開空！"}

                kc_band_width = kc_u - kc_lo
                is_kc_too_narrow = bool((atr_v > 0 and kc_band_width < 1.0 * atr_v) or is_sqz_now)
                if is_kc_too_narrow:
                    logger.info(f"🚫 [BLOCKED_KC_SQUEEZE_TOO_NARROW] {symbol} KC 通道過度收窄或處於擠壓 (width={kc_band_width:.6f} < 1.0 ATR)，空間不足一票否決！")
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_KC_SQUEEZE_TOO_NARROW", "details": "KC 變小過度縮口，嚴禁開倉！"}

                is_kc_horn_overextended = bool(
                    (atr_v > 0 and kc_band_width > 5.0 * atr_v)
                    or (m15_curr > 0 and abs(curr_quote - m15_curr) / m15_curr >= 0.020)
                )
                if is_kc_horn_overextended:
                    logger.info(f"🚫 [BLOCKED_KC_HORN_OVEREXTENDED] {symbol} KC 喇叭口過度放大或價格偏離 MA15 正負乖離過大 (>=2%)，已是噴發尾聲，一票否決追單！")
                    return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_KC_HORN_OVEREXTENDED", "details": "KC 喇叭口或乖離過大，嚴禁追單！"}

                # 0.15 🛑 【V 轉反彈廢除開空】：若當前價格已反彈站上 MA15 或正拉出強勢陽線，100% 一票否決滯後開空！
                if candidate_side == "SHORT" and (curr_quote > m15_curr or p_close > m15_curr or (p_close > p_open and (p_close - p_open) >= 0.25 * market_data.atr)):
                    logger.info(
                        f"🚫 [BLOCKED_SHORT_ON_V_REVERSAL] {symbol} 價格已強勢反彈站回 MA15 "
                        f"(quote={curr_quote:.6f}, c={p_close:.6f}, m15={m15_curr:.6f})，一票否決滯後開空，防止阿呆谷賣空！"
                    )
                    return {
                        "status": "VETO",
                        "symbol": symbol,
                        "reason": "BLOCKED_SHORT_ON_V_REVERSAL",
                        "details": "價格已反彈站回 MA15 或出現強勢陽線，嚴禁在反彈半山腰滯後賣空！",
                    }

                # 0.2 🛡️ 【三大危險區開倉防禦】：前低支撐/前高壓力牆、均線/外軌超賣超買乖離、外軌穿透影線止跌受阻
                # 妖幣開倉放寬：妖幣與轉折開倉不因一般支撐/壓力牆或微幅背離乖離而阻斷開倉！
                if candidate_side == "SHORT" and not allow_short_defense and not is_swing_pivot and not is_meme_contract:
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
                if candidate_side == "LONG" and not allow_long_defense and not is_swing_pivot and not is_meme_contract:
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

                # 1. 空頭趨勢嚴禁開多：常規開多拉遠看是空頭 (is_zoomed_out_bearish) 或 KC 單棒中軌下行 (kc_slope <= 0)，100% 一票否決！
                #    🚨【轉折特例豁免】：妖幣轉折開多 (is_swing_pivot) 或 谷底轉折 100% 豁免逆向中軌，立即搶進谷底！
                # 2. 多頭趨勢嚴禁開空：常規開空拉遠看是多頭 (is_zoomed_out_bullish) 或 KC 單棒中軌上行 (kc_slope >= 0)，100% 一票否決！
                #    🚨【轉折特例豁免】：妖幣轉折開空 (is_swing_pivot) 或 頂部轉折 100% 豁免逆向中軌，立即搶進頂部！
                if not is_swing_pivot:
                    if candidate_side == "LONG" and (is_zoomed_out_bearish or macro_trend == "BEARISH" or kc_slope <= 0):
                        logger.info(
                            f"🚫 [BLOCKED_LONG_ON_BEARISH_TREND] {symbol} KC 中軌下行或空頭趨勢 "
                            f"(zoomed_bear={is_zoomed_out_bearish}, macro={macro_trend}, kc_slope={kc_slope:.8f})，100% 絕對禁止在空頭下降通道中開多！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_LONG_ON_BEARISH_TREND", "details": "KC 中軌向下走空頭趨勢，強制阻斷買多！"}
                    if candidate_side == "SHORT" and (is_zoomed_out_bullish or macro_trend == "BULLISH" or kc_slope >= 0):
                        logger.info(
                            f"🚫 [BLOCKED_SHORT_ON_BULLISH_TREND] {symbol} KC 中軌上行或多頭趨勢 "
                            f"(zoomed_bull={is_zoomed_out_bullish}, macro={macro_trend}, kc_slope={kc_slope:.8f})，100% 絕對禁止在多頭上升通道中開空！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_SHORT_ON_BULLISH_TREND", "details": "KC 中軌向上走多頭趨勢，強制阻斷賣空！"}
                if not is_swing_pivot:
                    if candidate_side == "LONG" and (
                        is_zoomed_out_bearish
                        or is_macro_htf_bearish
                        or is_macro_bear
                        or macro_trend == "BEARISH"
                        or kc_slope <= 0
                        or m15_c < m15_p
                        or curr_close < kc_mid_val
                        or curr_close < ma15_curr
                        or entry_ma5_curr < kc_mid_val
                        or entry_ma5_curr <= entry_ma5_prev
                        or curr_close <= curr_open
                    ):
                        logger.info(
                            f"🚫 [BLOCKED_LONG_ON_BEARISH_TREND] {symbol} 違反開多精準方向門檻 (拉遠空頭={is_zoomed_out_bearish}, "
                            f"kc_slope_3={kc_slope_3_ref:.8f}, kc_slope_5={kc_slope_5_ref:.8f}, m15_slope_5={m15_slope_5_ref:.8f}, slope={kc_slope:.8f}, close={curr_close:.8f}, kc_mid={kc_mid_val:.8f})，100% 絕對禁止在空頭下降通道中開多！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_LONG_ON_BEARISH_TREND", "details": "拉遠看或中軌/MA15為空頭下降趨勢，強制阻斷買多！"}
                    if candidate_side == "SHORT" and (
                        is_zoomed_out_bullish
                        or is_macro_htf_bullish
                        or is_macro_bull
                        or macro_trend == "BULLISH"
                        or kc_slope >= 0
                        or m15_c > m15_p
                        or curr_close > kc_mid_val
                        or curr_close > ma15_curr
                        or entry_ma5_curr > kc_mid_val
                        or entry_ma5_curr >= entry_ma5_prev
                        or curr_close >= curr_open
                    ):
                        logger.info(
                            f"🚫 [BLOCKED_SHORT_ON_BULLISH_TREND] {symbol} 違反開空精準方向門檻 (拉遠多頭={is_zoomed_out_bullish}, "
                            f"kc_slope_3={kc_slope_3_ref:.8f}, kc_slope_5={kc_slope_5_ref:.8f}, m15_slope_5={m15_slope_5_ref:.8f}, slope={kc_slope:.8f}, close={curr_close:.8f}, kc_mid={kc_mid_val:.8f})，100% 絕對禁止在多頭上升通道中開空！"
                        )
                        return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_SHORT_ON_BULLISH_TREND", "details": "拉遠看或中軌/MA15為多頭上升趨勢，強制阻斷賣空！"}

                # ── 🛡️ 妖幣專用 3 大開倉前防護審查 (Demon Coin Defense Pre-Entry Gate) ──
                # 1. 訊號冷卻期機制 (Cooldown Period) - 若大週期已正式扭轉且站穩中軌與 MA5，豁免舊反手冷卻鎖死
                is_trend_confirmed_break = bool(
                    (candidate_side == "LONG" and kc_slope > 0 and curr_close > kc_mid_val and entry_ma5_curr > kc_mid_val and curr_close > curr_open)
                    or (candidate_side == "SHORT" and kc_slope < 0 and curr_close < kc_mid_val and entry_ma5_curr < kc_mid_val and curr_close < curr_open)
                )
                passed_cd, cd_msg = DemonCoinDefenseController.check_entry_cooldown(symbol, candidate_side)
                if not passed_cd and not is_trend_confirmed_break and not meme_priority_entry:
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
                    # 🛑 2026-11-01 使用者鐵律：entry_ma5 沒在 ma15 上方且方向向上不可以開多，向上只能開多！
                    # 轉折特例 (is_swing_pivot) 豁免慢線限制，搶佔谷底先機
                    if not is_swing_pivot:
                        if not hard_long_allowed:
                            logger.warning(
                                f"🛑 [HARD_LONG_VETO] {symbol} {candidate_signal} 違反開多絕對門檻 "
                                f"(entry_ma5_up={is_entry_ma5_trending_up}, entry_ma5>ma15={is_entry_ma5_above_slow}, bullish={is_candle_bullish})，一票否決！"
                            )
                            return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_HARD_LONG_MA5_OR_CANDLE"}
                        if not direction_long_ok:
                            logger.warning(
                                f"🛑 [DIRECTION_MASTER_LOCK] {symbol} {candidate_signal} 違反開多方向鎖 "
                                f"(entry_ma5={entry_ma5_curr:.6f}, kc_mid={kc_mid_val:.6f}, entry_ma5_prev={entry_ma5_prev:.6f})，一票否決！"
                            )
                            return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_DIRECTION_MASTER_LOCK_LONG"}
                elif candidate_side == "SHORT":
                    # 🛑 2026-11-01 使用者鐵律：entry_ma5 沒在 ma15 下方且方向沒向下不要開空，向下只能開空！
                    # 轉折特例 (is_swing_pivot) 豁免慢線限制，搶佔峰頂先機
                    if not is_swing_pivot:
                        if not hard_short_allowed:
                            logger.warning(
                                f"🛑 [HARD_SHORT_VETO] {symbol} {candidate_signal} 違反開空絕對門檻 "
                                f"(entry_ma5_down={is_entry_ma5_trending_down}, entry_ma5<ma15={is_entry_ma5_below_slow}, bearish={is_candle_bearish})，一票否決！"
                            )
                            return {"status": "VETO", "symbol": symbol, "reason": "BLOCKED_HARD_SHORT_MA5_OR_CANDLE"}
                        if not direction_short_ok:
                            logger.warning(
                                f"🛑 [DIRECTION_MASTER_LOCK] {symbol} {candidate_signal} 違反開空方向鎖 "
                                f"(entry_ma5={entry_ma5_curr:.6f}, kc_mid={kc_mid_val:.6f}, entry_ma5_prev={entry_ma5_prev:.6f})，一票否決！"
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
                        ma3_curr=entry_ma5_curr,
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
                    whitelist = evaluate_ma5_ma15_entry(
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
                    call_meta["macro_trend"] = macro_trend
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
                    if raw_df_instance is not None:
                        call_meta.setdefault("entry_gate_frame", raw_df_instance)
                        call_meta.setdefault("frame", raw_df_instance)
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

        return method_rejection or {"status": "IDLE", "symbol": symbol}

    _last_exit_record: Dict[str, Dict[str, Any]] = {}
    _last_trade_bar_time: Dict[str, int] = {}
    _hourly_entry_records: Dict[str, list] = {}
    _last_open_error: Dict[str, str] = {}
    MAX_ENTRIES_PER_HOUR: int = 15

    @classmethod
    def _get_account_key(cls, account: Any, symbol: str) -> str:
        acc_id = getattr(account, "account_id", None) or getattr(account, "id", None) or id(account)
        return f"{acc_id}:{symbol}"

    @classmethod
    def _ma5_angle_latched(cls, account: Any, symbol: str, candle_timestamp_ms: int) -> bool:
        state = getattr(account, "entry_filter_last", None)
        latches = state.get("ma5_angle_signal_latch") if isinstance(state, dict) else None
        latch = latches.get(symbol) if isinstance(latches, dict) else None
        return (
            isinstance(latch, dict)
            and latch.get("candle_timestamp_ms") == int(candle_timestamp_ms)
        )

    @classmethod
    async def _claim_ma5_angle_latch(
        cls, account: Any, symbol: str, side: str, candle_timestamp_ms: int,
    ) -> bool:
        """Persist one MA5 reversal attempt per symbol and live 1m candle."""
        key = cls._get_account_key(account, symbol)
        lock = cls._ma5_angle_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if cls._ma5_angle_latched(account, symbol, candle_timestamp_ms):
                return False
            state = getattr(account, "entry_filter_last", None)
            if not isinstance(state, dict):
                state = {}
                account.entry_filter_last = state
            latches = state.get("ma5_angle_signal_latch")
            if not isinstance(latches, dict):
                latches = {}
                state["ma5_angle_signal_latch"] = latches
            previous = latches.get(symbol)
            latches[symbol] = {
                "side": str(side).upper(),
                "candle_timestamp_ms": int(candle_timestamp_ms),
                "status": "CLAIMED",
            }
            save_state = getattr(account, "save_state", None)
            if not callable(save_state):
                if previous is None:
                    latches.pop(symbol, None)
                else:
                    latches[symbol] = previous
                return False
            try:
                try:
                    save_state(strict=True)
                except TypeError:
                    save_state()
            except Exception:
                logger.exception("MA5_ANGLE_LATCH_PERSIST_FAILED symbol=%s", symbol)
                if previous is None:
                    latches.pop(symbol, None)
                else:
                    latches[symbol] = previous
                return False
            return True

    @classmethod
    async def _try_structure_position_flip(
        cls,
        account: Any,
        symbol: str,
        position_side: str,
        frame: Any,
        quote: float,
        macro_trend: Optional[str],
        *,
        exit_reason: Optional[str] = None,
        position: Optional[Dict[str, Any]] = None,
        position_meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Re-evaluate and enter the opposite side only after a confirmed close is flat."""
        from core.gates.master_gate_funnel import (
            MasterGateFunnel, market_snapshot_from_frame,
        )
        from core.services.candle_data import closed_entry_candles
        from core.services.exits.authorized_position_exit import (
            dual_trend_exit_block_reason,
            is_dual_trend_emergency_exit,
        )

        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return None
        try:
            snapshot = market_snapshot_from_frame(frame, symbol, quote, macro_trend)
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError, IndexError) as exc:
            # 容錯處理：在回放或快照時間略有偏差時，若仍有充足已收線 K 棒，允許補正 attrs 後重新計算快照
            try:
                if isinstance(frame, pd.DataFrame) and len(frame) >= 8:
                    if hasattr(frame, "attrs") and isinstance(frame.attrs, dict):
                        frame.attrs["snapshot_ms"] = float(time.time() * 1000)
                        frame.attrs["timeframe_ms"] = 60_000.0
                    snapshot = market_snapshot_from_frame(frame, symbol, quote, macro_trend)
                else:
                    logger.info("POSITION_FLIP_DATA_BLOCK symbol=%s error=%s", symbol, exc)
                    return None
            except Exception as inner_exc:
                logger.info("POSITION_FLIP_DATA_BLOCK symbol=%s error=%s", symbol, inner_exc)
                return None
        held = str(position_side or "").upper()
        sharp_exit = exit_reason in SHARP_REVERSAL_EXIT_REASONS.values()
        angle_exit = exit_reason in MA5_ANGLE_REVERSAL_EXIT_REASONS.values()
        dual_trend_reversal = bool(
            position
            and exit_reason
            and not is_dual_trend_emergency_exit(exit_reason)
            and dual_trend_exit_block_reason(position, frame, position_meta) is None
        )
        candidate = (
            ("SHORT" if held == "LONG" else "LONG", "TRIGGER_DUAL_TREND_REVERSAL")
            if dual_trend_reversal
            else (
                (
                    "SHORT" if held == "LONG" else "LONG",
                    "TRIGGER_MA5_75_DEGREE" if angle_exit else "TRIGGER_SHARP_REVERSAL_15ATR",
                )
                if sharp_exit or angle_exit
                else MasterGateFunnel.evaluate_position_flip(snapshot, position_side)
            )
        )
        is_true_3pt_flip = False
        if (
            not sharp_exit
            and not angle_exit
            and
            held == "SHORT"
            and snapshot.is_closed
            and snapshot.kc_middle > snapshot.kc_middle_prev
            and str(snapshot.macro_trend).upper() != "BEARISH"
            and snapshot.close_price > snapshot.open_price
            and snapshot.close_price > snapshot.ma5_curr
            and snapshot.ma5_curr > snapshot.ma5_prev
        ):
            if candidate is None:
                candidate = ("LONG", "TRIGGER_BULLISH_ORIGIN_MA5_TURN")
            is_true_3pt_flip = True
        elif (
            not sharp_exit
            and not angle_exit
            and
            held == "LONG"
            and snapshot.is_closed
            and snapshot.kc_middle < snapshot.kc_middle_prev
            and str(snapshot.macro_trend).upper() != "BULLISH"
            and snapshot.close_price < snapshot.open_price
            and snapshot.close_price < snapshot.ma5_curr
            and snapshot.ma5_curr < snapshot.ma5_prev
        ):
            if candidate is None:
                candidate = ("SHORT", "TRIGGER_BEARISH_ORIGIN_MA5_TURN")
            is_true_3pt_flip = True

        if exit_reason in (
            "EXIT_LONG_TREND_REVERSAL", "EXIT_SHORT_TREND_REVERSAL",
            "EXIT_SHORT_BOTTOM_GREEN_CANDLE_MA5", "EXIT_LONG_TOP_RED_CANDLE_MA5",
            "EXIT_SHORT_TRUE_VALLEY_BOTTOM", "EXIT_LONG_TRUE_PEAK_TOP",
        ):
            if held == "LONG":
                candidate = ("SHORT", "TRIGGER_PEAK_REVERSAL_FLIP")
                is_true_3pt_flip = True
            elif held == "SHORT":
                candidate = ("LONG", "TRIGGER_VALLEY_REVERSAL_FLIP")
                is_true_3pt_flip = True
        if candidate is None:
            return None
        target_side, trigger = candidate
        is_dual_trend_location = bool(
            dual_trend_reversal and trigger == "TRIGGER_DUAL_TREND_REVERSAL"
        )
        if (target_side == "LONG" and snapshot.kc_middle <= snapshot.kc_middle_prev) or (
            target_side == "SHORT" and snapshot.kc_middle >= snapshot.kc_middle_prev
        ):
            return {
                "success": False,
                "side": target_side,
                "reason": "BLOCKED_LONG_ON_BEARISH_TREND" if target_side == "LONG" else "BLOCKED_SHORT_ON_BULLISH_TREND",
            }
        if symbol in getattr(account, "positions", {}):
            return {"success": False, "side": target_side, "reason": "POSITION_NOT_FLAT"}
        dual_trend_entry_context = None
        if is_dual_trend_location:
            expected_macro = "BULLISH" if target_side == "LONG" else "BEARISH"
            if str(snapshot.macro_trend).upper() != expected_macro:
                return {
                    "success": False,
                    "side": target_side,
                    "reason": "DUAL_TREND_MACRO_ALIGNMENT_STALE",
                }
            from core.services.entry_methods import (
                KC_DUAL_TREND_LOCATION,
                evaluate_entry_method,
                revalidate_entry_method,
            )

            last_closed_at = getattr(account, "last_closed_at", {}).get(symbol)
            if last_closed_at is None:
                return {
                    "success": False,
                    "side": target_side,
                    "reason": "DUAL_TREND_CLOSE_IDENTITY_UNAVAILABLE",
                }
            signal = evaluate_entry_method(
                frame,
                quote=quote,
                symbol=symbol,
                last_exit_timestamp=last_closed_at,
                expected_method=KC_DUAL_TREND_LOCATION,
                allow_same_bar_after_close=True,
            )
            if signal is None or signal.get("side") != target_side:
                return {
                    "success": False,
                    "side": target_side,
                    "reason": "DUAL_TREND_LOCATION_NOT_READY",
                }
            dual_trend_entry_context = {
                "entry_method_authority": KC_DUAL_TREND_LOCATION,
                "entry_method_signal": signal,
                "entry_method_bar_id": signal["confirmation_bar_id"],
                "entry_method_event_id": signal["event_id"],
                "entry_method_last_exit_timestamp": last_closed_at,
                "entry_method_allow_same_bar_after_close": True,
                "entry_type": "STRUCTURE_REVERSAL",
                "is_flip": True,
                "trigger": trigger,
            }
            method_allowed, method_reason = revalidate_entry_method(
                frame,
                symbol=symbol,
                side=target_side,
                quote=quote,
                context=dual_trend_entry_context,
            )
            if not method_allowed:
                return {
                    "success": False,
                    "side": target_side,
                    "reason": method_reason,
                }
        elif angle_exit:
            angle_signal = UltimateShortExitGatekeeper.ma5_angle_opportunity(frame, quote)
            if not angle_signal or angle_signal[0] != target_side:
                return {"success": False, "side": target_side, "reason": "LIVE_MA5_ANGLE_SIGNAL_STALE"}
            from core.gates.direction_master_lock import evaluate_direction_master_lock

            direction_ok, direction_reason, _ = evaluate_direction_master_lock(
                target_side,
                ma5_curr=snapshot.ma5_curr,
                ma5_prev=snapshot.ma5_prev,
                kc_middle=snapshot.kc_middle,
                direction_override=False,
                ma15_curr=snapshot.ma15_curr,
                curr_close=snapshot.close_price,
                curr_open=snapshot.open_price,
            )
            if not direction_ok:
                return {"success": False, "side": target_side, "reason": direction_reason}
            try:
                closed_frame = closed_entry_candles(frame)
            except (KeyError, TypeError, ValueError, OverflowError, AttributeError, IndexError):
                closed_frame = pd.DataFrame()
            if closed_frame.empty:
                return {"success": False, "side": target_side, "reason": "VETO_CLOSED_CANDLE_UNAVAILABLE"}
            from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

            macro_allowed, macro_reason = MacroCandleStructureGate(
                max_bias_atr=1.2,
            ).can_open(
                target_side,
                closed_frame.iloc[-1].to_dict(),
                {
                    "kc_upper": snapshot.kc_upper,
                    "kc_lower": snapshot.kc_lower,
                    "kc_middle": snapshot.kc_middle,
                    "ma15": snapshot.ma15_curr,
                    "atr": snapshot.atr,
                    "kc_middle_previous": snapshot.kc_middle_prev,
                    "ma15_previous": snapshot.ma15_prev,
                    "ma5_current": snapshot.ma5_curr,
                },
                symbol=symbol,
                entry_pattern="REVERSAL",
            )
            if not macro_allowed:
                return {"success": False, "side": target_side, "reason": macro_reason}
        if not is_dual_trend_location:
            location_veto = MasterGateFunnel.pivot_kc_location_veto(snapshot, target_side)
            if location_veto:
                logger.info(
                    "POSITION_FLIP_ENTRY_VETO symbol=%s side=%s trigger=%s reason=%s",
                    symbol, target_side, trigger, location_veto,
                )
                return {"success": False, "side": target_side, "reason": location_veto}
        if not is_true_3pt_flip and not is_dual_trend_location:
            gate = (
                MasterGateFunnel.evaluate_open_long(snapshot, is_in_cooldown=True)
                if target_side == "LONG"
                else MasterGateFunnel.evaluate_open_short(snapshot, is_in_cooldown=True)
            )
            if not gate.can_execute:
                logger.info(
                    "POSITION_FLIP_ENTRY_VETO symbol=%s side=%s trigger=%s reason=%s",
                    symbol, target_side, trigger, gate.reason,
                )
                return {"success": False, "side": target_side, "reason": gate.reason}

        account_key = cls._get_account_key(account, symbol)
        lock = cls._position_flip_locks.setdefault(account_key, asyncio.Lock())
        async with lock:
            if symbol in getattr(account, "positions", {}):
                return {"success": False, "side": target_side, "reason": "POSITION_NOT_FLAT"}
            if dual_trend_reversal and dual_trend_exit_block_reason(
                position, frame, position_meta,
            ) is not None:
                return {
                    "success": False,
                    "side": target_side,
                    "reason": "DUAL_TREND_REVERSAL_EVIDENCE_STALE",
                }
            if is_dual_trend_location:
                from core.services.entry_methods import revalidate_entry_method

                current_allowed, current_reason = revalidate_entry_method(
                    frame,
                    symbol=symbol,
                    side=target_side,
                    quote=quote,
                    context=dual_trend_entry_context,
                    final_submission=True,
                )
                if not current_allowed:
                    return {
                        "success": False,
                        "side": target_side,
                        "reason": current_reason,
                    }
            elif not is_true_3pt_flip:
                current_gate = (
                    MasterGateFunnel.evaluate_open_long(snapshot, is_in_cooldown=True)
                    if target_side == "LONG"
                    else MasterGateFunnel.evaluate_open_short(snapshot, is_in_cooldown=True)
                )
                if not current_gate.can_execute:
                    return {
                        "success": False, "side": target_side,
                        "reason": current_gate.reason,
                    }
            recent_entries = [
                stamp for stamp in cls._hourly_entry_records.get(
                    account_key, cls._hourly_entry_records.get(symbol, []),
                )
                if time.time() - stamp < 3600.0
            ]
            if len(recent_entries) >= cls.MAX_ENTRIES_PER_HOUR:
                return {
                    "success": False, "side": target_side,
                    "reason": "BLOCKED_HOURLY_ENTRY_LIMIT_EXCEEDED",
                }
            entry_meta = {
                "side": target_side,
                "entry_type": "STRUCTURE_REVERSAL",
                "entry_pattern": "REVERSAL" if angle_exit else "CROSSOVER",
                "candidate_signal_type": f"ENTRY_STRUCTURE_REVERSAL_{target_side}",
                "structure_confirmation": "MA5_75_DEGREE_LIVE" if angle_exit else "MA_CROSSOVER",
                "trigger": trigger,
                "raw_df": frame,
                "atr": snapshot.atr,
                "macro_trend": snapshot.macro_trend,
                "bar_timestamp": int(
                    frame.iloc[-1]["timestamp"] if angle_exit
                    else closed_entry_candles(frame).iloc[-1]["timestamp"]
                ),
                "intrabar_authorized": bool(angle_exit and not snapshot.is_closed),
            }
            if dual_trend_entry_context is not None:
                entry_meta.update(dual_trend_entry_context)
                signal = dual_trend_entry_context["entry_method_signal"]
                entry_meta.update({
                    "entry_type": "STRUCTURE_REVERSAL",
                    "entry_pattern": signal["entry_pattern"],
                    "structure_confirmation": signal["structure_confirmation"],
                    "structure_confirmed": True,
                    "channel_expanding": signal["channel_expanding"],
                    "signal_id": signal["event_id"],
                    "bar_timestamp": signal["confirmation_bar_id"],
                    "channel_confirmation_bar_id": signal["confirmation_bar_id"],
                    "source": "UNIFIED_PIPELINE|ENTRY_METHOD|KC_DUAL_TREND_LOCATION",
                    "is_flip": True,
                })
            if is_true_3pt_flip:
                entry_meta["direction_override"] = True
                entry_meta["allow_kc_internal"] = True
                entry_meta["is_flip"] = True
                entry_meta["_entry_method_validated"] = True
                entry_meta["raise_on_direction_lock"] = False
                if hasattr(frame, "attrs") and isinstance(frame.attrs, dict):
                    frame.attrs["direction_override"] = True
                    frame.attrs["is_pivot_flip_override"] = True
            order_accepted = await cls.open_position(
                engine_or_account=account,
                symbol=symbol,
                side=target_side,
                price=quote,
                reason=f"StructureReversal:{trigger}",
                meta=entry_meta,
            )
            opened_position = getattr(account, "positions", {}).get(symbol)
            opened = bool(
                order_accepted and isinstance(opened_position, dict)
                and str(opened_position.get("side") or "").upper() == target_side
            )
            logger.info(
                "POSITION_FLIP_%s symbol=%s from=%s to=%s trigger=%s",
                "FILLED" if opened else "REJECTED",
                symbol, position_side, target_side, trigger,
            )
            return {
                "success": bool(opened), "side": target_side,
                "reason": trigger if opened else "POSITION_FLIP_ENTRY_REJECTED",
            }

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
            if getattr(account, "system_emergency_halt", False) or getattr(account, "is_halted", False):
                logger.critical(f"🛑 [EXECUTION_BLOCKED_HALT] {symbol} 系統處於 HALT 緊急停機狀態，阻斷平倉派發！")
                return False
            positions_dict = getattr(account, "positions", {})
            pos_obj = positions_dict.get(symbol) if isinstance(positions_dict, dict) else None
            pos_meta_obj = getattr(account, "position_meta", {}).get(symbol, {}) if isinstance(getattr(account, "position_meta", None), dict) else {}
            pos_state = str((pos_obj or {}).get("state") or (pos_meta_obj or {}).get("state") or "").upper()
            if pos_state in ("MANUAL_REVIEW", "RECONCILIATION_REQUIRED", "HALT") or (pos_obj or {}).get("manual_review") or (pos_meta_obj or {}).get("manual_review"):
                logger.critical(f"🛑 [EXECUTION_BLOCKED_{pos_state or 'MANUAL_REVIEW'}] {symbol} 處於人工審核/對帳鎖定狀態，嚴禁自動平倉副作用！")
                return False
            if (pos_obj or {}).get("exit_in_progress") or (pos_meta_obj or {}).get("exit_in_progress") or symbol in getattr(account, "closing_lock", set()):
                logger.warning(f"⏳ [EXECUTION_BLOCKED_DUPLICATE_EXIT] {symbol} 已有在途平倉委託 (exit_in_progress/closing_lock)，阻斷重複平倉！")
                return False

            reason_upper = str(reason or "").upper()
            manual_exit = bool((meta or {}).get("is_manual")) or "MANUAL" in reason_upper
            emergency_exit = any(token in reason_upper for token in (
                "OPPOSITE_KC_BAND_BREACH", "EMERGENCY", "HARD_STOP",
                "CIRCUIT_BREAKER", "WATERFALL", "CATASTROPHIC_DUMP",
                "CATASTROPHIC_PUMP", "LIQUIDATION", "閃崩", "瀑布",
                "EXIT_LONG_AT_PEAK", "EXIT_SHORT_AT_BOTTOM",
                "TWO_CLOSED_ADVERSE_ABNORMAL",
                "PRIORITY_EXIT_",
            ))
            profit_protection_exit = any(token in reason_upper for token in (
                "PROFIT_LOCK", "TRAILING_LOCK", "RATCHET", "BREAKEVEN",
            ))
            position_side = str((pos_obj or {}).get("side") or "").upper()
            is_mtf_reversal = reason_upper.startswith(
                "MTF_KC_STATE_MACHINE_"
            ) and "REVERSAL_EXIT" in reason_upper
            if (
                position_side in ("LONG", "SHORT")
                and is_mtf_reversal
                and not manual_exit
                and not emergency_exit
                and not profit_protection_exit
            ):
                from core.services.mtf_kc_state_machine import (
                    evaluate_closed_15m_kc_trend,
                )

                frame_15m = (meta or {}).get("frame_15m")
                if not isinstance(frame_15m, pd.DataFrame):
                    frames = getattr(account, "_macro_trend_frame_15m", {})
                    frame_15m = frames.get(symbol) if isinstance(frames, dict) else None
                expected_exit_trend = (
                    "DOWNTREND" if position_side == "LONG" else "UPTREND"
                )
                position_meta = getattr(account, "position_meta", {}).get(symbol, {})
                exit_authority = (
                    position_meta.get("mtf_kc_exit_authority")
                    if isinstance(position_meta, dict) else None
                )
                forced_state_mismatch = (
                    isinstance(exit_authority, dict)
                    and isinstance(exit_authority.get("evidence"), dict)
                    and exit_authority["evidence"].get("forced_state_mismatch") is True
                )
                if forced_state_mismatch:
                    from core.services.mtf_kc_state_machine import mtf_exit_is_authorized

                    authorized = mtf_exit_is_authorized(
                        position_meta, reason, price, position_side,
                    )
                else:
                    trend = evaluate_closed_15m_kc_trend(frame_15m)
                    authorized = (
                        trend.get("valid")
                        and trend.get("direction") == expected_exit_trend
                    )
                if not authorized:
                    logger.info(
                        "POSITION_EXIT_VETO_MTF_TREND symbol=%s side=%s "
                        "reason=%s",
                        symbol, position_side, reason,
                    )
                    return False

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
                from core.symbol_aliases import is_meme_breakout_symbol
                if is_meme_breakout_symbol(symbol):
                    from core.services.exits.authorized_position_exit import UltimateExitGatekeeper
                    position = positions_dict.get(symbol) if isinstance(positions_dict, dict) else None
                    frame = raw_df if raw_df is not None else getattr(account, "_channel_exit_frames", {}).get(symbol)
                    if position is None or not UltimateExitGatekeeper.can_execute_exit(
                        symbol, position, getattr(account, "position_meta", {}).get(symbol, {}),
                        reason, price, frame=frame,
                        is_bar_closed=(meta or {}).get("is_bar_closed"),
                    ):
                        logger.info("[ULTIMATE_EXIT_VETO] symbol=%s reason=%s request=DISCARDED", symbol, reason)
                        return False
                is_limit = False if emergency_exit else bool((meta or {}).get("is_limit", False))
                success = await account.close_position(
                    symbol=symbol,
                    current_price=price,
                    close_reason=reason,
                    is_manual=manual_exit,
                    is_limit=is_limit,
                )
                if success and closed_side:
                    close_label = "CLOSE_SHORT" if str(closed_side).upper() == "SHORT" else "CLOSE_LONG"
                    logger.info(
                        "[UI_DISPLAY: %s] action=%s symbol=%s price=%s reason=%s",
                        "平空" if close_label == "CLOSE_SHORT" else "平多",
                        close_label, symbol, price, reason,
                    )
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
                        from core.services.ExchangeOrderManager import ExchangeOrderManager
                        await ExchangeOrderManager.cancel_native_stop_order(account, symbol)
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
            if hasattr(engine_or_account, "positions"):
                account = engine_or_account
            else:
                account = getattr(engine_or_account, "account", engine_or_account)

            if getattr(account, "system_emergency_halt", False) or getattr(account, "is_halted", False):
                cls._last_open_error[symbol] = "BLOCKED_SYSTEM_EMERGENCY_HALT"
                logger.critical(f"🛑 [EXECUTION_BLOCKED_HALT] {symbol} 系統處於 HALT 緊急停機狀態，阻斷開倉派發！")
                return False
            pos_meta_check = getattr(account, "position_meta", {}).get(symbol, {}) if isinstance(getattr(account, "position_meta", None), dict) else {}
            pos_state_check = str((pos_meta_check or {}).get("state") or "").upper()
            if pos_state_check in ("MANUAL_REVIEW", "RECONCILIATION_REQUIRED", "HALT") or (pos_meta_check or {}).get("manual_review"):
                cls._last_open_error[symbol] = f"BLOCKED_{pos_state_check or 'MANUAL_REVIEW'}"
                logger.critical(f"🛑 [EXECUTION_BLOCKED_{pos_state_check or 'MANUAL_REVIEW'}] {symbol} 處於人工審核/對帳鎖定狀態，嚴禁自動開倉副作用！")
                return False
            manual_entry = bool((meta or {}).get("manual_entry")) or bool(
                (meta or {}).get("is_manual")
            ) or "MANUAL" in str(reason or "").upper()
            wait_authority = (
                str((meta or {}).get("entry_authority") or "").upper() == "WAIT"
            )
            from core.services.entry_contract import (
                automatic_entry_kc_structure_problem,
            )

            entry_frame = (meta or {}).get("raw_df")
            if entry_frame is None:
                entry_frame = (meta or {}).get("entry_gate_frame")
            if entry_frame is None:
                entry_frame = (meta or {}).get("frame")
            if entry_frame is None:
                frames = getattr(account, "_channel_exit_frames", {})
                if isinstance(frames, dict):
                    entry_frame = frames.get(symbol)
            structure_problem = automatic_entry_kc_structure_problem(
                side, entry_frame, meta, reason,
                account=account, symbol=symbol,
            )
            if structure_problem:
                cls._last_open_error[symbol] = structure_problem
                return False
            if not manual_entry and not wait_authority:
                from core.services.mtf_kc_state_machine import (
                    _detect_ltf_outer_band_turn_state,
                    _has_ltf_outer_band_valley_or_peak_turn,
                    _valid_live_frame,
                    evaluate_closed_15m_kc_trend,
                    evaluate_htf_kc_state,
                    mtf_entry_midline_problem,
                )

                frame_15m = (meta or {}).get("frame_15m")
                if not isinstance(frame_15m, pd.DataFrame):
                    frames = getattr(account, "_macro_trend_frame_15m", {})
                    frame_15m = frames.get(symbol) if isinstance(frames, dict) else None
                raw_frame_1m = (meta or {}).get("raw_df")
                if isinstance(raw_frame_1m, pd.DataFrame):
                    midline_problem = mtf_entry_midline_problem(
                        raw_frame_1m, frame_15m, str(side).upper(),
                    )
                    if midline_problem is not None:
                        cls._last_open_error[symbol] = midline_problem
                        logger.warning(
                            "ENTRY_MTF_MIDLINE_VETO symbol=%s side=%s reason=%s",
                            symbol, side, midline_problem,
                        )
                        return False
                trend = evaluate_closed_15m_kc_trend(frame_15m)
                normalized_side = str(side).upper()
                expected_entry_trend = (
                    "UPTREND" if normalized_side == "LONG" else "DOWNTREND"
                )
                trend_direction = trend.get("direction")
                if (
                    trend.get("valid")
                    and isinstance((meta or {}).get("raw_df"), pd.DataFrame)
                ):
                    ltf_data = _valid_live_frame((meta or {}).get("raw_df"), 60_000.0)
                    ltf_turn = (
                        _detect_ltf_outer_band_turn_state(ltf_data[0])
                        if ltf_data is not None
                        else None
                    )
                    if ltf_turn == expected_entry_trend:
                        trend_direction = expected_entry_trend
                    elif trend_direction == "FLAT":
                        htf_live = evaluate_htf_kc_state(frame_15m)
                        if (
                            htf_live.get("valid")
                            and htf_live.get("state") == expected_entry_trend
                            and ltf_data is not None
                            and _has_ltf_outer_band_valley_or_peak_turn(
                                ltf_data[0], expected_entry_trend,
                            )
                        ):
                            trend_direction = expected_entry_trend
                if (
                    normalized_side not in ("LONG", "SHORT")
                    or not trend.get("valid")
                    or trend_direction != expected_entry_trend
                ):
                    cls._last_open_error[symbol] = (
                        f"BLOCKED_15M_KC_TREND_{trend_direction or 'UNKNOWN'}"
                    )
                    logger.warning(
                        "ENTRY_MTF_TREND_VETO symbol=%s side=%s trend=%s "
                        "trend_reason=%s",
                        symbol, side, trend_direction, trend.get("reason"),
                    )
                    return False
            entry_method_authority = (meta or {}).get("entry_method_authority")
            if entry_method_authority:
                from core.services.entry_methods import revalidate_entry_method

                method_frame = (meta or {}).get("raw_df")
                if method_frame is None:
                    cls._last_open_error[symbol] = "BLOCKED_ENTRY_METHOD_FRAME_UNAVAILABLE"
                    logger.warning(
                        "ENTRY_METHOD_FINAL_VETO symbol=%s reason=BLOCKED_ENTRY_METHOD_FRAME_UNAVAILABLE",
                        symbol,
                    )
                    return False
                method_allowed, method_reason = revalidate_entry_method(
                    method_frame,
                    symbol=symbol,
                    side=side,
                    quote=price,
                    context=meta or {},
                    final_submission=True,
                )
                if not method_allowed:
                    cls._last_open_error[symbol] = str(method_reason)
                    logger.warning(
                        "ENTRY_METHOD_FINAL_VETO symbol=%s side=%s method=%s reason=%s",
                        symbol, side, entry_method_authority, method_reason,
                    )
                    return False

            # 檢查持倉與鎖定 (反手開倉豁免平倉鎖並清理舊倉殘留)
            is_flip = (
                "FLIP" in str(reason).upper()
                or "FLIP" in str((meta or {}).get("entry_type", "")).upper()
                or (meta or {}).get("is_flip") is True
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
            from core.config import TEST_BUDGET_CAP_USDT
            if TEST_BUDGET_CAP_USDT > 0:
                wallet = min(wallet, TEST_BUDGET_CAP_USDT)
                available = min(available, TEST_BUDGET_CAP_USDT)

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
                    amount_usdt = min((wallet / 2.0) * 0.98, fee_safe_available)
            else:
                amount_usdt = min((wallet / 2.0) * 0.98, fee_safe_available)

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
                    from core.services.low_latency_exit import wrap_frame_ref as _wrap_frame_ref
                    for pv_attr in (
                        "is_peak_valley_inflection",
                        "is_pv_flip",
                        "pv_flip_bar_id",
                        "pv_flip_origin_exit_reason",
                        "pv_flip_watch_state",
                        "frame_5m",
                    ):
                        if (meta or {}).get(pv_attr) is not None:
                            v_pv = (meta or {}).get(pv_attr)
                            frame.attrs[pv_attr] = _wrap_frame_ref(v_pv) if pv_attr == "frame_5m" else v_pv
                    macro_label = (meta or {}).get("macro_trend")
                    if macro_label:
                        frame.attrs["macro_trend"] = str(macro_label).upper()
                    if entry_method_authority:
                        frame.attrs["entry_method_context"] = {
                            key: (
                                _wrap_frame_ref((meta or {}).get(key))
                                if key == "frame_15m"
                                else (meta or {}).get(key)
                            )
                            for key in (
                                "entry_method_authority",
                                "entry_method_signal",
                                "entry_method_bar_id",
                                "entry_method_event_id",
                                "entry_method_last_exit_timestamp",
                                "frame_15m",
                            )
                        }
                        frame.attrs["entry_method_context"]["symbol"] = symbol
                        frame.attrs["entry_method_quote"] = float(price)

            atr = float((meta or {}).get("atr") or (price * 0.015))
            no_rigid_sl = True
            initial_sl = 0.0  # 不要硬止損，只留帳戶硬止損、大瀑布及市場熔斷：全面取消單倉固定價位/ATR硬止損

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
                else (meta or {}).get("structure_confirmation")
                or (meta or {}).get("entry_pattern")
                or ("MID_LINE_BREAKDOWN" if side == "SHORT" and ("BREAKDOWN" in str(reason).upper() or "PIVOT" in str(reason).upper()) else "MID_LINE_BREAKOUT" if side == "LONG" and ("BREAKOUT" in str(reason).upper() or "PIVOT" in str(reason).upper()) else "BREAKOUT")
            )

            from core.gates.central_entry_gate import CentralEntryGate
            signal_id = (meta or {}).get("signal_id") or CentralEntryGate.generate_signal_id(symbol, side, "UNIFIED_PIPELINE")
            is_entry_method = bool(entry_method_authority)
            is_cross_mid = False if is_entry_method else bool(
                (meta or {}).get("is_cross_mid")
                or (meta or {}).get("allow_kc_internal")
                or (meta or {}).get("strategy_mode") == "AGILE_MOMENTUM"
                or (meta or {}).get("entry_type") == "SWING_PIVOT"
                or any(k in str(reason).upper() for k in ("CROSS_MID", "MA_CROSS", "EMA_CROSS", "MID_LINE", "PULLBACK", "AGILE"))
                or any(k in str(confirmation).upper() for k in ("MID_LINE", "CROSS", "AGILE"))
            )

            # 確保 entry_signal_code 完整傳遞以符合白名單檢查
            entry_signal_code = (
                (meta or {}).get("entry_signal_code")
                or (meta or {}).get("trigger")
                or ("TRIGGER_B_MA_CROSS" if entry_method_authority == "MA5_MA15_CROSS" else None)
                or ("KC_OUTER_PIVOT_LONG" if entry_method_authority in ("KC_OUTER_PIVOT_REVERSAL", "THREE_BAR_PIVOT_REVERSAL") and side.upper() == "LONG" else None)
                or ("KC_OUTER_PIVOT_SHORT" if entry_method_authority in ("KC_OUTER_PIVOT_REVERSAL", "THREE_BAR_PIVOT_REVERSAL") and side.upper() == "SHORT" else None)
                or ("TRIGGER_A_KC_BREAKOUT" if entry_method_authority in ("MEME_GATE_ENTRY", "KC_TWO_BAR_BREAKOUT") else None)
                or "TRIGGER_A_KC_BREAKOUT"
            )

            entry_context = {
                "is_manual": True,
                "managed_by_bot": True,
                "entry_mode": "WILD_SWING",
                "strategy_mode": "AGILE_MOMENTUM" if is_cross_mid else "CONSERVATIVE",
                "source": str((meta or {}).get("source") or "UNIFIED_PIPELINE"),
                "candidate_signal_type": (meta or {}).get("candidate_signal_type"),
                "entry_signal_code": entry_signal_code,
                "entry_method_authority": entry_method_authority,
                "entry_method_allow_same_bar_after_close": bool(
                    (meta or {}).get("entry_method_allow_same_bar_after_close")
                ),
                "mtf_cycle_reverse_after_close": bool(
                    (meta or {}).get("mtf_cycle_reverse_after_close")
                ),
                "mtf_colorless_reverse": bool(
                    (meta or {}).get("mtf_colorless_reverse")
                ),
                "entry_method_signal": (meta or {}).get("entry_method_signal"),
                "entry_method_bar_id": (meta or {}).get("entry_method_bar_id"),
                "entry_method_event_id": (meta or {}).get("entry_method_event_id"),
                "entry_method_last_exit_timestamp": (
                    (meta or {}).get("entry_method_last_exit_timestamp")
                    if (meta or {}).get("entry_method_last_exit_timestamp") is not None
                    else getattr(account, "last_closed_at", {}).get(symbol)
                ),
                "upstream_entry_gate_authorized": bool(
                    (meta or {}).get("upstream_entry_gate_authorized")
                ),
                "intrabar_authorized": bool((meta or {}).get("intrabar_authorized")),
                "entry_pattern": confirmation,
                "structure_confirmation": confirmation,
                "is_outer_entry": not is_cross_mid,
                "allow_kc_internal": bool((meta or {}).get("allow_kc_internal") or is_cross_mid),
                "direction_override": bool((meta or {}).get("direction_override") or is_cross_mid),
                "is_peak_valley_inflection": bool((meta or {}).get("is_peak_valley_inflection")),
                "entry_swing_low": (meta or {}).get("entry_swing_low"),
                "entry_swing_high": (meta or {}).get("entry_swing_high"),
                "entry_kc_width_pct": (meta or {}).get("entry_kc_width_pct"),
                "is_pv_flip": bool((meta or {}).get("is_pv_flip")),
                "pv_flip_bar_id": (meta or {}).get("pv_flip_bar_id"),
                "pv_flip_origin_exit_reason": (meta or {}).get("pv_flip_origin_exit_reason"),
                "pv_flip_watch_state": (meta or {}).get("pv_flip_watch_state"),
                "frame_5m": (meta or {}).get("frame_5m"),
                "frame_15m": (meta or {}).get("frame_15m"),
                "mtf_pending_candidate": (meta or {}).get("mtf_pending_candidate"),
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
                entry_context["entry_gate_quote"] = float(price)

            # 🛑 2026-11-01 執行邊界物理總閘門：開多必綠K、開空必紅K，嚴禁任何反向K棒或盤中逆向開倉
            pv_flip_verified_exec = False
            if (meta or {}).get("is_pv_flip") and isinstance(frame, pd.DataFrame):
                from core.services.low_latency_exit import verify_pv_flip_entry_authority
                pv_flip_verified_exec = verify_pv_flip_entry_authority(
                    frame, side=side, live_price=price, context=meta, symbol=symbol,
                )
            pv_entry_verified_exec = False
            if (
                (meta or {}).get("is_peak_valley_inflection") is True
                and isinstance(frame, pd.DataFrame)
            ):
                from core.services.low_latency_entry import evaluate_peak_valley_inflection_entry

                pv_entry = evaluate_peak_valley_inflection_entry(
                    frame,
                    live_price=price,
                    symbol=symbol,
                    require_directional_candle=True,
                )
                pv_entry_verified_exec = (
                    pv_entry.get("action") == f"OPEN_{side.upper()}"
                    and not pv_entry.get("is_consolidation")
                )
            if frame is not None and isinstance(frame, pd.DataFrame) and len(frame) >= 1:
                last_f_row = frame.iloc[-1]
                prev_f_row = frame.iloc[-2] if len(frame) >= 2 else last_f_row
                f_is_closed = bool(last_f_row.get("is_closed", True)) or bool((meta or {}).get("intrabar_authorized"))
                eval_f_row = last_f_row if f_is_closed else prev_f_row
                prev_eval_f_row = prev_f_row if f_is_closed else (frame.iloc[-3] if len(frame) >= 3 else prev_f_row)
                eval_f_c = float(eval_f_row.get("close", price))
                eval_f_o = float(eval_f_row.get("open", price))
            is_entry_ma5_ma15_entry = entry_method_authority == "MA5_MA15_CROSS"
            is_extreme_reversal_entry = (
                entry_method_authority in ("KC_OUTER_PIVOT_REVERSAL", "THREE_BAR_PIVOT_REVERSAL")
                or any(k in str(reason).upper() for k in ("PIVOT", "REVERSAL", "MODE_A", "VALLEY", "PEAK"))
                or (meta or {}).get("is_mode_a") is True
                or (meta or {}).get("is_flip") is True
                or pv_entry_verified_exec
                or pv_flip_verified_exec
            )
            # 建立多空方向與標頭/中軌一致性門禁
            from core.symbol_aliases import is_meme_breakout_symbol
            is_meme_symbol = is_meme_breakout_symbol(symbol)
            if (
                entry_method_authority != "MTF_KC_STATE_MACHINE"
                and (is_meme_symbol or True)
                and "kc_middle" in frame.columns
                and len(frame) >= 2
                and not pv_flip_verified_exec
            ):
                eval_kc_mid = float(eval_f_row.get("kc_middle", 0.0))
                prev_kc_mid = float(prev_eval_f_row.get("kc_middle", eval_kc_mid))
                is_kc_up = eval_kc_mid > prev_kc_mid
                is_kc_down = eval_kc_mid < prev_kc_mid

                # 當市場方向明確為 KC_UP（中軌向上 / 介面標頭「只評估多單」）：
                # 1. 不允許一般趨勢引擎開空。
                # 2. 只有獨立的極值峰頂反轉引擎，在具備完整反轉證據時，才可評估逆勢空單。
                if is_kc_up and side.upper() == "SHORT":
                    if not is_extreme_reversal_entry:
                        cls._last_open_error[symbol] = "BLOCKED_SHORT_ON_BULLISH_TREND"
                        logger.critical(
                            f"🛑 [DIRECTION_CONSISTENCY_VETO_SHORT] {symbol} 開空硬攔截：KC 中軌明確向上 (KC_UP, kc_mid={eval_kc_mid:.6f} > prev={prev_kc_mid:.6f})，"
                            f"界面方向為『KC 向上 (只評估多單)』，一般趨勢引擎絕對禁止開空！(只有獨立極值峰頂反轉具備完整證據時才允許)"
                        )
                        return False

                # 當市場方向明確為 KC_DOWN（中軌向下 / 介面標頭「只評估空單」）：
                # 1. 不允許一般趨勢引擎開多。
                # 2. 只有獨立的極值谷底反轉引擎，在具備完整反轉證據時，才可評估逆勢多單。
                if is_kc_down and side.upper() == "LONG":
                    if not is_extreme_reversal_entry:
                        cls._last_open_error[symbol] = "BLOCKED_LONG_ON_BEARISH_TREND"
                        logger.critical(
                            f"🛑 [DIRECTION_CONSISTENCY_VETO_LONG] {symbol} 開多硬攔截：KC 中軌明確向下 (KC_DOWN, kc_mid={eval_kc_mid:.6f} < prev={prev_kc_mid:.6f})，"
                            f"界面方向為『KC 向下 (只評估空單)』，一般趨勢引擎絕對禁止開多！(只有獨立極值谷底反轉具備完整證據時才允許)"
                        )
                        return False
                # ──【開倉三大必要條件與即時 K 線顏色嚴格審查（Pre-Order Guard）】──
                # 取得當前最新未收盤 K 棒 open 與最新即時價格
                current_open = float(last_f_row.get("open", eval_f_o))
                current_price = float(price)

                # 提取 MA5 與 MA15 數值（若最新未收盤或為 NaN，透過 quote_adjusted_entry_mas 動態推算）
                raw_last_ma5 = last_f_row.get("ma5")
                raw_prev_ma5 = prev_f_row.get("ma5")
                raw_last_ma15 = last_f_row.get("ma15")

                live_ma5 = float(raw_last_ma5) if raw_last_ma5 is not None and not pd.isna(raw_last_ma5) else (float(raw_prev_ma5) if raw_prev_ma5 is not None and not pd.isna(raw_prev_ma5) else 0.0)
                prev_ma5 = float(raw_prev_ma5) if raw_prev_ma5 is not None and not pd.isna(raw_prev_ma5) else live_ma5
                live_ma15 = float(raw_last_ma15) if raw_last_ma15 is not None and not pd.isna(raw_last_ma15) else (float(prev_f_row.get("ma15", 0.0)) if not pd.isna(prev_f_row.get("ma15")) else 0.0)

                if not bool(last_f_row.get("is_closed", True)) or pd.isna(raw_last_ma5) or pd.isna(raw_last_ma15):
                    from core.services.ma5_entry_slope import quote_adjusted_entry_mas

                    live_mas = quote_adjusted_entry_mas(frame, current_price)
                    if live_mas is None or not all(
                        math.isfinite(float(value)) and float(value) > 0
                        for value in live_mas
                    ):
                        cls._last_open_error[symbol] = "BLOCKED_INVALID_LIVE_MA_DATA"
                        logger.critical(
                            "BLOCKED_INVALID_LIVE_MA_DATA symbol=%s bar=%s",
                            symbol,
                            last_f_row.get("timestamp"),
                        )
                        return False
                    live_ma5, prev_ma5, live_ma15 = live_mas

                if side.upper() == "LONG":
                    # 開多單三大必要條件：
                    # 條件 A: MA5 > MA15
                    # 條件 B: MA5 向上 (live_ma5 > prev_ma5)
                    # 條件 C: 嚴格綠 K (current_price > current_open)
                    if not (live_ma5 > live_ma15):
                        logger.critical(
                            f"🛑 [防呆攔截] {symbol} 準備開多但 MA5 未大於 MA15 (MA5={live_ma5:.6f} <= MA15={live_ma15:.6f})，取消下單"
                        )
                        return False
                    if not (live_ma5 > prev_ma5):
                        logger.critical(
                            f"🛑 [防呆攔截] {symbol} 準備開多但 MA5 未向上推進 (MA5={live_ma5:.6f} <= 前一根={prev_ma5:.6f})，取消下單"
                        )
                        return False
                    if current_price <= current_open:
                        logger.critical(
                            f"🛑 [防呆攔截] 準備開多但當前非綠K (現價={current_price:.6f} <= 開盤價={current_open:.6f})，取消下單"
                        )
                        return False
                    if price < eval_f_c and (eval_f_c - price) > 0.3 * atr:
                        logger.critical(
                            f"🛑 [EXECUTION_FAILSAFE_VETO_LONG_DUMP] {symbol} 開多硬攔截：即時報價正大幅向下急殺 "
                            f"(price={price:.6f} < close={eval_f_c:.6f})，嚴防逆勢接飛刀！"
                        )
                        return False

                elif side.upper() == "SHORT":
                    # 開空單三大必要條件：
                    # 條件 A: MA5 < MA15
                    # 條件 B: MA5 向下 (live_ma5 < prev_ma5)
                    # 條件 C: 嚴格紅 K (current_price < current_open)
                    if not (live_ma5 < live_ma15):
                        logger.critical(
                            f"🛑 [防呆攔截] {symbol} 準備開空但 MA5 未小於 MA15 (MA5={live_ma5:.6f} >= MA15={live_ma15:.6f})，取消下單"
                        )
                        return False
                    if not (live_ma5 < prev_ma5):
                        logger.critical(
                            f"🛑 [防呆攔截] {symbol} 準備開空但 MA5 未向下拐頭 (MA5={live_ma5:.6f} >= 前一根={prev_ma5:.6f})，取消下單"
                        )
                        return False
                    if current_price >= current_open:
                        logger.critical(
                            f"🛑 [防呆攔截] 準備開空但當前非紅K (現價={current_price:.6f} >= 開盤價={current_open:.6f})，取消下單"
                        )
                        return False
                    if price > eval_f_c and (price - eval_f_c) > 0.3 * atr:
                        logger.critical(
                            f"🛑 [EXECUTION_FAILSAFE_VETO_SHORT_BOUNCE] {symbol} 開空硬攔截：即時報價正大幅向上反彈 "
                            f"(price={price:.6f} > close={eval_f_c:.6f})，嚴防逆勢追空！"
                        )
                        return False

            if hasattr(account, "open_position"):
                open_reject_reasons = getattr(account, "open_reject_reasons", None)
                if isinstance(open_reject_reasons, dict):
                    open_reject_reasons.pop(symbol, None)
                getattr(cls, "_last_open_error", {}).pop(symbol, None)
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
                            pos.pop("meme_wick_stop_loss", None)
                    open_label = "OPEN_SHORT" if str(side).upper() == "SHORT" else "OPEN_LONG"
                    logger.info(
                        "[UI_DISPLAY: %s] action=%s symbol=%s price=%s reason=%s",
                        "賣空" if open_label == "OPEN_SHORT" else "買多",
                        open_label, symbol, price, reason,
                    )
                    logger.info(f"🎉 [UNIFIED_ORDER_FILLED] {symbol} {side} @ {price} | Margin={amount_usdt:.2f}U Lev={leverage}x")
                    print(f"🎉 [UNIFIED_ORDER_FILLED] {symbol} {side} @ {price} | Margin={amount_usdt:.2f}U Lev={leverage}x", flush=True)
                else:
                    account_open_rejects = getattr(account, "open_reject_reasons", None)
                    account_open_error = (
                        account_open_rejects.get(symbol)
                        if isinstance(account_open_rejects, dict)
                        else None
                    )
                    last_err = (
                        account_open_error
                        or getattr(cls, "_last_open_error", {}).get(symbol)
                        or getattr(account, "last_error", None)
                    )
                    if not last_err:
                        last_err = "ACCOUNT_REJECTED_WITHOUT_REASON"
                        logger.error(
                            "ACCOUNT_OPEN_REJECTED_WITHOUT_REASON symbol=%s side=%s",
                            symbol, side,
                        )
                    print(f"⚠️ [EXECUTION_FAILED] {symbol} {side} account.open_position returned False! reason={reason} detail={last_err}", flush=True)
                return opened
            print(f"⚠️ [EXECUTION_FAILED] {symbol} account object does not have open_position method!", flush=True)
            return False
        except Exception as exc:
            cls._last_open_error[symbol] = str(exc)
            print(f"❌ [EXECUTION_EXCEPTION] {symbol} open_position 異常: {exc}", flush=True)
            logger.error(f"[EXECUTION] open_position 異常 {symbol}: {exc}", exc_info=True)
            return False


__all__ = [
    "ExecutionManager",
    "check_macro_trend_end",
    "find_htf_key_pivot",
    "calculate_htf_ma15",
    "check_pivot_status",
    "evaluate_meme_trade_valid",
    "is_meme_trade_valid",
]
