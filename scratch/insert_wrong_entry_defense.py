import re

with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

new_func = """def check_wrong_entry_defense(position: Dict[str, Any], frame: pd.DataFrame, price: float, velocity_drop_ratio: float = 0.0) -> Optional[str]:
    \"\"\"
    錯倉修正防禦 (Three-Degree Confirmation Defense)
    \"\"\"
    try:
        side = position.get("side")
        entry = float(position.get("entry_price") or 0)
        
        if frame is None or len(frame) < 3 or entry <= 0:
            return None
            
        atr = float(frame.iloc[-2]["atr"])
        if atr <= 0:
            return None
            
        kc_middle = float(frame.iloc[-1].get("kc_middle", price))
        
        # 1. 結構斷裂 (Structural Break)
        if side == "LONG" and price < kc_middle:
            return "MARKET_EXIT_DEFENSE_STRUCTURAL_BREAK"
        elif side == "SHORT" and price > kc_middle:
            return "MARKET_EXIT_DEFENSE_STRUCTURAL_BREAK"
            
        # 2. 動能反轉 (Momentum Reversal)
        last_1 = frame.iloc[-1]
        last_2 = frame.iloc[-2]
        
        body_1 = float(last_1['close']) - float(last_1['open'])
        body_2 = float(last_2['close']) - float(last_2['open'])
        
        if side == "LONG":
            if body_1 < 0 and body_2 < 0:
                if abs(body_1) + abs(body_2) > 0.5 * atr:
                    return "MARKET_EXIT_DEFENSE_MOMENTUM_REVERSAL"
        elif side == "SHORT":
            if body_1 > 0 and body_2 > 0:
                if body_1 + body_2 > 0.5 * atr:
                    return "MARKET_EXIT_DEFENSE_MOMENTUM_REVERSAL"
                    
        # 3. 空間飽和 (Space Satiation)
        dist_from_mid = abs(price - kc_middle)
        is_velocity_peak = (velocity_drop_ratio >= 0.20)
        
        if dist_from_mid > 1.5 * atr and is_velocity_peak:
            # 只有當利潤非常微小甚至為負的時候才算是錯倉防禦 (如果獲利極大，則交由動態鎖利處理)
            # 在這裡，極端乖離但動能消失，我們選擇退出
            return "LIMIT_EXIT_DEFENSE_SPACE_SATIATION"
            
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        pass
    return None

def check_dynamic_trailing_exit"""

content = content.replace("def check_dynamic_trailing_exit", new_func)

injection = """        # 1.5 【第二階：錯倉修正防禦 (三度確認)】
        defense_reason = check_wrong_entry_defense(position, frame, price, kwargs.get("velocity_drop_ratio", 0.0))
        if defense_reason:
            return defense_reason
            
        # 2 & 3."""

content = content.replace("        # 2 & 3.", injection)

with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(content)
