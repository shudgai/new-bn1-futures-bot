"""Dual-Track Exit Strategy evaluating extreme defense, structure swing breaks, and ratchet locks.
Implements IExitStrategy interface.
"""
import math
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy


DUAL_TRACK_STATE_KEYS = (
    "ratchet_lock_state", "last_valid_swing_low", "last_valid_swing_high",
)


def check_emergency_exit(position: Dict[str, Any], frame: pd.DataFrame, price: float) -> Optional[str]:
    """
    極端行情斷路器 (最高優先級)：
    1. 大瀑布 (Flash Crash)：單根跌/漲幅超過 5%
    2. 連續兩根異常 K 線 (Double Crash)：兩根實體都大於 1.5 ATR 且持續向不利方向推進
    """
    if position.get("emergency_circuit_breaker"):
        return "EMERGENCY_EXIT_CIRCUIT_BREAKER"
        
    try:
        if frame is None or len(frame) < 3:
            return None
            
        side = position.get("side")
        if side not in ("LONG", "SHORT"):
            return None
            
        last = frame.iloc[-1]
        prev = frame.iloc[-2]
        
        # --- 定義異常參數 ---
        waterfall_percent = 0.05  # 單根跌/漲幅超過 5% 視為大瀑布
        atr_multiplier = 1.5      # 實體大於 1.5 倍 ATR 視為異常
        
        last_close = float(last["close"])
        last_open = float(last["open"])
        last_atr = float(last.get("atr", price * 0.01))
        
        prev_close = float(prev["close"])
        prev_open = float(prev["open"])
        prev_atr = float(prev.get("atr", price * 0.01))
        
        if side == "LONG":
            # 定義「異常陰線」
            def is_abnormal_bearish(o, c, atr):
                return (o - c) > (atr_multiplier * atr) and c < o
                
            prev_is_abnormal = is_abnormal_bearish(prev_open, prev_close, prev_atr)
            last_is_abnormal = is_abnormal_bearish(last_open, last_close, last_atr)
            
            # A. 兩根連續大陰線
            is_double_crash = prev_is_abnormal and last_is_abnormal
            
            # B. 單一極大瀑布
            single_waterfall = (prev_close - last_close) > (prev_close * waterfall_percent)
            
            if single_waterfall:
                return "EXIT_EMERGENCY_WATERFALL_LONG"
            if is_double_crash:
                return "EXIT_EMERGENCY_DOUBLE_ABNORMAL_LONG"
                
        elif side == "SHORT":
            # 定義「異常陽線」 (軋空)
            def is_abnormal_bullish(o, c, atr):
                return (c - o) > (atr_multiplier * atr) and c > o
                
            prev_is_abnormal = is_abnormal_bullish(prev_open, prev_close, prev_atr)
            last_is_abnormal = is_abnormal_bullish(last_open, last_close, last_atr)
            
            # A. 兩根連續大陽線
            is_double_crash = prev_is_abnormal and last_is_abnormal
            
            # B. 單一極大瀑布 (軋空)
            single_waterfall = (last_close - prev_close) > (prev_close * waterfall_percent)
            
            if single_waterfall:
                return "EXIT_EMERGENCY_WATERFALL_SHORT"
            if is_double_crash:
                return "EXIT_EMERGENCY_DOUBLE_ABNORMAL_SHORT"
                
    except Exception:
        pass
    return None

def check_structure_exit(position: Dict[str, Any], frame: pd.DataFrame, price: float) -> Optional[str]:
    """
    常規波段平倉：
    CK 轉向前的最後峰谷破位。
    """
    try:
        if frame is None or len(frame) < 4:
            return None
            
        side = position.get("side")
        if side not in ("LONG", "SHORT"):
            return None
            
        # 1. 手動開倉豁免早期逃命 (避免人工摸底或測單被秒殺)
        is_manual = position.get("manual_entry", False) or position.get("is_manual", False)
        if is_manual:
            return None
            
        # 計算 CK 斜率
        key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
        mid_curr = float(frame.iloc[-2][key])
        mid_prev = float(frame.iloc[-3][key])
        kc_slope = mid_curr - mid_prev
        
        # 尋找與記錄最後有效波谷/波峰
        left = frame.iloc[-4]
        mid = frame.iloc[-3]
        right = frame.iloc[-2]
        
        if side == "LONG":
            # 確認波谷 ( Fractal Swing Low )
            if float(mid["low"]) < float(left["low"]) and float(mid["low"]) < float(right["low"]):
                if kc_slope > 0: # 在 CK 向上期間記錄
                    position["last_valid_swing_low"] = float(mid["low"])
                    
            last_low = position.get("last_valid_swing_low")
            if last_low and kc_slope <= 0 and price < last_low:
                return "CK_REVERSAL_SWING_BREAK_LONG"
                
        elif side == "SHORT":
            # 確認波峰 ( Fractal Swing High )
            if float(mid["high"]) > float(left["high"]) and float(mid["high"]) > float(right["high"]):
                if kc_slope < 0: # 在 CK 向下期間記錄
                    position["last_valid_swing_high"] = float(mid["high"])
                    
            last_high = position.get("last_valid_swing_high")
            if last_high and kc_slope >= 0 and price > last_high:
                return "CK_REVERSAL_SWING_BREAK_SHORT"
                
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        pass
    return None


