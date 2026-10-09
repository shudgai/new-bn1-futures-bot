"""Position-bound peak, abnormal-body and independent hard-stop exits."""
import copy
import math
import sys

from core.services.strategies.outer_strategy import ma5_ma15_trend_confirmed

POLICY = 'confirmed_pivot_abnormal_only_v3'
ABNORMAL_BODY_ATR = 1.2
ABNORMAL_REASON = 'EXIT_ADVERSE_ABNORMAL_BODY'
DOJI_TRIGGER = 'DOJI_REVERSAL_EXIT'
DOJI_RULE_VERSION = 3
DOJI_BODY_RATIO = 0.25
DOJI_ADVERSE_BODY_ATR = 0.20
STATE_KEY = 'peak_trailing_state'
PEAK_REASON = 'EXIT_REALTIME_PEAK_TRAILING'
HARD_REASON = 'EXIT_INITIAL_ATR_HARD_STOP'
RETIRED_KEYS = (
    'instant_exit_state', 'closed_exit_state', 'doji_reversal_state',
    'three_stage_exit_state', 'chandelier_state', 'channel_profit_protection',
    'ratchet_lock_state', 'swing_breakeven_armed', 'swing_peak_profit_atr',
    'swing_trailing_armed', 'swing_trailing_line', 'swing_trailing_last_bar',
    'has_broken_outer_band', 'peak_pnl_usdt', 'peak_profit_diff',
    'last_valid_swing_low', 'last_valid_swing_high', 'v10_phase_trailing',
    'active_stop_price', 'defense_line', 'ratchet_floor', 'channel_peak_abnormal',
    'channel_live_ma3_exit_pending', 'channel_live_ma3_turn_exit_pending',
    'outer_run_active', 'channel_cross_lock', 'channel_pre_lock_sl',
    'is_breakeven_moved', 'profit_lock_atr_armed', 'profit_lock_usdt_armed',
    'fixed_profit_lock_pct_armed', 'profit_lock_mode',
    'limit_tp1_price', 'limit_tp1_filled', 'breakeven_trigger_price',
)
STATE_KEYS = (STATE_KEY, 'peak_price', 'peak_pnl', 'peak_pnl_usd', 'peak_net_pnl_usd',
              'peak_gain_atr', 'peak_unrealized_profit_usd', 'current_unrealized_pnl_usd',
              'current_net_pnl_usd', 'sl', 'tp', 'stop_loss', 'entry_atr', 'atr_sl',
              'atr_tp', 'atr_protection_version', 'initial_sl', 'initial_risk')
CHANNEL_SWING_EXIT_TRIGGERS = frozenset({
    'PROFIT_LOCK_SELL_PRESSURE', 'EXIT_DOJI_BEARISH_CONFIRMATION', 'EXIT_DOJI_BULLISH_CONFIRMATION',
    'WATERFALL_DROP',
    'TWO_CLOSED_ADVERSE_ABNORMAL',
    'OPPOSITE_KC_BAND_BREACH',
    'THREE_POINT_PIVOT',
})
PIVOT_ONLY_CHANNEL_EXIT_TRIGGERS = frozenset({
    'PROFIT_LOCK_SELL_PRESSURE', 'EXIT_DOJI_BEARISH_CONFIRMATION', 'EXIT_DOJI_BULLISH_CONFIRMATION',
    'WATERFALL_DROP',
    'TWO_CLOSED_ADVERSE_ABNORMAL',
    'OPPOSITE_KC_BAND_BREACH',
    'THREE_POINT_PIVOT',
})
PIVOT_ONLY_CHANNEL_SYMBOLS = frozenset({
    'SUI/USDT', 'CAP/USDT', '龙虾/USDT', 'LOBSTER/USDT',
})
DISABLED_CHANNEL_PULLBACK_TRIGGERS = frozenset({
    'NET_ROE_5_PERCENT_1_POINT_GIVEBACK',
    'EXIT_PEAK_PULLBACK_PRESSURE',
    'CHANNEL_PEAK_PULLBACK_REVERSAL',
    'KC_CHANNEL_RETURN',
})

PROFIT_FLOOR_ENABLED = False
LOCK_ARM_ATR = None
TRAILING_DISTANCE_ATR = None

