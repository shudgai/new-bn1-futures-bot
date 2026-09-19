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

    curr = df.iloc[-1]       # 當前即時 K 棒
    prev_1 = df.iloc[-2]     # 剛收盤確認信號的 K 棒
    prev_2 = df.iloc[-3]     # 前兩根 K 棒

    atr = float(prev_1.get('atr', 0))
    if atr <= 0:
        return False, "WAIT_INVALID_ATR"

    # 計算前一根收盤 K 棒實體與總長
    prev_open = float(prev_1['open'])
    prev_close = float(prev_1['close'])
    prev_high = float(prev_1['high'])
    prev_low = float(prev_1['low'])

    body = abs(prev_close - prev_open)
    candle_range = prev_high - prev_low
    body_ratio = body / candle_range if candle_range > 0 else 0.0

    # -------------------------------------------------------------
    # 優先級 1：【特例 K】極端爆發模式 (絕對優先，無視一切過濾)
    # -------------------------------------------------------------
    if body >= 2.0 * atr:
        if side == "LONG" and prev_close > prev_open:
            return True, "SPECIAL_ENTRY_MOMENTUM_LONG"
        elif side == "SHORT" and prev_close < prev_open:
            return True, "SPECIAL_ENTRY_MOMENTUM_SHORT"

    # 基礎品質過濾：防長影線、防假突破插針 (實體比例必須 >= 60%)
    if body_ratio < 0.60:
        return False, f"WAIT_FILTERED_BODY_RATIO_{body_ratio:.2f}"

    # 通道動能與斜率計算
    kc_mid_prev1 = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0)))
    kc_mid_prev2 = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0)))
    kc_mid_prev3 = float(df.iloc[-4].get("kc_middle", df.iloc[-4].get("ema_20", 0)))
    
    kc_upper_prev1 = float(prev_1.get("kc_upper", kc_mid_prev1))
    kc_lower_prev1 = float(prev_1.get("kc_lower", kc_mid_prev1))
    kc_upper_prev2 = float(prev_2.get("kc_upper", kc_mid_prev2))
    kc_lower_prev2 = float(prev_2.get("kc_lower", kc_mid_prev2))

    curr_width = kc_upper_prev1 - kc_lower_prev1
    prev_width = kc_upper_prev2 - kc_lower_prev2
    is_expanding = curr_width > prev_width
    is_fast_expanding = (curr_width - prev_width) >= (0.15 * atr)

    slope = kc_mid_prev1 - kc_mid_prev2
    slope_prev = kc_mid_prev2 - kc_mid_prev3

    cooldown_active = kwargs.get("cooldown_active", False)
    forecast_steps = 2

    # -------------------------------------------------------------
    # 優先級 2：【結構反轉模式】 (極端區域 + MA 死叉/金叉，絕對豁免空間)
    # -------------------------------------------------------------
    ma3_prev1 = float(prev_1.get('ma3', 0))
    ma15_prev1 = float(prev_1.get('ma15', 0))
    ma3_prev2 = float(prev_2.get('ma3', 0))
    ma15_prev2 = float(prev_2.get('ma15', 0))
    has_ma = (ma3_prev1 > 0 and ma15_prev1 > 0 and ma3_prev2 > 0 and ma15_prev2 > 0)
    
    dist_from_middle_atr = abs(prev_close - kc_mid_prev1) / atr

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
    prev2_close = float(prev_2['close'])

    if side == "LONG":
        # 多頭延續
        if prev_close > kc_mid_prev1 and prev2_close > kc_mid_prev2:
            if is_expanding:
                consecutive_slope = (slope > 0) and (slope_prev > 0)
                req_space = 0.6 if consecutive_slope else 0.8
                projected_target = kc_upper_prev1 + (slope * forecast_steps)
                space_atr = (projected_target - prev_close) / atr

                if space_atr >= req_space:
                    if is_fast_expanding and consecutive_slope:
                        return True, "TREND_CONT_UNLOCKED_EXEMPT_LONG"
                    elif not cooldown_active:
                        return True, "TREND_CONT_UNLOCKED_LONG"

        # 多頭初始破軌
        if (prev_close > kc_mid_prev1) and (prev2_close <= kc_mid_prev2):
            if (is_expanding or slope > 0) and not cooldown_active:
                return True, "INITIAL_BREAKOUT_UNLOCKED_LONG"

    elif side == "SHORT":
        # 空頭延續
        if prev_close < kc_mid_prev1 and prev2_close < kc_mid_prev2:
            if is_expanding:
                consecutive_slope = (slope < 0) and (slope_prev < 0)
                req_space = 0.6 if consecutive_slope else 0.8
                projected_target = kc_lower_prev1 + (slope * forecast_steps)
                space_atr = (prev_close - projected_target) / atr

                if space_atr >= req_space:
                    if is_fast_expanding and consecutive_slope:
                        return True, "TREND_CONT_UNLOCKED_EXEMPT_SHORT"
                    elif not cooldown_active:
                        return True, "TREND_CONT_UNLOCKED_SHORT"

        # 空頭初始破軌
        if (prev_close < kc_mid_prev1) and (prev2_close >= kc_mid_prev2):
            if (is_expanding or slope < 0) and not cooldown_active:
                return True, "INITIAL_BREAKOUT_UNLOCKED_SHORT"


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
