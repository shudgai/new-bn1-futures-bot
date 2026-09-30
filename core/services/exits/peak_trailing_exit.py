"""Position-bound real-time peak trailing; candles are never exit gates."""
import copy
import math

POLICY = 'realtime_peak_trailing_v1'
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
        
        open_ts = ident[1]
        if open_ts < 1e11:
            open_ts *= 1000
        time_held_ms = stamp - open_ts
        is_same_bar = time_held_ms < 60000
        
        if positive(stop) and sign*(price-stop) <= 0:
            reason, trigger = HARD_REASON, 'INITIAL_ATR'
        elif state.get('pending') in (PEAK_REASON,HARD_REASON):
            reason, trigger = state['pending'], state.get('trigger','RETRY')
        elif not is_same_bar:
            # 波段尾部確認平倉機制
            is_long = (sign == 1)
            
            c_close = float(snapshot.get('close', 0.)) if isinstance(snapshot, dict) else 0.
            c_ma5 = float(snapshot.get('ma5', 0.)) if isinstance(snapshot, dict) else 0.
            c_upper = float(snapshot.get('kc_upper', 0.)) if isinstance(snapshot, dict) else 0.
            c_lower = float(snapshot.get('kc_lower', 0.)) if isinstance(snapshot, dict) else 0.
            c_middle = float(snapshot.get('kc_middle', 0.)) if isinstance(snapshot, dict) else 0.
            prev_close = float(snapshot.get('prev_close', 0.)) if isinstance(snapshot, dict) else 0.
            prev_ma5 = float(snapshot.get('prev_ma5', 0.)) if isinstance(snapshot, dict) else 0.
            
            # 尾部信號 C: 當浮盈曾達到 2.5 ATR 以上，回落 40% (即時判斷)
            if scale > 0 and gain >= 2.5 * scale:
                retrace = sign*(state['peak_price']-price)
                if retrace > 0.40 * gain:
                    reason, trigger = PEAK_REASON, 'PEAK_RETRACE_40PCT_AFTER_2.5ATR'
                    
            # 尾部信號 A 與 B: 實質跌回軌內 或 短線動能竭盡 (收盤判定)
            if not reason and c_close > 0 and c_upper > 0 and c_lower > 0 and c_ma5 > 0 and c_middle > 0:
                if is_long:
                    if c_close < c_middle:
                        reason, trigger = PEAK_REASON, 'CLOSED_BELOW_KC_MIDDLE'
                    elif c_close < c_upper:
                        reason, trigger = PEAK_REASON, 'CLOSED_INSIDE_KC_UPPER'
                    elif c_close < c_ma5 and prev_close < prev_ma5 and prev_close > 0:
                        reason, trigger = PEAK_REASON, 'CLOSED_BELOW_MA5_TWICE'
                else:
                    if c_close > c_middle:
                        reason, trigger = PEAK_REASON, 'CLOSED_ABOVE_KC_MIDDLE'
                    elif c_close > c_lower:
                        reason, trigger = PEAK_REASON, 'CLOSED_INSIDE_KC_LOWER'
                    elif c_close > c_ma5 and prev_close > prev_ma5 and prev_close > 0:
                        reason, trigger = PEAK_REASON, 'CLOSED_ABOVE_MA5_TWICE'

        if reason:
            state.update(pending=reason,trigger=trigger)
            return dict(action='FULL_CLOSE',type=reason,reason=reason,trigger=trigger,price=price)
        return None
    except (KeyError,TypeError,ValueError,OverflowError):
        return None
