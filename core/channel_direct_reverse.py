"""Matched-fill authorization for direct reverse entries, never manual/stops."""
import math
from core.channel_outer_entry import live_adverse_entry_safe


def authorized(account, symbol, signal, now):
    t = getattr(account, 'channel_profit_reentries', {}).get(symbol, {})
    try:
        if (symbol in account.positions or t.get('mode') != 'direct_reverse'
                or t.get('phase') != 'closed' or not t.get('token')
                or signal.get('profit_reentry_token') != t['token']
                or signal.get('side') != t.get('side')
                or t.get('old_side') not in ('LONG', 'SHORT')
                or t['side'] != ('SHORT' if t['old_side'] == 'LONG' else 'LONG')
                or not str(t.get('close_reason', '')).startswith(
                    ('Channel Swing PROFIT_PROTECTION ', 'Channel Swing CK_REVERSE '))):
            return False
        trades = [x for x in getattr(account, 'trades', []) if x.get('symbol') == symbol]
        fills = [float(x['id']) for x in trades
                 if x.get('action') == 'CLOSE_' + t['old_side']
                 and x.get('reason') == t['close_reason']
                 and float(x['id']) >= float(t['close_requested_at_ms'])]
        if not fills:
            return False
        closed = max(fills)
        return (math.isfinite(closed) and int(closed / 60000) == int(now / 60)
                and not any(x.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                            and float(x.get('id', 0)) >= closed for x in trades))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def quote_ready(engine, symbol, frame, price, side):
    import time
    try:
        now = time.time()
        quoted = float(getattr(engine, '_channel_entry_quote_times', {}).get(symbol, float('nan')))
        return (math.isfinite(quoted) and 0 <= now - quoted <= 5
                and frame is not None and len(frame) >= 3
                and int(float(frame.iloc[-1]['timestamp']) / 60000) == int(now / 60)
                and math.isfinite(float(price)) and price > 0
                and live_adverse_entry_safe(frame, price, side))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
