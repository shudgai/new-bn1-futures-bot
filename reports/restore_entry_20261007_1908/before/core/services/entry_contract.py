"""Shared signal contract for scan, execution and account revalidation."""
import math
from decimal import Decimal
from core.services.channel_same_bar import entry_side as channel_entry_side, EVIDENCE_KEYS as SAME_BAR_EVIDENCE_KEYS
from core.services.early_swing_reversal import evaluate_early_swing,PHASE as REVERSAL_PHASE,CODES as REVERSAL_CODES,EVIDENCE_KEYS as REVERSAL_EVIDENCE_KEYS,EXIT as REVERSAL_EXIT

from core.services.channel_structure_entry import (evaluate_channel_structure, PHASE as STRUCTURE_PHASE, CODES as STRUCTURE_CODES, EVIDENCE_KEYS as STRUCTURE_EVIDENCE_KEYS)

import numpy as np
import pandas as pd
from core.services.outer_small_pair_entry import (CODES as OUTER_SMALL_PAIR_CODES,
    PHASE as OUTER_SMALL_PAIR_PHASE, EVIDENCE_KEYS as OUTER_SMALL_PAIR_EVIDENCE_KEYS,
    evaluate_outer_small_pair)

from core.services.closed_ma_cross import (CODES as CLOSED_MA_CROSS_CODES,
    EVIDENCE_KEYS as CROSS_EVIDENCE_KEYS, evaluate_closed_ma_cross)

from core.services.candle_data import closed_entry_candles
from core.services.wait_authority import CODES as WAIT_CODES, WaitAuthority
from core.services.entry_chop_gate import evaluate_entry_chop, EVIDENCE_KEYS as CHOP_EVIDENCE_KEYS
from core.services.swing_entry_gate import (
    evaluate_swing_entry_gate, EVIDENCE_KEYS as SWING_EVIDENCE_KEYS,
)
from core.services.cap_breakout_entry import (
    SYMBOL as CAP_SYMBOL, CODES as CAP_CONTINUATION_CODES,
    EVIDENCE_KEYS as CAP_EVIDENCE_KEYS, continuation_decision,
)
from core.services.kc_pending_entry import (KC_PENDING_CODES, KC_PENDING_EVIDENCE_KEYS,
                                            evaluate_kc_pending_entry)
from core.services.ma5_outer_pivot_entry import (
    CODES as MA5_PIVOT_CODES, PHASE as MA5_PIVOT_PHASE,
    EVIDENCE_KEYS as MA5_PIVOT_EVIDENCE_KEYS, evaluate_ma5_outer_pivot_entry,
)
from core.services.strategies.outer_strategy import (live_body_breakout_side, ck_direction,
                                                    live_ma3_direction_ready, live_candle_color_ready,
                                                    live_adverse_entry_safe, ma3_outer_continuation_ready,
                                                    OUTER_CODES, LIVE_BREAKOUT_BODY_ATR,
                                                    LIVE_BODY_BREAKOUT_CODES)

LONG_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_LONG"
SHORT_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_SHORT"
TURN_CODES = {'KC_CHANNEL_TURN_LONG', 'KC_CHANNEL_TURN_SHORT'}
MA3_REVERSE_CODES = {"KC_MA3_TURN_REVERSE_LONG", "KC_MA3_TURN_REVERSE_SHORT"}
ENTRY_CODES = MA3_REVERSE_CODES | KC_PENDING_CODES | {"KC_LIVE_BODY_BREAKOUT_LONG", "KC_LIVE_BODY_BREAKOUT_SHORT"} | OUTER_CODES
MA_CROSS_TREND_CODES = {'KC_MA_CROSS_TREND_LONG', 'KC_MA_CROSS_TREND_SHORT'}
LIVE_EXPANSION_CODES = {'LIVE_LONG_BODY_LONG', 'LIVE_LONG_BODY_SHORT'}
PULLBACK_PHASE = 'KC_CONFIRMED_PULLBACK_RESUME'
PULLBACK_CODES = {PULLBACK_PHASE+'_LONG', PULLBACK_PHASE+'_SHORT'}
CHANNEL_BODY_PHASE = 'KC_CHANNEL_LIVE_LONG_BODY'
CHANNEL_BODY_CODES = {CHANNEL_BODY_PHASE+'_LONG',CHANNEL_BODY_PHASE+'_SHORT'}
FAST_BODY_PHASES = (REVERSAL_PHASE,'KC_LIVE_BODY_BREAKOUT', OUTER_SMALL_PAIR_PHASE, CHANNEL_BODY_PHASE, STRUCTURE_PHASE, PULLBACK_PHASE)
from core.services.dual_breakout_entry import CODES as BREAKOUT_CODES, evaluate_breakout
ENTRY_CODES = BREAKOUT_CODES
MAX_THIRD_OPEN_CHASE_ATR = 0.10
CHASE_EVIDENCE_KEYS = ('third_bar_id', 'third_open', 'third_reference_atr',
                       'max_chase_atr', 'chase_atr', 'chase_bar_id',
                       'chase_open', 'chase_reference_atr')

ENTRY_EVIDENCE_KEYS = SAME_BAR_EVIDENCE_KEYS + REVERSAL_EVIDENCE_KEYS + CHASE_EVIDENCE_KEYS + KC_PENDING_EVIDENCE_KEYS + ('entry_failure_level','structure_risk_stop','continuation_origin_bar_id','pullback_level','pullback_pivot_ms','pullback_confirmed_ms') + STRUCTURE_EVIDENCE_KEYS + CROSS_EVIDENCE_KEYS + OUTER_SMALL_PAIR_EVIDENCE_KEYS + MA5_PIVOT_EVIDENCE_KEYS
BREAKOUT_EVIDENCE_KEYS = ('breakout_live_open', 'breakout_live_edge', 'breakout_body',
                         'breakout_min_body', 'breakout_previous_ma5', 'breakout_live_ma5')
ENTRY_EVIDENCE_KEYS += BREAKOUT_EVIDENCE_KEYS
ENTRY_EVIDENCE_KEYS += ('entry_live_ma5', 'entry_live_ma15')
ENTRY_EVIDENCE_KEYS += CHOP_EVIDENCE_KEYS
ENTRY_EVIDENCE_KEYS += CAP_EVIDENCE_KEYS
ENTRY_EVIDENCE_KEYS += ('wait_trigger_id', 'wait_setup', 'wait_live_open', 'wait_fixed_atr')
ENTRY_EVIDENCE_KEYS += SWING_EVIDENCE_KEYS
LIVE_KC_EVIDENCE_KEYS = (
    'entry_qualification_policy', 'breakout_previous_kc_middle',
    'breakout_latest_kc_middle', 'breakout_kc_direction', 'breakout_direction',
)
ENTRY_EVIDENCE_KEYS += LIVE_KC_EVIDENCE_KEYS
ENTRY_EVIDENCE_KEYS += ('wait_outer_edge', 'wait_outer_quote', 'wait_entry_policy')
ENTRY_EVIDENCE_KEYS += ('entry_previous_ma5', 'entry_previous_ma15')


