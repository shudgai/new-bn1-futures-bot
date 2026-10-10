"""Shared signal contract for scan, execution and account revalidation."""
import math

import numpy as np

from core.services.candle_data import closed_entry_candles
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
GOLDEN_CROSS_FAST_LONG_CODE = "KC_GOLDEN_CROSS_FAST_LONG"
BEARISH_INSTANT_BREAKOUT_CODE = "BEARISH_INSTANT_BREAKOUT"
CONTINUATION_CODES = frozenset(('KC_OUTSIDE_LONG', 'KC_OUTSIDE_SHORT'))
LIVE_BODY_BREAKOUT_CODES = frozenset((
    "KC_LIVE_BODY_BREAKOUT_LONG", "KC_LIVE_BODY_BREAKOUT_SHORT",
))
# Continuation is independently revalidated from the current expanding KC candle.
NEW_TRIGGER_CODES = frozenset(("TRIGGER_A_KC_BREAKOUT", "TRIGGER_B_MA_CROSS", "TRIGGER_C_CONTINUATION", BEARISH_INSTANT_BREAKOUT_CODE, "RE_ENTRY_LONG", "RE_ENTRY_SHORT"))
ENTRY_CODES = (KC_PENDING_CODES | LIVE_BODY_BREAKOUT_CODES | CONTINUATION_CODES
               | NEW_TRIGGER_CODES | frozenset((GOLDEN_CROSS_FAST_LONG_CODE,)))
