"""Shared signal contract for scan, execution and account revalidation."""
import math

import numpy as np

from core.services.candle_data import closed_entry_candles
from core.services.kc_pending_entry import (KC_PENDING_CODES, KC_PENDING_EVIDENCE_KEYS,
                                            evaluate_kc_pending_entry)
from core.services.strategies.outer_strategy import (live_body_breakout_side, ck_direction,
                                                    live_ma3_direction_ready, live_candle_color_ready,
                                                    live_adverse_entry_safe, ma3_outer_continuation_ready,
                                                    OUTER_CODES)

LONG_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_LONG"
SHORT_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_SHORT"
ENTRY_CODES = KC_PENDING_CODES | {"KC_LIVE_BODY_BREAKOUT_LONG", "KC_LIVE_BODY_BREAKOUT_SHORT"} | OUTER_CODES
MAX_THIRD_OPEN_CHASE_ATR = 0.10
CHASE_EVIDENCE_KEYS = ('third_bar_id', 'third_open', 'third_reference_atr',
                       'max_chase_atr', 'chase_atr', 'chase_bar_id',
                       'chase_open', 'chase_reference_atr')

ENTRY_EVIDENCE_KEYS = CHASE_EVIDENCE_KEYS + KC_PENDING_EVIDENCE_KEYS


