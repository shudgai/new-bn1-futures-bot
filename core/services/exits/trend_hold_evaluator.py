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
