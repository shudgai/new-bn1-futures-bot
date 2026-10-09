"""Channel Swing ATR observations and peak exits, without an initial ATR stop."""
import math
import time

from core.services.exits.dual_track_exit_service import DUAL_TRACK_STATE_KEYS as STATE_KEYS
from core.services.exits.peak_trailing_exit import (
    CHANNEL_SWING_EXIT_TRIGGERS, PIVOT_ONLY_CHANNEL_EXIT_TRIGGERS,
)


def valid_entry_atr(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def channel_strategy_exit_grace_active(position, meta=None, now=None):
    from core.config import MIN_HOLD_SEC_FOR_STRATEGY_EXIT

    meta = meta or {}
    hold_seconds = MIN_HOLD_SEC_FOR_STRATEGY_EXIT
    if (hold_seconds <= 0
            or str(position.get("entry_mode") or meta.get("entry_mode") or "").upper()
            != "CHANNEL_SWING"):
        return False
    try:
        opened_at = float(position.get("open_timestamp") or meta.get("open_timestamp"))
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(opened_at) or opened_at <= 0:
        return False
    return (time.time() if now is None else float(now)) - opened_at < hold_seconds


def clear_channel_strategy_exit_pending(position, meta):
    pending_keys = (
        "pending", "trigger", "trigger_bar_ms", "trigger_open",
        "trigger_atr", "trigger_price", "trigger_confirmed_ms",
    )
    changed = False
    for source in (position, meta):
        state = source.get("peak_trailing_state")
        if isinstance(state, dict):
            for key in pending_keys:
                changed = state.pop(key, None) is not None or changed
        for key in pending_keys:
            changed = source.pop(key, None) is not None or changed
    return changed


def initialize_atr_protection(position, entry_price, side, atr, initial_stop=None):
    from core.services.exit_service import initialize_chandelier
    from core.services.exits.peak_trailing_exit import channel_initial_stop_disabled
    initialize_chandelier(position, entry_price, side, atr, initial_stop=initial_stop,
                          initial_stop_enabled=not channel_initial_stop_disabled(position))


def atr_exit_reason(position, price, frame=None):
    from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
    return DualTrackExitStrategy().evaluate_exit(position, frame, current_price=price)


async def enforce_atr_protection(account, symbol, price):
    """Observe peak exits even when the strategy scan is delayed."""
    import copy
    position = account.positions.get(symbol)
    if not position:
        return False
    meta = account.position_meta.setdefault(symbol, {})
    previous_meta = copy.deepcopy(meta)
    for key in STATE_KEYS:
        if key not in position and key in meta:
            position[key] = copy.deepcopy(meta[key])
    from core.services.exits.peak_trailing_exit import migrate_peak_state
    migrate_peak_state(position, meta)
    entry_mode = str(position.get("entry_mode") or meta.get("entry_mode") or "").upper()
    decision = None
    if entry_mode == "CHANNEL_SWING":
        from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT
        from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
        decision = evaluate_peak_trailing(
            position, price, time.time() * 1000,
            fee=TAKER_FEE_RATE, slippage=SLIPPAGE_PCT,
        )
        reason = decision["type"] if decision else None
    else:
        reason = atr_exit_reason(position, price)
    observed = {key: copy.deepcopy(position[key]) for key in STATE_KEYS if key in position}
    meta.update(observed)
    if previous_meta != meta:
        account.save_state()
    if not reason:
        return False
    if (entry_mode == "CHANNEL_SWING"
            and channel_strategy_exit_grace_active(position, meta)):
        if clear_channel_strategy_exit_pending(position, meta):
            meta.update({key: copy.deepcopy(position[key])
                         for key in STATE_KEYS if key in position})
            account.save_state()
        return False
    allowed_triggers = (
        PIVOT_ONLY_CHANNEL_EXIT_TRIGGERS
        if symbol in ("SUI/USDT", "龙虾/USDT", "LOBSTER/USDT")
        else CHANNEL_SWING_EXIT_TRIGGERS
    )
    if (entry_mode == "CHANNEL_SWING"
            and decision.get("trigger") not in allowed_triggers):
        state = position.get("peak_trailing_state", {})
        meta_state = meta.get("peak_trailing_state", {})
        pending_keys = ("pending", "trigger", "trigger_bar_ms", "trigger_open",
                        "trigger_atr", "trigger_price", "trigger_confirmed_ms")
        for source in (state, meta_state):
            for key in pending_keys:
                source.pop(key, None)
        for key in pending_keys:
            position.pop(key, None)
            meta.pop(key, None)
        meta.update({key: copy.deepcopy(position[key])
                     for key in STATE_KEYS if key in position})
        account.save_state()
        return False
    await account.close_position(symbol, price, "Channel Swing " + reason, is_manual=True)
    return True
