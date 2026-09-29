"""Closed bearish impulse priority and bounded, matched-fill reversal."""
import math
import time

from core.services.candle_data import closed_entry_candles, closed_entry_problem

CODE = 'CLOSED_BEAR_IMPULSE_SHORT'
BODY_ATR = 0.8
MID_PROXIMITY_ATR = 0.25


def short_problem(frame, price):
    closed = closed_entry_candles(frame)
    if len(closed) < 2 or closed_entry_problem(closed):
        return 'WAIT_INVALID_MARKET_DATA'
    try:
        row = closed.iloc[-1]
        atr = float(closed.iloc[-2]['atr'])
        price, rail = float(price), float(frame.iloc[-1]['kc_lower'])
        close, closed_rail = float(row['close']), float(row['kc_lower'])
        if not all(math.isfinite(v) and v > 0 for v in (atr, price, rail, close, closed_rail)):
            return 'WAIT_INVALID_MARKET_DATA'
        if max(rail-price, closed_rail-close) > 2 * atr:
            return 'BLOCKED_LOW_SHORT_EXTENSION'
        middle = float(row.get('kc_middle', row.get('ema_20', float('nan'))))
        if not math.isfinite(middle) or middle <= 0:
            return 'WAIT_INVALID_MARKET_DATA'
        last_two = closed.iloc[-2:]
        if (last_two['close'] < last_two['open']).all() and min(price, close) <= middle + MID_PROXIMITY_ATR * atr:
            return 'BLOCKED_LATE_SHORT_NEAR_MIDDLE'
        if 'rsi' in row:
            rsi = float(row['rsi'])
            if not math.isfinite(rsi):
                return 'WAIT_INVALID_MARKET_DATA'
            if rsi < 25:
                return 'BLOCKED_LOW_SHORT_RSI'
        if len(closed) >= 6 and (closed['close'].iloc[-6:] < closed['open'].iloc[-6:]).all():
            return 'BLOCKED_LOW_SHORT_SIX_BARS'
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'WAIT_INVALID_MARKET_DATA'
    return None


def impulse(frame, price):
    if short_problem(frame, price):
        return None
    closed = closed_entry_candles(frame)
    row = closed.iloc[-1]
    atr = float(closed.iloc[-2]['atr'])
    opening, close, high, low = (float(row[k]) for k in ('open', 'close', 'high', 'low'))
    if not low <= close < opening <= high:
        return None
    previous = closed.iloc[-2]
    if (float(previous['close']) <= float(previous['open'])
            or opening-close <= BODY_ATR*atr or float(price) >= opening):
        return None
    return dict(action='ENTER', side='SHORT', reason=CODE, entry_atr=atr,
                entry_type='CLOSED_BEAR_IMPULSE', is_breakout=True)


def reversal_authorized(engine, symbol, signal, now=None):
    """An in-process ticket expires this minute; persisted fills prevent reuse."""
    if signal.get('side') != 'SHORT' or signal.get('signal_code') != CODE:
        return False
    ticket = getattr(engine, '_closed_bear_reverse_tickets', {}).get(symbol)
    if not isinstance(ticket, dict) or symbol in engine.account.positions:
        return False
    try:
        now = time.time() if now is None else now
        fill_id = float(ticket['fill_id'])
        if not math.isfinite(fill_id) or not 0 < fill_id <= now*1000 or int(fill_id/60000) != int(now/60):
            return False
        trades = engine.account.trades
        matched = any(t.get('symbol') == symbol and t.get('action') == 'CLOSE_LONG'
                      and float(t.get('id', 0)) == fill_id and t.get('reason') == ticket['reason']
                      for t in trades)
        reused = any(t.get('symbol') == symbol and ((str(t.get('action', '')).startswith('OPEN_') and float(t.get('id', 0)) >= fill_id)
                          or (str(t.get('action', '')).startswith('CLOSE_') and float(t.get('id', 0)) > fill_id)) for t in trades)
        return matched and not reused
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def arm_reversal(engine, symbol, reason, requested_ms):
    try:
        fills = [t for t in engine.account.trades if t.get('symbol') == symbol
                 and t.get('action') == 'CLOSE_LONG' and t.get('reason') == reason
                 and math.isfinite(float(t.get('id', 0)))
                 and float(t.get('id', 0)) >= requested_ms]
    except (TypeError, ValueError, OverflowError):
        return False
    if not fills or symbol in engine.account.positions:
        return False
    if not hasattr(engine, '_closed_bear_reverse_tickets'):
        engine._closed_bear_reverse_tickets = {}
    engine._closed_bear_reverse_tickets[symbol] = dict(
        fill_id=max(float(t['id']) for t in fills), reason=reason)
    return True


def long_start_problem(frame):
    """The candle before the confirmation pair must still be inside its rail."""
    closed = closed_entry_candles(frame)
    if len(closed) < 3:
        return 'WAIT_INITIAL_BREAKOUT_HISTORY'
    try:
        c0 = closed.iloc[-3]
        close, upper = float(c0['close']), float(c0['kc_upper'])
        if not all(math.isfinite(v) and v > 0 for v in (close, upper)):
            return 'WAIT_INVALID_MARKET_DATA'
        if close > upper:
            return 'BLOCKED_LATE_LONG_C0_OUTSIDE'
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'WAIT_INVALID_MARKET_DATA'
    return None
