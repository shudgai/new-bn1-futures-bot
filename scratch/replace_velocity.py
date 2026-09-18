import re

with open("core/engine.py", "r") as f:
    content = f.read()

# Change function signature
content = content.replace("def get_velocity_slowdown(self, symbol: str) -> bool:", "def get_velocity_drop_ratio(self, symbol: str) -> float:")

# Change return logic
old_return = """        if speed_prev > 0:
            drop_ratio = (speed_prev - speed_recent) / speed_prev
            return drop_ratio >= 0.20
        return False"""

new_return = """        if speed_prev > 0:
            return (speed_prev - speed_recent) / speed_prev
        return 0.0"""

content = content.replace(old_return, new_return)

with open("core/engine.py", "w") as f:
    f.write(content)
