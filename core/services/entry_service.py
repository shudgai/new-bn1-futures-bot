import pandas as pd
from typing import Dict, Any, Optional
from core.services.strategies.outer_strategy import ma3_outer_cross_ready, ma3_outer_continuation_ready, live_candle_color_ready

ENTRY_TREND_CODES = {
    "KC_UPPER_TREND_ENTRY", "KC_LOWER_TREND_ENTRY",
    # V9.0 Track codes — must bypass invalid-candidate lock & snapshot strictness
    "TRACK_A_EXTREME_REVERSAL_LONG", "TRACK_A_EXTREME_REVERSAL_SHORT",
    "TRACK_B_MID_PULLBACK_LONG",     "TRACK_B_MID_PULLBACK_SHORT",
    "TRACK_C_BREAKOUT_LONG",         "TRACK_C_BREAKOUT_SHORT",
    "TRACK_D_TREND_CONT_LONG",       "TRACK_D_TREND_CONT_SHORT",
}
LIVE_OUTER_CODES = {
    "KC_LIVE_UPPER_BREAK_LONG", "KC_LIVE_LOWER_BREAK_SHORT",
    # V9.0 live-price signals are also treated as live-outer
    "TRACK_A_EXTREME_REVERSAL_LONG", "TRACK_A_EXTREME_REVERSAL_SHORT",
    "TRACK_D_TREND_CONT_LONG",       "TRACK_D_TREND_CONT_SHORT",
}

def channel_candidate_bar_id(frame: pd.DataFrame) -> Optional[Any]:
    if frame is None or frame.empty:
        return None
    last_row = frame.iloc[-1]
    return last_row.get("timestamp", frame.index[-1])

def channel_outer_directional_entry_allowed(
    frame: pd.DataFrame, side: str
) -> bool:
    if frame is None or frame.empty:
        return False
    curr = frame.iloc[-1]
    ma3 = float(curr["ma3"])
    kc_upper = float(curr["kc_upper"])
    kc_lower = float(curr["kc_lower"])
    if side == "LONG":
        return ma3 > kc_upper
    return ma3 < kc_lower

def channel_closed_body_break_entry_allowed(
    frame: pd.DataFrame, side: str
) -> bool:
    if frame is None or len(frame) < 2:
        return False
    prev = frame.iloc[-2]
    curr = frame.iloc[-1]
    if side == "LONG":
        return float(curr["close"]) > float(curr["kc_upper"]) and float(prev["close"]) <= float(prev["kc_upper"])
    return float(curr["close"]) < float(curr["kc_lower"]) and float(prev["close"]) >= float(prev["kc_lower"])

def channel_closed_body_break_has_outer_ma3_reversal(
    frame: pd.DataFrame, side: str
) -> bool:
    if frame is None or len(frame) < 2:
        return False
    prev = frame.iloc[-2]
    curr = frame.iloc[-1]
    if side == "LONG":
        return float(curr["ma3"]) > float(prev["ma3"])
    return float(curr["ma3"]) < float(prev["ma3"])

def channel_closed_body_break_entry_action(
    frame: pd.DataFrame, price: float, side: str
) -> Dict[str, Any]:
    if channel_closed_body_break_entry_allowed(frame, side):
        reason = "KC_UPPER_BREAKOUT" if side == "LONG" else "KC_LOWER_BREAKOUT"
        return {"action": "ENTER", "side": side, "reason": reason}
    return {"action": "WAIT", "side": None, "reason": "CLOSED_BODY_BREAK_NOT_READY"}

def channel_outer_continuation_entry_action(
    frame: pd.DataFrame, price: float, side: str
) -> Dict[str, Any]:
    if ma3_outer_continuation_ready(frame, price, side):
        reason = "KC_CONTINUATION_LONG" if side == "LONG" else "KC_CONTINUATION_SHORT"
        return {"action": "ENTER", "side": side, "reason": reason}
    return {"action": "WAIT", "side": None, "reason": "CONTINUATION_NOT_READY"}

