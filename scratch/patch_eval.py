import re

user_code = """
        c1 = frame.iloc[-2]
        c2 = frame.iloc[-1]
        atr = float(c2.get("atr", 0))
        
        if atr <= 0:
            return False, "WAIT_INVALID_ATR", {"action": "WAIT"}

        c2_body = abs(c2["close"] - c2["open"])

        # ==================== 1. 大動能長實體：第一根收盤即刻開倉 ====================
        # 多單：當前這根 c2 剛好爆破上軌，且是超大實體 (>= 1.2 ATR)
        if (
            side == "LONG"
            and c2["close"] > c2.get("kc_upper", 0)
            and c2["close"] > c2["open"]
            and c2_body >= 1.2 * atr
        ):
            return True, "MOMENTUM_BREAKOUT_C1_LONG", {
                "action": "ENTER",
                "side": "LONG",
                "reason": "MOMENTUM_BREAKOUT_C1_LONG",
                "entry_atr": atr,
            }

        # 空單：當前這根 c2 剛好爆破下軌，且是超大實體 (>= 1.2 ATR)
        if (
            side == "SHORT"
            and c2["close"] < c2.get("kc_lower", 0)
            and c2["close"] < c2["open"]
            and c2_body >= 1.2 * atr
        ):
            return True, "MOMENTUM_BREAKOUT_C1_SHORT", {
                "action": "ENTER",
                "side": "SHORT",
                "reason": "MOMENTUM_BREAKOUT_C1_SHORT",
                "entry_atr": atr,
            }

        # ==================== 2. 常規突破：等第二根 (c2) 收盤確認 ====================
        # 多單：c1 破上軌，c2 收盤依然留於上軌外
        if side == "LONG" and c1["close"] > c1.get("kc_upper", 0) and c2["close"] > c2.get("kc_upper", 0):
            return True, "CONFIRMED_KC_BREAKOUT_LONG", {
                "action": "ENTER",
                "side": "LONG",
                "reason": "CONFIRMED_KC_BREAKOUT_LONG",
                "entry_atr": atr,
            }

        # 空單：c1 破下軌，c2 收盤依然留於下軌外
        if side == "SHORT" and c1["close"] < c1.get("kc_lower", 0) and c2["close"] < c2.get("kc_lower", 0):
            return True, "CONFIRMED_KC_BREAKOUT_SHORT", {
                "action": "ENTER",
                "side": "SHORT",
                "reason": "CONFIRMED_KC_BREAKOUT_SHORT",
                "entry_atr": atr,
            }
            
        return False, "WAIT_NO_SIGNAL", {"action": "WAIT"}
"""

def replace_eval(filepath, is_unified=False):
    with open(filepath, "r") as f:
        content = f.read()

    # Find where evaluate_entry / check_entry_signals starts
    if is_unified:
        start_marker = "        if momentum_signal and momentum_signal[\"side\"] == side:\n            return True, momentum_signal[\"reason\"], momentum_signal\n"
        pattern = re.escape(start_marker) + r".*?return False, \"WAIT_NO_SIGNAL\", \{\"action\": \"WAIT\"\}"
    else:
        start_marker = "    if momentum_signal and momentum_signal[\"side\"] == side:\n        return momentum_signal\n"
        pattern = re.escape(start_marker) + r".*?return \{\"action\": \"WAIT\", \"reason\": \"NO_ENTRY_CONDITION_MET\"\}"
        
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        print(f"Could not find match in {filepath}")
        return
        
    replacement = start_marker + "\n" + user_code
    if not is_unified:
        # adjust returns for entry_service
        replacement = replacement.replace("return True, ", "return ")
        replacement = replacement.replace("return False, ", "return ")
        replacement = replacement.replace("return \"WAIT_NO_SIGNAL\", ", "return ")
        # dedent one level
        replacement = "\n".join([line[4:] if line.startswith("    ") else line for line in replacement.split("\n")])

    new_content = content[:match.start()] + replacement + content[match.end():]
    with open(filepath, "w") as f:
        f.write(new_content)

replace_eval("core/services/strategies/unified_entry_strategy.py", True)
replace_eval("core/services/entry_service.py", False)

