"""Unified Entry Strategy evaluating 4 distinct entry methods.
Implements IEntryStrategy interface.
"""
import math
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy


def check_common_direction(frame: pd.DataFrame, side: str) -> bool:
    """
    共同方向條件：
    比較當根中軌與前一根已收線中軌。
    中軌上升：只評估多單。
    中軌下降：只評估空單。
    中軌持平或資料無效：不開倉。
    """
    try:
        if frame is None or len(frame) < 2 or side not in ("LONG", "SHORT"):
            return False
        key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
        prev_mid = float(frame.iloc[-2][key])
        curr_mid = float(frame.iloc[-1][key])
        
        if not (math.isfinite(prev_mid) and math.isfinite(curr_mid) and prev_mid > 0 and curr_mid > 0):
            return False
            
        if side == "LONG":
            return curr_mid > prev_mid
        else:
            return curr_mid < prev_mid
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def check_target_space(price: float, atr: float, target: float = None, side: str = "LONG") -> bool:
    """
    目標空間檢查（≥ 1.5 ATR）：作為進場前的最後一道過濾條件。
    若未傳入前高低時暫以預設 3 ATR 兜底。
    """
    if not math.isfinite(atr) or atr <= 0:
        return False
        
    if target is None or not math.isfinite(target):
        # 預設 3 ATR
        return True
        
    if side == "LONG":
        return (target - price) / atr >= 1.5
    else:
        return (price - target) / atr >= 1.5


def check_ma_cross_entry(frame: pd.DataFrame, side: str, price: float) -> Tuple[bool, str]:
    """
    峰谷進場 (MA 收盤金叉)：
    需要三根已收線形成谷底/頂峰。
    多單：第三根收盤突破中間根高點；谷底位於下軌附近或外側。
    空單：第三根收盤跌破中間根低點；頂峰位於上軌附近或外側。
    """
    try:
        if frame is None or len(frame) < 4:
            return False, "WAIT_PIVOT_DATA"
            
        # 檢查三根已收線 (排除最新一根未收線)
        left = frame.iloc[-4]
        mid = frame.iloc[-3]
        right = frame.iloc[-2]
        
        if side == "LONG":
            if not (float(mid["low"]) < float(left["low"]) and float(mid["low"]) < float(right["low"])):
                return False, "WAIT_PIVOT_VALLEY"
            if float(right["close"]) <= float(mid["high"]):
                return False, "WAIT_PIVOT_BREAK_HIGH"
            
            # 谷底位置
            if float(mid["low"]) > float(mid["kc_lower"]) * 1.01:
                return False, "WAIT_PIVOT_NEAR_LOWER"
                
            return True, "ENTRY_PIVOT_LONG"
            
        elif side == "SHORT":
            if not (float(mid["high"]) > float(left["high"]) and float(mid["high"]) > float(right["high"])):
                return False, "WAIT_PIVOT_PEAK"
            if float(right["close"]) >= float(mid["low"]):
                return False, "WAIT_PIVOT_BREAK_LOW"
                
            # 頂峰位置
            if float(mid["high"]) < float(mid["kc_upper"]) * 0.99:
                return False, "WAIT_PIVOT_NEAR_UPPER"
                
            return True, "ENTRY_PIVOT_SHORT"
            
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False, "WAIT_PIVOT_ERROR"
    return False, "WAIT_PIVOT"


