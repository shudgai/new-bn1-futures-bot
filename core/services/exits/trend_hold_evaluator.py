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
    """Use only confirmed one-bar-sided pivots; require a strict live break."""
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
            confirmed = (pivot < values[i-1] and pivot < values[i+1]) if side == 'LONG' else (pivot > values[i-1] and pivot > values[i+1])
            if confirmed:
                # A prior closed break cannot be undone by a later rebound.
                later = values[i+1:]
                intact = min(later+[price]) >= pivot if side == 'LONG' else max(later+[price]) <= pivot
                return {'intact': intact, 'level': pivot, 'side': side}
    except (KeyError, TypeError, ValueError):
        return None
    return None
