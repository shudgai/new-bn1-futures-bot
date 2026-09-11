"""Risk and position guard functions."""
import pandas as pd
from typing import Dict, Any, Optional

def same_side_entry_allowed(
    positions: Dict[str, Any],
    pending_orders: Dict[str, Any],
    requested_side: str,
    max_same_side: int,
) -> bool:
    side_str = str(requested_side or "").upper()
    count = 0
    for pos in positions.values():
        if str(pos.get("side") or "").upper() == side_str:
            count += 1
    for order in pending_orders.values():
        if str(order.get("side") or "").upper() == side_str:
            count += 1
    return count < max_same_side

def candidate_bar_id(frame: pd.DataFrame) -> Optional[Any]:
    if frame is None or frame.empty:
        return None
    last_row = frame.iloc[-1]
    return last_row.get("timestamp", frame.index[-1])

def candidate_bar_invalid_locked(account: Any, symbol: str, bar_id: Any) -> bool:
    if not account or not symbol or bar_id is None:
        return False
    locked_bar = getattr(account, "invalid_candidate_bars", {}).get(symbol)
    return locked_bar is not None and locked_bar == bar_id