CHOP_FILTER_SYMBOLS = frozenset(("SUI/USDT", "龙虾/USDT", "LOBSTER/USDT"))
CHOP_MA_OVERLAP_ATR = 0.1
CHOP_FLAT_MOVE_ATR = 0.1
CHOP_MA5_RANGE_ATR = 1.0
ENTRY_EVIDENCE_KEYS = (
    "kc_confirmation_edge", "pending_signal_id", "pending_second_bar_id",
    "pending_wait_bars", "pending_max_wait_bars", "breakout_bar_id",
    "pair_confirmation_bar_id", "third_bar_id", "live_pattern_start_bar_id",
    "live_pattern_pullback_bars", "live_pattern_body_atr", "kc_distance_atr",
    "kc_max_distance_atr", "qualification_signal_id", "live_opening_context",
    "reverse_close_id", "live_body_atr", "continuation_entry_bar_id",
    "continuation_entry_bar_low", "continuation_entry_bar_high",
    "post_close_continuation_close_id",
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


def excessive_upper_shadow_problem(frame, quote, side):
    """Reject LONG entries whose live upper shadow exceeds 70% of the body."""
    if side != "LONG":
        return None
    try:
        if frame is None or frame.empty:
            return "BLOCKED_INVALID_ENTRY_CANDLE"
        live = frame.iloc[-1]
        opening = float(live["open"])
        raw_high, raw_low, close = (
            float(live["high"]), float(live["low"]), float(live["close"]),
        )
        high = max(raw_high, float(quote))
        low = min(raw_low, float(quote))
        quote = float(quote)
        if not all(math.isfinite(value) and value > 0 for value in
                   (opening, raw_high, raw_low, close, high, low, quote)):
            return "BLOCKED_INVALID_ENTRY_CANDLE"
        if raw_low > min(opening, float(live["close"])) or raw_high < max(opening, float(live["close"])):
            return "BLOCKED_INVALID_ENTRY_CANDLE"
        close = quote
        body = abs(close - opening)
        upper_wick = high - max(opening, close)
        if upper_wick > 0.7 * body:
            return "BLOCKED_BY_EXCESSIVE_UPPER_SHADOW"
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "BLOCKED_INVALID_ENTRY_CANDLE"


def evaluate_golden_cross_fast_lane(frame, quote, symbol=""):
    """Authorize a live LONG only on a fresh MA5/MA15 cross and strong KC break."""
    try:
        if (frame is None or len(frame) < 2 or "is_closed" not in frame
                or bool(frame.iloc[-1]["is_closed"])):
            return None
        closed = closed_entry_candles(frame)
        if len(closed) < 12:
            return None
        previous = closed.iloc[-1]
        live = frame.iloc[-1]
        stamp = float(live["timestamp"])
        previous_stamp = float(previous["timestamp"])
        opening = float(live["open"])
        quote = float(quote)
        high = max(float(live["high"]), quote)
        low = min(float(live["low"]), quote)
        upper = float(live["kc_upper"])
        lower = float(live["kc_lower"])
        atr = float(previous["atr"])
        live_close = float(live["close"])
        previous_ma5 = float(previous["ma5"])
        previous_ma15 = float(previous["ma15"])
        live_ma5 = float(live["ma5"]) + (quote - live_close) / 5.0
        live_ma15 = float(live["ma15"]) + (quote - live_close) / 15.0
        values = (stamp, previous_stamp, opening, quote, high, low, upper, lower, atr,
                  previous_ma5, previous_ma15, live_ma5, live_ma15)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or stamp != previous_stamp + 60000
                or lower >= upper
                or low > min(opening, quote)
                or high < max(opening, quote)
                or opening > upper
                or quote <= upper
                or previous_ma5 > previous_ma15
                or live_ma5 <= live_ma15):
            return None

        body = quote - opening
        if body < 0.4 * atr:
            return None
        if high - max(opening, quote) > 0.7 * body:
            return None
        return dict(
            action="ENTER", side="LONG", type=GOLDEN_CROSS_FAST_LONG_CODE,
            reason="LIVE_MA5_MA15_GOLDEN_CROSS_KC_BREAKOUT",
            price=quote, entry_atr=atr, confirmation_bar_id=stamp,
            breakout_bar_id=stamp, pair_confirmation_bar_id=previous_stamp,
            exit_bar_id=stamp,
            close_price=quote, intrabar=True, entry_phase="MA_CROSS_FAST_LANE",
            pending_signal_id=f"{symbol}:{GOLDEN_CROSS_FAST_LONG_CODE}:{int(stamp)}",
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


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


def evaluate_post_close_continuation(frame, quote, symbol, account):
    """Authorize one live same-side continuation candle after a confirmed close."""
    try:
        if (frame is None or frame.empty or account is None
                or symbol in getattr(account, 'positions', {})
                or 'is_closed' not in frame.columns
                or bool(frame.iloc[-1]['is_closed'])):
            return None
        closed = closed_entry_candles(frame)
        if closed.empty:
            return None

        relevant_trades = [
            trade for trade in getattr(account, 'trades', [])
            if trade.get('symbol') == symbol
            and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT', 'CLOSE_LONG', 'CLOSE_SHORT')
        ]
        if not relevant_trades:
            return None
        latest_trade = max(
            relevant_trades,
            key=lambda trade: float(trade.get('id') or 0.),
        )
        close_action = latest_trade.get('action')
        if (close_action not in ('CLOSE_LONG', 'CLOSE_SHORT')
                or latest_trade.get('status') != 'CLOSED'):
            return None
        side = 'LONG' if close_action == 'CLOSE_LONG' else 'SHORT'

        close_id = float(latest_trade.get('id') or 0.)
        live = frame.iloc[-1]
        previous = closed.iloc[-1]
        stamp = float(live['timestamp'])
        previous_stamp = float(previous['timestamp'])
        quote = float(quote)
        if (not all(math.isfinite(value) and value > 0
                    for value in (close_id, stamp, previous_stamp, quote))
                or stamp != math.floor(close_id / 60000.0) * 60000.0 + 60000.0
                or previous_stamp != stamp - 60000.0):
            return None

        opening = float(live['open'])
        close = float(live['close'])
        high = max(float(live['high']), quote)
        low = min(float(live['low']), quote)
        ma5 = float(live['ma5']) + (quote - close) / 5.0
        ma15 = float(live['ma15']) + (quote - close) / 15.0
        rail = float(live['kc_upper' if side == 'LONG' else 'kc_lower'])
        atr = float(previous['atr'])
        values = (opening, close, high, low, ma5, ma15, rail, atr)
        if not all(math.isfinite(value) and value > 0 for value in values):
            return None
        if low > min(opening, quote) or high < max(opening, quote):
            return None
        if side == 'LONG':
            if quote <= opening or quote <= ma5 or (quote < rail and quote <= ma15):
                return None
        elif quote >= opening or quote >= ma5 or (quote > rail and quote >= ma15):
            return None

        signal_id = (
            f'{symbol}:TRIGGER_C_CONTINUATION:{int(stamp)}:'
            f'{int(close_id)}:{side}'
        )
        if any(
            trade.get('symbol') == symbol
            and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
            and (trade.get('entry_snapshot') or {}).get('pending_signal_id') == signal_id
            for trade in getattr(account, 'trades', [])
        ):
            return None

        return dict(
            action='ENTER', side=side, type='TRIGGER_C_CONTINUATION',
            reason=('POST_CLOSE_BULLISH_CONTINUATION'
                    if side == 'LONG' else 'POST_CLOSE_BEARISH_CONTINUATION'),
            price=quote, entry_atr=atr, confirmation_bar_id=stamp,
            close_price=quote, intrabar=True, exit_bar_id=stamp,
            entry_phase='POST_CLOSE_CONTINUATION_ENTRY',
            breakout_bar_id=stamp, pair_confirmation_bar_id=previous_stamp,
            third_bar_id=stamp, pending_signal_id=signal_id,
            post_close_continuation_close_id=close_id,
            continuation_entry_bar_id=stamp,
            continuation_entry_bar_low=low,
            continuation_entry_bar_high=high,
            live_opening_context='POST_CLOSE_SAME_SIDE_CONTINUATION',
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def evaluate_post_close_long_continuation(frame, quote, symbol, account):
    """Preserve the LONG-specific entry contract for existing callers."""
    decision = evaluate_post_close_continuation(frame, quote, symbol, account)
    return decision if decision is not None and decision['side'] == 'LONG' else None


def evaluate_continuation_entry(frame, quote, code=None, symbol: str = '', account=None):
    """Re-enter an expanding outer-rail trend on a live directional candle."""
    try:
        if (frame is None or frame.empty or code not in (None, 'TRIGGER_C_CONTINUATION')
                or ('is_closed' in frame.columns and bool(frame.iloc[-1]['is_closed']))):
            return None
        if account is not None and symbol in getattr(account, 'positions', {}):
            return None
        post_close = evaluate_post_close_continuation(
            frame, quote, symbol, account,
        )
        if post_close is not None and code in (None, 'TRIGGER_C_CONTINUATION'):
            return post_close
        closed = closed_entry_candles(frame)
        if closed.empty or float(frame.iloc[-1]['timestamp']) != float(closed.iloc[-1]['timestamp']) + 60000:
            return None
        live = frame.iloc[-1]
        previous = closed.iloc[-1]
        stamp, previous_stamp = float(live['timestamp']), float(previous['timestamp'])
        quote = float(quote)
        opening = float(live['open'])
        close = float(live['close'])
        high = max(float(live['high']), quote)
        low = min(float(live['low']), quote)
        upper, lower = float(live['kc_upper']), float(live['kc_lower'])
        previous_upper, previous_lower = float(previous['kc_upper']), float(previous['kc_lower'])
        ma5 = float(live['ma5']) + (quote - close) / 5.0
        atr = float(previous['atr'])
        values = (stamp, previous_stamp, quote, opening, close, high, low,
                  upper, lower, previous_upper, previous_lower, ma5, atr)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or lower >= upper or previous_lower >= previous_upper
                or low > min(opening, quote) or high < max(opening, quote)):
            return None

        widths = (upper - lower, previous_upper - previous_lower)
        if widths[0] <= widths[1]:
            return None
        if quote > opening:
            side = 'LONG'
        elif quote < opening:
            side = 'SHORT'
        else:
            return None
        current_bar = live.to_dict()
        current_bar.update(close=quote, high=high, low=low, ma5=ma5)
        gate_reason = continuation_entry_problem(
            closed, side, current_bar=current_bar, previous_bar=previous,
            reference_bars=closed.tail(5),
        )
        if gate_reason:
            return None
        qualification = getattr(account, 'breakout_qualification', {}).get(symbol)
        qualification_id = (
            qualification.get('pending_signal_id')
            if qualification and qualification.get('side') == side else None
        )
        signal_id = f'{symbol}:TRIGGER_C_CONTINUATION:{int(stamp)}:{side}'
        return dict(
            action='ENTER', side=side, type='TRIGGER_C_CONTINUATION',
            reason='KC_EXPANSION_OUTER_TREND_CONTINUATION',
            price=quote, entry_atr=atr, confirmation_bar_id=stamp,
            close_price=quote, intrabar=True, exit_bar_id=stamp,
            entry_phase='KC_CONTINUATION_ENTRY', breakout_bar_id=stamp,
            pair_confirmation_bar_id=previous_stamp, third_bar_id=stamp,
            pending_signal_id=signal_id,
            pending_second_bar_id=previous_stamp, pending_wait_bars=1,
            pending_max_wait_bars=1,
            kc_confirmation_edge=upper if side == 'LONG' else lower,
            kc_width=widths[0], kc_width_prev=widths[1],
            continuation_entry_bar_id=stamp,
            continuation_entry_bar_low=low,
            continuation_entry_bar_high=high,
            qualification_signal_id=qualification_id,
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def evaluate_bearish_instant_breakout(frame, quote, symbol: str = '', account=None):
    """Authorize a same-candle SHORT only after a confirmed LONG waterfall close."""
    try:
        if (frame is None or frame.empty or account is None
                or symbol in getattr(account, 'positions', {})
                or ('is_closed' in frame.columns and bool(frame.iloc[-1]['is_closed']))):
            return None
        closed = closed_entry_candles(frame)
        if closed.empty:
            return None
        live, previous = frame.iloc[-1], closed.iloc[-1]
        stamp = float(live['timestamp'])
        previous_stamp = float(previous['timestamp'])
        quote = float(quote)
        if (not math.isfinite(stamp) or stamp != previous_stamp + 60000
                or not math.isfinite(quote) or quote <= 0):
            return None

        close_trade = next((
            trade for trade in getattr(account, 'trades', [])
            if trade.get('symbol') == symbol and trade.get('action') == 'CLOSE_LONG'
        ), None)
        if not close_trade or close_trade.get('status') != 'CLOSED':
            return None
        close_id = float(close_trade.get('id') or 0.)
        close_reason = str(close_trade.get('reason') or '')
        if (not math.isfinite(close_id) or not stamp <= close_id < stamp + 60000
                or not any(trigger in close_reason for trigger in (
                    'BEARISH_INSTANT_BREAKOUT', 'WATERFALL_DROP',
                    'EXIT_DOJI_BEARISH_CONFIRMATION', 'DOJI_REVERSAL_EXIT',
                ))):
            return None

        opening = float(live['open'])
        raw_close = float(live['close'])
        high = max(float(live['high']), quote)
        low = min(float(live['low']), quote)
        upper, lower = float(live['kc_upper']), float(live['kc_lower'])
        middle = float(live['kc_middle'])
        ma5 = float(live['ma5']) + (quote - raw_close) / 5.0
        atr = float(previous['atr'])
        values = (opening, raw_close, high, low, upper, lower, middle, ma5, atr)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or lower >= upper or low > min(opening, quote)
                or high < max(opening, quote)
                or not lower <= opening <= upper
                or opening - quote < 0.5 * atr
                or quote >= lower or quote >= middle or quote >= ma5):
            return None

        signal_id = (
            f'{symbol}:{BEARISH_INSTANT_BREAKOUT_CODE}:'
            f'{int(stamp)}:{int(close_id)}:SHORT'
        )
        return dict(
            action='ENTER', side='SHORT', type=BEARISH_INSTANT_BREAKOUT_CODE,
            reason='LONG_WATERFALL_CLOSED_THEN_BEARISH_KC_BREAKOUT',
            price=quote, entry_atr=atr, confirmation_bar_id=stamp,
            breakout_bar_id=stamp, pair_confirmation_bar_id=previous_stamp,
            close_price=quote, intrabar=True,
            entry_phase='ATOMIC_BEARISH_REVERSE',
            pending_signal_id=signal_id,
            reverse_close_id=int(close_id),
            kc_confirmation_edge=lower,
            live_body_atr=(opening - quote) / atr,
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


def continuation_entry_problem(
    closed_frame, side, *, current_bar=None, previous_bar=None, reference_bars=None,
):
    """Fail closed on weakening MA5, contracting volume, exhaustion, or lost strength."""
    try:
        if side not in ('LONG', 'SHORT') or closed_frame is None or closed_frame.empty:
            return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'
        if current_bar is None:
            if len(closed_frame) < 2:
                return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'
            current_bar = closed_frame.iloc[-1]
            previous_bar = closed_frame.iloc[-2]
            reference_bars = closed_frame.iloc[-6:-1]
        if previous_bar is None or reference_bars is None or len(reference_bars) < 5:
            return 'BLOCKED_BY_LOW_VOLUME'

        current = current_bar.to_dict() if hasattr(current_bar, 'to_dict') else dict(current_bar)
        previous = previous_bar
        opening, high, low, close, ma5, ma15, volume = (
            float(current[key]) for key in
            ('open', 'high', 'low', 'close', 'ma5', 'ma15', 'volume')
        )
        previous_ma5 = float(previous['ma5'])
        previous_values = (opening, high, low, close, ma5, ma15, volume, previous_ma5)
        if (not all(math.isfinite(value) and value > 0 for value in previous_values)
                or not low <= min(opening, close) <= max(opening, close) <= high):
            return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'

        if ((side == 'LONG' and (ma5 <= previous_ma5 or close <= ma5))
                or (side == 'SHORT' and (ma5 >= previous_ma5 or close >= ma5))):
            return 'BLOCKED_BY_MA5_DOWNTURN'

        reference_volume = [float(value) for value in reference_bars['volume']]
        if (not all(math.isfinite(value) and value > 0 for value in reference_volume)
                or volume < sum(reference_volume) / 5.0):
            return 'BLOCKED_BY_LOW_VOLUME'

        body = abs(close - opening)
        wick = high - max(opening, close) if side == 'LONG' else min(opening, close) - low
        if is_doji_candle(current) or wick > body * 0.7:
            return 'BLOCKED_BY_PEAK_EXHAUSTION'

        upper, lower = float(current.get('kc_upper') or 0.), float(current.get('kc_lower') or 0.)
        if side == 'LONG':
            strong = (upper > 0 and close > upper) or ma5 > ma15
        else:
            strong = (lower > 0 and close < lower) or ma5 < ma15
        if not strong:
            return 'BLOCKED_BY_CONTINUATION_STRENGTH'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'


def live_continuation_entry_problem(frame, quote, side):
    """Apply the closed-history volume baseline and live candle values to TRIGGER_C."""
    try:
        closed = closed_entry_candles(frame)
        if closed.empty or frame is None or frame.empty:
            return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'
        live = frame.iloc[-1]
        previous = closed.iloc[-1]
        quote = float(quote)
        opening, raw_close = float(live['open']), float(live['close'])
        current_ma5 = float(live['ma5']) + (quote - raw_close) / 5.0
        current = live.to_dict()
        current.update(
            close=quote,
            high=max(float(live['high']), quote),
            low=min(float(live['low']), quote),
            ma5=current_ma5,
        )
        return continuation_entry_problem(
            closed, side, current_bar=current, previous_bar=previous,
            reference_bars=closed.tail(5),
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'


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

def check_entry_gates(account, symbol, closed_frame, side, trigger_type):
    if len(closed_frame) < 12: return False, "WAIT_ENOUGH_DATA_FOR_GATES"
    curr = closed_frame.iloc[-1]
    prev = closed_frame.iloc[-2]

    if account is not None and symbol in getattr(account, "positions", {}):
        return False, "BLOCKED_BY_POSITION_GATE"

    # ================= 破軌專屬防護 (FRESH & QUALITY GATE) =================
    if trigger_type == "TRIGGER_A_KC_BREAKOUT":
        # FRESH_BREAKOUT_GATE: 防止高位連拉盲目追高
        if side == "LONG" and prev['close'] > prev['kc_upper'] and prev['open'] > prev['kc_upper']:
            return False, "BLOCKED_BY_EXTENDED_BREAKOUT_GATE"
        if side == "SHORT" and prev['close'] < prev['kc_lower'] and prev['open'] < prev['kc_lower']:
            return False, "BLOCKED_BY_EXTENDED_BREAKOUT_GATE"

        # CANDLE_QUALITY_GATE: 防止急漲急跌插針假突破
        candle_range = curr['high'] - curr['low'] + 1e-6
        body = abs(curr['close'] - curr['open'])
        # 放寬實體佔比要求，因為大波動破軌常常伴隨較長影線
        if body / candle_range < 0.35:
            return False, "BLOCKED_BY_WEAK_CANDLE_STRUCTURE"

    # ================= 快車道豁免 =================
    is_fast_lane = trigger_type in ("TRIGGER_A_KC_BREAKOUT", "TRIGGER_C_CONTINUATION", "RE_ENTRY_LONG", "RE_ENTRY_SHORT")
    if is_fast_lane:
        if trigger_type == 'TRIGGER_C_CONTINUATION':
            continuation_problem = continuation_entry_problem(closed_frame, side)
            if continuation_problem:
                return False, continuation_problem
        if is_doji_candle(curr): return False, "BLOCKED_BY_DOJI_GATE"
        if side == "SHORT" and curr['close'] > curr['open']: return False, "BLOCKED_BY_GREEN_CANDLE_GATE"
        if side == "LONG" and curr['close'] < curr['open']: return False, "BLOCKED_BY_RED_CANDLE_GATE"
        return True, "GATE_PASSED_FAST_LANE"

    # ================= 常規進場檢查 (MA_CROSS) =================
    tolerance = curr['atr'] * 0.05
    is_golden_cross_fast = trigger_type == GOLDEN_CROSS_FAST_LONG_CODE
    is_ma_cross = trigger_type == "TRIGGER_B_MA_CROSS" or is_golden_cross_fast
    if side == "LONG":
        if not is_ma_cross and curr['kc_middle'] < prev['kc_middle'] - tolerance: return False, "BLOCKED_BY_BEARISH_KC_SLOPE"
        if curr['ma5'] < curr['ma15'] and not is_golden_cross_fast: return False, "BLOCKED_BY_MA_DIVERGENCE"
    elif side == "SHORT":
        if not is_ma_cross and curr['kc_middle'] > prev['kc_middle'] + tolerance: return False, "BLOCKED_BY_BULLISH_KC_SLOPE"
        if curr['ma5'] > curr['ma15']: return False, "BLOCKED_BY_MA_DIVERGENCE"

    if (curr['kc_upper'] - curr['kc_lower']) / curr['atr'] < 1.2: return False, "BLOCKED_BY_VOLATILITY_GATE"

    past_k = closed_frame.iloc[-4]
    atr_norm = curr['atr'] + 1e-6
    if (abs(curr['ma15'] - past_k['ma15']) / atr_norm < 0.10 and abs(curr['kc_middle'] - past_k['kc_middle']) / atr_norm < 0.10):
        return False, "BLOCKED_BY_FLAT_MARKET_GATE"

    from core.services.strategies.outer_strategy import count_ma_crosses
    # NOTE: Since count_ma_crosses was explicitly defined previously, we just use it directly
    if count_ma_crosses(closed_frame.iloc[-8:]) >= 4: return False, "BLOCKED_BY_WHIPSAW_CHOP_GATE"

    is_trend_bypassed = is_ma_cross
    if not is_trend_bypassed and not entry_trend_alignment_ready(closed_frame, side):
        return False, "BLOCKED_BY_TREND_GATE"

    if is_doji_candle(curr) and not is_golden_cross_fast: return False, "BLOCKED_BY_DOJI_GATE"
    if side == "LONG" and curr['close'] < curr['open'] and not is_golden_cross_fast: return False, "BLOCKED_BY_RED_CANDLE_GATE"
    if side == "SHORT" and curr['close'] > curr['open']: return False, "BLOCKED_BY_GREEN_CANDLE_GATE"

    return True, "GATE_PASSED_STANDARD"

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
        live_cross = evaluate_golden_cross_fast_lane(frame, quote, symbol)
        if live_cross is not None and code in (None, GOLDEN_CROSS_FAST_LONG_CODE):
            passed, gate_reason = check_entry_gates(
                account, symbol, closed, "LONG", GOLDEN_CROSS_FAST_LONG_CODE,
            )
            if not passed:
                return reject(gate_reason)
            if any(
                trade.get("symbol") == symbol
                and trade.get("action") in ("OPEN_LONG", "OPEN_SHORT")
                and (trade.get("entry_snapshot") or {}).get("pending_signal_id")
                    == live_cross["pending_signal_id"]
                for trade in getattr(account, "trades", [])
            ):
                return reject("BLOCKED_KC_BREAKOUT_ALREADY_FILLED")
            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(live_cross)
            return live_cross
        if code == GOLDEN_CROSS_FAST_LONG_CODE:
            return reject("BLOCKED_GOLDEN_CROSS_FAST_LANE_NOT_QUALIFIED")

        instant_reverse = evaluate_bearish_instant_breakout(
            frame, quote, symbol=symbol, account=account,
        )
        if (instant_reverse is not None
                and code in (None, BEARISH_INSTANT_BREAKOUT_CODE)):
            if any(
                trade.get('symbol') == symbol
                and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                and (trade.get('entry_snapshot') or {}).get('pending_signal_id')
                    == instant_reverse['pending_signal_id']
                for trade in getattr(account, 'trades', [])
            ):
                return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')
            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(instant_reverse)
            return instant_reverse
        if code == BEARISH_INSTANT_BREAKOUT_CODE:
            return reject('BLOCKED_BEARISH_INSTANT_BREAKOUT_NOT_CONFIRMED')

        continuation = evaluate_continuation_entry(
            frame, quote,
            code='TRIGGER_C_CONTINUATION' if code == 'TRIGGER_C_CONTINUATION' else None,
            symbol=symbol, account=account,
        )
        if continuation is not None and code in (None, 'TRIGGER_C_CONTINUATION'):
            if any(
                trade.get('symbol') == symbol
                and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                and (trade.get('entry_snapshot') or {}).get('pending_signal_id')
                    == continuation['pending_signal_id']
                for trade in getattr(account, 'trades', [])
            ):
                return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')
            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(continuation)
            return continuation
        if code == 'TRIGGER_C_CONTINUATION':
            try:
                live = frame.iloc[-1]
                side = 'LONG' if float(quote) > float(live['open']) else 'SHORT'
                problem = (
                    live_continuation_entry_problem(frame, quote, side)
                    if not bool(live.get('is_closed', True))
                    else continuation_entry_problem(closed, side)
                )
            except (AttributeError, KeyError, TypeError, ValueError, IndexError):
                problem = None
            return reject(problem or 'BLOCKED_CONTINUATION_NOT_EXPANDING_OR_NOT_DIRECTIONAL')

        side, trigger_type = detect_raw_triggers(closed, account, symbol)
        if side is None: return reject("WAIT_DUAL_TRACK_TRIGGER")

        if (trigger_type == 'TRIGGER_C_CONTINUATION'
                and not bool(frame.iloc[-1].get('is_closed', True))):
            continuation_problem = live_continuation_entry_problem(frame, quote, side)
            if continuation_problem:
                return reject(continuation_problem)

        passed, gate_reason = check_entry_gates(account, symbol, closed, side, trigger_type)
        if not passed: return reject(gate_reason)
            
        stamp = float(closed.iloc[-1].timestamp)
        quote = price if price is not None else float(closed.iloc[-1].close)
        
        decision = dict(
            action='ENTER', side=side, type=trigger_type, reason=gate_reason,
            price=quote, entry_atr=float(closed.iloc[-1]['atr']),
            confirmation_bar_id=stamp, breakout_bar_id=stamp, exit_bar_id=stamp,
            close_price=float(closed.iloc[-1]['close']),
            pair_confirmation_bar_id=None,
            pending_signal_id=f'{symbol}:{trigger_type}:{int(stamp)}:{side}',
            entry_phase='PIPELINE_CONFIRMED',
        )
        if trigger_type == 'TRIGGER_C_CONTINUATION':
            entry_bar = (
                frame.iloc[-1]
                if not bool(frame.iloc[-1].get('is_closed', True))
                else closed.iloc[-1]
            )
            entry_low, entry_high = float(entry_bar['low']), float(entry_bar['high'])
            entry_bar_id = float(entry_bar['timestamp'])
            if not bool(frame.iloc[-1].get('is_closed', True)):
                entry_low, entry_high = min(entry_low, quote), max(entry_high, quote)
            decision.update(
                continuation_entry_bar_id=entry_bar_id,
                continuation_entry_bar_low=entry_low,
                continuation_entry_bar_high=entry_high,
            )
        
        for trade in getattr(account, 'trades', []):
            if (trade.get('symbol') == symbol and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                    and (trade.get('entry_snapshot') or {}).get('pending_signal_id') == decision['pending_signal_id']):
                return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')

        shadow_problem = excessive_upper_shadow_problem(frame, quote, side)
        if shadow_problem:
            return reject(shadow_problem)
                
        if diagnostics is not None: diagnostics.update(decision)
        return decision

    except Exception as e:
        return reject(f"ENTRY_ERROR_{str(e)}")
