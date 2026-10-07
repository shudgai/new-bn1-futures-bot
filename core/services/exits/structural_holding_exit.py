"""Closed price pivots with MA5 reversal; profit locks remain retired."""
import math
from core.config import CHANNEL_WATERFALL_BODY_ATR
from core.services.early_swing_reversal import EXIT as REVERSAL_EXIT
from core.services.exits.confirmed_pivot_exit import confirmed_pivot_turn, REASON as PIVOT_EXIT
from core.services.exits.ma5_outer_pivot_exit import (
    REASON as MA5_EXIT, RULE_VERSION, confirmed_ma5_outer_pivot,
)

REASON = 'EXIT_CONFIRMED_SWING_STRUCTURE'
HARD = 'EXIT_ACCOUNT_HARD_STOP'
WATERFALL = 'EXIT_STRUCTURAL_WATERFALL'
POLICY = 'closed_price_pivot_ma5_reverse_v3'
ALLOWED = {HARD, WATERFALL, MA5_EXIT}
RETIRED_CLOSE_REASONS = {
    'EXIT_INITIAL_ATR_HARD_STOP',
    'EXIT_CLOSED_MA5_OUTER_PIVOT',
    'EXIT_SWING_ATR_PROFIT_LOCK', 'EXIT_MOVING_PROFIT_STOP',
    'EXIT_CHANNEL_SAME_BAR_NET_PROFIT_LOCK', 'EXIT_CHANNEL_SAME_BAR_END',
    'EXIT_CONFIRMED_TREND_REVERSAL', 'EXIT_EXHAUSTED_OUTER_SWING_REVERSAL',
    'EXIT_PEAK_PULLBACK_PRESSURE', 'EXIT_REALTIME_PEAK_TRAILING',
    'EXIT_OPPOSITE_KC_BREAK',
    REASON, REVERSAL_EXIT, PIVOT_EXIT,
    'EXIT_TERMINAL_DOJI_PRESSURE', 'EXIT_ADVERSE_ABNORMAL_BODY',
}
PROFIT_KEYS = ('swing_atr_profit_lock', 'same_bar_profit_lock',
    'profit_stop_price', 'profit_stop_net', 'profit_stop_source',
    'profit_stop_triggered', 'profit_stop_policy', 'profit_stop_pivot_ms',
    'profit_stop_confirmed_ms', 'profit_stop_arm_atr', 'confirmed_trend_exit',
    'structure_break_warning', 'trend_exhaustion_warning')


def retire_profit_state(position, state, meta=None):
    """Revoke old soft-close retries in both persisted copies; keep hard retries."""
    if str(position.get('entry_mode', '')).upper() != 'CHANNEL_SWING':
        return
    from core.services.exits.hard_stop_service import retire_initial_atr_pending
    retire_initial_atr_pending(position, meta)
    sources = [state, position, meta or {}]
    if meta and isinstance(meta.get('peak_trailing_state'), dict):
        sources.append(meta['peak_trailing_state'])
    for source in sources:
        for key in PROFIT_KEYS:
            source.pop(key, None)
        source['armed'] = False
        pending = source.get('pending')
        if pending not in (HARD, WATERFALL) and source.get('holding_exit_policy') != POLICY:
            source.pop('pending', None)
            source.pop('trigger', None)
        if pending in RETIRED_CLOSE_REASONS:
            source.pop('pending', None)
            source.pop('trigger', None)
        if pending == MA5_EXIT and (source.get('ma5_outer_pivot') or {}).get('rule_version') != RULE_VERSION:
            source.pop('pending', None)
            source.pop('trigger', None)
        for key in ('confirmed_pivot_exit', 'pivot_guard_version',
                    'structure_break_confirmation', 'structure_break_level',
                    'structure_break_atr', 'structure_break_threshold', 'intact_trend_pullback'):
            source.pop(key, None)
    for source in (position, meta or {}):
        audit = source.get('exit_protection_snapshot')
        if isinstance(audit, dict) and audit.get('reason') in RETIRED_CLOSE_REASONS:
            source.pop('exit_protection_snapshot', None)


