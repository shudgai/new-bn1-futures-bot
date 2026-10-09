"""Shared signal contract for scan, execution and account revalidation."""
import math

import numpy as np

from core.services.candle_data import closed_entry_candles
from core.services.strict_entry_gates import validate_strict_entry
from core.services.impulse_breakout import CODES as IMPULSE_CODES, impulse_entry, reverse_receipt
from core.services.post_profit_lock_gate import post_profit_lock_reason
from core.services.second_third_entry import CODES as SECOND_THIRD_CODES, evaluate_second_third
from core.services.strategies.outer_strategy import (
    LIVE_BREAKOUT_BODY_ATR,
    LIVE_BREAKOUT_MAX_DISTANCE_ATR,
    ck_direction,
    live_body_breakout_side,
    ma5_ma15_trend_confirmed,
    live_ma3_direction_ready,
    live_candle_color_ready,
    live_adverse_entry_safe,
    ma3_outer_continuation_ready,
    outer_body_breakout_side,
)
from core.services.kc_pending_entry import (
    KC_PENDING_CODES,
    evaluate_kc_pending_entry,
)

LONG_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_LONG"
SHORT_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_SHORT"
CONTINUATION_CODES = frozenset(('KC_OUTSIDE_LONG', 'KC_OUTSIDE_SHORT'))
LIVE_BODY_BREAKOUT_CODES = frozenset((
    "KC_LIVE_BODY_BREAKOUT_LONG", "KC_LIVE_BODY_BREAKOUT_SHORT",
))
# Continuation requires a persisted, previously observed outer-rail breakout.
NEW_TRIGGER_CODES = frozenset(("TRIGGER_A_KC_BREAKOUT", "TRIGGER_B_MA_CROSS", "TRIGGER_C_CONTINUATION", "RE_ENTRY_LONG", "RE_ENTRY_SHORT"))
ENTRY_CODES = IMPULSE_CODES | SECOND_THIRD_CODES | KC_PENDING_CODES | LIVE_BODY_BREAKOUT_CODES | CONTINUATION_CODES | NEW_TRIGGER_CODES
CHOP_FILTER_SYMBOLS = frozenset(("SUI/USDT", "CAP/USDT", "龙虾/USDT", "LOBSTER/USDT"))
CHOP_MA_OVERLAP_ATR = 0.1
CHOP_FLAT_MOVE_ATR = 0.1
CHOP_MA5_RANGE_ATR = 1.0
ENTRY_EVIDENCE_KEYS = (
    "strict_gate_evidence", "reverse_close_trade_id",
    "kc_confirmation_edge", "pending_signal_id", "pending_second_bar_id",
    "pending_wait_bars", "pending_max_wait_bars", "breakout_bar_id",
    "pair_confirmation_bar_id", "third_bar_id", "live_pattern_start_bar_id",
    "live_pattern_pullback_bars", "live_pattern_body_atr", "kc_distance_atr",
    "kc_max_distance_atr", "qualification_signal_id", "live_opening_context",
)


