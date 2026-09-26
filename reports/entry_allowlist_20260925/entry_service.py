import math
import logging
from core.services.candle_data import closed_entry_candles, closed_entry_problem


logger = logging.getLogger("uvicorn.error.entry_debug")


def log_entry_inputs(frame, side, stage, symbol="", price=None):
    """Report raw snapshot values without coercing missing/invalid data to zero."""
    closed = closed_entry_candles(frame)
    c1 = closed.iloc[-2] if len(closed) >= 2 else {}
    c2 = closed.iloc[-1] if len(closed) else {}
    live = frame.iloc[-1] if frame is not None and not frame.empty else {}
    logger.info(
        "[ENTRY_DEBUG] stage=%s symbol=%s side=%s c1_close=%r c2_close=%r "
        "c2_open=%r kc_lower=%r kc_upper=%r atr=%r c1_time=%r c2_time=%r "
        "c2_closed=%r closed_count=%s live_time=%r live_close=%r "
        "live_closed=%r live_kc_lower=%r live_kc_upper=%r price=%r",
        stage, symbol, side, c1.get("close"), c2.get("close"), c2.get("open"),
        c2.get("kc_lower"), c2.get("kc_upper"), c2.get("atr"),
        c1.get("timestamp"), c2.get("timestamp"), c2.get("is_closed"), len(closed),
        live.get("timestamp"), live.get("close"), live.get("is_closed"),
        live.get("kc_lower"), live.get("kc_upper"), price,
    )


def ma3_outer_return_problem(frame, price: float, side: str) -> str | None:
    """Reject an outer MA3 turning back toward its rail, using the latest quote."""
    try:
        closed = closed_entry_candles(frame)
        closes = [float(v) for v in closed['close'].iloc[-3:]]
        if len(closes) != 3 or not all(math.isfinite(v) and v > 0 for v in closes):
            return "WAIT_INVALID_MA3_DATA"
        previous = sum(closes) / 3.
        live_ma3 = (sum(closes[-2:]) + float(price)) / 3.
        rail_key = 'kc_upper' if side == 'LONG' else 'kc_lower'
        previous_rail = float(closed.iloc[-1][rail_key])
        live_rail = float(frame.iloc[-1][rail_key])
        sign = 1 if side == 'LONG' else -1
        outside = sign * (previous - previous_rail) > 0 or sign * (live_ma3 - live_rail) > 0
        if outside and sign * (live_ma3 - previous) < 0:
            return "WAIT_MA3_RETURNING_TO_OUTER_RAIL"
    except (KeyError, TypeError, ValueError, IndexError):
        return "WAIT_INVALID_MA3_DATA"
    return None


def strict_kc_entry_gate(frame, price, side):
    """No entry reason may bypass closed confirmation or the live outer rail."""
    if side not in ("LONG", "SHORT"):
        return "UNKNOWN_SIDE"
    try:
        closed = closed_entry_candles(frame)
        if closed.empty:
            return "WAIT_CLOSED_CONFIRMATION"
        c2, live = closed.iloc[-1], frame.iloc[-1]
        close, upper, lower, atr = (float(c2[k]) for k in ("close", "kc_upper", "kc_lower", "atr"))
        live_upper, live_lower = (float(live[k]) for k in ("kc_upper", "kc_lower"))
        price = float(price)
        if (not all(math.isfinite(v) and v > 0 for v in
                    (close, upper, lower, atr, live_upper, live_lower, price))
                or lower >= upper or live_lower >= live_upper):
            return "WAIT_INVALID_MARKET_DATA"
        sign = 1 if side == "LONG" else -1
        rail = upper if side == "LONG" else lower
        live_rail = live_upper if side == "LONG" else live_lower
        if sign * (close - rail) <= 0:
            return "STRICT_BLOCK_CLOSED_INSIDE_KC"
        if sign * (price - live_rail) <= 0:
            return "STRICT_BLOCK_INSIDE_KC"
        if sign * (price - live_rail) > 2.0 * atr:
            return "OVEREXTENDED_BEYOND_2_ATR_" + side
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "WAIT_INVALID_MARKET_DATA"
    return ma3_outer_return_problem(frame, price, side)


