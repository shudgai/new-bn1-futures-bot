"""Position-bound abnormal-body exits and independent initial hard stops."""
import copy
import math

POLICY = 'abnormal_body_only_v2'
ABNORMAL_BODY_ATR = 1.2
ABNORMAL_REASON = 'EXIT_ADVERSE_ABNORMAL_BODY'
STATE_KEY = 'peak_trailing_state'
PEAK_REASON = 'EXIT_REALTIME_PEAK_TRAILING'
HARD_REASON = 'EXIT_INITIAL_ATR_HARD_STOP'
RETIRED_KEYS = (
    'instant_exit_state', 'closed_exit_state', 'doji_reversal_state',
    'three_stage_exit_state', 'chandelier_state', 'channel_profit_protection',
    'ratchet_lock_state', 'swing_breakeven_armed', 'swing_peak_profit_atr',
    'swing_trailing_armed', 'swing_trailing_line', 'swing_trailing_last_bar',
    'has_broken_outer_band', 'peak_pnl_usdt', 'peak_profit_diff',
    'last_valid_swing_low', 'last_valid_swing_high', 'v10_phase_trailing',
    'active_stop_price', 'defense_line', 'ratchet_floor', 'channel_peak_abnormal',
    'channel_live_ma3_exit_pending', 'channel_live_ma3_turn_exit_pending',
    'outer_run_active', 'channel_cross_lock', 'channel_pre_lock_sl',
    'is_breakeven_moved', 'profit_lock_atr_armed', 'profit_lock_usdt_armed',
    'fixed_profit_lock_pct_armed', 'profit_lock_mode',
    'limit_tp1_price', 'limit_tp1_filled', 'breakeven_trigger_price',
)
STATE_KEYS = (STATE_KEY, 'peak_price', 'peak_pnl', 'peak_pnl_usd', 'peak_net_pnl_usd',
              'peak_gain_atr', 'peak_unrealized_profit_usd', 'current_unrealized_pnl_usd',
              'current_net_pnl_usd', 'sl', 'tp', 'stop_loss', 'entry_atr', 'atr_sl',
              'atr_tp', 'atr_protection_version', 'initial_sl', 'initial_risk')


