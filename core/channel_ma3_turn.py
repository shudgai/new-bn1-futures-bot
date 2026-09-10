"""Observe post-entry MA3 peaks/troughs and ignore small reversals."""
import math


def significant_ma3_turn(position, frame, price):
    key = 'channel_significant_ma3_turn'
    try:
        side = position['side']
        opened = float(position['open_timestamp'])
        entry = float(position['entry_price'])
        identity = [side, opened, entry]
        state = position.get(key)
        if state and (state.get('identity') != identity or state.get('version') != 2):
            position.pop(key, None)
            state = None
        if (position.get('channel_profit_protection') or {}).get('armed'):
            position.pop(key, None)
            return False
        if state and state.get('pending'):
            return True
        price = float(price)
        closes = [float(v) for v in frame['close'].iloc[-4:-1]]
        atr = float(frame.iloc[-2]['atr'])
        bar = float(frame.iloc[-1]['timestamp']) / 1000.
        if (side not in ('LONG', 'SHORT') or len(closes) != 3
                or not all(math.isfinite(v) and v > 0 for v in [opened, entry, price, atr, bar, *closes])
                or opened >= bar + 60):
            position.pop(key, None)
            return False
        closed_ma = sum(closes) / 3.
        ma = (sum(closes[-2:]) + price) / 3.
        if not state:
            position[key] = dict(identity=identity, version=2, extreme=ma, threshold=atr * .10,
                                 favorable=False, last_bar=bar, pending=False)
            return False
        if bar < state['last_bar']:
            return False
        state['last_bar'] = bar
        sign = 1 if side == 'LONG' else -1
        advance = sign * (ma - state['extreme'])
        if advance > 0:
            state['extreme'] = ma
            state['favorable'] = state['favorable'] or sign * (ma - closed_ma) > 0
        # A tick retracement alone is not a reversal of the plotted MA3 line.
        elif (state['favorable'] and sign * (ma - closed_ma) < 0
              and -advance >= state['threshold'] - abs(ma) * 1e-12):
            state['pending'] = True
            return True
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        position.pop(key, None)
    return False
