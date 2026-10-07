"""Observed live MA5 V/inverted-V reversal with independent price confirmation."""
from decimal import Decimal
import logging
import math
from uuid import uuid4

from core.services.exits.peak_trailing_exit import position_identity

REASON = 'EXIT_LIVE_MA5_V_REVERSAL'
RULE_VERSION = 1
MA5_REVERSAL_ATR = Decimal('0.10')
PRICE_REVERSAL_ATR = Decimal('0.15')
SESSION = str(uuid4())
MAX_GAP_MS = 5000


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
        bar = math.floor(stamp/60000)*60000
        if (snapshot.get('reason') or snapshot.get('fallback_used')
                or snapshot.get('live_ma5_verified') is not True
                or snapshot.get('live_bar_ms') != bar
                or snapshot.get('closed_bar_ms') != bar-60000
                or stamp < identity[1]*1000
                or not all(math.isfinite(v) and v > 0 for v in (stamp, ma5, atr, price))):
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
                            favorable=False)
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
        observed['ma5_extreme'] = extreme(ma5, observed['ma5_extreme'])
        observed['price_extreme'] = extreme(price, observed['price_extreme'])
        if step > tolerance:
            observed['favorable'] = True
        observed.update(previous_ma5=ma5, last_ms=stamp)
        state['live_ma5_v_observation'] = observed
        if not observed['favorable']:
            return hold('WAIT_POST_ENTRY_MA5_PROGRESSION')
        if step >= -tolerance:
            return hold('WAIT_ACTUAL_MA5_REVERSE_SLOPE')
        scale = Decimal(str(observed['atr']))
        ma5_retreat = sign*(Decimal(str(observed['ma5_extreme']))-Decimal(str(ma5)))
        price_retreat = sign*(Decimal(str(observed['price_extreme']))-Decimal(str(price)))
        if ma5_retreat < MA5_REVERSAL_ATR*scale:
            return hold('WAIT_MA5_REVERSAL_AMPLITUDE')
        if price_retreat < PRICE_REVERSAL_ATR*scale:
            return hold('WAIT_PRICE_REVERSAL_AMPLITUDE')
        state['live_ma5_v_status'] = 'CONFIRMED'
        return dict(rule_version=RULE_VERSION, identity=identity, quote_ms=stamp,
                    atr=observed['atr'], ma5=ma5, ma5_extreme=observed['ma5_extreme'],
                    price=price, price_extreme=observed['price_extreme'],
                    ma5_reversal=str(ma5_retreat), price_reversal=str(price_retreat),
                    ma5_threshold=str(MA5_REVERSAL_ATR*scale),
                    price_threshold=str(PRICE_REVERSAL_ATR*scale))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return hold('BLOCKED_LIVE_MA5_V_DATA')
