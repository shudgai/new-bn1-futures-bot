import re

file_path = "core/services/strategies/outer_strategy.py"
with open(file_path, "r") as f:
    content = f.read()

new_evaluate_entry = """    def evaluate_entry(
        self,
        frame: pd.DataFrame,
        price: float,
        side: str,
        **kwargs: Any
    ) -> Tuple[bool, str, Dict[str, Any]]:
        if frame is None or len(frame) < 2:
            return False, "INSUFFICIENT_DATA", {"action": "WAIT"}
            
        c1 = frame.iloc[-2]
        c2 = frame.iloc[-1]
        
        atr = float(c2.get("atr", 0))
        if atr <= 0:
            return False, "INVALID_ATR", {"action": "WAIT"}

        c2_close = float(c2["close"])
        c1_close = float(c1["close"])
        c1_open = float(c1["open"])
        
        c2_kc_upper = float(c2.get("kc_upper", 0))
        c2_kc_lower = float(c2.get("kc_lower", 0))
        c1_kc_upper = float(c1.get("kc_upper", 0))
        c1_kc_lower = float(c1.get("kc_lower", 0))
        c1_low = float(c1["low"])
        c1_high = float(c1["high"])
        c2_open = float(c2["open"])
        
        c1_body = abs(c1_close - c1_open)
        is_strong_momentum = c1_body >= 1.2 * atr
        
        c2_kc_mid = float(c2.get("kc_middle", c2.get("ema_20", 0)))
        c1_kc_mid = float(c1.get("kc_middle", c1.get("ema_20", 0)))
        
        if not is_strong_momentum:
            if side == "LONG" and c2_kc_mid < c1_kc_mid:
                return False, "WAIT_ENVIRONMENT_FLAT", {"action": "WAIT"}
            if side == "SHORT" and c2_kc_mid > c1_kc_mid:
                return False, "WAIT_ENVIRONMENT_FLAT", {"action": "WAIT"}

        if side == "SHORT":
            c1_breakout = c1_close < c1_kc_lower
            c2_is_bearish = c2_close < c2_open
            c2_stays_below = c2_close < c2_kc_lower
            c2_makes_lower_low = c2_close < c1_low
            
            if c1_breakout and c2_is_bearish and c2_stays_below and c2_makes_lower_low:
                return True, "CONFIRMED_KC_BREAKOUT_SHORT", {"action": "ENTER", "side": "SHORT", "entry_atr": atr}
                
        elif side == "LONG":
            c1_breakout = c1_close > c1_kc_upper
            c2_is_bullish = c2_close > c2_open
            c2_stays_above = c2_close > c2_kc_upper
            c2_makes_higher_high = c2_close > c1_high
            
            if c1_breakout and c2_is_bullish and c2_stays_above and c2_makes_higher_high:
                return True, "CONFIRMED_KC_BREAKOUT_LONG", {"action": "ENTER", "side": "LONG", "entry_atr": atr}
                
        return False, "NO_ENTRY_CONDITION_MET", {"action": "WAIT"}
"""

# Replace the OuterChannelEntryStrategy.evaluate_entry method
pattern = re.compile(r"    def evaluate_entry\([^:]*:\n        decision = aligned_entry\(frame, price, \*\*kwargs\)\n        if decision.get\(\"action\"\).*?return False, decision.get\(\"reason\", \"WAIT\"\), decision", re.DOTALL)
new_content, count = pattern.subn(new_evaluate_entry.strip("\n"), content)

if count > 0:
    with open(file_path, "w") as f:
        f.write(new_content)
    print("Replaced evaluate_entry successfully.")
else:
    print("Pattern not found!")
