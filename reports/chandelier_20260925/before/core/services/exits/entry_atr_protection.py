"""Fixed entry-ATR protection, initialized from the actual fill price."""
import math

STATE_KEYS = ("entry_atr", "atr_sl", "atr_tp", "atr_protection_version")


def valid_entry_atr(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def initialize_atr_protection(position, entry_price, side, atr):
    entry_price, atr = float(entry_price), float(atr)
    if side not in ("LONG", "SHORT") or not all(valid_entry_atr(v) for v in (entry_price, atr)):
        raise ValueError("Invalid entry ATR protection inputs")
    sign = 1 if side == "LONG" else -1
    stop, target = entry_price - sign * 1.5 * atr, entry_price + sign * 2.0 * atr
    if min(stop, target) <= 0:
        raise ValueError("Invalid entry ATR protection prices")
    position.update(entry_atr=atr, atr_sl=stop, atr_tp=target,
                    atr_protection_version=1, sl=stop, tp=target,
                    initial_sl=stop, initial_risk=1.5 * atr)


def atr_exit_reason(position, price, frame=None):
    """Anchored TP/SL and post-entry closed middle break; persist failed exits."""
    if not valid_entry_atr(price):
        return None
    if position.get('atr_protection_version') != 1:
        # Only use the persisted entry snapshot; never derive ATR from later candles.
        if not valid_entry_atr(position.get('entry_atr')):
            return None
        try:
            initialize_atr_protection(position, position['entry_price'], position['side'], position['entry_atr'])
        except (KeyError, TypeError, ValueError):
            return None
    state = position.setdefault('channel_profit_protection', {})
    allowed = ('EXIT_STOP_LOSS: 1.5 ATR', 'EXIT_TAKE_PROFIT: 2.0 ATR', 'EXIT_KC_MIDDLE_CLOSED')
    if state.get('policy') != 'ma_cross_atr_v1':
        pending = state.get('reason') if state.get('pending') and state.get('reason') in allowed else None
        state.clear()
        state.update(policy='ma_cross_atr_v1', pending=bool(pending))
        if pending:
            state['reason'] = pending
    if state.get('pending') and state.get('reason') in allowed:
        return state['reason']
    side = position.get('side')
    stop, target = position.get('atr_sl'), position.get('atr_tp')
    if side not in ('LONG', 'SHORT') or not all(valid_entry_atr(v) for v in (stop, target)):
        return None
    sign = 1 if side == 'LONG' else -1
    reason = None
    if sign * (float(price) - float(target)) >= 0:
        reason = 'EXIT_TAKE_PROFIT: 2.0 ATR'
    elif sign * (float(price) - float(stop)) <= 0:
        reason = 'EXIT_STOP_LOSS: 1.5 ATR'
    elif frame is not None:
        from core.services.candle_data import closed_entry_candles
        closed = closed_entry_candles(frame)
        if not closed.empty:
            try:
                c2 = closed.iloc[-1]
                close = float(c2['close'])
                middle = float(c2['kc_middle'] if 'kc_middle' in c2 else c2['ema_20'])
                completed = float(c2['timestamp']) + float(frame.attrs.get('timeframe_ms', 60000))
                opened = float(position['open_timestamp']) * 1000
                if (all(math.isfinite(v) and v > 0 for v in (close, middle, completed, opened))
                        and completed > opened and sign * (close-middle) < 0):
                    reason = 'EXIT_KC_MIDDLE_CLOSED'
            except (KeyError, TypeError, ValueError):
                pass
    if reason:
        state.update(pending=True, reason=reason)
    return reason


async def enforce_atr_protection(account, symbol, price):
    """Account updates also enforce the lines when the strategy scan is delayed."""
    import copy
    position = account.positions.get(symbol)
    if not position:
        return False
    reason = atr_exit_reason(position, price)
    if not reason:
        return False
    account.position_meta.setdefault(symbol, {})["channel_profit_protection"] = copy.deepcopy(
        position["channel_profit_protection"])
    account.save_state()
    await account.close_position(symbol, price, "Channel Swing " + reason, is_manual=True)
    return True
