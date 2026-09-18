import re

with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

new_logic = '''def check_state_machine_exit(position: dict, frame: 'pd.DataFrame', price: float) -> str | None:
    """
    狀態機專屬：極端衰竭期平倉 (EXHAUSTION_ZONE ONLY)
    完全使用最新一根「已收線 (Closed)」的 K 棒來判斷，拒絕盤中即時價格的雜訊。
    """
    try:
        if frame is None or len(frame) < 3:
            return None
            
        trade_phase = position.get("trade_phase", "TRENDING")
        if trade_phase != "EXHAUSTION_ZONE":
            return None
            
        side = position.get("side")
        last_closed = frame.iloc[-2] # 最新一根已經收線的 K 棒
        
        atr = float(last_closed.get("atr", price * 0.01))
        kc_upper_closed = float(last_closed.get("kc_upper", price))
        kc_lower_closed = float(last_closed.get("kc_lower", price))
        ma3_closed = float(last_closed.get("ma3", price))
        
        c_open = float(last_closed['open'])
        c_close = float(last_closed['close'])
        
        if side == "LONG":
            back_inside = c_close < kc_upper_closed
            is_bearish = c_close < c_open and (c_open - c_close) > 0.1 * atr
            break_ma3 = c_close < ma3_closed
            
            if back_inside and is_bearish and break_ma3:
                return "PEAK_EXHAUSTION_EXIT_LONG"
                
        elif side == "SHORT":
            back_inside = c_close > kc_lower_closed
            is_bullish = c_close > c_open and (c_close - c_open) > 0.1 * atr
            break_ma3 = c_close > ma3_closed
            
            if back_inside and is_bullish and break_ma3:
                return "PEAK_EXHAUSTION_EXIT_SHORT"
                
    except Exception:
        pass
    return None'''

# Replace the check_state_machine_exit function
match = re.search(r'def check_state_machine_exit.*?return None', content, re.DOTALL)
if match:
    content = content[:match.start()] + new_logic + content[match.end():]
else:
    print("Could not find check_state_machine_exit method")

with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(content)
