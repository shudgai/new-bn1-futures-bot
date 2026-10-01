"""Closed-candle KC two-bar entry; no forming-candle signal predicates."""
import math

KC_TWO_BAR_CODES = frozenset(('KC_2BAR_BREAKOUT_LONG', 'KC_2BAR_BREAKOUT_SHORT'))
KC_TWO_BAR_EVIDENCE_KEYS = ('kc_confirmation_edge', 'kc_distance_atr', 'kc_max_distance_atr',
                            'confirmation_ma5', 'previous_ma5', 'confirmation_ma15')
MAX_KC_DISTANCE_ATR = 0.5


def evaluate_kc_two_bar_entry(closed, quote, code=None):
    """Caller validates OHLC, finality and account guards; use each row's own rail."""
    try:
        first, second = closed.iloc[-2], closed.iloc[-1]
        ma5, previous_ma5, ma15 = float(second.ma5), float(first.ma5), float(second.ma15)
        atr = float(second.atr)
        if not all(math.isfinite(v) and v > 0 for v in (ma5, previous_ma5, ma15, atr)):
            return None
        for side, sign in (('LONG', 1), ('SHORT', -1)):
            signal = 'KC_2BAR_BREAKOUT_' + side
            if code is not None and code != signal:
                continue
            edge = 'kc_upper' if sign == 1 else 'kc_lower'
            if any(sign * (float(row.close) - float(row.open)) <= 0 or
                   sign * (float(row.close) - float(row[edge])) <= 0
                   for row in (first, second)):
                continue
            if sign * (ma5 - ma15) <= 0 or sign * (ma5 - previous_ma5) <= 0:
                continue
            distance = sign * (float(second.close) - float(second[edge])) / atr
            if distance > MAX_KC_DISTANCE_ATR and not math.isclose(distance, MAX_KC_DISTANCE_ATR, rel_tol=1e-12):
                continue
            return dict(action='ENTER', side=side, type=signal, reason=signal,
                        price=quote, entry_atr=atr, confirmation_bar_id=float(second.timestamp),
                        breakout_bar_id=float(first.timestamp), pair_confirmation_bar_id=float(second.timestamp),
                        close_price=float(second.close), intrabar=False, entry_phase='KC_2BAR_BREAKOUT',
                        kc_confirmation_edge=float(second[edge]), kc_distance_atr=distance,
                        kc_max_distance_atr=MAX_KC_DISTANCE_ATR, confirmation_ma5=ma5,
                        previous_ma5=previous_ma5, confirmation_ma15=ma15)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None
    return None
