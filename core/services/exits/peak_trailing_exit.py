"""Position-bound peak, abnormal-body and independent hard-stop exits."""
import copy
import math
import sys

from core.services.strategies.outer_strategy import ma5_ma15_trend_confirmed

POLICY = 'confirmed_pivot_abnormal_only_v3'
ABNORMAL_BODY_ATR = 1.2
ABNORMAL_REASON = 'EXIT_ADVERSE_ABNORMAL_BODY'
DOJI_TRIGGER = 'DOJI_REVERSAL_EXIT'
DOJI_RULE_VERSION = 4
DOJI_BODY_RATIO = 0.25
DOJI_ADVERSE_BODY_ATR = 0.50
DOJI_REVERSAL_BODY_RATIO = 0.60
STATE_KEY = 'peak_trailing_state'
PEAK_REASON = 'EXIT_REALTIME_PEAK_TRAILING'
HARD_REASON = 'EXIT_INITIAL_ATR_HARD_STOP'
NET_ROE_LOCK_TRIGGER = 'NET_ROE_STAGED_GIVEBACK'
NET_ROE_LOCK_FIRST_PCT = 5.0
NET_ROE_LOCK_STEP_PCT = 4.0
NET_ROE_LOCK_GIVEBACK_PCT = 1.5
LIVE_MA5_BREAKDOWN_TRIGGER = 'LIVE_MA5_BREAKDOWN_EXIT'
LIVE_FLASH_DUMP_TRIGGER = 'LIVE_FLASH_DUMP_EXIT'
LIVE_MA5_BREAKDOWN_ATR = 0.25
LIVE_FLASH_DUMP_ATR = 0.60
V_REVERSAL_SHORT_BODY_ATR = 0.50
V_REVERSAL_SHORT_MA_RECLAIM_TRIGGER = 'V_REVERSAL_SHORT_GREEN_MA_RECLAIM'
V_REVERSAL_SHORT_KC_RECLAIM_TRIGGER = 'V_REVERSAL_SHORT_KC_LOWER_RECLAIM'
CONSECUTIVE_DOJI_STALL_TRIGGER = 'EXIT_CONSECUTIVE_DOJI_STALL'
CONSECUTIVE_DOJI_STALL_BARS = 3
DOJI_STALL_BODY_ATR = 0.25
DOJI_STALL_BODY_RANGE_RATIO = 0.30
CONTINUATION_FAILED_TRIGGER = 'EXIT_CONTINUATION_FAILED'
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
    'WATERFALL_DROP',
    'BEARISH_INSTANT_BREAKOUT',
    'TWO_CLOSED_ADVERSE_ABNORMAL',
    'OPPOSITE_KC_BAND_BREACH',
    'EXIT_DOJI_BEARISH_CONFIRMATION',
    'EXIT_DOJI_BULLISH_CONFIRMATION',
    CONSECUTIVE_DOJI_STALL_TRIGGER,
    LIVE_MA5_BREAKDOWN_TRIGGER,
    LIVE_FLASH_DUMP_TRIGGER,
    CONTINUATION_FAILED_TRIGGER,
})
PIVOT_ONLY_CHANNEL_EXIT_TRIGGERS = frozenset({
    'WATERFALL_DROP',
    'BEARISH_INSTANT_BREAKOUT',
    'TWO_CLOSED_ADVERSE_ABNORMAL',
    'OPPOSITE_KC_BAND_BREACH',
    'EXIT_DOJI_BEARISH_CONFIRMATION',
    'EXIT_DOJI_BULLISH_CONFIRMATION',
    CONSECUTIVE_DOJI_STALL_TRIGGER,
    LIVE_MA5_BREAKDOWN_TRIGGER,
    LIVE_FLASH_DUMP_TRIGGER,
    CONTINUATION_FAILED_TRIGGER,
})
PIVOT_ONLY_CHANNEL_SYMBOLS = frozenset({
    'SUI/USDT', '龙虾/USDT', 'LOBSTER/USDT',
})
DISABLED_CHANNEL_PULLBACK_TRIGGERS = frozenset({
    'EXIT_PEAK_PULLBACK_PRESSURE',
    'EXIT_PARABOLIC_PULLBACK_1_ATR',
    'CHANNEL_PEAK_PULLBACK_REVERSAL',
    'KC_CHANNEL_RETURN',
    'THREE_POINT_PIVOT',
    'EXIT_PROFIT_LOCK_FLOOR',
    NET_ROE_LOCK_TRIGGER,
})

