"""Shared signal contract for scan, execution and account revalidation."""
import math

import numpy as np

from core.services.candle_data import closed_entry_candles

LONG_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_LONG"
SHORT_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_SHORT"
ENTRY_CODES = frozenset((LONG_ENTRY_CODE, SHORT_ENTRY_CODE))
MAX_THIRD_OPEN_CHASE_ATR = 0.10
CHASE_EVIDENCE_KEYS = ('third_bar_id', 'third_open', 'third_reference_atr',
                       'max_chase_atr', 'chase_atr')



def prohibited_entry_candle(row, quote=None):
    """Reject doji or either wick at least as long as the body."""
    opening = float(row.open)
    closing = float(row.close if quote is None else quote)
    high = max(float(row.high), closing)
    low = min(float(row.low), closing)
    body = abs(closing-opening)
    span = high-low
    upper = high-max(opening, closing)
    lower = min(opening, closing)-low
    if span <= 0 or body <= 0:
        return True
    return (body <= .25*span or math.isclose(body, .25*span, rel_tol=1e-12) or
            max(upper, lower) >= body or
            math.isclose(max(upper, lower), body, rel_tol=1e-12))


def evaluate_entry_contract(frame, price=None, code=None, *, account=None,
                            symbol="", diagnostics=None):
    def reject(reason):
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics["reason"] = reason
        return None
    reject("WAIT_VALID_ENTRY_DATA")
    if code is not None and code not in ENTRY_CODES:
        return reject("BLOCKED_OBSOLETE_ENTRY_SIGNAL")
    if account is not None and symbol in getattr(account, "positions", {}):
        return reject("WAIT_EXISTING_POSITION")
    try:
        if frame is None or frame.empty or frame.attrs.get('timeframe_ms', 60000) != 60000:
            return None
        if 'is_closed' not in frame or not all(isinstance(v, (bool, np.bool_)) for v in frame.is_closed):
            return None
        # Rolling indicators legitimately have an unavailable leading prefix.
        # Trim only that prefix; never bridge missing data inside valid history.
        indicator_keys = ['atr', 'kc_upper', 'kc_middle', 'kc_lower']
        ready = frame[indicator_keys].notna().all(axis=1).to_numpy()
        valid_indices = np.flatnonzero(ready)
        if not len(valid_indices):
            return None
        frame = frame.iloc[int(valid_indices[0]):].copy()
        closed = closed_entry_candles(frame)
        if len(closed) < 3 or len(frame)-len(closed) not in (0, 1):
            return None
        keys = ['timestamp','open','high','low','close','kc_upper','kc_middle','kc_lower','atr']
        values = frame[keys].astype(float)
        if not np.isfinite(values.to_numpy()).all() or not values.gt(0).all().all():
            return None
        if not values.timestamp.diff().dropna().eq(60000).all():
            return None
        if not ((values.low <= values[['open','close']].min(axis=1)) &
                (values.high >= values[['open','close']].max(axis=1)) &
                (values.kc_lower < values.kc_middle) & (values.kc_middle < values.kc_upper)).all():
            return None
        quote = float(frame.iloc[-1].close if price is None else price)
        if not math.isfinite(quote) or quote <= 0:
            return None
        live = frame.iloc[-1]
        latest = closed.iloc[-1]
        if prohibited_entry_candle(live, quote):
            return reject('BLOCKED_LIVE_LONG_WICK_OR_DOJI')
        if prohibited_entry_candle(latest):
            return reject('BLOCKED_CLOSED_LONG_WICK_OR_DOJI')
        # Do not reopen in the candle of a successful close, even after restart.
        exit_bar = None
        for trade in getattr(account, 'trades', []):
            if trade.get('symbol') == symbol and trade.get('action') in ('CLOSE_LONG','CLOSE_SHORT'):
                stamp = float(trade['id'])
                if not math.isfinite(stamp) or stamp <= 0:
                    return reject('WAIT_VALID_CLOSE_HISTORY')
                bar = math.floor(stamp/60000)*60000
                exit_bar = max(exit_bar or bar, bar)
        saved_close = getattr(account, 'last_closed_at', {}).get(symbol)
        if saved_close is not None:
            saved_close = float(saved_close)
            if not math.isfinite(saved_close) or saved_close <= 0:
                return reject('WAIT_VALID_CLOSE_HISTORY')
            saved_bar = math.floor(saved_close/60)*60000
            exit_bar = max(exit_bar or saved_bar, saved_bar)
        execution_bar = float(latest.timestamp)+60000
        if exit_bar is not None and execution_bar <= exit_bar:
            return reject('WAIT_POST_EXIT_NEXT_BAR')
        for side, signal in (('LONG', LONG_ENTRY_CODE), ('SHORT', SHORT_ENTRY_CODE)):
            if code is not None and signal != code:
                continue
            sign = 1 if side == 'LONG' else -1
            edge = 'kc_upper' if side == 'LONG' else 'kc_lower'
            if sign*(quote-float(live[edge])) <= 0:
                continue
            if sign*(float(latest.close)-float(latest.open)) <= 0:
                continue
            # Locate a real closed breakout pair; continuation expires on a
            # closed return to the rail/interior. Never infer a pair from wicks.
            for i in range(len(closed)-2, 0, -1):
                first, second = closed.iloc[i], closed.iloc[i+1]
                if prohibited_entry_candle(first) or prohibited_entry_candle(second):
                    continue
                tail = closed.iloc[i+1:]
                if not (sign*(tail.close-tail[edge])).gt(0).all():
                    continue
                body = sign*(float(first.close)-float(first.open))
                threshold = .5*float(closed.iloc[i-1].atr)
                if body < threshold and not math.isclose(body, threshold, rel_tol=1e-12):
                    continue
                if sign*(float(first.open)-float(first[edge])) > 0 or sign*(float(first.close)-float(first[edge])) <= 0:
                    continue
                if sign*(float(second.close)-float(second.open)) <= 0:
                    continue
                # Keep the original pair's third open and second closed ATR,
                # including continuation: a new candle must not reset the cap.
                if i+2 >= len(frame):
                    return reject('WAIT_THIRD_CANDLE_OPEN')
                third = frame.iloc[i+2]
                if float(third.timestamp) != float(second.timestamp)+60000:
                    return reject('WAIT_THIRD_CANDLE_OPEN')
                third_open = float(third.open)
                reference_atr = float(second.atr)
                chase = sign*(quote-third_open)
                limit = MAX_THIRD_OPEN_CHASE_ATR*reference_atr
                evidence = dict(third_bar_id=float(third.timestamp), third_open=third_open,
                                third_reference_atr=reference_atr,
                                max_chase_atr=MAX_THIRD_OPEN_CHASE_ATR,
                                chase_atr=chase/reference_atr)
                if chase > limit and not math.isclose(chase,limit,rel_tol=1e-12):
                    reject('BLOCKED_THIRD_OPEN_CHASE')
                    if diagnostics is not None:
                        diagnostics.update(evidence)
                    return None
                phase = 'INITIAL_BREAKOUT' if i+1 == len(closed)-1 else 'OUTSIDE_CONTINUATION'
                if phase == 'OUTSIDE_CONTINUATION' and exit_bar is not None:
                    phase = 'POST_EXIT_CONTINUATION'
                if diagnostics is not None:
                    diagnostics['reason'] = phase
                return dict(action='ENTER', side=side, type=signal, reason=signal,
                            price=quote, entry_atr=float(latest.atr),
                            confirmation_bar_id=float(latest.timestamp),
                            breakout_bar_id=float(first.timestamp),
                            pair_confirmation_bar_id=float(second.timestamp),
                            close_price=float(latest.close), intrabar=False,
                            entry_phase=phase, exit_bar_id=exit_bar, **evidence)
        return reject('WAIT_CLOSED_BREAKOUT_OR_CONTINUATION')
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return reject('WAIT_VALID_ENTRY_DATA')
