"""Unified Entry Strategy evaluating streamlined entry methods.
Implements IEntryStrategy interface.
"""
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str]:
    """
    解鎖版全動態進場系統：
    - 移除初始破軌空間門檻 (由階梯鎖利接管利潤管理)
    - 引入中軌斜率 (Slope) 作為通道擴張替代條件
    - 結構反轉與特例 K 絕對豁免空間檢查
    """
    if df is None or len(df) < 5:
        return False, "WAIT_INSUFFICIENT_DATA"

    latest = df.iloc[-1]       # 當前即時 K 棒
    prev_1 = df.iloc[-2]       # 剛收盤確認信號的 K 棒
    prev_2 = df.iloc[-3]       # 前兩根 K 棒
    
    current_atr = float(prev_1.get('atr', 0))
    if current_atr <= 0:
        return False, "WAIT_INVALID_ATR"

    # 計算前一根收盤 K 棒實體與總長
    prev_open = float(prev_1['open'])
    prev_close = float(prev_1['close'])
    prev_high = float(prev_1['high'])
    prev_low = float(prev_1['low'])

    prev_body = abs(prev_close - prev_open)
    prev_range = prev_high - prev_low
    prev_body_ratio = prev_body / prev_range if prev_range > 0 else 0.0

    # -------------------------------------------------------------
    # 優先級 1：【特例 K】極端爆發模式 (絕對優先，無視一切過濾)
    # -------------------------------------------------------------
    if prev_body >= 2.0 * current_atr:
        if side == "LONG" and prev_close > prev_open:
            return True, "SPECIAL_ENTRY_MOMENTUM_LONG"
        elif side == "SHORT" and prev_close < prev_open:
            return True, "SPECIAL_ENTRY_MOMENTUM_SHORT"

    # 基礎品質過濾：進場前一根實體比例必須 >= 60%
    if prev_body_ratio < 0.60:
        return False, f"WAIT_FILTERED_BODY_RATIO_{prev_body_ratio:.2f}"

    # 讀取 MA3 / MA15
    ma3_prev1 = float(prev_1.get('ma3', 0))
    ma15_prev1 = float(prev_1.get('ma15', 0))
    ma3_prev2 = float(prev_2.get('ma3', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    has_ma = (ma3_prev1 > 0 and ma15_prev1 > 0 and ma3_prev2 > 0 and ma15_prev2 > 0)
    
    # 讀取 KC 數據
    kc_mid_prev1 = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0)))
    kc_mid_prev2 = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0)))
    
    kc_upper_prev1 = float(prev_1.get("kc_upper", kc_mid_prev1))
    kc_lower_prev1 = float(prev_1.get("kc_lower", kc_mid_prev1))
    kc_upper_prev2 = float(prev_2.get("kc_upper", kc_mid_prev2))
    kc_lower_prev2 = float(prev_2.get("kc_lower", kc_mid_prev2))

    # -------------------------------------------------------------
    # 2. 中軌斜率與通道動能判定
    # -------------------------------------------------------------
    middle_slope = kc_mid_prev1 - kc_mid_prev2
    current_width = kc_upper_prev1 - kc_lower_prev1
    prev_width = kc_upper_prev2 - kc_lower_prev2
    is_expanding = current_width > prev_width
    
    # 斜率達標門檻 (單根斜率變動達 0.08 ATR 視為顯著傾斜)
    slope_threshold = 0.08 * current_atr
    is_steep_slope_up = middle_slope >= slope_threshold
    is_steep_slope_down = middle_slope <= -slope_threshold

    # 通道啟動判定：物理寬度變大 OR 中軌劇烈傾斜
    trend_started_long = is_expanding or is_steep_slope_up
    trend_started_short = is_expanding or is_steep_slope_down

    dist_from_middle_atr = abs(prev_close - kc_mid_prev1) / current_atr
    cooldown_active = kwargs.get("cooldown_active", False)
    prev2_close = float(prev_2['close'])

    # -------------------------------------------------------------
    # 優先級 2：【結構反轉模式】 (極端區域 + MA 死叉/金叉，絕對豁免空間)
    # -------------------------------------------------------------
    if has_ma:
        # 做空反轉：處於頂部極端區 (>= 1.5 ATR) + MA3 死叉 MA15 + 實體陰線
        ma_cross_down = (ma3_prev2 >= ma15_prev2) and (ma3_prev1 < ma15_prev1)
        if side == "SHORT" and dist_from_middle_atr >= 1.5 and ma_cross_down and (prev_close < prev_open):
            return True, "REVERSAL_ENTRY_SHORT"

        # 做多反轉：處於底部極端區 (>= 1.5 ATR) + MA3 金叉 MA15 + 實體陽線
        ma_cross_up = (ma3_prev2 <= ma15_prev2) and (ma3_prev1 > ma15_prev1)
        if side == "LONG" and dist_from_middle_atr >= 1.5 and ma_cross_up and (prev_close > prev_open):
            return True, "REVERSAL_ENTRY_LONG"

    # -------------------------------------------------------------
    # 優先級 3 & 4：【初始破軌模式】與【趨勢延續模式】
    # -------------------------------------------------------------
    if side == "LONG":
        # 初始破軌：收盤上穿中軌 + 趨勢啟動 (寬度擴張或中軌上翹)
        initial_break_long = (prev_close > kc_mid_prev1) and (prev2_close <= kc_mid_prev2)
        if initial_break_long and trend_started_long and not cooldown_active:
            return True, "INITIAL_BREAKOUT_UNLOCKED_LONG"

        # 趨勢延續
        continuation_long = (prev_close > kc_mid_prev1) and (middle_slope > 0)
        if continuation_long and trend_started_long:
            target_long = kc_upper_prev1 + (middle_slope * 2)
            eval_price = live_price if live_price > 0 else prev_close
            space_long_atr = (target_long - eval_price) / current_atr
            if space_long_atr >= 0.5:
                if is_steep_slope_up:
                    return True, "TREND_CONT_UNLOCKED_EXEMPT_LONG"
                elif not cooldown_active:
                    return True, "TREND_CONT_UNLOCKED_LONG"

    elif side == "SHORT":
        # 初始破軌：收盤下穿中軌 + 趨勢啟動 (寬度擴張或中軌下俯)
        initial_break_short = (prev_close < kc_mid_prev1) and (prev2_close >= kc_mid_prev2)
        if initial_break_short and trend_started_short and not cooldown_active:
            return True, "INITIAL_BREAKOUT_UNLOCKED_SHORT"

        # 趨勢延續
        continuation_short = (prev_close < kc_mid_prev1) and (middle_slope < 0)
        if continuation_short and trend_started_short:
            target_short = kc_lower_prev1 + (middle_slope * 2)
            eval_price = live_price if live_price > 0 else prev_close
            space_short_atr = (eval_price - target_short) / current_atr
            if space_short_atr >= 0.5:
                if is_steep_slope_down:
                    return True, "TREND_CONT_UNLOCKED_EXEMPT_SHORT"
                elif not cooldown_active:
                    return True, "TREND_CONT_UNLOCKED_SHORT"

    return False, "WAIT_NO_VALID_ENTRY_CONDITIONS"


class UnifiedEntryStrategy(IEntryStrategy):
    def evaluate_entry(self, frame, price, side, **kwargs):
        if frame is None or len(frame) < 10 or ("kc_middle" not in frame.columns and "ema_20" not in frame.columns):
            return False, "WAIT_INSUFFICIENT_DATA_OR_INDICATORS", {"action": "WAIT"}

        try:
            ok, reason = check_streamlined_entry_signal(frame, side, price, **kwargs)
        except Exception as e:
            return False, f"WAIT_ERROR_{e}", {"action": "WAIT"}

        if ok:
            return True, reason, {"action": "ENTER", "side": side, "reason": reason}
        return False, reason, {"action": "WAIT"}