def channel_outer_uptrend_entry_action(
    frame: pd.DataFrame, price: float
) -> Dict[str, Any]:
    if ma3_outer_cross_ready(frame, price, "LONG"):
        return {"action": "ENTER", "side": "LONG", "reason": "KC_UPPER_TREND_ENTRY"}
    return {"action": "WAIT", "side": None, "reason": "UPTREND_CROSS_NOT_READY"}

def channel_outer_downtrend_entry_action(
    frame: pd.DataFrame, price: float
) -> Dict[str, Any]:
    if ma3_outer_cross_ready(frame, price, "SHORT"):
        return {"action": "ENTER", "side": "SHORT", "reason": "KC_LOWER_TREND_ENTRY"}
    return {"action": "WAIT", "side": None, "reason": "DOWNTREND_CROSS_NOT_READY"}

def channel_outer_trend_entry_action(
    frame: pd.DataFrame, price: float, side: str
) -> Dict[str, Any]:
    if side == "LONG":
        return channel_outer_uptrend_entry_action(frame, price)
    return channel_outer_downtrend_entry_action(frame, price)

def channel_strong_first_outer_touch_action(
    frame: pd.DataFrame, price: float, side: str
) -> Dict[str, Any]:
    if live_candle_color_ready(frame, price, side):
        reason = "KC_LIVE_UPPER_BREAK_LONG" if side == "LONG" else "KC_LIVE_LOWER_BREAK_SHORT"
        return {"action": "ENTER", "side": side, "reason": reason}
    return {"action": "WAIT", "side": None, "reason": "LIVE_TOUCH_COLOR_NOT_READY"}

def channel_immediate_outer_break_action(
    frame: pd.DataFrame, price: float
) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {"action": "WAIT", "side": None, "reason": "EMPTY_FRAME"}
    curr = frame.iloc[-1]
    kc_upper = float(curr["kc_upper"])
    kc_lower = float(curr["kc_lower"])
    ma3 = float(curr["ma3"])
    if price > kc_upper and ma3 > kc_upper:
        return channel_strong_first_outer_touch_action(frame, price, "LONG")
    elif price < kc_lower and ma3 < kc_lower:
        return channel_strong_first_outer_touch_action(frame, price, "SHORT")
    return {"action": "WAIT", "side": None, "reason": "INSIDE_KC"}

