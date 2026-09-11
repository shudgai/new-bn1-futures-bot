"""Risk and position guard functions.
OOP RiskGuardManager implementing IGuardRule.
"""
import pandas as pd
from typing import Dict, Any, Optional, Tuple
from core.interfaces.guard_interface import IGuardRule

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


class RiskGuardManager(IGuardRule):
    """OOP Guard Manager implementing IGuardRule for same-side limits and bar lock enforcement."""

    def __init__(self, max_same_side: int = 3):
        self.max_same_side = max_same_side

    def check_permission(
        self,
        account: Any,
        symbol: str,
        side: str,
        frame: Optional[pd.DataFrame] = None,
        price: float = 0.0,
        **kwargs: Any
    ) -> Tuple[bool, str]:
        positions = getattr(account, "positions", {})
        pending = getattr(account, "pending_orders", {})
        if not same_side_entry_allowed(positions, pending, side, self.max_same_side):
            return False, f"SAME_SIDE_LIMIT_EXCEEDED_MAX_{self.max_same_side}"

        if frame is not None:
            bar = candidate_bar_id(frame)
            if candidate_bar_invalid_locked(account, symbol, bar):
                return False, "CANDIDATE_BAR_INVALID_LOCKED"

        return True, "PERMISSION_GRANTED"
