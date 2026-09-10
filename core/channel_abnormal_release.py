"""Read-only eligibility for retiring an abnormal exit's old-direction ticket."""
import math

from core.channel_outer_entry import (
    ck_direction, ck_entry_momentum_ready, live_ma3_direction_ready,
    live_adverse_entry_safe,
)


def opposite_entry_releases(account, symbol, frame, price):
    ticket = getattr(account, 'channel_profit_reentries', {}).get(symbol, {})
    if (symbol in account.positions or ticket.get('phase') != 'closed'
            or ticket.get('mode') != 'outer_cycle' or not ticket.get('requires_pullback')
            or ticket.get('side') not in ('LONG', 'SHORT')
            or ticket.get('close_reason') not in {
                'Channel Swing KC_LONG_LIVE_RED_LONG_EXIT',
                'Channel Swing KC_SHORT_LIVE_GREEN_LONG_EXIT',
                'Channel Swing EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL',
                'Channel Swing EMERGENCY_EXIT_CLOSED_ADVERSE_WATERFALL',
                'Channel Swing EMERGENCY_EXIT_2_CANDLE_ADVERSE',
                'Channel Swing EMERGENCY_EXIT_LIVE_ADVERSE_ABNORMAL',
            }):
        return False
    try:
        requested = float(ticket['close_requested_at_ms'])
        exited = float(ticket['exit_bar_id'])
        live = float(frame.iloc[-1]['timestamp'])
        confirmed = float(frame.iloc[-2]['timestamp'])
        if not all(math.isfinite(v) and v > 0 for v in (requested, exited, live, confirmed)):
            return False
        fills = [float(t.get('id') or 0) for t in getattr(account, 'trades', [])
                 if t.get('symbol') == symbol and t.get('action') == 'CLOSE_' + ticket['side']
                 and t.get('reason') == ticket['close_reason']]
        fills = [v for v in fills if math.isfinite(v) and v >= requested and v >= exited]
        if not fills:
            return False
        # Use the actual successful close bar, including a delayed close retry.
        close_bar = math.floor(min(fills) / 60_000) * 60_000
        if live <= close_bar or not close_bar <= confirmed < live:
            return False
        side = 'SHORT' if ticket['side'] == 'LONG' else 'LONG'
        return (ck_direction(frame) == side and ck_entry_momentum_ready(frame, side)
                and live_ma3_direction_ready(frame, price, side)
                and live_adverse_entry_safe(frame, price, side))
    except (AttributeError, TypeError, ValueError, KeyError, IndexError, OverflowError):
        return False
