import sys
import os

with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

import re

# We will replace check_ratchet_lock_exit with the new V5 logic
new_ratchet = """def check_dynamic_trailing_exit(position: Dict[str, Any], frame: pd.DataFrame, price: float, fee: float = 0.0005, slippage: float = 0.0005) -> Optional[str]:
    \"\"\"
    V5.0 動態移動鎖利與回吐空間：
    Total_Drawdown = (0.2 ATR * 斜率係數) + 0.1 ATR 緩衝區
    \"\"\"
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
            # 取得 MA3 與 MA15 計算斜率係數
            ma3_curr = float(frame.iloc[-1].get("ma3", 0))
            ma3_prev = float(frame.iloc[-2].get("ma3", 0))
            ma15_curr = float(frame.iloc[-1].get("ma15", 0))
            ma15_prev = float(frame.iloc[-2].get("ma15", 0))
            
            # 斜率係數計算 (簡化版：若 MA3 斜率極強則減小步長，弱則放大)
            # 這裡預設係數為 1.0，強勢 < 1.0，弱勢 > 1.0
            slope_factor = 1.0
            if ma3_curr > 0 and ma3_prev > 0:
                ma3_slope = abs(ma3_curr - ma3_prev) / ma3_prev
                if ma3_slope > 0.005:  # 強勢
                    slope_factor = 0.5
                elif ma3_slope < 0.001:  # 弱勢
                    slope_factor = 1.5
                    
            ratchet_step = (0.2 * slope_factor) + 0.1
            
            # 計算鎖利線並確保只升不降
            new_locked_atr = max_net_atr - ratchet_step
            locked_atr = max(float(state.get("locked_atr", 0)), new_locked_atr)
            state["locked_atr"] = locked_atr
            
            # 終極動能退出 (若外層傳入 velocity 降速標記)
            if position.get("velocity_slowdown", False):
                return "EXIT_VELOCITY_SLOWDOWN"
            
            # 執行平倉
            if locked_atr > 0 and net_atr <= locked_atr:
                return "RATCHET_PROFIT_LOCK_EXIT"
                
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        pass
    return None"""

content = re.sub(
    r'def check_ratchet_lock_exit.*?return None',
    new_ratchet,
    content,
    flags=re.DOTALL
)

# Also update the call site in DualTrackExitStrategy
content = content.replace(
    'ratchet_reason = check_ratchet_lock_exit(position, frame, price, self.fee, self.slippage)',
    'ratchet_reason = check_dynamic_trailing_exit(position, frame, price, self.fee, self.slippage)'
)

with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(content)
print("Updated dual_track_exit_service.py")
