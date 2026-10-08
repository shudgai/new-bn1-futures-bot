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
    if str(position.get("entry_mode") or "CHANNEL_SWING").upper() == "CHANNEL_SWING":
        position.update(sl=0., tp=0., stop_loss=0., atr_sl=0., atr_tp=0.)


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
    from core.services.exits.trend_pivot_exit import enabled, enforce
    if enabled(position, meta):
        return await enforce(account, symbol, price)
    for key in STATE_KEYS:
        if key not in position and key in meta:
            position[key] = copy.deepcopy(meta[key])
    from core.services.exits.peak_trailing_exit import migrate_peak_state
    migrate_peak_state(position, meta)
    reason = atr_exit_reason(position, price)
    observed = {key: copy.deepcopy(position[key]) for key in STATE_KEYS if key in position}
    if any(meta.get(key) != value for key, value in observed.items()):
        meta.update(observed)
        account.save_state()
    if not reason:
        return False
    await account.close_position(symbol, price, "Channel Swing " + reason, is_manual=True)
    return True
