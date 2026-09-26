"""Observe a post-entry price turn before permitting an abnormal-body exit."""
import math

from core.config import RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR
from core.services.candle_data import closed_entry_candles


def peak_abnormal_exit(position: dict, frame, price: float) -> str | None:
    key = 'channel_peak_abnormal'
    side = position.get('side')
    identity = [side, position.get('open_timestamp'), position.get('entry_price')]
    state = position.get(key, {})
    if state.get('identity') != identity:
        state = {}
    if state.get('pending'):
        return 'PEAK_CONFIRMED_ADVERSE_ABNORMAL_EXIT'
    try:
        closed = closed_entry_candles(frame)
        if closed.empty or side not in ('LONG', 'SHORT'):
            return None
        price, entry = float(price), float(position['entry_price'])
        opened = float(position['open_timestamp'])
        live = frame.iloc[-1]
        bar, candle_open = float(live['timestamp']) / 1000., float(live['open'])
        atr = float(closed.iloc[-1]['atr'])
        if (not all(math.isfinite(v) and v > 0 for v in (price, entry, opened, bar, candle_open, atr))
                or opened >= bar + 60):
            return None
        sign = 1 if side == 'LONG' else -1
        if not state:
            position[key] = dict(identity=identity, last=price, extreme=price,
                                 favorable=False, confirmed=False, pending=False)
            return None
        move = sign * (price - state['last'])
        if sign * (price - state['extreme']) > 0:
            state.update(extreme=price, confirmed=False)
        if move > 0 and sign * (price - entry) > 0:
            state['favorable'] = True
        if state['favorable'] and move < 0:
            state['confirmed'] = True
        state['last'] = price
        threshold = atr * RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR
        adverse = -sign * (price - candle_open)
        if state['confirmed'] and threshold > 0 and adverse >= threshold:
            state['pending'] = True
            return 'PEAK_CONFIRMED_ADVERSE_ABNORMAL_EXIT'
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return None