PROFIT_FLOOR_ENABLED = False
LOCK_ARM_ATR = None
TRAILING_DISTANCE_ATR = None

def positive(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def live_intraday_sell_pressure_trigger(position, price, snapshot):
    """Authorize a LONG close from adverse live-tick evidence, without candle finality."""
    try:
        if position.get('side') != 'LONG' or not isinstance(snapshot, dict):
            return None
        quote = float(price)
        atr = float(snapshot.get('atr') or 0.)
        live_ma5 = float(snapshot.get('live_ma5') or 0.)
        live_open = float(snapshot.get('live_open') or 0.)
        live_high = float(snapshot.get('live_high') or 0.)
        if (not all(positive(value) for value in (quote, atr, live_open, live_high))
                or live_high < max(live_open, quote)):
            return None

        bearish_body = live_open - quote
        if (bearish_body >= LIVE_FLASH_DUMP_ATR * atr
                and live_high - quote >= LIVE_FLASH_DUMP_ATR * atr):
            return LIVE_FLASH_DUMP_TRIGGER
        if (positive(live_ma5)
                and quote < live_ma5 - LIVE_MA5_BREAKDOWN_ATR * atr):
            return LIVE_MA5_BREAKDOWN_TRIGGER
        return None
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None


def v_reversal_short_exit_trigger(position, price, snapshot):
    """Fast-cut SHORT on a strong live green reclaim or a return inside KC lower."""
    try:
        if position.get('side') != 'SHORT' or not isinstance(snapshot, dict):
            return None
        quote = float(price)
        opening = float(snapshot.get('live_open') or 0.)
        atr = float(snapshot.get('atr') or 0.)
        live_low = float(snapshot.get('live_low') or 0.)
        lower = float(snapshot.get('live_kc_lower') or 0.)
        live_ma3 = float(snapshot.get('live_ma3') or 0.)
        live_ma5 = float(snapshot.get('live_ma5') or 0.)
        last_close = float(snapshot.get('last_close') or 0.)
        if not all(positive(value) for value in
                   (quote, opening, atr, live_low, lower, last_close)):
            return None
        if quote > lower and (live_low < lower or last_close < lower):
            return V_REVERSAL_SHORT_KC_RECLAIM_TRIGGER
        body = quote - opening
        reclaimed = any(
            positive(ma) and quote > ma and (opening < ma or last_close < ma)
            for ma in (live_ma3, live_ma5)
        )
        if body >= V_REVERSAL_SHORT_BODY_ATR * atr and reclaimed:
            return V_REVERSAL_SHORT_MA_RECLAIM_TRIGGER
        return None
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None
def position_identity(position):
    result = [position['side'], float(position['open_timestamp']),
              float(position['entry_price']), abs(float(position.get('qty', position.get('quantity', 0))))]
    if result[0] not in ('LONG', 'SHORT') or not all(positive(v) for v in result[1:]):
        raise ValueError('Invalid peak-trailing identity')
    return result


def confirmed_doji_reversal(position, snapshot):
    """Confirm a closed weak candle followed by an adverse close through MA5."""
    try:
        bars = snapshot.get('history_5', [])
        if len(bars) < 2:
            return None
        doji, reversal = bars[-2:]
        quote_ms = float(snapshot['quote_ms'])
        live_bar_ms = math.floor(quote_ms / 60000) * 60000
        doji_ms, reversal_ms = float(doji['ms']), float(reversal['ms'])
        if (doji_ms <= float(position['open_timestamp']) * 1000
                or reversal_ms != doji_ms + 60000
                or reversal_ms != float(snapshot.get('snapshot_bar_id', 0))
                or live_bar_ms != reversal_ms + 60000):
            return None
        for bar in (doji, reversal):
            values = [float(bar[key]) for key in ('o', 'h', 'l', 'c')]
            opening, high, low, close = values
            if (not all(math.isfinite(value) and value > 0 for value in values)
                    or not low <= min(opening, close) <= max(opening, close) <= high):
                return None

        doji_span = float(doji['h']) - float(doji['l'])
        doji_body = abs(float(doji['c']) - float(doji['o']))
        doji_ratio = doji_body / doji_span if doji_span > 0 else float('inf')
        if (doji_span <= 0
                or (doji_ratio > DOJI_BODY_RATIO
                    and not math.isclose(doji_ratio, DOJI_BODY_RATIO, rel_tol=1e-12))):
            return None

        sign = 1 if position['side'] == 'LONG' else -1
        reversal_open = float(reversal['o'])
        reversal_close = float(reversal['c'])
        reversal_high = float(reversal['h'])
        reversal_low = float(reversal['l'])
        reversal_body = abs(reversal_close - reversal_open)
        reversal_span = reversal_high - reversal_low
        reference_atr = float(doji.get('atr') or 0.)
        body_atr_confirmed = (
            positive(reference_atr)
            and (reversal_body >= DOJI_ADVERSE_BODY_ATR * reference_atr
                 or math.isclose(
                     reversal_body, DOJI_ADVERSE_BODY_ATR * reference_atr,
                     rel_tol=1e-12,
                 ))
        )
        body_ratio = reversal_body / reversal_span if reversal_span > 0 else 0.
        body_ratio_confirmed = (
            body_ratio >= DOJI_REVERSAL_BODY_RATIO
            or math.isclose(body_ratio, DOJI_REVERSAL_BODY_RATIO, rel_tol=1e-12)
        )
        if not (body_atr_confirmed or body_ratio_confirmed):
            return None

        ma5 = float(reversal.get('ma5') or 0.)
        if (sign * (reversal_close - reversal_open) >= 0
                or not positive(ma5)
                or sign * (reversal_close - ma5) >= 0):
            return None
        outer_key = 'kc_upper' if sign > 0 else 'kc_lower'
        outer_rail = float(reversal.get(outer_key) or 0.)
        if (not positive(outer_rail)
                or (sign > 0 and reversal_close > outer_rail)
                or (sign < 0 and reversal_close < outer_rail)):
            return None
        return ('EXIT_DOJI_BEARISH_CONFIRMATION' if sign > 0
                else 'EXIT_DOJI_BULLISH_CONFIRMATION')
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def consecutive_doji_stall_exit(position, snapshot):
    """Return an exit trigger after three full, consecutive post-entry doji bars."""
    try:
        if not isinstance(snapshot, dict):
            return None
        bars = snapshot.get('history_5')
        if not isinstance(bars, list) or len(bars) < CONSECUTIVE_DOJI_STALL_BARS:
            return None

        quote_ms = float(snapshot['quote_ms'])
        live_bar_ms = math.floor(quote_ms / 60000) * 60000
        closed_bar_ms = float(snapshot['closed_bar_ms'])
        if (snapshot.get('live_bar_ms') != live_bar_ms
                or closed_bar_ms != live_bar_ms - 60000
                or float(snapshot.get('snapshot_bar_id') or 0.) != closed_bar_ms):
            return None

        opened_ms = float(position['open_timestamp']) * 1000
        first_full_bar_ms = math.ceil(opened_ms / 60000) * 60000
        recent = bars[-CONSECUTIVE_DOJI_STALL_BARS:]
        parsed = []
        for bar in recent:
            bar_ms = float(bar['ms'])
            opening, high, low, close = (
                float(bar[key]) for key in ('o', 'h', 'l', 'c')
            )
            atr = float(bar.get('atr') or 0.)
            if (not all(math.isfinite(value) and value > 0
                        for value in (bar_ms, opening, high, low, close))
                    or not low <= min(opening, close) <= max(opening, close) <= high):
                return None
            parsed.append((bar_ms, opening, high, low, close, atr))

        if (parsed[0][0] < first_full_bar_ms
                or parsed[-1][0] != closed_bar_ms
                or any(right[0] - left[0] != 60000
                       for left, right in zip(parsed, parsed[1:]))):
            return None

        for _, opening, high, low, close, atr in parsed:
            body = abs(close - opening)
            span = high - low
            body_atr_doji = (
                positive(atr)
                and (body <= DOJI_STALL_BODY_ATR * atr
                     or math.isclose(
                         body, DOJI_STALL_BODY_ATR * atr, rel_tol=1e-12,
                     ))
            )
            body_ratio = body / span if span > 0 else float('inf')
            body_range_doji = (
                span > 0
                and (body_ratio <= DOJI_STALL_BODY_RANGE_RATIO
                     or math.isclose(
                         body_ratio, DOJI_STALL_BODY_RANGE_RATIO,
                         rel_tol=1e-12,
                     ))
            )
            if not (body_atr_doji or body_range_doji):
                return None

        return CONSECUTIVE_DOJI_STALL_TRIGGER
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def lower_shadow_support_hold(position, snapshot, price):
    """Hold LONGs during a lower-wick rejection that closes above support."""
    try:
        if position.get('side') != 'LONG' or not isinstance(snapshot, dict):
            return False
        opening = float(snapshot.get('live_open') or 0.)
        low = float(snapshot.get('live_low') or 0.)
        close = float(price)
        stamp = float(snapshot.get('quote_ms') or 0.)
        live_bar = float(snapshot.get('live_bar_ms') or 0.)
        ma15 = float(snapshot.get('ma15') or 0.)
        middle = float(snapshot.get('live_kc_middle') or snapshot.get('kc_middle') or 0.)
        if (not all(positive(value) for value in (opening, low, close, stamp, live_bar, ma15, middle))
                or live_bar != math.floor(stamp / 60000) * 60000
                or low > min(opening, close)):
            return False
        body = abs(close - opening)
        lower_shadow = min(opening, close) - low
        return lower_shadow > body and close > ma15 and close > middle
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def upper_shadow_resistance_hold(position, snapshot):
    """Hold SHORTs under KC mid when a completed upper wick rejects higher prices."""
    try:
        if (position.get('side') != 'SHORT' or not isinstance(snapshot, dict)):
            return False
        history = snapshot.get('history_5')
        if not isinstance(history, list) or len(history) < 3:
            return False
        candle = history[-1]
        stamp = float(snapshot.get('snapshot_bar_id') or 0.)
        candle_ms = float(candle.get('ms') or 0.)
        opening, high, low, close = (
            float(candle[key]) for key in ('o', 'h', 'l', 'c')
        )
        middle = float(candle.get('kc_middle') or 0.)
        values = (stamp, candle_ms, opening, high, low, close, middle)
        if (not all(positive(value) for value in values)
                or candle_ms != stamp
                or not low <= min(opening, close) <= max(opening, close) <= high):
            return False

        body = abs(close - opening)
        upper_shadow = high - max(opening, close)
        if upper_shadow <= body or close > middle:
            return False

        ma5_values = [float(bar.get('ma5') or 0.) for bar in history[-3:]]
        if (all(positive(value) for value in ma5_values)
                and ma5_values[0] < ma5_values[1] < ma5_values[2]):
            return False
        return True
    except (AttributeError, TypeError, ValueError, IndexError, OverflowError):
        return False


def kc_outer_hold_reason(position, snapshot):
    """Return a hold lock while the latest completed Channel Swing candle closes outside KC."""
    try:
        if (str(position.get('entry_mode') or '').upper() != 'CHANNEL_SWING'
                or not isinstance(snapshot, dict)):
            return None
        history = snapshot.get('history_5')
        if not isinstance(history, list) or not history:
            return None
        candle = history[-1]
        stamp = float(snapshot.get('snapshot_bar_id') or 0.)
        candle_ms = float(candle.get('ms') or 0.)
        close = float(candle.get('c') or 0.)
        upper = float(candle.get('kc_upper') or 0.)
        lower = float(candle.get('kc_lower') or 0.)
        if (not all(positive(value) for value in (stamp, candle_ms, close, upper, lower))
                or candle_ms != stamp or lower >= upper):
            return None
        if position.get('side') == 'LONG' and close > upper:
            return 'HOLD_OUTSIDE_KC_UPPER'
        if position.get('side') == 'SHORT' and close < lower:
            return 'HOLD_OUTSIDE_KC_LOWER'
        return None
    except (AttributeError, TypeError, ValueError, IndexError, OverflowError):
        return None


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


def continuation_failed_exit(position, snapshot):
    """Authorize the fast fail only on the first completed candle after a C entry."""
    try:
        entry_snapshot = position.get('entry_snapshot') or {}
        if (not isinstance(snapshot, dict)
                or entry_snapshot.get('signal_code') != 'TRIGGER_C_CONTINUATION'):
            return None
        entry_bar_ms = float(entry_snapshot['continuation_entry_bar_id'])
        entry_low = float(entry_snapshot['continuation_entry_bar_low'])
        entry_high = float(entry_snapshot['continuation_entry_bar_high'])
        opened_ms = float(position['open_timestamp']) * 1000.0
        expected_bar_ms = entry_bar_ms + 60000.0
        latest_bar_ms = float(snapshot['snapshot_bar_id'])
        history = snapshot.get('history_5')
        if (not all(math.isfinite(value) and value > 0 for value in
                    (entry_bar_ms, entry_low, entry_high, opened_ms, latest_bar_ms))
                or entry_low > entry_high
                or not entry_bar_ms <= opened_ms < expected_bar_ms
                or latest_bar_ms != expected_bar_ms
                or not isinstance(history, list)):
            return None
        closed = next(
            (bar for bar in history if float(bar.get('ms') or 0.) == expected_bar_ms),
            None,
        )
        if closed is None:
            return None
        opening, high, low, close, ma5 = (
            float(closed[key]) for key in ('o', 'h', 'l', 'c', 'ma5')
        )
        if (not all(math.isfinite(value) and value > 0 for value in
                    (opening, high, low, close, ma5))
                or not low <= min(opening, close) <= max(opening, close) <= high):
            return None

        side = str(position.get('side') or '').upper()
        failed = (
            (side == 'LONG' and (close < ma5 or close < entry_low))
            or (side == 'SHORT' and (close > ma5 or close > entry_high))
        )
        if not failed:
            return None
        return {
            'trigger_bar_ms': expected_bar_ms,
            'trigger_price': close,
            'continuation_entry_bar_ms': entry_bar_ms,
            'continuation_entry_bar_low': entry_low,
            'continuation_entry_bar_high': entry_high,
            'continuation_failure_close': close,
            'continuation_failure_ma5': ma5,
        }
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
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
    # Doji tickets from weaker confirmation rules must be re-evaluated.
    if (state.get('trigger') in (
            DOJI_TRIGGER, 'EXIT_DOJI_BEARISH_CONFIRMATION',
            'EXIT_DOJI_BULLISH_CONFIRMATION',
        )
            and state.get('doji_rule_version') != DOJI_RULE_VERSION):
        for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open', 'trigger_atr', 'trigger_price'):
            state.pop(key, None)
        state.pop('doji_rule_version', None)
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
    # Profit locks and retracement exits are retired. Remove persisted retries
    # as well as arming fields so an old position cannot close on a later tick.
    retired_profit_triggers = DISABLED_CHANNEL_PULLBACK_TRIGGERS
    if state.get('trigger') in retired_profit_triggers:
        for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open',
                    'trigger_atr', 'trigger_price', 'trigger_confirmed_ms'):
            state.pop(key, None)
    for source in (position, meta):
        for key in ('net_roe_peak_pct', 'net_roe_lock_armed',
                    'net_roe_lock_floor_pct', 'profit_floor_armed',
                    'profit_floor_price', 'frozen_lock_arm_atr',
                    'frozen_trailing_distance_atr'):
            source.pop(key, None)
    for key in ('net_roe_peak_pct', 'net_roe_lock_armed',
                'net_roe_lock_floor_pct', 'profit_floor_armed',
                'profit_floor_price', 'frozen_lock_arm_atr',
                'frozen_trailing_distance_atr'):
        state.pop(key, None)
    for source in (position, meta):
        if STATE_KEY in source:
            source[STATE_KEY] = copy.deepcopy(state)
    for source in (position, meta):
        for key in RETIRED_KEYS:
            source.pop(key, None)
    position[STATE_KEY] = state
    return state


