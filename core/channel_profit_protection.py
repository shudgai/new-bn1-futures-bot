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
    retracement = .10 if state.get('tightened') else .20
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


def directional_entry_ready(frame, price, side="LONG"):
    """Require a solid directional live body and non-adverse outer/middle KC."""
    if side not in ("LONG", "SHORT"):
        return False
    sign = 1 if side == "LONG" else -1
    try:
        rows = frame.iloc[-3:]
        if len(rows) != 3:
            return False
        upper = [float(v) for v in rows['kc_upper']]
        lower = [float(v) for v in rows['kc_lower']]
        middle = []
        for (_, row), top, bottom in zip(rows.iterrows(), upper, lower):
            value = row.get('ema_20', float('nan'))
            if not math.isfinite(float(value)):
                value = row.get('kc_middle', float('nan'))
            middle.append(float(value) if math.isfinite(float(value)) else (top + bottom) / 2)
        live = rows.iloc[-1]
        opened, high, low = (float(live[k]) for k in ('open', 'high', 'low'))
        if not all(math.isfinite(v) and v > 0 for v in upper + lower + middle + [opened, high, low, price]):
            return False
        span = max(high, price) - min(low, price)
        return bool(all(b < t for b, t in zip(lower, upper))
                    and all(sign * values[0] <= sign * values[1] <= sign * values[2]
                            for values in (upper if side == "LONG" else lower, middle))
                    and sign * (price - opened) > 0
                    and sign * (price - (upper[-1] if side == "LONG" else lower[-1])) > 0
                    and span > 0 and sign * (price - opened) / span >= .20)
    except (TypeError, ValueError, KeyError, IndexError):
        return False


def long_entry_ready(frame, price):
    return directional_entry_ready(frame, price, "LONG")


def reentry_gate(ticket, frame, price):
    """Pull back, then form two new closed directional bodies before reentry."""
    try:
        side = ticket['side']
        if side not in ('LONG', 'SHORT') or frame is None or len(frame) < 4:
            return 'wait'
        sign = 1 if side == 'LONG' else -1
        rail_key = 'kc_upper' if side == 'LONG' else 'kc_lower'
        live = frame.iloc[-1]
        rail = float(live[rail_key])
        bar = float(live.get('timestamp', live.name))
        if not all(math.isfinite(v) and v > 0 for v in (price, rail)) or not math.isfinite(bar):
            return 'wait'
        if sign * (price - rail) <= 0:
            ticket['pulled_back_inside'] = True
            ticket['pullback_bar'] = bar
            return 'wait'
        if not ticket.get('pulled_back_inside') or 'pullback_bar' not in ticket:
            return 'wait'
        breakout, confirmation = frame.iloc[-3], frame.iloc[-2]
        times = [float(row.get('timestamp', row.name)) for row in (breakout, confirmation)]
        pullback = float(ticket['pullback_bar'])
        if not all(math.isfinite(v) for v in times + [pullback]) or not pullback <= times[0] < times[1] < bar:
            return 'wait'
        for row in (breakout, confirmation):
            o, c, h, l, u, d = [float(row[k]) for k in ('open','close','high','low','kc_upper','kc_lower')]
            if (not all(math.isfinite(v) and v > 0 for v in (o,c,h,l,u,d))
                    or not l <= min(o,c) < max(o,c) <= h or d >= u
                    or sign * (c-o) / (h-l) < .20
                    or sign * (c-float(row[rail_key])) <= 0):
                return 'wait'
        o = float(breakout['open'])
        if not float(breakout['kc_lower']) <= o <= float(breakout['kc_upper']):
            return 'wait'
        body = abs(float(breakout['close']) - o)
        if (abs(float(confirmation['open']) - float(breakout['close'])) > .25 * body
                or sign * (float(confirmation['close']) - float(breakout['close'])) <= 0):
            return 'wait'
        return 'ready' if directional_entry_ready(frame, price, side) else 'wait'
    except (TypeError, ValueError, KeyError, IndexError, ZeroDivisionError):
        return 'wait'