def valid_snapshot(snapshot):
    try:
        if not isinstance(snapshot, dict) or snapshot.get('reason') or snapshot.get('fallback_used'):
            return False
        stamp = float(snapshot['quote_ms'])
        bar = math.floor(stamp / 60000) * 60000
        return math.isfinite(stamp) and stamp > 0 and snapshot['closed_bar_ms'] == bar-60000 and snapshot['live_bar_ms'] == bar
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def intact_trend_pullback(snapshot, sign):
    """A local turn alone cannot close an intact, still advancing trend."""
    try:
        if not valid_snapshot(snapshot):
            return False
        structure = snapshot['swing_structure_' + ('long' if sign == 1 else 'short')]
        rows = snapshot['kc_closed_history'][-2:]
        ma5, previous = float(snapshot['ma5']), float(snapshot['last_ma5'])
        if (structure.get('side') != ('LONG' if sign == 1 else 'SHORT')
                or structure.get('intact') is not True
                or structure.get('closed_break_confirmed') is True or len(rows) != 2):
            return False
        stamps = [float(r['timestamp']) for r in rows]
        middle = [float(r['middle']) for r in rows]
        if not all(math.isfinite(v) and v > 0 for v in [*stamps,*middle,ma5,previous]):
            return False
        return (stamps[1]-stamps[0] == 60000 and stamps[-1] == snapshot['closed_bar_ms']
                and sign*(middle[1]-middle[0]) > max(middle)*1e-12
                and sign*(ma5-previous) > max(ma5,previous)*1e-12)
    except (KeyError,TypeError,ValueError,IndexError,OverflowError):
        return False


def evaluate_structural_holding(position, state, price, snapshot, entry, qty, sign, atr, fee, slippage):
    retire_profit_state(position, state)
    reason = state.get('pending') if state.get('pending') in ALLOWED else None
    # Account loss limits are independent of profit, pivot or a new opposite entry.
    from core.services.exits.hard_stop_service import hard_stop_reason
    account_reason = hard_stop_reason(position, price)
    if account_reason:
        reason = HARD
    valid = valid_snapshot(snapshot)
    if reason != HARD and valid and waterfall_ready(position, snapshot, price, sign):
        reason = WATERFALL
    if reason is None and valid:
        confirmation = confirmed_ma5_outer_pivot(position, state, snapshot, sign)
        if confirmation:
            reason = MA5_EXIT
            state['ma5_outer_pivot'] = confirmation
    state['holding_exit_policy'] = POLICY
    if not reason:
        return None
    trigger = (account_reason or state.get('trigger', 'ACCOUNT_LOSS')) if reason == HARD else 'WATERFALL_DROP' if reason == WATERFALL else reason
    state.update(pending=reason, trigger=trigger)
    return dict(action='FULL_CLOSE', type=reason, reason=reason, trigger=trigger, price=price)


def early_reversal_ready(position, state, snapshot, price, sign):
    """Restore the v33 post-entry closed pivot reversal and prior excursion gate."""
    try:
        evidence = snapshot.get('early_swing_reversal') or {}
        if evidence.get('side') != ('SHORT' if sign == 1 else 'LONG'):
            return False
        pivot, confirmed, edge, entry, scale, entered, peak = map(float, (
            evidence['reversal_pivot_ms'], evidence['reversal_confirmed_ms'], evidence['reversal_edge'],
            position['entry_price'], position['entry_atr'], position['open_timestamp'], state['peak_price']))
        if not all(math.isfinite(v) and v > 0 for v in (pivot, confirmed, edge, entry, scale, entered, peak, price)):
            return False
        return (pivot > entered*1000 and confirmed == snapshot['closed_bar_ms']
                and confirmed > pivot and sign*(peak-entry) >= .5*scale
                and sign*(price-edge) < 0)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def waterfall_ready(position, snapshot, price, sign):
    """Live adverse body using the prior completed ATR, never a wick."""
    try:
        opened, scale, bar, entered = map(float, (
            snapshot['live_open'], snapshot['atr'], snapshot['live_bar_ms'], position['open_timestamp']))
        if not all(math.isfinite(v) and v > 0 for v in (opened, scale, bar, entered, price)):
            return False
        if bar < math.floor(entered*1000/60000)*60000:
            return False
        return sign*(opened-price) >= CHANNEL_WATERFALL_BODY_ATR*scale
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
