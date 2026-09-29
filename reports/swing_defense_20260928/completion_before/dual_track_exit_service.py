"""Completed-candle MA15 defense, adverse MA3/MA15 cross, and entry breakeven."""
import math
from core.interfaces.exit_interface import IExitStrategy
from core.services.strategies.unified_entry_strategy import confirmed

POLICY = 'closed_1m_ma15_structure_v1'
SL_INIT_MULT = 1.5
DUAL_TRACK_STATE_KEYS = [
    'closed_exit_state', 'sl', 'tp', 'stop_loss', 'entry_atr', 'atr_sl',
    'atr_tp', 'atr_protection_version', 'swing_breakeven_armed', 'swing_peak_profit_atr',
]


def observe_breakeven(position, price, atr):
    """Observe actual quotes only; never infer pre-entry peaks from wicks."""
    entry = float(position['entry_price'])
    sign = 1 if position['side'] == 'LONG' else -1
    if not all(math.isfinite(v) and v > 0 for v in (entry, price, atr)):
        return
    profit_atr = sign * (price - entry) / atr
    peak = max(float(position.get('swing_peak_profit_atr', 0.)), profit_atr, 0.)
    position['swing_peak_profit_atr'] = peak
    if peak >= 1.2 or math.isclose(peak, 1.2, rel_tol=1e-12):
        position['swing_breakeven_armed'] = True
    stop = float(position.get('stop_loss') or position.get('sl') or entry-sign*SL_INIT_MULT*atr)
    if position.get('swing_breakeven_armed'):
        stop = max(stop, entry) if sign == 1 else min(stop, entry)
    position.update(stop_loss=stop, sl=stop, atr_sl=stop)


def evaluate_trend_exit_and_take_profit(position, candles, indicators):
    """MA3 slope or a close through MA3 alone never authorizes an exit."""
    sign = 1 if position['side'] == 'LONG' else -1
    close = float(candles[-1]['close'])
    previous_gap = sign * (float(indicators['ma3'][-2]) - float(indicators['ma15'][-2]))
    current_gap = sign * (float(indicators['ma3'][-1]) - float(indicators['ma15'][-1]))
    if sign * (close-float(indicators['ma15'][-1])) < 0:
        reason = 'EXIT_MA15_DEFENSE_CLOSED'
    elif previous_gap >= 0 and current_gap < 0:
        reason = 'EXIT_MA3_MA15_CROSS_CLOSED'
    else:
        return dict(should_exit=False, action='HOLD', reason='TREND_RUNNING')
    return dict(should_exit=True, action='FULL_CLOSE', reason=reason)


class DualTrackExitStrategy(IExitStrategy):
    def initialize_position(self, position, entry_price, atr):
        sign = 1 if position['side'] == 'LONG' else -1
        position.update(entry_atr=float(atr), sl=entry_price-sign*SL_INIT_MULT*atr,
                        stop_loss=entry_price-sign*SL_INIT_MULT*atr, tp=0.)

    def evaluate_exit(self, position, frame, current_price=None, **kwargs):
        closed = confirmed(frame)
        if closed is None or position.get('side') not in ('LONG', 'SHORT'):
            return None
        try:
            entry = float(position['entry_price'])
            opened = float(position.get('open_timestamp') or 0)*1000
            if not math.isfinite(entry) or entry <= 0 or not math.isfinite(opened):
                return None
            c1, c = closed.iloc[-2], closed.iloc[-1]
            atr = float(position.get('entry_atr') or c1.atr)
            if not math.isfinite(atr) or atr <= 0:
                return None
            position.setdefault('entry_atr', atr)
            # Quotes can arm protection even before the next completed candle.
            if current_price is not None:
                observe_breakeven(position, float(current_price), atr)
            if (float(c.timestamp)+60000 <= opened
                    or float(c.timestamp) <= float(position.get('channel_confirmation_bar_id') or -1)):
                return None
            observe_breakeven(position, float(c.close), atr)
            position.update(tp=0., atr_tp=0.)
            sign = 1 if position['side'] == 'LONG' else -1
            stop_quote = float(current_price) if current_price is not None else float(c.close)
            if math.isfinite(stop_quote) and sign*(stop_quote-position['stop_loss']) <= 0:
                return ('EXIT_BREAKEVEN' if sign*(position['stop_loss']-entry) >= 0
                        else 'EXIT_INITIAL_ATR_HARD_STOP')
            result = evaluate_trend_exit_and_take_profit(position,
                [{'close':float(c1.close)}, {'close':float(c.close)}],
                {'ma3':[float(c1.ma3),float(c.ma3)], 'ma15':[float(c1.ma15),float(c.ma15)]})
            return result['reason'] if result['should_exit'] else None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def handle_post_exit_cleanup(self, position, exit_reason):
        position.pop('closed_exit_state', None)
        position['cooldown_mode'] = 'NONE'