def validated_entry_market_frame(frame):
    """Trim indicator warm-up only; reject gaps or invalid authoritative candles."""
    if (frame is None or frame.empty or frame.attrs.get('timeframe_ms', 60000) != 60000
            or 'is_closed' not in frame
            or not all(isinstance(v, (bool, np.bool_)) for v in frame.is_closed)):
        raise ValueError('WAIT_INVALID_MARKET_DATA')
    ready = frame[['atr', 'kc_upper', 'kc_middle', 'kc_lower']].notna().all(axis=1).to_numpy()
    indices = np.flatnonzero(ready)
    if not len(indices):
        raise ValueError('WAIT_INVALID_MARKET_DATA')
    validated = frame.iloc[int(indices[0]):].copy()
    closed = closed_entry_candles(validated)
    if len(closed) < 2 or len(validated) != len(closed)+1:
        raise ValueError('WAIT_FORMING_BREAKOUT_BAR')
    values = validated[['timestamp', 'open', 'high', 'low', 'close',
                        'kc_upper', 'kc_middle', 'kc_lower', 'atr']].astype(float)
    if (not np.isfinite(values.to_numpy()).all() or not values.gt(0).all().all()
            or not values.timestamp.diff().dropna().eq(60000).all()
            or not values.timestamp.mod(60000).eq(0).all()
            or not ((values.low <= values[['open', 'close']].min(axis=1))
                    & (values.high >= values[['open', 'close']].max(axis=1))
                    & (values.kc_lower < values.kc_middle)
                    & (values.kc_middle < values.kc_upper)).all()):
        raise ValueError('WAIT_INVALID_MARKET_DATA')
    if not closed.high.gt(closed.low).all():
        raise ValueError('WAIT_INVALID_MARKET_DATA')
    return validated


def _evaluate_small_to_big_entry(frame, price, code, account, symbol, diagnostics):
    from core.services.wait_authority import STATE_KEY, SYMBOLS

    def reject(reason):
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics['reason'] = reason
        return None

    if symbol not in SYMBOLS or code not in (None, *WAIT_CODES):
        return reject('BLOCKED_OBSOLETE_ENTRY_SIGNAL')
    state = getattr(account, 'position_meta', {}).get(STATE_KEY, {}).get(symbol, {})
    if any(c.get('phase') in ('CLAIMED', 'UNKNOWN', 'PARTIAL')
           for c in state.get('claims', {}).values()):
        return reject('BLOCKED_WAIT_ORDER_RECONCILIATION')
    if account is None:
        return reject('WAIT_NO_OBSERVED_LIVE_TRIGGER')
    if (symbol in getattr(account, 'positions', {})
            or symbol in getattr(account, 'closing_lock', set())
            or symbol in getattr(account, 'pending_limit_orders', {})):
        return reject('WAIT_POSITION_OR_PENDING_ORDER_BLOCK')
    try:
        frame = validated_entry_market_frame(frame)
        quote = float(frame.iloc[-1].close if price is None else price)
        stamp = frame.attrs.get('entry_quote_ms', frame.attrs.get('entry_finality_server_ms'))
        wait = WaitAuthority(account).candidate(symbol, quote, stamp)
        if not wait or code not in (None, wait['type']):
            return reject('WAIT_NO_OBSERVED_LIVE_TRIGGER')
        live = frame.iloc[-1]
        if (float(live.timestamp) != wait['confirmation_bar_id']
                or float(live.open) != wait['wait_live_open']):
            return reject('WAIT_LIVE_CANDLE_IDENTITY_MISMATCH')
        sign = 1 if wait['side'] == 'LONG' else -1
        edge = float(live['kc_upper' if sign == 1 else 'kc_lower'])
        if sign*(quote-edge) <= 0:
            return reject('WAIT_QUOTE_NOT_STRICTLY_OUTSIDE_KC')
        closed = closed_entry_candles(frame)
        defensive = closed['low' if sign == 1 else 'high'].tail(5).astype(float)
        level = float(defensive.min() if sign == 1 else defensive.max())
        stop = level-sign*.1*wait['entry_atr']
        if stop <= 0:
            return reject('WAIT_INVALID_DEFENSIVE_STOP')
        wait.update(structure_risk_stop=stop, exit_bar_id=None,
                    wait_outer_edge=edge, wait_outer_quote=quote,
                    wait_entry_policy='SMALL_OPPOSITE_LIVE_BIG_OUTSIDE_KC_V1',
                    entry_failure_level=edge)
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics['reason'] = wait['type']
        return wait
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, ArithmeticError):
        return reject('WAIT_INVALID_MARKET_DATA')


def live_breakout_kc_evidence(frame, quote, side):
    """Real live breakout owns direction; closed KC slope is diagnostic only."""
    try:
        closed = closed_entry_candles(frame)
        if side not in ('LONG', 'SHORT') or len(closed) < 2 or len(frame) != len(closed)+1:
            return None
        if live_body_breakout_side(frame, quote) != side:
            return None
        previous, latest = map(float, closed.kc_middle.iloc[-2:])
        if not all(math.isfinite(value) and value > 0 for value in (previous, latest)):
            return None
        direction = 'LONG' if latest > previous else 'SHORT' if latest < previous else 'FLAT'
        return dict(entry_qualification_policy='LIVE_BODY_BREAKOUT_V2',
                    breakout_previous_kc_middle=previous,
                    breakout_latest_kc_middle=latest, breakout_kc_direction=direction,
                    breakout_direction=side)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def live_ma_alignment_evidence(frame, quote, side):
    evidence = live_ma_values(frame, quote)
    if evidence is None or side not in ('LONG', 'SHORT'):
        return None
    ma5, ma15 = evidence['entry_live_ma5'], evidence['entry_live_ma15']
    if (1 if side == 'LONG' else -1)*(ma5-ma15) <= max(ma5, ma15)*1e-12:
        return None
    return evidence


def live_ma_direction_evidence(frame, quote, side, diagnostics):
    evidence = live_ma_values(frame, quote)
    try:
        closed = closed_entry_candles(frame)
        previous = {period: float(closed.iloc[-1][f'ma{period}']) for period in (5, 15)}
        if evidence is None or not all(math.isfinite(v) and v > 0 for v in previous.values()):
            raise ValueError('INVALID_MA_DATA')
        sign = 1 if side == 'LONG' else -1
        for period in (5, 15):
            current = evidence[f'entry_live_ma{period}']
            if sign*(current-previous[period]) <= max(current, previous[period])*1e-12:
                diagnostics['reason'] = f'WAIT_LIVE_MA{period}_DIRECTION'
                return None
        return dict(**evidence, entry_previous_ma5=previous[5],
                    entry_previous_ma15=previous[15])
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        diagnostics['reason'] = 'BLOCKED_LIVE_MA_DIRECTION_DATA'
        return None


def _entry_close_series(closed):
    return (closed.close_price_spike_filtered.fillna(closed.close)
            if 'close_price_spike_filtered' in closed.columns else closed.close)


def live_ma_values(frame, quote):
    """Adjust snapshot SMA15 to the same quote used to reconstruct live SMA5."""
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 4 or len(frame) != len(closed)+1:
            return None
        close_series = _entry_close_series(closed)
        closes = [float(v) for v in close_series.iloc[-4:]]
        live = frame.iloc[-1]
        filtered = live.get('close_price_spike_filtered')
        observed = live.close if filtered is None or pd.isna(filtered) else filtered
        quote, observed, ma15 = float(quote), float(observed), float(live.ma15)
        if not all(math.isfinite(v) and v > 0 for v in [quote, observed, ma15, *closes]):
            return None
        ma5 = (sum(closes)+quote)/5.
        ma15 += (quote-observed)/15.
        if not math.isfinite(ma15) or ma15 <= 0:
            return None
        return dict(entry_live_ma5=ma5, entry_live_ma15=ma15)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def live_breakout_ma5_evidence(frame, quote, side):
    """Quote-derived live MA5 must strictly advance from the last closed MA5."""
    try:
        closed = closed_entry_candles(frame)
        if side not in ('LONG', 'SHORT') or len(closed) < 4 or len(frame) != len(closed)+1:
            return None
        closes = [float(v) for v in _entry_close_series(closed).iloc[-4:]]
        previous = float(closed.iloc[-1].ma5)
        quote = float(quote)
        if not all(math.isfinite(v) and v > 0 for v in [quote, previous, *closes]):
            return None
        current = (sum(closes)+quote)/5.
        sign = 1 if side == 'LONG' else -1
        if sign*(current-previous) <= max(current, previous)*1e-12:
            return None
        return dict(breakout_previous_ma5=previous, breakout_live_ma5=current)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


