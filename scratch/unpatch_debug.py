import re

with open("core/services/strategies/unified_entry_strategy.py", "r") as f:
    content = f.read()

content = content.replace(
    '''        print(f"DEBUG EVAL: frame_is_none={frame is None}, len_frame={len(frame) if frame is not None else 0}, has_kc={'kc_middle' in frame.columns}, has_ema={'ema_20' in frame.columns}", flush=True)
        if frame is None or len(frame) < 10 or ("kc_middle" not in frame.columns and "ema_20" not in frame.columns):''',
    '        if frame is None or len(frame) < 10 or ("kc_middle" not in frame.columns and "ema_20" not in frame.columns):'
)

with open("core/services/strategies/unified_entry_strategy.py", "w") as f:
    f.write(content)

with open("core/services/symbol_runner.py", "r") as f:
    content2 = f.read()
    
content2 = content2.replace(
    'print(f"[{symbol}] {direct_side} Rejected: {reason} | Len: {len(channel_df)} | Cols: {list(channel_df.columns)}", flush=True)',
    'print(f"[{symbol}] {direct_side} Rejected: {reason}", flush=True)'
)

with open("core/services/symbol_runner.py", "w") as f:
    f.write(content2)
