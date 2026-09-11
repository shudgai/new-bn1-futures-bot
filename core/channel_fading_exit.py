"""Post-entry MA3 turning exit gated by confirmed CK momentum fading."""
import math
from statistics import median
from core.channel_ma3_turn import significant_ma3_turn
from core.channel_outer_entry import ck_momentum_fading, aligned_entry

STATE_KEY = 'channel_fading_ma3_turn'
EXIT_REASON = 'CK_FADING_MA3_TURN_EXIT'
IMMEDIATE_EXIT_REASON = 'MA3_IMMEDIATE_TURN_EXIT'


# Relative closed-bar bandwidth avoids the fixed KC-width / current-ATR ratio.
NARROW_LOOKBACK = 20
NARROW_RATIO = 0.75


def immediate_ma3_turn(position, frame, price):
    """Retired tick-to-tick exit; historical pending state cannot close a trade."""
    position.pop('channel_immediate_ma3_turn', None)
    return False


def ck_channel_narrow(frame):
    """Latest closed relative width <= 75% of preceding 20-bar median."""
    try:
        if frame is None or len(frame) < NARROW_LOOKBACK + 2:
            return False
        rows = frame.iloc[-(NARROW_LOOKBACK + 2):-1]
        widths = []
        for _, row in rows.iterrows():
            upper, lower = float(row['kc_upper']), float(row['kc_lower'])
            middle = float(row['kc_middle'] if 'kc_middle' in rows.columns else row['ema_20'])
            if not all(math.isfinite(v) for v in (upper, lower, middle)) or not 0 < lower < middle < upper:
                return False
            widths.append((upper - lower) / middle)
        return widths[-1] <= median(widths[:-1]) * NARROW_RATIO
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


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
    eligible = fading and ck_channel_narrow(frame)
    if turned and not eligible:
        # Do not save a non-fading turn to trigger retrospectively on a later bar.
        state.update(pending=False, favorable=False)
    position[STATE_KEY] = state
    return bool(turned and eligible)


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
