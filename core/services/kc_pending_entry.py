"""Third-candle live KC breakout confirmation shared by scan and order checks.

Evaluation is read-only. The original breakout pair identifies successful fills.
Only the immediately following forming candle can authorize entry.
"""
import math
from collections import OrderedDict

KC_PENDING_CODES = frozenset(('KC_3BAR_CONFIRM_LONG', 'KC_3BAR_CONFIRM_SHORT',
                              'KC_2BAR_CONFIRM_LONG', 'KC_2BAR_CONFIRM_SHORT'))
KC_PENDING_EVIDENCE_KEYS = ('kc_confirmation_edge', 'kc_distance_atr', 'kc_max_distance_atr',
                            'confirmation_ma5', 'previous_ma5', 'confirmation_ma15',
                            'pending_signal_id', 'pending_second_bar_id', 'pending_wait_bars',
                            'pending_max_wait_bars', 'third_body_ratio', 'third_weak_body_max_ratio')
MAX_WAIT_BARS = 1
WEAK_BODY_MAX_RATIO = 0.25
MAX_DISTANCE_ATR = 0.5
STRONG_BREAKOUT_MAX_ATR = 2.0
MAX_PULLBACK_BODY_ATR = 0.5
MIN_ENTRY_BODY_ATR = 0.5
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
    """Confirm using 2 completed candles (K1 breakout, K2 confirm).
    Live quote (K3) must be strictly outside the rail.
    """
    wait = lambda reason: dict(action='WAIT', reason=reason)
    if len(closed) < 2:
        return wait('WAIT_VALID_CLOSE_HISTORY')
        
    from core.services.strategies.outer_strategy import ck_direction
    direction_frame = closed if live is None else closed.copy()
    if live is not None:
        import pandas as pd
        direction_frame = pd.concat([closed, live.to_frame().T], ignore_index=True)
    direction = ck_direction(direction_frame)
    if not direction:
        return wait('WAIT_VALID_DIRECTION')

    first, second = closed.iloc[-2], closed.iloc[-1]
    
    try:
        for row in (first, second):
            values = [float(row[key]) for key in ('timestamp', 'open', 'close', 'high', 'low', 'atr', 'kc_upper', 'kc_lower', 'ma5', 'ma15')]
            if not all(math.isfinite(v) and v > 0 for v in values):
                return wait('WAIT_VALID_KC_PENDING_DATA')
                
        if float(second.timestamp) - float(first.timestamp) != 60000:
            return wait('WAIT_VALID_KC_PENDING_DATA')

        for side, sign, key in (('LONG', 1, 'kc_upper'), ('SHORT', -1, 'kc_lower')):
            if direction != side:
                continue
                
            # Rule 1 & 4 - 必須在軌道內側或碰軌
            if not (float(first.kc_lower) <= float(first.open) <= float(first.kc_upper)):
                continue

            # K1 & K2 Same color
            if sign * (float(first.close) - float(first.open)) <= 0:
                continue
            if sign * (float(second.close) - float(second.open)) <= 0:
                continue

            # K1 & K2 Close outside
            if sign * (float(first.close) - float(first[key])) <= 0:
                continue
            if sign * (float(second.close) - float(second[key])) <= 0:
                continue

            # Every body >= 20%
            f_span = float(first.high) - float(first.low)
            f_body = abs(float(first.close) - float(first.open))
            if f_span <= 0 or (f_body / f_span) < 0.20:
                continue

            s_span = float(second.high) - float(second.low)
            s_body = abs(float(second.close) - float(second.open))
            if s_span <= 0 or (s_body / s_span) < 0.20:
                continue
                
            # MA5 conditions (from old logic)
            k2_ma5_delta = sign * (float(second.ma5) - float(first.ma5))
            if k2_ma5_delta <= max(float(first.ma5), float(second.ma5)) * 1e-12:
                return wait('BLOCKED_MA5_FLAT_OPPOSITE_OR_INVALID')
            if sign * (float(second.ma5) - float(second.ma15)) <= 0:
                continue

            s_atr = float(second.atr)
            if s_atr > 0 and k2_ma5_delta / s_atr < MIN_MA5_SLOPE_ATR:
                return wait('BLOCKED_FLAT_MA5')

            signal = 'KC_2BAR_CONFIRM_' + side
            if code is not None and code != signal:
                return wait('WAIT_NEW_KC_BREAKOUT')

            price = float(quote)
            if not math.isfinite(price) or price <= 0:
                return wait('WAIT_VALID_QUOTE')

            # Live price must be strictly outside
            s_edge = float(second[key] if live is None else live[key])
            if not math.isfinite(s_edge) or s_edge <= 0:
                return wait('WAIT_VALID_KC_PENDING_DATA')
            distance = sign * (price - s_edge) / s_atr if s_atr > 0 else 0.
            if distance <= 0:
                return wait('KC_PENDING_CANCELLED_INSIDE_RAIL')
                
            stamp_val = float(second.timestamp) + 60000 if live is None else float(getattr(live, 'timestamp', float(second.timestamp) + 60000))
            
            return dict(action='ENTER', side=side, type=signal, reason=signal,
                        price=price, entry_atr=s_atr, confirmation_bar_id=stamp_val,
                        close_price=float(second.close), intrabar=True,
                        entry_phase='KC_2BAR_CLOSED_CONFIRM',
                        breakout_bar_id=float(first.timestamp),
                        pair_confirmation_bar_id=float(second.timestamp),
                        third_bar_id=stamp_val,
                        pending_signal_id=f'{side}:{int(first.timestamp)}:{int(second.timestamp)}',
                        pending_second_bar_id=float(second.timestamp),
                        pending_wait_bars=1, pending_max_wait_bars=1)
                        
        return wait('WAIT_NEW_KC_BREAKOUT')
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return wait('WAIT_VALID_KC_PENDING_DATA')