MA5_MIN_ENTRY_SLOPE_ATR = 0.05

def ma5_entry_ready(frame, quote, side):
    """Require quote beyond live MA5 and directional MA5 movement of 0.05 closed ATR."""
    try:
        if side not in ('LONG', 'SHORT') or frame is None or len(frame) < 5:
            return False
        quote = float(quote)
        if not math.isfinite(quote) or quote <= 0:
            return False
        closed = closed_entry_candles(frame)
        if len(frame) == len(closed) + 1:
            atr = float(closed.iloc[-1]['atr'])
            previous = float(closed.iloc[-1]['ma5'])
            prices = [float(v) for v in closed['close'].iloc[-4:]] + [float(quote)]
            if len(prices) != 5 or not all(math.isfinite(v) and v > 0 for v in prices):
                return False
            current = sum(prices) / 5.
            # A newer quote must not reverse the forming MA5 relative to the
            # independently fetched live close, even if it remains below/above
            # the previous completed MA5. Equal quotes are neutral, not a veto.
            observed_close = float(frame.iloc[-1]['close'])
            if not math.isfinite(observed_close) or observed_close <= 0:
                return False
            sign = 1 if side == 'LONG' else -1
            if sign*(quote-observed_close) < -max(quote,observed_close)*1e-12:
                return False
        elif len(frame) == len(closed) and len(closed) >= 2:
            atr = float(closed.iloc[-1]['atr'])
            previous = float(closed.iloc[-2]['ma5'])
            current = float(closed.iloc[-1]['ma5'])
        else:
            return False
        if not all(math.isfinite(v) and v > 0 for v in (atr, previous, current)):
            return False
        sign = 1 if side == 'LONG' else -1
        if sign * (quote - current) <= 0:
            return False
        movement = sign * (current - previous)
        return movement > 0 and (movement >= MA5_MIN_ENTRY_SLOPE_ATR * atr or math.isclose(movement, MA5_MIN_ENTRY_SLOPE_ATR * atr, rel_tol=1e-10))
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def ma5_kc_trend_ready(frame, quote, side):
    """MA5 must stay outside without approaching the same-side KC outer rail."""
    try:
        if not ma5_entry_ready(frame, quote, side):
            return False
        closed = closed_entry_candles(frame)
        if len(frame) == len(closed) + 1:
            previous = float(closed.iloc[-1]['ma5'])
            current = (sum(float(v) for v in closed.close.iloc[-4:]) + float(quote)) / 5.
            previous_middle = float(closed.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
            current_middle = float(frame.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
        else:
            previous = float(closed.iloc[-2]['ma5'])
            current = float(closed.iloc[-1]['ma5'])
            previous_middle = float(closed.iloc[-2]['kc_upper' if side == 'LONG' else 'kc_lower'])
            current_middle = float(closed.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
        if not all(math.isfinite(v) and v > 0 for v in (previous,current,previous_middle,current_middle)):
            return False
        sign = 1 if side == 'LONG' else -1
        prior_gap = sign * (previous - previous_middle)
        gap = sign * (current - current_middle)
        tolerance = max(current, current_middle) * 1e-12
        return gap > tolerance and gap - prior_gap >= -tolerance
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def evaluate_channel_turn(frame, quote, code=None, symbol=''):
    """Retired: an intrachannel rebound does not authorize entry or reversal."""
    return None


def channel_long_body_side(frame, quote):
    """Inside-channel body producer; common gates still own final authority."""
    return channel_entry_side(frame, quote)


def confirmed_kc_entry_direction_ready(frame, side, *, check_outer_rails=True, moves=2):
    """Require confirmed closed KC direction and reject contradictory outer rails."""
    try:
        closed=closed_entry_candles(frame)
        if not isinstance(moves,int) or moves<1 or len(closed)<moves+1 or side not in ('LONG','SHORT'):
            return False
        middle=[float(v) for v in closed.kc_middle.iloc[-(moves+1):]]
        if not all(math.isfinite(v) and v>0 for v in middle):
            return False
        sign=1 if side=='LONG' else -1
        tolerance=max(middle)*1e-12
        if not all(sign*(b-a)>tolerance for a,b in zip(middle,middle[1:])):
            return False
        if not check_outer_rails:
            return True
        for rail in ('kc_upper','kc_lower'):
            values=[float(v) for v in closed[rail].iloc[-3:]]
            if not all(math.isfinite(v) and v>0 for v in values):
                return False
            if any(sign*(b-a) < -max(values)*1e-12 for a,b in zip(values,values[1:])):
                return False
        return True
    except (AttributeError,KeyError,TypeError,ValueError,IndexError):
        return False


def first_long_body_ready(frame, side):
    """A long body still inside KC cannot consume the first true breakout."""
    try:
        closed = closed_entry_candles(frame)
        if len(closed)<2 or side not in ('LONG','SHORT'):
            return False
        sign = 1 if side=='LONG' else -1
        row = closed.iloc[-1]
        opened,price,lower,upper = [float(row[k]) for k in ('open','close','kc_lower','kc_upper')]
        atr = float(closed.iloc[-2]['atr'])
        if not all(math.isfinite(v) and v>0 for v in (opened,price,lower,upper,atr)) or lower>=upper:
            return False
        edge = upper if side=='LONG' else lower
        prior_true_breakout = (lower<=opened<=upper and sign*(price-edge)>0
                               and sign*(price-opened)>=.5*atr)
        return not prior_true_breakout
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):
        return False


def outside_confirmation_pair_ready(frame,side):
    try:
        rows=closed_entry_candles(frame).iloc[-2:]
        if len(rows)!=2:
            return False
        sign=1 if side=='LONG' else -1
        rail='kc_upper' if side=='LONG' else 'kc_lower'
        for _,row in rows.iterrows():
            o,c,h,l,e=[float(row[k]) for k in ('open','close','high','low',rail)]
            if not all(math.isfinite(v) and v>0 for v in (o,c,h,l,e)) or h<=l or sign*(c-o)<=0 or abs(c-o)/(h-l)<.2 or sign*(c-e)<=0:
                return False
        return True
    except (AttributeError,KeyError,TypeError,ValueError,IndexError):
        return False


def live_long_body_side(frame, quote):
    """A live 0.5 closed-ATR directional body can qualify before a rail break."""
    try:
        closed=closed_entry_candles(frame)
        if len(closed)<2 or len(frame)!=len(closed)+1:
            return None
        row=frame.iloc[-1]
        opened,upper,lower,atr,quote=[float(v) for v in (row.open,row.kc_upper,row.kc_lower,closed.iloc[-1].atr,quote)]
        if not all(math.isfinite(v) and v>0 for v in (opened,upper,lower,atr,quote)) or not lower<=opened<=upper:
            return None
        body=quote-opened
        if abs(body)>=.5*atr:
            return 'LONG' if body>0 else 'SHORT'
    except (AttributeError,KeyError,TypeError,ValueError,IndexError):
        pass
    return None


def closed_ma5_entry_ready(frame, side):
    """Ordinary entries cannot use an intrabar rebound to mask falling closed MA5."""
    try:
        closed = closed_entry_candles(frame)
        if len(closed)<6 or side not in ('LONG','SHORT'):
            return False
        values=[float(v) for v in closed.close.iloc[-6:]]
        atr=float(closed.iloc[-1].atr)
        if not all(math.isfinite(v) and v>0 for v in [atr,*values]):
            return False
        movement=(values[-1]-values[0])/5
        sign=1 if side=='LONG' else -1
        return sign*movement >= MA5_MIN_ENTRY_SLOPE_ATR*atr
    except (AttributeError,KeyError,TypeError,ValueError,IndexError):
        return False


LIVE_OPPOSITE_MAX_CHANNEL_RATIO = 0.5


def live_breakout_history_ready(frame, side, quote=None):
    """Opposite candle size uses full high-low range versus its own KC width."""
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 2 or side not in ('LONG','SHORT'):
            return False
        sign = 1 if side == 'LONG' else -1
        for _, row in closed.iloc[-2:].iterrows():
            opened, closing, high, low, atr, lower, upper = [float(row[k]) for k in ('open','close','high','low','atr','kc_lower','kc_upper')]
            if (not all(math.isfinite(v) and v > 0 for v in (opened,closing,high,low,atr,lower,upper))
                    or lower>=upper or not low<=min(opened,closing)<=max(opened,closing)<=high):
                return False
            body = sign*(closing-opened)
            if body < 0:
                span = high-low
                limit = LIVE_OPPOSITE_MAX_CHANNEL_RATIO*(upper-lower)
                if span<=0 or (span>limit and not math.isclose(span,limit,rel_tol=1e-10)):
                    return False
                adverse_wick = (high-max(opened,closing)) if side=='LONG' else (min(opened,closing)-low)
                if adverse_wick >= .5*atr and adverse_wick >= 2*abs(body):
                    reclaimed = False
                    if quote is not None:
                        value = float(quote)
                        reclaimed = math.isfinite(value) and value > 0 and sign*(value-(high if side=='LONG' else low)) > 0
                    if not reclaimed:
                        return False
        return True
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):
        return False


def live_peak_retracement_ready(frame, quote, side):
    """Use the fetched current candle extreme, not a future wick or invented ticks."""
    try:
        closed=closed_entry_candles(frame)
        if side not in ('LONG','SHORT') or len(frame)!=len(closed)+1:return False
        row=frame.iloc[-1]
        opened,high,low,closing,atr,q=map(float,(row.open,row.high,row.low,row.close,closed.iloc[-1].atr,quote))
        if not all(math.isfinite(v) and v>0 for v in (opened,high,low,closing,atr,q)):return False
        if not low<=min(opened,closing)<=max(opened,closing)<=high:return False
        retreat=max(0.,high-q) if side=='LONG' else max(0.,q-low)
        return retreat <= .5*atr or math.isclose(retreat,.5*atr,rel_tol=1e-10)
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):return False


