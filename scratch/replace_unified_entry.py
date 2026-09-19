import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

new_content = """\"\"\"Unified Entry Strategy evaluating streamlined entry methods.
Implements IEntryStrategy interface.
\"\"\"
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def calculate_projected_space(df, side: str, forecast_steps=3) -> float:
    \"\"\"
    計算預期動態獲利空間 (以 ATR 單位計算)
    公式: 目標投射空間 = 當前剩餘靜態距離 + (中軌動態斜率 * 預測步數) + (帶寬擴張速度 * 預測步數)
    \"\"\"
    if df is None or len(df) < 3:
        return 0.0
        
    latest = df.iloc[-1]
    prev_1 = df.iloc[-2]
    
    current_price = float(latest['close'])
    current_atr = float(latest.get('atr', 0.0))
    if current_atr <= 0:
        return 0.0

    # 1. 中軌斜率 (每根 K 棒的均線變化量)
    kc_mid_latest = float(latest.get('kc_middle', latest.get('ema_20', 0)))
    kc_mid_prev = float(prev_1.get('kc_middle', prev_1.get('ema_20', 0)))
    middle_slope = kc_mid_latest - kc_mid_prev
    
    # 2. 帶寬變化速度 (Bandwidth Expansion Speed)
    kc_upper_latest = float(latest.get('kc_upper', kc_mid_latest))
    kc_lower_latest = float(latest.get('kc_lower', kc_mid_latest))
    current_width = kc_upper_latest - kc_lower_latest
    
    kc_upper_prev = float(prev_1.get('kc_upper', kc_mid_prev))
    kc_lower_prev = float(prev_1.get('kc_lower', kc_mid_prev))
    prev_width = kc_upper_prev - kc_lower_prev
    
    bandwidth_expansion_rate = max(0.0, current_width - prev_width)

    # 3. 預測延伸空間 (ATR 單位)
    # 動態增量 = 斜率推動 + 擴張推動
    if side == "LONG":
        dynamic_delta = (middle_slope * forecast_steps) + (bandwidth_expansion_rate * 0.5 * forecast_steps)
        projected_target = kc_upper_latest + dynamic_delta
        projected_distance = projected_target - current_price
    else: # SHORT
        # 目標下軌 = kc_lower_latest + (middle_slope * steps) - (bandwidth_expansion * 0.5 * steps)
        dynamic_delta = (middle_slope * forecast_steps) - (bandwidth_expansion_rate * 0.5 * forecast_steps)
        projected_target = kc_lower_latest + dynamic_delta
        # 做空的空間 = 當前價格 - 目標價格
        projected_distance = current_price - projected_target
        
    return projected_distance / current_atr

def check_streamlined_entry_signal(df, side: str, live_price: float, **kwargs) -> tuple[bool, str]:
    if df is None or len(df) < 5:
        return False, "WAIT_INSUFFICIENT_DATA"
        
    # 我們以剛收盤的 K 棒 (prev) 判定實體，最新未收盤 K 棒 (latest) 算距離
    curr = df.iloc[-2] # 剛收線的 K 棒
    prev = df.iloc[-3]
    prev2 = df.iloc[-4]
    
    body_length = abs(float(curr['close']) - float(curr['open']))
    candle_range = float(curr['high']) - float(curr['low'])
    body_ratio = body_length / candle_range if candle_range > 0 else 0.0
    
    current_atr = float(curr.get('atr', 0))
    if current_atr <= 0:
        return False, "WAIT_INVALID_ATR"
        
    projected_space_atr = calculate_projected_space(df, side, forecast_steps=3)
    
    kc_mid_curr = float(curr.get("kc_middle", curr.get("ema_20", 0)))
    kc_mid_prev = float(prev.get("kc_middle", prev.get("ema_20", 0)))
    kc_mid_prev2 = float(prev2.get("kc_middle", prev2.get("ema_20", 0)))
    
    # 判定中軌斜率是否連續翻正 / 翻負
    slope_1 = kc_mid_curr - kc_mid_prev
    slope_2 = kc_mid_prev - kc_mid_prev2
    
    if side == "LONG":
        is_slope_favorable = (slope_1 > 0) and (slope_2 > 0)
    else:
        is_slope_favorable = (slope_1 < 0) and (slope_2 < 0)
        
    curr_kc_upper = float(curr.get("kc_upper", kc_mid_curr))
    curr_kc_lower = float(curr.get("kc_lower", kc_mid_curr))
    prev_kc_upper = float(prev.get("kc_upper", kc_mid_prev))
    prev_kc_lower = float(prev.get("kc_lower", kc_mid_prev))
    
    curr_width = curr_kc_upper - curr_kc_lower
    prev_width = prev_kc_upper - prev_kc_lower
    is_expanding = curr_width > prev_width
    
    trend_started = is_expanding or is_slope_favorable
    
    cooldown_active = kwargs.get("cooldown_active", False)

    curr_close = float(curr['close'])
    prev_close = float(prev['close'])
    
    if side == "LONG":
        is_directional = curr_close > float(curr['open'])
    else:
        is_directional = curr_close < float(curr['open'])

    # -------------------------------------------------------------
    # 1. 最高優先級：【極端爆發模式】 (特例 K)
    # -------------------------------------------------------------
    if body_length >= 2.0 * current_atr and is_directional:
        if side == "LONG" and curr_close > kc_mid_curr:
            return True, "SPECIAL_ENTRY_MOMENTUM_LONG"
        elif side == "SHORT" and curr_close < kc_mid_curr:
            return True, "SPECIAL_ENTRY_MOMENTUM_SHORT"

    # 必須具備實體動能驗證 (實體比 >= 60%)
    if body_ratio < 0.60:
        return False, f"WAIT_FILTERED_BODY_RATIO_{body_ratio:.2f}"

    # -------------------------------------------------------------
    # 2. 次高優先級：【趨勢延續模式】
    # -------------------------------------------------------------
    if side == "LONG":
        consecutive_outside_mid = (curr_close > kc_mid_curr) and (prev_close > kc_mid_prev)
    else:
        consecutive_outside_mid = (curr_close < kc_mid_curr) and (prev_close < kc_mid_prev)
        
    if consecutive_outside_mid and trend_started:
        if projected_space_atr >= 0.6:
            # 若中軌斜率連正且通道顯著擴張，可豁免冷卻期
            if is_slope_favorable and is_expanding:
                return True, f"TREND_CONTINUATION_EXEMPT_{side}"
            elif not cooldown_active:
                return True, f"TREND_CONTINUATION_{side}"
            else:
                return False, "WAIT_COOLDOWN_ACTIVE"

    # -------------------------------------------------------------
    # 3. 標準優先級：【初始破軌模式】
    # -------------------------------------------------------------
    if side == "LONG":
        initial_cross_mid = (curr_close > kc_mid_curr) and (prev_close <= kc_mid_prev)
    else:
        initial_cross_mid = (curr_close < kc_mid_curr) and (prev_close >= kc_mid_prev)
        
    if initial_cross_mid and trend_started:
        if projected_space_atr >= 0.8:
            if not cooldown_active:
                return True, f"INITIAL_BREAKOUT_{side}"
            else:
                return False, "WAIT_COOLDOWN_ACTIVE"

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

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(new_content)
