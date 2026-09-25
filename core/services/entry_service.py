
def check_special_momentum_engulfing(df):
    """檢查：微幅蓄勢後突發大動能吞噬開倉 (支援 KC 與 MA 雙模式，豁免橫盤，無視冷卻)

    df: 包含 open, high, low, close, atr, kc_upper, kc_lower, ma3, ma15 的 DataFrame
    """
    if len(df) < 3:
        return None

    c1 = df.iloc[-2]  # 前一根蓄勢棒
    c2 = df.iloc[-1]  # 最新收盤的突發動能棒
    atr = float(c2.get("atr", 0))

    if atr <= 0:
        return None

    # 計算實體長度
    c1_body = abs(c1["close"] - c1["open"])
    c2_body = abs(c2["close"] - c2["open"])

    # 1. 基礎條件：c1 為蓄勢小K棒，c2 為突發大長實體 (>= 1.5 ATR 且 至少是 c1 的 2 倍)
    is_c1_small = c1_body <= 0.6 * atr
    is_c2_huge = c2_body >= 1.5 * atr and c2_body >= 2.0 * c1_body

    if not (is_c1_small and is_c2_huge):
        return None

    # ==================== 空單判斷 (SHORT) ====================
    # c2 必須是實體陰線 (跌)
    if c2["close"] < c2["open"]:
        # 條件 1：跌破前一根最低點 (吞噬)
        engulf_short = c2["close"] < c1["low"]

        # 條件 2：KC 模式跌破 (或貫穿下軌) OR MA 模式 (收盤灌破 MA3 且低於 MA15)
        kc_pattern_short = c2["close"] <= float(c2.get("kc_lower", 0)) or (
            c1["close"] > float(c1.get("kc_lower", 0))
            and c2["close"] < float(c2.get("kc_middle", 0))
        )

        ma_pattern_short = c2["close"] < float(c2.get("ma3", 0)) and c2[
            "close"
        ] < float(c2.get("ma15", 0))

        if engulf_short and (kc_pattern_short or ma_pattern_short):
            return {
                "action": "ENTER",
                "side": "SHORT",
                "reason": "MOMENTUM_ENGULFING_SHORT",
                "entry_atr": atr,
                "allow_pyramiding": True,  # 標記：允許加倉/重複開倉
                "bypass_cooldown": True,  # 標記：無冷卻期
                "bypass_flat_check": True,  # 標記：指標不明/平盤強制豁免
            }

    # ==================== 多單判斷 (LONG) ====================
    # c2 必須是實體陽線 (漲)
    if c2["close"] > c2["open"]:
        # 條件 1：突破前一根最高點 (吞噬)
        engulf_long = c2["close"] > c1["high"]

        # 條件 2：KC 模式突破 (或貫穿上軌) OR MA 模式 (收盤強拉突破 MA3 且高於 MA15)
        kc_pattern_long = c2["close"] >= float(c2.get("kc_upper", 0)) or (
            c1["close"] < float(c1.get("kc_upper", 0))
            and c2["close"] > float(c2.get("kc_middle", 0))
        )

        ma_pattern_long = c2["close"] > float(c2.get("ma3", 0)) and c2[
            "close"
        ] > float(c2.get("ma15", 0))

        if engulf_long and (kc_pattern_long or ma_pattern_long):
            return {
                "action": "ENTER",
                "side": "LONG",
                "reason": "MOMENTUM_ENGULFING_LONG",
                "entry_atr": atr,
                "allow_pyramiding": True,  # 標記：允許加倉/重複開倉
                "bypass_cooldown": True,  # 標記：無冷卻期
                "bypass_flat_check": True,  # 標記：指標不明/平盤強制豁免
            }

    return None


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

def is_safe_to_enter(curr: pd.Series, prev: pd.Series, side: str, atr: float) -> Optional[str]:
    """
    環境安全檢查 (Context Check)：
    依據您的強制指令，此函數已完全放行，不再阻擋破軌開倉。
    """
    return None

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
        
    if frame is None or len(frame) < 2:
        return {"action": "WAIT", "reason": "INSUFFICIENT_DATA"}

    # 特例豁免：突發大動能吞噬開倉
    momentum_signal = check_special_momentum_engulfing(frame)
    if momentum_signal and momentum_signal["side"] == side:
        return momentum_signal


    c1 = frame.iloc[-2]
    c2 = frame.iloc[-1]
    atr = float(c2.get("atr", 0))
    
    if atr <= 0:
        return "WAIT_INVALID_ATR", {"action": "WAIT"}

    c2_body = abs(c2["close"] - c2["open"])

    # ==================== 1. 大動能長實體：第一根收盤即刻開倉 ====================
    # 多單：當前這根 c2 剛好爆破上軌，且是超大實體 (>= 1.2 ATR)
    if (
        side == "LONG"
        and c2["close"] > c2.get("kc_upper", 0)
        and c2["close"] > c2["open"]
        and c2_body >= 1.2 * atr
    ):
        return {
            "action": "ENTER",
            "side": "LONG",
            "reason": "MOMENTUM_BREAKOUT_C1_LONG",
            "entry_atr": atr,
        }

    # 空單：當前這根 c2 剛好爆破下軌，且是超大實體 (>= 1.2 ATR)
    if (
        side == "SHORT"
        and c2["close"] < c2.get("kc_lower", 0)
        and c2["close"] < c2["open"]
        and c2_body >= 1.2 * atr
    ):
        return {
            "action": "ENTER",
            "side": "SHORT",
            "reason": "MOMENTUM_BREAKOUT_C1_SHORT",
            "entry_atr": atr,
        }

    # ==================== 2. 常規突破：等第二根 (c2) 收盤確認 ====================
    # 多單：c1 破上軌，c2 收盤依然留於上軌外
    if side == "LONG" and c1["close"] > c1.get("kc_upper", 0) and c2["close"] > c2.get("kc_upper", 0):
        return {
            "action": "ENTER",
            "side": "LONG",
            "reason": "CONFIRMED_KC_BREAKOUT_LONG",
            "entry_atr": atr,
        }

    # 空單：c1 破下軌，c2 收盤依然留於下軌外
    if side == "SHORT" and c1["close"] < c1.get("kc_lower", 0) and c2["close"] < c2.get("kc_lower", 0):
        return {
            "action": "ENTER",
            "side": "SHORT",
            "reason": "CONFIRMED_KC_BREAKOUT_SHORT",
            "entry_atr": atr,
        }
        
    return {"action": "WAIT"}

