"""Two-mode entry authority (2026-10-13 convergence).

Only two entry families may authorize a new position:

1. KC closed-candle breakout (``KC_BREAKOUT_CLOSE_LONG/SHORT``)
2. Decisive momentum guillotine (``GUILLOTINE_LONG/SHORT``)

Both modes are evaluated on the latest *closed* 1m candle only, so intrabar
wicks and micro rail pierces can never authorize an entry. MA5/MA15 cross
lanes, intrabar rail breaches, continuation and meme pyramid/ambush routes
are retired from automatic entry.
"""
import math

BREAKOUT_CLOSE_LONG_CODE = 'KC_BREAKOUT_CLOSE_LONG'
BREAKOUT_CLOSE_SHORT_CODE = 'KC_BREAKOUT_CLOSE_SHORT'
GUILLOTINE_LONG_CODE = 'GUILLOTINE_LONG'
GUILLOTINE_SHORT_CODE = 'GUILLOTINE_SHORT'

BREAKOUT_CLOSE_CODES = frozenset((BREAKOUT_CLOSE_LONG_CODE, BREAKOUT_CLOSE_SHORT_CODE))
GUILLOTINE_CODES = frozenset((GUILLOTINE_LONG_CODE, GUILLOTINE_SHORT_CODE))
TWO_MODE_ENTRY_CODES = BREAKOUT_CLOSE_CODES | GUILLOTINE_CODES

BREAKOUT_MIN_BODY_ATR = 0.4
BREAKOUT_MAX_WICK_BODY_RATIO = 0.7
GUILLOTINE_MIN_BODY_ATR = 0.85
GUILLOTINE_MIN_BODY_RATIO = 0.70

_CODE_SIDE = {
    BREAKOUT_CLOSE_LONG_CODE: 'LONG',
    BREAKOUT_CLOSE_SHORT_CODE: 'SHORT',
    GUILLOTINE_LONG_CODE: 'LONG',
    GUILLOTINE_SHORT_CODE: 'SHORT',
}


def code_side(code):
    return _CODE_SIDE.get(code)


def _is_closed(row):
    value = row.get('is_closed', True)
    try:
        return bool(value)
    except (TypeError, ValueError):
        return False


def _closed_rows(frame):
    """Return (current_closed, previous_closed) rows or (None, None)."""
    if frame is None or len(frame) < 2:
        return None, None
    if 'is_closed' in frame.columns:
        closed = frame[frame['is_closed'].astype(bool)]
    else:
        closed = frame
    if len(closed) < 2:
        return None, None
    return closed.iloc[-1], closed.iloc[-2]


def _num(row, key):
    value = float(row[key])
    if not math.isfinite(value):
        raise ValueError(key)
    return value


def kc_trend(curr, prev):
    """'UP' / 'DOWN' / 'FLAT' from an explicit kc_trend column or KC middle slope."""
    raw = str(curr.get('kc_trend', '') or '').upper()
    if 'UP' in raw or '向上' in raw:
        return 'UP'
    if 'DOWN' in raw or '向下' in raw:
        return 'DOWN'
    middle, prev_middle = _num(curr, 'kc_middle'), _num(prev, 'kc_middle')
    if middle > prev_middle:
        return 'UP'
    if middle < prev_middle:
        return 'DOWN'
    return 'FLAT'


def _candle(curr):
    opening, close = _num(curr, 'open'), _num(curr, 'close')
    high, low = _num(curr, 'high'), _num(curr, 'low')
    atr = _num(curr, 'atr')
    if (min(opening, close, high, low) <= 0 or atr <= 0
            or high < max(opening, close) or low > min(opening, close)):
        raise ValueError('invalid candle')
    return opening, close, high, low, atr


def _at_least(value, threshold):
    return value >= threshold or math.isclose(value, threshold, rel_tol=1e-9, abs_tol=0.0)


def _at_most(value, threshold):
    return value <= threshold or math.isclose(value, threshold, rel_tol=1e-9, abs_tol=0.0)


def breakout_close_problem(frame, side):
    """Mode 1: KC closed-candle breakout. Return None when qualified."""
    try:
        curr, prev = _closed_rows(frame)
        if curr is None:
            return 'WAIT_CLOSED_CANDLE'
        if not _is_closed(curr):
            return 'BLOCKED_BREAKOUT_REQUIRES_CLOSED_CANDLE'
        opening, close, high, low, atr = _candle(curr)
        trend = kc_trend(curr, prev)
        if side == 'LONG':
            if not (close > _num(curr, 'kc_upper') and close > opening):
                return 'WAIT_CLOSE_ABOVE_KC_UPPER'
            body = close - opening
            wick = high - close
            if trend == 'DOWN':
                return 'BLOCKED_BREAKOUT_LONG_KC_TREND_DOWN'
        elif side == 'SHORT':
            if not (close < _num(curr, 'kc_lower') and close < opening):
                return 'WAIT_CLOSE_BELOW_KC_LOWER'
            body = opening - close
            wick = close - low
            if trend == 'UP':
                return 'BLOCKED_BREAKOUT_SHORT_KC_TREND_UP'
        else:
            return 'BLOCKED_INVALID_ENTRY_SIDE'
        if not _at_least(body, BREAKOUT_MIN_BODY_ATR * atr):
            return 'BLOCKED_BREAKOUT_BODY_TOO_SMALL'
        if not _at_most(wick, BREAKOUT_MAX_WICK_BODY_RATIO * body):
            return 'BLOCKED_BREAKOUT_WICK_TOO_LONG'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_INVALID_ENTRY_DATA'


