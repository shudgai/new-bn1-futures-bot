import pandas as pd
from typing import Dict, Any, Tuple, Optional

def validate_strict_pivot_entry(
    frame: pd.DataFrame, side: str, live_price: float
) -> Tuple[bool, str]:
    if frame is None or len(frame) < 3:
        return False, "INSUFFICIENT_DATA"
    return True, "PIVOT_VALID"

def resolve_entry_atr(cr_info: dict, frame: pd.DataFrame, live_price: float) -> float:
    if frame is not None and not frame.empty and "atr" in frame.columns:
        atr_val = float(frame["atr"].iloc[-1])
        if atr_val > 0:
            return atr_val
    return max(live_price * 0.005, 1e-6)

def pivot_confirmation_body_atr(frame: pd.DataFrame, atr: float) -> float:
    if frame is None or frame.empty or atr <= 0:
        return 0.0
    curr = frame.iloc[-1]
    body = abs(float(curr["close"]) - float(curr["open"]))
    return float(body / atr)

def strong_burst_live_entry_is_valid(
    frame: pd.DataFrame, side: str, live_price: float
) -> bool:
    if frame is None or frame.empty:
        return False
    return True

def resolve_trailing_atr(
    cr_info: dict, frame: pd.DataFrame, live_price: float
) -> float:
    return resolve_entry_atr(cr_info, frame, live_price)

def opposite_closed_candle_exit(frame: pd.DataFrame, side: str) -> bool:
    if frame is None or len(frame) < 2:
        return False
    prev = frame.iloc[-2]
    if side == "LONG":
        return float(prev["close"]) < float(prev["open"])
    return float(prev["close"]) > float(prev["open"])

def outer_run_second_candle_status(frame: pd.DataFrame, side: str) -> Tuple[bool, bool]:
    if frame is None or len(frame) < 3:
        return False, False
    return True, True
