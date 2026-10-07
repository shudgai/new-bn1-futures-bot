"""CAP two-closed-bar authority and observed, persistent continuation provenance."""
import copy
import math
import time
import uuid

from core.services.candle_data import closed_entry_candles
from core.services.kc_pending_entry import evaluate_kc_pending_entry

SYMBOL = 'CAP/USDT'
STATE_KEY = '_cap_breakout_origins'
CODES = frozenset('CAP_KC_CONTINUATION_'+side for side in ('LONG', 'SHORT'))
EVIDENCE_KEYS = ('cap_origin_id', 'cap_origin_first_ms', 'cap_origin_second_ms',
                 'cap_origin_candles', 'cap_quote_ma5', 'cap_progress_previous_close',
                 'cap_progress_last_close', 'cap_progress_quote')


def directional_price_progress(frame, quote, side):
    """Both recent completed closes and the live quote must advance."""
    try:
        closed = closed_entry_candles(frame)
        if side not in ('LONG', 'SHORT') or len(closed) < 2:
            return None
        previous, latest, quote = map(float, (closed.iloc[-2].close, closed.iloc[-1].close, quote))
        if not all(math.isfinite(value) and value > 0 for value in (previous, latest, quote)):
            return None
        sign = 1 if side == 'LONG' else -1
        tolerance = max(previous, latest, quote)*1e-12
        if sign*(latest-previous) <= tolerance or sign*(quote-latest) <= tolerance:
            return None
        return dict(cap_progress_previous_close=previous,
                    cap_progress_last_close=latest, cap_progress_quote=quote)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def confirmed_pair(frame, quote):
    if frame is None or 'is_closed' not in frame:
        return None
    closed = closed_entry_candles(frame)
    if len(closed) < 2 or len(frame) != len(closed)+1:
        return None
    decision = evaluate_kc_pending_entry(closed, quote, live=frame.iloc[-1], symbol=SYMBOL)
    if decision.get('action') != 'ENTER':
        return None
    first = closed.iloc[-2]
    for _, row in closed.iloc[-2:].iterrows():
        values = [float(row[key]) for key in
                  ('timestamp', 'open', 'high', 'low', 'close', 'kc_lower', 'kc_upper')]
        stamp, opened, high, low, close, lower, upper = values
        if (not all(math.isfinite(v) and v > 0 for v in values)
                or stamp % 60000 != 0 or lower >= upper or high <= low
                or not low <= min(opened, close) <= max(opened, close) <= high):
            return None
    if not float(first.kc_lower) <= float(first.open) <= float(first.kc_upper):
        return None
    return decision


