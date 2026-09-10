"""Post-entry MA3 turning exit gated by confirmed CK momentum fading."""
import math
from core.channel_ma3_turn import significant_ma3_turn
from core.channel_outer_entry import ck_momentum_fading, aligned_entry

STATE_KEY = 'channel_fading_ma3_turn'
EXIT_REASON = 'CK_FADING_MA3_TURN_EXIT'


def fading_ma3_turn(position, frame, price):
    identity = [position.get('side'), position.get('open_timestamp'), position.get('entry_price')]
    state = position.get(STATE_KEY)
    if state and state.get('identity') != identity:
        position.pop(STATE_KEY, None)
        state = None
    if state and state.get('version') == 3 and state.get('pending'):
        return True
    fading = ck_momentum_fading(frame, position.get('side'))
    if fading is None:
        position.pop(STATE_KEY, None)
        return False
    # Reuse the existing post-entry observation and fixed 0.10 ATR threshold.
    # This additional exit remains eligible after profit protection arms.
    observed = {k: position.get(k) for k in ('side', 'open_timestamp', 'entry_price')}
    if state:
        observed['channel_significant_ma3_turn'] = state
    turned = significant_ma3_turn(observed, frame, price)
    state = observed.get('channel_significant_ma3_turn')
    if state is None:
        position.pop(STATE_KEY, None)
        return False
    if turned and not fading:
        # Do not save a non-fading turn to trigger retrospectively on a later bar.
        state.update(pending=False, favorable=False)
    position[STATE_KEY] = state
    return bool(turned and fading)


def next_breakout_ready(account, symbol, frame, price):
    """A matched successful close must precede the live MA3 continuation candle."""
    ticket = getattr(account, 'channel_profit_reentries', {}).get(symbol, {})
    if symbol in account.positions or ticket.get('mode') != 'next_breakout':
        return False
    try:
        if ticket.get('close_reason') != 'Channel Swing ' + EXIT_REASON:
            return False
        requested = float(ticket['close_requested_at_ms'])
        fills = [float(t['id']) for t in account.trades
                 if t.get('symbol') == symbol and t.get('action') == 'CLOSE_' + ticket['side']
                 and t.get('reason') == ticket['close_reason'] and float(t['id']) >= requested]
        if not fills or not math.isfinite(requested):
            return False
        closed = max(fills)
        first = float(frame.iloc[-1]['timestamp'])
        return (math.isfinite(closed) and math.isfinite(first)
                and first > math.floor(closed / 60000) * 60000
                and aligned_entry(frame, price).get('action') == 'ENTER')
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
        return False
