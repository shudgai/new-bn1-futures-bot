"""Trend-aligned pullbacks beyond the opposite KC rail, observed in quote order."""
import math
import time

from core.services.candle_data import closed_entry_candles, closed_entry_problem

TURN_RECOVERY_ATR = 0.10

CODES = {f'KC_OUTER_TURN_{side}' for side in ('LONG', 'SHORT')}


def observation_store(engine):
    state = getattr(engine, '_kc_outer_turn_observations', None)
    if not isinstance(state, dict):
        state = engine._kc_outer_turn_observations = {}
    return state


def evaluate_outer_turn(frame, price, side, observations=None, symbol='', now=None):
    """Require closed MA alignment and an observed 0.10 ATR outer-rail recovery.

    Observations expire after five seconds without a call, or at bar/direction
    changes. Revalidating an unchanged quote preserves a ready signal. No history
    is reconstructed from OHLC and no observation survives a process restart.
    """
    def wait(reason):
        if observations is not None:
            observations.pop((symbol, side), None)
        return False, reason, {'action': 'WAIT'}

    if side not in ('LONG', 'SHORT'):
        return wait('WAIT_INVALID_SIDE')
    closed = closed_entry_candles(frame)
    if len(closed) < 2:
        return wait('WAIT_INSUFFICIENT_CLOSED_DATA')
    problem = closed_entry_problem(closed)
    if problem:
        return wait(problem)
    key = 'kc_middle' if 'kc_middle' in closed else 'ema_20'
    try:
        previous, latest = (float(v) for v in closed[key].iloc[-2:])
        upper, lower = (float(frame.iloc[-1][k]) for k in ('kc_upper', 'kc_lower'))
        bar = float(frame.iloc[-1]['timestamp'])
        price = float(price)
        atr = float(closed.iloc[-1]['atr'])
        ma3 = float(closed.iloc[-1]['ma3'])
        ma15 = float(closed.iloc[-1]['ma15'])
    except (KeyError, TypeError, ValueError, OverflowError):
        return wait('WAIT_INVALID_MARKET_DATA')
    if not all(math.isfinite(v) and v > 0 for v in (previous, latest, upper, lower, price, atr, ma3, ma15)) or lower >= upper or not math.isfinite(bar):
        return wait('WAIT_INVALID_MARKET_DATA')
        
    # --- V5.0 Environment Filters applied for KC Turn ---
    from core.config import ENV_MIN_KC_BANDWIDTH, ENV_MIN_KC_SLOPE, ENV_ATR_EXPANSION_RATIO
    kc_middle = previous  # the middle of the closed bar
    is_bandwidth_ok = (kc_middle > 0 and (upper - lower) / kc_middle >= ENV_MIN_KC_BANDWIDTH)
    atr_expanding = False
    if 'atr' in frame.columns and len(frame) > 6:
        recent_atr = frame['atr'].iloc[-3:].mean()
        prev_atr = frame['atr'].iloc[-6:-3].mean()
        if prev_atr > 0 and (recent_atr / prev_atr - 1) >= 0.20:
            atr_expanding = True
            
    if not is_bandwidth_ok and not atr_expanding:
        return wait("ENV_FILTER_BANDWIDTH_REJECTED")
    
    if len(closed) >= 3:
        prev_middle = float(closed[key].iloc[-3])
        if abs(kc_middle - prev_middle) < ENV_MIN_KC_SLOPE:
            return wait("ENV_FILTER_SLOPE_REJECTED")
            
    if 'atr' in frame.columns and len(frame) > 4:
        short_atr = frame['atr'].iloc[-3:].mean()
        long_atr = frame['atr'].mean()
        if long_atr > 0 and (short_atr / long_atr) < ENV_ATR_EXPANSION_RATIO:
            return wait("ENV_FILTER_VOLATILITY_REJECTED")
    # --------------------------------------------------
    sign = 1 if side == 'LONG' else -1
    if sign * (latest - previous) <= 0:
        return wait('WAIT_KC_MID_DIRECTION')
    if sign * (ma3 - ma15) <= 0:
        return wait('WAIT_MA3_MA15_ALIGNMENT')
    if not (price < lower if side == 'LONG' else price > upper):
        return wait('WAIT_OUTSIDE_LOWER' if side == 'LONG' else 'WAIT_OUTSIDE_UPPER')
    if observations is None or not symbol:
        return wait('WAIT_QUOTE_OBSERVATION')
    now = time.monotonic() if now is None else now
    identity = (bar, float(closed.iloc[-1].get('timestamp', bar)), side, 'ma_atr_v1')
    state = observations.get((symbol, side))
    if state is None or state['identity'] != identity or not 0 <= now-state['seen'] <= 5:
        observations[(symbol, side)] = dict(identity=identity, seen=now, price=price, adverse=False, ready=False,
                                                extreme=sign * price, recovery_required=atr * TURN_RECOVERY_ATR)
        return False, 'WAIT_QUOTE_OBSERVATION', {'action': 'WAIT'}
    movement = sign * (price-state['price'])
    if movement < 0:
        # A new adverse leg must build its own recovery, not reuse an old turn.
        if not state['adverse'] or state['ready']:
            state['extreme'] = sign * price
        else:
            state['extreme'] = min(state['extreme'], sign * price)
        state.update(adverse=True, ready=False)
    elif movement > 0 and state['adverse']:
        recovery = sign * price - state['extreme']
        required = state['recovery_required']
        state['ready'] = recovery >= required or math.isclose(recovery, required, rel_tol=1e-10)

    state.update(price=price, seen=now)
    if not state['ready']:
        return False, 'WAIT_OUTER_TURN', {'action': 'WAIT'}
    reason = f'KC_OUTER_TURN_{side}'
    return True, reason, dict(action='ENTER', side=side, reason=reason, entry_atr=atr,
                              entry_type='OUTER_TURN', is_breakout=False)