ORDINARY_ADVERSE_BODY_ATR = 0.5


def ordinary_live_body_safe(frame, quote, side):
    """Reject a long adverse forming body using original open and closed ATR."""
    try:
        closed = closed_entry_candles(frame)
        if side not in ('LONG', 'SHORT') or closed.empty or len(frame) != len(closed)+1:
            return False
        opened = float(frame.iloc[-1]['open'])
        atr = float(closed.iloc[-1]['atr'])
        quote = float(quote)
        if not all(math.isfinite(v) and v > 0 for v in (opened, atr, quote)):
            return False
        adverse = (1 if side == 'LONG' else -1) * (opened-quote)
        threshold = ORDINARY_ADVERSE_BODY_ATR*atr
        return adverse < threshold and not math.isclose(adverse, threshold, rel_tol=1e-10, abs_tol=0.)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def ordinary_breakout_pair_ready(frame, quote, side):
    """Two closed same-color bodies beyond the rail, including outside continuation."""
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 2 or len(frame) != len(closed)+1 or side not in ('LONG','SHORT'):
            return False
        first, second = closed.iloc[-2], closed.iloc[-1]
        sign = 1 if side == 'LONG' else -1
        key = 'kc_upper' if side == 'LONG' else 'kc_lower'
        for row in (first,second):
            opened, closing, high, low, rail = [float(row[k]) for k in ('open','close','high','low',key)]
            if not all(math.isfinite(v) and v > 0 for v in (opened,closing,high,low,rail)):
                return False
            span = high-low
            if span <= 0 or sign*(closing-opened) <= 0 or abs(closing-opened)/span < .20 or sign*(closing-rail) <= 0:
                return False
        edge = float(frame.iloc[-1][key]);quote=float(quote)
        return math.isfinite(edge) and edge > 0 and math.isfinite(quote) and quote > 0 and sign*(quote-edge) > 0
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):
        return False


def evaluate_ma_cross_trend(frame, quote, code=None, symbol=''):
    """Quote-derived MA5/MA15 alignment and slope with confirmed three-rail KC trend."""
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 15 or len(frame) != len(closed)+1:
            return None
        quote = float(quote)
        prices = [float(v) for v in closed.close.iloc[-14:]]
        atr = float(closed.iloc[-1].atr)
        ma5 = (sum(prices[-4:])+quote)/5.
        ma15 = (sum(prices)+quote)/15.
        last5 = float(closed.iloc[-1].ma5)
        last15 = float(closed.iloc[-1].ma15)
        if not all(math.isfinite(v) and v > 0 for v in [quote,atr,ma5,ma15,last5,last15,*prices]):
            return None
        side = 'LONG' if ma5 > ma15 else 'SHORT' if ma5 < ma15 else None
        if side is None:
            return None
        sign = 1 if side == 'LONG' else -1
        signal = 'KC_MA_CROSS_TREND_'+side
        if code not in (None,signal) or sign*(ma15-last15) <= 0 or not ma5_entry_ready(frame,quote,side):
            return None
        for key in ('kc_upper','kc_middle','kc_lower'):
            previous, current = [float(v) for v in closed[key].iloc[-2:]]
            if not all(math.isfinite(v) and v > 0 for v in (previous,current)) or sign*(current-previous) <= 0:
                return None
        if not live_candle_color_ready(frame,quote,side) or not live_adverse_entry_safe(frame,quote,side):
            return None
        stamp = float(frame.iloc[-1].timestamp)
        prev = float(closed.iloc[-1].timestamp)
        return dict(action='ENTER',side=side,type=signal,reason=signal,price=quote,
                    entry_atr=atr,confirmation_bar_id=stamp,close_price=float(closed.iloc[-1].close),
                    intrabar=True,entry_phase='KC_MA_CROSS_TREND',breakout_bar_id=stamp,
                    pair_confirmation_bar_id=prev,third_bar_id=stamp,
                    pending_signal_id=f'{symbol}_MA_TREND_{int(stamp)}_{side}',
                    pending_second_bar_id=prev,pending_wait_bars=0,pending_max_wait_bars=0)
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):
        return None


MAX_ENTRY_ADVANCE_ATR = 0.50


def entry_chase_ready(frame, quote, side):
    """Reject entry more than 0.5 prior closed ATR past the latest closed close."""
    try:
        if side not in ('LONG','SHORT'):
            return False
        closed = closed_entry_candles(frame)
        quote, reference, atr = float(quote), float(closed.iloc[-1].close), float(closed.iloc[-1].atr)
        if not all(math.isfinite(v) and v > 0 for v in (quote,reference,atr)):
            return False
        advance = (1 if side == 'LONG' else -1)*(quote-reference)
        limit = MAX_ENTRY_ADVANCE_ATR*atr
        return advance <= limit or math.isclose(advance,limit,rel_tol=1e-10)
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):
        return False


