"""Third-candle live KC breakout confirmation shared by scan and order checks.

Evaluation is read-only. The original breakout pair identifies successful fills.
Only the immediately following forming candle can authorize entry.
"""
import math
from collections import OrderedDict

KC_PENDING_CODES = frozenset(('KC_3BAR_CONFIRM_LONG', 'KC_3BAR_CONFIRM_SHORT'))
KC_PENDING_EVIDENCE_KEYS = ('kc_confirmation_edge', 'kc_distance_atr', 'kc_max_distance_atr',
                            'confirmation_ma5', 'previous_ma5', 'confirmation_ma15',
                            'pending_signal_id', 'pending_second_bar_id', 'pending_wait_bars',
                            'pending_max_wait_bars', 'third_body_ratio', 'third_weak_body_max_ratio')
MAX_WAIT_BARS = 1
WEAK_BODY_MAX_RATIO = 0.25
MAX_DISTANCE_ATR = 0.5
STRONG_BREAKOUT_MAX_ATR = 2.0
MAX_PULLBACK_BODY_ATR = 0.5
MIN_ENTRY_BODY_ATR = 0.25
MAX_ADVERSE_ATR = 0.50
MIN_MA5_SLOPE_ATR = 0.01

# Bounded LRU cache for invalidated signals.
# Key: (symbol, side, signal_id, candidate_bar_id) — composite, globally unique.
# Value: True (sentinel; only key matters).
# Size: fixed upper bound. Oldest entry evicted first (FIFO via OrderedDict).
MAX_INVALIDATED_SIGNALS = 4096


class _BoundedSet:
    """FIFO-evicting bounded set backed by an OrderedDict."""

    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._data: OrderedDict = OrderedDict()

    def add(self, key) -> None:
        if key in self._data:
            return  # already present, no duplicate
        self._data[key] = True
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)  # evict oldest

    def __contains__(self, key) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        self._data.clear()


_INVALIDATED_SIGNALS: _BoundedSet = _BoundedSet(MAX_INVALIDATED_SIGNALS)


def _make_signal_identity(symbol: str, side: str, signal_id: str, candidate_bar_id) -> tuple:
    """Composite identity tuple. Globally unique across symbol/side/time."""
    return (symbol, side, signal_id, int(candidate_bar_id))


def above_limit(value, limit):
    return value > limit and not math.isclose(value, limit, rel_tol=1e-12)


