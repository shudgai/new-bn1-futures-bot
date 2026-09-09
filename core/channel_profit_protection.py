"""Channel Swing profit floor and retracement calculations, independent of orders."""
import math


def trend_style(frame, side, opened_at=None):
    """Classify closed bars, using KC width to compare different price scales.

    STACKED: two favorable bodies, each >=15% of channel width, total
    >=50%, with rising/falling closes and <=25% adjacent body overlap.
    SMOOTH: six bars with >=75% directional efficiency, <=25% width
    close drawdown, favorable channel slope and closes in its outer half.
    Stacked evidence must start after entry; smooth context can precede it.
    """
    required = {'open', 'close', 'kc_upper', 'kc_lower'}
    if frame is None or len(frame) < 3 or not required.issubset(frame.columns):
        return 'UNKNOWN'
    try:
        rows = frame.iloc[-7:-1]
        values = rows[list(sorted(required))].astype(float)
        if not all(math.isfinite(v) and v > 0 for v in values.to_numpy().flat):
            return 'UNKNOWN'
        widths = (rows['kc_upper'].astype(float) - rows['kc_lower'].astype(float)).tolist()
        if min(widths) <= 0:
            return 'UNKNOWN'
        sign = 1 if side == 'LONG' else -1
        opens = [sign * float(v) for v in rows['open']]
        closes = [sign * float(v) for v in rows['close']]
        bodies = [c - o for o, c in zip(opens, closes)]
        recent = rows.iloc[-2:]
        post_entry = (not opened_at or ('timestamp' in recent and
                      all(float(v) >= float(opened_at) * 1000 for v in recent['timestamp'])))
        stacked = post_entry and all(b >= .15 * w for b, w in zip(bodies[-2:], widths[-2:]))
        if stacked and sum(bodies[-2:]) >= .50 * sum(widths[-2:]) / 2:
            if all(closes[i] > closes[i-1] and
                   max(0., closes[i-1] - opens[i]) <= .25 * min(bodies[i-1], bodies[i])
                   for i in range(len(closes)-1, len(closes))):
                return 'STACKED'
        if len(rows) < 6:
            return 'UNKNOWN'
        moves = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        distance = sum(abs(v) for v in moves)
        efficiency = sum(moves) / distance if distance else 0.
        drawdown = max(max(closes[:i+1]) - c for i, c in enumerate(closes))
        middles = [sign * (float(u) + float(l)) / 2
                   for u, l in zip(rows['kc_upper'], rows['kc_lower'])]
        if (efficiency >= .75 and drawdown <= .25 * min(widths)
                and all(middles[i] > middles[i-1] for i in range(1, len(middles)))
                and all(c > m for c, m in zip(closes, middles))):
            return 'SMOOTH'
        return 'CHOPPY'
    except (TypeError, ValueError, KeyError, OverflowError):
        return 'UNKNOWN'


def protection(position, price, fee, slippage, frame=None):
    """Persist a per-position peak; return whether the synthetic stop is crossed."""
    entry = float(position.get('entry_price') or 0)
    qty = float(position.get('qty') or 0)
    side = position.get('side')
    if side not in ('LONG', 'SHORT') or not all(math.isfinite(x) and x > 0 for x in (entry, qty, price)):
        return None
    sign = 1 if side == 'LONG' else -1
    execution = price * (1 - sign * slippage)
    gross = sign * (price - entry) * qty
    net = sign * (execution - entry) * qty - (entry + execution) * qty * fee
    state = position.setdefault('channel_profit_protection', {})
    identity = [side, position.get('open_timestamp'), entry, qty]
    if state.get('identity') != identity:
        state.clear()
        state['identity'] = identity
    peak = max(float(state.get('peak_gross', 0)), gross)
    state['peak_gross'] = peak
    was_armed = bool(state.get('armed'))
    style_opened_at = position.get('open_timestamp')
    if style_opened_at and 'KC_NEXT_LIVE_PUSH_' in str(position.get('reason') or ''):
        # This entry was bought during the second candle: retain its preceding
        # breakout as context, otherwise its two-body stack would be discarded.
        style_opened_at = math.floor(float(style_opened_at) / 60) * 60 - 60
    style = trend_style(frame, side, style_opened_at) if frame is not None else 'CHOPPY'
    state['trend_style'] = style
    if style == 'STACKED':
        state['stacked_seen'] = True
    # Every style arms at the same net floor; classification only tightens stacking.
    state['armed'] = was_armed or net >= 1.0
    if not state['armed']:
        return None
    if state.get('stacked_seen') and frame is not None and not frame.empty:
        try:
            live_open = float(frame.iloc[-1]['open'])
            if math.isfinite(live_open) and live_open > 0 and sign * (price - live_open) < 0:
                state['tightened'] = True
        except (TypeError, ValueError, KeyError):
            pass
    retracement = .10 if state.get('tightened') else .30
    state['retracement_fraction'] = retracement
    if side == 'LONG':
        floor = (entry * (1 + fee) + 1 / qty) / ((1 - slippage) * (1 - fee))
        trailing = entry + (1 - retracement) * peak / qty
        stop = max(floor, trailing, float(state.get('stop_price', 0)))
    else:
        floor = (entry * (1 - fee) - 1 / qty) / ((1 + slippage) * (1 + fee))
        trailing = entry - (1 - retracement) * peak / qty
        stop = min(floor, trailing, float(state.get('stop_price', float('inf'))))
    state['stop_price'] = stop
    state['net_floor_price'] = floor
    # Arming at exactly one dollar must not instantly close the new position.
    crossed = sign * (price - stop)
    triggered = (was_armed and crossed <= 0) or (bool(state.get('tightened')) and crossed < 0)
    return {'triggered': triggered,
            'stop_price': stop, 'peak_gross': peak, 'net_pnl': net,
            'retracement_fraction': retracement, 'trend_style': style}