def check_breakout_entry(frame: pd.DataFrame, side: str) -> Tuple[bool, str]:
    """
    破軌確認：
    突破根與確認根都須收線，實體 ≥ 20%。
    多單：同向K(或反色只要收在同側軌外)收在上軌外。後續第一根合格陽線收在上軌外。
    空單：同向K(或反色只要收在同側軌外)收在下軌外。後續第一根合格陰線收在下軌外。
    """
    try:
        if frame is None or len(frame) < 3:
            return False, "WAIT_BREAKOUT_DATA"
            
        breakout = frame.iloc[-3]
        confirm = frame.iloc[-2]
        
        body = abs(float(confirm["close"]) - float(confirm["open"]))
        candle_range = float(confirm["high"]) - float(confirm["low"])
        if candle_range <= 0 or body / candle_range < 0.20:
            return False, "WAIT_BREAKOUT_BODY_SIZE"
            
        if side == "LONG":
            if float(breakout["close"]) <= float(breakout["kc_upper"]):
                return False, "WAIT_BREAKOUT_BAR_UPPER"
            if float(confirm["close"]) <= float(confirm["kc_upper"]) or float(confirm["close"]) <= float(confirm["open"]):
                return False, "WAIT_BREAKOUT_CONFIRM_UPPER"
            return True, "ENTRY_BREAKOUT_LONG"
            
        elif side == "SHORT":
            if float(breakout["close"]) >= float(breakout["kc_lower"]):
                return False, "WAIT_BREAKOUT_BAR_LOWER"
            if float(confirm["close"]) >= float(confirm["kc_lower"]) or float(confirm["close"]) >= float(confirm["open"]):
                return False, "WAIT_BREAKOUT_CONFIRM_LOWER"
            return True, "ENTRY_BREAKOUT_SHORT"

    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False, "WAIT_BREAKOUT_ERROR"
    return False, "WAIT_BREAKOUT"


def check_extreme_momentum_entry(frame: pd.DataFrame, side: str) -> Tuple[bool, str]:
    """
    特例長 K：
    不等收線，盤中即時判斷。
    當根實體 ≥ 1.8 ATR 且實體占全長 ≥ 75%。
    防呆機制：嚴禁在軌外連漲（連跌） 2 根以上時觸發，避免山頂追多/谷底追空。
    """
    try:
        if frame is None or len(frame) < 4:
            return False, "WAIT_LONG_K_DATA"
            
        live = frame.iloc[-1]
        prev = frame.iloc[-2]
        atr = float(prev["atr"])
        
        opened = float(live["open"])
        current_price = float(live["close"])
        high = float(live["high"])
        low = float(live["low"])
        
        body = abs(current_price - opened)
        candle_range = high - low
        
        if body < 1.8 * atr:
            return False, "WAIT_LONG_K_ATR"
            
        if candle_range <= 0 or body / candle_range < 0.75:
            return False, "WAIT_LONG_K_BODY_RATIO"
            
        # 防呆：檢查是否已處於軌外連漲/連跌狀態 (>= 2 根)
        prev1 = frame.iloc[-2]
        prev2 = frame.iloc[-3]
        
        if side == "LONG" and current_price > opened:
            outside_streak = 0
            if float(prev1["close"]) > float(prev1["open"]) and float(prev1["close"]) > float(prev1["kc_upper"]):
                outside_streak += 1
                if float(prev2["close"]) > float(prev2["open"]) and float(prev2["close"]) > float(prev2["kc_upper"]):
                    outside_streak += 1
                    
            if outside_streak >= 2:
                return False, "WAIT_LONG_K_STREAK_BLOCK"
                
            return True, "ENTRY_LONG_K_LONG"
            
        elif side == "SHORT" and current_price < opened:
            outside_streak = 0
            if float(prev1["close"]) < float(prev1["open"]) and float(prev1["close"]) < float(prev1["kc_lower"]):
                outside_streak += 1
                if float(prev2["close"]) < float(prev2["open"]) and float(prev2["close"]) < float(prev2["kc_lower"]):
                    outside_streak += 1
                    
            if outside_streak >= 2:
                return False, "WAIT_LONG_K_STREAK_BLOCK"
                
            return True, "ENTRY_LONG_K_SHORT"
            
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False, "WAIT_LONG_K_ERROR"
    return False, "WAIT_LONG_K"


