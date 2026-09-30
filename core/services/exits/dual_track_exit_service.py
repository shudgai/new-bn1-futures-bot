"""Completed-candle MA15 defense and entry breakeven."""
import math
from core.interfaces.exit_interface import IExitStrategy
from core.services.strategies.unified_entry_strategy import confirmed

POLICY = 'closed_1m_confirmed_swing_v2'
SL_INIT_MULT = 1.5
DUAL_TRACK_STATE_KEYS = [
    'instant_exit_state', 'peak_pnl_usd', 'peak_gain_atr', 'peak_unrealized_profit_usd', 'current_unrealized_pnl_usd',
    'closed_exit_state', 'sl', 'tp', 'stop_loss', 'entry_atr', 'atr_sl',
    'atr_tp', 'atr_protection_version', 'swing_breakeven_armed', 'swing_peak_profit_atr',
    'swing_trailing_armed', 'swing_trailing_line', 'swing_trailing_last_bar',
    'has_broken_outer_band', 'peak_pnl_usdt', 'peak_profit_diff',
]


def evaluate_trend_exit_and_take_profit(position, closed, atr):
    """Compatibility entry point for the single confirmed-swing policy."""
    from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2
    reason = PureTrendStrategyV2().evaluate_bar_closed_exit(position, closed)
    return dict(should_exit=bool(reason), action='FULL_CLOSE' if reason else 'HOLD',
                reason=reason or 'TREND_RUNNING')



class DualTrackExitStrategy(IExitStrategy):
    def initialize_position(self, position, entry_price, atr):
        sign = 1 if position['side'] == 'LONG' else -1
        position.update(entry_atr=float(atr), sl=entry_price-sign*SL_INIT_MULT*atr,
                        stop_loss=entry_price-sign*SL_INIT_MULT*atr, tp=0.)

    def evaluate_exit(self, position, frame=None, current_price=None, **kwargs):
        from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2
        if position.get('side') not in ('LONG', 'SHORT'):
            return None
        state = position.get('closed_exit_state') or {}
        valid_reasons = {'EXIT_KC_MID_BREACH', 'EXIT_PEAK_DRAWDOWN_25PCT',
                         'EXIT_PROFIT_TIER2_LOCK', 'EXIT_BREAKEVEN_LOCK',
                         'EXIT_INTRADAY_KC_MID_BREACH', 'EXIT_INTRADAY_ANOMALY_SPIKE',
                         'EXIT_INTRADAY_PROFIT_DRAWDOWN_20PCT', 'EXIT_INITIAL_ATR_HARD_STOP', 'EXIT_SWING_LOW_BREAK_CLOSED',
                         'EXIT_SWING_HIGH_BREAK_CLOSED', 'EMERGENCY_BTC_CRASH',
                         'EMERGENCY_FLASH_CRASH_LONG', 'EMERGENCY_FLASH_SURGE_SHORT',
                         'EMERGENCY_GIANT_REVERSE_CANDLE'}
        if state.get('pending') and state.get('reason') in valid_reasons and (
                state.get('policy') == POLICY or state.get('reason') == 'EXIT_INITIAL_ATR_HARD_STOP'):
            return state['reason']
        if state and state.get('policy') != POLICY:
            position['closed_exit_state'] = dict(policy=POLICY, pending=False)
        reason = None
        try:
            sign = 1 if position['side'] == 'LONG' else -1
            entry = float(position['entry_price'])
            atr = float(position.get('entry_atr') or 0.)
            stored_stop = position.get('stop_loss') or position.get('sl')
            stop = float(stored_stop or (entry-sign*SL_INIT_MULT*atr if math.isfinite(atr) and atr > 0 else float('nan')))
            if current_price is not None:
                quote = float(current_price)
                if all(math.isfinite(v) and v > 0 for v in (quote, stop)) and sign*(quote-stop) <= 0:
                    reason = 'EXIT_INITIAL_ATR_HARD_STOP'
            if reason is None and frame is not None:
                reason = PureTrendStrategyV2().evaluate_bar_closed_exit(position, frame)
            if reason:
                position['closed_exit_state'] = dict(policy=POLICY, pending=True, reason=reason)
            return reason
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def handle_post_exit_cleanup(self, position, exit_reason):
        position.pop('closed_exit_state', None)
        position.pop('has_broken_outer_band', None)
        position['cooldown_mode'] = 'NONE'