def positive(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def position_identity(position):
    result = [position['side'], float(position['open_timestamp']),
              float(position['entry_price']), abs(float(position.get('qty', position.get('quantity', 0))))]
    if result[0] not in ('LONG', 'SHORT') or not all(positive(v) for v in result[1:]):
        raise ValueError('Invalid peak-trailing identity')
    return result


def live_body_breakout_position_side(position, meta=None):
    meta = {} if meta is None else meta
    entry_snapshot = position.get('entry_snapshot') or meta.get('entry_snapshot')
    code = position.get('entry_signal_code') or meta.get('entry_signal_code')
    if not code and isinstance(entry_snapshot, dict):
        code = entry_snapshot.get('signal_code')
    side = str(position.get('side') or '').upper()
    expected = f'KC_LIVE_BODY_BREAKOUT_{side}'
    if (str(position.get('entry_mode') or meta.get('entry_mode') or '').upper()
            == 'CHANNEL_SWING'
            and side in ('LONG', 'SHORT')
            and code == expected):
        return side
    return None


def channel_initial_stop_disabled(position: dict, meta: dict | None = None) -> bool:
    meta = meta or {}
    return str(position.get('entry_mode') or meta.get('entry_mode') or '').upper() == 'CHANNEL_SWING'


def migrate_peak_state(position, meta=None):
    """Remove legacy authorities in both stores, preserving verified observations."""
    meta = {} if meta is None else meta
    ident = position_identity(position)
    state = position.get(STATE_KEY) or meta.get(STATE_KEY) or {}
    if state.get('policy') == POLICY and state.get('identity') == ident:
        state = copy.deepcopy(state)
    else:
        old = position.get('instant_exit_state') or meta.get('instant_exit_state') or {}
        peak = 0.
        if not state and (not old or old.get('identity') == ident):
            peak = max([0.] + [float(v) for v in (position.get('peak_pnl_usd'), old.get('peak')) if positive(v)])
        sign = 1 if ident[0] == 'LONG' else -1
        margin = position.get('margin')
        if not positive(margin):
            leverage = position.get('leverage') or 1.
            margin = ident[2]*ident[3]/float(leverage) if positive(leverage) else ident[2]*ident[3]
        state = dict(policy=POLICY, identity=ident, peak_price=ident[2]+sign*peak/ident[3],
                     entry_margin=float(margin), armed=False)
        if positive(position.get('entry_atr')):
            state['atr'] = float(position['entry_atr'])
        # Retain a verified initial hard-stop retry, never a retired strategy exit.
        pending = position.get('closed_exit_state') or meta.get('closed_exit_state') or {}
        if old.get('identity') == ident and pending.get('pending') and pending.get('reason') == HARD_REASON:
            state.update(pending=HARD_REASON, trigger='INITIAL_ATR')
    # Migrate matching observations, but revoke retired strategy close authority.
    if state.get('identity') == ident and state.get('policy') == POLICY:
        prior = position.get(STATE_KEY) or meta.get(STATE_KEY) or {}
        if prior.get('identity') == ident and prior.get('policy') != POLICY:
            for key in ('peak_price', 'peak_net_pnl', 'atr', 'last_ms'):
                if positive(prior.get(key)):
                    state[key] = prior[key]
            if prior.get('pending') == HARD_REASON:
                state.update(pending=HARD_REASON, trigger='INITIAL_ATR')
            elif (prior.get('pending') == ABNORMAL_REASON
                    and prior.get('trigger') in (
                        'WATERFALL_DROP', 'TWO_CLOSED_ADVERSE_ABNORMAL',
                    )):
                for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open',
                            'trigger_atr', 'trigger_price', 'trigger_confirmed_ms'):
                    if key in prior:
                        state[key] = copy.deepcopy(prior[key])
            initial = position.get('initial_sl') or meta.get('initial_sl')
            if positive(initial):
                position.update(sl=float(initial), stop_loss=float(initial), atr_sl=float(initial))
    # Old MA-touch doji tickets can retry without ever satisfying the body rule.
    # Revoke only that obsolete authority; preserve peaks and other exit retries.
    if state.get('trigger') == DOJI_TRIGGER and state.get('doji_rule_version') != DOJI_RULE_VERSION:
        for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open', 'trigger_atr', 'trigger_price'):
            state.pop(key, None)
    # Retired single-point MA turns cannot authorize a close retry.
    if state.get('trigger') in ('EXIT_PEAK_MA_TURN_PRESSURE', 'EXIT_PARABOLIC_MA3_TURN'):
        for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open', 'trigger_atr', 'trigger_price'):
            state.pop(key, None)
    if (position.get('symbol') in PIVOT_ONLY_CHANNEL_SYMBOLS
            and state.get('trigger') in (
                'MA5_TRUE_PEAK_REVERSAL', 'EXIT_PROFIT_LOCK_FLOOR',
                'EXIT_PEAK_PULLBACK_PRESSURE',
            )):
        for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open',
                    'trigger_atr', 'trigger_price', 'trigger_confirmed_ms'):
            state.pop(key, None)
        for key in ('ma5_reversal_extreme', 'ma5_reversal_last_value',
                    'ma5_reversal_favorable_seen', 'ma5_reversal_outside_seen',
                    'ma5_reversal_last_price'):
            state.pop(key, None)
    if channel_initial_stop_disabled(position, meta) and state.get('profit_lock_basis') != 'ratchet_sell_pressure_v1':
        if state.get('trigger') in ('PROFIT_LOCK_T1','PROFIT_LOCK_T2','PROFIT_LOCK_T3'):
            for key in ('pending','trigger','trigger_bar_ms','trigger_confirmed_ms'):
                state.pop(key, None)
        for key in ('tiered_roi_peak','tiered_roi_current','tiered_roi_allowance'):
            state.pop(key, None)
        state['profit_lock_basis'] = 'ratchet_sell_pressure_v1'
    if channel_initial_stop_disabled(position, meta) and state.get('lifeline_policy_version') != 1:
        # Older pivot authorization did not check the new unarmed life-line rule.
        if state.get('trigger') == 'THREE_POINT_PIVOT':
            for key in ('pending','trigger','trigger_bar_ms','trigger_confirmed_ms'):
                state.pop(key, None)
        state['lifeline_policy_version'] = 1
    if channel_initial_stop_disabled(position, meta):
        if (state.get('pending') and state.get('trigger') != HARD_REASON
                and state.get('trigger') not in CHANNEL_SWING_EXIT_TRIGGERS):
            for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open',
                        'trigger_atr', 'trigger_price', 'trigger_confirmed_ms'):
                state.pop(key, None)
        if state.get('trigger') in DISABLED_CHANNEL_PULLBACK_TRIGGERS:
            for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open',
                        'trigger_atr', 'trigger_price', 'trigger_confirmed_ms'):
                state.pop(key, None)
        if not position.get('entry_mode'):
            position['entry_mode'] = 'CHANNEL_SWING'
        if live_body_breakout_position_side(position, meta) and state.get('trigger') in (
                'THREE_POINT_PIVOT', 'MA5_TURN_REVERSAL',
                'MA5_TRUE_PEAK_REVERSAL') and position.get('symbol') not in PIVOT_ONLY_CHANNEL_SYMBOLS:
            for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open',
                        'trigger_atr', 'trigger_price', 'trigger_confirmed_ms'):
                state.pop(key, None)
        if (state.get('trigger') == 'MA5_TRUE_PEAK_REVERSAL'
                and not state.get('ma5_reversal_outside_seen')):
            for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open',
                        'trigger_atr', 'trigger_price', 'trigger_confirmed_ms'):
                state.pop(key, None)
        if state.get('pending') == HARD_REASON:
            for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open', 'trigger_atr', 'trigger_price'):
                state.pop(key, None)
        for source in (position, meta):
            source.update(sl=0., stop_loss=0., atr_sl=0., initial_sl=0., initial_risk=0.)
            source.pop('profit_floor_armed', None)
            source.pop('profit_floor_price', None)
            source.pop('frozen_lock_arm_atr', None)
            source.pop('frozen_trailing_distance_atr', None)
        for key in ('profit_floor_armed', 'profit_floor_price', 'frozen_lock_arm_atr',
                    'frozen_trailing_distance_atr'):
            state.pop(key, None)
        if state.get('trigger') in (
                'EXIT_PROFIT_LOCK_FLOOR', 'EXIT_PEAK_PULLBACK_PRESSURE',
                'EXIT_PARABOLIC_PULLBACK_1_ATR', 'TRAILING_2U_LADDER'):
            for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open',
                        'trigger_atr', 'trigger_price'):
                state.pop(key, None)
        if STATE_KEY in meta:
            meta[STATE_KEY] = copy.deepcopy(state)
    for source in (position, meta):
        for key in RETIRED_KEYS:
            source.pop(key, None)
    for key in ('net_roe_lock_peak', 'net_roe_lock_armed', 'net_roe_lock_current'):
        state.pop(key, None)
    if STATE_KEY in meta:
        meta[STATE_KEY] = copy.deepcopy(state)
    position[STATE_KEY] = state
    return state


def estimated_net_pnl(entry, price, qty, sign, fee, slippage):
    execution = price*(1-sign*slippage)
    return sign*(execution-entry)*qty - (entry+execution)*qty*fee


