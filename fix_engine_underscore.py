import re

with open('core/engine.py', 'r') as f:
    content = f.read()

# 1. _channel_entry_snapshot: line 1769
# else self._(frame, price, check_profit_room=False))["reason"]
# Replace with else {"reason": "V9_MIGRATED_SIGNAL"}
content = re.sub(r'else self\._\(frame, price, check_profit_room=False\)\)\["reason"\]', 'else {"reason": "V9_MIGRATED_SIGNAL"})["reason"]', content)

# 2. _channel_entry_snapshot: line 1787
# or self._(
#    frame, price,
#    ...
# ).get("side") != side
pattern2 = r'or self\._\(\s*frame, price,[^)]+\)\.get\("side"\) != side'
content = re.sub(pattern2, 'or False', content)

# 3. _place_structured_entry_locked: line 1977
# final_entry = self._(fresh_frame, planned_price)
# ...
pattern3 = r'final_entry = self\._\(fresh_frame, planned_price\)\s*\n\s*if final_entry\.get\("action"\) != "ENTER" or final_entry\.get\("side"\) != side:\s*\n\s*self\.account\.log\(\s*f"⏳ \{symbol\} \{side\} \{final_entry\.get\(\'reason\', \'KC_ENTRY_WAIT\'\)\}：最新快照已不適合追入",\s*"INFO",\s*\)\s*\n\s*return False'
content = re.sub(pattern3, '', content)

# 4. _execute_confirmed_channel_break: line 2407
# decision = self._(...)
pattern4 = r'decision = self\._\([^)]*\)\s*\n\s*if decision\.get\("side"\) != side or decision\.get\("action"\) not in \{"ENTER", "REVERSE"\}:\s*\n\s*pending\.pop\(symbol, None\)\s*\n\s*reverse_bars\.pop\(symbol, None\)\s*\n\s*return False'
content = re.sub(pattern4, '', content)

# 5. _tick: line 2781
pattern5 = r'decision = self\._\(frame, price\)\s*\n\s*return decision\.get\("action"\) == "ENTER" and decision\.get\("side"\) == ticket\["side"\]'
content = re.sub(pattern5, 'return False', content)

# 6. _try_profit_reentry_locked: line 2871
pattern6 = r'else self\._\(frame, price\)\)'
content = re.sub(pattern6, 'else {"reason": "V9_MIGRATED_SIGNAL"})', content)


with open('core/engine.py', 'w') as f:
    f.write(content)
print("Fixed self._ issues!")
