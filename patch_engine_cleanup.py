import re

with open('core/engine.py', 'r') as f:
    content = f.read()

# 1. Remove channel_swing_action import
content = re.sub(r'channel_swing_action,\s*', '', content)
content = re.sub(r'channel_swing_action\s*', '', content)

# 2. Remove staticmethod assignment
content = re.sub(r'^\s*_channel_swing_action = staticmethod\(channel_swing_action\)\n?', '', content, flags=re.MULTILINE)

# 3. Simplify _execute_confirmed_channel_break V8 fallback
fallback_pattern = r'is_v9_signal = v8_reason and v8_reason\.startswith\("TRACK_"\)\s*\n\s*if is_v9_signal:\s*\n\s*decision = \{"action": "ENTER", "side": side, "reason": v8_reason\}\s*\n\s*else:\s*\n\s*decision = self\._channel_swing_action\([^)]*\)\s*\n\s*if decision\.get\("side"\) != side or decision\.get\("action"\) not in \{"ENTER", "REVERSE"\}:\s*\n\s*pending\.pop\(symbol, None\)\s*\n\s*reverse_bars\.pop\(symbol, None\)\s*\n\s*return False'

replacement = r'''is_v9_signal = v8_reason and v8_reason.startswith("TRACK_")
            decision = {"action": "ENTER", "side": side, "reason": v8_reason}'''
content = re.sub(fallback_pattern, replacement, content, flags=re.MULTILINE)

# 4. Simplify _place_structured_entry_locked V8 fallback
fallback_pattern2 = r'is_v9_signal = signal\.get\("signal_code", ""\)\.startswith\("TRACK_"\)\s*\n\s*if not ck_reverse and not live_pivot and not is_v9_signal:\s*\n\s*final_entry = self\._channel_swing_action\([^)]*\)\s*\n\s*if final_entry\.get\("action"\) != "ENTER" or final_entry\.get\("side"\) != side:\s*\n\s*self\.account\.log\(\s*f"⏳ \{symbol\} \{side\} \{final_entry\.get\(\'reason\', \'KC_ENTRY_WAIT\'\)\}：最新快照已不適合追入",\s*"INFO",\s*\)\s*\n\s*return False'

replacement2 = r'''is_v9_signal = signal.get("signal_code", "").startswith("TRACK_")
            # V8 fallback removed'''
content = re.sub(fallback_pattern2, replacement2, content, flags=re.MULTILINE)

# 5. Remove aligned_entry_ready from intrabar_ready
intrabar_pattern = r'ready = \(ck_direction\(frame\) == side and live_adverse_entry_safe\(frame, price, side\)\s*\n\s*and live_ma3_direction_ready\(frame, price, side\)\s*\n\s*if ck_reverse else aligned_entry_ready\(frame, price, side\)\)'
replacement3 = r'''ready = (ck_direction(frame) == side and live_adverse_entry_safe(frame, price, side)
                 and live_ma3_direction_ready(frame, price, side)
                 if ck_reverse else True)'''
content = re.sub(intrabar_pattern, replacement3, content, flags=re.MULTILINE)

with open('core/engine.py', 'w') as f:
    f.write(content)
print("Applied cleanups successfully!")
