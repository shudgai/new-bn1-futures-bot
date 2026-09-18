import re

with open("core/services/strategies/outer_strategy.py", "r") as f:
    content = f.read()

anti_chase_long = """        # --- V5.1 Anti-Chase Patch (雙向防禦過濾) ---
        row = frame.iloc[-1]
        kc_lower = float(row.get('kc_lower', 0))
        kc_upper = float(row.get('kc_upper', 0))
        kc_middle = float(row.get('kc_middle', row.get('ema_20', 0)))
        atr = float(row.get('atr', 1.0))
        low = float(row.get('low', price))
        high = float(row.get('high', price))
        close = float(row.get('close', price))
        open_price = float(row.get('open', price))
        body = abs(close - open_price)

        if side == "SHORT":
            # 1. 邊界保護: 跌破下軌不追空
            if price < kc_lower:
                return {**wait, "reason": "ANTI_CHASE_OUTSIDE_BANDS"}
            # 2. 乖離限制: 與中軌距離過大不追空
            if atr > 0 and abs(price - kc_middle) / atr > 1.5:
                return {**wait, "reason": "ANTI_CHASE_DISTANCE_LIMIT"}
            # 3. 影線過濾: 長下影線托盤拒絕
            lower_wick = min(open_price, close) - low
            if body > 0 and (lower_wick / body) > 1.2:
                return {**wait, "reason": "ANTI_CHASE_REJECTION_CANDLE"}
                
        elif side == "LONG":
            # 1. 邊界保護: 衝破上軌不追多
            if price >= kc_upper:
                return {**wait, "reason": "ANTI_CHASE_OUTSIDE_BANDS"}
            # 2. 乖離限制: 與中軌距離過大不追多
            if atr > 0 and abs(price - kc_middle) / atr > 1.5:
                return {**wait, "reason": "ANTI_CHASE_DISTANCE_LIMIT"}
            # 3. 趨勢對齊: 檢查長週期均線 (EMA50) 斜率
            if len(frame) > 2:
                ema50_curr = float(frame.iloc[-1].get('ema_50', 0))
                ema50_prev = float(frame.iloc[-2].get('ema_50', 0))
                if ema50_curr > 0 and ema50_prev > 0 and (ema50_curr - ema50_prev) < 0:
                    return {**wait, "reason": "ANTI_CHASE_TREND_MISALIGNMENT"}
            # 4. 過度延伸: 連續 3 根實體陽線且乖離過大
            if len(frame) >= 3:
                is_consecutive_green = all(float(frame.iloc[-i]['close']) > float(frame.iloc[-i]['open']) for i in range(1, 4))
                if is_consecutive_green and atr > 0 and abs(price - kc_middle) / atr > 1.5:
                    return {**wait, "reason": "ANTI_CHASE_OVEREXTENDED"}
        # ----------------------------------------
"""

# The script replaces the existing short-only patch with the bi-directional patch.
content = re.sub(
    r'# --- V5\.1 Anti-Chase Patch.*?# ----------------------------------------\n',
    anti_chase_long,
    content,
    flags=re.DOTALL
)

with open("core/services/strategies/outer_strategy.py", "w") as f:
    f.write(content)
print("Updated outer_strategy.py for Bi-Directional Anti-Chase")