def evaluate_ma3_reverse(frame, quote, code=None, *, account=None, symbol=''):
    """One opposite entry after a matching successful MA3 close in its candle."""
    try:
        closes = [t for t in getattr(account, 'trades', []) if t.get('symbol') == symbol
                  and t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT')]
        if not closes:
            return None
        fill = max(closes, key=lambda t: float(t['id']))
        if (fill.get('reason') != 'Channel Swing EXIT_MA3_CONFIRMED_TURN'
                or ('status' in fill and fill['status'] != 'CLOSED')):
            return None
        stamp = float(frame.iloc[-1].timestamp)
        close_id = float(fill['id'])
        if not math.isfinite(close_id) or close_id <= 0 or math.floor(close_id/60000)*60000 != stamp:
            return None
        if any(t.get('symbol') == symbol and t.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
               and float(t.get('id') or 0) >= close_id for t in getattr(account, 'trades', [])):
            return None
        side = 'SHORT' if fill['action'] == 'CLOSE_LONG' else 'LONG'
        signal = 'KC_MA3_TURN_REVERSE_' + side
        if code not in (None, signal):
            return None
        trend = evaluate_ma_cross_trend(frame, quote, symbol=symbol)
        if not trend or trend['side'] != side or not entry_chase_ready(frame, quote, side):
            return None
        sign = 1 if side == 'LONG' else -1
        quote = float(quote)
        atr = float(frame.iloc[-2].atr)
        prices = [float(v) for v in frame.close.iloc[-5:-1]]
        if len(prices) != 4 or not all(math.isfinite(v) and v > 0 for v in [quote, atr, *prices]):
            return None
        ma3 = (sum(prices[-2:])+quote)/3.
        previous_ma3 = float(frame.iloc[-2].ma3)
        ma5 = (sum(prices)+quote)/5.
        if (not math.isfinite(previous_ma3) or previous_ma3 <= 0
                or sign*(ma3-previous_ma3) < .10*atr
                or not ma5_entry_ready(frame, quote, side)
                or sign*(quote-ma5) <= 0 or not live_adverse_entry_safe(frame, quote, side)):
            return None
        return dict(action='ENTER',side=side,type=signal,reason=signal,price=quote,
                    entry_atr=atr,confirmation_bar_id=stamp,close_price=float(frame.iloc[-2].close),
                    intrabar=True,entry_phase='KC_MA3_TURN_REVERSE',breakout_bar_id=stamp,
                    pair_confirmation_bar_id=float(frame.iloc[-2].timestamp),third_bar_id=stamp,
                    pending_signal_id=f'{symbol}_MA3_REVERSE_{int(close_id)}_{side}',
                    pending_second_bar_id=float(frame.iloc[-2].timestamp),pending_wait_bars=0,
                    pending_max_wait_bars=0,same_bar_close_id=close_id)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def continuation_breakout_origin(frame, side):
    """Find a real closed body breakout with no later closed return inside."""
    try:
        closed=closed_entry_candles(frame).iloc[-60:]
        if side not in ('LONG','SHORT'):
            return None
        sign=1 if side=='LONG' else -1
        rail='kc_upper' if side=='LONG' else 'kc_lower'
        for _,row in closed.iloc[::-1].iterrows():
            values=[float(row[k]) for k in ('open','close','high','low','kc_lower','kc_upper','timestamp')]
            if not all(math.isfinite(v) and v>0 for v in values):
                return None
            opened,price,high,low,lower,upper,stamp=values
            if sign*(price-float(row[rail]))<=0:
                return None
            if (lower<=opened<=upper and high>low and sign*(price-opened)>0
                    and abs(price-opened)/(high-low)>=.2):
                return stamp
    except (AttributeError,KeyError,TypeError,ValueError,IndexError):
        pass
    return None


def evaluate_pullback_resume(frame, quote, code=None):
    """A completed pullback pivot and renewed price breakout authorize resume."""
    try:
        closed=closed_entry_candles(frame)
        if len(closed)<5 or len(frame)!=len(closed)+1:return None
        side=ck_direction(frame)
        if side not in ('LONG','SHORT') or code not in (None,PULLBACK_PHASE+'_'+side):return None
        origin=continuation_breakout_origin(frame,side)
        if origin is None:return None
        sign=1 if side=='LONG' else -1
        rows=closed.iloc[-3:]
        values=[[float(r[k]) for k in ('timestamp','open','high','low','close')] for _,r in rows.iterrows()]
        live=frame.iloc[-1];q=float(quote);atr=float(closed.iloc[-1].atr)
        if not all(math.isfinite(v) and v>0 for row in values for v in row):return None
        if not all(math.isfinite(v) and v>0 for v in (q,atr,float(live.open))):return None
        if any(not l<=min(o,c)<=max(o,c)<=h for _,o,h,l,c in values):return None
        if any(b[0]-a[0]!=60000 for a,b in zip(values,values[1:])) or float(live.timestamp)-values[-1][0]!=60000:return None
        index=3 if side=='LONG' else 2
        level=values[1][index]
        if any(sign*(r[index]-level)<=0 for r in (values[0],values[2])):return None
        last=values[-1]
        edge=last[2] if side=='LONG' else last[3]
        # The confirmation candle itself must resume direction. Live price then
        # breaks its extreme without having breached the defended pivot.
        if sign*(last[4]-last[1])<=0 or sign*(q-edge)<=0 or sign*(q-float(live.open))<=0:return None
        extreme=float(live.low if side=='LONG' else live.high)
        if not math.isfinite(extreme) or extreme<=0 or sign*(extreme-level)<=0:return None
        if sign*(q-edge)>.5*atr:return None
        rail=float(live.kc_upper if side=='LONG' else live.kc_lower)
        if not math.isfinite(rail) or sign*(q-rail)<=0:return None
        if not ma5_kc_trend_ready(frame,q,side):return None
        return dict(side=side,code=PULLBACK_PHASE+'_'+side,phase=PULLBACK_PHASE,
                    continuation_origin_bar_id=origin,pullback_level=level,
                    pullback_pivot_ms=values[1][0],pullback_confirmed_ms=last[0])
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):return None


