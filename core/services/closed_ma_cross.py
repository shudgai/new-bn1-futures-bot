"""Closed 1M MA5/MA15 crossover entries and position-bound opposite exits."""
import math
from core.services.candle_data import closed_entry_candles

CODES = frozenset(('MA5_MA15_CLOSED_CROSS_LONG', 'MA5_MA15_CLOSED_CROSS_SHORT'))
PHASE = 'MA5_MA15_CLOSED_CROSS'
EXIT_REASON = 'EXIT_CLOSED_MA5_MA15_REVERSE_CROSS'
LONG_PERIOD = 60
VOLUME_PERIOD = 20
VOLUME_MULTIPLE = 1.5
MIN_SLOPE_ATR = .05
MIN_BODY_RATIO = .50
MAX_MA5_DISTANCE_ATR = 1.0
EVIDENCE_KEYS = ('cross_bar_id', 'cross_previous_ma5', 'cross_previous_ma15',
                 'cross_ma5', 'cross_ma15', 'cross_ma60', 'cross_previous_ma60',
                 'cross_volume', 'cross_volume_average', 'cross_body_ratio', 'initial_sl',
                 'cross_pivot_bar_id', 'cross_pivot_price', 'cross_ma5_distance_atr')


def closed_cross_evidence(frame):
    """Recompute both averages from closed prices, never cached live indicators."""
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 16:
            return None
        rows = closed.iloc[-16:]
        values = [float(v) for v in rows.close]
        stamps = [float(v) for v in rows.timestamp]
        if (not all(math.isfinite(v) and v > 0 for v in values+stamps)
                or any(b-a != 60000 for a,b in zip(stamps,stamps[1:]))):
            return None
        previous5, previous15 = sum(values[-6:-1])/5, sum(values[-16:-1])/15
        current5, current15 = sum(values[-5:])/5, sum(values[-15:])/15
        direction = None
        if previous5 <= previous15 and current5 > current15:
            direction = 'LONG'
        elif previous5 >= previous15 and current5 < current15:
            direction = 'SHORT'
        return dict(timestamp=stamps[-1], direction=direction, previous_ma5=previous5,
                    previous_ma15=previous15, ma5=current5, ma15=current15)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def evaluate_closed_ma_cross(frame, quote, code=None, symbol='', diagnostics=None):
    def reject(reason):
        if diagnostics is not None:
            diagnostics['reason'] = reason
        return None
    try:
        if code is not None and code not in CODES:
            return None
        closed = closed_entry_candles(frame)
        if len(closed) < VOLUME_PERIOD+1 or len(frame) != len(closed)+1:
            return reject('WAIT_CROSS_CLOSED_HISTORY')
        cross = closed_cross_evidence(frame)
        if not cross or cross['direction'] is None:
            return reject('WAIT_NEW_CLOSED_MA5_MA15_CROSS')
        side = cross['direction'];sign = 1 if side == 'LONG' else -1
        signal = 'MA5_MA15_CLOSED_CROSS_'+side
        if code not in (None,signal):
            return reject('BLOCKED_CROSS_DIRECTION_CHANGED')
        latest = closed.iloc[-1];live = frame.iloc[-1]
        stamp = float(live.timestamp)
        if stamp-cross['timestamp'] != 60000:
            return reject('BLOCKED_CROSS_SIGNAL_EXPIRED')
        prices = [float(v) for v in closed.close.iloc[-61:]]
        atr, quote = float(latest.atr), float(quote)
        if not all(math.isfinite(v) and v > 0 for v in [atr,quote,*prices]):
            return reject('BLOCKED_CROSS_INVALID_DATA')
        # MA60 is diagnostic only; a fresh reversal need not wait for it.
        long_ma = sum(prices[-60:])/60 if len(prices)>=60 else None
        previous_long = sum(prices[-61:-1])/60 if len(prices)>=61 else None
        if sign*(cross['ma5']-cross['previous_ma5']) < MIN_SLOPE_ATR*atr:
            return reject('BLOCKED_CROSS_FLAT_MA5')
        if sign*(quote-cross['ma5']) <= 0:
            return reject('BLOCKED_CROSS_QUOTE_BACK_INSIDE_MA5')
        distance = sign*(quote-cross['ma5'])/atr
        if distance > MAX_MA5_DISTANCE_ATR and not math.isclose(distance,MAX_MA5_DISTANCE_ATR,rel_tol=1e-10):
            return reject('BLOCKED_CROSS_TOO_FAR_FROM_MA5')
        volumes = [float(v) for v in closed.volume.iloc[-21:]]
        if len(volumes) != 21 or not all(math.isfinite(v) and v >= 0 for v in volumes):
            return reject('BLOCKED_CROSS_INVALID_VOLUME')
        average = sum(volumes[:-1])/20
        if average <= 0 or volumes[-1] <= VOLUME_MULTIPLE*average:
            return reject('BLOCKED_CROSS_VOLUME_NOT_EXPANDED')
        o,h,l,c = [float(latest[k]) for k in ('open','high','low','close')]
        if (not all(math.isfinite(v) and v>0 for v in (o,h,l,c))
                or not l <= min(o,c) <= max(o,c) <= h or h <= l):
            return reject('BLOCKED_CROSS_INVALID_BODY')
        ratio = abs(c-o)/(h-l)
        if sign*(c-o) <= 0 or ratio < MIN_BODY_RATIO or abs(c-o) < .5*atr:
            return reject('BLOCKED_CROSS_WEAK_OR_OPPOSITE_BODY')
        # A local turning point is confirmed by one closed neighbour on each side.
        # Search only the 20 completed candles preceding the crossover candle.
        history = closed.iloc[-21:]
        pivot = None
        column = 'low' if side == 'LONG' else 'high'
        for i in range(len(history)-2,0,-1):
            value = float(history.iloc[i][column])
            left, right = float(history.iloc[i-1][column]), float(history.iloc[i+1][column])
            confirmed = (value < left and value < right) if side == 'LONG' else (value > left and value > right)
            if confirmed:
                pivot = (float(history.iloc[i].timestamp), value)
                break
        if pivot is None or sign*(c-pivot[1]) < .5*atr:
            return reject('WAIT_CROSS_CONFIRMED_PIVOT')
        defense = closed.iloc[-6:-1]
        swing = float(defense.low.min() if side == 'LONG' else defense.high.max())
        stop = max(swing,quote-1.5*atr) if side == 'LONG' else min(swing,quote+1.5*atr)
        if not math.isfinite(stop) or stop <= 0 or sign*(quote-stop) <= 0:
            return reject('BLOCKED_CROSS_INVALID_INITIAL_STOP')
        return dict(action='ENTER',side=side,type=signal,reason=signal,price=quote,
                    entry_atr=atr,confirmation_bar_id=stamp,close_price=c,intrabar=True,
                    entry_phase=PHASE,breakout_bar_id=cross['timestamp'],
                    pair_confirmation_bar_id=cross['timestamp'],third_bar_id=stamp,
                    pending_signal_id=f'{symbol}_MA5_MA15_CLOSED_{int(cross["timestamp"])}_{side}',
                    cross_bar_id=cross['timestamp'],cross_previous_ma5=cross['previous_ma5'],
                    cross_previous_ma15=cross['previous_ma15'],cross_ma5=cross['ma5'],
                    cross_ma15=cross['ma15'],cross_ma60=long_ma,cross_previous_ma60=previous_long,
                    cross_volume=volumes[-1],cross_volume_average=average,cross_body_ratio=ratio,
                    initial_sl=stop,cross_pivot_bar_id=pivot[0],cross_pivot_price=pivot[1],
                    cross_ma5_distance_atr=distance)
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):
        return reject('BLOCKED_CROSS_INVALID_DATA')


def opposite_cross_exit_ready(position, snapshot):
    """Only crossover-entry positions gain this independent closing authority."""
    try:
        if (position.get('entry_snapshot') or {}).get('signal_code') not in CODES:
            return False
        if not isinstance(snapshot,dict) or snapshot.get('reason') or snapshot.get('fallback_used'):
            return False
        cross = snapshot.get('closed_ma_cross') or {}
        stamp = float(snapshot['quote_ms']);bar = math.floor(stamp/60000)*60000
        cross_bar = float(cross['timestamp'])
        opened_bar = math.floor(float(position['open_timestamp'])/60)*60000
        return (cross_bar == bar-60000 and cross_bar >= opened_bar
                and cross.get('direction') == ('SHORT' if position['side']=='LONG' else 'LONG'))
    except (KeyError,TypeError,ValueError,OverflowError):
        return False