def check_hard_stop_exit(position: dict, frame: 'pd.DataFrame', price: float) -> str | None:
    """
    第一層：硬性保護線 (Hard Stop Loss) —— 「保命符」
    Entry_Price ± (2.0 * ATR)
    """
    try:
        side = position.get("side")
        entry = float(position.get("entry_price") or 0)
        
        if frame is None or len(frame) < 3 or entry <= 0:
            return None
            
        atr = float(frame.iloc[-2]["atr"])
        if atr <= 0:
            return None
            
        if side == "LONG":
            hard_stop = entry - (2.0 * atr)
            if price <= hard_stop:
                return "HARD_STOP_EXIT"
        elif side == "SHORT":
            hard_stop = entry + (2.0 * atr)
            if price >= hard_stop:
                return "HARD_STOP_EXIT"
                
    except Exception:
        pass
    return None

def check_partial_take_profit(position: dict, frame: 'pd.DataFrame', price: float) -> str | None:
    """
    目標一：當價格達到 KC 對稱點 (中軌 -> 對向軌道) 時，平掉 50% 倉位。
    """
    if position.get("is_half_closed"):
        return None
        
    if frame is None or len(frame) < 1:
        return None
        
    side = position.get("side")
    kc_upper = float(frame.iloc[-1].get("kc_upper", price))
    kc_lower = float(frame.iloc[-1].get("kc_lower", price))
    
    if side == "LONG":
        if price >= kc_upper:
            return "PARTIAL_TAKE_PROFIT"
    elif side == "SHORT":
        if price <= kc_lower:
            return "PARTIAL_TAKE_PROFIT"
            
    return None

def check_trailing_stop_exit(position: dict, frame: 'pd.DataFrame', price: float, fee: float = 0.0005, slippage: float = 0.0005) -> str | None:
    """
    第二層：保本與結構式移動鎖利 (Break-Even & Structural Trailing Stop)
    1. 當獲利 >= 1R 時，止損點移至進場點。
    2. 當獲利 > 1R 後，止損點跟隨前一個結構點 (Swing High/Low) ± 0.2 ATR，只進不退。
    """
    try:
        if frame is None or len(frame) < 4:
            return None
            
        side = position.get("side")
        entry = float(position.get("entry_price") or 0)
        initial_sl = float(position.get("initial_sl") or 0)
        
        # 若沒有 initial_sl，用 2 ATR 估算初始風險
        atr_current = float(frame.iloc[-1].get("atr", price * 0.01))
        if initial_sl <= 0:
            if side == "LONG":
                initial_sl = entry - 2 * atr_current
            else:
                initial_sl = entry + 2 * atr_current
                
        if entry <= 0:
            return None
            
        # 初始風險 (1R)
        initial_risk = abs(entry - initial_sl)
        if initial_risk <= 0:
            initial_risk = 2 * atr_current
            
        # 計算目前未實現利潤
        sign = 1 if side == "LONG" else -1
        current_profit = sign * (price - entry)
        
        # 狀態紀錄
        state = position.setdefault("v7_trailing_state", {})
        current_sl = float(state.get("current_sl", initial_sl))
        
        atr = float(frame.iloc[-2].get("atr", atr_current))
        
        if side == "LONG":
            # 獲利 >= 1R
            if current_profit >= initial_risk:
                # 至少保本
                new_sl = max(current_sl, entry)
                
                # 尋找前一個結構點 (過去 3 根已收線的最低點)
                swing_low = float(frame["low"].iloc[-4:-1].min())
                # 加上 0.2 ATR 緩衝 (多單防插針往外擴)
                structural_sl = swing_low - (0.2 * atr)
                
                # 止損點只進不退
                new_sl = max(new_sl, structural_sl)
                state["current_sl"] = new_sl
                
            if price <= state.get("current_sl", initial_sl):
                return "TRAILING_STOP_EXIT_V7"
                
        elif side == "SHORT":
            if current_profit >= initial_risk:
                # 至少保本
                new_sl = min(current_sl, entry)
                
                # 尋找前一個結構點 (過去 3 根已收線的最高點)
                swing_high = float(frame["high"].iloc[-4:-1].max())
                # 加上 0.2 ATR 緩衝 (空單防插針往外擴)
                structural_sl = swing_high + (0.2 * atr)
                
                # 止損點只進不退
                new_sl = min(new_sl, structural_sl)
                state["current_sl"] = new_sl
                
            if price >= state.get("current_sl", initial_sl):
                return "TRAILING_STOP_EXIT_V7"
                
    except Exception:
        pass
    return None