def ma_trend_confirms_position(position, snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('reason') is not None:
        return False
    side = position.get('side')
    return ma5_ma15_trend_confirmed(
        snapshot.get('ma5_history', ()),
        snapshot.get('ma15_history', ()),
        side,
    )


def channel_pivot_trend_confirmed(position, snapshot):
    """Require clear MA and closed-KC direction before a channel pivot can exit."""
    if not ma_trend_confirms_position(position, snapshot):
        return False
    try:
        bars = snapshot.get('history_outer_pivots')
        if not isinstance(bars, list) or len(bars) < 2:
            return False
        previous, latest = bars[-2:]
        values = [
            float(previous[key]) for key in ('kc_lower', 'kc_middle', 'kc_upper')
        ] + [
            float(latest[key]) for key in ('kc_lower', 'kc_middle', 'kc_upper')
        ]
        atr = float(snapshot.get('atr'))
        if (not all(positive(value) for value in values) or not positive(atr)
                or not values[0] < values[1] < values[2]
                or not values[3] < values[4] < values[5]
                or float(latest['ms']) - float(previous['ms']) != 60000):
            return False
        side = position.get('side')
        middle_move = values[4] - values[1]
        minimum_move = atr * 0.001
        if side == 'LONG':
            return middle_move > minimum_move and values[5] >= values[2]
        if side == 'SHORT':
            return middle_move < -minimum_move and values[3] <= values[0]
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return False
    return False


def two_closed_adverse_abnormal_exit(position, snapshot):
    """Confirm two consecutive post-entry adverse closed bodies using prior ATR."""
    try:
        from core.config import RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR

        if not isinstance(snapshot, dict):
            return None
        bars = snapshot.get('history_outer_pivots')
        if not isinstance(bars, list) or len(bars) < 3:
            return None
        opened_ms = float(position['open_timestamp']) * 1000
        entry = float(position['entry_price'])
        sign = 1 if position.get('side') == 'LONG' else -1 if position.get('side') == 'SHORT' else 0
        if not sign or not positive(opened_ms) or not positive(entry):
            return None
        first, second = bars[-2:]
        before_first = bars[-3]
        adverse_bodies = []
        for prior, bar in ((before_first, first), (first, second)):
            prior_ms = float(prior['ms'])
            bar_ms = float(bar['ms'])
            opened = float(bar['o'])
            closed = float(bar['c'])
            atr = float(prior['atr'])
            if (bar_ms - prior_ms != 60000 or bar_ms + 60000 <= opened_ms
                    or not all(positive(value) for value in
                               (prior_ms, bar_ms, opened, closed, atr))):
                return None
            effective_open = entry if math.floor(opened_ms / 60000) * 60000 == bar_ms else opened
            adverse_body = -sign * (closed - effective_open)
            threshold = atr * float(RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR)
            if not positive(threshold) or adverse_body < threshold:
                return None
            adverse_bodies.append(adverse_body)
        return {
            'trigger': 'TWO_CLOSED_ADVERSE_ABNORMAL',
            'trigger_bar_ms': float(second['ms']),
            'trigger_price': float(second['c']),
            'trigger_atr': float(bars[-2]['atr']),
        }
    except (KeyError, TypeError, ValueError, OverflowError, IndexError):
        return None


def three_point_pivot_exit(position, snapshot):
    """Return the latest confirmed pivot formed after entry began."""
    try:
        if not isinstance(snapshot, dict):
            return None
        if snapshot.get('reason') is not None:
            return None
        bars = snapshot.get('history_outer_pivots')
        if not isinstance(bars, list) or len(bars) < 3:
            return None
        opened_ms = float(position['open_timestamp']) * 1000
        sign = 1 if position['side'] == 'LONG' else -1 if position['side'] == 'SHORT' else 0
        if not sign or not positive(opened_ms):
            return None
        quote_ms = float(snapshot['quote_ms'])
        snapshot_bar_id = float(snapshot['snapshot_bar_id'])
        if (not positive(quote_ms) or not positive(snapshot_bar_id)
                or snapshot_bar_id > quote_ms):
            return None
        if (not positive(quote_ms) or quote_ms < snapshot_bar_id
                or quote_ms - snapshot_bar_id > 300000):
            return None
        # Entry-candle extremes may precede the fill; never replay them.
        for index in (len(bars) - 3,):
            before, pivot, confirm = bars[index:index + 3]
            values = [
                float(bar[key])
                for bar in (before, pivot, confirm)
                for key in ('ms', 'o', 'h', 'l', 'c')
            ]
            if not all(positive(value) for value in values):
                continue
            if any(
                float(bar['l']) > min(float(bar['o']), float(bar['c']))
                or float(bar['h']) < max(float(bar['o']), float(bar['c']))
                for bar in (before, pivot, confirm)
            ):
                continue
            before_ms, pivot_ms, confirm_ms = (
                float(bar['ms']) for bar in (before, pivot, confirm)
            )
            latest_confirmed_pivot = snapshot_bar_id == confirm_ms
            pivot_confirmation_is_fresh = quote_ms - confirm_ms <= 300000
            if (pivot_ms <= opened_ms
                    or confirm_ms <= opened_ms
                    or pivot_ms - before_ms != 60000
                    or confirm_ms - pivot_ms != 60000
                    or not (latest_confirmed_pivot and pivot_confirmation_is_fresh)
                    or quote_ms < confirm_ms
                    ):
                continue
            if sign == 1 and not (
                float(pivot['h']) > float(before['h'])
                and float(pivot['h']) > float(confirm['h'])
            ):
                continue
            if sign == -1 and not (
                float(pivot['l']) < float(before['l'])
                and float(pivot['l']) < float(confirm['l'])
            ):
                continue
            return {
                'trigger': 'THREE_POINT_PIVOT',
                'trigger_bar_ms': pivot_ms,
                'trigger_confirmed_ms': confirm_ms,
                'trigger_price': float(pivot['h'] if sign == 1 else pivot['l']),
            }
        return None
    except (KeyError, TypeError, ValueError, OverflowError, IndexError):
        return None


def live_ma5_reversal_exit(position, snapshot, sign, state=None):
    """Exit on an observed MA5 reversal after extending beyond the held-side rail.

    The previous implementation waited for live MA5 to cross the prior
    candle's MA5. That anchor can be far behind the actual intrabar extreme,
    so it often authorized the close only after most of the move had retraced.
    Keep the best MA5 observed while this position is open and act on the
    first adverse MA5 update that is also confirmed by an adverse price tick.
    """
    try:
        if not isinstance(snapshot, dict) or snapshot.get('reason') is not None:
            return None
        quote_ms = float(snapshot['quote_ms'])
        live_bar_ms = float(snapshot['live_bar_ms'])
        closed_bar_ms = float(snapshot['closed_bar_ms'])
        snapshot_bar_id = float(snapshot['snapshot_bar_id'])
        closed_ma5 = float(snapshot['closed_ma5'])
        live_ma5 = float(snapshot['live_ma5'])
        rail_key = 'live_kc_upper' if sign == 1 else 'live_kc_lower'
        live_rail = float(snapshot[rail_key])
        bar_ms = math.floor(quote_ms / 60000) * 60000
        if (not all(positive(value) for value in
                    (quote_ms, live_bar_ms, closed_bar_ms, snapshot_bar_id,
                     closed_ma5, live_ma5, live_rail))
                or live_bar_ms != bar_ms
                or closed_bar_ms != bar_ms - 60000
                or snapshot_bar_id != closed_bar_ms
                or quote_ms < closed_bar_ms
                or quote_ms - closed_bar_ms > 120000):
            return None
        state = state if isinstance(state, dict) else {}
        previous_ma5 = state.get('ma5_reversal_last_value')
        previous_price = state.get('ma5_reversal_last_price')
        peak = state.get('ma5_reversal_extreme')
        favorable_seen = bool(state.get('ma5_reversal_favorable_seen', False))
        outside_seen = bool(state.get('ma5_reversal_outside_seen', False))

        # Seed from the current quote only. Never infer an unobserved intrabar
        # peak from candle highs/lows or from data preceding position entry.
        if not positive(peak):
            peak = live_ma5
        if previous_ma5 is not None and positive(previous_ma5):
            if sign * (live_ma5 - float(previous_ma5)) > 0:
                favorable_seen = True

        if sign * (live_ma5 - float(peak)) > 0:
            peak = live_ma5
        if sign * (live_ma5 - live_rail) > 0:
            outside_seen = True

        ma5_reversed = sign * (live_ma5 - float(peak)) < 0
        # The exit decision receives the authoritative latest quote separately;
        # the caller attaches it here without reconstructing tick order from OHLC.
        quote_price = snapshot.get('quote_price', snapshot.get('price'))
        price_reversed = (
            quote_price is not None and positive(quote_price)
            and previous_price is not None and positive(previous_price)
            and sign * (float(quote_price) - float(previous_price)) < 0
        )

        state.update(
            ma5_reversal_extreme=float(peak),
            ma5_reversal_last_value=live_ma5,
            ma5_reversal_favorable_seen=favorable_seen,
            ma5_reversal_outside_seen=outside_seen,
        )
        if quote_price is not None and positive(quote_price):
            state['ma5_reversal_last_price'] = float(quote_price)
        if not (outside_seen and favorable_seen and ma5_reversed and price_reversed):
            return None
        return {
            'trigger': 'MA5_TRUE_PEAK_REVERSAL',
            'trigger_bar_ms': live_bar_ms,
            'trigger_price': float(quote_price),
            'closed_ma5': closed_ma5,
            'live_ma5': live_ma5,
            'ma5_reversal_extreme': float(peak),
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def live_breakout_channel_return_exit(position, snapshot, price, sign):
    """Close first-live-breakout positions when price re-enters the KC channel."""
    try:
        if not isinstance(snapshot, dict) or snapshot.get('reason') is not None:
            return None
        side = live_body_breakout_position_side(position)
        if side != ('LONG' if sign == 1 else 'SHORT'):
            return None

        quote_ms = float(snapshot['quote_ms'])
        live_bar_ms = float(snapshot['live_bar_ms'])
        closed_bar_ms = float(snapshot['closed_bar_ms'])
        snapshot_bar_id = float(snapshot['snapshot_bar_id'])
        lower = float(snapshot['live_kc_lower'])
        upper = float(snapshot['live_kc_upper'])
        price = float(price)
        bar_ms = math.floor(quote_ms / 60000) * 60000
        if (not all(positive(value) for value in (
                quote_ms, live_bar_ms, closed_bar_ms, snapshot_bar_id,
                lower, upper, price))
                or lower >= upper
                or live_bar_ms != bar_ms
                or closed_bar_ms != bar_ms - 60000
                or snapshot_bar_id != closed_bar_ms
                or quote_ms < closed_bar_ms
                or quote_ms - closed_bar_ms > 120000):
            return None
        returned_inside = lower < price < upper
        if not returned_inside:
            return None
        return {
            'trigger': 'KC_CHANNEL_RETURN',
            'trigger_bar_ms': live_bar_ms,
            'trigger_price': price,
            'live_kc_lower': lower,
            'live_kc_upper': upper,
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def doji_reversal_evidence(
    snapshot, price, sign, entry, opened_ms, peak_gain_atr, symbol=None
):
    """A completed doji (or stall) followed immediately by an adverse live body."""
    try:
        stamp = float(snapshot['quote_ms'])
        bar = math.floor(stamp / 60000) * 60000
        if (snapshot['live_bar_ms'] != bar or snapshot['closed_bar_ms'] != bar - 60000
                or float(snapshot['live_bar_ms']) < opened_ms - 60000):
            return None
        keys = ('last_open', 'last_high', 'last_low', 'last_close',
                'live_open', 'live_high', 'live_low', 'atr')
        values = [float(snapshot[key]) for key in keys]
        if not all(positive(v) for v in values):
            return None
        last_open, last_high, last_low, last_close, opening, high, low, atr = values
        if not (last_low <= min(last_open, last_close) <= max(last_open, last_close) <= last_high
                and low <= opening <= high):
            return None
        span = last_high - last_low
        if span <= 0:
            return None
        ratio = abs(last_close - last_open) / span
        if ratio > DOJI_BODY_RATIO and not math.isclose(ratio, DOJI_BODY_RATIO, rel_tol=1e-12):
            return None
        if symbol in PIVOT_ONLY_CHANNEL_SYMBOLS:
            history = snapshot.get('history_outer_pivots')
            if not isinstance(history, list) or len(history) < 2:
                return None
            previous_bar, doji_bar = history[-2:]
            previous_ms = float(previous_bar['ms'])
            doji_ms = float(doji_bar['ms'])
            previous_extreme = float(previous_bar['h'] if sign > 0 else previous_bar['l'])
            doji_extreme = float(doji_bar['h'] if sign > 0 else doji_bar['l'])
            if (doji_ms != bar - 60000 or doji_ms - previous_ms != 60000
                    or not all(positive(value) for value in
                               (previous_ms, doji_ms, previous_extreme, doji_extreme))
                    or (sign > 0 and doji_extreme <= previous_extreme)
                    or (sign < 0 and doji_extreme >= previous_extreme)):
                return None
        body = sign * (opening - price)
        threshold = DOJI_ADVERSE_BODY_ATR * atr
        if body <= 0 or (body < threshold and not math.isclose(body, threshold, rel_tol=1e-12)):
            return None
        live_span = max(high, price) - min(low, price)
        live_ratio = abs(price - opening) / live_span if live_span > 0 else 0.
        # Another weak/doji candle is not confirmed reversal pressure.
        if live_ratio <= DOJI_BODY_RATIO or math.isclose(live_ratio, DOJI_BODY_RATIO, rel_tol=1e-12):
            return None
        return dict(trigger_bar_ms=bar, trigger_open=opening, trigger_atr=atr,
                    trigger_price=price, doji_bar_ms=bar-60000,
                    doji_body_ratio=ratio, reversal_body_ratio=live_ratio,
                    reversal_body_atr=body/atr, doji_rule_version=DOJI_RULE_VERSION)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def evaluate_mature_reversal_exit(position, snapshot, state, sign, entry_atr):
    """Evaluates mature swing reversal exit using strictly CLOSED bars."""
    try:
        if not positive(entry_atr):
            return None

        closed_bar_ms = snapshot.get('closed_bar_ms')
        if closed_bar_ms is None or not positive(closed_bar_ms):
            return None
        closed_bar_ms = float(closed_bar_ms)

        try:
            position_open_ms = float(position.get('open_timestamp', 0))
            if position_open_ms < 1e11: # if seconds, convert to ms
                position_open_ms *= 1000
        except (TypeError, ValueError):
            position_open_ms = float('inf')

        closed_history = state.get('closed_history', [])

        if 'history_5' in snapshot:
            raw_history = snapshot['history_5']
            closed_history = []
            for b in raw_history:
                is_mature = False
                c, o, h, l = b['c'], b['o'], b['h'], b['l']
                ma3, ma5 = b['ma3'], b['ma5']
                if all(positive(v) for v in (c, o, h, l, ma3, ma5)):
                    if sign == 1:
                        is_mature = ((l > ma5) or (c > ma3)) and (b['ms'] >= position_open_ms)
                    else:
                        is_mature = ((h < ma5) or (c < ma3)) and (b['ms'] >= position_open_ms)
                closed_history.append({
                    'ms': b['ms'], 'o': o, 'h': h, 'l': l, 'c': c,
                    'ma3': ma3, 'ma5': ma5, 'is_mature': is_mature
                })
            state['closed_history'] = closed_history
        elif not closed_history or closed_history[-1]['ms'] < closed_bar_ms:
            c = snapshot.get('last_close')
            o = snapshot.get('last_open')
            h = snapshot.get('last_high')
            l = snapshot.get('last_low')
            ma3 = snapshot.get('ma3')
            ma5 = snapshot.get('ma5')

            if not all(positive(v) for v in (c, o, h, l, ma3, ma5)):
                return None

            is_mature = False
            if sign == 1:
                is_mature = ((l > ma5) or (c > ma3)) and (closed_bar_ms >= position_open_ms)
            else:
                is_mature = ((h < ma5) or (c < ma3)) and (closed_bar_ms >= position_open_ms)

            rec = {
                'ms': closed_bar_ms,
                'o': float(o), 'h': float(h), 'l': float(l), 'c': float(c),
                'ma3': float(ma3), 'ma5': float(ma5),
                'is_mature': is_mature
            }
            closed_history.append(rec)
            closed_history = closed_history[-5:]
            state['closed_history'] = closed_history

        if len(closed_history) < 5:
            return None

        rev_bar = closed_history[-1]

        maturity_bars = closed_history[-5:-1]
        if not all(b['is_mature'] for b in maturity_bars):
            return None

        o, h, l, c = rev_bar['o'], rev_bar['h'], rev_bar['l'], rev_bar['c']
        ma3 = rev_bar['ma3']
        body = abs(c - o)
        span = h - l

        pinbar = False
        doji = False

        if sign == 1:
            upper_wick = h - max(o, c)
            close_pos = (c - l) / span if span > 0 else 0
            body_ratio = body / span if span > 0 else 0
            pinbar = (upper_wick > 0.5 * entry_atr) and (upper_wick > 2.0 * body) and (close_pos < 0.40)
            doji = (body_ratio < 0.25) and (c < o) and (c < ma3)
        else:
            lower_wick = min(o, c) - l
            close_pos = (c - l) / span if span > 0 else 0
            body_ratio = body / span if span > 0 else 0
            pinbar = (lower_wick > 0.5 * entry_atr) and (lower_wick > 2.0 * body) and (close_pos > 0.60)
            doji = (body_ratio < 0.25) and (c > o) and (c > ma3)

        trigger = None
        if pinbar and doji:
            trigger = 'MATURE_REVERSAL_PINBAR_DOJI'
        elif pinbar:
            trigger = 'MATURE_REVERSAL_PINBAR'
        elif doji:
            trigger = 'MATURE_REVERSAL_DOJI'

        if trigger:
            return dict(trigger=trigger, trigger_bar_ms=rev_bar['ms'], trigger_price=c)

        return None
    except Exception:
        return None


def lifeline_held(side, price, snapshot):
    """A valid MA15 or KC middle still supporting the held direction."""
    if not isinstance(snapshot, dict):
        return False
    sign = 1 if side == 'LONG' else -1
    return any(positive(snapshot.get(key)) and sign*(price-float(snapshot[key])) >= 0
               for key in ('ma15','kc_middle'))


def trend_continuation_hold(side, price, snapshot):
    """Any observed directional candle, MA slope or rail support vetoes soft exits."""
    if not isinstance(snapshot, dict):
        return None
    sign = 1 if side == 'LONG' else -1
    ma = snapshot.get('live_ma5', snapshot.get('ma5'))
    prior_ma = snapshot.get('closed_ma5', snapshot.get('last_ma5'))
    strong = (positive(ma) and positive(prior_ma)
              and sign*(price-float(ma)) >= 0
              and sign*(float(ma)-float(prior_ma)) >= 0)
    if strong:
        return 'RIDING_STRONG_TREND'
    return None


def confirmed_doji_reversal(position, snapshot, price=None):
    """A closed post-entry doji followed by a live adverse body, or closed fallback."""
    try:
        bars = snapshot.get('history_5', [])
        if not bars:
            return None
        stamp = float(snapshot['quote_ms'])
        live_ms = snapshot.get('live_bar_ms')
        if (positive(price) and positive(live_ms) and positive(snapshot.get('live_open'))
                and float(live_ms) == math.floor(stamp/60000)*60000
                and float(bars[-1]['ms']) == float(live_ms)-60000):
            doji = bars[-1]
            reversal = dict(ms=float(live_ms),o=float(snapshot['live_open']),c=float(price),
                            h=max(float(price),float(snapshot['live_open'])),
                            l=min(float(price),float(snapshot['live_open'])))
            expected_reversal = float(live_ms)
        elif len(bars) >= 2:
            doji, reversal = bars[-2:]
            expected_reversal = math.floor(stamp/60000)*60000-60000
        else:
            return None
        if (float(doji['ms']) <= float(position['open_timestamp'])*1000
                or float(reversal['ms']) != float(doji['ms'])+60000
                or float(reversal['ms']) != expected_reversal):
            return None
        for bar in (doji, reversal):
            if not all(positive(bar.get(k)) for k in ('o','h','l','c')):
                return None
            if not bar['l'] <= min(bar['o'],bar['c']) <= max(bar['o'],bar['c']) <= bar['h']:
                return None
        span = doji['h']-doji['l']; body = abs(doji['c']-doji['o'])
        if span <= 0 or body/span > .2:
            return None
        sign = 1 if position['side']=='LONG' else -1
        if sign*(reversal['c']-reversal['o']) >= 0:
            return None
        return 'EXIT_DOJI_BEARISH_CONFIRMATION' if sign==1 else 'EXIT_DOJI_BULLISH_CONFIRMATION'
    except (KeyError,TypeError,ValueError,OverflowError):
        return None


def evaluate_peak_trailing(position, price, snapshot, atr=0., *, fee=0.0005, slippage=0.0001):
    try:
        ident = position_identity(position)
        stamp = float(snapshot) if isinstance(snapshot, (int, float)) else float(snapshot.get('quote_ms', 0))
        price, stamp, fee, slippage = map(float, (price, stamp, fee, slippage))
        if (not positive(price) or not positive(stamp) or stamp < ident[1]*1000
                or not all(math.isfinite(v) and 0 <= v < 1 for v in (fee, slippage))):
            return None
        previous = position.get(STATE_KEY) or {}
        if previous.get('identity') == ident and stamp < previous.get('last_ms', 0):
            return None
        state = migrate_peak_state(position)
        sign = 1 if ident[0] == 'LONG' else -1
        entry, qty = ident[2:]

        if 'peak_price' not in state:
            state['peak_price'] = entry

        try:
            from core.services.exits.trend_hold_evaluator import evaluate_trend_hold
            trend_status, trend_reason = evaluate_trend_hold(position, snapshot if isinstance(snapshot, dict) else {}, price)
        except Exception:
            trend_status, trend_reason = 'RELEASED', 'EVAL_ERROR'

        position['trend_hold_status'] = trend_status
        position['trend_hold_reason'] = trend_reason

        if 'crossed_kc_middle' not in state:
            state['crossed_kc_middle'] = False

        kc_middle = snapshot.get('kc_middle') if isinstance(snapshot, dict) else None
        if not state['crossed_kc_middle'] and kc_middle is not None and positive(kc_middle):
            if sign == 1 and price > kc_middle:
                state['crossed_kc_middle'] = True
            elif sign == -1 and price < kc_middle:
                state['crossed_kc_middle'] = True

        if not positive(state.get('atr')) and positive(atr):
            state['atr'] = float(atr)
        scale = float(state.get('atr') or 0.)
        state['last_ms'] = stamp
        if sign*(price-state['peak_price']) > 0:
            state['peak_price'] = price
        gain = max(0., sign*(state['peak_price']-entry))
        peak_net = estimated_net_pnl(entry, state['peak_price'], qty, sign, fee, slippage)
        net = estimated_net_pnl(entry, price, qty, sign, fee, slippage)
        state['peak_net_pnl'] = max(float(state.get('peak_net_pnl', peak_net)), peak_net)

        ladder_reason, ladder_trigger = None, None
        # 2U Fixed Ladder Profit Lock is DISABLED per user request: "只有遇到真峰頂谷底才要平倉"

        # 暴漲逃頂機制 (Parabolic Reversal Exit): 無視 CK 是否衰退
        parabolic_reason, parabolic_trigger = None, None
        peak_gain_atr = gain / scale if scale > 0 else 0.

        pf_enabled = getattr(sys.modules[__name__], 'PROFIT_FLOOR_ENABLED', False)
        pf_arm = getattr(sys.modules[__name__], 'LOCK_ARM_ATR', None)
        pf_trail = getattr(sys.modules[__name__], 'TRAILING_DISTANCE_ATR', None)

        is_channel_swing = channel_initial_stop_disabled(position)
        if (not is_channel_swing and pf_enabled and pf_arm is not None
                and pf_trail is not None and pf_arm > 0 and pf_trail > 0 and scale > 0):
            is_armed = state.get('profit_floor_armed', False)
            if not is_armed and peak_gain_atr >= pf_arm:
                is_armed = True
                state['profit_floor_armed'] = True
                # ARMED_CONFIG_POLICY = FREEZE: snapshot global parameters into state at first arming.
                # Global changes after arming must not affect this position's trailing behavior.
                state['frozen_lock_arm_atr'] = float(pf_arm)
                state['frozen_trailing_distance_atr'] = float(pf_trail)

            if is_armed:
                # Always use frozen values from state; ignore current global config.
                frozen_trail = state.get('frozen_trailing_distance_atr', pf_trail)
                if sign == 1:
                    candidate_floor = state['peak_price'] - frozen_trail * scale
                    existing_floor = state.get('profit_floor_price', -float('inf'))
                    state['profit_floor_price'] = max(existing_floor, candidate_floor)
                else:
                    candidate_floor = state['peak_price'] + frozen_trail * scale
                    existing_floor = state.get('profit_floor_price', float('inf'))
                    state['profit_floor_price'] = min(existing_floor, candidate_floor)

        try:
            from core.services.exits.profit_exit_telemetry import ProfitExitTelemetry
            
            telemetry_data = {
                'symbol': position.get('symbol', 'UNKNOWN'),
                'side': 'LONG' if sign == 1 else 'SHORT',
                'entry_price': entry,
                'frozen_entry_atr': scale,
                'current_price': price,
                'runtime_peak_price': state['peak_price'],
                'runtime_peak_gain_atr': peak_gain_atr,
                'trend_hold_status': trend_status,
                'trend_hold_reason': trend_reason,
            }
            if isinstance(snapshot, dict):
                telemetry_data.update({
                    'ma3': snapshot.get('ma3'),
                    'last_ma3': snapshot.get('last_ma3'),
                    'ma5': snapshot.get('ma5'),
                    'last_ma5': snapshot.get('last_ma5'),
                })
            
            if peak_gain_atr >= 3.0 and not state.get('telemetry_arm_logged'):
                state['telemetry_arm_logged'] = True
                ProfitExitTelemetry.log_event(position.get('id', 'UNKNOWN'), 'PARABOLIC_ARM_REACHED', telemetry_data)
                
            new_peak = sign*(price-state.get('last_logged_peak_price', entry)) > 0
            if new_peak and peak_gain_atr >= 3.0:
                old_peak_atr = state.get('last_logged_peak_atr', 0)
                if peak_gain_atr - old_peak_atr >= 0.1: # meaningful change
                    state['last_logged_peak_price'] = price
                    state['last_logged_peak_atr'] = peak_gain_atr
                    ProfitExitTelemetry.log_event(position.get('id', 'UNKNOWN'), 'NEW_RUNTIME_PEAK', telemetry_data)
        except Exception:
            pass

        # 高點賣壓即時平倉機制 (Peak Opposing Pressure Exit): 只要有利潤，高點後面出現賣壓/買壓立即平倉，不需等 MA5 進入通道
        drawdown_atr = (state['peak_price'] - price) / scale if (scale > 0 and sign == 1) else (price - state['peak_price']) / scale if scale > 0 else 0.

        is_straight_rocket = (trend_status == 'HOLD' and peak_gain_atr >= 2.0) or (peak_gain_atr >= 3.0)
        if not is_channel_swing and net > 0 and is_straight_rocket:
            # 方案 2 寬鬆大波段階梯回踩門檻（利潤越高，回踩門檻越小）
            if peak_gain_atr >= 3.0:
                pullback_limit_atr = 0.35
            else:
                pullback_limit_atr = 0.40

            # 1. 價格從最高點回踩達動態階梯門檻
            if drawdown_atr >= pullback_limit_atr:
                if not state.get('profit_floor_armed', False):
                    parabolic_reason, parabolic_trigger = PEAK_REASON, 'EXIT_PEAK_PULLBACK_PRESSURE'
        elif not is_channel_swing and peak_gain_atr >= 3.0:
            if drawdown_atr >= 1.0:
                parabolic_reason, parabolic_trigger = PEAK_REASON, 'EXIT_PARABOLIC_PULLBACK_1_ATR'

        reached = lambda v, limit: v >= limit or math.isclose(v,limit,rel_tol=1e-12)

        position.update(peak_price=state['peak_price'], peak_pnl=gain*qty, peak_pnl_usd=gain*qty,
                        peak_net_pnl_usd=state['peak_net_pnl'], peak_gain_atr=gain/scale if scale>0 else 0.,
                        peak_unrealized_profit_usd=gain*qty, current_unrealized_pnl_usd=sign*(price-entry)*qty,
                        current_net_pnl_usd=net)

        initial_stop_enabled = not channel_initial_stop_disabled(position)
        stop = entry-sign*1.5*scale if scale>0 and initial_stop_enabled else 0.
        initial = position.get('initial_sl') if initial_stop_enabled else None
        if positive(initial):
            stop = ((max if sign==1 else min)(stop,float(initial)) if positive(stop) else float(initial))

        if positive(stop):
            position.update(sl=stop,stop_loss=stop,atr_sl=stop,atr_tp=0.,tp=0.)
        reason, trigger = parabolic_reason or ladder_reason, parabolic_trigger or ladder_trigger
        
        pre_veto_reason = reason
        pre_veto_trigger = trigger
        
        try:
            if (parabolic_reason or ladder_reason) and 'telemetry_data' in locals():
                telemetry_data['current_profit_atr'] = gain / scale if scale > 0 else 0.
                if 'drawdown_atr' in locals():
                    telemetry_data['drawdown_atr'] = drawdown_atr
                if not state.get('telemetry_candidate_logged'):
                    telemetry_data['pre_veto_action'] = 'FULL_CLOSE'
                    telemetry_data['pre_veto_reason'] = reason
                    telemetry_data['pre_veto_trigger'] = trigger
                    ProfitExitTelemetry.log_event(position.get('id', 'UNKNOWN'), 'PARABOLIC_CANDIDATE_GENERATED', telemetry_data)
                    state['telemetry_candidate_logged'] = True
        except Exception:
            pass

        if positive(stop) and sign*(price-stop) <= 0:
            reason, trigger = HARD_REASON, 'INITIAL_ATR'
        elif state.get('pending') in (ABNORMAL_REASON, HARD_REASON, PEAK_REASON, 'PROFIT_LOCK_SELL_PRESSURE'):
            reason, trigger = state['pending'], state.get('trigger', 'RETRY')
        else:
            if isinstance(snapshot, dict):
                # The live body's original open and immediately prior closed ATR
                # must belong to this quote's minute; never infer them from wicks.
                bar = math.floor(stamp / 60000) * 60000
                opening = snapshot.get('live_open')
                prior_atr = snapshot.get('atr')
                if (snapshot.get('live_bar_ms') == bar
                        and snapshot.get('closed_bar_ms') == bar - 60000
                        and positive(opening) and positive(prior_atr)):

                    ma15 = snapshot.get('ma15')
                    # MA15 Tracking Defense is DISABLED per user request

                    if not is_channel_swing:
                        e_atr = position.get('entry_atr')
                        mature_evidence = None
                        if e_atr is not None and float(e_atr) > 0:
                            mature_evidence = evaluate_mature_reversal_exit(
                                position, snapshot, state, sign, entry_atr=float(e_atr)
                            )
                        if mature_evidence is not None and reason != HARD_REASON:
                            reason, trigger = ABNORMAL_REASON, mature_evidence['trigger']
                            state.update(mature_evidence)

                        evidence = doji_reversal_evidence(
                            snapshot, price, sign, entry, ident[1]*1000, peak_gain_atr,
                            symbol=position.get('symbol'))
                        if evidence is not None and not mature_evidence and reason != HARD_REASON:
                            reason, trigger = ABNORMAL_REASON, DOJI_TRIGGER
                            state.update(evidence)

            # Model T Profit Floor Hit Evaluation
            floor_reason, floor_trigger = None, None
            if (not is_channel_swing and state.get('profit_floor_armed', False)
                    and 'profit_floor_price' in state):
                floor = state['profit_floor_price']
                if sign == 1 and price <= floor:
                    floor_reason, floor_trigger = ABNORMAL_REASON, 'EXIT_PROFIT_LOCK_FLOOR'
                elif sign == -1 and price >= floor:
                    floor_reason, floor_trigger = ABNORMAL_REASON, 'EXIT_PROFIT_LOCK_FLOOR'

            # Floor overrides soft exits (Doji / MA15 / Ladder)
            if floor_reason:
                reason, trigger = floor_reason, floor_trigger

            # Extreme selling pressure (Waterfall) protection overrides everything including Floor
            if isinstance(snapshot, dict):
                if (snapshot.get('live_bar_ms') == bar
                        and snapshot.get('closed_bar_ms') == bar - 60000
                        and positive(opening) and positive(prior_atr)):
                    live_open = float(opening)
                    entry_bar_ms = math.floor(ident[1] * 1000 / 60000) * 60000
                    if is_channel_swing and bar == entry_bar_ms:
                        live_open = entry
                    body = sign*(live_open-price)
                    if is_channel_swing:
                        from core.config import CHANNEL_WATERFALL_BODY_ATR
                        threshold = CHANNEL_WATERFALL_BODY_ATR * float(prior_atr)
                    else:
                        threshold = ABNORMAL_BODY_ATR * float(prior_atr)

                    if body > 0 and body >= threshold:
                        reason, trigger = ABNORMAL_REASON, 'WATERFALL_DROP'
                        state.update(trigger_bar_ms=bar, trigger_open=live_open,
                                     trigger_atr=float(prior_atr), trigger_price=price)

            if isinstance(snapshot, dict) and 'kc_lower' in snapshot and 'kc_upper' in snapshot:
                kc_lower = float(snapshot.get('kc_lower', 0))
                kc_upper = float(snapshot.get('kc_upper', 0))
                if sign == 1 and kc_lower > 0 and price < kc_lower:
                    reason, trigger = ABNORMAL_REASON, 'OPPOSITE_KC_BAND_BREACH'
                elif sign == -1 and kc_upper > 0 and price > kc_upper:
                    reason, trigger = ABNORMAL_REASON, 'OPPOSITE_KC_BAND_BREACH'

            pivot_only_symbol = position.get('symbol') in PIVOT_ONLY_CHANNEL_SYMBOLS
            if is_channel_swing:
                abnormal_evidence = two_closed_adverse_abnormal_exit(position, snapshot)
                if (abnormal_evidence is not None and reason != HARD_REASON
                        and trigger != 'WATERFALL_DROP'):
                    reason, trigger = ABNORMAL_REASON, abnormal_evidence['trigger']
                    state.update(abnormal_evidence)
                pivot_evidence = three_point_pivot_exit(position, snapshot)
                if (pivot_evidence is not None
                        and channel_pivot_trend_confirmed(position, snapshot)
                        and reason != HARD_REASON
                        and trigger not in ('WATERFALL_DROP', 'TWO_CLOSED_ADVERSE_ABNORMAL')):
                    reason, trigger = ABNORMAL_REASON, pivot_evidence['trigger']
                    state.update(pivot_evidence)
            elif not pivot_only_symbol:
                pivot_evidence = three_point_pivot_exit(position, snapshot)
                if (pivot_evidence is not None and reason != HARD_REASON
                        and trigger not in ('WATERFALL_DROP', 'CHANNEL_PEAK_PULLBACK_REVERSAL')):
                    reason, trigger = ABNORMAL_REASON, pivot_evidence['trigger']
                    state.update(pivot_evidence)
                ma5_snapshot = dict(snapshot) if isinstance(snapshot, dict) else {}
                ma5_snapshot['quote_price'] = price
                ma5_evidence = live_ma5_reversal_exit(position, ma5_snapshot, sign, state)
                if (ma5_evidence is not None and reason != HARD_REASON
                        and trigger not in ('WATERFALL_DROP', 'CHANNEL_PEAK_PULLBACK_REVERSAL')):
                    reason, trigger = ABNORMAL_REASON, ma5_evidence['trigger']
                    state.update(ma5_evidence)

            if (is_channel_swing and reason != HARD_REASON
                    and trigger not in CHANNEL_SWING_EXIT_TRIGGERS):
                reason, trigger = None, None

            live_breakout_side = live_body_breakout_position_side(position)
            if live_breakout_side and reason != HARD_REASON:
                authorized_live_breakout_exit = (
                    trigger in CHANNEL_SWING_EXIT_TRIGGERS
                )
                if not authorized_live_breakout_exit:
                    reason, trigger = None, None

            if (is_channel_swing and reason != HARD_REASON
                    and trigger not in CHANNEL_SWING_EXIT_TRIGGERS):
                reason, trigger = None, None

            # No profit protection: if position currently has no net profit, do not prematurely exit on soft/reversal signals
            if (reason and reason != HARD_REASON
                    and trigger not in ('WATERFALL_DROP', 'TWO_CLOSED_ADVERSE_ABNORMAL',
                                        'KC_OUTER_PIVOT',
                                        'THREE_POINT_PIVOT', 'MA5_TURN_REVERSAL',
                                        'MA5_TRUE_PEAK_REVERSAL',
                                        'CHANNEL_PEAK_PULLBACK_REVERSAL',
                                        'KC_CHANNEL_RETURN')):
                if net <= 0:
                    reason, trigger = None, None

            if reason:
                soft_exit_blocked = False
                peak_exemptions = (
                    'PROFIT_LOCK_T1', 'PROFIT_LOCK_T2', 'PROFIT_LOCK_T3',
                    'NET_ROE_5_PERCENT_1_POINT_GIVEBACK',
                    'WATERFALL_DROP', 'TWO_CLOSED_ADVERSE_ABNORMAL',
                    'EXIT_PROFIT_LOCK_FLOOR', DOJI_TRIGGER,
                    'MATURE_REVERSAL_PINBAR', 'MATURE_REVERSAL_DOJI', 'MATURE_REVERSAL_PINBAR_DOJI',
                    'EXIT_PEAK_PULLBACK_PRESSURE',
                    'EXIT_PARABOLIC_PULLBACK_1_ATR', 'KC_OUTER_PIVOT',
                    'THREE_POINT_PIVOT', 'MA5_TURN_REVERSAL',
                    'MA5_TRUE_PEAK_REVERSAL',
                    'CHANNEL_PEAK_PULLBACK_REVERSAL', 'KC_CHANNEL_RETURN'
                )
                if reason != HARD_REASON and trigger not in peak_exemptions:
                    if trend_status in ('HOLD', 'WARNING', 'UNKNOWN'):
                        soft_exit_blocked = True

                opened_ms = float(position.get('open_timestamp', 0)) * 1000
                if stamp - opened_ms < 120000:
                    allowed_early_triggers = ('PROFIT_LOCK_T1', 'PROFIT_LOCK_T2', 'PROFIT_LOCK_T3', 'INITIAL_ATR', 'WATERFALL_DROP', 'TWO_CLOSED_ADVERSE_ABNORMAL', 'NET_ROE_5_PERCENT_1_POINT_GIVEBACK')
                    if reason != HARD_REASON and trigger not in allowed_early_triggers:
                        soft_exit_blocked = True
                        trend_reason = 'MIN_HOLD_BARS_LOCK'

                import logging
                logger = logging.getLogger('TrendHold')
                sym = position.get('symbol', 'UNKNOWN')
                side_str = 'LONG' if sign == 1 else 'SHORT'
                snap_dict = snapshot if isinstance(snapshot, dict) else {}
                logger.info(
                    f"TREND_HOLD={trend_status} symbol={sym} side={side_str} "
                    f"exit_owner=PeakTrailing exit_reason={trigger} "
                    f"trend_hold_reason={trend_reason} "
                    f"snapshot_bar_id={snap_dict.get('snapshot_bar_id')} "
                    f"live_bar_id={snap_dict.get('live_bar_id')} "
                    f"snapshot_age={snap_dict.get('snapshot_age')} "
                    f"snapshot_source={snap_dict.get('snapshot_source', 'UNKNOWN')} "
                    f"fallback_used={str(snap_dict.get('fallback_used', False)).lower()} "
                    f"soft_exit_blocked={str(soft_exit_blocked).lower()} "
                    f"price={price} MA5={snap_dict.get('ma5')} MA15={snap_dict.get('ma15')} KC_MID={snap_dict.get('kc_middle')}"
                )

                if soft_exit_blocked:
                    try:
                        if pre_veto_reason and 'telemetry_data' in locals():
                            telemetry_data['trend_hold_veto_applied'] = True
                            telemetry_data['post_veto_action'] = None
                            telemetry_data['post_veto_reason'] = None
                            telemetry_data['post_veto_trigger'] = None
                            telemetry_data['final_exit_authorized'] = False
                            ProfitExitTelemetry.log_event(position.get('id', 'UNKNOWN'), 'TREND_HOLD_VETOED', telemetry_data)
                            state['telemetry_candidate_logged'] = False # reset so we log next candidate
                    except Exception:
                        pass
                    reason, trigger = None, None

        if is_channel_swing:
            # Retire all legacy soft authorities; only the two-stage policy can
            # authorize profit-taking. Hard stops and emergency defenses remain.
            emergency = trigger in ('WATERFALL_DROP','TWO_CLOSED_ADVERSE_ABNORMAL','OPPOSITE_KC_BAND_BREACH')
            if reason != HARD_REASON and not emergency:
                reason, trigger = None, None
                for key in ('pending','trigger','trigger_bar_ms','trigger_confirmed_ms'):
                    state.pop(key, None)
            peak = float(state.get('peak_price', price))
            peak_return = sign*(peak-entry)/entry
            state['ratchet_peak_return'] = max(float(state.get('ratchet_peak_return', 0)), peak_return)
            if state['ratchet_peak_return'] + 1e-12 >= .05:
                state['ratchet_armed'] = True
                candidate = peak*(1-sign*.015)
                old_floor = state.get('locked_floor_price')
                state['locked_floor_price'] = ((max if sign == 1 else min)(float(old_floor),candidate)
                                               if positive(old_floor) else candidate)
            held = trend_continuation_hold(position['side'], price, snapshot)
            state.update(soft_exit_blocked=bool(held), trend_hold_reason=held)
            snap = snapshot if isinstance(snapshot, dict) else {}
            ma = snap.get('live_ma5',snap.get('ma5'))
            prior = snap.get('closed_ma5',snap.get('last_ma5'))
            opened = snap.get('live_open',snap.get('open'))
            below_ma = positive(ma) and sign*(price-float(ma)) < 0
            ma_turned = positive(ma) and positive(prior) and sign*(float(ma)-float(prior)) < 0
            adverse_body = positive(opened) and sign*(price-float(opened)) < 0
            drawdown = sign*(peak-price)/peak
            doji_signal = confirmed_doji_reversal(position, snap, price)
            high, low = snap.get('live_high'), snap.get('live_low')
            body = abs(price-float(opened)) if positive(opened) else 0
            wick = (float(high)-max(price,float(opened)) if sign==1 else min(price,float(opened))-float(low)) if all(positive(v) for v in (high,low,opened)) else 0
            wick_pressure = body > 0 and wick > 1.5*body and drawdown > .012
            scale = snap.get('atr',atr)
            volume, baseline = snap.get('live_volume'), snap.get('prior_volume')
            heavy_break = (adverse_body and positive(scale) and body >= 1.2*float(scale)
                           and positive(volume) and positive(baseline) and float(volume) >= 1.5*float(baseline))
            healthy = (drawdown <= .015 and lifeline_held(position['side'],price,snap)
                       and not heavy_break and not doji_signal and not wick_pressure)
            sell_pressure = below_ma or ma_turned or wick_pressure or bool(doji_signal)
            if state.get('ratchet_armed') and doji_signal and reason != HARD_REASON:
                reason = trigger = doji_signal
                state.update(soft_exit_blocked=False, trend_hold_reason='CONFIRMED_DOJI_REVERSAL')
            elif (held or healthy) and reason != HARD_REASON and not heavy_break:
                state.update(soft_exit_blocked=True, trend_hold_reason=held or 'HEALTHY_PULLBACK_HOLD')
                reason, trigger = None, None
                for key in ('pending','trigger','trigger_bar_ms','trigger_confirmed_ms'):
                    state.pop(key,None)
            elif state.get('ratchet_armed') and sell_pressure and reason != HARD_REASON and not emergency:
                reason = trigger = doji_signal or 'PROFIT_LOCK_SELL_PRESSURE'

        if reason:
            try:
                if 'telemetry_data' in locals():
                    telemetry_data['trend_hold_veto_applied'] = False
                    telemetry_data['post_veto_action'] = 'FULL_CLOSE'
                    telemetry_data['post_veto_reason'] = reason
                    telemetry_data['post_veto_trigger'] = trigger
                    telemetry_data['final_exit_authorized'] = True
                    ProfitExitTelemetry.log_event(position.get('id', 'UNKNOWN'), 'FINAL_EXIT_AUTHORIZED', telemetry_data)
            except Exception:
                pass
            state.update(pending=reason,trigger=trigger)
            return dict(action='FULL_CLOSE', type=reason, reason=reason, trigger=trigger, price=price)
        return None
    except Exception as e:
        import traceback
        traceback.print_exc()
        return None