# Deleted: check_special_momentum_engulfing (violated strict breakout rules)


import pandas as pd
from typing import Dict, Any, Optional
from core.services.strategies.outer_strategy import ma3_outer_cross_ready, ma3_outer_continuation_ready

CLOSED_BREAKOUT_CODES = {
    f"{prefix}_{side}"
    for prefix in ("MOMENTUM_ENGULFING", "MOMENTUM_BREAKOUT_C1", "CONFIRMED_KC_BREAKOUT")
    for side in ("LONG", "SHORT")
}


PIVOT_REVERSAL_CODES = {'PIVOT_LOW_REVERSAL_LONG', 'PIVOT_HIGH_REVERSAL_SHORT'}

def supported_entry_reason(reason, side):
    return (side == "LONG" and reason == "MA_CROSS_GOLDEN_LONG") or (
        side == "SHORT" and reason == "MA_CROSS_DEATH_SHORT")


def ma_cross_entry_gate(frame, price, side):
    try:
        if not math.isfinite(float(price)) or float(price) <= 0:
            return "WAIT_INVALID_QUOTE"
    except (TypeError, ValueError):
        return "WAIT_INVALID_QUOTE"
    result = check_entry_signals(frame, side, 0)
    return None if result['action'] == 'ENTER' else result['reason']



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

def global_hard_gate_check(frame: pd.DataFrame, side: str) -> Optional[dict]:
    """
    物理位置硬性閘門：徹底封死通道內開倉。
    只要收盤價不在軌道外，必須直接 return WAIT。
    """
    try:
        from core.services.candle_data import closed_entry_candles
        closed = closed_entry_candles(frame)
        if len(closed) < 2:
            return {"action": "WAIT", "reason": "HARD_GATE: 歷史K線不足"}
        
        c2 = closed.iloc[-1]
        atr = float(c2.get("atr", 0))
        
        # 空單唯一物理準則：收盤價必須 < KC 下軌，且本根 K 棒必須是陰線 (close < open)
        if side == "SHORT":
            if c2["close"] >= c2["kc_lower"]:
                return {"action": "WAIT", "reason": "HARD_GATE: 未收在KC下軌外"}
            if c2["close"] >= c2["open"]:
                return {"action": "WAIT", "reason": "HARD_GATE: 陽線嚴禁開空"}
            if atr > 0 and (c2["kc_lower"] - c2["close"]) > 2.0 * atr:
                return {"action": "WAIT", "reason": "HARD_GATE: 超過2.0 ATR力竭防追空"}

        # 多單唯一物理準則：收盤價必須 > KC 上軌，且本根 K 棒必須是陽線 (close > open)
        if side == "LONG":
            if c2["close"] <= c2["kc_upper"]:
                return {"action": "WAIT", "reason": "HARD_GATE: 未收在KC上軌外"}
            if c2["close"] <= c2["open"]:
                return {"action": "WAIT", "reason": "HARD_GATE: 陰線嚴禁開多"}
            if atr > 0 and (c2["close"] - c2["kc_upper"]) > 2.0 * atr:
                return {"action": "WAIT", "reason": "HARD_GATE: 超過2.0 ATR力竭防追多"}
                
    except (KeyError, ValueError, TypeError):
        return {"action": "WAIT", "reason": "HARD_GATE: 數據無效"}
        
    return None

