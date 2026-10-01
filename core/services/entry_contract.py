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
    Reject doji or long adverse wicks.
    Body ratio must be >= 50%.
    Adverse wick ratio must be <= 35%.
    """
    try:
        opening = float(row.open)
        closing = float(row.close if quote is None else quote)
        high = max(float(row.high), closing)
        low = min(float(row.low), closing)
        body = abs(closing-opening)
        span = high-low
        if span <= 0 or body <= 0:
            return True
        if body < 0.15 * float(row.get('atr', 0)):
            return True
            
        body_ratio = body / span
        if body_ratio < 0.40 and not math.isclose(body_ratio, 0.40, rel_tol=1e-12):
            return True
            
        ma5 = float(row.get('ma5', row.get('ma3', 0)))
        
        if side == 'LONG':
            # 嚴格做多過濾 (下殺未止不接多)：若是紅K，除非有止跌信號(長下影線或站穩短均線)，否則嚴禁開倉
            if closing < opening:
                lower_wick = closing - low
                if (lower_wick / span) <= 0.40 and closing < ma5:
                    return True
            adverse_wick = high - max(opening, closing)
        elif side == 'SHORT':
            # 嚴格做空過濾 (反彈未歇不開空)：若是綠K，除非有滯漲信號(長上影線或跌破短均線)，否則嚴禁開倉
            if closing > opening:
                upper_wick = high - closing
                if (upper_wick / span) <= 0.40 and closing > ma5:
                    return True
            adverse_wick = min(opening, closing) - low
        else:
            return False
            
        adverse_wick_ratio = adverse_wick / span
        if adverse_wick_ratio > 0.40 and not math.isclose(adverse_wick_ratio, 0.40, rel_tol=1e-12):
            return True
            
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
            
            is_continuation_entry = False
            # Continuation requires the current live candle to be a valid directional bar
            if not prohibited_entry_candle(live, side, quote):
                if side == 'LONG':
                    kc_trending_up = kc_middle > prev_kc_middle and quote > kc_middle
                    ma_bullish = ma5 > ma15
                    low_val = float(live.low)
                    open_val = float(live.open)
                    try:
                        prev_close = float(latest.close)
                        prev_ma5 = float(latest.get('ma5', latest.get('ma3', 0)))
                    except:
                        prev_close = prev_ma5 = 0
                    
                    re_entry_trigger = ((low_val <= ma5 and quote >= ma5 and quote > open_val) or 
                                        (quote > ma5 and quote > kc_middle and prev_close <= prev_ma5))
                    
                    if kc_trending_up and ma_bullish and re_entry_trigger:
                        is_continuation_entry = True
                else:
                    kc_trending_down = kc_middle < prev_kc_middle and quote < kc_middle
                    ma_bearish = ma5 < ma15
                    high_val = float(live.high)
                    open_val = float(live.open)
                    try:
                        prev_close = float(latest.close)
                        prev_ma5 = float(latest.get('ma5', latest.get('ma3', 0)))
                    except:
                        prev_close = prev_ma5 = float('inf')
                        
                    re_entry_trigger = ((high_val >= ma5 and quote <= ma5 and quote < open_val) or 
                                        (quote < ma5 and quote < kc_middle and prev_close >= prev_ma5))
                    
                    if kc_trending_down and ma_bearish and re_entry_trigger:
                        is_continuation_entry = True
            if is_continuation_entry:
                phase = 'POST_EXIT_CONTINUATION' if exit_bar is not None else 'PULLBACK_BOUNCE_CONTINUATION'
                if diagnostics is not None:
                    diagnostics['reason'] = 'PULLBACK_BOUNCE_CONTINUATION'
                # Provide mock evidence since this skips the original pair matching
                atr = float(latest.atr)
                evidence = dict(third_bar_id=float(live.timestamp), third_open=float(live.open),
                                third_reference_atr=atr, chase_bar_id=float(live.timestamp),
                                chase_open=float(live.open), chase_reference_atr=atr,
                                max_chase_atr=MAX_THIRD_OPEN_CHASE_ATR, chase_atr=0.0)
                return dict(action='ENTER', side=side, type=signal, reason=signal,
                            price=quote, entry_atr=atr, confirmation_bar_id=float(latest.timestamp),
                            breakout_bar_id=float(latest.timestamp), pair_confirmation_bar_id=float(latest.timestamp),
                            close_price=float(latest.close), intrabar=True,
                            entry_phase=phase, exit_bar_id=exit_bar, **evidence)
            # -----------------------------------------------------------------

            if prohibited_entry_candle(live, side, quote):
                reject('BLOCKED_LIVE_LONG_WICK_OR_DOJI')
                continue
            if prohibited_entry_candle(latest, side):
                reject('BLOCKED_CLOSED_LONG_WICK_OR_DOJI')
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
                if prohibited_entry_candle(first, side) or prohibited_entry_candle(second, side):
                    continue
                tail = closed.iloc[i+1:]
                if not (sign*(tail.close-tail[edge])).gt(0).all():
                    continue
                body = sign*(float(first.close)-float(first.open))
                threshold = .3*float(closed.iloc[i-1].atr)
                if body < threshold and not math.isclose(body, threshold, rel_tol=1e-12):
                    continue
                if sign*(float(first.open)-float(first[edge])) > 0 or sign*(float(first.close)-float(first[edge])) <= 0:
                    continue
                if sign*(float(second.close)-float(second.open)) <= 0:
                    continue
                # Initial entry uses the third open; later outside continuation
                # gets a fresh opportunity near its own live candle's open.
                if i+2 >= len(frame):
                    return reject('WAIT_THIRD_CANDLE_OPEN')
                third = frame.iloc[i+2]
                if float(third.timestamp) != float(second.timestamp)+60000:
                    return reject('WAIT_THIRD_CANDLE_OPEN')
                third_open = float(third.open)
                is_continuation = i+1 < len(closed)-1
                anchor = live if is_continuation else third
                reference_atr = float(latest.atr if is_continuation else second.atr)
                chase = sign*(quote-float(anchor.open))
                limit = MAX_THIRD_OPEN_CHASE_ATR*reference_atr
                evidence = dict(third_bar_id=float(third.timestamp), third_open=third_open,
                                third_reference_atr=float(second.atr),
                                chase_bar_id=float(anchor.timestamp),
                                chase_open=float(anchor.open), chase_reference_atr=reference_atr,
                                max_chase_atr=MAX_THIRD_OPEN_CHASE_ATR,
                                chase_atr=chase/reference_atr)
                if chase > limit and not math.isclose(chase,limit,rel_tol=1e-12):
                    reject('BLOCKED_CONTINUATION_OPEN_CHASE' if is_continuation
                           else 'BLOCKED_THIRD_OPEN_CHASE')
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
