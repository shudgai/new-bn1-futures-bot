import os

filepath = "core/services/strategies/unified_entry_strategy.py"

new_content = """\"\"\"Unified Entry Strategy evaluating streamlined entry methods.
Implements IEntryStrategy interface.
\"\"\"
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str]:
    \"\"\"
    加速版進場檢驗：動態啟動與單根破軌直進 (由 check_entry_signals_fast 改編)
    \"\"\"
    if df is None or len(df) < 5:
        return False, "WAIT_INSUFFICIENT_DATA"
        
    # 我們以剛收盤的 K 棒 (curr) 判定實體，前一根 (prev) 判定破軌
    curr = df.iloc[-2]
    prev = df.iloc[-3]
    
    current_atr = float(curr.get('atr', 0))
    if current_atr <= 0:
        return False, "WAIT_INVALID_ATR"

    # 計算實體比例
    curr_close = float(curr['close'])
    curr_open = float(curr['open'])
    curr_high = float(curr['high'])
    curr_low = float(curr['low'])
    
    body_length = abs(curr_close - curr_open)
    candle_range = curr_high - curr_low
    body_ratio = body_length / candle_range if candle_range > 0 else 0.0

    # 1. 優先級一：【特例 K】極端爆發模式 (絕對優先，無視一切限制)
    if body_length >= 2.0 * current_atr:
        if side == "LONG" and curr_close > curr_open:
            return True, "SPECIAL_ENTRY_MOMENTUM_LONG"
        elif side == "SHORT" and curr_close < curr_open:
            return True, "SPECIAL_ENTRY_MOMENTUM_SHORT"

    # 基礎濾網：進場 K 棒實體比例必須 >= 60% (防長影線、防假突破插針)
    if body_ratio < 0.60:
        return False, f"WAIT_FILTERED_BODY_RATIO_{body_ratio:.2f}"

    # 取得 KC 數據
    kc_mid_curr = float(curr.get("kc_middle", curr.get("ema_20", 0)))
    kc_mid_prev = float(prev.get("kc_middle", prev.get("ema_20", 0)))
    
    kc_upper_curr = float(curr.get("kc_upper", kc_mid_curr))
    kc_lower_curr = float(curr.get("kc_lower", kc_mid_curr))
    kc_upper_prev = float(prev.get("kc_upper", kc_mid_prev))
    kc_lower_prev = float(prev.get("kc_lower", kc_mid_prev))

    # 通道擴張與斜率計算
    current_width = kc_upper_curr - kc_lower_curr
    prev_width = kc_upper_prev - kc_lower_prev
    width_diff = current_width - prev_width
    
    # 通道正在擴張 (Width 變大)
    is_expanding = width_diff > 0
    # 顯著擴張判定 (當前擴張速度 >= 0.15 ATR，視為強動能啟動)
    is_fast_expanding = width_diff >= (0.15 * current_atr)

    # 預期獲利空間計算 (動態空間投射: 剩餘軌道 + 斜率動能)
    middle_slope = kc_mid_curr - kc_mid_prev
    
    cooldown_active = kwargs.get("cooldown_active", False)
    prev_close = float(prev['close'])

    if side == "LONG":
        long_target = kc_upper_curr + (middle_slope * 2)
        # 用即時報價 (live_price) 計算剩餘空間，若無則用收盤價
        eval_price = live_price if live_price > 0 else curr_close
        long_space_atr = (long_target - eval_price) / current_atr

        # 【初始破軌模式 - 多頭】：第一根實體收盤站上中軌 + 通道正在擴張
        initial_breakout_long = (curr_close > kc_mid_curr) and (prev_close <= kc_mid_prev)
        
        if initial_breakout_long and is_expanding and not cooldown_active:
            # 空間門檻：顯著擴張自動放寬至 0.6 ATR，常規擴張要求 0.8 ATR
            required_space = 0.6 if is_fast_expanding else 0.8
            if long_space_atr >= required_space:
                return True, "INITIAL_BREAKOUT_LONG"

        # 【趨勢延續模式 - 多頭】：中軌之外且通道持續擴張
        continuation_long = (curr_close > kc_mid_curr) and (middle_slope > 0)
        if continuation_long and is_expanding:
            if long_space_atr >= 0.6:
                # 若通道顯著擴張且斜率正向，豁免冷卻期
                if is_fast_expanding:
                    return True, "TREND_CONTINUATION_EXEMPT_LONG"
                elif not cooldown_active:
                    return True, "TREND_CONTINUATION_LONG"
                    
    elif side == "SHORT":
        short_target = kc_lower_curr + (middle_slope * 2)
        eval_price = live_price if live_price > 0 else curr_close
        short_space_atr = (eval_price - short_target) / current_atr

        # 【初始破軌模式 - 空頭】：第一根實體收盤跌破中軌 + 通道正在擴張
        initial_breakout_short = (curr_close < kc_mid_curr) and (prev_close >= kc_mid_prev)
        
        if initial_breakout_short and is_expanding and not cooldown_active:
            required_space = 0.6 if is_fast_expanding else 0.8
            if short_space_atr >= required_space:
                return True, "INITIAL_BREAKOUT_SHORT"

        # 【趨勢延續模式 - 空頭】
        continuation_short = (curr_close < kc_mid_curr) and (middle_slope < 0)
        if continuation_short and is_expanding:
            if short_space_atr >= 0.6:
                if is_fast_expanding:
                    return True, "TREND_CONTINUATION_EXEMPT_SHORT"
                elif not cooldown_active:
                    return True, "TREND_CONTINUATION_SHORT"

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
"""

with open(filepath, "w") as f:
    f.write(new_content)
