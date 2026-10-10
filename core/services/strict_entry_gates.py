"""Shared fail-closed entry gates for every automatic Channel Swing trigger."""
import math

from core import config
from core.services.candle_data import closed_entry_candles

POLICY = 'strict_entry_gates_20261009_v1'


def validate_strict_entry(frame, quote, side, *, entry_mode='BREAKOUT'):
    """Return auditable live-quote evidence or a specific rejection reason."""
    evidence = {'policy': POLICY, 'side': side}
    def reject(reason):
        evidence['passed'] = False
        evidence['reason'] = reason
        return False, reason, evidence
    try:
        if side not in ('LONG', 'SHORT'):
            return reject('BLOCKED_STRICT_INVALID_SIDE')
        if entry_mode not in ('BREAKOUT', 'PULLBACK'):
            return reject('BLOCKED_STRICT_INVALID_ENTRY_MODE')
        closed = closed_entry_candles(frame)
        if len(closed) < 60 or bool(frame.iloc[-1].get('is_closed', True)):
            return reject('BLOCKED_STRICT_INSUFFICIENT_DATA')
        live, previous = frame.iloc[-1], closed.iloc[-1]
        history = closed.tail(60)
        keys = ['timestamp', 'open', 'high', 'low', 'close', 'atr',
                'kc_lower', 'kc_middle', 'kc_upper', 'ma5', 'ma15']
        for _, bar in history.iterrows():
            if not all(math.isfinite(float(bar[k])) and float(bar[k]) > 0 for k in keys):
                return reject('BLOCKED_STRICT_INVALID_DATA')
            if not (bar['kc_lower'] < bar['kc_middle'] < bar['kc_upper']
                    and bar['low'] <= min(bar['open'], bar['close'])
                    and bar['high'] >= max(bar['open'], bar['close'])):
                return reject('BLOCKED_STRICT_INVALID_DATA')
        if not (history['timestamp'].diff().dropna() == 60000).all():
            return reject('BLOCKED_STRICT_NONCONTIGUOUS_DATA')
        if float(live['timestamp']) != float(previous['timestamp']) + 60000:
            return reject('BLOCKED_STRICT_STALE_LIVE_BAR')
        quote = float(quote)
        if not all(math.isfinite(float(live[k])) and float(live[k]) > 0 for k in keys):
            return reject('BLOCKED_STRICT_INVALID_LIVE_DATA')
        if not math.isfinite(quote) or quote <= 0:
            return reject('BLOCKED_STRICT_INVALID_QUOTE')
        if not (float(live['low']) <= min(float(live['open']), float(live['close']))
                and float(live['high']) >= max(float(live['open']), float(live['close']))):
            return reject('BLOCKED_STRICT_INVALID_LIVE_DATA')
        lower, middle, upper = map(float, (live['kc_lower'], live['kc_middle'], live['kc_upper']))
        if not lower < middle < upper:
            return reject('BLOCKED_STRICT_INVALID_LIVE_DATA')
        sign = 1 if side == 'LONG' else -1
        opening = float(live['open'])
        atr = float(previous['atr'])
        edge = upper if sign == 1 else lower
        live_ma5 = float(live['ma5']) + (quote - float(live['close'])) / 5.
        live_ma15 = float(live['ma15']) + (quote - float(live['close'])) / 15.
        distance = sign * (quote - edge) / atr
        body_atr = sign * (quote - opening) / atr
        width = upper - lower
        max_width = float((closed.tail(20)['kc_upper'] - closed.tail(20)['kc_lower']).max())
        live_span = max(float(live['high']), quote) - min(float(live['low']), quote)
        if entry_mode != 'PULLBACK' and (live_span <= 0 or abs(quote-opening) / live_span <= .2):
            return reject('BLOCKED_STRICT_LIVE_DOJI')
        evidence.update(quote=quote, live_bar_ms=float(live['timestamp']), opened=opening,
                        previous_atr=atr, kc_lower=lower, kc_middle=middle, kc_upper=upper,
                        body_atr=body_atr, distance_atr=distance,
                        previous_ma5=float(previous['ma5']), live_ma5=live_ma5,
                        live_ma15=live_ma15, channel_width=width, max_width_20=max_width,
                        ma_spacing=abs(live_ma5-live_ma15), rail_spacing=abs(live_ma5-edge))
        if entry_mode == 'PULLBACK':
            if not lower < quote < upper:
                return reject('BLOCKED_STRICT_PULLBACK_NOT_INSIDE_KC')
        elif distance <= 0:
            return reject('BLOCKED_STRICT_QUOTE_INSIDE_KC')
        if (sign == 1 and opening < lower) or (sign == -1 and opening > upper):
            return reject('BLOCKED_STRICT_OPPOSITE_GAP')
        if entry_mode != 'PULLBACK' and body_atr < 0.5:
            return reject('BLOCKED_STRICT_BODY_BELOW_HALF_ATR')
        if entry_mode != 'PULLBACK' and distance > 3.:
            return reject('BLOCKED_STRICT_DISTANCE_ABOVE_3_ATR')
        if sign * (live_ma5 - float(previous['ma5'])) <= 0:
            return reject('BLOCKED_STRICT_MA5_DIRECTION')
        if width <= 0.25 * max_width:
            return reject('BLOCKED_STRICT_CHANNEL_CONVERGENCE')
        if abs(live_ma5-live_ma15) < 0.25 * width:
            return reject('BLOCKED_STRICT_MA_SPACING')
        if abs(live_ma5-edge) < 0.25 * width:
            return reject('BLOCKED_STRICT_RAIL_SPACING')
        continuation = opening > upper if sign == 1 else opening < lower
        evidence['opening_context'] = 'SAME_SIDE_CONTINUATION' if continuation else 'IN_CHANNEL_BREAKOUT'
        if continuation:
            prior_edge = float(previous['kc_upper' if sign == 1 else 'kc_lower'])
            if sign * (float(previous['close']) - prior_edge) <= 0:
                return reject('BLOCKED_STRICT_PREVIOUS_NOT_OUTSIDE')
            if sign * (float(previous['kc_middle']) - float(closed.iloc[-2]['kc_middle'])) <= 0:
                return reject('BLOCKED_STRICT_CK_DIRECTION')
            if sign * (quote - live_ma5) <= 0:
                return reject('BLOCKED_STRICT_QUOTE_INSIDE_MA5')
        evidence.update(passed=True, reason='STRICT_ENTRY_GATES_PASSED')
        evidence.update(passed=True, reason='STRICT_ENTRY_GATES_PASSED')
        return True, 'STRICT_ENTRY_GATES_PASSED', evidence
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return reject('BLOCKED_STRICT_INVALID_DATA')