def evaluate_continuation_entry(frame, quote, code=None, symbol: str = '', *, account=None):
    """Continuation entry for sustained trend outside the outer rail.

    Permits opening when a prior breakout was missed or after a position was closed,
    provided that the KC direction, live MA3 direction, live candle color, and outer band position
    remain consistently in favor of the trend.
    """
    if symbol == CAP_SYMBOL:
        decision = evaluate_entry_contract(frame, quote, code, symbol=symbol, account=account)
        return decision if decision and decision['type'] in CAP_CONTINUATION_CODES else None
    try:
        if frame is None or len(closed_entry_candles(frame)) != len(frame)-1:
            return None
        side = ck_direction(frame)
        if not side:
            return None
        # A historical breakout is setup, never authority for an unconfirmed pair.
        if not ordinary_breakout_pair_ready(frame, quote, side):
            return None
        if not ma5_entry_ready(frame, quote, side):
            return None
        origin = continuation_breakout_origin(frame, side)
        if origin is None:
            return None
        signal = 'KC_OUTSIDE_' + side
        if code is not None and code != signal:
            return None

        quote = float(quote)
        atr = float(frame.iloc[-2]['atr'])
        sign = 1 if side == 'LONG' else -1

        if len(frame) < 5 or not math.isfinite(atr) or atr <= 0:
            return None
        # A forming directional candle resumes a previously confirmed breakout.
        opened = float(frame.iloc[-1]['open'])
        if not math.isfinite(opened) or opened<=0 or not math.isfinite(quote) or quote<=0 or sign*(quote-opened)<=0:
            return None

        if not live_candle_color_ready(frame, quote, side):
            return None
        if not live_adverse_entry_safe(frame, quote, side):
            return None

        edge = float(frame.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
        lower, upper = float(frame.iloc[-1]['kc_lower']), float(frame.iloc[-1]['kc_upper'])
        if not all(math.isfinite(v) and v > 0 for v in (quote, lower, upper)) or lower >= upper:
            return None
        
        # [NEW GATE RULE: 必須在 kc 線及 ma5 外，若在 kc 內或 ma5 內就不要開倉]
        if sign * (quote - edge) <= 0:
            return None

        distance = sign * (quote - edge) / atr
        if distance <= 0 or distance > 3.0:
            return None

        live = frame.iloc[-1]
        stamp = float(live['timestamp'])
        prev_stamp = float(frame.iloc[-2]['timestamp'])
        if not all(math.isfinite(v) and v > 0 for v in (stamp, prev_stamp)) or stamp-prev_stamp != 60000:
            return None

        return dict(action='ENTER', side=side, type=signal, reason=signal,
                    price=quote, entry_atr=atr, confirmation_bar_id=stamp,
                    close_price=float(frame.iloc[-2]['close']), intrabar=True,
                    entry_phase='KC_CONTINUATION_ENTRY', continuation_origin_bar_id=origin,
                    breakout_bar_id=stamp,
                    pair_confirmation_bar_id=prev_stamp,
                    third_bar_id=stamp,
                    pending_signal_id=f"{symbol}_CONTINUATION_{int(stamp)}_{side}",
                    pending_second_bar_id=prev_stamp,
                    pending_wait_bars=1, pending_max_wait_bars=1,
                    kc_confirmation_edge=edge,
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
    # 無論漲勢或跌勢，只要出現十字線（走到後面時）就不要再開倉
    if is_entry_doji(live, quote):
        return 'BLOCKED_LIVE_DOJI'
    bars = [row for _, row in closed.tail(2).iterrows()]
    for row in bars:
        if is_entry_doji(row):
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


def first_live_breakout_ready(frame, quote, side):
    """First true inside-to-outside live body breakout; no mandatory small setup."""
    try:
        if (live_body_breakout_side(frame, quote) != side
                or not first_long_body_ready(frame, side)):
            return False
        atr = float(closed_entry_candles(frame).iloc[-1]['atr'])
        sign = 1 if side == 'LONG' else -1
        body = sign * (float(quote) - float(frame.iloc[-1]['open']))
        return body >= .5 * atr or math.isclose(body, .5 * atr, rel_tol=1e-10)
    except (KeyError, AttributeError, TypeError, ValueError, IndexError, OverflowError):
        return False


def complete_instant_pattern_ready(frame, quote, side):
    """Two or three completed small setup candles, then a live >=1 ATR body."""
    try:
        closed = closed_entry_candles(frame)
        if side not in ('LONG', 'SHORT') or len(frame) != len(closed) + 1:
            return False
        sign = 1 if side == 'LONG' else -1
        atr = float(closed.iloc[-1]['atr'])
        live = frame.iloc[-1]
        quote = float(quote)
        opening, lower, upper = map(float, (live.open, live.kc_lower, live.kc_upper))
        if not all(math.isfinite(v) and v > 0 for v in (atr, quote, opening, lower, upper)) or lower >= upper:
            return False
        body = sign * (quote - opening)
        if body < atr and not math.isclose(body, atr, rel_tol=1e-10):
            return False
        if sign * (quote - (upper if side == 'LONG' else lower)) <= 0:
            return False
        for bridges in (1, 2):
            if len(closed) < bridges + 1:
                continue
            rows = closed.iloc[-(bridges + 1):]
            valid = True
            opposite_small = False
            previous_stamp = None
            for offset, (_, row) in enumerate(rows.iterrows()):
                o, c, h, l, stamp = map(float, (row.open, row.close, row.high, row.low, row.timestamp))
                if not all(math.isfinite(v) and v > 0 for v in (o,c,h,l,stamp)) or not l <= min(o,c) <= max(o,c) <= h or h <= l:
                    valid = False; break
                # Owner permits doji as setup only; never as live authority.
                if abs(c-o) > .25 * atr + atr * 1e-10:
                    valid = False; break
                if previous_stamp is not None and stamp - previous_stamp != 60000:
                    valid = False; break
                if sign*(c-o) < 0 and not is_entry_doji(row):
                    opposite_small = True
                previous_stamp = stamp
            if valid and opposite_small and float(live.timestamp) - previous_stamp == 60000:
                return True
        return False
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def _evaluate_strategy_contract(frame, price=None, code=None, *, account=None,
                            symbol="", diagnostics=None, evaluate_held=False):
    def reject(reason):
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics["reason"] = reason
        return None
    reject("WAIT_VALID_ENTRY_DATA")
    if code is not None and code not in ENTRY_CODES:
        return reject("BLOCKED_OBSOLETE_ENTRY_SIGNAL")
    cap = symbol == CAP_SYMBOL
    if not cap and code in CAP_CONTINUATION_CODES:
        return reject('BLOCKED_CAP_AUTHORITY_WRONG_SYMBOL')
    if not evaluate_held and account is not None and symbol in getattr(account, "positions", {}):
        return reject("WAIT_EXISTING_POSITION")
    try:
        if frame is None or frame.empty or frame.attrs.get('timeframe_ms', 60000) != 60000:
            return None
        if 'is_closed' not in frame or not all(isinstance(v, (bool, np.bool_)) for v in frame.is_closed):
            return None
        ma5_frame = frame
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
        # A persisted successful close can authorize one fresh entry in its candle.
        exit_bar = None
        close_fill = None
        for trade in getattr(account, 'trades', []):
            if trade.get('symbol') == symbol and trade.get('action') in ('CLOSE_LONG','CLOSE_SHORT'):
                stamp = float(trade['id'])
                if not math.isfinite(stamp) or stamp <= 0:
                    return reject('WAIT_VALID_CLOSE_HISTORY')
                if trade.get('status') not in (None, 'CLOSED'):
                    continue
                if close_fill is None or stamp > float(close_fill['id']):
                    close_fill = trade
                bar = math.floor(stamp/60000)*60000
                exit_bar = max(exit_bar or bar, bar)
        saved_close = getattr(account, 'last_closed_at', {}).get(symbol)
        if saved_close is not None:
            saved_close = float(saved_close)
            if not math.isfinite(saved_close) or saved_close <= 0:
                return reject('WAIT_VALID_CLOSE_HISTORY')
            saved_bar = math.floor(saved_close/60)*60000
            exit_bar = max(exit_bar or saved_bar, saved_bar)
        # The independent pivot authority does not inherit breakout qualifiers.
        if len(frame) != len(closed) + 1:
            return reject('WAIT_FORMING_BREAKOUT_BAR')
        pivot_decision = evaluate_ma5_outer_pivot_entry(frame, quote, symbol)
        if account is not None and symbol in getattr(account, 'positions', {}):
            pivot_decision = None
        breakout_code = code if code not in MA5_PIVOT_CODES else None
        fast_side = live_body_breakout_side(frame, quote)
        fast_code = 'KC_LIVE_BODY_BREAKOUT_' + fast_side if fast_side else None
        fast_evidence = live_breakout_kc_evidence(frame, quote, fast_side)
        if pivot_decision and fast_evidence and fast_side != pivot_decision['side']:
            return reject('BLOCKED_OPPOSITE_ENTRY_AUTHORITIES')
        if fast_side and code in LIVE_BODY_BREAKOUT_CODES and fast_evidence is None:
            return reject('BLOCKED_LIVE_BREAKOUT_INVALID_KC_DATA')
        if fast_evidence and breakout_code in (None, fast_code):
            stamp = float(live.timestamp)
            prev_stamp = float(latest.timestamp)
            decision = dict(action='ENTER', side=fast_side, type=fast_code,
                            reason=fast_code, price=quote, entry_atr=float(latest.atr),
                            confirmation_bar_id=stamp, close_price=float(latest.close),
                            intrabar=True, entry_phase='KC_LIVE_BODY_BREAKOUT',
                            pending_signal_id=f'{symbol}_LIVE_BODY_{int(stamp)}_{fast_side}',
                            breakout_bar_id=stamp, pair_confirmation_bar_id=prev_stamp,
                            third_bar_id=stamp, pending_second_bar_id=prev_stamp,
                            pending_wait_bars=0, pending_max_wait_bars=0,
                            breakout_live_open=float(live.open),
                            breakout_live_edge=float(live['kc_upper' if fast_side == 'LONG' else 'kc_lower']),
                            breakout_body=(1 if fast_side == 'LONG' else -1)*(quote-float(live.open)),
                            breakout_min_body=LIVE_BREAKOUT_BODY_ATR*float(latest.atr))
            decision.update(fast_evidence)
        else:
            decision = evaluate_kc_pending_entry(closed, quote, breakout_code, symbol=symbol, live=live)
            if decision.get('action') == 'ENTER':
                sign = 1 if decision['side'] == 'LONG' else -1
                first = closed.iloc[-2]
                edge = float(first['kc_upper' if sign == 1 else 'kc_lower'])
                if not (float(first.kc_lower) <= float(first.open) <= float(first.kc_upper)
                        and sign * (float(first.close) - edge) > 0):
                    decision = dict(reason='WAIT_FIRST_CLOSED_INSIDE_TO_OUTSIDE_BREAKOUT')
                elif not ordinary_breakout_pair_ready(ma5_frame, quote, decision['side']):
                    decision = dict(reason='WAIT_TWO_CLOSED_SAME_COLOR_BODY_BREAKOUT')
                elif not live_ma3_direction_ready(ma5_frame, quote, decision['side']):
                    decision = dict(reason='BLOCKED_LIVE_MA3_FLAT_OR_OPPOSITE')
        if cap and (code in CAP_CONTINUATION_CODES or decision.get('action') != 'ENTER'):
            continuation = continuation_decision(frame, quote, account, code)
            if continuation:
                decision = continuation
        # Other producers retain their same-side outer-rail chase limit.
        # Decimal avoids binary subtraction turning an exact 0.5 boundary into
        # an over-limit value; it never tolerates a genuinely larger distance.
        if decision.get('action') == 'ENTER' and decision['type'] not in LIVE_BODY_BREAKOUT_CODES:
            sign = 1 if decision['side'] == 'LONG' else -1
            edge = float(live['kc_upper' if sign == 1 else 'kc_lower'])
            scale = float(latest.atr)
            distance = sign * (Decimal(str(quote)) - Decimal(str(edge)))
            if distance <= 0 or distance > Decimal('0.5') * Decimal(str(scale)):
                decision = dict(reason='BLOCKED_OUTSIDE_RAIL_CHASE_OVER_0_5_ATR')
            else:
                decision.update(kc_confirmation_edge=edge, kc_distance_atr=float(distance)/scale,
                                kc_max_distance_atr=.5)
        if decision.get('action') == 'ENTER' and decision['type'] not in LIVE_BODY_BREAKOUT_CODES:
            ma5_evidence = live_breakout_ma5_evidence(ma5_frame, quote, decision['side'])
            if ma5_evidence is None:
                decision = dict(reason='BLOCKED_LIVE_MA5_FLAT_OPPOSITE_OR_INVALID')
            else:
                decision.update(ma5_evidence)
        if (pivot_decision and decision.get('action') == 'ENTER'
                and pivot_decision['side'] != decision['side']):
            return reject('BLOCKED_OPPOSITE_ENTRY_AUTHORITIES')
        if code in MA5_PIVOT_CODES:
            if pivot_decision is None or pivot_decision['type'] != code:
                return reject('WAIT_SUSTAINED_OUTER_RUN_PIVOT_RETURN')
            decision = pivot_decision
        elif code is None and decision.get('action') != 'ENTER' and pivot_decision:
            decision = pivot_decision
        if decision.get('action') != 'ENTER':
            return reject(decision.get('reason', 'WAIT_NEW_KC_BREAKOUT'))
        if cap and decision['entry_phase'] == 'KC_2BAR_CLOSED_CONFIRM' and account is not None:
            from core.services.cap_breakout_entry import STATE_KEY as CAP_STATE_KEY
            cap_state = getattr(account, 'position_meta', {}).get(CAP_STATE_KEY, {}).get(symbol, {})
            if decision['pair_confirmation_bar_id'] <= cap_state.get('cancelled_second_ms', 0):
                return reject('WAIT_CAP_NEW_PAIR_AFTER_RAIL_RETURN')
        if code is not None and decision['type'] != code:
            return reject('BLOCKED_ENTRY_AUTHORITY_MISMATCH')
        if decision['type'] not in LIVE_BODY_BREAKOUT_CODES:
            directional_ma5 = live_breakout_ma5_evidence(ma5_frame, quote, decision['side'])
            if directional_ma5 is None:
                return reject('BLOCKED_LIVE_MA5_FLAT_OPPOSITE_OR_INVALID')
            decision.update(directional_ma5)
            alignment = live_ma_alignment_evidence(ma5_frame, quote, decision['side'])
            if alignment is None:
                return reject('BLOCKED_LIVE_MA5_MA15_ALIGNMENT')
            decision.update(alignment)
            if cap and decision['type'] not in MA5_PIVOT_CODES:
                sign = 1 if decision['side'] == 'LONG' else -1
                if sign*(quote-alignment['entry_live_ma5']) <= max(quote, alignment['entry_live_ma5'])*1e-12:
                    return reject('BLOCKED_CAP_QUOTE_INSIDE_MA5')
                decision['cap_quote_ma5'] = alignment['entry_live_ma5']
            chop_status, chop_evidence = evaluate_entry_chop(ma5_frame)
            if chop_evidence is None:
                return reject(chop_status)
            decision.update(chop_evidence)
        if cap and decision['entry_phase'] in ('KC_2BAR_CLOSED_CONFIRM', 'KC_CONTINUATION_ENTRY'):
            from core.services.cap_breakout_entry import directional_price_progress
            progress = directional_price_progress(frame, quote, decision['side'])
            if progress is None:
                return reject('BLOCKED_CAP_PRICE_NOT_ADVANCING')
            decision.update(progress)
        if not live_adverse_entry_safe(ma5_frame, quote, decision['side']):
            return reject('BLOCKED_LIVE_ADVERSE_ABNORMAL')
        same_bar_close = (close_fill is not None and float(live.timestamp) == exit_bar
                          and math.floor(float(close_fill['id'])/60000)*60000 == exit_bar)

        # Only a matched successful opposite reversal close grants one same-bar entry.
        from core.services.auto_reverse import matched_ticket
        auto_reverse = matched_ticket(account, symbol, decision['side'], float(live.timestamp)) if account is not None else None
        if exit_bar is not None and float(live.timestamp) <= exit_bar and not auto_reverse:
            return reject('WAIT_POST_EXIT_NEW_FORMATION')
        if same_bar_close:
            close_id = float(close_fill['id'])
            if any(t.get('symbol') == symbol and t.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                   and float(t.get('id') or 0) >= close_id for t in getattr(account, 'trades', [])):
                return reject('BLOCKED_CLOSE_ALREADY_REOPENED')
            decision['same_bar_close_id'] = close_id
            decision['pending_signal_id'] += f'_AFTER_CLOSE_{close_id}'
        # Persisted successful fills own deduplication, including after restart.
        for trade in getattr(account, 'trades', []):
            if (trade.get('symbol') == symbol and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                    and (trade.get('entry_snapshot') or {}).get('pending_signal_id') == decision['pending_signal_id']):
                return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')
        # Fresh completed-candle defense for fixed-budget structural sizing.
        sign=1 if decision['side']=='LONG' else -1
        defensive=closed['low' if sign==1 else 'high'].iloc[-5:]
        level=float(defensive.min() if sign==1 else defensive.max())
        scale=float(latest.atr)
        if not math.isfinite(level) or level<=0 or not math.isfinite(scale) or scale<=0:
            return reject('WAIT_VALID_STRUCTURAL_RISK_STOP')
        decision['structure_risk_stop']=level-sign*.1*scale
        failure_level=None
        phase=decision['entry_phase']
        if phase=='KC_LIVE_BODY_BREAKOUT':
            failure_level=float(live['kc_upper' if sign==1 else 'kc_lower'])
        elif phase==MA5_PIVOT_PHASE:
            failure_level=decision['ma5_entry_boundary']
        elif phase in (OUTER_SMALL_PAIR_PHASE,'KC_2BAR_CLOSED_CONFIRM','KC_CONTINUATION_ENTRY'):
            recent=closed['high' if sign==1 else 'low'].iloc[-3:]
            failure_level=float(recent.max() if sign==1 else recent.min())
        elif phase==REVERSAL_PHASE:
            failure_level=decision.get('reversal_level')
        elif phase==STRUCTURE_PHASE:
            failure_level=decision.get('structure_entry_level')
        elif phase==PULLBACK_PHASE:
            failure_level=decision.get('pullback_level')
        if failure_level is not None and math.isfinite(float(failure_level)) and sign*(quote-float(failure_level))>0:
            decision['entry_failure_level']=float(failure_level)

        decision['exit_bar_id'] = exit_bar
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics['reason'] = decision['type']
        return decision
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return reject('WAIT_VALID_ENTRY_DATA')


def _evaluate_authority_contract(frame, price=None, code=None, *, account=None,
                            symbol="", diagnostics=None, evaluate_held=False):
    from core.services.wait_authority import STATE_KEY as WAIT_STATE_KEY
    wait_state = getattr(account, "position_meta", {}).get(WAIT_STATE_KEY, {}).get(symbol, {})
    if any(c.get("phase") in ("CLAIMED", "UNKNOWN", "PARTIAL")
           for c in wait_state.get("claims", {}).values()):
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics["reason"] = "BLOCKED_WAIT_ORDER_RECONCILIATION"
        return None
    wait = None
    if account is not None and frame is not None and not frame.empty and not evaluate_held:
        stamp = frame.attrs.get("entry_quote_ms", frame.attrs.get("entry_finality_server_ms"))
        quote = frame.iloc[-1].close if price is None else price
        wait = WaitAuthority(account).candidate(symbol, quote, stamp)
    if wait is None and code not in WAIT_CODES:
        return _evaluate_strategy_contract(frame, price, code, account=account,
                                           symbol=symbol, diagnostics=diagnostics,
                                           evaluate_held=evaluate_held)
    other = _evaluate_strategy_contract(frame, price, None, account=account,
                                        symbol=symbol, evaluate_held=evaluate_held)

    def reject(reason):
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics["reason"] = reason
        return None

    if wait and other and wait["side"] != other["side"]:
        return reject("BLOCKED_OPPOSITE_ENTRY_AUTHORITIES")
    if code is None and other and other['type'] in LIVE_BODY_BREAKOUT_CODES:
        return other
    if code not in (None, *WAIT_CODES):
        return _evaluate_strategy_contract(frame, price, code, account=account,
                                           symbol=symbol, diagnostics=diagnostics,
                                           evaluate_held=evaluate_held)
    if not wait or code not in (None, wait["type"]):
        return reject("WAIT_NO_OBSERVED_LIVE_TRIGGER")
    if (symbol in getattr(account, "closing_lock", set())
            or symbol in getattr(account, "pending_limit_orders", {})):
        return reject("WAIT_POSITION_OR_PENDING_ORDER_BLOCK")
    sign = 1 if wait["side"] == "LONG" else -1
    # WAIT forms independently; the Owner-approved shared swing gate follows.
    closed = closed_entry_candles(frame)
    defensive = closed["low" if sign == 1 else "high"].tail(5).astype(float)
    if not np.isfinite(defensive).all() or not defensive.gt(0).all():
        return reject("WAIT_INVALID_DEFENSIVE_DATA")
    level = float(defensive.min() if sign == 1 else defensive.max())
    stop = level-sign*.1*wait["entry_atr"]
    if stop <= 0:
        return reject("WAIT_INVALID_DEFENSIVE_STOP")
    wait.update(structure_risk_stop=stop, exit_bar_id=None)
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics["reason"] = wait["type"]
    return wait


def evaluate_entry_contract(frame, price=None, code=None, *, account=None,
                            symbol="", diagnostics=None, evaluate_held=False):
    from core.services.wait_authority import STATE_KEY, SYMBOLS

    def reject(reason):
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics['reason'] = reason
        return None

    if symbol not in SYMBOLS or code not in (None, *ENTRY_CODES):
        return reject('BLOCKED_OBSOLETE_ENTRY_SIGNAL')
    state = getattr(account, 'position_meta', {}).get(STATE_KEY, {}).get(symbol, {})
    if any(c.get('phase') in ('CLAIMED', 'UNKNOWN', 'PARTIAL')
           for c in state.get('claims', {}).values()):
        return reject('BLOCKED_WAIT_ORDER_RECONCILIATION')
    if (symbol in getattr(account, 'positions', {})
            or symbol in getattr(account, 'closing_lock', set())
            or symbol in getattr(account, 'pending_limit_orders', {})):
        return reject('WAIT_POSITION_OR_PENDING_ORDER_BLOCK')
    try:
        frame = validated_entry_market_frame(frame)
        quote = float(frame.iloc[-1].close if price is None else price)
        if not math.isfinite(quote) or quote <= 0:
            return reject('WAIT_INVALID_MARKET_DATA')
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, ArithmeticError):
        return reject('WAIT_INVALID_MARKET_DATA')
    outside_diagnostics = {}
    try:
        decision = evaluate_breakout(frame, quote, account, symbol, outside_diagnostics, code)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return reject('BLOCKED_INVALID_ACCOUNT_ENTRY_HISTORY')
    if not decision or code not in (None, decision['type']):
        return reject(outside_diagnostics.get('reason', 'WAIT_QUOTE_NOT_STRICTLY_OUTSIDE_KC'))
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics['reason'] = decision['type']
    return decision
