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
    if peak > 1.8:
        position['swing_trailing_armed'] = True
    stop = float(position.get('stop_loss') or position.get('sl') or entry-sign*SL_INIT_MULT*atr)
    if position.get('swing_breakeven_armed'):
        stop = max(stop, entry) if sign == 1 else min(stop, entry)
    position.update(stop_loss=stop, sl=stop, atr_sl=stop)


def trailing_structure_exit(position, closed):
    """Monotone prior-two lows; evaluate each subsequent closed bar once.

    Keep this close-only line separate from the quote-enforced hard/BE stop.
    First observation establishes a baseline, never replays an old exit.
    """
    current = closed.iloc[-1]
    bar = float(current.timestamp)
    last = position.get('swing_trailing_last_bar')
    if last is not None and bar <= float(last):
        return None
    position['swing_trailing_last_bar'] = bar
    prior = closed.iloc[-3:-1]
    opened_ms = float(position.get('open_timestamp') or 0)*1000
    if not (prior.timestamp.astype(float) >= opened_ms).all():
        return None
    is_long = position['side'] == 'LONG'
    candidate = float(prior.low.astype(float).min() if is_long else prior.high.astype(float).max())
    line = (max if is_long else min)(float(position.get('swing_trailing_line') or candidate), candidate)
    position['swing_trailing_line'] = line
    crossed = float(current.close) < line if is_long else float(current.close) > line
    if last is not None and crossed:
        return 'EXIT_TWO_BAR_LOW_TRAILING_CLOSED' if is_long else 'EXIT_TWO_BAR_HIGH_TRAILING_CLOSED'
    return None


def evaluate_trend_exit_and_take_profit(position, candles, indicators):
    """Exit only on a structural break through KC Middle; ignore MA3/MA15."""
    sign = 1 if position['side'] == 'LONG' else -1
    close = float(candles[-1]['close'])
    if sign * (close-float(indicators['kc_middle'][-1])) < 0:
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
            # Account quote updates must enforce protection without a candle
            # snapshot, including on the entry/confirmation candle.
            if current_price is not None:
                quote = float(current_price)
                if not math.isfinite(quote) or quote <= 0:
                    return None
                observe_breakeven(position, quote, atr)
            else:
                quote = None
            state = position.get('closed_exit_state') or {}
            if state.get('policy') == POLICY and state.get('reason') == 'EXIT_MA3_MA15_CROSS_CLOSED':
                # Retire the removed crossover exit in both position and metadata.
                position['closed_exit_state'] = dict(policy=POLICY, pending=False)
                state = position['closed_exit_state']
            if state.get('policy') == POLICY and state.get('pending'):
                return state['reason']
            reason = None
            if quote is not None and sign*(quote-position['stop_loss']) <= 0:
                reason = ('EXIT_BREAKEVEN' if sign*(position['stop_loss']-entry) >= 0
                          else 'EXIT_INITIAL_ATR_HARD_STOP')
            if reason is None and closed is not None:
                c1, c = closed.iloc[-2], closed.iloc[-1]
                if (float(c.timestamp)+60000 <= opened
                        or float(c.timestamp) <= float(position.get('channel_confirmation_bar_id') or -1)):
                    return None
                observe_breakeven(position, float(c.close), atr)
                position.update(tp=0., atr_tp=0.)
                stop_quote = quote if quote is not None else float(c.close)
                if sign*(stop_quote-position['stop_loss']) <= 0:
                    reason = ('EXIT_BREAKEVEN' if sign*(position['stop_loss']-entry) >= 0
                              else 'EXIT_INITIAL_ATR_HARD_STOP')
                else:
                    from core.services.exit_service import exhaustion_exit_reason
                    reason = exhaustion_exit_reason(position, closed, stop_quote)
                if reason is None and position.get('swing_trailing_armed'):
                    reason = trailing_structure_exit(position, closed)
                elif reason is None:
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
