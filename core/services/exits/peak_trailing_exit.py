"""Position-bound abnormal-body exits and independent initial hard stops."""
import copy
import math
import sys

POLICY = 'abnormal_body_only_v2'
ABNORMAL_BODY_ATR = 1.2
ABNORMAL_REASON = 'EXIT_ADVERSE_ABNORMAL_BODY'
DOJI_TRIGGER = 'DOJI_REVERSAL_EXIT'
DOJI_RULE_VERSION = 3
DOJI_BODY_RATIO = 0.25
DOJI_ADVERSE_BODY_ATR = 0.20
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

PROFIT_FLOOR_ENABLED = False
PROFIT_FLOOR_ARM_ATR = None
PROFIT_FLOOR_LOCK_ATR = None

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
    # Old MA-touch doji tickets can retry without ever satisfying the body rule.
    # Revoke only that obsolete authority; preserve peaks and other exit retries.
    if state.get('trigger') == DOJI_TRIGGER and state.get('doji_rule_version') != DOJI_RULE_VERSION:
        for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open', 'trigger_atr', 'trigger_price'):
            state.pop(key, None)
    for source in (position, meta):
        for key in RETIRED_KEYS:
            source.pop(key, None)
    position[STATE_KEY] = state
    return state


def estimated_net_pnl(entry, price, qty, sign, fee, slippage):
    execution = price*(1-sign*slippage)
    return sign*(execution-entry)*qty - (entry+execution)*qty*fee