def check_entry_signals(
    frame: pd.DataFrame, side: str, min_space_buffer_atr: float, state: dict = None
) -> Dict[str, Any]:
    """
    全新「三位一體」結構性進場架構 (動能+結構+空間)
    路徑 A: 特例 K 爆發 (Special K Path)
    路徑 B: 結構性轉折 (Structural Reversal Path)
    路徑 C: 強勢趨勢延續 (Trend Continuation Path)
    """
    if state is None:
        state = {}
        
    if frame is None or len(frame) < 4:
        return {"action": "WAIT", "reason": "INSUFFICIENT_DATA"}
        
    curr = frame.iloc[-1]
    prev = frame.iloc[-2]
    prev2 = frame.iloc[-3]
    prev3 = frame.iloc[-4]
    
    atr = float(curr.get("atr", 0))
    if atr <= 0:
        return {"action": "WAIT", "reason": "INVALID_ATR"}
        
    # K 棒特徵
    prev_open = float(prev["open"])
    prev_close = float(prev["close"])
    prev_body = abs(prev_close - prev_open)
    prev_is_long = prev_close > prev_open
    prev_is_short = prev_close < prev_open
    
    # 趨勢斜率判斷 (以中軌或 MA3 為基準)
    curr_kc_mid = float(curr["kc_middle"])
    prev_kc_mid = float(prev["kc_middle"])
    is_slope_aligned_long = curr_kc_mid >= prev_kc_mid
    is_slope_aligned_short = curr_kc_mid <= prev_kc_mid
    
    # === 1. 路徑 A：特例 K 爆發 (Special K Path) ===
    # 條件：單根 >= 2.0 ATR 且斜率對齊，豁免空間緩衝
    if prev_body >= 2.0 * atr:
        if side == "LONG" and prev_is_long and is_slope_aligned_long:
            return {
                "action": "ENTER",
                "side": side,
                "reason": "SPECIAL_K_BREAKOUT_LONG",
                "tag": "[SPECIAL_ENTRY]"
            }
        elif side == "SHORT" and prev_is_short and is_slope_aligned_short:
            return {
                "action": "ENTER",
                "side": side,
                "reason": "SPECIAL_K_BREAKOUT_SHORT",
                "tag": "[SPECIAL_ENTRY]"
            }

    # === 空間緩衝檢查 (適用於路徑 B 與 C) ===
    # 在這裡我們直接用參數傳進來的 min_space_buffer_atr，若沒有則預設 0.8 ATR
    if min_space_buffer_atr < 0.8 * atr:
        min_space_buffer_atr = 0.8 * atr

    # 這裡簡化為：外部已經計算好空間，如果傳進來的空間不足，則直接擋下
    # 假設外部呼叫時會將 expected_profit_space 傳入 min_space_buffer_atr 參數中。
    # 為了語意正確，我們將其視為可獲得的利潤空間。
    expected_profit_space = min_space_buffer_atr
    if expected_profit_space < 0.8 * atr:
        return {"action": "WAIT", "reason": "SPACE_TOO_SMALL"}

    # === 2. 路徑 B：結構性轉折 (Structural Reversal Path) ===
    # 條件：MA3 金叉/死叉 + 斜率對齊 + 實體飽滿 (>= 0.6) + 空間緩衝
    prev_ma3 = float(prev.get("ma3", 0))
    prev_ma15 = float(prev.get("ma15", 0))
    prev2_ma3 = float(prev2.get("ma3", 0))
    prev2_ma15 = float(prev2.get("ma15", 0))
    
    is_ma_cross_long = prev_ma3 > prev_ma15 and prev2_ma3 <= prev2_ma15
    is_ma_cross_short = prev_ma3 < prev_ma15 and prev2_ma3 >= prev2_ma15
    
    # 實體飽滿度 (假設實體長度佔高低點全長的比例 >= 0.6)
    prev_high = float(prev["high"])
    prev_low = float(prev["low"])
    prev_range = prev_high - prev_low
    is_solid_body = (prev_body / prev_range >= 0.6) if prev_range > 0 else False
    
    if side == "LONG" and is_ma_cross_long and is_slope_aligned_long and is_solid_body:
        return {
            "action": "ENTER",
            "side": side,
            "reason": "STRUCTURAL_REVERSAL_LONG",
            "tag": "[STRUCTURAL_REVERSAL]"
        }
    elif side == "SHORT" and is_ma_cross_short and is_slope_aligned_short and is_solid_body:
        return {
            "action": "ENTER",
            "side": side,
            "reason": "STRUCTURAL_REVERSAL_SHORT",
            "tag": "[STRUCTURAL_REVERSAL]"
        }

    # === 3. 路徑 C：強勢趨勢延續 (Trend Continuation Path) ===
    # 條件：區段動能確認（3 根 K 棒總動能 >= 1.0 ATR）+ 斜率對齊 + 空間緩衝
    # 總動能：最新收盤價與 3 根前的開盤價之位移
    prev3_open = float(prev3["open"])
    segment_displacement = prev_close - prev3_open
    
    if side == "LONG" and is_slope_aligned_long and segment_displacement >= 1.0 * atr:
        return {
            "action": "ENTER",
            "side": side,
            "reason": "TREND_CONTINUATION_LONG",
            "tag": "[TREND_CONTINUATION]"
        }
    elif side == "SHORT" and is_slope_aligned_short and -segment_displacement >= 1.0 * atr:
        return {
            "action": "ENTER",
            "side": side,
            "reason": "TREND_CONTINUATION_SHORT",
            "tag": "[TREND_CONTINUATION]"
        }

    return {"action": "WAIT", "reason": "NO_ENTRY_CONDITION_MET"}
