"""Shared signal contract for scan, execution and account revalidation."""
import math

import numpy as np

from core.services.candle_data import closed_entry_candles

LONG_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_LONG"
SHORT_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_SHORT"
ENTRY_CODES = frozenset((LONG_ENTRY_CODE, SHORT_ENTRY_CODE))
MAX_THIRD_OPEN_CHASE_ATR = 0.10
CHASE_EVIDENCE_KEYS = ('third_bar_id', 'third_open', 'third_reference_atr',
                       'max_chase_atr', 'chase_atr', 'chase_bar_id',
                       'chase_open', 'chase_reference_atr')



def prohibited_entry_candle(row, side=None, quote=None):
    """
    Enforce strict right-side confirmation and forbid catching knives/fading tops.
    Evaluated exclusively on the closed candle ([1]).
    """
    try:
        opening = float(row.open)
        closing = float(row.close)
        high = max(float(row.high), closing)
        low = min(float(row.low), closing)
        body = abs(closing-opening)
        span = high-low
        if span <= 0 or body <= 0:
            return True
            
        body_ratio = body / span
        lower_wick = min(opening, closing) - low
        upper_wick = high - max(opening, closing)
        lower_wick_ratio = lower_wick / span
        upper_wick_ratio = upper_wick / span
        
        ma5 = float(row.get('ma5', row.get('ma3', 0)))
        ma15 = float(row.get('ma15', 0))
        
        if side == 'LONG':
            confirmed = (closing > opening and body_ratio >= 0.30 and closing >= ma5) or \
                        (lower_wick_ratio >= 0.40 and closing >= ma15)
            forbid = (closing < opening) and (lower_wick_ratio < 0.40)
            if forbid or not confirmed:
                return True
                
        elif side == 'SHORT':
            confirmed = (closing < opening and body_ratio >= 0.30 and closing <= ma5) or \
                        (upper_wick_ratio >= 0.40 and closing <= ma15)
            forbid = (closing > opening) and (upper_wick_ratio < 0.40)
            if forbid or not confirmed:
                return True
        else:
            return False
            
        return False
    except (KeyError, TypeError, ValueError):
        return True


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
            # --- 使用者新增：趨勢延續二次進場（回踩 MA5/MA15 獲得支撐） ---
            ma5 = float(live.get('ma5', live.get('ma3', 0)))
            ma15 = float(live.get('ma15', 0))
            kc_middle = float(live.kc_middle)
            prev_kc_middle = float(latest.kc_middle)
            try:
                prev2_kc_middle = float(closed.iloc[-2].kc_middle)
            except (IndexError, KeyError, ValueError):
                prev2_kc_middle = prev_kc_middle
            
            # Require strict right-side confirmation on the CLOSED candle
            if prohibited_entry_candle(latest, side):
                reject('BLOCKED_CLOSED_NOT_CONFIRMED_RIGHT_SIDE')
                continue
                
            is_valid_entry = False
            phase = 'INITIAL_BREAKOUT'
            
            latest_close = float(latest.close)
            latest_kc_upper = float(latest.kc_upper)
            latest_kc_lower = float(latest.kc_lower)
            latest_kc_middle = float(latest.kc_middle)
            latest_low = float(latest.low)
            latest_high = float(latest.high)
            latest_ma5 = float(latest.get('ma5', latest.get('ma3', 0)))
            
            if side == 'LONG':
                # Base Signal: Breakout or Continuation
                base_breakout = latest_close > latest_kc_upper
                base_continuation = latest_kc_middle > prev2_kc_middle and latest_low <= latest_ma5 and latest_close > latest_ma5
                if base_breakout or base_continuation:
                    is_valid_entry = True
                    phase = 'PULLBACK_BOUNCE_CONTINUATION' if base_continuation and not base_breakout else 'INITIAL_BREAKOUT'
            else:
                base_breakout = latest_close < latest_kc_lower
                base_continuation = latest_kc_middle < prev2_kc_middle and latest_high >= latest_ma5 and latest_close < latest_ma5
                if base_breakout or base_continuation:
                    is_valid_entry = True
                    phase = 'PULLBACK_BOUNCE_CONTINUATION' if base_continuation and not base_breakout else 'INITIAL_BREAKOUT'

            if is_valid_entry:
                # Use live open to measure chase, but the decision is purely based on closed bar
                atr = float(latest.atr)
                chase = sign*(quote - float(live.open))
                limit = MAX_THIRD_OPEN_CHASE_ATR * atr
                
                if chase > limit and not math.isclose(chase, limit, rel_tol=1e-12):
                    reject('BLOCKED_OPEN_CHASE')
                    continue
                    
                if phase == 'PULLBACK_BOUNCE_CONTINUATION' and exit_bar is not None:
                    phase = 'POST_EXIT_CONTINUATION'
                    
                evidence = dict(third_bar_id=float(live.timestamp), third_open=float(live.open),
                                third_reference_atr=atr, chase_bar_id=float(live.timestamp),
                                chase_open=float(live.open), chase_reference_atr=atr,
                                max_chase_atr=MAX_THIRD_OPEN_CHASE_ATR, chase_atr=chase/atr if atr else 0.0)
                
                if diagnostics is not None:
                    diagnostics['reason'] = phase
                    
                return dict(action='ENTER', side=side, type=signal, reason=signal,
                            price=quote, entry_atr=atr, confirmation_bar_id=float(latest.timestamp),
                            breakout_bar_id=float(latest.timestamp), pair_confirmation_bar_id=float(latest.timestamp),
                            close_price=latest_close, intrabar=False,
                            entry_phase=phase, exit_bar_id=exit_bar, **evidence)
        return reject('WAIT_CLOSED_BREAKOUT_OR_CONTINUATION')
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return reject('WAIT_VALID_ENTRY_DATA')
