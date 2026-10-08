"""Observed live MA5 V/inverted-V reversal with independent price confirmation."""
from decimal import Decimal
import logging
import math
from uuid import uuid4

from core.services.exits.peak_trailing_exit import position_identity

REASON = 'EXIT_OUTER_MA5_V_REVERSAL'
RULE_VERSION = 4
MA5_REVERSAL_ATR = Decimal('0.10')
PRICE_REVERSAL_ATR = Decimal('0.15')
SESSION = str(uuid4())
MAX_GAP_MS = 5000


def closed_reversal_confirmation(position, snapshot, sign):
    from core.services.exits.confirmed_pivot_exit import closed_price_pivot
    try:
        rows = snapshot['ma5_pivot_history'][-3:]
        if len(rows) != 3:
            return 'WAIT_CLOSED_PRICE_MA5_HISTORY', None
        stamps = [float(row['timestamp']) for row in rows]
        candles = [[float(row[key]) for key in ('open', 'high', 'low', 'close')]
                   for row in rows]
        averages = [float(row['ma5']) for row in rows]
        entered = float(position['open_timestamp'])*1000
        if not all(math.isfinite(value) and value > 0
                   for value in [entered, *stamps, *averages, *[v for row in candles for v in row]]):
            return 'BLOCKED_CLOSED_PRICE_MA5_DATA', None
        if (stamps[-1] != snapshot['closed_bar_ms']
                or any(stamp % 60000 != 0 for stamp in stamps)
                or any(b-a != 60000 for a, b in zip(stamps, stamps[1:]))):
            return 'BLOCKED_CLOSED_PRICE_MA5_IDENTITY', None
        if stamps[1] < entered:
            return 'WAIT_POST_ENTRY_CLOSED_PRICE_PIVOT', None
        if any(not low <= min(o, c) <= max(o, c) <= high or high <= low
               for o, high, low, c in candles):
            return 'BLOCKED_CLOSED_PRICE_MA5_DATA', None
        level = closed_price_pivot(candles, sign)
        if level is None:
            return 'WAIT_CLOSED_PRICE_PIVOT', None
        if sign*(averages[2]-averages[1]) >= -max(averages)*1e-12:
            return 'WAIT_CLOSED_MA5_REVERSE_SLOPE', None
        kc_rows = snapshot['kc_closed_history'][-2:]
        if len(kc_rows) != 2:
            return 'BLOCKED_CLOSED_KC_HISTORY', None
        kc_stamps = [float(row['timestamp']) for row in kc_rows]
        middle = [float(row['middle']) for row in kc_rows]
        if (not all(math.isfinite(value) and value > 0 for value in [*kc_stamps, *middle])
                or kc_stamps != stamps[-2:]):
            return 'BLOCKED_CLOSED_KC_HISTORY', None
        if sign*(middle[1]-middle[0]) >= -max(middle)*1e-12:
            return 'WAIT_CLOSED_KC_REVERSE_DIRECTION', None
        return 'CONFIRMED', dict(
            confirmation='CLOSED', pivot_ms=stamps[1], confirmed_ms=stamps[2],
            pivot_price=level, previous_closed_ma5=averages[1], closed_ma5=averages[2],
            kc_confirmation='CLOSED_OPPOSITE',
            previous_closed_kc_middle=middle[0], closed_kc_middle=middle[1])
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_CLOSED_PRICE_MA5_DATA', None


