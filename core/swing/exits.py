from __future__ import annotations

from typing import Optional, Sequence

from .models import LONG, SHORT, Bar, ExitDecision, Position
from .pivot import history_before, true_reversal
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
    def trend_intact(side: str, bar: Bar, prior_bars: Sequence[Bar]) -> bool:
        """Healthy one-way trend: close holds the right side of MA5 or MA15 and both MAs slope that way."""
        hist = history_before(bar, prior_bars)
        if not hist:
            return False
        prev = hist[-1]
        if side == LONG:
            return (bar.close > bar.ma5 or bar.close > bar.ma15) and bar.ma5 > prev.ma5 and bar.ma15 > prev.ma15
        return (bar.close < bar.ma5 or bar.close < bar.ma15) and bar.ma5 < prev.ma5 and bar.ma15 < prev.ma15

    @staticmethod
    def evaluate(
        position: Position, bar: Bar, last_price: float,
        live_bar: Optional[Bar] = None, prior_bars: Sequence[Bar] = (),
    ) -> Optional[ExitDecision]:
        locked = ExitGatekeeper.ratchet(position, last_price)
        if locked:
            return locked

        # True peak/trough passes in every phase (live bar first, then the closed bar after bar 0).
        reason = true_reversal(position.side, live_bar, prior_bars) if live_bar is not None else None
        if reason is None and position.bars_held >= 1 and bar.is_closed and prior_bars:
            reason = true_reversal(position.side, bar, prior_bars)
        if reason:
            return ExitDecision(reason, "MARKET")

        if position.bars_held < MIN_HOLD_BARS:
            return None
        if floor_roe(position.peak_roe) is not None or not bar.is_closed:
            return None  # an armed ratchet owns the exit; forming bars never trigger structure exits
        if ExitGatekeeper.trend_intact(position.side, bar, prior_bars):
            return None  # trend continuation invariance: never exit mid-trend
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