def doji_reversal_evidence(snapshot, price, sign, entry, opened_ms, peak_gain_atr):
    """A completed doji (or stall) followed immediately by an adverse live body."""
    try:
        stamp = float(snapshot['quote_ms'])
        bar = math.floor(stamp / 60000) * 60000
        if (snapshot['live_bar_ms'] != bar or snapshot['closed_bar_ms'] != bar - 60000
                or float(snapshot['live_bar_ms']) < opened_ms - 60000):
            return None
        keys = ('last_open', 'last_high', 'last_low', 'last_close',
                'live_open', 'live_high', 'live_low', 'atr')
        values = [float(snapshot[key]) for key in keys]
        if not all(positive(v) for v in values):
            return None
        last_open, last_high, last_low, last_close, opening, high, low, atr = values
        if not (last_low <= min(last_open, last_close) <= max(last_open, last_close) <= last_high
                and low <= opening <= high):
            return None
        span = last_high - last_low
        if span <= 0:
            return None
        ratio = abs(last_close - last_open) / span
        if ratio > DOJI_BODY_RATIO and not math.isclose(ratio, DOJI_BODY_RATIO, rel_tol=1e-12):
            return None
        body = sign * (opening - price)
        threshold = DOJI_ADVERSE_BODY_ATR * atr
        if body <= 0 or (body < threshold and not math.isclose(body, threshold, rel_tol=1e-12)):
            return None
        live_span = max(high, price) - min(low, price)
        live_ratio = abs(price - opening) / live_span if live_span > 0 else 0.
        # Another weak/doji candle is not confirmed reversal pressure.
        if live_ratio <= DOJI_BODY_RATIO or math.isclose(live_ratio, DOJI_BODY_RATIO, rel_tol=1e-12):
            return None
        return dict(trigger_bar_ms=bar, trigger_open=opening, trigger_atr=atr,
                    trigger_price=price, doji_bar_ms=bar-60000,
                    doji_body_ratio=ratio, reversal_body_ratio=live_ratio,
                    reversal_body_atr=body/atr, doji_rule_version=DOJI_RULE_VERSION)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def evaluate_mature_reversal_exit(position, snapshot, state, sign, entry_atr):
    """Evaluates mature swing reversal exit using strictly CLOSED bars."""
    try:
        if not positive(entry_atr):
            return None

        closed_bar_ms = snapshot.get('closed_bar_ms')
        if closed_bar_ms is None or not positive(closed_bar_ms):
            return None
        closed_bar_ms = float(closed_bar_ms)

        try:
            position_open_ms = float(position.get('open_timestamp', 0))
            if position_open_ms < 1e11: # if seconds, convert to ms
                position_open_ms *= 1000
        except (TypeError, ValueError):
            position_open_ms = float('inf')

        closed_history = state.get('closed_history', [])

        if 'history_5' in snapshot:
            raw_history = snapshot['history_5']
            closed_history = []
            for b in raw_history:
                is_mature = False
                c, o, h, l = b['c'], b['o'], b['h'], b['l']
                ma3, ma5 = b['ma3'], b['ma5']
                if all(positive(v) for v in (c, o, h, l, ma3, ma5)):
                    if sign == 1:
                        is_mature = ((l > ma5) or (c > ma3)) and (b['ms'] >= position_open_ms)
                    else:
                        is_mature = ((h < ma5) or (c < ma3)) and (b['ms'] >= position_open_ms)
                closed_history.append({
                    'ms': b['ms'], 'o': o, 'h': h, 'l': l, 'c': c,
                    'ma3': ma3, 'ma5': ma5, 'is_mature': is_mature
                })
            state['closed_history'] = closed_history
        elif not closed_history or closed_history[-1]['ms'] < closed_bar_ms:
            c = snapshot.get('last_close')
            o = snapshot.get('last_open')
            h = snapshot.get('last_high')
            l = snapshot.get('last_low')
            ma3 = snapshot.get('ma3')
            ma5 = snapshot.get('ma5')

            if not all(positive(v) for v in (c, o, h, l, ma3, ma5)):
                return None

            is_mature = False
            if sign == 1:
                is_mature = ((l > ma5) or (c > ma3)) and (closed_bar_ms >= position_open_ms)
            else:
                is_mature = ((h < ma5) or (c < ma3)) and (closed_bar_ms >= position_open_ms)

            rec = {
                'ms': closed_bar_ms,
                'o': float(o), 'h': float(h), 'l': float(l), 'c': float(c),
                'ma3': float(ma3), 'ma5': float(ma5),
                'is_mature': is_mature
            }
            closed_history.append(rec)
            closed_history = closed_history[-5:]
            state['closed_history'] = closed_history

        if len(closed_history) < 5:
            return None

        rev_bar = closed_history[-1]

        maturity_bars = closed_history[-5:-1]
        if not all(b['is_mature'] for b in maturity_bars):
            return None

        o, h, l, c = rev_bar['o'], rev_bar['h'], rev_bar['l'], rev_bar['c']
        ma3 = rev_bar['ma3']
        body = abs(c - o)
        span = h - l

        pinbar = False
        doji = False

        if sign == 1:
            upper_wick = h - max(o, c)
            close_pos = (c - l) / span if span > 0 else 0
            body_ratio = body / span if span > 0 else 0
            pinbar = (upper_wick > 0.5 * entry_atr) and (upper_wick > 2.0 * body) and (close_pos < 0.40)
            doji = (body_ratio < 0.25) and (c < o) and (c < ma3)
        else:
            lower_wick = min(o, c) - l
            close_pos = (c - l) / span if span > 0 else 0
            body_ratio = body / span if span > 0 else 0
            pinbar = (lower_wick > 0.5 * entry_atr) and (lower_wick > 2.0 * body) and (close_pos > 0.60)
            doji = (body_ratio < 0.25) and (c > o) and (c > ma3)

        trigger = None
        if pinbar and doji:
            trigger = 'MATURE_REVERSAL_PINBAR_DOJI'
        elif pinbar:
            trigger = 'MATURE_REVERSAL_PINBAR'
        elif doji:
            trigger = 'MATURE_REVERSAL_DOJI'

        if trigger:
            return dict(trigger=trigger, trigger_bar_ms=rev_bar['ms'], trigger_price=c)

        return None
    except Exception:
        return None


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

        ladder_reason, ladder_trigger = None, None
        # 2U Fixed Ladder Profit Lock is DISABLED per user request: "只有遇到真峰頂谷底才要平倉"

        # 暴漲逃頂機制 (Parabolic Reversal Exit): 無視 CK 是否衰退
        parabolic_reason, parabolic_trigger = None, None
        peak_gain_atr = gain / scale if scale > 0 else 0.
        try:
            from core.services.exits.profit_exit_telemetry import ProfitExitTelemetry
            
            telemetry_data = {
                'symbol': position.get('symbol', 'UNKNOWN'),
                'side': 'LONG' if sign == 1 else 'SHORT',
                'entry_price': entry,
                'frozen_entry_atr': scale,
                'current_price': price,
                'runtime_peak_price': state['peak_price'],
                'runtime_peak_gain_atr': peak_gain_atr,
                'trend_hold_status': trend_status,
                'trend_hold_reason': trend_reason,
            }
            if isinstance(snapshot, dict):
                telemetry_data.update({
                    'ma3': snapshot.get('ma3'),
                    'last_ma3': snapshot.get('last_ma3'),
                    'ma5': snapshot.get('ma5'),
                    'last_ma5': snapshot.get('last_ma5'),
                })
            
            if peak_gain_atr >= 3.0 and not state.get('telemetry_arm_logged'):
                state['telemetry_arm_logged'] = True
                ProfitExitTelemetry.log_event(position.get('id', 'UNKNOWN'), 'PARABOLIC_ARM_REACHED', telemetry_data)
                
            new_peak = sign*(price-state.get('last_logged_peak_price', entry)) > 0
            if new_peak and peak_gain_atr >= 3.0:
                old_peak_atr = state.get('last_logged_peak_atr', 0)
                if peak_gain_atr - old_peak_atr >= 0.1: # meaningful change
                    state['last_logged_peak_price'] = price
                    state['last_logged_peak_atr'] = peak_gain_atr
                    ProfitExitTelemetry.log_event(position.get('id', 'UNKNOWN'), 'NEW_RUNTIME_PEAK', telemetry_data)
        except Exception:
            pass

        # 高點賣壓即時平倉機制 (Peak Opposing Pressure Exit): 只要有利潤，高點後面出現賣壓/買壓立即平倉，不需等 MA5 進入通道
        drawdown_atr = (state['peak_price'] - price) / scale if (scale > 0 and sign == 1) else (price - state['peak_price']) / scale if scale > 0 else 0.

        if peak_gain_atr >= 1.0:
            # 方案 2 寬鬆大波段階梯回踩門檻（利潤越高，回踩門檻越小）
            if peak_gain_atr >= 3.0:
                pullback_limit_atr = 0.35
            elif peak_gain_atr >= 2.0:
                pullback_limit_atr = 0.40
            elif peak_gain_atr >= 1.0:
                pullback_limit_atr = 0.50
            else:
                pullback_limit_atr = 0.60

            from core.services.exits.trend_hold_evaluator import strong_direction_held
            strong_trend = strong_direction_held(position, snapshot, price)
            if strong_trend:
                pullback_limit_atr *= 1.5
            position['atr_pullback_limit'] = pullback_limit_atr
            position['strong_trend_pullback'] = strong_trend

            # 1. 價格從最高點回踩達動態階梯門檻
            if drawdown_atr >= pullback_limit_atr:
                parabolic_reason, parabolic_trigger = PEAK_REASON, 'EXIT_PEAK_PULLBACK_PRESSURE'
            # 2. MA 轉向反轉賣壓 (ma5 或 ma3 反向拐頭)
            elif isinstance(snapshot, dict):
                ma5 = snapshot.get('ma5')
                last_ma5 = snapshot.get('last_ma5')
                ma3 = snapshot.get('ma3')
                last_ma3 = snapshot.get('last_ma3')

                ma_turned = False
                if sign == 1:
                    if (ma5 and last_ma5 and ma5 < last_ma5) or (ma3 and last_ma3 and ma3 < last_ma3):
                        ma_turned = True
                else:
                    if (ma5 and last_ma5 and ma5 > last_ma5) or (ma3 and last_ma3 and ma3 > last_ma3):
                        ma_turned = True

                if ma_turned:
                    # [EMERGENCY FAIL-CLOSED]
                    # Single live MA3/MA5 adverse turn has no direct CLOSE authority.
                    # parabolic_reason, parabolic_trigger = PEAK_REASON, 'EXIT_PEAK_MA_TURN_PRESSURE'
                    pass
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
        reason, trigger = parabolic_reason or ladder_reason, parabolic_trigger or ladder_trigger
        
        pre_veto_reason = reason
        pre_veto_trigger = trigger
        
        try:
            if (parabolic_reason or ladder_reason) and 'telemetry_data' in locals():
                telemetry_data['current_profit_atr'] = gain / scale if scale > 0 else 0.
                if 'drawdown_atr' in locals():
                    telemetry_data['drawdown_atr'] = drawdown_atr
                if not state.get('telemetry_candidate_logged'):
                    telemetry_data['pre_veto_action'] = 'FULL_CLOSE'
                    telemetry_data['pre_veto_reason'] = reason
                    telemetry_data['pre_veto_trigger'] = trigger
                    ProfitExitTelemetry.log_event(position.get('id', 'UNKNOWN'), 'PARABOLIC_CANDIDATE_GENERATED', telemetry_data)
                    state['telemetry_candidate_logged'] = True
        except Exception:
            pass

        if positive(stop) and sign*(price-stop) <= 0:
            reason, trigger = HARD_REASON, 'INITIAL_ATR'
        elif state.get('pending') in (ABNORMAL_REASON, HARD_REASON):
            reason, trigger = state['pending'], state.get('trigger', 'RETRY')
        else:
            if isinstance(snapshot, dict):
                # The live body's original open and immediately prior closed ATR
                # must belong to this quote's minute; never infer them from wicks.
                bar = math.floor(stamp / 60000) * 60000
                opening = snapshot.get('live_open')
                prior_atr = snapshot.get('atr')
                if (snapshot.get('live_bar_ms') == bar
                        and snapshot.get('closed_bar_ms') == bar - 60000
                        and positive(opening) and positive(prior_atr)):

                    ma15 = snapshot.get('ma15')
                    # MA15 Tracking Defense is DISABLED per user request

                    # 1. Mature Swing Reversal check (strictly closed bars)
                    e_atr = position.get('entry_atr')
                    mature_evidence = None
                    if e_atr is not None and float(e_atr) > 0:
                        mature_evidence = evaluate_mature_reversal_exit(position, snapshot, state, sign, entry_atr=float(e_atr))

                    from core.services.exits.trend_hold_evaluator import strong_direction_held
                    trend_held = strong_direction_held(position, snapshot, price)
                    structure = snapshot.get('swing_structure_' + ('long' if sign == 1 else 'short'))
                    structure_broken = isinstance(structure, dict) and structure.get('intact') is False
                    if mature_evidence is not None and reason != HARD_REASON and not trend_held and structure_broken:
                        reason, trigger = ABNORMAL_REASON, mature_evidence['trigger']
                        state.update(mature_evidence)

                    # 2. Older Doji evidence check (this uses live candle for reversal)
                    evidence = doji_reversal_evidence(
                        snapshot, price, sign, entry, ident[1]*1000, peak_gain_atr)
                    if evidence is not None and not mature_evidence and reason != HARD_REASON and not trend_held and structure_broken:
                        reason, trigger = ABNORMAL_REASON, DOJI_TRIGGER
                        state.update(evidence)

            # Catastrophic Profit Floor Evaluation
            floor_reason, floor_trigger = None, None
            entry_atr = float(state.get('atr', position.get('entry_atr', 0.0)))

            pf_enabled = getattr(sys.modules[__name__], 'PROFIT_FLOOR_ENABLED', False)
            pf_arm = getattr(sys.modules[__name__], 'PROFIT_FLOOR_ARM_ATR', None)
            pf_lock = getattr(sys.modules[__name__], 'PROFIT_FLOOR_LOCK_ATR', None)

            if pf_enabled and pf_arm is not None and pf_lock is not None and pf_arm > 0 and 0 <= pf_lock <= pf_arm and entry_atr > 0:
                mfe_price = state.get('mfe_price', entry)
                if sign == 1:
                    mfe_price = max(mfe_price, price)
                    mfe_atr = (mfe_price - entry) / entry_atr
                else:
                    mfe_price = min(mfe_price, price)
                    mfe_atr = (entry - mfe_price) / entry_atr
                state['mfe_price'] = mfe_price

                is_armed = state.get('profit_floor_armed', False)
                if not is_armed and mfe_atr >= pf_arm:
                    is_armed = True
                    state['profit_floor_armed'] = True
                    state['profit_floor_arm_atr'] = float(pf_arm)
                    state['profit_floor_lock_atr'] = float(pf_lock)

                if is_armed:
                    latr = state.get('profit_floor_lock_atr', pf_lock)
                    if sign == 1:
                        candidate_floor = entry + latr * entry_atr
                        existing_floor = state.get('profit_floor_price', -float('inf'))
                        floor = max(existing_floor, candidate_floor)
                        state['profit_floor_price'] = floor
                        if price <= floor:
                            floor_reason, floor_trigger = ABNORMAL_REASON, 'EXIT_CATASTROPHIC_PROFIT_FLOOR'
                    else:
                        candidate_floor = entry - latr * entry_atr
                        existing_floor = state.get('profit_floor_price', float('inf'))
                        floor = min(existing_floor, candidate_floor)
                        state['profit_floor_price'] = floor
                        if price >= floor:
                            floor_reason, floor_trigger = ABNORMAL_REASON, 'EXIT_CATASTROPHIC_PROFIT_FLOOR'

            # Floor overrides soft exits (Doji / MA15 / Ladder)
            if floor_reason:
                reason, trigger = floor_reason, floor_trigger

            # Extreme selling pressure (Waterfall) protection overrides everything including Floor
            if isinstance(snapshot, dict):
                if (snapshot.get('live_bar_ms') == bar
                        and snapshot.get('closed_bar_ms') == bar - 60000
                        and positive(opening) and positive(prior_atr)):
                    body = sign*(float(opening)-price)
                    try:
                        threshold = ABNORMAL_BODY_ATR * float(prior_atr)
                    except NameError:
                        threshold = 1.5 * float(prior_atr)

                    if body > 0 and body >= threshold:
                        reason, trigger = ABNORMAL_REASON, 'WATERFALL_DROP'
                        state.update(trigger_bar_ms=bar, trigger_open=float(opening),
                                     trigger_atr=float(prior_atr), trigger_price=price)

            # No profit protection: if position currently has no net profit, do not prematurely exit on soft/reversal signals
            if reason and reason != HARD_REASON and trigger not in ('WATERFALL_DROP', 'EXIT_PEAK_PULLBACK_PRESSURE'):
                if net <= 0:
                    reason, trigger = None, None

            if reason:
                soft_exit_blocked = False
                peak_exemptions = (
                    'WATERFALL_DROP', 'EXIT_CATASTROPHIC_PROFIT_FLOOR', DOJI_TRIGGER,
                    'MATURE_REVERSAL_PINBAR', 'MATURE_REVERSAL_DOJI', 'MATURE_REVERSAL_PINBAR_DOJI',
                    'EXIT_PEAK_PULLBACK_PRESSURE', 'EXIT_PEAK_MA_TURN_PRESSURE',
                    'EXIT_PARABOLIC_PULLBACK_1_ATR', 'EXIT_PARABOLIC_MA3_TURN'
                )
                if reason != HARD_REASON and trigger not in peak_exemptions:
                    if trend_status in ('HOLD', 'WARNING', 'UNKNOWN'):
                        soft_exit_blocked = True

                import logging
                logger = logging.getLogger('TrendHold')
                sym = position.get('symbol', 'UNKNOWN')
                side_str = 'LONG' if sign == 1 else 'SHORT'
                snap_dict = snapshot if isinstance(snapshot, dict) else {}
                logger.info(
                    f"TREND_HOLD={trend_status} symbol={sym} side={side_str} "
                    f"exit_owner=PeakTrailing exit_reason={trigger} "
                    f"trend_hold_reason={trend_reason} "
                    f"snapshot_bar_id={snap_dict.get('snapshot_bar_id')} "
                    f"live_bar_id={snap_dict.get('live_bar_id')} "
                    f"snapshot_age={snap_dict.get('snapshot_age')} "
                    f"snapshot_source={snap_dict.get('snapshot_source', 'UNKNOWN')} "
                    f"fallback_used={str(snap_dict.get('fallback_used', False)).lower()} "
                    f"soft_exit_blocked={str(soft_exit_blocked).lower()} "
                    f"price={price} MA5={snap_dict.get('ma5')} MA15={snap_dict.get('ma15')} KC_MID={snap_dict.get('kc_middle')}"
                )

                if soft_exit_blocked:
                    try:
                        if pre_veto_reason and 'telemetry_data' in locals():
                            telemetry_data['trend_hold_veto_applied'] = True
                            telemetry_data['post_veto_action'] = None
                            telemetry_data['post_veto_reason'] = None
                            telemetry_data['post_veto_trigger'] = None
                            telemetry_data['final_exit_authorized'] = False
                            ProfitExitTelemetry.log_event(position.get('id', 'UNKNOWN'), 'TREND_HOLD_VETOED', telemetry_data)
                            state['telemetry_candidate_logged'] = False # reset so we log next candidate
                    except Exception:
                        pass
                    reason, trigger = None, None

        if reason:
            try:
                if 'telemetry_data' in locals():
                    telemetry_data['trend_hold_veto_applied'] = False
                    telemetry_data['post_veto_action'] = 'FULL_CLOSE'
                    telemetry_data['post_veto_reason'] = reason
                    telemetry_data['post_veto_trigger'] = trigger
                    telemetry_data['final_exit_authorized'] = True
                    ProfitExitTelemetry.log_event(position.get('id', 'UNKNOWN'), 'FINAL_EXIT_AUTHORIZED', telemetry_data)
            except Exception:
                pass
            state.update(pending=reason,trigger=trigger)
            return dict(action='FULL_CLOSE', type=reason, reason=reason, trigger=trigger, price=price)
        return None
    except Exception as e:
        import traceback
        traceback.print_exc()
        return None
