def evaluate_trend_hold(position, snapshot, current_price):
    if not snapshot or snapshot.get('reason'):
        return 'UNKNOWN', snapshot.get('reason', 'NO_DATA')

    if 'ma5' not in snapshot or 'last_ma5' not in snapshot:
        return 'UNKNOWN', 'NO_DATA'

    side = position.get('side', 'LONG')
    ma5 = snapshot.get('ma5', 0.0)
    ma15 = snapshot.get('ma15', 0.0)
    kc_mid = snapshot.get('kc_middle', 0.0)
    last_ma5 = snapshot.get('last_ma5', ma5)
    last_ma15 = snapshot.get('last_ma15', ma15)
    last_close = snapshot.get('last_close', current_price)

    ma5_slope = ma5 - last_ma5
    ma15_slope = ma15 - last_ma15

    if side == 'LONG':
        if last_close < kc_mid and ma5_slope <= 0:
            return 'RELEASED', 'CLOSED_BELOW_KC_MID_AND_MA5_WEAK'
        if ma5 < ma15:
            return 'RELEASED', 'MA5_BELOW_MA15'

        if ma5 > ma15 and ma5_slope > 0 and ma15_slope >= 0 and current_price > kc_mid:
            if current_price < ma5:
                return 'WARNING', 'PRICE_BELOW_MA5_BUT_KC_HELD'
            return 'HOLD', 'STRONG_TREND_LONG'

        return 'RELEASED', 'NOT_IN_STRONG_TREND'

    elif side == 'SHORT':
        if last_close > kc_mid and ma5_slope >= 0:
            return 'RELEASED', 'CLOSED_ABOVE_KC_MID_AND_MA5_WEAK'
        if ma5 > ma15:
            return 'RELEASED', 'MA5_ABOVE_MA15'

        if ma5 < ma15 and ma5_slope < 0 and ma15_slope <= 0 and current_price < kc_mid:
            if current_price > ma5:
                return 'WARNING', 'PRICE_ABOVE_MA5_BUT_KC_HELD'
            return 'HOLD', 'STRONG_TREND_SHORT'

        return 'RELEASED', 'NOT_IN_STRONG_TREND'

    return 'RELEASED', 'UNKNOWN_SIDE'


def strong_direction_held(position, snapshot, current_price):
    """Keep a directional holding through ordinary pullbacks, without waiving stops."""
    import math
    if not isinstance(snapshot, dict) or snapshot.get('fallback_used', False):
        return False
    status, _ = evaluate_trend_hold(position, snapshot, current_price)
    if status not in ('HOLD', 'WARNING'):
        return False
    try:
        atr = float(position.get('entry_atr', 0))
        movement = float(snapshot['ma5']) - float(snapshot['last_ma5'])
        sign = 1 if position.get('side') == 'LONG' else -1
        return math.isfinite(atr) and atr > 0 and math.isfinite(movement) and sign * movement >= .05 * atr
    except (KeyError, TypeError, ValueError):
        return False


def confirmed_swing_structure(position, closed, price):
    """Use confirmed pivots including equal-price plateaus; require a strict break."""
    import math
    side = position.get('side')
    if side not in ('LONG', 'SHORT') or closed is None or len(closed) < 3:
        return None
    column = 'low' if side == 'LONG' else 'high'
    try:
        values = [float(v) for v in closed[column].iloc[-60:]]
        price = float(price)
        if not math.isfinite(price) or price <= 0 or any(not math.isfinite(v) or v <= 0 for v in values):
            return None
        for i in range(len(values)-2, 0, -1):
            pivot = values[i]
            # A flat top/bottom is one swing, not a missing pivot. Require a
            # strictly lower/higher completed candle on both sides of its plateau.
            left = i
            while left > 0 and values[left-1] == pivot:
                left -= 1
            confirmed = left > 0 and ((pivot < values[left-1] and pivot < values[i+1]) if side == 'LONG' else (pivot > values[left-1] and pivot > values[i+1]))
            if confirmed:
                # A prior closed break cannot be undone by a later rebound.
                later = values[i+1:]
                intact = min(later+[price]) >= pivot if side == 'LONG' else max(later+[price]) <= pivot
                closing = float(closed.iloc[-1]['close'])
                if not math.isfinite(closing) or closing <= 0:
                    return None
                closed_break = closing < pivot if side == 'LONG' else closing > pivot
                return {'intact': intact, 'level': pivot, 'side': side,
                        'closed_break_confirmed': closed_break}
    except (KeyError, TypeError, ValueError):
        return None
    return None


def evaluate_closed_kc_hold(position, snapshot):
    """Two consecutive non-directional closed middle steps release trend protection."""
    import math
    try:
        if not isinstance(snapshot,dict) or snapshot.get('reason') or snapshot.get('fallback_used'):
            return 'UNKNOWN'
        side=position.get('side')
        if side not in ('LONG','SHORT'):
            return 'UNKNOWN'
        history=snapshot['kc_closed_history'][-3:]
        if len(history)!=3:
            return 'UNKNOWN'
        stamps=[float(row['timestamp']) for row in history]
        middle=[float(row['middle']) for row in history]
        quote_ms=float(snapshot['quote_ms'])
        if not all(math.isfinite(v) and v>0 for v in [quote_ms,*stamps,*middle]):
            return 'UNKNOWN'
        if stamps[1]-stamps[0]!=60000 or stamps[2]-stamps[1]!=60000:
            return 'UNKNOWN'
        if stamps[-1]!=math.floor(quote_ms/60000)*60000-60000:
            return 'UNKNOWN'
        sign=1 if side=='LONG' else -1
        steps=[sign*(b-a) for a,b in zip(middle,middle[1:])]
        if steps[-1]>0:
            return 'HOLD'
        return 'RELEASED' if steps[0]<=0 else 'WAIT_CONFIRMATION'
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):
        return 'UNKNOWN'


def confirmed_early_reversal(position, snapshot, price, require_closed_break=False):
    """Require a strict live pivot break or two closed reverse KC steps plus MA5."""
    import math
    try:
        if not isinstance(snapshot,dict) or snapshot.get('reason') or snapshot.get('fallback_used'):
            return False
        side=position['side']
        if side not in ('LONG','SHORT'):
            return False
        sign=1 if side=='LONG' else -1
        price=float(price)
        if not math.isfinite(price) or price<=0:
            return False
        structure=snapshot.get('swing_structure_'+side.lower()) or {}
        level=float(structure.get('level') or 0)
        if (structure.get('side')==side and math.isfinite(level) and level>0
                and sign*(price-level)<0
                and (not require_closed_break or structure.get('closed_break_confirmed') is True)):
            return True
        if evaluate_closed_kc_hold(position,snapshot)!='RELEASED':
            return False
        history=snapshot['kc_closed_history']
        if not all(sign*(float(b['middle'])-float(a['middle']))<0 for a,b in zip(history,history[1:])):
            return False
        ma5,previous,atr=[float(snapshot[k]) for k in ('ma5','last_ma5','atr')]
        return (all(math.isfinite(v) and v>0 for v in (ma5,previous,atr))
                and sign*(ma5-previous)<=-.05*atr and sign*(price-ma5)<0)
    except (KeyError,TypeError,ValueError,OverflowError):
        return False
