import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

content = content.replace(
    '        if frame is None or len(frame) < 10 or ("kc_middle" not in frame.columns and "ema_20" not in frame.columns):',
    '''        print(f"DEBUG EVAL: frame_is_none={frame is None}, len_frame={len(frame) if frame is not None else 0}, has_kc={'kc_middle' in frame.columns}, has_ema={'ema_20' in frame.columns}", flush=True)
        if frame is None or len(frame) < 10 or ("kc_middle" not in frame.columns and "ema_20" not in frame.columns):'''
)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)
