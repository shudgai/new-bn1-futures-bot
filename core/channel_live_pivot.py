"""Confirm a live turn from ordered quotes, never from a completed candle wick."""
import math


class LivePivot:
    def __init__(self):
        self.states = {}

    def reset(self, symbol):
        self.states.pop(symbol, None)

    def observe(self, symbol, frame, price, side, now):
        try:
            price, now = float(price), float(now)
            bar = float(frame.iloc[-1]['timestamp']) / 1000
            if (side not in ('LONG', 'SHORT')
                    or not all(math.isfinite(v) and v > 0 for v in (price, now, bar))
                    or bar % 60 or not bar <= now < bar + 60):
                self.reset(symbol)
                return False
            value = price if side == 'LONG' else -price
            identity = (bar, side)
            state = self.states.get(symbol)
            if state is None or state['identity'] != identity or now - state['at'] > 5:
                self.states[symbol] = dict(identity=identity, last=value, extreme=value,
                                           adverse=False, ready=False, at=now)
                return False
            if now < state['at'] or (now == state['at'] and value != state['last']):
                return False
            previous = state['last']
            state.update(last=value, at=now)
            if value < previous:
                state.update(extreme=value, adverse=True, ready=False)
            elif value > previous and state['adverse']:
                state['ready'] = True
            return state['ready']
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            self.reset(symbol)
            return False