def guillotine_problem(frame, side):
    """Mode 2: decisive momentum guillotine. Return None when qualified."""
    try:
        curr, _prev = _closed_rows(frame)
        if curr is None:
            return 'WAIT_CLOSED_CANDLE'
        if not _is_closed(curr):
            return 'BLOCKED_GUILLOTINE_REQUIRES_CLOSED_CANDLE'
        opening, close, high, low, atr = _candle(curr)
        body = abs(close - opening)
        candle_range = high - low
        if side == 'LONG' and not close > opening:
            return 'WAIT_GUILLOTINE_LONG_BULLISH_BODY'
        if side == 'SHORT' and not close < opening:
            return 'WAIT_GUILLOTINE_SHORT_BEARISH_BODY'
        if side not in ('LONG', 'SHORT'):
            return 'BLOCKED_INVALID_ENTRY_SIDE'
        if not _at_least(body, GUILLOTINE_MIN_BODY_ATR * atr):
            return 'WAIT_GUILLOTINE_BODY_ATR'
        if candle_range <= 0 or not _at_least(body / candle_range, GUILLOTINE_MIN_BODY_RATIO):
            return 'BLOCKED_GUILLOTINE_BODY_RATIO'
        levels = [_num(curr, key) for key in ('ma5', 'ma15', 'kc_middle') if key in curr]
        if side == 'LONG':
            pierced = any(opening < level < close for level in levels)
        else:
            pierced = any(close < level < opening for level in levels)
        if not pierced:
            return 'WAIT_GUILLOTINE_PIERCE_MA_OR_MIDDLE'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_INVALID_ENTRY_DATA'


def _decision(frame, code, side, quote, symbol):
    curr, _prev = _closed_rows(frame)
    bar_id = float(curr['timestamp']) if 'timestamp' in curr else float(len(frame) - 2)
    is_breakout = code in BREAKOUT_CLOSE_CODES
    phase = 'KC_BREAKOUT_CLOSE_ENTRY' if is_breakout else 'GUILLOTINE_MOMENTUM_ENTRY'
    return {
        'action': 'ENTER',
        'side': side,
        'type': code,
        'reason': code,
        'price': quote,
        'close_price': float(curr['close']),
        'entry_atr': float(curr['atr']),
        'confirmation_bar_id': bar_id,
        'breakout_bar_id': bar_id,
        'pair_confirmation_bar_id': bar_id,
        'entry_phase': phase,
        'strategy_type': 'BREAKOUT' if is_breakout else 'MOMENTUM',
        'bypass_spatial_check': bool(is_breakout),
        'intrabar': False,
        'pending_signal_id': f"{symbol or 'SYM'}:{code}:{int(bar_id)}",
        '_is_authorized': True,
    }


def evaluate_two_mode_entry(frame, quote=None, side=None, code=None, symbol=''):
    """Return (decision, reason). decision is None when no mode qualifies.

    Mode 1 (closed breakout) has priority over Mode 2 (guillotine).
    """
    if code is not None and code not in TWO_MODE_ENTRY_CODES:
        return None, 'BLOCKED_RETIRED_ENTRY_SIGNAL'
    sides = [side] if side in ('LONG', 'SHORT') else ['LONG', 'SHORT']
    if code is not None:
        if side not in (None, code_side(code)):
            return None, 'BLOCKED_ENTRY_SIDE_MISMATCH'
        sides = [code_side(code)]
    if quote is None:
        try:
            quote = float(frame.iloc[-1]['close'])
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            return None, 'BLOCKED_INVALID_ENTRY_DATA'
    reasons = []
    for current_side in sides:
        breakout_code = (BREAKOUT_CLOSE_LONG_CODE if current_side == 'LONG'
                         else BREAKOUT_CLOSE_SHORT_CODE)
        guillotine_code = (GUILLOTINE_LONG_CODE if current_side == 'LONG'
                           else GUILLOTINE_SHORT_CODE)
        if code in (None, breakout_code):
            problem = breakout_close_problem(frame, current_side)
            if problem is None:
                return _decision(frame, breakout_code, current_side, quote, symbol), breakout_code
            reasons.append(problem)
        if code in (None, guillotine_code):
            problem = guillotine_problem(frame, current_side)
            if problem is None:
                return _decision(frame, guillotine_code, current_side, quote, symbol), guillotine_code
            reasons.append(problem)
    blocked = [reason for reason in reasons if reason.startswith('BLOCKED_')]
    return None, (blocked[0] if blocked else (reasons[0] if reasons else 'WAIT_TWO_MODE_ENTRY'))
