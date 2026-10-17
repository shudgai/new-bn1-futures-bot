"""Channel Swing ATR observations and peak exits, without an initial ATR stop."""
import math
import time

from core.services.exits.dual_track_exit_service import DUAL_TRACK_STATE_KEYS as STATE_KEYS
from core.services.exits.peak_trailing_exit import channel_swing_exit_allowed
from core.services.exits.hard_stop_service import POSITION_HARD_STOPS_ENABLED


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
                          initial_stop_enabled=(
                              POSITION_HARD_STOPS_ENABLED
                              and not channel_initial_stop_disabled(position)
                          ))


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

    from core.symbol_aliases import is_auto_entry_symbol
    if is_auto_entry_symbol(symbol):
        from core.services.exits.strict_true_pivot import (
            CONFIRMED_PIVOT_PENDING_KEY,
            confirmed_true_pivot_exit,
            pending_confirmed_pivot_exit,
            store_confirmed_pivot_exit,
        )

        pivot = pending_confirmed_pivot_exit(position, meta)
        if pivot is None:
            frame_provider = getattr(account, "channel_exit_frame_provider", None)
            frame = frame_provider(symbol) if callable(frame_provider) else None
            pivot = confirmed_true_pivot_exit(position, frame)
            if pivot is not None and not store_confirmed_pivot_exit(position, pivot):
                account.log(
                    f"TREND_EXIT_BLOCK symbol={symbol} "
                    "reason=INVALID_CONFIRMED_PIVOT_TICKET",
                    "ERROR",
                )
                return False
            if pivot is not None:
                meta[CONFIRMED_PIVOT_PENDING_KEY] = copy.deepcopy(
                    position[CONFIRMED_PIVOT_PENDING_KEY]
                )
                account.save_state()

        if pivot is not None:
            pivot_reason, pivot_details = pivot
            account.log(
                f"TRUE_PIVOT_EXIT symbol={symbol} side={position.get('side')} "
                f"reason={pivot_reason} details={pivot_details}",
                "WARNING",
            )
            closed = await account.close_position(
                symbol, price, f"{entry_mode} {pivot_reason}", is_manual=True,
            )
            if not closed:
                account.log(
                    f"TRUE_PIVOT_EXIT_RETRY_PENDING symbol={symbol} "
                    f"reason={pivot_reason}",
                    "WARNING",
                )
            return True

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

    if entry_mode == "CHANNEL_SWING" and is_auto_entry_symbol(symbol) and reason:
        from core.services.exits.authorized_position_exit import (
            automated_position_exit_allowed,
        )

        if not automated_position_exit_allowed(
            symbol, position, meta, reason, price, is_manual=True,
        ):
            if clear_channel_strategy_exit_pending(position, meta):
                meta.update({
                    key: copy.deepcopy(position[key])
                    for key in STATE_KEYS
                    if key in position
                })
                account.save_state()
            reason = None
            decision = None

    observed = {key: copy.deepcopy(position[key]) for key in STATE_KEYS if key in position}
    meta.update(observed)
    if previous_meta != meta:
        account.save_state()
    if not reason:
        return False
    if entry_mode == "CHANNEL_SWING":
        trigger = decision.get("trigger") if decision else None
        if not channel_swing_exit_allowed(decision.get("type"), trigger):
            if clear_channel_strategy_exit_pending(position, meta):
                meta.update({key: copy.deepcopy(position[key])
                             for key in STATE_KEYS if key in position})
                account.save_state()
            account.log(
                f"TREND_EXIT_GATE symbol={symbol} side={position.get('side')} "
                f"rule=PROFIT_LOCK_OR_VERIFIED_PIVOT reason=HOLD_UNAUTHORIZED_ACCOUNT_POLL "
                f"trigger={trigger}",
                "INFO",
            )
            return False
    trigger = decision.get("trigger") if decision else None
    if (entry_mode == "CHANNEL_SWING"
            and channel_strategy_exit_grace_active(position, meta)
            and not channel_swing_exit_allowed(reason, trigger)):
        if clear_channel_strategy_exit_pending(position, meta):
            meta.update({key: copy.deepcopy(position[key])
                         for key in STATE_KEYS if key in position})
            account.save_state()
        return False
    if (entry_mode == "CHANNEL_SWING"
            and not channel_swing_exit_allowed(reason, decision.get("trigger"))):
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
