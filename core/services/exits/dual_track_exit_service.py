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

def check_trailing_stop_exit(position: dict, frame: 'pd.DataFrame', price: float, fee: float = 0.0005, slippage: float = 0.0005) -> str | None:
    """
    第二層：寬幅移動止損 (Trailing Stop)
    目的：鎖定獲利，但給予足夠的寬容度 (1.5 ATR)，避免在趨勢中段被洗出場。
    """
    try:
        if frame is None or len(frame) < 3:
            return None
            
        side = position.get("side")
        entry = float(position.get("entry_price") or 0)
        qty = float(position.get("qty") or 0)
        atr = float(frame.iloc[-2]["atr"])
        
        if not (entry > 0 and qty > 0 and atr > 0):
            return None
            
        sign = 1 if side == "LONG" else -1
        execution = price * (1 - sign * slippage)
        net_profit = sign * (execution - entry) * qty - (entry + execution) * qty * fee
        net_atr = net_profit / (qty * atr)
        
        state = position.setdefault("ratchet_lock_state", {})
        max_net_atr = max(float(state.get("max_net_atr", 0)), net_atr)
        state["max_net_atr"] = max_net_atr
        
        # 只有當獲利超過 1.0 ATR 時，才啟動移動止損
        if max_net_atr > 1.0:
            # 止損線設在最高獲利回撤 1.5 ATR 的位置
            locked_atr = max_net_atr - 1.5
            # 更新鎖利線，只升不降
            current_locked = max(float(state.get("locked_atr", -999)), locked_atr)
            state["locked_atr"] = current_locked
            
            if net_atr <= current_locked:
                return "TRAILING_STOP_EXIT"
                
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
        
        # 1. 硬性防禦 (保命符)
        hard_stop_reason = check_hard_stop_exit(position, frame, price)
        if hard_stop_reason:
            return hard_stop_reason
            
        # 2. 趨勢反轉 (真正的出場點)
        reversal_reason = check_reversal_exit(position, frame, price)
        if reversal_reason:
            return reversal_reason
            
        # 3. 寬幅移動止損 (防大深V洗盤)
        trailing_reason = check_trailing_stop_exit(position, frame, price, self.fee, self.slippage)
        if trailing_reason:
            return trailing_reason
            
        return None
