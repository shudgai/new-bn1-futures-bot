import re

with open("core/engine.py", "r") as f:
    content = f.read()

# Fix _bottom_entry_grace to use 3 mins for MA3_PIVOT
replacement = """
    @staticmethod
    def _bottom_entry_grace(position: dict, now: float) -> tuple[bool, float]:
        entry_mode = position.get("entry_mode")
        if entry_mode not in ("MA5_BOTTOM_LIMIT", "MA3_PIVOT"):
            return False, 0.0
        opened_at = float(position.get("open_timestamp") or now)
        age_sec = max(0.0, now - opened_at)
        if entry_mode == "MA3_PIVOT":
            return age_sec < 180, age_sec
        return age_sec < MA5_BOTTOM_MIN_HOLD_SEC, age_sec
"""

content = re.sub(
    r'\s*@staticmethod\s*def _bottom_entry_grace\(position: dict, now: float\) -> tuple\[bool, float\]:.*?return age_sec < MA5_BOTTOM_MIN_HOLD_SEC, age_sec',
    replacement,
    content,
    flags=re.DOTALL
)

# Remove live_pivot_exit bypass from bottom grace
content = content.replace("and (not bottom_grace or pre_turn_exit or live_pivot_exit)", "and (not bottom_grace or pre_turn_exit)")

with open("core/engine.py", "w") as f:
    f.write(content)