def check_state_machine_exit(position: dict, frame: 'pd.DataFrame', price: float) -> str | None:
    """
    狀態機專屬：極端衰竭期平倉 (EXHAUSTION_ZONE ONLY)
    完全使用最新一根「已收線 (Closed)」的 K 棒來判斷，拒絕盤中即時價格的雜訊。
    """
    try:
        if frame is None or len(frame) < 3:
            return None
            
        trade_phase = position.get("trade_phase", "TRENDING")
        if trade_phase != "EXHAUSTION_ZONE":
            return None
            
        side = position.get("side")
        last_closed = frame.iloc[-2] # 最新一根已經收線的 K 棒
        
        atr = float(last_closed.get("atr", price * 0.01))
        kc_upper_closed = float(last_closed.get("kc_upper", price))
        kc_lower_closed = float(last_closed.get("kc_lower", price))
        ma3_closed = float(last_closed.get("ma3", price))
        
        c_open = float(last_closed['open'])
        c_close = float(last_closed['close'])
        
        if side == "LONG":
            back_inside = c_close < kc_upper_closed
            is_bearish = c_close < c_open and (c_open - c_close) > 0.1 * atr
            break_ma3 = c_close < ma3_closed
            
            if back_inside and is_bearish and break_ma3:
                return "PEAK_EXHAUSTION_EXIT_LONG"
                
        elif side == "SHORT":
            back_inside = c_close > kc_lower_closed
            is_bullish = c_close > c_open and (c_close - c_open) > 0.1 * atr
            break_ma3 = c_close > ma3_closed
            
            if back_inside and is_bullish and break_ma3:
                return "PEAK_EXHAUSTION_EXIT_SHORT"
                
    except Exception:
        pass
    return None

def check_reversal_exit(position: dict, frame: 'pd.DataFrame', price: float) -> str | None:
    """
    第三層：趨勢徹底反轉 (CK 彎頭或對向破軌)
    """
    try:
        if frame is None or len(frame) < 3:
            return None
            
        side = position.get("side")
        kc_upper = float(frame.iloc[-1].get("kc_upper", price))
        kc_lower = float(frame.iloc[-1].get("kc_lower", price))
        
        kc_middle_curr = float(frame.iloc[-1].get("kc_middle", price))
        kc_middle_prev = float(frame.iloc[-2].get("kc_middle", price))
        kc_slope = kc_middle_curr - kc_middle_prev
        
        if side == "LONG":
            # 1. 對向破軌：價格跌破下軌
            if price <= kc_lower:
                return "REVERSAL_EXIT_OPPOSITE_RAIL"
            # 2. CK 彎頭：中軌明確向下轉折
            if kc_slope < -0.0001:
                return "REVERSAL_EXIT_CK_TURN_DOWN"
                
        elif side == "SHORT":
            # 1. 對向破軌：價格突破上軌
            if price >= kc_upper:
                return "REVERSAL_EXIT_OPPOSITE_RAIL"
            # 2. CK 彎頭：中軌明確向上轉折
            if kc_slope > 0.0001:
                return "REVERSAL_EXIT_CK_TURN_UP"
                
    except Exception:
        pass
    return None

class DualTrackExitStrategy(IExitStrategy):
    """OOP Strategy class implementing strict holding logic."""

    def __init__(self, fee: float = 0.0005, slippage: float = 0.0005):
        self.fee = fee
        self.slippage = slippage


    def evaluate_exit(
        self,
        position: dict,
        frame: 'pd.DataFrame',
        price: float,
        **kwargs: 'Any'
    ) -> str | None:
        
        # --- 狀態機管理 (State Machine Management) ---
        side = position.get("side")
        trade_phase = position.setdefault("trade_phase", "TRENDING")
        
        if frame is not None and len(frame) >= 1:
            kc_upper = float(frame.iloc[-1].get("kc_upper", price))
            kc_lower = float(frame.iloc[-1].get("kc_lower", price))
            
            if trade_phase == "TRENDING":
                if side == "LONG" and price > kc_upper:
                    position["trade_phase"] = "EXHAUSTION_ZONE"
                elif side == "SHORT" and price < kc_lower:
                    position["trade_phase"] = "EXHAUSTION_ZONE"
        # ---------------------------------------------
        
        # 0. 災難性平倉 (極端防禦檢測，最高優先)
        emergency_reason = check_emergency_exit(position, frame, price)
        if emergency_reason:
            return emergency_reason
            
        # 0.5 目標一：半倉停利 (KC 對稱點)
        partial_tp_reason = check_partial_take_profit(position, frame, price)
        if partial_tp_reason:
            return partial_tp_reason
            
        # 1. 硬性防禦 (保命符)
        hard_stop_reason = check_hard_stop_exit(position, frame, price)
        if hard_stop_reason:
            return hard_stop_reason
            
        # 3. 趨勢反轉 (真正的結構出場點)
        reversal_reason = check_reversal_exit(position, frame, price)
        if reversal_reason:
            return reversal_reason
            
        # 4. 寬幅移動止損 (防大深V洗盤)
        trailing_reason = check_trailing_stop_exit(position, frame, price, self.fee, self.slippage)
        if trailing_reason:
            return trailing_reason
            
        return None
