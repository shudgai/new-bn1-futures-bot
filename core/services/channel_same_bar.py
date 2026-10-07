"""Owner-authorized inside-channel long-body entry and one-candle holding policy."""
import math
from core.services.candle_data import closed_entry_candles

PHASE = 'KC_CHANNEL_LIVE_LONG_BODY'
CODES = {PHASE+'_LONG', PHASE+'_SHORT'}
EXIT = 'EXIT_CHANNEL_SAME_BAR_END'
EVIDENCE_KEYS = ('same_bar_entry_bar_ms', 'same_bar_exit_deadline_ms')


def entry_side(frame, quote):
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 3 or len(frame) != len(closed)+1:
            return None
        live = frame.iloc[-1]
        opened, quote, lower, upper, atr = map(float,
            (live.open, quote, live.kc_lower, live.kc_upper, closed.iloc[-1].atr))
        if not all(math.isfinite(v) and v > 0 for v in (opened, quote, lower, upper, atr)):
            return None
        # Both original opening and current executable quote must be inside.
        if not lower < opened < upper or not lower < quote < upper:
            return None
        body = abs(quote-opened)
        if body < atr and not math.isclose(body, atr, rel_tol=1e-10):
            return None
        return 'LONG' if quote > opened else 'SHORT'
    except (KeyError, AttributeError, TypeError, ValueError, IndexError, OverflowError):
        return None


def exit_due(position, quote_ms):
    """Bind deadline to this mode and the actual filled candle, including restart."""
    try:
        snapshot = position.get('entry_snapshot') or {}
        side = position['side']
        code = PHASE+'_'+side
        bar = float(snapshot['same_bar_entry_bar_ms'])
        deadline = float(snapshot['same_bar_exit_deadline_ms'])
        entered = float(position['open_timestamp'])*1000
        now = float(quote_ms)
        if not all(math.isfinite(v) and v > 0 for v in (bar, deadline, entered, now)):
            return False
        if (position.get('entry_mode') != 'CHANNEL_SWING'
                or snapshot.get('entry_phase') != PHASE
                or snapshot.get('signal_code') != code
                or side not in ('LONG', 'SHORT')
                or bar % 60000 != 0 or deadline != bar+60000
                or not bar <= entered < deadline):
            return False
        return now >= deadline
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


PROFIT_EXIT = 'EXIT_CHANNEL_SAME_BAR_NET_PROFIT_LOCK'
PROFIT_ARM_USDT = 1.0
PROFIT_GIVEBACK_RATIO = 0.20


def profit_lock_triggered(position, state, net):
    """Observe executable estimated net profit, never infer an unseen wick peak."""
    snapshot = position.get('entry_snapshot') or {}
    if not exit_due(position, snapshot.get('same_bar_exit_deadline_ms')):
        state.pop('same_bar_profit_lock', None)
        return False
    if not math.isfinite(net):
        return False
    lock = state.setdefault('same_bar_profit_lock', {})
    peak = max(0., float(lock.get('peak_net_usdt') or 0.), net)
    armed = peak >= PROFIT_ARM_USDT or math.isclose(peak, PROFIT_ARM_USDT, rel_tol=1e-10)
    floor = peak*(1-PROFIT_GIVEBACK_RATIO) if armed else None
    lock.update(peak_net_usdt=peak, armed=armed, floor_net_usdt=floor,
                arm_usdt=PROFIT_ARM_USDT, giveback_ratio=PROFIT_GIVEBACK_RATIO)
    return bool(armed and (net <= floor or math.isclose(net, floor, rel_tol=1e-10)))
