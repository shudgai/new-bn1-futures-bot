"""Completed-candle MA15 defense and entry breakeven."""
import math
from core.interfaces.exit_interface import IExitStrategy
from core.services.strategies.unified_entry_strategy import confirmed

POLICY = 'closed_1m_ma15_structure_v1'
SL_INIT_MULT = 1.5
DUAL_TRACK_STATE_KEYS = [
    'closed_exit_state', 'sl', 'tp', 'stop_loss', 'entry_atr', 'atr_sl',
    'atr_tp', 'atr_protection_version', 'swing_breakeven_armed', 'swing_peak_profit_atr',
    'swing_trailing_armed', 'swing_trailing_line', 'swing_trailing_last_bar',
]


def evaluate_trend_exit_and_take_profit(position, candles, indicators):
    """Exit only on a structural break through KC Middle or middle slope turning opposite; ignore MA3/MA15."""
    sign = 1 if position['side'] == 'LONG' else -1
    close = float(candles[-1]['close'])
    kc_mid = float(indicators['kc_middle'][-1])
    prev_kc_mid = float(indicators['kc_middle'][-2])
    
    price_broken = sign * (close - kc_mid) < 0
    trend_reversed = sign * (kc_mid - prev_kc_mid) < 0

    if price_broken or trend_reversed:
        reason = 'EXIT_KC_MIDDLE_DEFENSE_CLOSED'
    else:
        return dict(should_exit=False, action='HOLD', reason='TREND_RUNNING')
    return dict(should_exit=True, action='FULL_CLOSE', reason=reason)


class DualTrackExitStrategy(IExitStrategy):
    def initialize_position(self, position, entry_price, atr):
        sign = 1 if position['side'] == 'LONG' else -1
        position.update(entry_atr=float(atr), sl=entry_price-sign*SL_INIT_MULT*atr,
                        stop_loss=entry_price-sign*SL_INIT_MULT*atr, tp=0.)

    def evaluate_exit(self, position, frame=None, current_price=None, **kwargs):
        if position.get('side') not in ('LONG', 'SHORT'):
            return None
        try:
            entry = float(position['entry_price'])
            opened = float(position.get('open_timestamp') or 0)*1000
            if not math.isfinite(entry) or entry <= 0 or not math.isfinite(opened):
                return None
            closed = confirmed(frame)
            atr = float(position.get('entry_atr') or
                        (closed.iloc[-2].atr if closed is not None else 0.))
            if not math.isfinite(atr) or atr <= 0:
                return None
            position['entry_atr'] = atr
            sign = 1 if position['side'] == 'LONG' else -1

            state = position.get('closed_exit_state') or {}
            if state.get('policy') == POLICY and state.get('reason') == 'EXIT_MA3_MA15_CROSS_CLOSED':
                position['closed_exit_state'] = dict(policy=POLICY, pending=False)
                state = position['closed_exit_state']
            if state.get('policy') == POLICY and state.get('pending'):
                return state['reason']

            reason = None
            stop_loss = float(position.get('stop_loss') or position.get('sl') or entry-sign*SL_INIT_MULT*atr)
            
            # Account quote updates must enforce protection
            if current_price is not None:
                quote = float(current_price)
                if math.isfinite(quote) and quote > 0 and sign*(quote-stop_loss) <= 0:
                    reason = 'EXIT_INITIAL_ATR_HARD_STOP'
            
            if reason is None and closed is not None:
                c1, c = closed.iloc[-2], closed.iloc[-1]
                if (float(c.timestamp)+60000 <= opened
                        or float(c.timestamp) <= float(position.get('channel_confirmation_bar_id') or -1)):
                    return None
                
                if sign*(float(c.close)-stop_loss) <= 0:
                    reason = 'EXIT_INITIAL_ATR_HARD_STOP'
                else:
                    # 「一股不賣」吃滿波段：關閉所有短線疲態與軌跡出場，只由 KC 中軌實體貫穿作為唯一出場依據
                    result = evaluate_trend_exit_and_take_profit(position,
                        [{'close':float(c1.close)}, {'close':float(c.close)}],
                        {'ma3':[float(c1.ma3),float(c.ma3)], 'kc_middle':[float(c1.kc_middle),float(c.kc_middle)]})
                    reason = result['reason'] if result['should_exit'] else None
                    
            if reason:
                position['closed_exit_state'] = dict(policy=POLICY, pending=True, reason=reason)
            return reason
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def handle_post_exit_cleanup(self, position, exit_reason):
        position.pop('closed_exit_state', None)
        position['cooldown_mode'] = 'NONE'
