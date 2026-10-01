"""Bounded, closed-candle KC breakout replay shared by scan and order checks.

Replay is read-only: repeated diagnostics never age or consume a pending signal.
The original breakout timestamp survives pullbacks and identifies successful fills.
"""
import math

KC_PENDING_CODES = frozenset(('KC_3BAR_CONFIRM_LONG', 'KC_3BAR_CONFIRM_SHORT'))
KC_PENDING_EVIDENCE_KEYS = ('kc_confirmation_edge', 'kc_distance_atr', 'kc_max_distance_atr',
                            'confirmation_ma5', 'previous_ma5', 'confirmation_ma15',
                            'pending_signal_id', 'pending_second_bar_id', 'pending_wait_bars',
                            'pending_max_wait_bars')
MAX_WAIT_BARS = 2
MAX_DISTANCE_ATR = 0.5
MAX_PULLBACK_BODY_ATR = 0.5


def above_limit(value, limit):
    return value > limit and not math.isclose(value, limit, rel_tol=1e-12)


def evaluate_kc_pending_entry(closed, quote, code=None):
    """Return ENTER only for the latest closed confirmation, otherwise a WAIT state."""
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
            identity = f'{side}:{int(first.timestamp)}:{int(second.timestamp)}'
            evidence = dict(pending_signal_id=identity,
                            breakout_bar_id=float(first.timestamp),
                            pair_confirmation_bar_id=float(second.timestamp),
                            pending_second_bar_id=float(second.timestamp),
                            pending_wait_bars=waited, pending_max_wait_bars=MAX_WAIT_BARS)
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
                elif above_limit(distance, MAX_DISTANCE_ATR):
                    reason = 'KC_PENDING_CANCELLED_CHASE'
                else:
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
                                    kc_max_distance_atr=MAX_DISTANCE_ATR,
                                    confirmation_ma5=float(row.ma5), previous_ma5=float(prior.ma5),
                                    confirmation_ma15=float(row.ma15), **evidence)
                    continue
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