def evaluate_continuation_entry(frame, quote, code=None, symbol: str = ''):
    """Continuation entry for sustained trend outside the outer rail.

    Permits opening when a prior breakout was missed or after a position was closed,
    provided that the KC direction, live MA3 direction, live candle color, and outer band position
    remain consistently in favor of the trend.
    """
    try:
        side = ck_direction(frame)
        if not side:
            return None
        signal = 'KC_OUTSIDE_' + side
        if code is not None and code != signal:
            return None

        quote = float(quote)
        atr = float(frame.iloc[-2]['atr'])
        sign = 1 if side == 'LONG' else -1

        # Check MA5 direction & non-flat slope
        closes = [float(v) for v in frame['close'].iloc[-5:-1]]
        if len(closes) >= 4:
            live_ma5 = (sum(closes[-4:]) + quote) / 5.0
            last_ma5 = float(frame.iloc[-2]['ma5'])
            ma5_slope = sign * (live_ma5 - last_ma5)
            if ma5_slope <= 0 or (atr > 0 and ma5_slope / atr < 0.01):
                return None

        if not live_ma3_direction_ready(frame, quote, side):
            return None
        if not live_candle_color_ready(frame, quote, side):
            return None
        if not live_adverse_entry_safe(frame, quote, side):
            return None

        mid = float(frame.iloc[-1].get('kc_middle', frame.iloc[-1].get('ema_20', 0)))
        edge = float(frame.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
        
        # Valid if outside outer rail OR on the correct side of KC middle during active trend:
        is_outside_rail = sign * (quote - edge) > 0 and ma3_outer_continuation_ready(frame, quote, side)
        is_trend_side_of_middle = sign * (quote - mid) > 0 and sign * (live_ma5 - mid) > 0
        
        if not (is_outside_rail or is_trend_side_of_middle):
            return None

        distance = sign * (quote - edge) / atr if is_outside_rail else sign * (quote - mid) / atr
        if distance <= 0 or distance > 3.0:
            return None

        live = frame.iloc[-1]
        stamp = float(live['timestamp'])
        prev_stamp = float(frame.iloc[-2]['timestamp'])

        return dict(action='ENTER', side=side, type=signal, reason=signal,
                    price=quote, entry_atr=atr, confirmation_bar_id=stamp,
                    close_price=float(frame.iloc[-2]['close']), intrabar=True,
                    entry_phase='KC_CONTINUATION_ENTRY',
                    breakout_bar_id=stamp,
                    pair_confirmation_bar_id=prev_stamp,
                    third_bar_id=stamp,
                    pending_signal_id=f"{symbol}_CONTINUATION_{int(stamp)}_{side}",
                    pending_second_bar_id=prev_stamp,
                    pending_wait_bars=1, pending_max_wait_bars=1,
                    kc_confirmation_edge=edge if is_outside_rail else mid,
                    kc_distance_atr=distance,
                    kc_max_distance_atr=3.0)
    except Exception:
        return None

DOJI_BODY_RATIO = 0.10


def is_entry_doji(row, quote=None):
    """Strictly below 10% doji boundary; no standalone long-wick veto."""
    try:
        opening = float(row.open)
        closing = float(row.close if quote is None else quote)
        high, low = float(row.high), float(row.low)
        if not all(math.isfinite(v) and v > 0 for v in (opening, closing, high, low)):
            return True
        high, low = max(high, closing), min(low, closing)
        body, span = abs(closing-opening), high-low
        if span <= 0:
            return True
        threshold = DOJI_BODY_RATIO*span
        return body < threshold and not math.isclose(body, threshold, rel_tol=1e-12)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return True


def entry_doji_problem(closed, live, quote):
    if is_entry_doji(live, quote):
        return 'BLOCKED_LIVE_DOJI'
    bars = [row for _, row in closed.tail(2).iterrows()]
    current = live.copy()
    current['close'] = quote
    for row, following in zip(bars, bars[1:] + [current]):
        if not is_entry_doji(row):
            continue
        # Only an immediate same-color non-doji successor confirms a doji.
        # A zero body has no color; live confirmation uses the fresh quote.
        body = float(row.close) - float(row.open)
        next_body = float(following.close) - float(following.open)
        if is_entry_doji(following) or body * next_body <= 0:
            return 'BLOCKED_CLOSED_DOJI'
    return None


def is_solid_push(row, side):
    """Require a finite directional body, independent of wick length."""
    try:
        opening, closing = float(row.open), float(row.close)
        if not all(math.isfinite(v) and v > 0 for v in (opening, closing)):
            return False
        if side == 'LONG':
            return closing > opening
        if side == 'SHORT':
            return closing < opening
        return False
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


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
        if len(closed) < 2 or len(frame)-len(closed) not in (0, 1):
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
        fast_side = live_body_breakout_side(frame, quote) if len(frame) > len(closed) else None
        if fast_side:
            atr_val = float(frame.iloc[-2]['atr'])
            stamp_val = float(live.timestamp)
            prev_stamp_val = float(frame.iloc[-2]['timestamp'])
            decision = {
                'action': 'ENTER',
                'side': fast_side,
                'type': 'KC_LIVE_BODY_BREAKOUT_' + fast_side,
                'reason': 'KC_LIVE_BODY_BREAKOUT_' + fast_side,
                'price': quote,
                'entry_atr': atr_val,
                'confirmation_bar_id': stamp_val,
                'close_price': float(frame.iloc[-2]['close']),
                'intrabar': True,
                'entry_phase': 'KC_LIVE_BODY_BREAKOUT',
                'pending_signal_id': f"{symbol}_LIVE_BREAKOUT_{int(stamp_val)}_{fast_side}",
                'breakout_bar_id': stamp_val,
                'pair_confirmation_bar_id': prev_stamp_val,
                'third_bar_id': stamp_val,
                'pending_second_bar_id': prev_stamp_val,
                'pending_wait_bars': 1,
                'pending_max_wait_bars': 1
            }
        else:
            decision = evaluate_kc_pending_entry(closed, quote, code, symbol=symbol,
                                                 live=live if len(frame) > len(closed) else None)
            # If 3-bar entry is not ready, or is an old breakout from before exit:
            if decision['action'] != 'ENTER' or (exit_bar is not None and decision.get('breakout_bar_id', 0) <= exit_bar):
                cont_decision = evaluate_continuation_entry(frame, quote, code, symbol=symbol)
                if cont_decision and cont_decision['action'] == 'ENTER':
                    decision = cont_decision
        if decision['action'] != 'ENTER':
            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(decision)
            return None

        # Post-exit formation verification:
        if exit_bar is not None and float(live.timestamp) <= exit_bar:
            return reject('WAIT_POST_EXIT_NEW_FORMATION')
        if exit_bar is not None and decision.get('entry_phase') not in ('KC_CONTINUATION_ENTRY', 'KC_LIVE_BODY_BREAKOUT') and decision.get('breakout_bar_id', 0) <= exit_bar:
            return reject('WAIT_POST_EXIT_NEW_FORMATION')
        # Persisted successful fills own deduplication, including after restart.
        for trade in getattr(account, 'trades', []):
            if (trade.get('symbol') == symbol and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                    and (trade.get('entry_snapshot') or {}).get('pending_signal_id') == decision['pending_signal_id']):
                return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')
        decision['exit_bar_id'] = exit_bar
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics['reason'] = decision['type']
        return decision
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return reject('WAIT_VALID_ENTRY_DATA')
