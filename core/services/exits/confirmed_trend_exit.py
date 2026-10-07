"""Closed MA5 directional failure; entry qualification never authorizes this exit."""
import math

REASON = 'EXIT_CONFIRMED_TREND_REVERSAL'
MIN_TURN_ATR = 0.05


def confirmed_reversal(position, snapshot, price, sign):
    try:
        rows = snapshot['closed_trend_history'][-2:]
        if len(rows) != 2 or sign not in (-1,1):
            return None
        previous, latest = rows
        stamps = [float(row['timestamp']) for row in rows]
        ma5 = [float(row['ma5']) for row in rows]
        close, atr, entered, quote_ms = map(float, (latest['close'], latest['atr'],
                position['open_timestamp'], snapshot['quote_ms']))
        if (not all(math.isfinite(v) and v>0 for v in [*stamps,*ma5,close,atr,entered,quote_ms,price])
                or stamps[1]-stamps[0] != 60000
                or stamps[1] != float(snapshot['closed_bar_ms'])
                or not entered*1000 < stamps[1]+60000 <= quote_ms):
            return None
        adverse = -sign*(ma5[1]-ma5[0])
        if (adverse < MIN_TURN_ATR*atr
                or sign*(close-ma5[1]) >= 0 or sign*(price-ma5[1]) >= 0):
            return None
        return dict(confirmation='CLOSED', closed_bar_ms=stamps[1], previous_ma5=ma5[0],
                    closed_ma5=ma5[1], closed_close=close, closed_atr=atr,
                    adverse_ma5=adverse, min_turn_atr=MIN_TURN_ATR,
                    quote_ms=quote_ms, price=price)
    except (KeyError,TypeError,ValueError,IndexError,OverflowError):
        return None
