import re

with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

# Replace the entire file content from check_hard_stop_exit downwards
match = re.search(r'def check_hard_stop_exit', content)
if match:
    header = content[:match.start()]
    
    new_logic = '''def check_hard_stop_exit(position: dict, frame: 'pd.DataFrame', price: float) -> str | None:
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
'''
    
    with open("core/services/exits/dual_track_exit_service.py", "w") as f:
        f.write(header + new_logic)
