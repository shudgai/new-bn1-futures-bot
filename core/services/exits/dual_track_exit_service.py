import pandas as pd
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# V10 狀態追蹤鍵列，持久化結構追蹤狀態
# v10_phase_trailing 取代 v10_ladder：改用 KC 三階段移動止損
DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close"]

class DualTrackExitStrategy:
    def __init__(self, account=None, fee: float = 0.0004, slippage: float = 0.0005):
        self.account = account
        self.fee = fee
        self.slippage = slippage

    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame, price: float, velocity_drop_ratio: float = 0.0, **kwargs) -> Optional[str]:
        if frame is None or len(frame) < 3:
            return None
            
        side = position.get("side")
        if not side:
            return None

        # 唯一平倉邏輯：純機械式 動態防禦 (0.5 ATR) + 階梯限價鎖利 (1.0 ATR)
        ladder_reason = check_atr_step_trailing_stop(
            position, frame, price
        )
        if ladder_reason:
            return ladder_reason

        return None
            



def check_atr_step_trailing_stop(
    position: dict,
    frame: pd.DataFrame,
    price: float,
) -> Optional[str]:
    """
    動態防禦 (0.5 ATR) + 階梯限價鎖利 (1.0 ATR) 獨立掛單架構
    """
    try:
        import math
        side = position.get("side")
        entry_price = float(position.get("entry_price") or 0)
        
        if not side or entry_price <= 0 or frame is None or len(frame) == 0:
            return None
            
        # 取得過去 20 根 K 棒的 ATR 均值，確保剛開倉時的防禦線不會因為單根 K 棒異常波動而設得太近
        if len(frame) >= 20:
            atr = float(frame["atr"].tail(20).mean())
        else:
            atr = float(frame.iloc[-1].get("atr", 0))
            
        if atr <= 0:
            return None
            
        state = position.setdefault("v10_phase_trailing", {})
        
        # 1. 處理 0.5 ATR 初始防禦線 (永遠不變)
        if "defense_line" not in state:
            if side == "LONG":
                defense = entry_price - 0.5 * atr
            else:
                defense = entry_price + 0.5 * atr
            state["defense_line"] = defense
            symbol = position.get("symbol", "UNKNOWN")
            logger.info(f"[Protection] {symbol} 已於 {defense:.6g} 掛出 0.5 ATR 初始防禦線保護單")
            
            # 特例 K 進場：立即在進場瞬間掛出第一張 1.0 ATR 鎖利單
            v8_reason = position.get("v8_reason", "")
            if v8_reason and v8_reason.startswith("SPECIAL_ENTRY_MOMENTUM_"):
                if side == "LONG":
                    initial_lock = entry_price - 1.0 * atr
                else:
                    initial_lock = entry_price + 1.0 * atr
                state["profit_lock_line"] = initial_lock
                state["atr_step"] = 1  # 標記已進入第一階梯，後續繼續正常推進
                logger.info(f"⚡ [Special Entry] Extreme Momentum - Initial Lock Order Placed at {initial_lock:.6g} ({symbol} {side})")

        defense_line = state["defense_line"]
        
        # 2. 計算目前推移距離 (用來推進鎖利單)
        if side == "LONG":
            distance = price - entry_price
        else:
            distance = entry_price - price
            
        highest_dist = state.get("highest_distance", 0)
        if distance > highest_dist:
            highest_dist = distance
            state["highest_distance"] = highest_dist
            
        current_step = state.get("atr_step", 0)
        target_step = 0
        if highest_dist >= 1.0 * atr:
            target_step = math.floor(highest_dist / atr)
            
        # 3. 如果到達新階梯，撤銷舊單並掛出新單
        if target_step > current_step:
            state["atr_step"] = target_step
            if side == "LONG":
                new_lock = entry_price + (target_step - 1) * atr
            else:
                new_lock = entry_price - (target_step - 1) * atr
            
            # 若已有舊鎖利單，確保方向正確 (不會往下退)
            old_lock = state.get("profit_lock_line")
            if old_lock is not None:
                if side == "LONG":
                    new_lock = max(old_lock, new_lock)
                else:
                    new_lock = min(old_lock, new_lock)
                    
            state["profit_lock_line"] = new_lock
            symbol = position.get("symbol", "UNKNOWN")
            logger.info(f"[Take Profit Lock] {symbol} 撤銷上一張鎖利掛單，重新掛出新的鎖利單 (Limit Order) 於: {new_lock:.6g}")

        # 4. 判定是否觸發平倉 (先檢查鎖利單，再檢查防禦線)
        profit_lock = state.get("profit_lock_line")
        
        if side == "LONG":
            if profit_lock is not None and price <= profit_lock:
                return "EXIT_1.0_ATR_PROFIT_LOCK"
            if price <= defense_line:
                return "EXIT_0.5_ATR_DEFENSE"
        else:
            if profit_lock is not None and price >= profit_lock:
                return "EXIT_1.0_ATR_PROFIT_LOCK"
            if price >= defense_line:
                return "EXIT_0.5_ATR_DEFENSE"
                
    except Exception as e:
        logger.error(f"Error in check_atr_step_trailing_stop: {e}")
    return None
