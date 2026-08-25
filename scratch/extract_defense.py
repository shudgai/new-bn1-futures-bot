import re

with open("core/engine.py", "r") as f:
    content = f.read()

# The defense block currently sits inside `if cr_info:` around line 2796
# It ends around line 2891:
#                                             cr_signal = "LONG"
#                                             cr_entry_type = "TROUGH_TURN"
# 
#                             if cr_signal:
#                                 # ...

# Let's find the defense block
start_marker = "                            # --- 防禦性提早平倉 (Defensive Early Exit) ---"
end_marker = "                            if cr_signal:"

start_idx = content.find(start_marker)
end_idx = content.find(end_marker, start_idx)

if start_idx == -1 or end_idx == -1:
    print("Could not find markers")
    exit(1)

defense_block = content[start_idx:end_idx]

# We want to unindent the defense block by 4 spaces and place it BEFORE `if cr_info:`
# Wait, actually `has_pos` and `curr_side` are evaluated inside `if cr_info:` right now.
# We need to ensure `has_pos` and `curr_side` are evaluated before the defense block.