def positive(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def position_identity(position):
    result = [position['side'], float(position['open_timestamp']),
              float(position['entry_price']), abs(float(position.get('qty', position.get('quantity', 0))))]
    if result[0] not in ('LONG', 'SHORT') or not all(positive(v) for v in result[1:]):
        raise ValueError('Invalid peak-trailing identity')
    return result


def migrate_peak_state(position, meta=None):
    """Remove legacy authorities in both stores, preserving verified observations."""
    meta = {} if meta is None else meta
    ident = position_identity(position)
    state = position.get(STATE_KEY) or meta.get(STATE_KEY) or {}
    if state.get('policy') == POLICY and state.get('identity') == ident:
        state = copy.deepcopy(state)
    else:
        old = position.get('instant_exit_state') or meta.get('instant_exit_state') or {}
        peak = 0.
        if not state and (not old or old.get('identity') == ident):
            peak = max([0.] + [float(v) for v in (position.get('peak_pnl_usd'), old.get('peak')) if positive(v)])
        sign = 1 if ident[0] == 'LONG' else -1
        margin = position.get('margin')
        if not positive(margin):
            leverage = position.get('leverage') or 1.
            margin = ident[2]*ident[3]/float(leverage) if positive(leverage) else ident[2]*ident[3]
        state = dict(policy=POLICY, identity=ident, peak_price=ident[2]+sign*peak/ident[3],
                     entry_margin=float(margin), armed=False)
        if positive(position.get('entry_atr')):
            state['atr'] = float(position['entry_atr'])
        # Retain a verified initial hard-stop retry, never a retired strategy exit.
        pending = position.get('closed_exit_state') or meta.get('closed_exit_state') or {}
        if old.get('identity') == ident and pending.get('pending') and pending.get('reason') == HARD_REASON:
            state.update(pending=HARD_REASON, trigger='INITIAL_ATR')
    # Migrate matching observations, but revoke retired strategy close authority.
    if state.get('identity') == ident and state.get('policy') == POLICY:
        prior = position.get(STATE_KEY) or meta.get(STATE_KEY) or {}
        if prior.get('identity') == ident and prior.get('policy') != POLICY:
            for key in ('peak_price', 'peak_net_pnl', 'atr', 'last_ms'):
                if positive(prior.get(key)):
                    state[key] = prior[key]
            if prior.get('pending') == HARD_REASON:
                state.update(pending=HARD_REASON, trigger='INITIAL_ATR')
            initial = position.get('initial_sl') or meta.get('initial_sl')
            if positive(initial):
                position.update(sl=float(initial), stop_loss=float(initial), atr_sl=float(initial))
    for source in (position, meta):
        for key in RETIRED_KEYS:
            source.pop(key, None)
    position[STATE_KEY] = state
    return state


def estimated_net_pnl(entry, price, qty, sign, fee, slippage):
    execution = price*(1-sign*slippage)
    return sign*(execution-entry)*qty - (entry+execution)*qty*fee


def evaluate_peak_trailing(position, price, snapshot, atr=0., *, fee=0.0005, slippage=0.0001):
    try:
        ident = position_identity(position)
        stamp = float(snapshot) if isinstance(snapshot, (int, float)) else float(snapshot.get('quote_ms', 0))
        price, stamp, fee, slippage = map(float, (price, stamp, fee, slippage))
        if (not positive(price) or not positive(stamp) or stamp < ident[1]*1000
                or not all(math.isfinite(v) and 0 <= v < 1 for v in (fee, slippage))):
            return None
        previous = position.get(STATE_KEY) or {}
        if previous.get('identity') == ident and stamp < previous.get('last_ms', 0):
            return None
        state = migrate_peak_state(position)
        sign = 1 if ident[0] == 'LONG' else -1
        entry, qty = ident[2:]
        
        if 'peak_price' not in state:
            state['peak_price'] = entry

        try:
            from core.services.exits.trend_hold_evaluator import evaluate_trend_hold
            trend_status, trend_reason = evaluate_trend_hold(position, snapshot if isinstance(snapshot, dict) else {}, price)
        except Exception:
            trend_status, trend_reason = 'RELEASED', 'EVAL_ERROR'
        
        position['trend_hold_status'] = trend_status
        position['trend_hold_reason'] = trend_reason
        
        if 'crossed_kc_middle' not in state:
            state['crossed_kc_middle'] = False
            
        kc_middle = snapshot.get('kc_middle') if isinstance(snapshot, dict) else None
        if not state['crossed_kc_middle'] and kc_middle is not None and positive(kc_middle):
            if sign == 1 and price > kc_middle:
                state['crossed_kc_middle'] = True
            elif sign == -1 and price < kc_middle:
                state['crossed_kc_middle'] = True

        if not positive(state.get('atr')) and positive(atr):
            state['atr'] = float(atr)
        scale = float(state.get('atr') or 0.)
        state['last_ms'] = stamp
        if sign*(price-state['peak_price']) > 0:
            state['peak_price'] = price
        gain = max(0., sign*(state['peak_price']-entry))
        peak_net = estimated_net_pnl(entry, state['peak_price'], qty, sign, fee, slippage)
        net = estimated_net_pnl(entry, price, qty, sign, fee, slippage)
        state['peak_net_pnl'] = max(float(state.get('peak_net_pnl', peak_net)), peak_net)
        
        # 2U Fixed Ladder Profit Lock (4U locks 2U, 6U locks 4U, etc.)
        if state['peak_net_pnl'] >= 4.0:
            locked_net = math.floor((state['peak_net_pnl'] - 4.0) / 2.0) * 2.0 + 2.0
            if trend_status == 'HOLD':
                locked_net = 2.0 # Relax to floor if strong trend
            if net <= locked_net:
                reason, trigger = PEAK_REASON, 'TRAILING_2U_LADDER'
                if trend_status == 'HOLD':
                    soft_exit_blocked = True
                else:
                    state.update(pending=reason, trigger=trigger)
                    return dict(action='FULL_CLOSE', type=reason, reason=reason, trigger=trigger, price=price)
                
        reached = lambda v, limit: v >= limit or math.isclose(v,limit,rel_tol=1e-12)
        
        position.update(peak_price=state['peak_price'], peak_pnl=gain*qty, peak_pnl_usd=gain*qty,
                        peak_net_pnl_usd=state['peak_net_pnl'], peak_gain_atr=gain/scale if scale>0 else 0.,
                        peak_unrealized_profit_usd=gain*qty, current_unrealized_pnl_usd=sign*(price-entry)*qty,
                        current_net_pnl_usd=net)
                        
        stop = entry-sign*1.5*scale if scale>0 else 0.
        initial = position.get('initial_sl')
        if positive(initial):
            stop = ((max if sign==1 else min)(stop,float(initial)) if positive(stop) else float(initial))
            
        if positive(stop):
            position.update(sl=stop,stop_loss=stop,atr_sl=stop,atr_tp=0.,tp=0.)
        reason, trigger = None, None
        
        if positive(stop) and sign*(price-stop) <= 0:
            reason, trigger = HARD_REASON, 'INITIAL_ATR'
        elif state.get('pending') in (ABNORMAL_REASON, HARD_REASON):
            reason, trigger = state['pending'], state.get('trigger', 'RETRY')
        elif isinstance(snapshot, dict):
            # The live body's original open and immediately prior closed ATR
            # must belong to this quote's minute; never infer them from wicks.
            bar = math.floor(stamp / 60000) * 60000
            opening = snapshot.get('live_open')
            prior_atr = snapshot.get('atr')
            if (snapshot.get('live_bar_ms') == bar
                    and snapshot.get('closed_bar_ms') == bar - 60000
                    and positive(opening) and positive(prior_atr)):
                
                ma15 = snapshot.get('ma15')
                if state.get('crossed_kc_middle'):
                    # Phase 2: MA15 Tracking Defense
                    if ma15 is not None and positive(ma15):
                        if sign == 1 and price < ma15 and price < opening:
                            reason, trigger = ABNORMAL_REASON, 'BROKE_MA15_DEFENSE'
                            state.update(trigger_bar_ms=bar, trigger_open=float(opening),
                                         trigger_atr=float(prior_atr), trigger_price=price)
                        elif sign == -1 and price > ma15 and price > opening:
                            reason, trigger = ABNORMAL_REASON, 'BROKE_MA15_DEFENSE'
                            state.update(trigger_bar_ms=bar, trigger_open=float(opening),
                                         trigger_atr=float(prior_atr), trigger_price=price)
                                         
                # 2-Bar Doji Reversal Protection
                if not reason:
                    last_open = snapshot.get('last_open')
                    last_high = snapshot.get('last_high')
                    last_low = snapshot.get('last_low')
                    last_close = snapshot.get('last_close')
                    if all(v is not None for v in (last_open, last_high, last_low, last_close)):
                        last_span = last_high - last_low
                        last_body = abs(last_close - last_open)
                        if last_span > 0 and (last_body / last_span) <= 0.15:
                            ma5 = snapshot.get('ma5')
                            if sign == 1 and last_close > entry:
                                # Previous was high-profit doji
                                if price < opening and (price < last_low or (ma5 and price < ma5)):
                                    reason, trigger = ABNORMAL_REASON, 'DOJI_REVERSAL_EXIT'
                                    state.update(trigger_bar_ms=bar, trigger_open=float(opening),
                                                 trigger_atr=float(prior_atr), trigger_price=price)
                            elif sign == -1 and last_close < entry:
                                # Previous was high-profit doji
                                if price > opening and (price > last_high or (ma5 and price > ma5)):
                                    reason, trigger = ABNORMAL_REASON, 'DOJI_REVERSAL_EXIT'
                                    state.update(trigger_bar_ms=bar, trigger_open=float(opening),
                                                 trigger_atr=float(prior_atr), trigger_price=price)
                
                # Extreme selling pressure (Waterfall) protection overrides defense lines
                if not reason:
                    body = sign*(float(opening)-price)
                    try:
                        threshold = ABNORMAL_BODY_ATR * float(prior_atr)
                    except NameError:
                        threshold = 1.5 * float(prior_atr)
                        
                    if body > 0 and body >= threshold:
                        reason, trigger = ABNORMAL_REASON, 'WATERFALL_DROP'
                        state.update(trigger_bar_ms=bar, trigger_open=float(opening),
                                     trigger_atr=float(prior_atr), trigger_price=price)

            if reason and reason != HARD_REASON and trigger != 'WATERFALL_DROP':
                if trend_status == 'HOLD':
                    import logging
                    logger = logging.getLogger('TrendHold')
                    logger.info(f"TREND_HOLD={trend_status} direction={'LONG' if sign==1 else 'SHORT'} MA5={snapshot.get('ma5')} MA15={snapshot.get('ma15')} KC_MID={snapshot.get('kc_middle')} price={price} soft_exit_requested={trigger} soft_exit_blocked=true final_exit_reason=NONE")
                    reason, trigger = None, None
            
            if reason:
                import logging
                logger = logging.getLogger('TrendHold')
                logger.info(f"TREND_HOLD={trend_status} direction={'LONG' if sign==1 else 'SHORT'} MA5={snapshot.get('ma5')} MA15={snapshot.get('ma15')} KC_MID={snapshot.get('kc_middle')} price={price} soft_exit_requested={trigger} soft_exit_blocked=false final_exit_reason={trigger}")

                    
        if reason:
            state.update(pending=reason,trigger=trigger)
            return dict(action='FULL_CLOSE',type=reason,reason=reason,trigger=trigger,price=price)
        return None
    except (KeyError,TypeError,ValueError,OverflowError):
        return None
