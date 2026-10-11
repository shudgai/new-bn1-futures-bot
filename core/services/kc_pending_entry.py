"""Third-candle live KC breakout confirmation shared by scan and order checks.

Evaluation is read-only. The original breakout pair identifies successful fills.
Only the immediately following forming candle can authorize entry.
"""
import math
from collections import OrderedDict

KC_PENDING_CODES = frozenset(('KC_2BAR_CONFIRM_LONG', 'KC_2BAR_CONFIRM_SHORT'))
KC_REALTIME_PATTERN_CODES = frozenset((
    'KC_LIVE_PATTERN_BREAKOUT_LONG', 'KC_LIVE_PATTERN_BREAKOUT_SHORT',
))
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
SMALL_PATTERN_BODY_MAX_ATR = 0.35
SMALL_PATTERN_BODY_MAX_RANGE_RATIO = 0.50
LIVE_PATTERN_BODY_MIN_ATR = 0.50
LIVE_PATTERN_BODY_MIN_RANGE_RATIO = 0.50
LIVE_PATTERN_MAX_DISTANCE_ATR = 3.0
MAX_BREAKOUT_DISTANCE_ATR = 3.0
MIN_BREAKOUT_BODY_ATR = 0.5

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
    wait = lambda reason, **evidence: dict(action='WAIT', reason=reason, **evidence)
    if len(closed) < 3:
        return wait('WAIT_VALID_CLOSE_HISTORY')
        
    reference, first, second = closed.iloc[-3], closed.iloc[-2], closed.iloc[-1]
    
    try:
        for row in (first, second):
            values = [float(row[key]) for key in ('timestamp', 'open', 'close', 'high', 'low', 'atr', 'kc_upper', 'kc_lower')]
            if not all(math.isfinite(v) and v > 0 for v in values):
                return wait('WAIT_VALID_KC_PENDING_DATA')
                
        if float(second.timestamp) - float(first.timestamp) != 60000:
            return wait('WAIT_VALID_KC_PENDING_DATA')

        for side, sign, key in (('LONG', 1, 'kc_upper'), ('SHORT', -1, 'kc_lower')):
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

            # The confirmed candle itself must align with the short trend.
            # The live candle's color is intentionally not part of this gate.
            previous_ma5 = float(first['ma5'])
            confirmation_ma5 = float(second['ma5'])
            if not all(math.isfinite(value) and value > 0
                       for value in (previous_ma5, confirmation_ma5)):
                continue
            if (sign * (confirmation_ma5 - previous_ma5) <= 0
                    or sign * (float(second.close) - confirmation_ma5) <= 0):
                continue

            # Every body >= 20%
            f_span = float(first.high) - float(first.low)
            f_body = abs(float(first.close) - float(first.open))
            reference_atr = float(reference['atr'])
            if (f_span <= 0 or not math.isfinite(reference_atr)
                    or reference_atr <= 0
                    or (f_body / f_span) < 0.20
                    or f_body < MIN_BREAKOUT_BODY_ATR * reference_atr):
                continue

            s_span = float(second.high) - float(second.low)
            s_body = abs(float(second.close) - float(second.open))
            if s_span <= 0 or (s_body / s_span) < 0.20:
                continue
                
            s_atr = float(second.atr)

            signal = 'KC_2BAR_CONFIRM_' + side
            if code is not None and code != signal:
                return wait('WAIT_NEW_KC_BREAKOUT')

            price = float(quote)
            if not math.isfinite(price) or price <= 0:
                return wait('WAIT_VALID_QUOTE')

            # The latest quote must remain beyond the latest available rail,
            # including the forming candle's updated outer band.
            s_edge = float(second[key])
            if live is not None:
                live_edge = float(getattr(live, key))
                if not math.isfinite(live_edge) or live_edge <= 0:
                    return wait('WAIT_VALID_KC_PENDING_DATA')
                s_edge = live_edge
            distance = sign * (price - s_edge) / s_atr if s_atr > 0 else 0.
            if distance <= 0:
                return wait(
                    'KC_PENDING_CANCELLED_INSIDE_RAIL',
                    side=side, type='KC_2BAR_CONFIRM_' + side,
                    entry_phase='KC_2BAR_CLOSED_CONFIRM',
                    breakout_bar_id=float(first.timestamp),
                    pair_confirmation_bar_id=float(second.timestamp),
                    pending_signal_id=f'{side}:{int(first.timestamp)}:{int(second.timestamp)}',
                )
            if distance > MAX_BREAKOUT_DISTANCE_ATR:
                return wait('BLOCKED_BY_EXTENDED_BREAKOUT', side=side,
                            kc_distance_atr=distance,
                            kc_max_distance_atr=MAX_BREAKOUT_DISTANCE_ATR)
                
            stamp_val = (
                float(second.timestamp) + 60000
                if live is None
                else float(getattr(live, 'timestamp', float(second.timestamp) + 60000))
            )
            is_intrabar = live is None or not bool(getattr(live, 'is_closed', False))
            
            return dict(action='ENTER', side=side, type=signal, reason=signal,
                        price=price, entry_atr=s_atr, confirmation_bar_id=stamp_val,
                        close_price=float(second.close), intrabar=is_intrabar,
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


def evaluate_kc_live_pattern_entry(closed, quote, live, code=None, symbol: str = ''):
    """Authorize a live outer-rail break after a small counter-push setup.

    SHORT: one small red candle, followed by one to three small green candles,
    then a large live red body crossing the lower KC rail. LONG mirrors colors
    and rail. The live candle must open inside the channel and the quote must
    already be strictly outside its side's outer rail.
    """
    wait = lambda reason, **evidence: dict(action='WAIT', reason=reason, **evidence)
    if len(closed) < 2 or live is None:
        return wait('WAIT_LIVE_PATTERN_HISTORY')

    try:
        quote = float(quote)
        live_stamp = float(getattr(live, 'timestamp'))
        opened = float(getattr(live, 'open'))
        upper = float(getattr(live, 'kc_upper'))
        lower = float(getattr(live, 'kc_lower'))
        atr = float(closed.iloc[-1]['atr'])
        high = max(float(getattr(live, 'high')), opened, quote)
        low = min(float(getattr(live, 'low')), opened, quote)
        values = (quote, live_stamp, opened, upper, lower, atr, high, low)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or lower >= upper or high <= low or atr <= 0):
            return wait('WAIT_VALID_LIVE_PATTERN_DATA')

        # Pattern setup is one initial small directional candle followed by
        # one, two, or three small opposite-color candles.
        for side, sign, rail in (('LONG', 1, upper), ('SHORT', -1, lower)):
            signal = f'KC_LIVE_PATTERN_BREAKOUT_{side}'
            if code is not None and code != signal:
                continue
            for pullback_count in (3, 2, 1):
                setup_count = pullback_count + 1
                if len(closed) < setup_count:
                    continue
                setup = closed.iloc[-setup_count:]
                valid_setup = True
                for index, (_, row) in enumerate(setup.iterrows()):
                    candle_open = float(row['open'])
                    candle_close = float(row['close'])
                    candle_high = float(row['high'])
                    candle_low = float(row['low'])
                    candle_atr = float(row['atr'])
                    candle_range = candle_high - candle_low
                    body = abs(candle_close - candle_open)
                    expected_sign = sign if index == 0 else -sign
                    if (not all(math.isfinite(value) and value > 0 for value in (
                            candle_open, candle_close, candle_high, candle_low, candle_atr))
                            or candle_range <= 0
                            or expected_sign * (candle_close - candle_open) <= 0
                            or body > SMALL_PATTERN_BODY_MAX_ATR * candle_atr
                            or body / candle_range > SMALL_PATTERN_BODY_MAX_RANGE_RATIO):
                        valid_setup = False
                        break
                if not valid_setup:
                    continue

                live_body = sign * (quote - opened)
                live_range = high - low
                opens_inside = lower <= opened <= upper
                outside = quote > upper if sign == 1 else quote < lower
                distance_atr = sign * (quote - rail) / atr
                if (opens_inside and outside
                        and live_body >= LIVE_PATTERN_BODY_MIN_ATR * atr
                        and live_body / live_range >= LIVE_PATTERN_BODY_MIN_RANGE_RATIO
                        and 0 < distance_atr <= LIVE_PATTERN_MAX_DISTANCE_ATR):
                    first_stamp = float(setup.iloc[0]['timestamp'])
                    last_setup_stamp = float(setup.iloc[-1]['timestamp'])
                    return dict(
                        action='ENTER', side=side, type=signal, reason=signal,
                        price=quote, entry_atr=atr, confirmation_bar_id=live_stamp,
                        close_price=quote, intrabar=True,
                        entry_phase='KC_LIVE_PATTERN_BREAKOUT',
                        breakout_bar_id=live_stamp,
                        pair_confirmation_bar_id=last_setup_stamp,
                        third_bar_id=live_stamp,
                        pending_signal_id=(
                            f'{symbol}:{side}:LIVE:{int(first_stamp)}:{int(live_stamp)}'
                        ),
                        pending_second_bar_id=last_setup_stamp,
                        pending_wait_bars=0, pending_max_wait_bars=0,
                        live_pattern_start_bar_id=first_stamp,
                        live_pattern_pullback_bars=pullback_count,
                        live_pattern_body_atr=live_body / atr,
                        kc_confirmation_edge=rail,
                        kc_distance_atr=distance_atr,
                        kc_max_distance_atr=LIVE_PATTERN_MAX_DISTANCE_ATR,
                    )
        return wait('WAIT_LIVE_PATTERN_BREAKOUT')
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return wait('WAIT_VALID_LIVE_PATTERN_DATA')
