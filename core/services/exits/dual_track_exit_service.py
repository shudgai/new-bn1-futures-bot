"""Account compatibility adapter for tick peak trailing and initial hard stops."""
from core.interfaces.exit_interface import IExitStrategy

from core.services.exits.peak_trailing_exit import (
    POLICY, STATE_KEY, STATE_KEYS, evaluate_peak_trailing,
)
DUAL_TRACK_STATE_KEYS = list(STATE_KEYS)
SL_INIT_MULT = 1.5


def evaluate_trend_exit_and_take_profit(position, closed, atr):
    """Retired candle-only adapter; no live quote means no exit authority."""
    return dict(should_exit=False, action='HOLD', reason='WAIT_LIVE_QUOTE')


def observe_breakeven(position: dict, price: float) -> dict:
    """Legacy helper to detect breakeven.

    The current implementation delegates to ``DualTrackExitStrategy`` which
    encapsulates the peak‑trailing logic.  If the strategy reports a result type
    of ``"BREAKEVEN"`` we consider the breakeven condition met.
    """
    # Use the existing strategy to evaluate the exit type.
    strategy = DualTrackExitStrategy()
    result_type = strategy.evaluate_exit(position, current_price=price)
    if result_type == "BREAKEVEN":
        return {"should_exit": True, "action": "BREAKEVEN", "reason": "BREAKEVEN_REACHED"}
    # No breakeven reached – keep holding.
    return {"should_exit": False, "action": "HOLD", "reason": "NO_BREAKEVEN"}



class DualTrackExitStrategy(IExitStrategy):
    def initialize_position(self, position, entry_price, atr):
        sign = 1 if position['side'] == 'LONG' else -1
        position.update(entry_atr=float(atr), sl=entry_price-sign*SL_INIT_MULT*atr,
                        stop_loss=entry_price-sign*SL_INIT_MULT*atr, tp=0.)

    def evaluate_exit(self, position, frame=None, current_price=None, **kwargs):
        import time
        from core.services.exits.trend_pivot_exit import enabled, evaluate
        if enabled(position):
            evidence, _ = evaluate(position, frame, current_price,
                                   kwargs.get('quote_ms', time.time()*1000))
            return evidence["reason"] if evidence else None
        from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT
        if current_price is None:
            return None
        result = evaluate_peak_trailing(position, current_price, kwargs.get('quote_ms', time.time()*1000),
                                        fee=TAKER_FEE_RATE, slippage=SLIPPAGE_PCT)
        return result['type'] if result else None

    def handle_post_exit_cleanup(self, position, exit_reason):
        position.pop(STATE_KEY, None)
        position.pop('has_broken_outer_band', None)
        position['cooldown_mode'] = 'NONE'