def quote_beyond_side_outer_rail(frame, side, quote):
    """Fail closed unless the quote is strictly beyond its own KC entry rail."""
    try:
        if side not in ("LONG", "SHORT") or frame is None or frame.empty:
            return False
        lower = float(frame.iloc[-1]["kc_lower"])
        upper = float(frame.iloc[-1]["kc_upper"])
        quote = float(quote)
        if not all(math.isfinite(value) and value > 0 for value in (lower, upper, quote)):
            return False
        if lower >= upper:
            return False
        return quote > upper if side == "LONG" else quote < lower
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def evaluate_live_ma5_direction(frame, quote, side):
    """Require the quote-adjusted MA5 to move strictly in the entry direction."""
    try:
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 2:
            return None
        previous, latest = frame.iloc[-2], frame.iloc[-1]
        previous_ma5 = float(previous["ma5"])
        latest_ma5 = float(latest["ma5"])
        latest_close = float(latest["close"])
        quote = float(quote)
        values = (previous_ma5, latest_ma5, latest_close, quote)
        if not all(math.isfinite(value) and value > 0 for value in values):
            return None
        live_ma5 = latest_ma5 + (quote - latest_close) / 5.0
        if not math.isfinite(live_ma5):
            return None
        if (side == "LONG" and live_ma5 <= previous_ma5) or (
            side == "SHORT" and live_ma5 >= previous_ma5
        ):
            return None
        return [previous_ma5, live_ma5]
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def evaluate_continuation_entry(frame, quote, code=None, symbol: str = '', account=None):
    """Continue only a qualified breakout while live trend and price stay outside."""
    try:
        side = ck_direction(frame)
        if not side:
            return None
        signal = 'KC_OUTSIDE_' + side
        if code is not None and code != signal:
            return None

        qualification = getattr(account, 'breakout_qualification', {}).get(symbol)
        if not qualification or qualification.get('side') != side:
            return None
        live = frame.iloc[-1]
        stamp = float(live['timestamp'])
        qualified_bar = float(qualification['breakout_bar_id'])
        quote = float(quote)
        atr = float(frame.iloc[-2]['atr'])
        if (not all(math.isfinite(value) and value > 0
                    for value in (stamp, qualified_bar, quote, atr))
                or stamp <= qualified_bar):
            return None

        sign = 1 if side == 'LONG' else -1
        opening = float(live['open'])
        if sign * (quote - opening) < LIVE_BREAKOUT_BODY_ATR * atr:
            return None
            
        # 第三根 (Live Bar) 十字星防護
        high = max(float(live["high"]), quote)
        low = min(float(live["low"]), quote)
        curr_range = high - low
        curr_body = abs(quote - opening)
        if curr_range > 0 and (curr_body / curr_range < 0.50):
            return None
            
        closes = [float(value) for value in frame['close'].iloc[-5:-1]]
        last_ma5 = float(frame.iloc[-2]['ma5'])
        if (len(closes) != 4 or not all(math.isfinite(value) and value > 0 for value in closes)
                or not math.isfinite(last_ma5) or last_ma5 <= 0):
            return None
        live_ma5 = (sum(closes) + quote) / 5.0
        if sign * (live_ma5 - last_ma5) < 0.01 * atr:
            return None
        if (not live_candle_color_ready(frame, quote, side)
                or not live_adverse_entry_safe(frame, quote, side)
                or not live_ma3_direction_ready(frame, quote, side)
                or not ma3_outer_continuation_ready(frame, quote, side)):
            return None

        edge = float(live['kc_upper' if side == 'LONG' else 'kc_lower'])
        distance = sign * (quote - edge) / atr
        if (distance <= 0 or distance > LIVE_BREAKOUT_MAX_DISTANCE_ATR
                or sign * (quote - live_ma5) <= 0):
            return None
        previous_stamp = float(frame.iloc[-2]['timestamp'])
        return dict(
            action='ENTER', side=side, type=signal, reason=signal,
            price=quote, entry_atr=atr, confirmation_bar_id=stamp,
            close_price=float(frame.iloc[-2]['close']), intrabar=True,
            entry_phase='KC_CONTINUATION_ENTRY', breakout_bar_id=stamp,
            pair_confirmation_bar_id=previous_stamp, third_bar_id=stamp,
            pending_signal_id=f'{symbol}:CONTINUATION:{int(stamp)}:{side}',
            pending_second_bar_id=previous_stamp, pending_wait_bars=1,
            pending_max_wait_bars=1, kc_confirmation_edge=edge,
            kc_distance_atr=distance,
            kc_max_distance_atr=LIVE_BREAKOUT_MAX_DISTANCE_ATR,
            qualification_signal_id=qualification['pending_signal_id'],
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None

DOJI_BODY_RATIO = 0.10


def is_entry_doji(row, quote=None):
    """Strictly below 10% doji boundary; no standalone long-wick veto."""
    try:
        opening = float(row.open)
        closing = float(row.close if quote is None else quote)
        high, low = float(row.high), float(row.low)
        if not all(math.isfinite(v) and v > 0 for v in (opening, closing, high, low)):
            return True
        high, low = max(high, closing), min(low, closing)
        body, span = abs(closing-opening), high-low
        if span <= 0:
            return True
        threshold = DOJI_BODY_RATIO*span
        return body < threshold and not math.isclose(body, threshold, rel_tol=1e-12)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return True


def entry_doji_problem(closed, live, quote):
    # 無論漲勢或跌勢，只要出現十字線（走到後面時）就不要再開倉
    if is_entry_doji(live, quote):
        return 'BLOCKED_LIVE_DOJI'
    bars = [row for _, row in closed.tail(2).iterrows()]
    for row in bars:
        if is_entry_doji(row):
            return 'BLOCKED_CLOSED_DOJI'
    return None


def is_solid_push(row, side):
    """Require a finite directional body, independent of wick length."""
    try:
        opening, closing = float(row.open), float(row.close)
        if not all(math.isfinite(v) and v > 0 for v in (opening, closing)):
            return False
        if side == 'LONG':
            return closing > opening
        if side == 'SHORT':
            return closing < opening
        return False
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def evaluate_closed_outer_body_breakout(frame, quote, symbol="", requested_side=None):
    """Allow one fresh live bar to enter from the immediately preceding outer-body break."""
    try:
        if frame is None or len(frame) < 3 or 'is_closed' not in frame:
            return None
        previous, breakout, live = frame.iloc[-3], frame.iloc[-2], frame.iloc[-1]
        if not (bool(previous['is_closed']) and bool(breakout['is_closed'])
                and not bool(live['is_closed'])):
            return None

        breakout_stamp = float(breakout['timestamp'])
        live_stamp = float(live['timestamp'])
        atr = float(previous['atr'])
        if (not all(math.isfinite(value) and value > 0
                    for value in (breakout_stamp, live_stamp, atr))
                or live_stamp != breakout_stamp + 60000):
            return None
        side = outer_body_breakout_side(
            breakout['open'], breakout['close'], breakout['kc_lower'],
            breakout['kc_middle'], breakout['kc_upper'], atr,
        )
        if side is None or (requested_side is not None and requested_side != side):
            return None
        quote = float(quote)
        if not quote_beyond_side_outer_rail(frame, side, quote):
            return None

        sign = 1 if side == 'LONG' else -1
        edge = float(live['kc_upper' if side == 'LONG' else 'kc_lower'])
        distance = sign * (quote - edge) / atr
        if (not math.isfinite(distance) or distance <= 0
                or distance > LIVE_BREAKOUT_MAX_DISTANCE_ATR):
            return None

        code = f"KC_LIVE_BODY_BREAKOUT_{side}"
        return dict(
            action="ENTER", side=side, type=code, reason=code,
            price=quote, entry_atr=atr, confirmation_bar_id=breakout_stamp,
            close_price=float(breakout['close']), intrabar=False,
            entry_phase='KC_LIVE_OUTER_BREAKOUT',
            breakout_bar_id=breakout_stamp, pair_confirmation_bar_id=None,
            third_bar_id=live_stamp, live_opening_context='CLOSED_OUTER_FORMATION',
            pending_signal_id=f"{symbol}:CLOSED_LIVE_BODY:{int(breakout_stamp)}:{side}",
            pending_second_bar_id=live_stamp, pending_wait_bars=0,
            pending_max_wait_bars=0, kc_confirmation_edge=edge,
            kc_distance_atr=distance,
            kc_max_distance_atr=LIVE_BREAKOUT_MAX_DISTANCE_ATR,
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def evaluate_live_body_breakout(frame, quote, symbol="", requested_side=None):
    """Authorize a first live breakout or same-side outer continuation."""
    try:
        if frame is None or len(frame) < 2:
            return None
        live = frame.iloc[-1]
        opened = float(live["open"])
        lower, upper = float(live["kc_lower"]), float(live["kc_upper"])
        quote = float(quote)
        if (not all(math.isfinite(value) and value > 0
                    for value in (opened, lower, upper, quote))
                or lower >= upper):
            return None

        # --- 第三根 (Live Bar) 十字星與反向 K 防護 ---
        high = max(float(live["high"]), quote)
        low = min(float(live["low"]), quote)
        curr_range = high - low
        curr_body = abs(quote - opened)
        
        # 十字星防護：實體不到波幅 50% 不開倉
        if curr_range > 0 and (curr_body / curr_range < 0.50):
            return None

        side = live_body_breakout_side(frame, quote)
        if side is None:
            ret = evaluate_closed_outer_body_breakout(
                frame, quote, symbol=symbol, requested_side=requested_side
            )
            if ret is None:
                return None
            side = ret['side']
            # 當由 evaluate_closed_outer_body_breakout 通過時，反向 K 防護：做多必須是紅 K(漲)，做空必須是綠 K(跌)
            if side == 'LONG' and quote <= opened:
                return None
            if side == 'SHORT' and quote >= opened:
                return None
            return ret

        # 當由 live_body_breakout_side 通過時，反向 K 防護
        if side == 'LONG' and quote <= opened:
            return None
        if side == 'SHORT' and quote >= opened:
            return None

        if requested_side is not None and requested_side != side:
            return None
        stamp = float(live["timestamp"])
        atr = float(frame.iloc[-2]["atr"])
        edge = upper if side == "LONG" else lower
        distance = (quote - edge) / atr if side == "LONG" else (edge - quote) / atr
        if (not math.isfinite(stamp) or stamp <= 0 or not math.isfinite(distance)
                or distance <= 0 or distance > LIVE_BREAKOUT_MAX_DISTANCE_ATR):
            return None

        code = f"KC_LIVE_BODY_BREAKOUT_{side}"
        opening_context = (
            'IN_CHANNEL' if lower <= opened <= upper else 'SAME_SIDE_OUTER'
        )
        return dict(
            action="ENTER", side=side, type=code, reason=code,
            price=quote, entry_atr=atr, confirmation_bar_id=stamp,
            close_price=float(frame.iloc[-2]["close"]), intrabar=True,
            entry_phase='KC_LIVE_OUTER_BREAKOUT',
            breakout_bar_id=stamp, pair_confirmation_bar_id=None,
            third_bar_id=stamp,
            live_opening_context=opening_context,
            pending_signal_id=f"{symbol}:LIVE_BODY:{int(stamp)}:{side}",
            pending_second_bar_id=stamp, pending_wait_bars=0,
            pending_max_wait_bars=0, kc_confirmation_edge=edge,
            kc_distance_atr=distance,
            kc_max_distance_atr=LIVE_BREAKOUT_MAX_DISTANCE_ATR,
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def entry_trend_alignment_ready(frame, side):
    """Require closed KC, MA5 and MA15 trends to agree with every entry side."""
    try:
        if side not in ("LONG", "SHORT") or ck_direction(frame) != side:
            return False
        closed = closed_entry_candles(frame)
        if len(closed) < 3 or not {"ma5", "ma15"}.issubset(closed.columns):
            return False
        recent = closed.tail(3)
        return ma5_ma15_trend_confirmed(recent["ma5"], recent["ma15"], side)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def entry_consolidation_problem(frame, quote, *, allow_directional_breakout=False):
    """Reject MA overlap, jointly flat MA15/KC, or unconfirmed MA5 oscillation."""
    try:
        closed = closed_entry_candles(frame)
        required = {"ma5", "ma15", "kc_middle", "close", "atr"}
        if len(closed) < 6 or not required.issubset(closed.columns):
            return "WAIT_CONSOLIDATION_GATE_DATA"
        latest = closed.iloc[-1]
        ma5, ma15, close, atr = (
            float(latest[key]) for key in ("ma5", "ma15", "close", "atr")
        )
        quote = float(quote)
        if not all(math.isfinite(value) and value > 0 for value in
                   (ma5, ma15, close, atr, quote)):
            return "WAIT_CONSOLIDATION_GATE_DATA"
        live_ma5 = ma5 + (quote - close) / 5.0
        live_ma15 = ma15 + (quote - close) / 15.0
        if not all(math.isfinite(value) and value > 0 for value in (live_ma5, live_ma15)):
            return "WAIT_CONSOLIDATION_GATE_DATA"
        if abs(live_ma5 - live_ma15) <= CHOP_MA_OVERLAP_ATR * atr:
            return "BLOCKED_MA5_MA15_OVERLAP"

        recent = closed.tail(6)
        ma5_values = [float(value) for value in recent["ma5"]]
        ma15_values = [float(value) for value in recent["ma15"]]
        kc_values = [float(value) for value in recent["kc_middle"]]
        if not all(math.isfinite(value) and value > 0
                   for value in ma5_values + ma15_values + kc_values):
            return "WAIT_CONSOLIDATION_GATE_DATA"

        if (abs(ma15_values[-1] - ma15_values[0]) <= CHOP_FLAT_MOVE_ATR * atr
                and abs(kc_values[-1] - kc_values[0]) <= CHOP_FLAT_MOVE_ATR * atr):
            return "BLOCKED_FLAT_MA15_KC"

        changes = [right - left for left, right in zip(ma5_values, ma5_values[1:])]
        directions = [1 if change > 0 else -1 if change < 0 else 0 for change in changes]
        nonzero_directions = [direction for direction in directions if direction]
        reversals = sum(
            previous != current
            for previous, current in zip(nonzero_directions, nonzero_directions[1:])
        )
        if (not allow_directional_breakout and reversals >= 2
                and max(ma5_values) - min(ma5_values) <= CHOP_MA5_RANGE_ATR * atr):
            return "BLOCKED_MA5_REGULAR_OSCILLATION"
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "WAIT_CONSOLIDATION_GATE_DATA"



def is_doji_candle(row, ratio_threshold=0.2):
    total_range = row['high'] - row['low']
    if total_range == 0: return True
    return (abs(row['close'] - row['open']) / total_range) <= ratio_threshold

def count_ma_crosses(frame):
    if len(frame) < 2: return 0
    ma5 = frame['ma5'].values
    ma15 = frame['ma15'].values
    diff = ma5 - ma15
    crosses = 0
    for i in range(1, len(diff)):
        if (diff[i-1] > 0 and diff[i] <= 0) or (diff[i-1] < 0 and diff[i] >= 0):
            crosses += 1
    return crosses

def evaluate_reentry_triggers(closed_frame, last_exit_side=None, bars_since_exit=None):
    if len(closed_frame) < 3: return None, None
    if not last_exit_side: return None, "NO_HISTORY"
    if bars_since_exit is not None and bars_since_exit < 2: return None, "WAIT_REENTRY_COOLDOWN"

    curr = closed_frame.iloc[-1]
    prev = closed_frame.iloc[-2]

    if last_exit_side == "SHORT":
        if (curr['close'] < curr['kc_middle']) and (curr['ma15'] <= prev['ma15']):
            if (curr['close'] < curr['open']) and (curr['close'] < curr['ma5'] or curr['close'] < prev['low']):
                return "SHORT", "RE_ENTRY_SHORT"

    if last_exit_side == "LONG":
        if (curr['close'] > curr['kc_middle']) and (curr['ma15'] >= prev['ma15']):
            if (curr['close'] > curr['open']) and (curr['close'] > curr['ma5'] or curr['close'] > prev['high']):
                return "LONG", "RE_ENTRY_LONG"

    return None, "NO_REENTRY_SIGNAL"

def detect_raw_triggers(closed_frame, account=None, symbol=None):
    if len(closed_frame) < 3: return None, None
    prev, curr = closed_frame.iloc[-2], closed_frame.iloc[-1]

    if account is not None and symbol is not None:
        last_trade = None
        for trade in reversed(getattr(account, 'trades', [])):
            if trade.get('symbol') == symbol and trade.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT'):
                last_trade = trade
                break
        if last_trade:
            re_side, re_trigger = evaluate_reentry_triggers(closed_frame, last_exit_side=last_trade['side'], bars_since_exit=None)
            if re_side is not None:
                return re_side, re_trigger

    atr = float(curr['atr'])

    # 情境 A: 軌內起爆衝擊 + 軌外標準破軌 (TRIGGER_A_KC_BREAKOUT)
    classic_breakout_long = (curr['close'] > curr['kc_upper'] and curr['close'] > curr['open'])
    impulsive_breakout_long = (
        curr['open'] < curr['kc_upper'] and 
        curr['high'] >= curr['kc_upper'] and 
        curr['close'] > curr['kc_middle'] and 
        curr['close'] > curr['open'] and 
        (curr['close'] - curr['open']) >= atr * 0.5 and 
        (curr['high'] - curr['close']) <= (curr['close'] - curr['open']) * 0.8
    )
    long_breakout = classic_breakout_long or impulsive_breakout_long
    
    classic_breakout_short = (curr['close'] < curr['kc_lower'] and curr['close'] < curr['open'])
    impulsive_breakout_short = (
        curr['open'] > curr['kc_lower'] and 
        curr['low'] <= curr['kc_lower'] and 
        curr['close'] < curr['kc_middle'] and 
        curr['close'] < curr['open'] and 
        (curr['open'] - curr['close']) >= atr * 0.5 and 
        (curr['close'] - curr['low']) <= (curr['open'] - curr['close']) * 0.8
    )
    short_breakout = classic_breakout_short or impulsive_breakout_short

    # 情境 B: 軌外順勢追車 (TRIGGER_C_CONTINUATION)
    long_cont = (
        curr['close'] > curr['kc_upper'] and 
        curr['close'] > curr['ma5'] and 
        curr['ma5'] >= prev['ma5'] and 
        curr['close'] > curr['open']
    )
    
    short_cont = (
        curr['close'] < curr['kc_lower'] and 
        curr['close'] < curr['ma5'] and 
        curr['ma5'] <= prev['ma5'] and 
        curr['close'] < curr['open']
    )

    golden_cross = prev['ma5'] <= prev['ma15'] and curr['ma5'] > curr['ma15']
    death_cross = prev['ma5'] >= prev['ma15'] and curr['ma5'] < curr['ma15']
    long_ma_cross = golden_cross and curr['close'] > curr['kc_middle'] and curr['close'] > curr['open']
    short_ma_cross = death_cross and curr['close'] < curr['kc_middle'] and curr['close'] < curr['open']

    if long_breakout: return "LONG", "TRIGGER_A_KC_BREAKOUT"
    if short_breakout: return "SHORT", "TRIGGER_A_KC_BREAKOUT"
    if long_cont: return "LONG", "TRIGGER_C_CONTINUATION"
    if short_cont: return "SHORT", "TRIGGER_C_CONTINUATION"
    if long_ma_cross: return "LONG", "TRIGGER_B_MA_CROSS"
    if short_ma_cross: return "SHORT", "TRIGGER_B_MA_CROSS"

    return None, None

def ma_momentum_reason(frame, side, quote=None):
    """Reject adverse MA5 slope, correcting the forming SMA for the latest quote."""
    try:
        curr, prev = frame.iloc[-1], frame.iloc[-2]
        current, previous = float(curr['ma5']), float(prev['ma5'])
        if quote is not None:
            current += (float(quote) - float(curr['close'])) / 5
        if not np.isfinite(current) or not np.isfinite(previous) or min(current, previous) <= 0:
            return 'BLOCKED_BY_INVALID_MA5'
        if side == 'LONG' and current < previous:
            return 'BLOCKED_BY_FALLING_MA5'
        if side == 'SHORT' and current > previous:
            return 'BLOCKED_BY_RISING_MA5'
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        return 'BLOCKED_BY_INVALID_MA5'
    return None


def check_entry_gates(account, symbol, closed_frame, side, trigger_type, *, live_frame=None, quote=None):
    """Account prerequisites; live strict gates run for every trigger below."""
    momentum = ma_momentum_reason(closed_frame if live_frame is None else live_frame, side, quote)
    if momentum:
        return False, momentum
    post_reason = post_profit_lock_reason(account, symbol, closed_frame, side)
    if post_reason:
        return False, post_reason
    if len(closed_frame) < 60:
        return False, 'BLOCKED_STRICT_INSUFFICIENT_DATA'
    if account is not None and symbol in getattr(account, 'positions', {}):
        return False, 'BLOCKED_BY_POSITION_GATE'
    return True, 'STRICT_ENTRY_GATES_PASSED'

def evaluate_entry_contract(frame, price=None, code=None, *, account=None, symbol="", diagnostics=None):
    def reject(reason):
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics["reason"] = reason
        return None
        
    reject("WAIT_VALID_ENTRY_DATA")
    if code is not None and code not in ENTRY_CODES:
        return reject("BLOCKED_OBSOLETE_ENTRY_SIGNAL")
        
    try:
        if frame is None or frame.empty or frame.attrs.get('timeframe_ms', 60000) != 60000:
            return None
            
        closed = closed_entry_candles(frame)
        if len(closed) < 3: return reject('WAIT_ENOUGH_CLOSED_CANDLES')
            
        quote = price if price is not None else float(frame.iloc[-1].close)
        continuation = evaluate_second_third(frame, quote, account, symbol)
        if continuation is not None and (code is None or code == continuation['type']):
            momentum = ma_momentum_reason(frame, continuation['side'], quote)
            if momentum:
                return reject(momentum)
            post_reason = post_profit_lock_reason(account, symbol, closed, continuation['side'])
            if post_reason:
                return reject(post_reason)
            if diagnostics is not None:
                diagnostics.update(continuation)
            return continuation
        if code in SECOND_THIRD_CODES:
            return reject('BLOCKED_SECOND_THIRD_OUTSIDE_OR_DOJI')

        impulse = impulse_entry(frame, quote, symbol)
        if impulse is not None and (code is None or code == impulse['type']):
            if symbol in getattr(account, 'positions', {}):
                return reject('BLOCKED_BY_POSITION_GATE')
            momentum = ma_momentum_reason(frame, impulse['side'], quote)
            if momentum:
                return reject(momentum)
            post_reason = post_profit_lock_reason(account, symbol, closed, impulse['side'])
            if post_reason:
                return reject(post_reason)
            receipt = reverse_receipt(account, symbol, impulse)
            if receipt is not None:
                impulse['reverse_close_trade_id'] = receipt['id']
                impulse['pending_signal_id'] += ':reverse:' + str(receipt['id'])
            if any(t.get('symbol') == symbol and t.get('action','').startswith('OPEN_')
                   and (t.get('entry_snapshot') or {}).get('pending_signal_id') == impulse['pending_signal_id']
                   for t in getattr(account, 'trades', [])):
                return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')
            if diagnostics is not None:
                diagnostics.update(impulse)
            return impulse
        if code in IMPULSE_CODES:
            return reject('BLOCKED_IMPULSE_REVALIDATION')

        side, trigger_type = detect_raw_triggers(closed, account, symbol)
        if side is None: return reject("WAIT_DUAL_TRACK_TRIGGER")
        if code is not None and code != trigger_type:
            return reject("BLOCKED_STRICT_SIGNAL_CHANGED")

        passed, gate_reason = check_entry_gates(account, symbol, closed, side, trigger_type, live_frame=frame, quote=quote)
        if not passed: return reject(gate_reason)
            
        stamp = float(closed.iloc[-1].timestamp)
        quote = price if price is not None else float(frame.iloc[-1].close)
        passed, gate_reason, gate_evidence = validate_strict_entry(frame, quote, side)
        if not passed:
            reject(gate_reason)
            if diagnostics is not None:
                diagnostics['strict_gate_evidence'] = gate_evidence
            return None
        
        decision = dict(
            action='ENTER', side=side, type=trigger_type, reason=gate_reason,
            strict_gate_evidence=gate_evidence,
            price=quote, entry_atr=float(closed.iloc[-1]['atr']),
            confirmation_bar_id=stamp, breakout_bar_id=stamp,
            close_price=float(closed.iloc[-1]['close']),
            pair_confirmation_bar_id=stamp,
            pending_signal_id=f'{symbol}:{trigger_type}:{int(stamp)}:{side}',
            entry_phase='PIPELINE_CONFIRMED',
        )
        
        for trade in getattr(account, 'trades', []):
            if (trade.get('symbol') == symbol and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                    and (trade.get('entry_snapshot') or {}).get('pending_signal_id') == decision['pending_signal_id']):
                return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')
                
        if diagnostics is not None: diagnostics.update(decision)
        return decision

    except Exception as e:
        return reject(f"ENTRY_ERROR_{str(e)}")