def abnormal_long_bar(frame, price):
    """Reuse KC spike sizes: range >1.25 widths, body >=.8 width or 3x average."""
    abnormal = None
    for offset in (-2, -1):
        row = frame.iloc[offset]
        width = float(row['kc_upper']) - float(row['kc_lower'])
        close = price if offset == -1 else float(row['close'])
        body = abs(close - float(row['open']))
        prior = frame.iloc[max(0, len(frame) + offset - 9):len(frame) + offset]
        average = (prior['close'].astype(float) - prior['open'].astype(float)).abs().mean()
        span = max(float(row['high']), close) - min(float(row['low']), close)
        if width > 0 and (span > width * 1.25 or body >= width * .8 or (average > 0 and body >= average * 3)):
            abnormal = float(row.get('timestamp', row.name))
    return abnormal


def reentry_gate(ticket, frame, price):
    """Returns ready/wait/end."""
    if frame is None or len(frame) < 4:
        return 'wait'
    upper, lower = (float(frame.iloc[-1][key]) for key in ('kc_upper', 'kc_lower'))
    if not all(math.isfinite(v) and v > 0 for v in (price, upper, lower)) or lower >= upper:
        return 'wait'
    side = ticket['side']
    outside = price > upper if side == 'LONG' else price < lower
    if side == 'LONG':
        # A tick outside can become only a wick. Require a closed solid body
        # crossing the rail after the latest pullback, then its live successor.
        try:
            live, previous = frame.iloc[-1], frame.iloc[-2]
            live_bar = float(live.get('timestamp', live.name))
            if price <= upper:
                if math.isfinite(live_bar):
                    ticket['pulled_back_inside'] = True
                    ticket['pullback_bar'] = live_bar
                return 'wait'
            if not ticket.get('pulled_back_inside') or 'pullback_bar' not in ticket:
                return 'wait'
            previous_bar = float(previous.get('timestamp', previous.name))
            pullback_bar = float(ticket['pullback_bar'])
            opened, closed, high, low, rail, bottom = (
                float(previous[key]) for key in
                ('open', 'close', 'high', 'low', 'kc_upper', 'kc_lower'))
            live_open = float(live['open'])
            if not all(math.isfinite(value) and value > 0 for value in
                       (opened, closed, high, low, rail, bottom, live_open)):
                return 'wait'
            if not all(math.isfinite(value) for value in (previous_bar, pullback_bar, live_bar)):
                return 'wait'
            body, span = closed - opened, high - low
            if (previous_bar < pullback_bar or previous_bar >= live_bar
                    or bottom >= rail or not low <= opened < closed <= high
                    or not bottom <= opened <= rail < closed
                    or span <= 0 or body / span < .20
                    or abs(live_open - closed) > .25 * body
                    or price <= max(live_open, closed, upper)):
                return 'wait'
            return 'ready'
        except (TypeError, ValueError, KeyError, IndexError):
            return 'wait'
    return 'ready' if outside else 'end'
