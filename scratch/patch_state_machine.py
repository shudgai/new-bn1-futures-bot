import re

with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

new_logic = '''def check_state_machine_exit(position: dict, frame: 'pd.DataFrame', price: float) -> str | None:
    """
    狀態機專屬：極端衰竭期平倉 (EXHAUSTION_ZONE ONLY)
    """
    try:
        if frame is None or len(frame) < 3:
            return None
            
        trade_phase = position.get("trade_phase", "TRENDING")
        if trade_phase != "EXHAUSTION_ZONE":
            return None
            
        side = position.get("side")
        last_closed = frame.iloc[-2] # 最新一根已經收線的 K 棒
        curr_live = frame.iloc[-1]   # 即時 K 棒
        
        atr = float(last_closed.get("atr", price * 0.01))
        kc_upper = float(curr_live.get("kc_upper", price))
        kc_lower = float(curr_live.get("kc_lower", price))
        ma3 = float(last_closed.get("ma3", price))
        
        c_open = float(last_closed['open'])
        c_close = float(last_closed['close'])
        c_high = float(last_closed['high'])
        c_low = float(last_closed['low'])
        
        # 1. 跌回軌道內
        # 2. 實體反向 (LONG要陰線, SHORT要陽線) 且有一定實體大小 (例如大於 0.2 ATR，避免十字星假摔)
        # 3. 破 MA3
        if side == "LONG":
            back_inside = price < kc_upper and c_close < kc_upper
            is_bearish = c_close < c_open and (c_open - c_close) > 0.1 * atr
            break_ma3 = price < ma3
            
            if back_inside and is_bearish and break_ma3:
                return "PEAK_EXHAUSTION_EXIT_LONG"
                
        elif side == "SHORT":
            back_inside = price > kc_lower and c_close > kc_lower
            is_bullish = c_close > c_open and (c_close - c_open) > 0.1 * atr
            break_ma3 = price > ma3
            
            if back_inside and is_bullish and break_ma3:
                return "PEAK_EXHAUSTION_EXIT_SHORT"
                
    except Exception:
        pass
    return None

def check_reversal_exit'''

content = content.replace("def check_reversal_exit", new_logic)

# Replace evaluate_exit method logic
eval_logic = '''
    def evaluate_exit(
        self,
        position: dict,
        frame: 'pd.DataFrame',
        price: float,
        **kwargs: 'Any'
    ) -> str | None:
        
        # --- 狀態機管理 (State Machine Management) ---
        side = position.get("side")
        trade_phase = position.setdefault("trade_phase", "TRENDING")
        
        if frame is not None and len(frame) >= 1:
            kc_upper = float(frame.iloc[-1].get("kc_upper", price))
            kc_lower = float(frame.iloc[-1].get("kc_lower", price))
            
            if trade_phase == "TRENDING":
                if side == "LONG" and price > kc_upper:
                    position["trade_phase"] = "EXHAUSTION_ZONE"
                elif side == "SHORT" and price < kc_lower:
                    position["trade_phase"] = "EXHAUSTION_ZONE"
        # ---------------------------------------------
        
        # 1. 硬性防禦 (保命符)
        hard_stop_reason = check_hard_stop_exit(position, frame, price)
        if hard_stop_reason:
            return hard_stop_reason
            
        # 2. 狀態機專屬衰竭平倉 (僅 EXHAUSTION_ZONE 觸發)
        exhaustion_reason = check_state_machine_exit(position, frame, price)
        if exhaustion_reason:
            return exhaustion_reason
            
        # 3. 趨勢反轉 (真正的結構出場點)
        reversal_reason = check_reversal_exit(position, frame, price)
        if reversal_reason:
            return reversal_reason
            
        # 4. 寬幅移動止損 (防大深V洗盤)
        trailing_reason = check_trailing_stop_exit(position, frame, price, self.fee, self.slippage)
        if trailing_reason:
            return trailing_reason
            
        return None
'''

# Find the evaluate_exit method and replace it
match = re.search(r'    def evaluate_exit\(.*?(?=def |\Z)', content, re.DOTALL)
if match:
    content = content[:match.start()] + eval_logic + content[match.end():]
else:
    print("Could not find evaluate_exit method")

with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(content)
