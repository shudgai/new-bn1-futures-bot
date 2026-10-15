from __future__ import annotations

from typing import Optional

from .models import LONG, SHORT, Bar, ExitDecision, Position
from .ratchet import floor_roe, limit_price


def evaluate_exit(position: Position, bar: Bar, last_price: float) -> Optional[ExitDecision]:
    """Regular exits only. No fixed hard stop (account-level circuit breaker owns tail risk)."""
    if position.bars_held < 1:
        return None

    floor = floor_roe(position.peak_roe)
    if floor is not None:
        if position.current_roe < floor:
            return ExitDecision("RATCHET_PROFIT_LOCK", "LIMIT", limit_price(position.side, last_price))
        return None

    if not bar.is_closed:
        return None
    if position.side == LONG and bar.close < bar.ma15:
        return ExitDecision("STRUCTURE_EXIT_BELOW_MIDLINE", "MARKET")
    if position.side == SHORT and bar.close > bar.ma15:
        return ExitDecision("STRUCTURE_EXIT_ABOVE_MIDLINE", "MARKET")
    return None
