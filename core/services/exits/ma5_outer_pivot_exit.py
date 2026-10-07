"""Price-pivot/closed-MA5 exits and shared outer-MA5 entry geometry."""
import logging
import math

REASON = 'EXIT_CLOSED_PRICE_PIVOT_MA5_REVERSE'
RULE_VERSION = 2


def closed_outer_ma5_turn(values, sign):
    """Classify three validated completed OHLC/MA5/rail rows."""
    left, pivot, right = [row[4] for row in values]
    tolerance = max(left, pivot, right) * 1e-12
    if sign * (pivot - left) <= tolerance or sign * (pivot - right) <= tolerance:
        return 'WAIT_CLOSED_MA5_REVERSAL', None
    rail = values[1][6 if sign == 1 else 5]
    if sign * (pivot - rail) <= max(pivot, rail) * 1e-12:
        return 'WAIT_MA5_PIVOT_OUTSIDE_RAIL', None
    return 'CONFIRMED', dict(pivot_ma5=pivot, pivot_rail=rail, confirmation_ma5=right)


def confirmed_ma5_outer_pivot(position, state, snapshot, sign):
    """Compatibility name: exits now require price geometry, not outer MA5."""
    def hold(status):
        if status.startswith('BLOCKED_') and state.get('ma5_pivot_status') != status:
            logging.getLogger('MA5OuterPivot').warning(
                '%s symbol=%s side=%s', status, position.get('symbol'), position.get('side'))
        state['ma5_pivot_status'] = status
        return None

    try:
        rows = snapshot['ma5_pivot_history'][-3:]
        if len(rows) != 3:
            return hold('BLOCKED_MA5_HISTORY')
        stamps = [float(row['timestamp']) for row in rows]
        values = [[float(row[key]) for key in
                   ('open', 'high', 'low', 'close', 'ma5', 'kc_lower', 'kc_upper')]
                  for row in rows]
        entered = float(position['open_timestamp']) * 1000
        stamp = float(snapshot['quote_ms'])
        if sign not in (-1, 1) or not all(
                math.isfinite(value) and value > 0
                for value in [entered, stamp, *stamps, *[v for row in values for v in row]]):
            return hold('BLOCKED_MA5_MARKET_DATA')
        live_bar = math.floor(stamp / 60000) * 60000
        if (snapshot.get('reason') or snapshot.get('fallback_used')
                or snapshot['live_bar_ms'] != live_bar
                or snapshot['closed_bar_ms'] != live_bar - 60000
                or stamps[-1] != live_bar - 60000
                or any(b - a != 60000 for a, b in zip(stamps, stamps[1:]))
                or any(value % 60000 != 0 for value in stamps)):
            return hold('BLOCKED_MA5_CANDLE_IDENTITY')
        if stamps[0] < entered:
            return hold('WAIT_POST_ENTRY_MA5_HISTORY')
        for opened, high, low, close, _, lower, upper in values:
            if not low <= min(opened, close) <= max(opened, close) <= high or high <= low or lower >= upper:
                return hold('BLOCKED_MA5_MARKET_DATA')
        from core.services.exits.confirmed_pivot_exit import closed_price_pivot
        pivot_price = closed_price_pivot(values, sign)
        if pivot_price is None:
            return hold('WAIT_CLOSED_PRICE_PIVOT')
        previous, current = values[1][4], values[2][4]
        if sign*(previous-current) <= max(previous, current)*1e-12:
            return hold('WAIT_CLOSED_MA5_REVERSAL')
        state['ma5_pivot_status'] = 'CONFIRMED'
        return dict(rule_version=RULE_VERSION, pivot_ms=stamps[1], confirmed_ms=stamps[2],
                    pivot_price=pivot_price, previous_ma5=previous,
                    confirmation_ma5=current,
                    confirmation='CLOSED', quote_ms=stamp)
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return hold('BLOCKED_MA5_MARKET_DATA')
