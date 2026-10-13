"""Fail-closed three-bar KC breakout gate shared by every entry authority."""
import math

from core.services.candle_data import closed_entry_candles


def three_bar_rail_gate_problem(frame, quote, side):
    """Validate two closed breakout bars and the live/closed third trigger bar.

    A third bar may continue in the breakout direction, or make a small
    counter-direction pullback without breaking the two-bar structure.
    """
    if side not in ('LONG', 'SHORT'):
        return 'BLOCKED_THREE_BAR_INVALID_SIDE'
    if frame is None or frame.empty:
        return 'BLOCKED_THREE_BAR_INVALID_DATA'

    try:
        closed = closed_entry_candles(frame)
        latest = frame.iloc[-1]
        latest_is_closed = bool(latest.get('is_closed', False))
        if latest_is_closed:
            if len(closed) < 3:
                return 'BLOCKED_THREE_BAR_NOT_READY'
            breakout, confirmation, trigger = (closed.iloc[-3], closed.iloc[-2], closed.iloc[-1])
            trigger_price = float(trigger['close']) if quote is None else float(quote)
        else:
            if len(closed) < 2:
                return 'BLOCKED_THREE_BAR_NOT_READY'
            breakout, confirmation = closed.iloc[-2], closed.iloc[-1]
            trigger = latest
            trigger_price = float(quote) if quote is not None else float(trigger['close'])

        keys = ('timestamp', 'open', 'high', 'low', 'close', 'kc_upper', 'kc_lower', 'atr')
        rows = (breakout, confirmation, trigger)
        values = [[float(row[key]) for key in keys] for row in rows]
        if not all(math.isfinite(value) and value > 0 for row in values for value in row):
            return 'BLOCKED_THREE_BAR_INVALID_DATA'
        if not math.isfinite(trigger_price) or trigger_price <= 0:
            return 'BLOCKED_THREE_BAR_INVALID_QUOTE'

        stamps = [row[0] for row in values]
        interval = int(frame.attrs.get('timeframe_ms', 60000))
        if interval <= 0 or not stamps[0] < stamps[1] < stamps[2]:
            return 'BLOCKED_THREE_BAR_INVALID_ORDER'
        if stamps[1] - stamps[0] != interval or stamps[2] - stamps[1] != interval:
            return 'BLOCKED_THREE_BAR_INVALID_ORDER'

        def body(row):
            return row[4] - row[1]

        first_body, second_body = body(values[0]), body(values[1])
        if side == 'LONG':
            if (values[0][1] < values[0][6] or values[0][1] > values[0][5]
                    or first_body <= 0 or values[0][4] <= values[0][5]):
                return 'BLOCKED_THREE_BAR_LONG_NOT_BROKEN_UPPER_RAIL'
            if second_body <= 0:
                return 'BLOCKED_THREE_BAR_LONG_CONFIRMATION_NOT_BULLISH'
            support = min(values[0][3], values[1][3])
            trigger_low = min(float(trigger['low']), trigger_price)
            if trigger_low < support:
                return 'BLOCKED_THREE_BAR_LONG_SUPPORT_BROKEN'
            if trigger_price <= float(trigger['open']):
                pullback_body = float(trigger['open']) - trigger_price
                if pullback_body <= 0 or pullback_body > 0.5 * values[0][7]:
                    return 'BLOCKED_THREE_BAR_LONG_PULLBACK_TOO_LARGE'
            upper = float(trigger['kc_upper'])
            if trigger_price <= upper:
                return 'BLOCKED_THREE_BAR_LONG_QUOTE_BACK_INSIDE_KC'
        else:
            if (values[0][1] > values[0][5] or values[0][1] < values[0][6]
                    or first_body >= 0 or values[0][4] >= values[0][6]):
                return 'BLOCKED_THREE_BAR_SHORT_NOT_BROKEN_LOWER_RAIL'
            if second_body >= 0:
                return 'BLOCKED_THREE_BAR_SHORT_CONFIRMATION_NOT_BEARISH'
            resistance = max(values[0][2], values[1][2])
            trigger_high = max(float(trigger['high']), trigger_price)
            if trigger_high > resistance:
                return 'BLOCKED_THREE_BAR_SHORT_RESISTANCE_BROKEN'
            if trigger_price >= float(trigger['open']):
                pullback_body = trigger_price - float(trigger['open'])
                if pullback_body <= 0 or pullback_body > 0.5 * values[0][7]:
                    return 'BLOCKED_THREE_BAR_SHORT_PULLBACK_TOO_LARGE'
            lower = float(trigger['kc_lower'])
            if trigger_price >= lower:
                return 'BLOCKED_THREE_BAR_SHORT_QUOTE_BACK_INSIDE_KC'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_THREE_BAR_INVALID_DATA'

