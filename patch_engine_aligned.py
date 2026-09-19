import re

with open('core/engine.py', 'r') as f:
    content = f.read()

# 1. Remove aligned_entry_ready from import
content = re.sub(r'aligned_entry_ready,\s*', '', content)

# 2. _execute_confirmed_channel_break V8 fallback removal (already replaced is_v9_signal check but let's check)
# line 2370: if not is_v9_signal and not aligned_entry_ready(...):
pattern1 = r'if not is_v9_signal and not aligned_entry_ready\([^)]+\):\s*\n\s*return False'
content = re.sub(pattern1, '', content, flags=re.MULTILINE)

# 3. _place_structured_entry_locked V8 fallback
# line 1989: reverse_quote_ready(self, symbol, fresh_frame, planned_price, side) if ck_reverse else aligned_entry_ready(fresh_frame, planned_price, side)
pattern2 = r'reverse_quote_ready\(self, symbol, fresh_frame, planned_price, side\) if ck_reverse else aligned_entry_ready\(fresh_frame, planned_price, side\)'
content = re.sub(pattern2, r'reverse_quote_ready(self, symbol, fresh_frame, planned_price, side) if ck_reverse else True', content)

# 4. _channel_entry_snapshot
pattern3 = r'\s*entry_ready = aligned_entry_ready\(frame, price, side\)\s*\n\s*if not entry_ready:\s*\n\s*return None'
content = re.sub(pattern3, '', content)

# 5. _ck_reverse_order_authorized
pattern4 = r'outer_ready = aligned_entry_ready\(frame, price, side\)'
content = re.sub(pattern4, 'outer_ready = True', content)

with open('core/engine.py', 'w') as f:
    f.write(content)
print("Applied aligned_entry_ready cleanups successfully!")
