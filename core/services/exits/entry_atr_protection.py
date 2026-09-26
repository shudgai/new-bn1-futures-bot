"""Compatibility adapters for closed-candle chandelier protection."""
import math

from core.services.exit_service import STATE_KEYS


def valid_entry_atr(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def initialize_atr_protection(position, entry_price, side, atr):
    from core.services.exit_service import initialize_chandelier
    initialize_chandelier(position, entry_price, side, atr)


def atr_exit_reason(position, price, frame=None):
    from core.services.exit_service import chandelier_exit_reason
    return chandelier_exit_reason(position, price, frame)


async def enforce_atr_protection(account, symbol, price):
    """Account updates also enforce the lines when the strategy scan is delayed."""
    import copy
    position = account.positions.get(symbol)
    if not position:
        return False
    reason = atr_exit_reason(position, price)
    meta = account.position_meta.setdefault(symbol, {})
    observed = {key: copy.deepcopy(position[key]) for key in STATE_KEYS if key in position}
    if any(meta.get(key) != value for key, value in observed.items()):
        meta.update(observed)
        account.save_state()
    if not reason:
        return False
    await account.close_position(symbol, price, "Channel Swing " + reason, is_manual=True)
    return True