def check_entry_signals(
    frame: pd.DataFrame, side: str, min_space_buffer_atr: float, state: dict = None
) -> Dict[str, Any]:
    """Double-Candle Momentum Breakout and Continuation (Overrides flat markets and cooldown)."""
    log_entry_inputs(frame, side, "check_entry_signals")
    wait = lambda reason: dict(action="WAIT", side=None, reason=reason)
    if side not in ("LONG", "SHORT"):
        return wait("UNKNOWN_SIDE")
    closed = closed_entry_candles(frame)
    if len(closed) < 2:
        return wait("WAIT_CLOSED_CONFIRMATION")
    try:
        c1, c2 = closed.iloc[-2], closed.iloc[-1]
        
        c1_open, c1_close = float(c1['open']), float(c1['close'])
        c1_high, c1_low = float(c1['high']), float(c1['low'])
        
        c2_open, c2_close = float(c2['open']), float(c2['close'])
        
        atr = float(c2.get('atr', 0))
        if not math.isfinite(atr) or atr <= 0:
            return wait("WAIT_INVALID_ATR")
            
        if side == "LONG":
            c1_kc_upper = float(c1.get('kc_upper', 0))
            c2_kc_upper = float(c2.get('kc_upper', 0))
            
            # === 多單決策流程 ===
            # 1. 基礎物理硬閘門：價格必須在 KC 上軌外
            if c2_close > c2_kc_upper:
                deviation = c2_close - c2_kc_upper
                
                # 防過度乖離力竭 (超過 1.5 ATR 不追)
                if deviation <= 1.5 * atr:
                    # 通道 1：首次破軌 (前一根在軌內，本根收陽突破)
                    is_first_breakout = (c1_close <= c1_kc_upper) and (c2_close > c2_open)
                    
                    # 通道 2：順勢延續 (前一根已在軌外，本根收陽破前高)
                    is_continuation = (
                        (c1_close > c1_kc_upper)
                        and (c2_close > c2_open)
                        and (c2_close > c1_high)
                    )
                    
                    if is_first_breakout or is_continuation:
                        reason = (
                            "ENTER_FIRST_BREAKOUT_LONG"
                            if is_first_breakout
                            else "ENTER_CONTINUATION_LONG"
                        )
                        return dict(
                            action="ENTER", side="LONG", reason=reason,
                            entry_atr=atr, bypass_flat_check=True,
                            bypass_cooldown=True, entry_type="MOMENTUM_BREAKOUT"
                        )
                else:
                    return wait("WAIT_OVEREXTENDED_LONG")
            return wait("WAIT_NOT_OUTSIDE_KC_LONG")

        elif side == "SHORT":
            c1_kc_lower = float(c1.get('kc_lower', 0))
            c2_kc_lower = float(c2.get('kc_lower', 0))
            
            # === 空單決策流程 ===
            # 1. 基礎物理硬閘門：價格必須在 KC 下軌外
            if c2_close < c2_kc_lower:
                deviation = c2_kc_lower - c2_close
                
                # 防過度乖離力竭 (超過 1.5 ATR 不追)
                if deviation <= 1.5 * atr:
                    # 通道 1：首次破軌 (前一根在軌內，本根收陰跌破)
                    is_first_breakout = (c1_close >= c1_kc_lower) and (c2_close < c2_open)
                    
                    # 通道 2：順勢延續 (前一根已在軌外，本根收陰破前低)
                    is_continuation = (
                        (c1_close < c1_kc_lower)
                        and (c2_close < c2_open)
                        and (c2_close < c1_low)
                    )
                    
                    if is_first_breakout or is_continuation:
                        reason = (
                            "ENTER_FIRST_BREAKOUT_SHORT"
                            if is_first_breakout
                            else "ENTER_CONTINUATION_SHORT"
                        )
                        return dict(
                            action="ENTER", side="SHORT", reason=reason,
                            entry_atr=atr, bypass_flat_check=True,
                            bypass_cooldown=True, entry_type="MOMENTUM_BREAKOUT"
                        )
                else:
                    return wait("WAIT_OVEREXTENDED_SHORT")
            return wait("WAIT_NOT_OUTSIDE_KC_SHORT")
                        
    except (KeyError, TypeError, ValueError, IndexError):
        return wait("WAIT_INVALID_MARKET_DATA")
