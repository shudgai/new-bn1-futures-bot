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

def evaluate_dynamic_priority_entry(
    frame: pd.DataFrame, side: str, expected_profit_space: float, state: dict = None
) -> Dict[str, Any]:
    """
    動態優先級進場系統 (三級優先權)
    整合了空間動態門檻與趨勢權限的合一進場邏輯，並加入『趨勢鎖定』補償機制
    """
    if state is None:
        state = {}
        
    if frame is None or len(frame) < 3:
        return {"action": "WAIT", "reason": "INSUFFICIENT_DATA"}
        
    curr = frame.iloc[-1]
    prev = frame.iloc[-2]
    prev2 = frame.iloc[-3]
    
    atr = float(curr.get("atr", 0))
    if atr <= 0:
        return {"action": "WAIT", "reason": "INVALID_ATR"}
        
    # K 棒特徵
    prev_body = abs(float(prev["close"]) - float(prev["open"]))
    prev_is_long = float(prev["close"]) > float(prev["open"])
    prev_is_short = float(prev["close"]) < float(prev["open"])
    
    # 通道擴張判斷
    curr_kc_width = float(curr["kc_upper"]) - float(curr["kc_lower"])
    prev_kc_width = float(prev["kc_upper"]) - float(prev["kc_lower"])
    is_expanding = curr_kc_width > prev_kc_width
    
    # 判斷實體是否收在中軌之外 (LONG: 實體下緣 > 中軌, SHORT: 實體上緣 < 中軌)
    def is_body_outside_mid(row, direction):
        if direction == "LONG":
            return min(float(row["close"]), float(row["open"])) > float(row["kc_middle"])
        else:
            return max(float(row["close"]), float(row["open"])) < float(row["kc_middle"])
            
    prev_outside_mid = is_body_outside_mid(prev, side)
    prev2_outside_mid = is_body_outside_mid(prev2, side)
    
    # 3. 狀態清除：如果回落到中軌內，或通道停止擴張並開始收斂，清除『趨勢鎖定模式』
    if state.get("trend_locked_side") == side:
        if not prev_outside_mid or not is_expanding:
            state.pop("trend_locked_side", None)
            
    # === 1. 最高優先級：極端動能特權 (Extreme Momentum) ===
    # 條件：前一根 K 棒實體 >= 2.0 ATR
    if prev_body >= 2.0 * atr:
        if (side == "LONG" and prev_is_long) or (side == "SHORT" and prev_is_short):
            state.pop("trend_locked_side", None) # 進場即清除鎖定
            return {
                "action": "ENTER",
                "side": side,
                "reason": f"SPECIAL_ENTRY_MOMENTUM_{side}",
                "tag": "[Special Entry] Extreme Momentum"
            }
            
    # === 補償機制：趨勢鎖定下的進場規則 ===
    if state.get("trend_locked_side") == side and is_expanding:
        if expected_profit_space >= 0.8 * atr:
            state.pop("trend_locked_side", None) # 進場即清除鎖定
            return {
                "action": "ENTER",
                "side": side,
                "reason": f"TREND_CONTINUATION_LOCKED_{side}",
                "tag": "[Entry] Trend Continuation (Locked)"
            }
            
    # === 2. 次高優先級：趨勢延續進場 (Trend Continuation) ===
    # 條件：價格已連續兩根 K 棒實體收在 KC 中軌之外，且通道寬度正在擴張
    if prev_outside_mid and prev2_outside_mid and is_expanding:
        if expected_profit_space >= 0.3 * atr:
            state.pop("trend_locked_side", None)
            return {
                "action": "ENTER",
                "side": side,
                "reason": f"TREND_CONTINUATION_{side}",
                "tag": "[Entry] Trend Continuation"
            }
        else:
            return {"action": "WAIT", "reason": "SPACE_TOO_SMALL_FOR_CONTINUATION"}
            
    # === 3. 標準優先級：初始破軌進場 (Initial Breakout) ===
    # 條件：價格剛開始突破 KC 中軌 (前一根破，前兩根沒破)，且通道正在擴張
    if prev_outside_mid and not prev2_outside_mid and is_expanding:
        if expected_profit_space >= 1.5 * atr:
            state.pop("trend_locked_side", None)
            return {
                "action": "ENTER",
                "side": side,
                "reason": f"INITIAL_BREAKOUT_{side}",
                "tag": "[Entry] Initial Breakout"
            }
        else:
            # 建立『趨勢鎖定』狀態
            state["trend_locked_side"] = side
            return {
                "action": "WAIT", 
                "reason": "INITIAL_SPACE_INSUFFICIENT_LOCKED",
                "tag": "[Skip Order] Initial Space Insufficient - Entering Trend Lock."
            }
            
    return {"action": "WAIT", "reason": "NO_DYNAMIC_ENTRY_CONDITION_MET"}
