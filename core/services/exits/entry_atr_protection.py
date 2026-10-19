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
    short_trend_hold = False
    frame = None
    if is_auto_entry_symbol(symbol):
        frame_provider = getattr(account, "channel_exit_frame_provider", None)
        frame = frame_provider(symbol) if callable(frame_provider) else None
        if frame is None:
            frame = getattr(account, "_channel_exit_frames", {}).get(symbol)
        from core.services.exits.kc_true_extreme_exit import (
            evaluate_kc_true_extreme_exit,
        )
        stamp_ms = (
            float(frame.iloc[-1]["timestamp"]) + 2000
            if frame is not None and len(frame) > 0 and "timestamp" in frame.iloc[-1]
            else time.time() * 1000
        )
        kc_exit, state_changed = evaluate_kc_true_extreme_exit(
            position, frame, price, stamp_ms, meta,
        )
        if state_changed:
            account.save_state()
        if kc_exit is not None:
            exit_reason, exit_details = kc_exit
            side = str(position.get("side", "")).upper()
            exit_cause = str(exit_details.get("cause") or "")

            # ═════════════════════════════════════════════════════════════════
            # 【趨勢保護鎖 Trend Shield：趨勢向上/向下中途回踩強制不平倉】
            # ═════════════════════════════════════════════════════════════════
            trend_shield_hold = False
            if frame is not None and len(frame) >= 1:
                curr_bar = frame.iloc[-1]
                prev_bar = frame.iloc[-2] if len(frame) >= 2 else curr_bar
                close_p = float(curr_bar.get("close", price))
                mid_p = float(curr_bar.get("kc_middle", curr_bar.get("kc_basis", 0.0)))
                prev_mid_p = float(prev_bar.get("kc_middle", mid_p))
                ma15_p = float(curr_bar.get("ma15", mid_p))
                upper_p = float(curr_bar.get("kc_upper", 0.0))
                lower_p = float(curr_bar.get("kc_lower", 0.0))

                from core.services.market_signal_analyzer import evaluate_frame_market_signal
                market_signal = evaluate_frame_market_signal(frame, price)
                if side == "LONG":
                    # 大趨勢依然向上：KC 中軌走平或向上，且收盤/即時價守在 KC 中軌與 MA15 之上
                    is_trend_up = (mid_p >= prev_mid_p - 0.0001) and (close_p >= min(mid_p, ma15_p) or price >= min(mid_p, ma15_p))
                    # 或者依然在 KC 上軌外狂飆
                    is_outer_run = upper_p > 0 and (close_p > upper_p or price > upper_p)
                    # 或者符合多頭健康回調續抱 (HOLD_LONG)
                    is_market_hold = (market_signal.get("action") == "HOLD_LONG")
                    if is_trend_up or is_outer_run or is_market_hold:
                        trend_shield_hold = True
                elif side == "SHORT":
                    # 大趨勢依然向下：KC 中軌走平或向下，且收盤/即時價壓在 KC 中軌與 MA15 之下
                    is_trend_down = (mid_p <= prev_mid_p + 0.0001) and (close_p <= max(mid_p, ma15_p) or price <= max(mid_p, ma15_p))
                    # 或者依然在 KC 下軌外狂跌
                    is_outer_run = lower_p > 0 and (close_p < lower_p or price < lower_p)
                    # 或者符合空頭次級反彈續抱 (HOLD_SHORT)
                    is_market_hold = (market_signal.get("action") == "HOLD_SHORT")
                    if is_trend_down or is_outer_run or is_market_hold:
                        trend_shield_hold = True

            # 只要趨勢保護鎖生效，中途的盤中回踩或普通觸軌一律強制不平倉，繼續死抱主升/主跌段！
            if trend_shield_hold and exit_cause in ("INTRADAY_PEAK_PULLBACK", "INTRADAY_TROUGH_REBOUND", "KC_MIDLINE_TOUCH"):
                account.log(
                    f"🛡️ [TREND_SHIELD] symbol={symbol} side={side} "
                    f"大趨勢保持完整 ({market_signal.get('status', '')})，中途回踩強制不平倉！繼續抱牢主波段 (cause={exit_cause})",
                    "INFO",
                )
                state = position.get("kc_true_extreme_exit_state")
                if isinstance(state, dict):
                    state["pending"] = None
                meta_state = meta.get("kc_true_extreme_exit_state")
                if isinstance(meta_state, dict):
                    meta_state["pending"] = None
                return False

            account.log(
                f"KC_STRUCTURE_EXIT symbol={symbol} "
                f"side={position.get('side')} reason={exit_reason} "
                f"details={exit_details}",
                "WARNING",
            )
            closed = await account.close_position(
                symbol, price, f"{entry_mode} {exit_reason}", is_manual=True,
            )
            if not closed:
                account.log(
                    f"KC_STRUCTURE_EXIT_RETRY_PENDING symbol={symbol} "
                    f"reason={exit_reason}",
                    "WARNING",
                )
            return True

        if entry_mode == "CHANNEL_SWING" and position.get("side") == "SHORT":
            from core.services.exits.short_oversold_exit import (
                STATE_KEY as SHORT_OVERSOLD_STATE_KEY,
                TREND_HOLD_STATE_KEY,
                short_bearish_trend_in_frame,
            )
            short_trend_hold = short_bearish_trend_in_frame(frame) is not False
            position[TREND_HOLD_STATE_KEY] = short_trend_hold
            meta[TREND_HOLD_STATE_KEY] = short_trend_hold
            oversold_state = (
                position.get(SHORT_OVERSOLD_STATE_KEY)
                or meta.get(SHORT_OVERSOLD_STATE_KEY)
            )
            if isinstance(oversold_state, dict):
                oversold_state["bearish_trend_hold_active"] = short_trend_hold
                if short_trend_hold:
                    oversold_state["partial_exit_pending"] = False
                    oversold_state["reversal_confirmed"] = False
                    oversold_state["full_exit_pending"] = False
                    oversold_state.pop("reversal_bar_ms", None)
                    oversold_state.pop("full_exit_price", None)
                position[SHORT_OVERSOLD_STATE_KEY] = copy.deepcopy(oversold_state)
                meta[SHORT_OVERSOLD_STATE_KEY] = copy.deepcopy(oversold_state)

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

    if is_auto_entry_symbol(symbol) and reason:
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
