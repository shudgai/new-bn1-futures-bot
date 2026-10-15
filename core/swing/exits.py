from __future__ import annotations

from typing import Optional, Sequence

from .models import LONG, SHORT, Bar, ExitDecision, Position
from .pivot import true_reversal
from .ratchet import floor_roe, limit_price

MIN_HOLD_BARS = 3


class ExitGatekeeper:
    """Single gate for all regular exits: ratchet lock + anti-chop breathing window.

    No per-position hard stop (the account circuit breaker owns tail risk).
    While bars_held < MIN_HOLD_BARS only two exits pass: the ratchet lock (net ROE reached
    the 10% activation) and an extreme >1.8 ATR body breaking the midline. Structure exits
    judge the CLOSE only, so intrabar wicks are ignored.
    """

    @staticmethod
    def ratchet(position: Position, last_price: float) -> Optional[ExitDecision]:
        floor = floor_roe(position.peak_roe)
        if floor is not None and position.current_roe <= floor:
            return ExitDecision("RATCHET_PROFIT_LOCK", "LIMIT", limit_price(position.side, last_price))
        return None

    @staticmethod
    def evaluate(
        position: Position, bar: Bar, last_price: float,
        live_bar: Optional[Bar] = None, prior_bars: Sequence[Bar] = (),
    ) -> Optional[ExitDecision]:
        locked = ExitGatekeeper.ratchet(position, last_price)
        if locked:
            return locked

        if position.bars_held < MIN_HOLD_BARS:
            reason = true_reversal(position.side, live_bar, prior_bars) if live_bar is not None else None
            if reason is None and position.bars_held >= 1 and bar.is_closed and prior_bars:
                reason = true_reversal(position.side, bar, prior_bars)
            return ExitDecision(reason, "MARKET") if reason else None

        if floor_roe(position.peak_roe) is not None or not bar.is_closed:
            return None  # an armed ratchet owns the exit; forming bars never trigger structure exits
        if position.side == LONG and bar.close < bar.ma15:
            return ExitDecision("STRUCTURE_EXIT_BELOW_MIDLINE", "MARKET")
        if position.side == SHORT and bar.close > bar.ma15:
            return ExitDecision("STRUCTURE_EXIT_ABOVE_MIDLINE", "MARKET")
        return None


def evaluate_exit(
    position: Position, bar: Bar, last_price: float,
    live_bar: Optional[Bar] = None, prior_bars: Sequence[Bar] = (),
) -> Optional[ExitDecision]:
    return ExitGatekeeper.evaluate(position, bar, last_price, live_bar, prior_bars)
