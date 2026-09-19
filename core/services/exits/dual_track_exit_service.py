import pandas as pd
from typing import Dict, Any, Optional
import logging
import math

logger = logging.getLogger(__name__)

# V10 狀態追蹤鍵列，持久化結構追蹤狀態
DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close"]

class DualTrackExitStrategy:
    def __init__(self, fee: float = 0.0004, slippage: float = 0.0005):
        self.fee = fee
        self.slippage = slippage

    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame, price: float, velocity_drop_ratio: float = 0.0, **kwargs) -> Optional[str]:
        if frame is None or len(frame) < 3:
            return None
            
        side = position.get("side")
        if not side:
            return None

        # 如果機器人正在開倉中，暫停所有平倉判定！
        if position.get("is_opening") is True or position.get("orders_pending") is True:
            return None

        # 極速反噬防禦 (Emergency Escape): 最高優先級
        curr = frame.iloc[-1]
        prev = frame.iloc[-2]
        
        # 使用開倉時的 ATR 或是最新收盤的 ATR
        atr = position.get("entry_atr")
        if not atr:
            atr = float(curr.get("atr", 0))
            if atr <= 0:
                atr = float(prev.get("atr", 0))
                
        if atr <= 0:
            return None
            
        curr_open = float(curr["open"])
        curr_close = float(curr["close"])
        prev_open = float(prev["open"])
        prev_close = float(prev["close"])
        
        curr_body = abs(curr_close - curr_open)
        
        is_curr_red = curr_close < curr_open
        is_curr_green = curr_close > curr_open
        is_prev_red = prev_close < prev_open
        is_prev_green = prev_close > prev_open
        
        # 條件 A: 單根 K 棒反向幅度超過 1.5 ATR
        # 條件 B: 實體吞沒形態 (Engulfing Reversal)
        if side == "LONG":
            engulfing = is_curr_red and (curr_open >= prev_close) and (curr_close < prev_open)
            crash = is_curr_red and (curr_body > 1.5 * atr)
            if engulfing or crash:
                logger.warning(f"[Emergency Escape] {position.get('symbol')} 觸發極速反噬防禦 (LONG)！市價平倉！")
                return "EXIT_MOMENTUM_REVERSAL_DEFENSE"
        elif side == "SHORT":
            engulfing = is_curr_green and (curr_open <= prev_close) and (curr_close > prev_open)
            surge = is_curr_green and (curr_body > 1.5 * atr)
            if engulfing or surge:
                logger.warning(f"[Emergency Escape] {position.get('symbol')} 觸發極速反噬防禦 (SHORT)！市價平倉！")
                return "EXIT_MOMENTUM_REVERSAL_DEFENSE"


        # 唯一平倉邏輯：純機械式 動態防禦 (1.5 ATR) + 階梯限價鎖利 (0.7 ATR)
        ladder_reason = check_atr_step_trailing_stop(position, frame, price)
        if ladder_reason:
            return ladder_reason

        return None

    def handle_post_exit_cleanup(self, position: Dict[str, Any], exit_reason: str):
        """
        執行平倉後狀態清理機制
        """
        symbol = position.get("symbol", "UNKNOWN")
        logger.info(f"[Post-Exit Cleanup] 啟動平倉後狀態清理，原因: {exit_reason} (幣種: {symbol})")
        
        if "v10_phase_trailing" in position:
            position.pop("v10_phase_trailing")
            
        # 狀態重置與冷靜期
        if exit_reason in ("EXIT_1.5_ATR_DEFENSE", "EXIT_MOMENTUM_REVERSAL_DEFENSE"):
            position["cooldown_mode"] = "WAIT_FOR_STABLE_KC"
            position["cooldown_kc_count"] = 2
        elif exit_reason == "EXIT_0.7_ATR_PROFIT_LOCK":
            position["cooldown_mode"] = "NONE"
            
        position["force_space_reevaluation"] = True


def check_atr_step_trailing_stop(
    position: dict,
    frame: pd.DataFrame,
    price: float
) -> Optional[str]:
    """
    動態防禦 (1.5 ATR) + 階梯限價鎖利 (0.7 ATR 推進)
    """
    try:
        side = position.get("side")
        entry_price = float(position.get("entry_price") or 0)
        
        if not side or entry_price <= 0 or frame is None or len(frame) == 0:
            return None
            
        atr = position.get("entry_atr")
        if not atr:
            atr = float(frame.iloc[-1].get("atr", 0))
            if atr <= 0:
                if len(frame) >= 2:
                    atr = float(frame.iloc[-2].get("atr", 0))
                    
        if atr <= 0:
            return None
            
        state = position.setdefault("v10_phase_trailing", {})
        
        # 1. 處理 1.5 ATR 初始防禦線
        if "defense_line" not in state:
            v8_reason = position.get("v8_reason", position.get("reason", ""))
            
            if side == "LONG":
                defense = entry_price - 1.5 * atr
                if "[STANDARD_ENTRY] Structural Reversal LONG" in v8_reason:
                    prev_low = float(frame.iloc[-2]['low'])
                    defense = min(defense, prev_low)
            else:
                defense = entry_price + 1.5 * atr
                if "[STANDARD_ENTRY] Structural Reversal SHORT" in v8_reason:
                    prev_high = float(frame.iloc[-2]['high'])
                    defense = max(defense, prev_high)
                
            state["defense_line"] = defense
            state["active_stop_price"] = defense
            state["last_locked_level"] = 0
            
            # 特例 K 進場：立即在進場瞬間推進一檔
            if v8_reason and ("[SPECIAL_ENTRY] Extreme Impulse" in v8_reason):
                state["last_locked_level"] = 1
                if side == "LONG":
                    state["active_stop_price"] = entry_price
                else:
                    state["active_stop_price"] = entry_price

        # 3. 階梯鎖利推進邏輯 (每推進 0.7 ATR，移動一次保護線)
        last_locked_level = state.get("last_locked_level", 0)
        active_stop_price = state.get("active_stop_price", state["defense_line"])
        step_size_atr = 0.7
        
        if side == "LONG":
            profit_distance = price - entry_price
            current_level = int(profit_distance // (step_size_atr * atr))
            if current_level > last_locked_level:
                state["last_locked_level"] = current_level
                new_stop = entry_price + ((current_level - 1) * step_size_atr * atr)
                state["active_stop_price"] = max(active_stop_price, new_stop)

        elif side == "SHORT":
            profit_distance = entry_price - price
            current_level = int(profit_distance // (step_size_atr * atr))
            if current_level > last_locked_level:
                state["last_locked_level"] = current_level
                new_stop = entry_price - ((current_level - 1) * step_size_atr * atr)
                state["active_stop_price"] = min(active_stop_price, new_stop)

        active_stop_price = state["active_stop_price"]
        
        # 4. 判定是否觸發平倉
        if side == "LONG" and price <= active_stop_price:
            tag = "EXIT_0.7_ATR_PROFIT_LOCK" if state["last_locked_level"] > 0 else "EXIT_1.5_ATR_DEFENSE"
            return tag
        elif side == "SHORT" and price >= active_stop_price:
            tag = "EXIT_0.7_ATR_PROFIT_LOCK" if state["last_locked_level"] > 0 else "EXIT_1.5_ATR_DEFENSE"
            return tag
                
    except Exception as e:
        logger.error(f"Error in check_atr_step_trailing_stop: {e}")
    return None
