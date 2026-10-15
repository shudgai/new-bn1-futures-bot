from __future__ import annotations

from typing import Optional, Sequence

from .models import LONG, SHORT, Bar, ExitDecision, Position
from .pivot import true_reversal
from .ratchet import floor_roe, limit_price

MIN_HOLD_BARS = 3


def evaluate_exit(
    position: Position, bar: Bar, last_price: float,
    live_bar: Optional[Bar] = None, prior_bars: Sequence[Bar] = (),
) -> Optional[ExitDecision]:
    """Regular exits only. No per-position hard stop (the account circuit breaker owns tail risk).

    During the breathing window (bars_held < MIN_HOLD_BARS) only two exits are allowed:
    the activated ratchet (net ROE reached the 10% lock) and an extreme >1.8 ATR body
    breaking the midline. Everything else (micro-loss, MA cross, band touch, wicks) holds.
    Structure exits judge the CLOSE only, so intrabar wicks are ignored.
    """
    if position.bars_held < MIN_HOLD_BARS:
        floor = floor_roe(position.peak_roe)
        if floor is not None and position.current_roe < floor:
            return ExitDecision("RATCHET_PROFIT_LOCK", "LIMIT", limit_price(position.side, last_price))
        reason = true_reversal(position.side, live_bar, prior_bars) if live_bar is not None else None
        if reason is None and position.bars_held >= 1 and bar.is_closed and prior_bars:
            reason = true_reversal(position.side, bar, prior_bars)
        if reason:
            return ExitDecision(reason, "MARKET")
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
