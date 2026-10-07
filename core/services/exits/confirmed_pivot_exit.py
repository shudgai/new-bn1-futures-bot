"""Position-bound completed pivot turns, independent of new-entry rail gates."""
import math

REASON = 'EXIT_CONFIRMED_PIVOT_TURN'
MIN_RUN_ATR = .5
MIN_CONFIRM_BODY_ATR = .1
MIN_BODY_RATIO = .2


def confirmed_pivot_turn(position, state, snapshot, price, sign):
    try:
        rows = snapshot['pivot_exit_history'][-3:]
        if len(rows) != 3 or sign not in (-1, 1):
            return None
        stamps = [float(r['timestamp']) for r in rows]
        candles = [[float(r[k]) for k in ('open','high','low','close')] for r in rows]
        entry, atr, entered, peak, quote_ms = map(float, (
            position['entry_price'], position['entry_atr'], position['open_timestamp'],
            state['peak_price'], snapshot['quote_ms']))
        if not all(math.isfinite(v) and v > 0 for v in [*stamps,entry,atr,entered,peak,price,quote_ms,*[v for r in candles for v in r]]):
            return None
        if (any(b-a != 60000 for a,b in zip(stamps,stamps[1:]))
                or stamps[-1] != snapshot['closed_bar_ms']
                or stamps[1] <= entered*1000 or stamps[-1]+60000 > quote_ms):
            return None
        if any(not low <= min(o,c) <= max(o,c) <= high or high <= low for o,high,low,c in candles):
            return None
        left, pivot, right = candles
        # Favorable extreme of the held side: high for a long, low for a short.
        key = 1 if sign == 1 else 2
        level = pivot[key]
        if not all(sign*(level-r[key]) > 0 for r in (left,right)):
            return None
        if sign*(peak-entry) < MIN_RUN_ATR*atr:
            return None
        adverse_body = -sign*(right[3]-right[0])
        if (adverse_body < MIN_CONFIRM_BODY_ATR*atr
                or adverse_body/(right[1]-right[2]) < MIN_BODY_RATIO
                or sign*(right[3]-pivot[3]) >= 0
                or sign*(price-right[3]) > 0):
            return None
        return dict(pivot_ms=stamps[1], confirmed_ms=stamps[2], pivot_price=level,
                    confirmation_close=right[3], quote_ms=quote_ms, price=price,
                    entry_atr=atr, min_run_atr=MIN_RUN_ATR,
                    min_confirm_body_atr=MIN_CONFIRM_BODY_ATR, confirmation='CLOSED')
    except (KeyError,TypeError,ValueError,IndexError,OverflowError):
        return None
