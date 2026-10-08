"""Retired fixed-entry-ATR profit-floor exit."""
REASON = "EXIT_FIXED_ATR_HALF_STEP_PROFIT"


def evaluate(position, state, symbol, price, stamp, *, fee, slippage):
    return None, None, {}
