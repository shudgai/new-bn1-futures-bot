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
        
        # --- 峰谷三點平倉邏輯 ---
        if "is_high_risk_entry" not in position:
            entry_price = float(position.get("entry_price", current_price))
            dist_at_entry = abs(entry_price - float(current_bar.get("kc_middle", entry_price)))
            position["is_high_risk_entry"] = dist_at_entry > (1.5 * atr)
            
        if position.get("is_high_risk_entry", False):
            if side == "LONG" and (current_price < opened) and (body_size > 0.2 * atr):
                return "EXIT_PEAK_VALLEY_RAPID_REVERSAL"
            if side == "SHORT" and (current_price > opened) and (body_size > 0.2 * atr):
                return "EXIT_PEAK_VALLEY_RAPID_REVERSAL"
        
        if side == "LONG":
            # (A) 大瀑布檢測
            if (current_price < opened) and (body_size >= 1.8 * atr):
                return "EMERGENCY_EXIT_FLASH_CRASH"
            # (B) 連續兩根異常 K 棒
            if (current_price < opened) and (prev_close < prev_opened):
                if (body_size >= 1.2 * atr) and (prev_body >= 1.2 * atr):
                    return "EMERGENCY_EXIT_TWO_ANOMALY_BARS"
                    
        elif side == "SHORT":
            # (A) 大瀑布檢測
            if (current_price > opened) and (body_size >= 1.8 * atr):
                return "EMERGENCY_EXIT_FLASH_CRASH"
            # (B) 連續兩根異常 K 棒
            if (current_price > opened) and (prev_close > prev_opened):
                if (body_size >= 1.2 * atr) and (prev_body >= 1.2 * atr):
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


def check_ratchet_lock_exit(position: Dict[str, Any], frame: pd.DataFrame, price: float, fee: float = 0.0005, slippage: float = 0.0005) -> Optional[str]:
    """
    棘輪鎖利機制：0.35 ATR 起步，每 0.20 ATR 墊高一階。
    """
    try:
        if frame is None or len(frame) < 2:
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
        
        START_THRESHOLD = 0.55
        
        max_net_atr = max(float(state.get("max_net_atr", 0)), net_atr)
        state["max_net_atr"] = max_net_atr
        
        if max_net_atr >= START_THRESHOLD:
            # 2. 動態決定回吐空間 (利潤越高，空間越大)
            if max_net_atr < 1.0:
                ratchet_step = 0.25
            elif max_net_atr < 2.0:
                ratchet_step = 0.35
            else:
                ratchet_step = 0.45
                
            # 3. 計算鎖利線並確保只升不降
            new_locked_atr = max_net_atr - ratchet_step
            locked_atr = max(float(state.get("locked_atr", 0)), new_locked_atr)
            state["locked_atr"] = locked_atr
            
            # 4. 執行平倉
            if locked_atr > 0 and net_atr <= locked_atr:
                return "RATCHET_PROFIT_LOCK_EXIT"
                
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
        
        # 1. 【最高優先級：極端熔斷】 (不論盈虧，保命第一)
        emergency_reason = check_emergency_exit(position, frame, price)
        if emergency_reason:
            return emergency_reason
            
        # 2. 【次高優先級：動能反轉逃命】 (沒利潤時立刻跑)
        structure_reason = check_structure_exit(position, frame, price)
        if structure_reason:
            if max_net_atr < 0.55:
                return "EXIT_EARLY_REVERSAL_NO_PROFIT"
                
        # 3. 【正常級：棘輪鎖利】 (有利潤時鎖利平倉)
        if max_net_atr >= 0.55:
            ratchet_reason = check_ratchet_lock_exit(position, frame, price, self.fee, self.slippage)
            if ratchet_reason:
                return ratchet_reason
            
        return None
