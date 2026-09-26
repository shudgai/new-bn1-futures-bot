import re

with open("core/services/entry_service.py", "r") as f:
    content = f.read()

# I want to rewrite check_entry_signals completely.
# Let's find check_entry_signals and replace everything until the end of the file or the end of the function.
# check_entry_signals is near the bottom.

pattern = re.compile(r"def check_entry_signals\(.*?\) -> Dict\[str, Any\]:.*?(?=def |\Z)", re.DOTALL)

new_func = """def check_entry_signals(
    frame: pd.DataFrame, side: str, min_space_buffer_atr: float, state: dict = None
) -> Dict[str, Any]:
    from core.services.strategies.unified_entry_strategy import check_streamlined_entry_signal
    
    if frame is None or len(frame) == 0:
        return {"action": "WAIT", "side": None, "reason": "EMPTY_FRAME"}
        
    try:
        live_price = float(frame.iloc[-1]['close'])
    except Exception:
        live_price = 0.0
        
    ok, reason, signal_dict = check_streamlined_entry_signal(frame, side, live_price, "NO_POSITION")
    
    if ok and signal_dict:
        return signal_dict
        
    return {"action": "WAIT", "side": side, "reason": reason}

"""

content = pattern.sub(new_func, content)

with open("core/services/entry_service.py", "w") as f:
    f.write(content)

print("Patched entry_service.py")