def evaluate_kc_pending_entry(closed, quote, code=None, symbol: str = '', *, live=None):
    """Confirm using 3 completed candles (K1 breakout, K2 confirm, K3 same-color closed confirm).

    The third candle must be fully closed and verified as a same-color body before entry.
    Entry takes place immediately upon K3 close (during K4 open/live quote).
    """
    wait = lambda reason: dict(action='WAIT', reason=reason)
    if len(closed) < 2:
        return wait('WAIT_NEW_KC_BREAKOUT')
        
    from core.services.strategies.outer_strategy import ck_direction
    direction = ck_direction(closed)
    if len(closed) < 3:
        # Check if first two closed bars are forming a valid breakout pair
        first, second = closed.iloc[-2], closed.iloc[-1]
        try:
            for row in (first, second):
                values = [float(row[key]) for key in
                          ('timestamp', 'open', 'close', 'atr', 'kc_upper', 'kc_lower', 'ma5', 'ma15')]
                if not all(math.isfinite(v) and v > 0 for v in values):
                    return wait('WAIT_VALID_KC_PENDING_DATA')
            if float(second.timestamp) - float(first.timestamp) != 60000:
                return wait('WAIT_VALID_KC_PENDING_DATA')
            for side, sign, key in (('LONG', 1, 'kc_upper'), ('SHORT', -1, 'kc_lower')):
                if direction != side:
                    continue
                if (sign * (float(first.close) - float(first.open)) > 0
                        and sign * (float(first.open) - float(first[key])) <= 0
                        and sign * (float(first.close) - float(first[key])) > 0
                        and sign * (float(second.close) - float(second.open)) > 0
                        and sign * (float(second.close) - float(second[key])) > 0
                        and sign * (float(second.ma5) - float(second.ma15)) > 0
                        and sign * (float(second.ma5) - float(first.ma5)) > 0):
                    return wait('WAIT_THIRD_BAR_CLOSE')
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
            pass
        return wait('WAIT_NEW_KC_BREAKOUT')

    first, second, third = closed.iloc[-3], closed.iloc[-2], closed.iloc[-1]
    try:
        for row in (first, second, third):
            values = [float(row[key]) for key in
                      ('timestamp', 'open', 'close', 'atr', 'kc_upper', 'kc_lower', 'ma5', 'ma15')]
            if not all(math.isfinite(v) and v > 0 for v in values):
                return wait('WAIT_VALID_KC_PENDING_DATA')
        if float(second.timestamp) - float(first.timestamp) != 60000 or float(third.timestamp) - float(second.timestamp) != 60000:
            return wait('WAIT_VALID_KC_PENDING_DATA')
        for side, sign, key in (('LONG', 1, 'kc_upper'), ('SHORT', -1, 'kc_lower')):
            if direction != side:
                continue
            k2_ma5_delta = sign * (float(second.ma5) - float(first.ma5))
            if not (sign * (float(first.close) - float(first.open)) > 0
                    and sign * (float(first.open) - float(first[key])) <= 0
                    and sign * (float(first.close) - float(first[key])) > 0
                    and sign * (float(second.close) - float(second.open)) > 0
                    and sign * (float(second.close) - float(second[key])) > 0
                    and sign * (float(second.ma5) - float(second.ma15)) > 0
                    and k2_ma5_delta > 0):
                continue

            t_atr = float(third.atr)
            if t_atr > 0 and k2_ma5_delta / t_atr < MIN_MA5_SLOPE_ATR:
                return wait('BLOCKED_FLAT_MA5')

            k3_ma5_delta = sign * (float(third.ma5) - float(second.ma5))
            if k3_ma5_delta <= 0 or (t_atr > 0 and k3_ma5_delta / t_atr < MIN_MA5_SLOPE_ATR):
                return wait('BLOCKED_FLAT_MA5')

            signal = 'KC_3BAR_CONFIRM_' + side
            if code is not None and code != signal:
                return wait('WAIT_NEW_KC_BREAKOUT')

            t_open, t_close, t_high, t_low = float(third.open), float(third.close), float(third.high), float(third.low)
            t_edge = float(third[key])
            t_atr = float(third.atr)

            # K3 Direction confirmation (Must be same color)
            if sign * (t_close - t_open) <= 0:
                return wait('BLOCKED_THIRD_OPPOSITE_BODY' if t_close != t_open else 'WAIT_THIRD_SAME_COLOR')

            # K3 must close outside the rail
            if sign * (t_close - t_edge) <= 0:
                return wait('KC_PENDING_CANCELLED_INSIDE_RAIL')

            # K3 body ratio / non-doji check
            t_span = max(t_high, t_close, t_open) - min(t_low, t_close, t_open)
            t_ratio = abs(t_close - t_open) / t_span if t_span > 0 else 0.
            if t_ratio <= 0.10:
                return wait('WAIT_THIRD_STRONG_BODY')

            # K3 minimum body ATR and max adverse excursion
            if side == 'LONG':
                body_atr = (t_close - t_open) / t_atr if t_atr > 0 else 0.
                adverse_atr = (t_open - t_low) / t_atr if t_atr > 0 else 0.
            else:
                body_atr = (t_open - t_close) / t_atr if t_atr > 0 else 0.
                adverse_atr = (t_high - t_open) / t_atr if t_atr > 0 else 0.

            if body_atr < MIN_ENTRY_BODY_ATR:
                return wait('WAIT_THIRD_MIN_BODY_ATR')

            if adverse_atr > MAX_ADVERSE_ATR:
                return wait('BLOCKED_THIRD_ADVERSE_EXCURSION')

            # Quote / 4th bar validation
            price = float(quote)
            if not math.isfinite(price) or price <= 0:
                return wait('WAIT_VALID_QUOTE')

            if live is not None:
                stamp = float(getattr(live, 'timestamp', 0) or 0)
                if stamp != float(third.timestamp) + 60000:
                    return wait('WAIT_VALID_LIVE_FOURTH_BAR')

            distance = sign * (price - t_edge) / t_atr if t_atr > 0 else 0.
            if distance <= 0:
                return wait('KC_PENDING_CANCELLED_INSIDE_RAIL')
            if above_limit(distance, STRONG_BREAKOUT_MAX_ATR):
                return wait('KC_PENDING_CANCELLED_CHASE')

            stamp_val = float(third.timestamp) + 60000 if live is None else float(getattr(live, 'timestamp', float(third.timestamp) + 60000))
            return dict(action='ENTER', side=side, type=signal, reason=signal,
                        price=price, entry_atr=t_atr, confirmation_bar_id=stamp_val,
                        close_price=float(third.close), intrabar=False,
                        entry_phase='KC_3BAR_CLOSED_CONFIRM',
                        breakout_bar_id=float(first.timestamp),
                        pair_confirmation_bar_id=float(second.timestamp),
                        third_bar_id=float(third.timestamp),
                        pending_signal_id=f'{side}:{int(first.timestamp)}:{int(second.timestamp)}:{int(third.timestamp)}',
                        pending_second_bar_id=float(second.timestamp),
                        pending_wait_bars=1, pending_max_wait_bars=1,
                        third_open=t_open, third_reference_atr=t_atr,
                        third_body_ratio=t_ratio, third_weak_body_max_ratio=WEAK_BODY_MAX_RATIO,
                        kc_confirmation_edge=t_edge, kc_distance_atr=distance,
                        kc_max_distance_atr=STRONG_BREAKOUT_MAX_ATR,
                        confirmation_ma5=float(third.ma5), previous_ma5=float(second.ma5),
                        confirmation_ma15=float(third.ma15),
                        entry_mode=('STRONG_BREAKOUT_CONFIRM' if above_limit(distance, MAX_DISTANCE_ATR)
                                    else 'NORMAL_CONFIRM'))
        return wait('WAIT_NEW_KC_BREAKOUT')
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return wait('WAIT_VALID_KC_PENDING_DATA')
