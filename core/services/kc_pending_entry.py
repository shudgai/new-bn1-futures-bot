"""Bounded, closed-candle KC breakout replay shared by scan and order checks.

Replay is read-only: repeated diagnostics never age or consume a pending signal.
The original breakout timestamp survives pullbacks and identifies successful fills.
"""
import math
from collections import OrderedDict

KC_PENDING_CODES = frozenset(('KC_3BAR_CONFIRM_LONG', 'KC_3BAR_CONFIRM_SHORT'))
KC_PENDING_EVIDENCE_KEYS = ('kc_confirmation_edge', 'kc_distance_atr', 'kc_max_distance_atr',
                            'confirmation_ma5', 'previous_ma5', 'confirmation_ma15',
                            'pending_signal_id', 'pending_second_bar_id', 'pending_wait_bars',
                            'pending_max_wait_bars')
MAX_WAIT_BARS = 2
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


def evaluate_kc_pending_entry(closed, quote, code=None, symbol: str = ''):
    """Return ENTER only for the latest closed confirmation, otherwise a WAIT state.

    Args:
        closed: DataFrame of closed candles with KC / MA indicators.
        quote:  Live price used only when returning ENTER on the last bar.
        code:   If given, restrict to a specific signal code.
        symbol: Symbol string (e.g. 'BTC/USDT') used to form the composite
                invalidation identity so that cross-symbol collisions are impossible.
    """
    result = dict(action='WAIT', reason='WAIT_NEW_KC_BREAKOUT')
    seed = pending = previous = None
    last_index = len(closed) - 1
    for index, row in enumerate(closed.itertuples(index=False)):
        try:
            numbers = [float(getattr(row, key)) for key in
                       ('timestamp', 'open', 'high', 'low', 'close', 'atr',
                        'kc_upper', 'kc_middle', 'kc_lower', 'ma5', 'ma15')]
            if not all(math.isfinite(v) and v > 0 for v in numbers):
                raise ValueError('invalid closed indicators')
        except (AttributeError, TypeError, ValueError, OverflowError):
            seed = pending = previous = None
            result = dict(action='WAIT', reason='WAIT_VALID_KC_PENDING_DATA')
            continue
        if previous is not None and float(row.timestamp) - float(previous.timestamp) != 60000:
            seed = pending = None
        prior = previous
        previous = row
        if pending is not None:
            first, second, side, second_index = pending
            sign = 1 if side == 'LONG' else -1
            edge = float(row.kc_upper if side == 'LONG' else row.kc_lower)
            body = sign * (float(row.close) - float(row.open))
            distance = sign * (float(row.close) - edge) / float(row.atr)
            waited = index - second_index
            signal_id_str = f'{side}:{int(first.timestamp)}:{int(second.timestamp)}'
            candidate_bar = int(row.timestamp)
            composite_key = _make_signal_identity(symbol, side, signal_id_str, candidate_bar)
            evidence = dict(pending_signal_id=signal_id_str,
                            breakout_bar_id=float(first.timestamp),
                            pair_confirmation_bar_id=float(second.timestamp),
                            pending_second_bar_id=float(second.timestamp),
                            pending_wait_bars=waited, pending_max_wait_bars=MAX_WAIT_BARS)

            # Terminal state: once this composite identity is invalidated, never revive.
            if composite_key in _INVALIDATED_SIGNALS:
                pending = None
                result = dict(action='WAIT', reason='KC_PENDING_INVALIDATED')
                continue

            reason = None
            if waited > MAX_WAIT_BARS:
                reason = 'KC_PENDING_EXPIRED'
            elif distance <= 0:
                reason = 'KC_PENDING_CANCELLED_INSIDE_RAIL'
            elif sign * (float(row.ma5) - float(row.ma15)) <= 0:
                reason = 'KC_PENDING_CANCELLED_MA_STRUCTURE'
            elif body > 0:
                if sign * (float(row.ma5) - float(prior.ma5)) <= 0:
                    reason = 'KC_PENDING_CANCELLED_MA_SLOPE'
                else:
                    is_strong_override = (waited == 1)
                    effective_limit = STRONG_BREAKOUT_MAX_ATR if is_strong_override else MAX_DISTANCE_ATR

                    if above_limit(distance, effective_limit):
                        reason = 'KC_PENDING_CANCELLED_CHASE'
                    else:
                        entry_mode = 'STRONG_BREAKOUT_CONFIRM' if above_limit(distance, MAX_DISTANCE_ATR) else 'NORMAL_CONFIRM'
                        evidence.update({
                            'entry_mode': entry_mode,
                            'normal_limit': MAX_DISTANCE_ATR,
                            'strong_limit': STRONG_BREAKOUT_MAX_ATR
                        })
                        signal = 'KC_3BAR_CONFIRM_' + side
                        # Consumed structurally once confirmed: a later bar cannot revive it.
                        pending = None
                        result = dict(action='WAIT', reason='KC_PENDING_CONFIRMATION_PASSED', **evidence)
                        if index == last_index and (code is None or code == signal):
                            return dict(action='ENTER', side=side, type=signal, reason=signal,
                                        price=quote, entry_atr=float(row.atr),
                                        confirmation_bar_id=float(row.timestamp),
                                        close_price=float(row.close), intrabar=False,
                                        entry_phase='KC_3BAR_CONFIRM',
                                        kc_confirmation_edge=edge, kc_distance_atr=distance,
                                        kc_max_distance_atr=effective_limit,
                                        confirmation_ma5=float(row.ma5), previous_ma5=float(prior.ma5),
                                        confirmation_ma15=float(row.ma15), **evidence)
                        continue
            elif waited == 1:
                # Bar 3 is opposite direction: TERMINAL invalidation.
                # Composite key prevents cross-symbol/side collision.
                reason = 'KC_PENDING_INVALIDATED'
                _INVALIDATED_SIGNALS.add(composite_key)
            elif above_limit(abs(body) / float(row.atr), MAX_PULLBACK_BODY_ATR):
                reason = 'KC_PENDING_CANCELLED_LARGE_PULLBACK'
            elif waited >= MAX_WAIT_BARS:
                reason = 'KC_PENDING_EXPIRED'
            if reason:
                pending = None
                result = dict(action='WAIT', reason=reason, **evidence)
            else:
                result = dict(action='WAIT', reason=f'KC_BREAKOUT_{side}_PENDING', **evidence)
            # No overlapping pair can reset the original timeout on this candle.
            continue
        if seed is not None:
            first, side = seed
            seed = None
            sign = 1 if side == 'LONG' else -1
            edge = float(row.kc_upper if side == 'LONG' else row.kc_lower)
            if (sign * (float(row.close) - float(row.open)) > 0
                    and sign * (float(row.close) - edge) > 0
                    and sign * (float(row.ma5) - float(row.ma15)) > 0
                    and sign * (float(row.ma5) - float(first.ma5)) > 0):
                pending = (first, row, side, index)
                result = dict(action='WAIT', reason=f'KC_BREAKOUT_{side}_PENDING',
                              pending_signal_id=f'{side}:{int(first.timestamp)}:{int(row.timestamp)}',
                              breakout_bar_id=float(first.timestamp),
                              pair_confirmation_bar_id=float(row.timestamp),
                              pending_second_bar_id=float(row.timestamp),
                              pending_wait_bars=0, pending_max_wait_bars=MAX_WAIT_BARS)
                continue
            result = dict(action='WAIT', reason='WAIT_NEW_KC_BREAKOUT')
        for side, sign, edge in (('LONG', 1, float(row.kc_upper)),
                                 ('SHORT', -1, float(row.kc_lower))):
            if (sign * (float(row.close) - float(row.open)) > 0
                    and sign * (float(row.open) - edge) <= 0
                    and sign * (float(row.close) - edge) > 0):
                seed = (row, side)
                result = dict(action='WAIT', reason=f'WAIT_KC_SECOND_{side}')
                break
    return result
