import re

with open("core/services/strategies/outer_strategy.py", "r") as f:
    content = f.read()

anti_chase_logic = """        if not aligned_direction(frame, side):
            return wait
            
        # --- V5.1 Anti-Chase Patch (防追空過濾) ---
        if side == "SHORT":
            row = frame.iloc[-1]
            kc_lower = float(row.get('kc_lower', 0))
            kc_middle = float(row.get('kc_middle', row.get('ema_20', 0)))
            atr = float(row.get('atr', 1.0))
            
            # 1. 邊界保護: 跌破下軌不追空
            if price < kc_lower:
                return {**wait, "reason": "ANTI_CHASE_OUTSIDE_BANDS"}
                
            # 2. 乖離限制: 與中軌距離過大不追空
            if atr > 0 and abs(price - kc_middle) / atr > 1.5:
                return {**wait, "reason": "ANTI_CHASE_DISTANCE_LIMIT"}
                
            # 3. 影線過濾: 長下影線托盤拒絕
            low = float(row.get('low', price))
            close = float(row.get('close', price))
            open_price = float(row.get('open', price))
            body = abs(close - open_price)
            lower_wick = min(open_price, close) - low
            if body > 0 and (lower_wick / body) > 1.2:
                return {**wait, "reason": "ANTI_CHASE_REJECTION_CANDLE"}
        # ----------------------------------------
"""

content = content.replace("        if not aligned_direction(frame, side):\n            return wait\n", anti_chase_logic)

with open("core/services/strategies/outer_strategy.py", "w") as f:
    f.write(content)
print("Updated outer_strategy.py for Anti-Chase")
