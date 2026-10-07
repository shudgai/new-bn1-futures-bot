"""Account update adapters for live peak trailing and initial hard stops."""
import math

from core.services.exits.dual_track_exit_service import DUAL_TRACK_STATE_KEYS as STATE_KEYS


def valid_entry_atr(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def initialize_atr_protection(position, entry_price, side, atr, initial_stop=None):
    from core.services.exit_service import initialize_chandelier
    initialize_chandelier(position, entry_price, side, atr, initial_stop=initial_stop)


def atr_exit_reason(position, price, frame=None):
    from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
    return DualTrackExitStrategy().evaluate_exit(position, frame, current_price=price)


async def enforce_atr_protection(account, symbol, price):
    """Account updates also enforce the lines when the strategy scan is delayed."""
    import copy
    position = account.positions.get(symbol)
    if not position:
        return False
    meta = account.position_meta.setdefault(symbol, {})
    previous_meta = copy.deepcopy(meta)
    if not position.get('entry_mode') and meta.get('entry_mode'):
        position['entry_mode'] = meta['entry_mode']
    for key in STATE_KEYS:
        if key not in position and key in meta:
            position[key] = copy.deepcopy(meta[key])
    from core.services.exits.peak_trailing_exit import migrate_peak_state
    migrate_peak_state(position, meta)
    reason = atr_exit_reason(position, price)
    observed = {key: copy.deepcopy(position[key]) for key in STATE_KEYS if key in position}
    meta.update(observed)
    if previous_meta != meta:
        account.save_state()
    if not reason:
        return False
    closed = await account.close_position(symbol, price, "Channel Swing " + reason, is_manual=True)
    from core.services.exits.ma5_outer_pivot_exit import REASON as MA5_EXIT
    return bool(closed) if reason in ('EXIT_CONFIRMED_SWING_STRUCTURE', MA5_EXIT, 'EXIT_OUTER_MA5_V_REVERSAL') else True