def estimated_net_pnl(entry, price, qty, sign, fee, slippage):
    execution = price*(1-sign*slippage)
    return sign*(execution-entry)*qty - (entry+execution)*qty*fee


def estimated_display_net_pnl(entry, price, qty, sign, fee, slippage):
    """Match the net-PnL estimate used by the UI's displayed Net ROE%."""
    raw_pnl = sign * (price - entry) * qty
    return raw_pnl - (entry + price) * qty * fee - price * qty * slippage


def net_roe_lock_floor(peak_net_roe_pct):
    """Retired: profit lock floors are disabled by current user policy."""
    return None


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
        entry_bar_ms = math.floor(opened_ms / 60000) * 60000
        allow_entry_bar_pivot = position.get('symbol') in PIVOT_ONLY_CHANNEL_SYMBOLS
        if (not positive(quote_ms) or quote_ms < snapshot_bar_id
                or quote_ms - snapshot_bar_id > 300000):
            return None
        first_index = len(bars) - 3 if not allow_entry_bar_pivot else 0
        for index in range(len(bars) - 3, first_index - 1, -1):
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
            entry_bar_pivot = allow_entry_bar_pivot and pivot_ms == entry_bar_ms
            latest_confirmed_pivot = snapshot_bar_id == confirm_ms
            pivot_confirmation_is_fresh = quote_ms - confirm_ms <= 300000
            if (not (pivot_ms > opened_ms or entry_bar_pivot)
                    or confirm_ms <= opened_ms
                    or pivot_ms - before_ms != 60000
                    or confirm_ms - pivot_ms != 60000
                    or not (
                        (latest_confirmed_pivot and pivot_confirmation_is_fresh)
                        or entry_bar_pivot
                    )
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
        live_span = max(high, price) - min(low, price)
        live_ratio = abs(price - opening) / live_span if live_span > 0 else 0.
        threshold = DOJI_ADVERSE_BODY_ATR * atr
        body_atr_confirmed = (
            body >= threshold or math.isclose(body, threshold, rel_tol=1e-12)
        )
        body_ratio_confirmed = (
            live_ratio >= DOJI_REVERSAL_BODY_RATIO
            or math.isclose(live_ratio, DOJI_REVERSAL_BODY_RATIO, rel_tol=1e-12)
        )
        if body <= 0 or not (body_atr_confirmed or body_ratio_confirmed):
            return None
        live_ma5 = float(snapshot.get('live_ma5') or snapshot.get('ma5') or 0.)
        outer_key = 'live_kc_upper' if sign > 0 else 'live_kc_lower'
        outer_rail = float(snapshot.get(outer_key) or 0.)
        if (not positive(live_ma5) or not positive(outer_rail)
                or sign * (price - live_ma5) >= 0
                or (sign > 0 and price > outer_rail)
                or (sign < 0 and price < outer_rail)):
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
        pending_at_start = previous.get('pending') in (
            ABNORMAL_REASON, HARD_REASON, PEAK_REASON,
        )
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

        is_channel_swing = channel_initial_stop_disabled(position)
        ladder_reason, ladder_trigger = None, None
        # Net ROE staged lock is retired; pullbacks must not authorize a close.

        # 暴漲逃頂機制 (Parabolic Reversal Exit): 無視 CK 是否衰退
        parabolic_reason, parabolic_trigger = None, None
        peak_gain_atr = gain / scale if scale > 0 else 0.

        pf_enabled = getattr(sys.modules[__name__], 'PROFIT_FLOOR_ENABLED', False)
        pf_arm = getattr(sys.modules[__name__], 'LOCK_ARM_ATR', None)
        pf_trail = getattr(sys.modules[__name__], 'TRAILING_DISTANCE_ATR', None)

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
        # Historical profit-floor and parabolic pullback exits stay disabled.

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
        elif state.get('pending') in (ABNORMAL_REASON, HARD_REASON, PEAK_REASON):
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
                        if (evidence is not None and not mature_evidence
                                and reason != HARD_REASON
                                and not state.get('net_roe_lock_armed')
                                and not state.get('profit_floor_armed')):
                            reason, trigger = ABNORMAL_REASON, DOJI_TRIGGER
                            state.update(evidence)

            # Model T Profit Floor Hit Evaluation
            floor_reason, floor_trigger = None, None

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
                    if is_channel_swing and sign == 1:
                        lower = float(snapshot.get('kc_lower') or 0.)
                        upper = float(snapshot.get('kc_upper') or 0.)
                        middle = float(snapshot.get('kc_middle') or 0.)
                        ma5 = float(snapshot.get('ma5') or 0.)
                        if (positive(lower) and positive(upper) and lower < upper
                                and positive(middle) and positive(ma5)
                                and lower <= live_open <= upper
                                and live_open - price >= 0.5 * float(prior_atr)
                                and price < lower and price < middle and price < ma5):
                            reason, trigger = ABNORMAL_REASON, 'BEARISH_INSTANT_BREAKOUT'
                            state.update(trigger_bar_ms=bar, trigger_open=live_open,
                                         trigger_atr=float(prior_atr), trigger_price=price)

            if isinstance(snapshot, dict) and 'kc_lower' in snapshot and 'kc_upper' in snapshot:
                kc_lower = float(snapshot.get('kc_lower', 0))
                kc_upper = float(snapshot.get('kc_upper', 0))
                if (sign == 1 and kc_lower > 0 and price < kc_lower
                        and trigger != 'BEARISH_INSTANT_BREAKOUT'):
                    reason, trigger = ABNORMAL_REASON, 'OPPOSITE_KC_BAND_BREACH'
                elif sign == -1 and kc_upper > 0 and price > kc_upper:
                    reason, trigger = ABNORMAL_REASON, 'OPPOSITE_KC_BAND_BREACH'

            pivot_only_symbol = position.get('symbol') in PIVOT_ONLY_CHANNEL_SYMBOLS
            if is_channel_swing:
                abnormal_evidence = two_closed_adverse_abnormal_exit(position, snapshot)
                if (abnormal_evidence is not None and reason != HARD_REASON
                        and trigger not in ('WATERFALL_DROP', CONTINUATION_FAILED_TRIGGER)):
                    reason, trigger = ABNORMAL_REASON, abnormal_evidence['trigger']
                    state.update(abnormal_evidence)
            elif not pivot_only_symbol:
                ma5_snapshot = dict(snapshot) if isinstance(snapshot, dict) else {}
                ma5_snapshot['quote_price'] = price
                ma5_evidence = live_ma5_reversal_exit(position, ma5_snapshot, sign, state)
                if (ma5_evidence is not None and reason != HARD_REASON
                        and trigger not in ('WATERFALL_DROP', 'CHANNEL_PEAK_PULLBACK_REVERSAL')):
                    reason, trigger = ABNORMAL_REASON, ma5_evidence['trigger']
                    state.update(ma5_evidence)

            doji_trigger = confirmed_doji_reversal(position, snapshot)
            if (doji_trigger is not None
                    and not state.get('net_roe_lock_armed')
                    and not state.get('profit_floor_armed')):
                reason, trigger = ABNORMAL_REASON, doji_trigger
                state['doji_rule_version'] = DOJI_RULE_VERSION

            continuation_failure = continuation_failed_exit(position, snapshot)
            if continuation_failure is not None and reason != HARD_REASON:
                reason, trigger = ABNORMAL_REASON, CONTINUATION_FAILED_TRIGGER
                state.update(continuation_failure)

            doji_stall_trigger = consecutive_doji_stall_exit(position, snapshot)
            if (doji_stall_trigger is not None
                    and reason != HARD_REASON
                    and trigger not in (
                        'WATERFALL_DROP', 'TWO_CLOSED_ADVERSE_ABNORMAL',
                        'OPPOSITE_KC_BAND_BREACH',
                    )):
                reason, trigger = ABNORMAL_REASON, doji_stall_trigger

            live_sell_pressure = live_intraday_sell_pressure_trigger(
                position, price, snapshot,
            )
            if (live_sell_pressure is not None
                    and trigger not in ('WATERFALL_DROP', 'TWO_CLOSED_ADVERSE_ABNORMAL')):
                reason, trigger = ABNORMAL_REASON, live_sell_pressure
                state.update(
                    trigger_bar_ms=bar,
                    trigger_open=float(opening),
                    trigger_atr=float(prior_atr),
                    trigger_price=price,
                )

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

            # A Channel Swing three-point pullback may close only after its
            # net-ROE profit lock has armed (first activation: 8% peak ROE).
            # Otherwise a pivot must not flatten an unprotected position.
            if trigger in DISABLED_CHANNEL_PULLBACK_TRIGGERS:
                reason, trigger = None, None
                for key in ('pending', 'trigger', 'trigger_bar_ms',
                            'trigger_confirmed_ms', 'trigger_open',
                            'trigger_atr', 'trigger_price'):
                    state.pop(key, None)

            # No profit protection: if position currently has no net profit, do not prematurely exit on soft/reversal signals
            if (reason and reason != HARD_REASON
                    and trigger not in ('WATERFALL_DROP', 'TWO_CLOSED_ADVERSE_ABNORMAL',
                                        'BEARISH_INSTANT_BREAKOUT',
                                        'KC_OUTER_PIVOT',
                                        'THREE_POINT_PIVOT', 'MA5_TURN_REVERSAL',
                                        'MA5_TRUE_PEAK_REVERSAL',
                                        'CHANNEL_PEAK_PULLBACK_REVERSAL',
                                        'KC_CHANNEL_RETURN',
                                        CONTINUATION_FAILED_TRIGGER,
                                        CONSECUTIVE_DOJI_STALL_TRIGGER,
                                        NET_ROE_LOCK_TRIGGER,
                                        'EXIT_DOJI_BEARISH_CONFIRMATION',
                                        'EXIT_DOJI_BULLISH_CONFIRMATION',
                                        LIVE_MA5_BREAKDOWN_TRIGGER,
                                        LIVE_FLASH_DUMP_TRIGGER)):
                if net <= 0:
                    reason, trigger = None, None

            if reason:
                soft_exit_blocked = False
                peak_exemptions = (
                    'WATERFALL_DROP', 'TWO_CLOSED_ADVERSE_ABNORMAL',
                    'BEARISH_INSTANT_BREAKOUT',
                    'EXIT_PROFIT_LOCK_FLOOR', DOJI_TRIGGER,
                    NET_ROE_LOCK_TRIGGER,
                    CONSECUTIVE_DOJI_STALL_TRIGGER,
                    CONTINUATION_FAILED_TRIGGER,
                    'MATURE_REVERSAL_PINBAR', 'MATURE_REVERSAL_DOJI', 'MATURE_REVERSAL_PINBAR_DOJI',
                    'EXIT_PEAK_PULLBACK_PRESSURE',
                    'EXIT_PARABOLIC_PULLBACK_1_ATR', 'KC_OUTER_PIVOT',
                    'THREE_POINT_PIVOT', 'MA5_TURN_REVERSAL',
                    'MA5_TRUE_PEAK_REVERSAL',
                    'CHANNEL_PEAK_PULLBACK_REVERSAL', 'KC_CHANNEL_RETURN',
                    'EXIT_DOJI_BEARISH_CONFIRMATION',
                    'EXIT_DOJI_BULLISH_CONFIRMATION',
                    LIVE_MA5_BREAKDOWN_TRIGGER, LIVE_FLASH_DUMP_TRIGGER,
                )
                if reason != HARD_REASON and trigger not in peak_exemptions:
                    if trend_status in ('HOLD', 'WARNING', 'UNKNOWN'):
                        soft_exit_blocked = True

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

        outer_hold_reason = kc_outer_hold_reason(position, snapshot)
        emergency_triggers = {
            'WATERFALL_DROP', 'TWO_CLOSED_ADVERSE_ABNORMAL',
            'OPPOSITE_KC_BAND_BREACH', 'BEARISH_INSTANT_BREAKOUT',
            CONTINUATION_FAILED_TRIGGER, NET_ROE_LOCK_TRIGGER,
            CONSECUTIVE_DOJI_STALL_TRIGGER,
            'EXIT_PROFIT_LOCK_FLOOR',
            LIVE_MA5_BREAKDOWN_TRIGGER, LIVE_FLASH_DUMP_TRIGGER,
        }
        if outer_hold_reason and reason != HARD_REASON and trigger not in emergency_triggers:
            reason = trigger = None
            state.update(
                soft_exit_blocked=True,
                trend_hold_reason=outer_hold_reason,
                kc_outer_hold_lock=outer_hold_reason,
            )
            if state.get('pending') in (ABNORMAL_REASON, PEAK_REASON):
                pending_trigger = state.get('trigger')
                if pending_trigger not in emergency_triggers:
                    for key in ('pending', 'trigger', 'trigger_bar_ms',
                                'trigger_confirmed_ms', 'trigger_open',
                                'trigger_atr', 'trigger_price'):
                        state.pop(key, None)
        else:
            state.pop('kc_outer_hold_lock', None)

        if (reason is not None and reason != HARD_REASON
                and trigger not in emergency_triggers
                and upper_shadow_resistance_hold(position, snapshot)):
            reason = trigger = None
            state.update(
                soft_exit_blocked=True,
                trend_hold_reason='HOLD_ON_UPPER_SHADOW_RESISTANCE',
                upper_shadow_resistance_hold=True,
            )
            if state.get('pending') in (ABNORMAL_REASON, PEAK_REASON):
                pending_trigger = state.get('trigger')
                if pending_trigger not in emergency_triggers:
                    for key in ('pending', 'trigger', 'trigger_bar_ms',
                                'trigger_confirmed_ms', 'trigger_open',
                                'trigger_atr', 'trigger_price'):
                        state.pop(key, None)
        else:
            state.pop('upper_shadow_resistance_hold', None)

        if (reason is not None and reason != HARD_REASON and not pending_at_start
                and trigger not in (
                    'WATERFALL_DROP', 'TWO_CLOSED_ADVERSE_ABNORMAL',
                    'BEARISH_INSTANT_BREAKOUT',
                    'OPPOSITE_KC_BAND_BREACH',
                    CONTINUATION_FAILED_TRIGGER,
                    CONSECUTIVE_DOJI_STALL_TRIGGER,
                    NET_ROE_LOCK_TRIGGER, 'EXIT_PROFIT_LOCK_FLOOR',
                    LIVE_MA5_BREAKDOWN_TRIGGER, LIVE_FLASH_DUMP_TRIGGER,
                )
                and lower_shadow_support_hold(position, snapshot, price)):
            reason, trigger = None, None

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