def observe_live_ma5_v(position, state, snapshot, price):
    def hold(status):
        if status.startswith('BLOCKED_') and state.get('live_ma5_v_status') != status:
            logging.getLogger('LiveMA5VExit').warning(
                '%s symbol=%s side=%s', status, position.get('symbol'), position.get('side'))
        state['live_ma5_v_status'] = status
        return None

    try:
        identity = position_identity(position)
        stamp, ma5, atr, price = map(float, (
            snapshot['quote_ms'], snapshot['ma5'], snapshot['atr'], price))
        lower, upper = float(snapshot['kc_lower']), float(snapshot['kc_upper'])
        bar = math.floor(stamp/60000)*60000
        if (snapshot.get('reason') or snapshot.get('fallback_used')
                or snapshot.get('live_ma5_verified') is not True
                or snapshot.get('live_bar_ms') != bar
                or snapshot.get('closed_bar_ms') != bar-60000
                or stamp < identity[1]*1000
                or not all(math.isfinite(v) and v > 0 for v in (stamp, ma5, atr, price, lower, upper))
                or lower >= upper):
            return hold('BLOCKED_LIVE_MA5_V_DATA')
        observed = state.get('live_ma5_v_observation') or {}
        if observed.get('identity') != identity or observed.get('rule_version') != RULE_VERSION:
            observed = dict(identity=identity, rule_version=RULE_VERSION, atr=atr)
        if not math.isfinite(float(observed['atr'])) or float(observed['atr']) <= 0:
            return hold('BLOCKED_LIVE_MA5_V_STATE')
        if (observed.get('session') != SESSION
                or stamp-observed.get('last_ms', stamp) > MAX_GAP_MS):
            observed = dict(identity=identity, rule_version=RULE_VERSION, atr=observed['atr'],
                            session=SESSION, first_ma5=ma5, ma5_extreme=ma5,
                            price_extreme=price, previous_ma5=ma5, last_ms=stamp,
                            favorable=False, outer_extreme=None)
            state['live_ma5_v_observation'] = observed
            return hold('WAIT_POST_ENTRY_MA5_PROGRESSION')
        if not all(math.isfinite(float(observed[key])) and float(observed[key]) > 0
                   for key in ('first_ma5', 'ma5_extreme', 'price_extreme', 'previous_ma5', 'last_ms')):
            return hold('BLOCKED_LIVE_MA5_V_STATE')
        if stamp <= observed['last_ms']:
            return hold('WAIT_NEW_MA5_OBSERVATION')
        sign = 1 if position['side'] == 'LONG' else -1
        tolerance = max(ma5, observed['previous_ma5']) * 1e-12
        step = sign*(ma5-observed['previous_ma5'])
        extreme = max if sign == 1 else min
        prior_extreme = observed['ma5_extreme']
        observed['ma5_extreme'] = extreme(ma5, prior_extreme)
        observed['price_extreme'] = extreme(price, observed['price_extreme'])
        edge = upper if sign == 1 else lower
        if sign*(ma5-prior_extreme) > 0:
            observed['outer_extreme'] = None
        if ma5 == observed['ma5_extreme'] and sign*(ma5-edge) >= 0:
            observed['outer_extreme'] = dict(ma5=ma5, edge=edge, quote_ms=stamp, bar=bar)
        if step > tolerance:
            observed['favorable'] = True
        observed.update(previous_ma5=ma5, last_ms=stamp)
        state['live_ma5_v_observation'] = observed
        if not observed['favorable']:
            return hold('WAIT_POST_ENTRY_MA5_PROGRESSION')
        if step >= -tolerance:
            return hold('WAIT_ACTUAL_MA5_REVERSE_SLOPE')
        if not observed.get('outer_extreme'):
            return hold('WAIT_MA5_EXTREME_AT_OUTER_RAIL')
        scale = Decimal(str(observed['atr']))
        ma5_retreat = sign*(Decimal(str(observed['ma5_extreme']))-Decimal(str(ma5)))
        price_retreat = sign*(Decimal(str(observed['price_extreme']))-Decimal(str(price)))
        if ma5_retreat < MA5_REVERSAL_ATR*scale:
            return hold('WAIT_MA5_REVERSAL_AMPLITUDE')
        if price_retreat < PRICE_REVERSAL_ATR*scale:
            return hold('WAIT_PRICE_REVERSAL_AMPLITUDE')
        status, closed_confirmation = closed_reversal_confirmation(position, snapshot, sign)
        if closed_confirmation is None:
            return hold(status)
        pivot_price = closed_confirmation['pivot_price']
        if sign*(observed['price_extreme']-pivot_price) > max(observed['price_extreme'], pivot_price)*1e-12:
            return hold('WAIT_NEW_CLOSED_PRICE_PIVOT')
        state['live_ma5_v_status'] = 'CONFIRMED'
        return dict(rule_version=RULE_VERSION, identity=identity, quote_ms=stamp,
                    closed_confirmation=closed_confirmation,
                    outer_extreme=observed['outer_extreme'],
                    atr=observed['atr'], ma5=ma5, ma5_extreme=observed['ma5_extreme'],
                    price=price, price_extreme=observed['price_extreme'],
                    ma5_reversal=str(ma5_retreat), price_reversal=str(price_retreat),
                    ma5_threshold=str(MA5_REVERSAL_ATR*scale),
                    price_threshold=str(PRICE_REVERSAL_ATR*scale))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return hold('BLOCKED_LIVE_MA5_V_DATA')