def check_refueling_entry(frame: pd.DataFrame, side: str, price: float) -> Tuple[bool, str]:
    """
    空中加油：需 3 根淺谷底 (Fractal Swing Low)。
    必須等突破 K 棒收線定格才可進場，禁止盤中搶跑。
    多單：淺谷底低點 ≥ 中軌 − 0.2 ATR；隨後收陽並收盤突破結構高點。
    空單：淺頂峰高點 ≤ 中軌 ＋ 0.2 ATR；隨後收陰並收盤跌破結構低點。
    """
    try:
        if frame is None or len(frame) < 6:
            return False, "WAIT_REFUELING_DATA"
            
        # 3 根 K 線波谷 (因為突破根必須收線，所以往前推一格，突破根是 -2，波谷是 -5, -4, -3)
        left = frame.iloc[-5]
        mid = frame.iloc[-4]
        right = frame.iloc[-3]
        
        # 突破根 (已收線)
        breakout_bar = frame.iloc[-2]
        prev_atr = float(frame.iloc[-3]["atr"]) # 突破根之前的那根的 ATR
        
        mid_midline = float(mid["kc_middle"]) if "kc_middle" in mid else float(mid["ema_20"])
        
        # 結構高/低點 (左中右三根的極值)
        struct_high = max(float(left["high"]), float(mid["high"]), float(right["high"]))
        struct_low = min(float(left["low"]), float(mid["low"]), float(right["low"]))
        
        if side == "LONG":
            # 三根低點形成波谷
            if not (float(mid["low"]) < float(left["low"]) and float(mid["low"]) < float(right["low"])):
                return False, "WAIT_REFUELING_VALLEY"
            
            # 淺回調限制
            if float(mid["low"]) < mid_midline - 0.2 * prev_atr:
                return False, "WAIT_REFUELING_SHALLOW"
                
            # 突破結構高點 (且收陽線)
            if float(breakout_bar["close"]) <= struct_high or float(breakout_bar["close"]) <= float(breakout_bar["open"]):
                return False, "WAIT_REFUELING_BREAK_HIGH"
                
            return True, "ENTRY_REFUELING_LONG"
            
        elif side == "SHORT":
            # 三根高點形成頂峰
            if not (float(mid["high"]) > float(left["high"]) and float(mid["high"]) > float(right["high"])):
                return False, "WAIT_REFUELING_PEAK"
                
            # 淺回調限制
            if float(mid["high"]) > mid_midline + 0.2 * prev_atr:
                return False, "WAIT_REFUELING_SHALLOW"
                
            # 跌破結構低點 (且收陰線)
            if float(breakout_bar["close"]) >= struct_low or float(breakout_bar["close"]) >= float(breakout_bar["open"]):
                return False, "WAIT_REFUELING_BREAK_LOW"
                
            return True, "ENTRY_REFUELING_SHORT"
            
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False, "WAIT_REFUELING_ERROR"
    return False, "WAIT_REFUELING"


class UnifiedEntryStrategy(IEntryStrategy):
    """OOP Strategy class implementing IEntryStrategy for the 4 unified entry conditions."""

    def evaluate_entry(
        self,
        frame: pd.DataFrame,
        price: float,
        side: str,
        **kwargs: Any
    ) -> Tuple[bool, str, Dict[str, Any]]:
        
        position = kwargs.get("position")
        target_price = kwargs.get("target_price") # 前高/前低目標價
        
        if position:
            if position.get("pivot_reversal_exit_pending"):
                return False, "WAIT_PIVOT_REVERSAL_UNLOCK", {"action": "WAIT"}
        
        if not check_common_direction(frame, side):
            return False, "WAIT_COMMON_DIRECTION", {"action": "WAIT"}
            
        # 檢查 4 種入口
        is_valid = False
        reason = "WAIT_ALL_ENTRIES"
        
        v, r = check_breakout_entry(frame, side)
        if v:
            is_valid, reason = True, r
        else:
            v, r = check_extreme_momentum_entry(frame, side)
            if v:
                is_valid, reason = True, r
            else:
                v, r = check_refueling_entry(frame, side, price)
                if v:
                    is_valid, reason = True, r
                else:
                    v, r = check_ma_cross_entry(frame, side, price)
                    if v:
                        is_valid, reason = True, r
                        
        if is_valid:
            # 最後一道過濾條件：目標空間
            try:
                atr = float(frame.iloc[-2]["atr"])
                # 空中加油本質是中繼突破，不檢查目標空間
                if "REFUELING" not in reason:
                    if not check_target_space(price, atr, target_price, side):
                        return False, "WAIT_TARGET_SPACE", {"action": "WAIT"}
                else:
                    print(f"[UnifiedEntry] REFUELING signal detected -> Target space check bypassed.", flush=True)
            except (AttributeError, KeyError, TypeError, ValueError, IndexError):
                return False, "WAIT_TARGET_SPACE_ERROR", {"action": "WAIT"}
                
            return True, reason, {"action": "ENTER", "side": side, "reason": reason}
            
        return False, reason, {"action": "WAIT"}
