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
    極端防禦檢測 (最高優先級)：
    1. 大瀑布 (Flash Crash)：單根反向實體 >= 1.8 ATR
    2. 連續異常 (Dual Anomaly)：連續兩根反向實體 >= 1.2 ATR
    3. 市場熔斷標記
    """
    if position.get("emergency_circuit_breaker"):
        return "EMERGENCY_EXIT_CIRCUIT_BREAKER"
        
    try:
        if frame is None or len(frame) < 3:
            return None
            
        side = position.get("side")
        if side not in ("LONG", "SHORT"):
            return None
            
        current_bar = frame.iloc[-1]
        prev_bar = frame.iloc[-2]
        atr = float(frame.iloc[-3]["atr"])
        
        opened = float(current_bar["open"])
        current_price = price
        body_size = abs(current_price - opened)
        
        prev_opened = float(prev_bar["open"])
        prev_close = float(prev_bar["close"])
        prev_body = abs(prev_close - prev_opened)
        
        # --- V5.0 動能衰減防禦 (Anti-Early Reversal) ---
        from core.config import ANTI_REVERSAL_ATR_MULT, FLASH_CRASH_ATR_MULT
        
        # 判断是否為外軌外進場
        if "is_outer_entry" not in position:
            entry_reason = position.get("reason", "")
            position["is_outer_entry"] = "OUTER" in entry_reason or "BREAKOUT" in entry_reason
            
        if position.get("is_outer_entry", False):
            # 若 MA3/價格反向轉彎超過 ANTI_REVERSAL_ATR_MULT 且無實體推動
            if side == "LONG" and (current_price < opened) and (body_size > ANTI_REVERSAL_ATR_MULT * atr):
                return "EXIT_EARLY_REVERSAL_NO_PROFIT"
            if side == "SHORT" and (current_price > opened) and (body_size > ANTI_REVERSAL_ATR_MULT * atr):
                return "EXIT_EARLY_REVERSAL_NO_PROFIT"
        
        if side == "LONG":
            # (A) 大瀑布檢測 (V5.0: 1.5 ATR)
            if (current_price < opened) and (body_size >= FLASH_CRASH_ATR_MULT * atr):
                return "EMERGENCY_EXIT_FLASH_CRASH"
            # (B) 連續兩根異常 K 棒 (V5.0: 0.5 ATR)
            if (current_price < opened) and (prev_close < prev_opened):
                if (body_size >= 0.5 * atr) and (prev_body >= 0.5 * atr):
                    return "EMERGENCY_EXIT_TWO_ANOMALY_BARS"
                    
        elif side == "SHORT":
            # (A) 大瀑布檢測 (V5.0: 1.5 ATR)
            if (current_price > opened) and (body_size >= FLASH_CRASH_ATR_MULT * atr):
                return "EMERGENCY_EXIT_FLASH_CRASH"
            # (B) 連續兩根異常 K 棒 (V5.0: 0.5 ATR)
            if (current_price > opened) and (prev_close > prev_opened):
                if (body_size >= 0.5 * atr) and (prev_body >= 0.5 * atr):
                    return "EMERGENCY_EXIT_TWO_ANOMALY_BARS"
                    
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
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


def check_hard_stop_exit(position: Dict[str, Any], frame: pd.DataFrame, price: float) -> Optional[str]:
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
                
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        pass
    return None


def check_dynamic_trailing_exit(position: Dict[str, Any], frame: pd.DataFrame, price: float, fee: float = 0.0005, slippage: float = 0.0005, velocity_slowdown: bool = False) -> Optional[str]:
    """
    第二層與第三層：智能動態空間 & 極致動能退出
    """
    try:
        if frame is None or len(frame) < 3:
            return None
            
        side = position.get("side")
        entry = float(position.get("entry_price") or 0)
        qty = float(position.get("qty") or 0)
        atr = float(frame.iloc[-2]["atr"])
        
        if not all(math.isfinite(x) and x > 0 for x in (entry, qty, price, atr)):
            return None
            
        sign = 1 if side == "LONG" else -1
        execution = price * (1 - sign * slippage)
        net_profit = sign * (execution - entry) * qty - (entry + execution) * qty * fee
        net_atr = net_profit / (qty * atr) if qty > 0 and atr > 0 else 0
        
        state = position.setdefault("ratchet_lock_state", {})
        
        # 摩擦損耗防禦：計算動態鎖利門檻
        position_value = entry * qty
        fee_cost = position_value * fee * 2
        slippage_cost = qty * price * slippage
        total_friction_cost = fee_cost + slippage_cost
        friction_atr = total_friction_cost / (qty * atr) if qty > 0 and atr > 0 else 0
        START_THRESHOLD = max(0.55, friction_atr)
        
        max_net_atr = max(float(state.get("max_net_atr", 0)), net_atr)
        state["max_net_atr"] = max_net_atr
        
        if max_net_atr >= START_THRESHOLD:
            # 取得 MA3 計算斜率係數
            ma3_curr = float(frame.iloc[-1].get("ma3", 0))
            ma3_prev = float(frame.iloc[-2].get("ma3", 0))
            
            from core.config import SLOPE_FACTOR_RANGE
            slope_factor = 1.0
            if ma3_curr > 0 and ma3_prev > 0:
                ma3_slope = abs(ma3_curr - ma3_prev) / ma3_prev
                if ma3_slope > 0.005:  # 強勢
                    slope_factor = SLOPE_FACTOR_RANGE[0] # 0.5
                elif ma3_slope < 0.001:  # 弱勢
                    slope_factor = SLOPE_FACTOR_RANGE[1] # 1.5
                    
            # 檢查極致動能標記 (Velocity Slowdown >= 30%)
            is_velocity_peak = velocity_slowdown
            
            # --- 峰值平倉分批 (Partial Exit) ---
            # 偵測到滯漲 (長影線或實體極小) 且處於目標區
            curr_bar = frame.iloc[-1]
            c_open, c_close, c_low, c_high = float(curr_bar['open']), float(curr_bar['close']), float(curr_bar['low']), float(curr_bar['high'])
            c_body = abs(c_close - c_open)
            wick = min(c_open, c_close) - c_low if side == "LONG" else c_high - max(c_open, c_close)
            is_stagnant = (c_body > 0 and (wick / c_body) > 1.5) or (c_body < 0.2 * atr)
            
            if is_velocity_peak and is_stagnant:
                if not state.get("partial_exit_triggered"):
                    state["partial_exit_triggered"] = True
                    return "LIMIT_EXIT_PEAK_MOMENTUM_PARTIAL"
            
            # --- 動態空間鎖利引擎 (Adaptive Profit Engine) ---
            from core.config import BASE_DRAWDOWN, SAFETY_BUFFER
            
            # 1. 基礎空間計算
            current_space = (BASE_DRAWDOWN * slope_factor) + SAFETY_BUFFER
            
            # 2. 動態壓縮 (若動能放緩，空間瞬間壓縮 75%)
            if is_velocity_peak:
                current_space = current_space * 0.25
                
            # 計算鎖利線並確保只升不降
            new_locked_atr = max_net_atr - current_space
            locked_atr = max(float(state.get("locked_atr", -999)), new_locked_atr)
            state["locked_atr"] = locked_atr
            
            # 執行平倉判斷
            if net_atr <= locked_atr:
                if is_velocity_peak:
                    return "LIMIT_EXIT_ADAPTIVE_VELOCITY_PEAK"
                else:
                    return "LIMIT_EXIT_ADAPTIVE_TRAILING"
                    
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        pass
    return None


class DualTrackExitStrategy(IExitStrategy):
    """OOP Strategy class implementing IExitStrategy for dual track exit evaluation."""

    def __init__(self, fee: float = 0.0005, slippage: float = 0.0005):
        self.fee = fee
        self.slippage = slippage

    def evaluate_exit(
        self,
        position: Dict[str, Any],
        frame: pd.DataFrame,
        price: float,
        **kwargs: Any
    ) -> Optional[str]:
        
        # 0. 提前計算目前利潤狀態 (供防禦分級判斷)
        max_net_atr = 0.0
        try:
            side = position.get("side")
            entry = float(position.get("entry_price") or 0)
            qty = float(position.get("qty") or 0)
            atr = float(frame.iloc[-2]["atr"]) if len(frame) >= 2 else 0.0
            
            if entry > 0 and qty > 0 and atr > 0:
                sign = 1 if side == "LONG" else -1
                execution = price * (1 - sign * self.slippage)
                net_profit = sign * (execution - entry) * qty - (entry + execution) * qty * self.fee
                net_atr = net_profit / (qty * atr)
                
                state = position.setdefault("ratchet_lock_state", {})
                max_net_atr = max(float(state.get("max_net_atr", 0)), net_atr)
                state["max_net_atr"] = max_net_atr
        except Exception:
            pass
        
        # 1. 【第一階：硬性防禦 (保命符)】
        hard_stop_reason = check_hard_stop_exit(position, frame, price)
        if hard_stop_reason:
            return hard_stop_reason
            
        # 2 & 3. 【第二階與第三階：極致動能收網 & 動態空間鎖利 (保獲利/搶高點)】
        velocity_slowdown = kwargs.get("velocity_slowdown", False)
        dynamic_reason = check_dynamic_trailing_exit(position, frame, price, self.fee, self.slippage, velocity_slowdown)
        if dynamic_reason:
            return dynamic_reason
            
        # 4. 【第四階：緊急防禦 (閃崩/崩潰 避災)】
        emergency_reason = check_emergency_exit(position, frame, price)
        if emergency_reason:
            return emergency_reason
            
        # 5. 【舊版結構防禦 (備用)】
        structure_reason = check_structure_exit(position, frame, price)
        if structure_reason:
            if max_net_atr < 0.55:
                return "EXIT_EARLY_REVERSAL_NO_PROFIT"
                
        return None
