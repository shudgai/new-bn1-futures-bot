import math
from core.services.candle_data import closed_entry_candles, closed_entry_problem


# Deleted: check_special_momentum_engulfing (violated strict breakout rules)


import pandas as pd
from typing import Dict, Any, Optional
from core.services.strategies.outer_strategy import ma3_outer_cross_ready, ma3_outer_continuation_ready

CLOSED_BREAKOUT_CODES = {
    f"{prefix}_{side}"
    for prefix in ("MOMENTUM_ENGULFING", "MOMENTUM_BREAKOUT_C1", "CONFIRMED_KC_BREAKOUT")
    for side in ("LONG", "SHORT")
}


def supported_entry_reason(reason, side):
    from core.services.outer_turn_entry import CODES
    from core.services.closed_breakout_entry import CODES as BREAKOUT_CODES
    if reason in ('MA_CROSS_GOLDEN_LONG', 'MA_CROSS_DEATH_SHORT'):
        return side in ('LONG', 'SHORT') and reason.endswith('_' + side)
    return side in ('LONG', 'SHORT') and reason in (CODES | BREAKOUT_CODES) and reason.endswith('_' + side)



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
    from core.services.closed_breakout_entry import evaluate_closed_breakout
    price = frame.iloc[-1]['close'] if frame is not None and not frame.empty else 0
    return evaluate_closed_breakout(frame, price, side)[0]

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
    from core.services.closed_breakout_entry import evaluate_closed_breakout
    return evaluate_closed_breakout(frame, price, side)[2]

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
    return channel_closed_body_break_entry_action(frame, price, side)

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

def is_safe_to_enter(curr: pd.Series, prev: pd.Series, side: str, atr: float) -> Optional[str]:
    """
    環境安全檢查 (Context Check)：
    依據您的強制指令，此函數已完全放行，不再阻擋破軌開倉。
    """
    return None

def check_entry_signals(
    frame: pd.DataFrame, side: str, min_space_buffer_atr: float, state: dict = None
    ) -> Dict[str, Any]:
    """Strictly enforced entry check: K-bar MUST break out of KC, but not overextend."""
    if frame is None or frame.empty:
        return {"action": "WAIT", "side": None, "reason": "EMPTY_FRAME"}
        
    curr = frame.iloc[-1]
    kc_upper = float(curr["kc_upper"])
    kc_lower = float(curr["kc_lower"])
    price = float(curr["close"])
    atr = float(curr["atr"])
    
    # 嚴格鐵律：K棒收盤價(price)必須突破軌道，否則一律 WAIT！
    if side == "LONG":
        if price <= kc_upper:
            return {"action": "WAIT", "side": None, "reason": "STRICT_BLOCK_INSIDE_KC"}
        if (price - kc_upper) > 2.0 * atr:
            return {"action": "WAIT", "side": None, "reason": "OVEREXTENDED_BEYOND_2_ATR_LONG"}
            
    if side == "SHORT":
        if price >= kc_lower:
            return {"action": "WAIT", "side": None, "reason": "STRICT_BLOCK_INSIDE_KC"}
        if (kc_lower - price) > 2.0 * atr:
            return {"action": "WAIT", "side": None, "reason": "OVEREXTENDED_BEYOND_2_ATR_SHORT"}
        
    from core.services.closed_breakout_entry import evaluate_channel_entry
    _, _, decision = evaluate_channel_entry(frame, price, side, state,
                                         str(frame.attrs.get('symbol', '')))
    return decision