def observe_cap_breakout(account, symbol, frame, quote, quote_ms=None):
    """Only market-processing paths call this; diagnostics never arm or clear."""
    if symbol != SYMBOL:
        return
    states = account.position_meta.setdefault(STATE_KEY, {})
    previous = copy.deepcopy(states.get(symbol, {}))
    state = copy.deepcopy(previous)
    session = getattr(account, '_cap_breakout_session', None)
    if session is None:
        session = account._cap_breakout_session = uuid.uuid4().hex
    if state.get('session') not in (None, session):
        state.pop('origin', None)
        state['cancelled_second_ms'] = max(state.get('cancelled_second_ms', 0),
                                          state.get('last_bar_ms', 0)-60000)
        state['status'] = 'CAP_ORIGIN_RESTART_REQUIRES_NEW_PAIR'
    state['session'] = session
    try:
        stamp = float(quote_ms if quote_ms is not None else time.time()*1000)
        quote = float(quote)
        if math.isfinite(stamp) and stamp < state.get('last_quote_ms', 0):
            return
        if (not math.isfinite(stamp) or not 0 <= time.time()*1000-stamp <= 5000
                or not math.isfinite(quote) or quote <= 0):
            raise ValueError('CAP_ORIGIN_INVALID_QUOTE')
        if frame is None or frame.empty or 'is_closed' not in frame:
            raise ValueError('CAP_ORIGIN_DATA_SUSPENDED')
        closed = closed_entry_candles(frame)
        live = frame.iloc[-1]
        bar, lower, upper = map(float, (live.timestamp, live.kc_lower, live.kc_upper))
        if (len(closed) < 2 or len(frame) != len(closed)+1
                or bar != math.floor(stamp/60000)*60000
                or not all(math.isfinite(v) and v > 0 for v in (lower, upper))
                or lower >= upper or float(closed.iloc[-1].timestamp) != bar-60000):
            raise ValueError('CAP_ORIGIN_DATA_SUSPENDED')
        origin = state.get('origin')
        if origin:
            sign = 1 if origin['side'] == 'LONG' else -1
            edge = upper if sign == 1 else lower
            if stamp-state.get('last_quote_ms', stamp) > 5000:
                raise ValueError('CAP_ORIGIN_INTERRUPTED_QUOTES')
            if sign*(quote-edge) <= 0:
                state.pop('origin', None)
                state['cancelled_second_ms'] = max(state.get('cancelled_second_ms', 0),
                                                  bar-60000)
                state['status'] = 'CAP_ORIGIN_CANCELLED_RAIL_RETURN'
        pair = confirmed_pair(frame, quote)
        if pair and pair['pair_confirmation_bar_id'] > state.get('cancelled_second_ms', 0):
            ident = f"{symbol}:{pair['side']}:{int(pair['breakout_bar_id'])}:{int(pair['pair_confirmation_bar_id'])}"
            if not state.get('origin') or state['origin']['id'] != ident:
                rows = closed.iloc[-2:]
                state['origin'] = dict(id=ident, side=pair['side'],
                    first_ms=pair['breakout_bar_id'], second_ms=pair['pair_confirmation_bar_id'],
                    candles=[{key: float(row[key]) for key in
                              ('timestamp', 'open', 'high', 'low', 'close', 'kc_lower', 'kc_upper')}
                             for _, row in rows.iterrows()])
                state['status'] = 'CAP_ORIGIN_ARMED'
        state.update(last_quote_ms=stamp, last_bar_ms=bar)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError) as exc:
        state.pop('origin', None)
        state['cancelled_second_ms'] = max(state.get('cancelled_second_ms', 0),
                                          state.get('last_bar_ms', 0)-60000)
        state['status'] = str(exc) if isinstance(exc, ValueError) else 'CAP_ORIGIN_INVALID_DATA'
    if state != previous:
        states[symbol] = state
        if state.get('status') != previous.get('status'):
            account.log(state['status']+' symbol='+symbol, 'INFO')
        if any(state.get(key) != previous.get(key) for key in
               ('origin', 'status', 'session', 'cancelled_second_ms', 'last_bar_ms')):
            account.save_state()


def continuation_decision(frame, quote, account, code=None):
    if account is None:
        return None
    state = getattr(account, 'position_meta', {}).get(STATE_KEY, {}).get(SYMBOL, {})
    origin = state.get('origin')
    if (not origin or state.get('session') != getattr(account, '_cap_breakout_session', None)
            or not 0 <= time.time()*1000-state.get('last_quote_ms', 0) <= 5000):
        return None
    from core.services.strategies.outer_strategy import ck_direction
    side = origin['side']
    signal = 'CAP_KC_CONTINUATION_'+side
    if code not in (None, signal) or ck_direction(frame) != side:
        return None
    closed = closed_entry_candles(frame)
    live = frame.iloc[-1]
    bar = float(live.timestamp)
    if bar <= origin['second_ms']+60000 or state.get('last_bar_ms') != bar:
        return None
    sign = 1 if side == 'LONG' else -1
    edge = float(live['kc_upper' if sign == 1 else 'kc_lower'])
    if sign*(quote-edge) <= 0:
        return None
    return dict(action='ENTER', side=side, type=signal, reason=signal, price=quote,
        entry_atr=float(closed.iloc[-1].atr), confirmation_bar_id=bar,
        close_price=float(closed.iloc[-1].close), intrabar=True,
        entry_phase='KC_CONTINUATION_ENTRY', breakout_bar_id=origin['first_ms'],
        pair_confirmation_bar_id=origin['second_ms'], third_bar_id=bar,
        pending_signal_id=f"{origin['id']}:CONT:{int(bar)}",
        cap_origin_id=origin['id'], cap_origin_first_ms=origin['first_ms'],
        cap_origin_second_ms=origin['second_ms'], cap_origin_candles=copy.deepcopy(origin['candles']))
