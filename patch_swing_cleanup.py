import re

with open('core/services/swing_service.py', 'r') as f:
    content = f.read()

# Let's completely remove the function channel_swing_action from swing_service.py
# It starts at `def channel_swing_action(` and goes on for a while. Let's just find its bounds.
# Alternatively we can just leave it for now or replace its body with `raise NotImplementedError`
# to be perfectly safe against weird imports, but user asked to remove.
content = re.sub(r'def channel_swing_action\(.*?(?=\ndef |\Z)', '', content, flags=re.DOTALL | re.MULTILINE)

with open('core/services/swing_service.py', 'w') as f:
    f.write(content)
print("Removed channel_swing_action successfully!")
