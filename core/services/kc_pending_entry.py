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
    """Confirm only the forming third candle using its open and the latest quote.

    The two completed candles establish structure. Counter-color bodies require
    a body/range ratio at most 25%; observations never poison later live checks.
    No fourth-bar recovery or retrospective closed-third entry is permitted.
    """
    wait = lambda reason: dict(action='WAIT', reason=reason)
    if len(closed) < 2:
        return wait('WAIT_NEW_KC_BREAKOUT')
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
            if not (sign * (float(first.close) - float(first.open)) > 0
                    and sign * (float(first.open) - float(first[key])) <= 0
                    and sign * (float(first.close) - float(first[key])) > 0
                    and sign * (float(second.close) - float(second.open)) > 0
                    and sign * (float(second.close) - float(second[key])) > 0
                    and sign * (float(second.ma5) - float(second.ma15)) > 0
                    and sign * (float(second.ma5) - float(first.ma5)) > 0):
                continue
            signal = 'KC_3BAR_CONFIRM_' + side
            if code is not None and code != signal:
                return wait('WAIT_NEW_KC_BREAKOUT')
            if live is None:
                return wait('WAIT_LIVE_THIRD_BAR')
            stamp, opening, edge, price = map(float, (live.timestamp, live.open, live[key], quote))
            if (not all(math.isfinite(v) and v > 0 for v in (stamp, opening, edge, price))
                    or stamp != float(second.timestamp) + 60000 or bool(live.is_closed)):
                return wait('WAIT_VALID_LIVE_THIRD_BAR')
            body = sign * (price - opening)
            high, low = float(live.high), float(live.low)
            if not all(math.isfinite(v) and v > 0 for v in (high, low)) or high < low:
                return wait('WAIT_VALID_LIVE_THIRD_BAR')
            span = max(high, price, opening) - min(low, price, opening)
            ratio = abs(price - opening) / span if span > 0 else 0.
            if body <= 0 and (span <= 0 or above_limit(ratio, WEAK_BODY_MAX_RATIO)):
                return wait('WAIT_THIRD_SAME_COLOR' if body == 0 else 'BLOCKED_THIRD_OPPOSITE_BODY')
            atr = float(second.atr)
            distance = sign * (price - edge) / atr
            if distance <= 0:
                return wait('KC_PENDING_CANCELLED_INSIDE_RAIL')
            if above_limit(distance, STRONG_BREAKOUT_MAX_ATR):
                return wait('KC_PENDING_CANCELLED_CHASE')
            return dict(action='ENTER', side=side, type=signal, reason=signal,
                        price=price, entry_atr=atr, confirmation_bar_id=stamp,
                        close_price=float(second.close), intrabar=True,
                        entry_phase='KC_3BAR_LIVE_CONFIRM',
                        breakout_bar_id=float(first.timestamp),
                        pair_confirmation_bar_id=float(second.timestamp),
                        pending_signal_id=f'{side}:{int(first.timestamp)}:{int(second.timestamp)}',
                        pending_second_bar_id=float(second.timestamp),
                        pending_wait_bars=1, pending_max_wait_bars=1,
                        third_bar_id=stamp, third_open=opening, third_reference_atr=atr,
                        third_body_ratio=ratio, third_weak_body_max_ratio=WEAK_BODY_MAX_RATIO,
                        kc_confirmation_edge=edge, kc_distance_atr=distance,
                        kc_max_distance_atr=STRONG_BREAKOUT_MAX_ATR,
                        confirmation_ma5=float(second.ma5), previous_ma5=float(first.ma5),
                        confirmation_ma15=float(second.ma15),
                        entry_mode=('STRONG_BREAKOUT_CONFIRM' if above_limit(distance, MAX_DISTANCE_ATR)
                                    else 'NORMAL_CONFIRM'))
        return wait('WAIT_NEW_KC_BREAKOUT')
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return wait('WAIT_VALID_KC_PENDING_DATA')
