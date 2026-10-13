"""Shared signal contract for scan, execution and account revalidation."""
import math
import time

import numpy as np
from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT

from core.gates.pipeline import (
    FLAT_LINE_BREAKOUT_TYPES, PIPELINE_ENTRY_TYPES,
    MOMENTUM_BURST_CODES, MOMENTUM_BURST_LONG_CODE,
    MOMENTUM_BURST_SHORT_CODE, entry_market_structure_problem,
    entry_strategy_type, fast_cross_evidence, has_consecutive_momentum_breakout,
    is_meme_breakout_symbol, long_large_bearish_candle_problem,
    ma5_ma15_cross_chop_problem, pipeline, strict_kc_entry_problem,
)
from core.symbol_aliases import (  # noqa: F401
    CHOP_FILTER_SYMBOLS,
    is_chop_filter_symbol,
)
from core.services.candle_data import closed_entry_candles
from core.services.strategies.unified_entry_strategy import (
    anti_bottom_short_problem,
    validate_ma_alignment,
    wick_exhaustion_problem,
)
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
    KC_REALTIME_PATTERN_CODES,
    evaluate_kc_pending_entry,
    evaluate_kc_live_pattern_entry,
)
from core.services.ma5_entry_slope import (
    live_ma5_entry_direction_problem,
    live_ma5_slope_problem,
    live_ma5_slope_atr,
)
from core.services.three_bar_rail_gate import three_bar_rail_gate_problem
from core.services.exits.dual_track_exit_service import (
    detect_climax_reversal, is_true_climax_peak, is_true_climax_valley,
)

LONG_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_LONG"
SHORT_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_SHORT"
GOLDEN_CROSS_FAST_LONG_CODE = "KC_GOLDEN_CROSS_FAST_LONG"
DEATH_CROSS_FAST_SHORT_CODE = "KC_DEATH_CROSS_FAST_SHORT"
# Backwards-compatible public name used by entry calibration callers.
MA_CROSS_FAST_LONG_CODE = GOLDEN_CROSS_FAST_LONG_CODE
BEARISH_INSTANT_BREAKOUT_CODE = "BEARISH_INSTANT_BREAKOUT"
BULLISH_INSTANT_BREAKOUT_CODE = "BULLISH_INSTANT_BREAKOUT"
CLIMAX_REVERSAL_FLIP_CODE = "CLIMAX_REVERSAL_FLIP"
TOP_WATERFALL_FLIP_CODE = "AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT"
SHADOW_RETEST_ENTRY_CODE = "AUTHORIZED_SHADOW_RETEST_ENTRY"
AUTHORIZED_SHADOW_RETEST_ENTRY_CODE = "AUTHORIZED_SHADOW_RETEST_ENTRY"
REALTIME_RAIL_BREACH_SHORT_CODE = "KC_REALTIME_RAIL_BREACH_SHORT"
CONTINUATION_CODES = frozenset(('KC_OUTSIDE_LONG', 'KC_OUTSIDE_SHORT'))
LIVE_BODY_BREAKOUT_CODES = frozenset((
    "KC_LIVE_BODY_BREAKOUT_LONG", "KC_LIVE_BODY_BREAKOUT_SHORT",
))
LIVE_PATTERN_BREAKOUT_CODES = KC_REALTIME_PATTERN_CODES
# Continuation is independently revalidated from the current expanding KC candle.
NEW_TRIGGER_CODES = frozenset((
    "TRIGGER_A_KC_BREAKOUT", "TRIGGER_C_CONTINUATION",
    TOP_WATERFALL_FLIP_CODE, "TOP_WATERFALL_FLIP",
))
# Automatic entries are limited to the general outer-rail breakout, its
# qualified post-close continuation, and the atomic top-waterfall flip. The
# firewall imports this set and revalidates continuation with the same rail gate.
LEGACY_ROUTE_CODES = frozenset((
    "TRIGGER_A_KC_BREAKOUT",
    "TRIGGER_C_CONTINUATION",
    TOP_WATERFALL_FLIP_CODE,
    "TOP_WATERFALL_FLIP",
))
from core.gates.meme_coin_gate import MEME_ENTRY_CODES

ENTRY_CODES = NEW_TRIGGER_CODES | MEME_ENTRY_CODES
CHOP_MA_OVERLAP_ATR = 0.1
CHOP_FLAT_MOVE_ATR = 0.1
CHOP_MA5_RANGE_ATR = 1.0
ENTRY_EVIDENCE_KEYS = (
    "kc_confirmation_edge", "pending_signal_id", "pending_second_bar_id",
    "pending_wait_bars", "pending_max_wait_bars", "breakout_bar_id",
    "pair_confirmation_bar_id", "third_bar_id", "live_pattern_start_bar_id",
    "live_pattern_pullback_bars", "live_pattern_body_atr", "kc_distance_atr",
    "kc_max_distance_atr", "qualification_signal_id", "live_opening_context",
    "reverse_close_id", "reverse_close_reason", "reverse_body_atr",
    "reverse_body_ratio", "live_body_atr", "continuation_entry_bar_id",
    "continuation_entry_bar_low", "continuation_entry_bar_high",
    "post_close_continuation_close_id",
    "post_close_continuation_bar_id", "post_close_bars_after_close",
    "post_close_strong_impulse", "post_close_opportunity",
    "climax_flip_evidence", "climax_flip_close_id",
    "realtime_body_atr", "realtime_distance_atr",
    "realtime_lower_shadow_ratio", "previous_lower_shadow_ratio",
    "strategy_type", "body_atr", "body_ratio", "midline_distance_atr",
    "upper_rail_space_ratio", "lower_rail_space_ratio",
    "live_ma5", "live_ma15",
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


def long_top_chase_problem(frame, quote):
    """Reject an extended LONG or a stalled retest of a recent swing high."""
    try:
        closed = closed_entry_candles(frame)
        if closed.empty:
            return 'BLOCKED_LONG_TOP_CHASE_INVALID_DATA'
        quote = float(quote)
        live = frame.iloc[-1]
        live_close = float(live.get('close', quote))
        live_ma5 = float(live['ma5']) + (quote - live_close) / 5.0
        if not all(math.isfinite(v) and v > 0 for v in (quote, live_ma5)):
            return 'BLOCKED_LONG_TOP_CHASE_INVALID_DATA'
        if (quote - live_ma5) / live_ma5 > 0.012:
            return 'BLOCKED_LONG_MA5_DEVIATION'

        recent = closed.tail(6)
        if len(recent) < 5:
            return None
        prior_high = float(recent.head(3)['high'].max())
        current_high = float(recent.tail(3)['high'].max())
        atr = float(recent.iloc[-1]['atr'])
        ma5 = [float(v) for v in recent['ma5']]
        closes = [float(v) for v in recent['close']]
        if not all(math.isfinite(v) and v > 0 for v in
                   (prior_high, current_high, atr, *ma5, *closes)):
            return 'BLOCKED_LONG_TOP_CHASE_INVALID_DATA'
        # Recent-high congestion: several bars press the swing high without
        # making progress while MA5 has flattened or rolled over.
        at_high = closes[-1] >= prior_high - 0.35 * atr
        stalled = current_high <= prior_high + 1e-12
        flat_or_falling = ma5[-1] <= ma5[-2] or abs(ma5[-1] - ma5[-2]) <= 0.02 * atr
        if at_high and stalled and flat_or_falling:
            return 'BLOCKED_LONG_PRIOR_HIGH_STALL'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_LONG_TOP_CHASE_INVALID_DATA'


def long_post_impulse_reentry_problem(frame, quote):
    """Require a support retest or a volume-confirmed high break after a surge."""
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 7:
            return None
        recent = closed.tail(8).reset_index(drop=True)
        quote = float(quote)
        impulses = []
        for index in range(max(0, len(recent) - 6), len(recent) - 1):
            bar = recent.iloc[index]
            opening, close, atr = float(bar['open']), float(bar['close']), float(bar['atr'])
            prior_atr = float(recent.iloc[index - 1]['atr']) if index > 0 else atr
            if (all(math.isfinite(v) and v > 0 for v in (opening, close, atr, prior_atr))
                    and close > opening and close - opening >= 1.25 * prior_atr):
                impulses.append((index, float(bar['high']), prior_atr))
        if not impulses:
            return None
        impulse_index, impulse_high, impulse_atr = impulses[-1]
        after = recent.iloc[impulse_index + 1:]
        if after.empty:
            return None

        support_retest = False
        touched = False
        for _, bar in after.iterrows():
            low, close = float(bar['low']), float(bar['close'])
            ma5, ma15 = float(bar['ma5']), float(bar['ma15'])
            atr = float(bar['atr'])
            if not all(math.isfinite(v) and v > 0 for v in (low, close, ma5, ma15, atr)):
                return 'BLOCKED_LONG_POST_IMPULSE_INVALID_DATA'
            if low <= max(ma5, ma15) + 0.10 * atr:
                touched = True
            if touched and close >= ma5:
                support_retest = True
        if support_retest:
            return None

        live = frame.iloc[-1]
        live_volume = float(live.get('volume', live.get('vol', 0.0)) or 0.0)
        volume_col = 'volume' if 'volume' in frame.columns else 'vol' if 'vol' in frame.columns else None
        prior_volumes = (
            [float(v) for v in closed.tail(6)[volume_col]] if volume_col else []
        )
        valid_volumes = [v for v in prior_volumes if math.isfinite(v) and v > 0]
        volume_breakout = (
            quote > impulse_high and live_volume > 0 and valid_volumes
            and live_volume >= 1.5 * (sum(valid_volumes) / len(valid_volumes))
        )
        if volume_breakout:
            return None

        latest_close = float(after.iloc[-1]['close'])
        near_high = (
            max(latest_close, quote) >= impulse_high - 0.40 * impulse_atr
            and quote <= impulse_high + 0.25 * impulse_atr
        )
        narrow = float(after['high'].max() - after['low'].min()) <= 1.25 * impulse_atr
        if near_high and narrow:
            return 'BLOCKED_LONG_POST_IMPULSE_NO_RETEST_OR_VOLUME_BREAKOUT'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_LONG_POST_IMPULSE_INVALID_DATA'


def same_bar_close_entry_problem(account, symbol, frame, side):
    """Block same-side re-entry only when close time maps to the current candle."""
    try:
        if account is None or not symbol or side not in ('LONG', 'SHORT'):
            return None
        last_closed_at = getattr(account, 'last_closed_at', {})
        if symbol not in last_closed_at:
            return None
        closed_at = float(last_closed_at[symbol])
        if closed_at <= 0:
            return None
        if not math.isfinite(closed_at):
            return 'BLOCKED_INVALID_LAST_CLOSE_TIMESTAMP'
        last_side = str(
            getattr(account, 'last_closed_side', {}).get(symbol, ''),
        ).upper()
        if last_side and last_side != side:
            return None
        if frame is None or frame.empty:
            return 'BLOCKED_CURRENT_BAR_ID_INVALID'
        bar_timestamp = float(frame.iloc[-1]['timestamp'])
        timeframe_ms = float(frame.attrs.get('timeframe_ms', 60000))
        if (not math.isfinite(bar_timestamp) or bar_timestamp <= 0
                or not math.isfinite(timeframe_ms) or timeframe_ms <= 0):
            return 'BLOCKED_CURRENT_BAR_ID_INVALID'
        bar_start = math.floor(bar_timestamp / timeframe_ms) * timeframe_ms / 1000.0
        if bar_start <= closed_at < bar_start + timeframe_ms / 1000.0:
            return 'BLOCKED_SAME_BAR_REENTRY'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_CURRENT_BAR_ID_INVALID'


def same_side_anti_churn_problem(account, symbol, frame, side, quote=None):
    """Block same-side re-entry until price escapes half an ATR from its close."""
    try:
        if (account is None or not symbol or side not in ('LONG', 'SHORT')
                or str(getattr(account, 'last_closed_side', {}).get(symbol, '')).upper() != side):
            return None
        close_at = float(getattr(account, 'last_closed_at', {}).get(symbol, 0.0))
        if not math.isfinite(close_at) or close_at <= 0:
            return 'BLOCKED_SAME_SIDE_CLOSE_INVALID_MARKET_DATA'
        if frame is None or frame.empty:
            return 'BLOCKED_CURRENT_BAR_ID_INVALID'
        timeframe_ms = float(frame.attrs.get('timeframe_ms', 60_000))
        if not math.isfinite(timeframe_ms) or timeframe_ms != 60_000:
            return 'BLOCKED_CURRENT_BAR_ID_INVALID'
        close_action = 'CLOSE_LONG' if side == 'LONG' else 'CLOSE_SHORT'
        matching_closes = []
        for trade in getattr(account, 'trades', []):
            if (trade.get('symbol') != symbol
                    or trade.get('action') not in ('CLOSE_LONG', 'CLOSE_SHORT')
                    or trade.get('status') != 'CLOSED'):
                continue
            closed_at_ms = float(trade.get('id') or 0.0)
            if math.isfinite(closed_at_ms) and closed_at_ms > 0:
                matching_closes.append((closed_at_ms, trade))
        if not matching_closes:
            return 'BLOCKED_SAME_SIDE_CLOSE_INVALID_MARKET_DATA'
        _, last_close = max(matching_closes, key=lambda item: item[0])
        if last_close.get('action') != close_action:
            return 'BLOCKED_SAME_SIDE_CLOSE_INVALID_MARKET_DATA'

        closed = closed_entry_candles(frame)
        if closed.empty or 'atr' not in closed:
            return 'BLOCKED_SAME_SIDE_CLOSE_INVALID_MARKET_DATA'
        atr = float(closed.iloc[-1]['atr'])
        current_quote = float(
            quote if quote is not None else frame.iloc[-1].get('close', float('nan'))
        )
        close_price = float(last_close.get('price') or 0.0)
        if (not all(math.isfinite(value) and value > 0
                    for value in (atr, current_quote, close_price))):
            return 'BLOCKED_SAME_SIDE_CLOSE_INVALID_MARKET_DATA'
        distance = abs(current_quote - close_price)
        within_close_range = (
            distance < 0.5 * atr
            or math.isclose(distance, 0.5 * atr, rel_tol=1e-12, abs_tol=0.0)
        )
        return (
            'BLOCKED_SAME_SIDE_CLOSE_PRICE_REENTRY'
            if within_close_range else None
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_SAME_SIDE_CLOSE_INVALID_MARKET_DATA'


def opposite_side_close_cooldown_problem(
    account, symbol, frame, side, cooldown_bars=3,
):
    """Block opposite-side entries until three full bar identities have elapsed."""
    try:
        if (account is None or not symbol or side not in ('LONG', 'SHORT')
                or cooldown_bars < 1):
            return None
        last_closed_at = getattr(account, 'last_closed_at', {})
        if symbol not in last_closed_at:
            return None
        last_side = str(
            getattr(account, 'last_closed_side', {}).get(symbol, ''),
        ).upper()
        if not last_side or last_side == side:
            return None
        if last_side not in ('LONG', 'SHORT'):
            return 'BLOCKED_INVALID_LAST_CLOSE_SIDE'
        closed_at = float(last_closed_at[symbol])
        if not math.isfinite(closed_at) or closed_at <= 0:
            return 'BLOCKED_INVALID_LAST_CLOSE_TIMESTAMP'
        if frame is None or frame.empty:
            return 'BLOCKED_CURRENT_BAR_ID_INVALID'
        timeframe_ms = float(frame.attrs.get('timeframe_ms', 60000))
        current_timestamp = float(frame.iloc[-1]['timestamp'])
        if (not math.isfinite(timeframe_ms) or timeframe_ms <= 0
                or not math.isfinite(current_timestamp) or current_timestamp <= 0):
            return 'BLOCKED_CURRENT_BAR_ID_INVALID'
        close_bar_id = math.floor(closed_at * 1000.0 / timeframe_ms)
        current_bar_id = math.floor(current_timestamp / timeframe_ms)
        bars_since_close = current_bar_id - close_bar_id
        if bars_since_close < 0:
            return 'BLOCKED_CURRENT_BAR_ID_INVALID'
        if bars_since_close < cooldown_bars:
            return 'BLOCKED_OPPOSITE_SIDE_COOLDOWN'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_CURRENT_BAR_ID_INVALID'


def excessive_upper_shadow_problem(frame, quote, side, trigger_type=None):
    """Reject LONG entries whose latest completed candle has an excessive wick."""
    if side != "LONG":
        return None
    try:
        closed = closed_entry_candles(frame)
        if closed.empty:
            return "BLOCKED_INVALID_ENTRY_CANDLE"
        quote = float(quote)
        if not math.isfinite(quote) or quote <= 0:
            return "BLOCKED_INVALID_ENTRY_CANDLE"
        candle = closed.iloc[-1]
        opening, high, low, close = (
            float(candle["open"]), float(candle["high"]),
            float(candle["low"]), float(candle["close"]),
        )
        if not all(math.isfinite(value) and value > 0 for value in
                   (opening, high, low, close)):
            return "BLOCKED_INVALID_ENTRY_CANDLE"
        if low > min(opening, close) or high < max(opening, close):
            return "BLOCKED_INVALID_ENTRY_CANDLE"
        body = abs(close - opening)
        upper_wick = high - max(opening, close)
        max_wick_body_ratio = 0.7
        breakout_fast_lanes = {
            "TRIGGER_A_KC_BREAKOUT", GOLDEN_CROSS_FAST_LONG_CODE,
        }
        if trigger_type in breakout_fast_lanes and len(closed) >= 3:
            trend = closed.iloc[-3:]
            try:
                middle = [float(value) for value in trend["kc_middle"]]
                opens = [float(value) for value in trend.iloc[-2:]["open"]]
                closes = [float(value) for value in trend.iloc[-2:]["close"]]
                ma5 = [float(value) for value in trend.iloc[-2:]["ma5"]]
            except (KeyError, TypeError, ValueError):
                middle, opens, closes, ma5 = [], [], [], []
            trend_values = middle + opens + closes + ma5
            if (len(middle) == 3 and len(opens) == 2
                    and all(math.isfinite(value) for value in trend_values)
                    and middle[0] < middle[1] < middle[2]
                    and all(close > opening and close > average
                            for opening, close, average
                            in zip(opens, closes, ma5))):
                max_wick_body_ratio = 1.0
        if upper_wick > max_wick_body_ratio * body:
            return "BLOCKED_BY_EXCESSIVE_UPPER_SHADOW"
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "BLOCKED_INVALID_ENTRY_CANDLE"


def entry_candle_shape_problem(frame, quote=None, decision=None):
    """Validate candle shape only for bars that authorize the current entry."""
    try:
        if frame is None or frame.empty:
            return "BLOCKED_INVALID_ENTRY_CANDLE"
        if decision and (
            decision.get("type") in MEME_ENTRY_CODES
            or decision.get("entry_phase") in (
                "MEME_BREAKOUT_ENTRY", "MEME_AMBUSH_ENTRY", "MEME_PYRAMID_ADDITION"
            )
        ):
            return None
        live = frame.iloc[-1]
        phase = decision.get("entry_phase") if decision else None
        if phase == "KC_MIDLINE_RETEST":
            bar_ids = (
                decision.get("breakout_bar_id"),
                decision.get("confirmation_bar_id"),
            )
        elif phase == "KC_2BAR_CLOSED_CONFIRM":
            # Only the two closed candles authorize a confirmed rail breakout.
            # The live candle is checked by quote-vs-rail gates, not its shape.
            bar_ids = (
                decision.get("breakout_bar_id"),
                decision.get("pair_confirmation_bar_id"),
            )
        elif decision and decision.get("intrabar"):
            bar_ids = (decision.get("confirmation_bar_id"),)
        elif decision and decision.get("breakout_bar_id") is not None:
            pair_id = decision.get("pair_confirmation_bar_id")
            bar_ids = (decision.get("breakout_bar_id"), pair_id) if pair_id is not None else (decision.get("breakout_bar_id"),)
        else:
            bar_ids = (live.get("timestamp"),)

        timestamps = frame["timestamp"]
        candles = []
        for bar_id in dict.fromkeys(bar_ids):
            if bar_id is None:
                return "BLOCKED_INVALID_ENTRY_CANDLE"
            matches = frame.loc[timestamps == bar_id]
            if matches.empty:
                return "BLOCKED_INVALID_ENTRY_CANDLE"
            candles.append(matches.iloc[-1])

        for candle in candles:
            try:
                is_live = (
                    candle.name == live.name
                    and not bool(live.get("is_closed", False))
                )
            except (TypeError, ValueError):
                is_live = False
            opening = float(candle["open"])
            raw_close = float(candle["close"])
            raw_high = float(candle["high"])
            raw_low = float(candle["low"])
            if (not all(math.isfinite(value) and value > 0
                        for value in (opening, raw_close, raw_high, raw_low))
                    or raw_low > min(opening, raw_close)
                    or raw_high < max(opening, raw_close)
                    or raw_high <= raw_low):
                return "BLOCKED_INVALID_ENTRY_CANDLE"
            close = float(quote) if is_live and quote is not None else raw_close
            high = max(raw_high, opening, close)
            low = min(raw_low, opening, close)
            min_body_ratio = 0.35
            if (is_live and decision
                    and decision.get('side') == 'SHORT'
                    and decision.get('type') == REALTIME_RAIL_BREACH_SHORT_CODE):
                try:
                    previous_atr = float(frame.iloc[-2]['atr'])
                    lower_rail = float(candle['kc_lower'])
                    if (previous_atr > 0 and close < lower_rail
                            and lower_rail - close >= 0.15 * previous_atr):
                        min_body_ratio = 0.20
                except (KeyError, TypeError, ValueError, IndexError, OverflowError):
                    pass
            wick_problem = wick_exhaustion_problem(
                candle, decision.get('side') if decision else None,
                quote=close if is_live else None,
                min_body_ratio=min_body_ratio,
            )
            if wick_problem:
                return wick_problem
            body = abs(close - opening)
            if body <= 0:
                return (
                    "BLOCKED_LIVE_LONG_WICK_OR_DOJI"
                    if is_live else "BLOCKED_ENTRY_WICK_OR_DOJI"
                )
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "BLOCKED_INVALID_ENTRY_CANDLE"


def evaluate_golden_cross_fast_lane(frame, quote, symbol=""):
    """Authorize a fresh live LONG cross above KC middle with upper-rail room."""
    try:
        evidence = fast_cross_evidence(frame, quote, 'LONG', symbol=symbol)
        if evidence is None:
            return None
        stamp = evidence['stamp']
        previous_stamp = evidence['previous_stamp']
        quote = evidence['quote']
        return dict(
            action="ENTER", side="LONG", type=GOLDEN_CROSS_FAST_LONG_CODE,
            reason="LIVE_MA5_MA15_GOLDEN_CROSS_ABOVE_KC_MIDDLE",
            price=quote, entry_atr=evidence['atr'], confirmation_bar_id=stamp,
            breakout_bar_id=stamp, pair_confirmation_bar_id=previous_stamp,
            exit_bar_id=stamp,
            close_price=quote, intrabar=True,
            entry_phase="MA_GOLDEN_CROSS_FAST_LANE",
            body_atr=evidence['body_atr'],
            upper_rail_space_ratio=evidence['directional_space_ratio'],
            pending_signal_id=f"{symbol}:{GOLDEN_CROSS_FAST_LONG_CODE}:{int(stamp)}",
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def evaluate_death_cross_fast_lane(frame, quote, symbol=""):
    """Authorize a fresh live SHORT cross below KC middle with lower-rail room."""
    try:
        evidence = fast_cross_evidence(frame, quote, 'SHORT', symbol=symbol)
        if evidence is None:
            return None
        stamp = evidence['stamp']
        previous_stamp = evidence['previous_stamp']
        quote = evidence['quote']
        return {
            "action": "ENTER",
            "side": "SHORT",
            "type": DEATH_CROSS_FAST_SHORT_CODE,
            "reason": "LIVE_MA5_MA15_DEATH_CROSS_BELOW_KC_MIDDLE",
            "price": quote,
            "entry_atr": evidence['atr'],
            "confirmation_bar_id": stamp,
            "breakout_bar_id": stamp,
            "pair_confirmation_bar_id": previous_stamp,
            "exit_bar_id": stamp,
            "close_price": quote,
            "intrabar": True,
            "entry_phase": "MA_DEATH_CROSS_FAST_LANE",
            "body_atr": evidence['body_atr'],
            "lower_rail_space_ratio": evidence['directional_space_ratio'],
            "pending_signal_id": (
                f"{symbol}:{DEATH_CROSS_FAST_SHORT_CODE}:{int(stamp)}"
            ),
        }
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def evaluate_live_ma5_direction(frame, quote, side):
    """Require a quote-adjusted MA5 slope of at least 0.05 ATR in entry direction."""
    try:
        if live_ma5_slope_problem(frame, quote, side) is not None:
            return None
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 2:
            return None
        previous = frame.iloc[-2]
        previous_ma5 = float(previous["ma5"])
        slope_atr = live_ma5_slope_atr(frame, quote)
        atr = float(previous["atr"])
        if not math.isfinite(previous_ma5) or previous_ma5 <= 0 or slope_atr is None:
            return None
        live_ma5 = previous_ma5 + slope_atr * atr
        if not math.isfinite(live_ma5):
            return None
        if (side == "LONG" and live_ma5 <= previous_ma5) or (
            side == "SHORT" and live_ma5 >= previous_ma5
        ):
            return None
        return [previous_ma5, live_ma5]
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def continuation_direction_problem(side, opening, close, ma5, previous_ma5):
    """Enforce live MA5 side and strict slope; candle color is not a veto."""
    try:
        values = tuple(float(value) for value in (opening, close, ma5, previous_ma5))
        if side not in ('LONG', 'SHORT') or not all(
            math.isfinite(value) and value > 0 for value in values
        ):
            return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'
        opening, close, ma5, previous_ma5 = values
        if side == 'LONG':
            if ma5 <= previous_ma5:
                return 'BLOCKED_BY_MA5_DOWNWARD_SLOPE'
            if close <= ma5:
                return 'BLOCKED_BY_PRICE_BELOW_MA5'
        else:
            if ma5 >= previous_ma5:
                return 'BLOCKED_BY_MA5_UPWARD_SLOPE'
            if close >= ma5:
                return 'BLOCKED_BY_PRICE_ABOVE_MA5'
        return None
    except (TypeError, ValueError, OverflowError):
        return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'


def continuation_kc_problem(frame, side):
    """Require closed-candle KC confirmation; LONG also needs a rising KC edge."""
    try:
        if side not in ('LONG', 'SHORT') or frame is None or frame.empty:
            return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'
        closed = closed_entry_candles(frame)
        if len(closed) < 2:
            return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'
        previous, current = closed.iloc[-2], closed.iloc[-1]
        upper, lower, close = (
            float(current[key]) for key in ('kc_upper', 'kc_lower', 'close')
        )
        previous_upper, previous_lower = (
            float(previous[key]) for key in ('kc_upper', 'kc_lower')
        )
        values = (upper, lower, close, previous_upper, previous_lower)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or lower >= upper):
            return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'

        channel_state = current.get('channel_state')
        if not isinstance(channel_state, str) or not channel_state.strip():
            channel_state = ck_direction(closed, has_forming_bar=False)
        channel_state = str(channel_state).strip()
        if channel_state not in ('LONG', 'SHORT', 'UP', 'DOWN'):
            return 'REJECT_FLAT_CHANNEL'

        slope_key = 'kc_upper_slope' if side == 'LONG' else 'kc_lower_slope'
        slope_value = current.get(slope_key)
        slope = (
            float(slope_value) if slope_value is not None and math.isfinite(float(slope_value))
            else upper - previous_upper if side == 'LONG'
            else lower - previous_lower
        )
        if (side == 'LONG' and slope <= 0) or (side == 'SHORT' and slope >= 0):
            return 'REJECT_FLAT_CHANNEL'

        if side == 'LONG':
            if channel_state in ('SHORT', 'DOWN'):
                return 'REJECT_FLAT_CHANNEL'
            if close <= upper:
                return 'BLOCKED_CONTINUATION_NOT_CLOSED_CONFIRMATION'
        elif close >= lower:
            return 'BLOCKED_CONTINUATION_NOT_CLOSED_CONFIRMATION'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'


def entry_direction_problem(frame, quote, side):
    """Hard direction firewall shared by every entry route and final submit check."""
    try:
        if side not in ('LONG', 'SHORT') or frame is None or frame.empty:
            return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'
        live = frame.iloc[-1]
        is_forming = 'is_closed' in frame.columns and not bool(live['is_closed'])
        if is_forming:
            closed = closed_entry_candles(frame)
            if closed.empty:
                return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'
            previous = closed.iloc[-1]
            opening = float(live['open'])
            raw_close = float(live['close'])
            close = float(quote)
            ma5 = float(live['ma5']) + (close - raw_close) / 5.0
        else:
            closed = closed_entry_candles(frame)
            if len(closed) < 2:
                return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'
            current = closed.iloc[-1]
            previous = closed.iloc[-2]
            opening = float(current['open'])
            close = float(current['close'])
            ma5 = float(current['ma5'])
        previous_ma5 = float(previous['ma5'])
        return continuation_direction_problem(
            side, opening, close, ma5, previous_ma5,
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_BY_INVALID_CONTINUATION_DATA'


def short_hard_preentry_problem(frame, quote):
    """Hard reject shorts without a completed close and live quote below KC lower."""
    try:
        if frame is None or frame.empty:
            return 'BLOCKED_INSIDE_KC_BANDS'
        closed = closed_entry_candles(frame)
        if len(closed) < 2:
            return 'BLOCKED_INSIDE_KC_BANDS'

        # With a live trigger candle, Bar 1 is the completed candle before
        # Bar 2. A current quote back inside the lower rail also invalidates it.
        bar1 = closed.iloc[-2]
        latest = frame.iloc[-1]
        bar1_close = float(bar1['close'])
        bar1_lower = float(bar1['kc_lower'])
        live_lower = float(latest['kc_lower'])
        quote = float(quote)
        if not all(math.isfinite(value) and value > 0 for value in
                   (bar1_close, bar1_lower, live_lower, quote)):
            return 'BLOCKED_INSIDE_KC_BANDS'
        if bar1_close >= bar1_lower or quote >= live_lower:
            return 'BLOCKED_INSIDE_KC_BANDS'

        # A completed Bar 1 close below KC lower is a confirmed directional
        # breakout. Flat/entangled MAs only describe inside-band chop and must
        # never veto this already-confirmed rail-break path.
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_INVALID_SHORT_RAIL_DATA'


def evaluate_climax_flip_entry(frame, quote, symbol, account):
    """Build a one-shot opposite entry from a same-minute, matched climax close."""
    try:
        if (frame is None or frame.empty or account is None
                or symbol in getattr(account, 'positions', {})
                or 'is_closed' not in frame.columns
                or bool(frame.iloc[-1]['is_closed'])):
            return None
        closed = closed_entry_candles(frame)
        if len(closed) < 6:
            return None
        closes = [trade for trade in getattr(account, 'trades', [])
                  if trade.get('symbol') == symbol
                  and trade.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT')
                  and trade.get('status') == 'CLOSED']
        if not closes:
            return None
        close_trade = max(closes, key=lambda trade: float(trade.get('id') or 0.))
        old_side = 'LONG' if close_trade['action'] == 'CLOSE_LONG' else 'SHORT'
        true_climax = (
            is_true_climax_peak(frame) if old_side == 'LONG'
            else is_true_climax_valley(frame)
        )
        if not true_climax:
            return None
        climax = detect_climax_reversal(frame, old_side)
        expected_reason = (
            'CLIMAX_REVERSAL_EXIT_TOP' if old_side == 'LONG'
            else 'CLIMAX_REVERSAL_EXIT_BOTTOM'
        )
        reason = str(close_trade.get('reason') or '')
        if not climax or expected_reason not in reason or climax['reason'] != expected_reason:
            return None
        live_stamp = float(frame.iloc[-1]['timestamp'])
        close_id = float(close_trade.get('id') or 0.)
        if not (math.isfinite(live_stamp) and math.isfinite(close_id)
                and live_stamp <= close_id < live_stamp + 60000
                and float(closed.iloc[-1]['timestamp']) + 60000 == live_stamp):
            return None
        quote = float(quote)
        stop = float(climax['initial_sl'])
        if (not math.isfinite(quote) or quote <= 0
                or (climax['side'] == 'SHORT' and quote >= stop)
                or (climax['side'] == 'LONG' and quote <= stop)):
            return None
        flip_close_id = int(close_id)
        pending_id = f'{symbol}:{CLIMAX_REVERSAL_FLIP_CODE}:{flip_close_id}:{climax["side"]}'
        if any(
            trade.get('symbol') == symbol
            and trade.get('action') == 'OPEN_' + climax['side']
            and (trade.get('entry_snapshot') or {}).get('climax_flip_close_id') == flip_close_id
            for trade in getattr(account, 'trades', [])
        ):
            return None
        evidence = dict(climax)
        evidence.update(close_id=flip_close_id, close_action=close_trade['action'])
        return dict(
            action='ENTER', side=climax['side'], type=CLIMAX_REVERSAL_FLIP_CODE,
            reason=expected_reason + '_FLIP_GATE_PASSED', price=quote,
            entry_atr=climax['atr'], confirmation_bar_id=live_stamp,
            breakout_bar_id=climax['reversal_bar_id'],
            pair_confirmation_bar_id=float(closed.iloc[-2]['timestamp']),
            close_price=float(closed.iloc[-1]['close']),
            entry_phase='EXTREME_CLIMAX_FLIP', pending_signal_id=pending_id,
            initial_sl=stop, reverse_close_id=flip_close_id,
            climax_flip_close_id=flip_close_id,
            climax_flip_evidence=evidence,
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def _post_close_long_opportunity(closed, close_id):
    """Find a post-close support reclaim or a confirmed, high-momentum break."""
    try:
        if closed is None or len(closed) < 3:
            return None
        close_bar_start = math.floor(float(close_id) / 60000.) * 60000.
        eligible = closed.loc[closed['timestamp'] > close_bar_start]
        if (eligible.empty
                or float(eligible.iloc[-1]['timestamp'])
                != float(closed.iloc[-1]['timestamp'])):
            return None

        signal = eligible.iloc[-1]
        signal_stamp = float(signal['timestamp'])
        signal_open, signal_high, signal_low, signal_close = (
            float(signal[key]) for key in ('open', 'high', 'low', 'close')
        )
        if not all(math.isfinite(value) and value > 0 for value in (
                signal_stamp, signal_open, signal_high, signal_low, signal_close)):
            return None
        if (signal_high < max(signal_open, signal_close)
                or signal_low > min(signal_open, signal_close)
                or signal_close <= signal_open):
            return None

        previous = closed.loc[closed['timestamp'] < signal_stamp].iloc[-1]
        previous_ma5, previous_ma15 = float(previous['ma5']), float(previous['ma15'])
        signal_ma5, signal_ma15 = float(signal['ma5']), float(signal['ma15'])
        trend_ready = (
            all(math.isfinite(value) and value > 0 for value in (
                previous_ma5, previous_ma15, signal_ma5, signal_ma15,
            ))
            and signal_ma5 > signal_ma15
            and signal_ma15 > previous_ma15
            and signal_ma5 >= previous_ma5
            and signal_close > signal_ma5
        )
        if trend_ready:
            # 延續開倉（Re-entry）擇機優化：
            # 若多單平倉後多頭趨勢未變（MA5 > MA15 且斜率向上），允許在「回踩 MA5 獲得支撐並收陽線」時啟動順勢延續開倉
            single_bar_pullback = (
                signal_low <= signal_ma5
                and signal_close >= signal_ma5
                and signal_close > signal_open
            )
            valid_multi_pullback = False
            if len(eligible) >= 2:
                pullback_bars = eligible.iloc[-3:-1].tail(2)
                valid_pullback = True
                touched_supports = []
                for _, pullback in pullback_bars.iterrows():
                    pullback_open, pullback_high, pullback_low, pullback_close, ma5, ma15 = (
                        float(pullback[key]) for key in
                        ('open', 'high', 'low', 'close', 'ma5', 'ma15')
                    )
                    pullback_values = (
                        pullback_open, pullback_high, pullback_low,
                        pullback_close, ma5, ma15,
                    )
                    if (not all(math.isfinite(value) and value > 0 for value in pullback_values)
                            or pullback_high < max(pullback_open, pullback_close)
                            or pullback_low > min(pullback_open, pullback_close)
                            or pullback_close < ma15):
                        valid_pullback = False
                        break
                    touched_supports.extend(
                        level for level in (ma5, ma15) if pullback_low <= level
                    )
                if valid_pullback and touched_supports:
                    valid_multi_pullback = True

            if single_bar_pullback or valid_multi_pullback:
                return {
                    'kind': 'PULLBACK_SUPPORT',
                    'bar_id': signal_stamp,
                    'support': float(signal_ma5),
                    'signal_high': signal_high,
                    'signal_low': signal_low,
                    'signal_close': signal_close,
                    'atr': float(previous.get('atr') or 0.),
                }

        previous = closed.loc[closed['timestamp'] < signal_stamp].tail(20)
        if previous.empty:
            return None
        prior_high = max(float(value) for value in previous['high'])
        prior = closed.loc[closed['timestamp'] < signal_stamp].iloc[-1]
        prior_atr = float(prior['atr'])
        body = signal_close - signal_open
        if (all(math.isfinite(value) and value > 0 for value in (
                    prior_high, prior_atr, previous_ma5, previous_ma15,
                    signal_ma5, signal_ma15))
                and signal_open <= prior_high < signal_close
                and (body >= 0.6 * prior_atr or math.isclose(
                    body, 0.6 * prior_atr, rel_tol=1e-12, abs_tol=0.0,
                ))
                and signal_ma5 >= previous_ma5
                and signal_ma15 > previous_ma15
                and signal_ma5 > signal_ma15
                and signal_close > signal_ma5):
            return {
                'kind': 'MOMENTUM_BREAKOUT',
                'bar_id': signal_stamp,
                'support': prior_high,
                'signal_high': signal_high,
                'signal_low': signal_low,
                'signal_close': signal_close,
                'atr': prior_atr,
            }
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def _has_long_upper_wick(opening, high, close):
    body = abs(float(close) - float(opening))
    upper_wick = float(high) - max(float(opening), float(close))
    return upper_wick > 0 and upper_wick >= body


def evaluate_post_close_continuation(frame, quote, symbol, account, diagnostics=None):
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
        if side == 'SHORT':
            kc_problem = continuation_kc_problem(closed, side)
            if kc_problem:
                if diagnostics is not None:
                    diagnostics['reason'] = kc_problem
                return None

        close_id = float(latest_trade.get('id') or 0.)
        live = frame.iloc[-1]
        previous = closed.iloc[-1]
        stamp = float(live['timestamp'])
        previous_stamp = float(previous['timestamp'])
        quote = float(quote)
        if not all(math.isfinite(value) and value > 0
                   for value in (close_id, stamp, previous_stamp, quote)):
            return None

        if side == 'LONG':
            opportunity = _post_close_long_opportunity(closed, close_id)
            if opportunity is None:
                if diagnostics is not None:
                    diagnostics['reason'] = 'BLOCKED_POST_CLOSE_NO_LONG_OPPORTUNITY'
                return None
            if stamp != previous_stamp + 60000.:
                if diagnostics is not None:
                    diagnostics['reason'] = 'BLOCKED_POST_CLOSE_STALE_OPPORTUNITY'
                return None
            if _has_long_upper_wick(
                    float(previous['open']), float(previous['high']),
                    float(previous['close'])):
                if diagnostics is not None:
                    diagnostics['reason'] = 'BLOCKED_POST_CLOSE_UPPER_WICK_COOLDOWN'
                return None

            opening = float(live['open'])
            raw_close = float(live['close'])
            high = max(float(live['high']), quote)
            low = min(float(live['low']), quote)
            ma5 = float(live['ma5']) + (quote - raw_close) / 5.
            ma15 = float(live['ma15']) + (quote - raw_close) / 15.
            values = (
                opening, high, low, quote, ma5, ma15,
                float(previous['ma5']), float(previous['ma15']),
                float(opportunity['support']), float(opportunity['atr']),
            )
            if (not all(math.isfinite(value) and value > 0 for value in values)
                    or low > min(opening, quote)
                    or high < max(opening, quote)
                    or quote <= opening
                    or quote <= ma5
                    or quote < opportunity['support']
                    or ma5 < float(previous['ma5'])
                    or ma15 <= float(previous['ma15'])):
                if diagnostics is not None:
                    diagnostics['reason'] = 'BLOCKED_POST_CLOSE_LONG_STRENGTH_LOST'
                return None
            if _has_long_upper_wick(opening, high, quote):
                if diagnostics is not None:
                    diagnostics['reason'] = 'BLOCKED_POST_CLOSE_LIVE_UPPER_WICK'
                return None

            signal_id = (
                f'{symbol}:POST_CLOSE_LONG_{opportunity["kind"]}:'
                f'{int(opportunity["bar_id"])}:{int(close_id)}'
            )
            if any(
                trade.get('symbol') == symbol
                and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                and (trade.get('entry_snapshot') or {}).get('pending_signal_id') == signal_id
                for trade in getattr(account, 'trades', [])
            ):
                return None
            return dict(
                action='ENTER', side='LONG', type='TRIGGER_C_CONTINUATION',
                reason=('POST_CLOSE_LONG_PULLBACK_RECLAIM'
                        if opportunity['kind'] == 'PULLBACK_SUPPORT'
                        else 'POST_CLOSE_LONG_MOMENTUM_BREAKOUT'),
                price=quote, entry_atr=opportunity['atr'],
                confirmation_bar_id=stamp, close_price=quote, intrabar=True,
                entry_phase='POST_CLOSE_CONTINUATION_ENTRY',
                breakout_bar_id=opportunity['bar_id'],
                pair_confirmation_bar_id=previous_stamp,
                third_bar_id=stamp, pending_signal_id=signal_id,
                post_close_continuation_close_id=close_id,
                post_close_continuation_bar_id=opportunity['bar_id'],
                post_close_opportunity=opportunity['kind'],
                continuation_entry_bar_id=stamp,
                continuation_entry_bar_low=low,
                continuation_entry_bar_high=high,
                live_opening_context='POST_CLOSE_LONG_STRUCTURE_OPPORTUNITY',
            )

        # Strong same-side impulse follow-up is scoped to the first three
        # fully completed minutes after a confirmed close.  This specific
        # close-linked route is exempt from Three-Bar only; it still requires
        # a directional impulse, structure/volume confirmation, falling KC +
        # MA5, and a fresh quote that remains beyond both MA5 and the rail.
        close_bar_start = math.floor(close_id / 60000.0) * 60000.0
        post_close_bars = closed.loc[closed['timestamp'] > close_bar_start]
        if (not post_close_bars.empty
                and stamp == float(post_close_bars.iloc[-1]['timestamp']) + 60000.0):
            impulse = post_close_bars.iloc[-1]
            impulse_stamp = float(impulse['timestamp'])
            bars_after_close = int(round((impulse_stamp - close_bar_start) / 60000.0))
            prior = closed.loc[closed['timestamp'] < impulse_stamp]
            if 1 <= bars_after_close <= 3 and len(prior) >= 5:
                prior_bar = prior.iloc[-1]
                try:
                    impulse_open = float(impulse['open'])
                    impulse_close = float(impulse['close'])
                    impulse_atr = float(prior_bar['atr'])
                    ma5_slope = float(impulse['ma5']) - float(prior_bar['ma5'])
                    kc_slope = float(impulse['kc_middle']) - float(prior_bar['kc_middle'])
                    direction_matches = (
                        impulse_close < impulse_open if side == 'SHORT'
                        else impulse_close > impulse_open
                    )
                    body_atr = abs(impulse_close - impulse_open) / impulse_atr
                    broke_structure = (
                        impulse_close < float(prior_bar['low']) if side == 'SHORT'
                        else impulse_close > float(prior_bar['high'])
                    )
                    volume_expansion = False
                    if 'volume' in closed.columns:
                        impulse_volume = float(impulse['volume'])
                        reference_volume = [float(value) for value in prior.tail(5)['volume']]
                        volume_expansion = (
                            len(reference_volume) == 5
                            and impulse_volume >= 1.5 * (sum(reference_volume) / 5.0)
                        )
                    trend_aligned = (
                        ma5_slope < 0 and kc_slope < 0 if side == 'SHORT'
                        else ma5_slope > 0 and kc_slope > 0
                    )
                    qualifies = (
                        direction_matches and impulse_atr > 0 and body_atr >= 0.5
                        and (broke_structure or volume_expansion) and trend_aligned
                    )
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    qualifies = False
                if qualifies:
                    quote_ma5 = float(live['ma5']) + (quote - float(live['close'])) / 5.0
                    quote_rail = float(live[
                        'kc_lower' if side == 'SHORT' else 'kc_upper'
                    ])
                    remains_aligned = (
                        quote < quote_ma5 and quote < quote_rail if side == 'SHORT'
                        else quote > quote_ma5 and quote > quote_rail
                    )
                    if remains_aligned:
                        signal_id = (
                            f'{symbol}:POST_CLOSE_STRONG_CONTINUATION:{int(impulse_stamp)}:'
                            f'{int(close_id)}:{side}'
                        )
                        return dict(
                            action='ENTER', side=side, type='TRIGGER_C_CONTINUATION',
                            reason=('POST_CLOSE_STRONG_BEARISH_IMPULSE'
                                    if side == 'SHORT'
                                    else 'POST_CLOSE_STRONG_BULLISH_IMPULSE'),
                            price=quote, entry_atr=impulse_atr,
                            confirmation_bar_id=stamp, close_price=impulse_close,
                            intrabar=True, entry_phase='POST_CLOSE_CONTINUATION_ENTRY',
                            breakout_bar_id=impulse_stamp,
                            pair_confirmation_bar_id=float(prior_bar['timestamp']),
                            third_bar_id=stamp, pending_signal_id=signal_id,
                            post_close_continuation_close_id=close_id,
                            post_close_continuation_bar_id=impulse_stamp,
                            post_close_bars_after_close=bars_after_close,
                            post_close_strong_impulse=True,
                            continuation_entry_bar_id=stamp,
                            continuation_entry_bar_low=min(float(live['low']), quote),
                            continuation_entry_bar_high=max(float(live['high']), quote),
                            live_opening_context='POST_CLOSE_STRONG_IMPULSE',
                        )

        if (stamp != math.floor(close_id / 60000.0) * 60000.0 + 60000.0
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
        direction_problem = continuation_direction_problem(
            side, opening, quote, ma5, float(previous['ma5']),
        )
        if direction_problem:
            if diagnostics is not None:
                diagnostics['reason'] = direction_problem
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
            close_price=quote, intrabar=True,
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


def evaluate_continuation_entry(frame, quote, code=None, symbol: str = '', account=None,
                                diagnostics=None):
    """Re-enter an expanding outer-rail trend on a live directional candle."""
    try:
        if (frame is None or frame.empty or code not in (None, 'TRIGGER_C_CONTINUATION')
                or ('is_closed' in frame.columns and bool(frame.iloc[-1]['is_closed']))):
            return None
        if account is not None and symbol in getattr(account, 'positions', {}):
            return None
        live = frame.iloc[-1]
        quote = float(quote)
        opening = float(live['open'])
        upper = float(live['kc_upper'])
        lower = float(live['kc_lower'])
        if (quote > opening and quote <= upper) or (quote < opening and quote >= lower):
            if diagnostics is not None:
                diagnostics['reason'] = 'BLOCKED_CONTINUATION_QUOTE_RETURNED_INSIDE_KC'
            return None
        qualification = getattr(account, 'breakout_qualification', {}).get(symbol)
        if (isinstance(qualification, dict)
                and qualification.get('side') in ('LONG', 'SHORT')
                and float(live.get('timestamp') or 0.)
                <= float(qualification.get('breakout_bar_id') or 0.)):
            return None
        post_close = evaluate_post_close_continuation(
            frame, quote, symbol, account, diagnostics=diagnostics,
        )
        if post_close is not None and code in (None, 'TRIGGER_C_CONTINUATION'):
            return post_close
        if (diagnostics is not None
                and diagnostics.get('reason', '').startswith('BLOCKED_POST_CLOSE_')):
            return None
        closed = closed_entry_candles(frame)
        if closed.empty or float(frame.iloc[-1]['timestamp']) != float(closed.iloc[-1]['timestamp']) + 60000:
            return None
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
        # A continuation is still a same-side outer-rail entry. Never authorize
        # it after the live quote has pulled back inside the channel.
        current_bar = live.to_dict()
        current_bar.update(close=quote, high=high, low=low, ma5=ma5)
        gate_reason = continuation_entry_problem(
            closed, side, current_bar=current_bar, previous_bar=previous,
            reference_bars=closed.tail(5),
        )
        if gate_reason:
            if diagnostics is not None:
                diagnostics['reason'] = gate_reason
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
            close_price=quote, intrabar=True,
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


def evaluate_instant_reverse_breakout(frame, quote, symbol: str = '', account=None,
                                      closed_side: str = 'LONG'):
    """Authorize an opposite entry only against a matched, same-candle sharp close."""
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

        close_action = 'CLOSE_LONG' if closed_side == 'LONG' else 'CLOSE_SHORT'
        side = 'SHORT' if closed_side == 'LONG' else 'LONG'
        code = BEARISH_INSTANT_BREAKOUT_CODE if side == 'SHORT' else BULLISH_INSTANT_BREAKOUT_CODE
        close_trade = next((
            trade for trade in sorted(
                getattr(account, 'trades', []),
                key=lambda item: float(item.get('id') or 0.0), reverse=True,
            )
            if trade.get('symbol') == symbol and trade.get('action') == close_action
        ), None)
        if not close_trade or close_trade.get('status') != 'CLOSED':
            return None
        close_id = float(close_trade.get('id') or 0.)
        close_reason = str(close_trade.get('reason') or '')
        close_triggers = (
            ('BEARISH_INSTANT_BREAKOUT', 'WATERFALL_DROP',
             'EXIT_DOJI_BEARISH_CONFIRMATION', 'DOJI_REVERSAL_EXIT',
             'REAL_TOP_DUMP', 'CLIMAX_REVERSAL_EXIT_TOP')
            if closed_side == 'LONG' else
            ('BULLISH_INSTANT_BREAKOUT', 'WATERFALL_RISE',
             'EXIT_DOJI_BULLISH_CONFIRMATION', 'DOJI_REVERSAL_EXIT',
             'REAL_BOTTOM_PUMP', 'CLIMAX_REVERSAL_EXIT_BOTTOM')
        )
        if (not math.isfinite(close_id) or not stamp <= close_id < stamp + 60000
                or not any(trigger in close_reason for trigger in close_triggers)):
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
        directional_body = opening - quote if side == 'SHORT' else quote - opening
        beyond_rail = quote < lower if side == 'SHORT' else quote > upper
        beyond_middle = quote < middle if side == 'SHORT' else quote > middle
        beyond_ma5 = quote < ma5 if side == 'SHORT' else quote > ma5
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or lower >= upper or low > min(opening, quote)
                or high < max(opening, quote)
                or not lower <= opening <= upper
                or directional_body < 0.5 * atr
                or not (beyond_rail and beyond_middle and beyond_ma5)):
            return None

        signal_id = (
            f'{symbol}:{code}:{int(stamp)}:{int(close_id)}:{side}'
        )
        return dict(
            action='ENTER', side=side, type=code,
            reason=f'{closed_side}_SHARP_REVERSAL_CLOSE_THEN_{side}_KC_BREAKOUT',
            price=quote, entry_atr=atr, confirmation_bar_id=stamp,
            breakout_bar_id=stamp, pair_confirmation_bar_id=previous_stamp,
            close_price=quote, intrabar=True,
            entry_phase=f'ATOMIC_{side}_REVERSE',
            pending_signal_id=signal_id,
            reverse_close_id=int(close_id),
            kc_confirmation_edge=lower if side == 'SHORT' else upper,
            live_body_atr=directional_body / atr,
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def evaluate_bearish_instant_breakout(frame, quote, symbol: str = '', account=None):
    """Backward-compatible name for the long-close to short reversal gate."""
    return evaluate_instant_reverse_breakout(frame, quote, symbol, account, 'LONG')


def evaluate_bullish_instant_breakout(frame, quote, symbol: str = '', account=None):
    """Authorize the symmetric short-close to long reversal gate."""
    return evaluate_instant_reverse_breakout(frame, quote, symbol, account, 'SHORT')


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
        passed, _ = validate_ma_alignment(frame, side, quote)
        if not passed:
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
            bypass_spatial_check=True,
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
        passed, _ = validate_ma_alignment(frame, side, quote)
        if not passed:
            return None
        stamp = float(live["timestamp"])
        atr = float(frame.iloc[-2]["atr"])
        closed_candles = closed_entry_candles(frame)

        if side == 'LONG':
            # 【即時破軌開多嚴格限制條件】
            # 1. 實質破軌深度：盤中最新價 > KC 上軌 + 0.15 ATR
            if quote <= upper + 0.15 * atr:
                return None
            # 2. 實體門檻：盤中實體陽線漲幅（Price - Open）>= 0.6 ATR
            body = quote - opened
            if atr > 0 and body < 0.6 * atr:
                return None
            # 3. 嚴格影線過濾：當前價位於高點區，上影線長度 <= 0.3 倍實體
            high_p = max(float(live.get("high", quote)), quote)
            upper_wick = high_p - quote
            if upper_wick > 0.3 * body:
                return None
            # 4. 趨勢強制對齊：kc_trend != 'DOWN'
            channel_state = str(live.get("channel_state", "")).strip().upper()
            is_kc_down = (
                channel_state in ("KC向下", "KC_TREND_DOWN", "DOWN", "DOWNTREND", "BEARISH", "SHORT")
                or (len(closed_candles) >= 2 and float(closed_candles.iloc[-1].get("kc_middle", 0.0)) < float(closed_candles.iloc[-2].get("kc_middle", 0.0)))
            )
            if is_kc_down:
                return None
            # 5. MA5 斜率向上（ΔMA5 > 0）
            prev_ma5 = float(closed_candles.iloc[-1].get("ma5", 0.0)) if len(closed_candles) >= 1 else 0.0
            live_ma5 = float(live.get("ma5", prev_ma5)) + (quote - float(live.get("close", quote))) / 5.0
            if prev_ma5 > 0 and live_ma5 <= prev_ma5:
                return None

        elif side == 'SHORT':
            # 【即時破軌開空嚴格限制條件】
            # 1. 實質破軌深度：盤中最新價 < KC 下軌 - 0.15 ATR
            if quote >= lower - 0.15 * atr:
                return None
            # 2. 實體門檻：盤中實體陰線跌幅（Open - Price）>= 0.6 ATR
            body = opened - quote
            if atr > 0 and body < 0.6 * atr:
                return None
            # 3. 嚴格影線過濾：當前價位於低點區，下影線長度 <= 0.3 倍實體
            low_p = min(float(live.get("low", quote)), quote)
            lower_wick = quote - low_p
            if lower_wick > 0.3 * body:
                return None
            # 4. 趨勢強制對齊：kc_trend != 'UP'
            channel_state = str(live.get("channel_state", "")).strip().upper()
            is_kc_up = (
                channel_state in ("KC向上", "KC_TREND_UP", "UP", "UPTREND", "BULLISH", "LONG")
                or (len(closed_candles) >= 2 and float(closed_candles.iloc[-1].get("kc_middle", 0.0)) > float(closed_candles.iloc[-2].get("kc_middle", 0.0)))
            )
            if is_kc_up:
                return None
            # 5. MA5 斜率向下（ΔMA5 < 0）
            prev_ma5 = float(closed_candles.iloc[-1].get("ma5", 0.0)) if len(closed_candles) >= 1 else 0.0
            live_ma5 = float(live.get("ma5", prev_ma5)) + (quote - float(live.get("close", quote))) / 5.0
            if prev_ma5 > 0 and live_ma5 >= prev_ma5:
                return None

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
            bypass_spatial_check=True,
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def evaluate_realtime_short_rail_breach(frame, quote, symbol=""):
    """Authorize a live SHORT at the first qualified KC lower-rail breach.

    A crossed rail is handled here immediately: it either produces the
    real-time candidate or a specific hard rejection, never a delayed fallback
    candidate from another short-entry route on the same quote.
    """
    try:
        if (frame is None or frame.empty or 'is_closed' not in frame.columns
                or bool(frame.iloc[-1]['is_closed'])):
            return None
        live = frame.iloc[-1]
        closed = closed_entry_candles(frame)
        if closed.empty:
            return None
        previous = closed.iloc[-1]
        quote = float(quote)
        opening = float(live['open'])
        lower = float(live['kc_lower'])
        middle = float(live['kc_middle'])
        previous_middle = float(previous['kc_middle'])
        atr = float(previous['atr'])
        raw_close = float(live['close'])
        values = (quote, opening, lower, middle, previous_middle, atr, raw_close)
        if not all(math.isfinite(value) and value > 0 for value in values) or atr <= 0:
            return dict(action='WAIT', reason='BLOCKED_REALTIME_SHORT_INVALID_DATA')
        if quote >= lower:
            return None

        distance_atr = (lower - quote) / atr
        if distance_atr > 3.0:
            return dict(action='WAIT', reason='BLOCKED_BY_EXTENDED_BREAKOUT')

        passed, reason = validate_ma_alignment(frame, 'SHORT', quote)
        if not passed:
            return dict(action='WAIT', reason=reason)

        live_high = max(float(live['high']), opening, quote)
        live_low = min(float(live['low']), opening, quote)
        prior_wick_problem = wick_exhaustion_problem(previous, 'SHORT')
        live_wick_problem = wick_exhaustion_problem(
            dict(open=opening, close=raw_close, high=live_high, low=live_low),
            'SHORT', quote=quote,
            min_body_ratio=0.20 if distance_atr >= 0.15 else 0.35,
        )
        if prior_wick_problem or live_wick_problem:
            return dict(action='WAIT', reason=(
                prior_wick_problem or live_wick_problem
            ))
        prior_body = abs(float(previous['close']) - float(previous['open']))
        prior_wick = (
            min(float(previous['open']), float(previous['close']))
            - float(previous['low'])
        ) / prior_body
        live_body = abs(quote - opening)
        live_wick = (min(opening, quote) - live_low) / live_body
        meme_coin = is_meme_breakout_symbol(symbol)

        body_atr = (opening - quote) / atr
        if quote >= opening:
            return dict(action='WAIT', reason='BLOCKED_REALTIME_SHORT_NOT_BEARISH')
            
        # [2026-10-10] 嚴格校驗破軌開空資格
        candle_range = live_high - live_low + 1e-9
        body = opening - quote
        body_ratio = body / candle_range
        
        min_body_atr = 0.20 if meme_coin else 0.50
        min_body_ratio = (
            0.20 if distance_atr >= 0.15
            else 0.35 if meme_coin else 0.60
        )
        is_strong_breakout = (
            body_atr >= min_body_atr and body_ratio >= min_body_ratio
        )
        if not is_strong_breakout:
            return dict(action='WAIT', reason='BLOCKED_BY_INSUFFICIENT_BREAKOUT_CONFIRMATION')

        live_ma3 = float(live['ma3']) + (quote - raw_close) / 3.0
        live_ma5 = float(live['ma5']) + (quote - raw_close) / 5.0
        if not all(math.isfinite(value) and value > 0 for value in (live_ma3, live_ma5)):
            return dict(action='WAIT', reason='BLOCKED_REALTIME_SHORT_INVALID_MA')
        if middle >= previous_middle:
            return dict(action='WAIT', reason='BLOCKED_REALTIME_SHORT_KC_NOT_FALLING')
        if live_ma3 > live_ma5:
            return dict(action='WAIT', reason='BLOCKED_REALTIME_SHORT_MA_ORDER')

        rr = realtime_short_reward_risk(frame, quote, atr)
        if rr is None:
            return dict(action='WAIT', reason='BLOCKED_REALTIME_SHORT_REWARD_RISK_UNAVAILABLE')
        if rr['reward'] < rr['risk']:
            return dict(action='WAIT', reason='BLOCKED_REALTIME_SHORT_REWARD_BELOW_RISK',
                        expected_net_reward=rr['reward'], expected_net_risk=rr['risk'],
                        expected_reward_risk=rr['reward_risk'])

        stamp = float(live['timestamp'])
        if not math.isfinite(stamp) or stamp <= 0:
            return dict(action='WAIT', reason='BLOCKED_REALTIME_SHORT_INVALID_TIMESTAMP')
        signal_id = f'{symbol}:{REALTIME_RAIL_BREACH_SHORT_CODE}:{int(stamp)}'
        return dict(
            action='ENTER', side='SHORT', type=REALTIME_RAIL_BREACH_SHORT_CODE,
            reason='REALTIME_KC_LOWER_RAIL_BREACH', price=quote,
            entry_atr=atr, confirmation_bar_id=stamp, close_price=quote,
            intrabar=True, entry_phase='REALTIME_RAIL_BREACH',
            breakout_bar_id=stamp, pair_confirmation_bar_id=None,
            third_bar_id=stamp, pending_signal_id=signal_id,
            live_opening_context='REALTIME_LOWER_RAIL_BREACH',
            realtime_body_atr=body_atr, realtime_distance_atr=distance_atr,
            realtime_lower_shadow_ratio=live_wick,
            previous_lower_shadow_ratio=prior_wick,
            expected_net_reward=rr['reward'], expected_net_risk=rr['risk'],
            expected_reward_risk=rr['reward_risk'], structural_target=rr['target'],
            estimated_stop=rr['stop'],
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return dict(action='WAIT', reason='BLOCKED_REALTIME_SHORT_INVALID_DATA')


def realtime_short_reward_risk(frame, quote, atr):
    """Estimate net reward to the nearest confirmed low versus a 1.5 ATR stop."""
    try:
        quote, atr = float(quote), float(atr)
        if not math.isfinite(quote) or not math.isfinite(atr) or quote <= 0 or atr <= 0:
            return None
        from core.services.entry_room_service import entry_room
        room = entry_room(frame, quote, 'SHORT', TAKER_FEE_RATE, SLIPPAGE_PCT, 0.0)
        target = float(room.get('target')) if room.get('target') is not None else None
        if target is None or not math.isfinite(target) or target <= 0 or target >= quote:
            return None
        entry_fill = quote * (1 + SLIPPAGE_PCT)
        target_fill = target * (1 + SLIPPAGE_PCT)
        stop = quote + 1.5 * atr
        stop_fill = stop * (1 + SLIPPAGE_PCT)
        reward = entry_fill - target_fill - (entry_fill + target_fill) * TAKER_FEE_RATE
        risk = stop_fill - entry_fill + (entry_fill + stop_fill) * TAKER_FEE_RATE
        if not all(math.isfinite(value) for value in (reward, risk)) or risk <= 0:
            return None
        return dict(reward=reward, risk=risk, target=target, stop=stop,
                    reward_risk=reward / risk)
    except (AttributeError, TypeError, ValueError, KeyError, OverflowError):
        return None


def evaluate_three_bar_outer_breakout(frame, quote, symbol="", requested_side=None):
    """Require a closed rail break, a closed same-color confirmation, and a live third bar."""
    try:
        if (frame is None or frame.empty or len(frame) < 3
                or frame.attrs.get("timeframe_ms", 60000) != 60000
                or "is_closed" not in frame.columns
                or bool(frame.iloc[-1]["is_closed"])):
            return None
        closed = closed_entry_candles(frame)
        if len(closed) < 2:
            return None
        breakout, confirmation = closed.iloc[-2], closed.iloc[-1]
        live = frame.iloc[-1]
        quote = float(quote)
        interval = float(frame.attrs.get("timeframe_ms", 60000))
        stamps = tuple(float(row["timestamp"]) for row in (breakout, confirmation, live))
        if (not all(math.isfinite(value) and value > 0 for value in (*stamps, quote))
                or stamps[1] != stamps[0] + interval
                or stamps[2] != stamps[1] + interval):
            return None

        for side in ("LONG", "SHORT"):
            if requested_side is not None and side != requested_side:
                continue
            sign = 1 if side == "LONG" else -1
            rail = "kc_upper" if side == "LONG" else "kc_lower"
            first = breakout
            second = confirmation
            opening = float(live["open"])
            current_rail = float(live[rail])
            values = []
            for candle in (first, second):
                candle_open, candle_close, high, low, candle_rail = (
                    float(candle[key]) for key in
                    ("open", "close", "high", "low", rail)
                )
                candle_lower, candle_upper = (
                    float(candle[key]) for key in ("kc_lower", "kc_upper")
                )
                if (not all(math.isfinite(value) and value > 0 for value in
                            (candle_open, candle_close, high, low, candle_rail,
                             candle_lower, candle_upper))
                        or low > min(candle_open, candle_close)
                        or high < max(candle_open, candle_close)
                        or high <= low or candle_lower >= candle_upper):
                    values = []
                    break
                body_ratio = abs(candle_close - candle_open) / (high - low)
                if body_ratio < 0.20 or sign * (candle_close - candle_open) <= 0:
                    values = []
                    break
                values.append((
                    candle_open, candle_close, candle_rail,
                    candle_lower, candle_upper, high, low,
                ))
            if len(values) != 2:
                continue

            (first_open, first_close, first_rail, first_lower, first_upper,
             first_high, first_low) = values[0]
            (_, _, _, _, _, second_high, second_low) = values[1]
            lower, upper = float(live["kc_lower"]), float(live["kc_upper"])
            high, low = max(float(live["high"]), quote), min(float(live["low"]), quote)
            if (not all(math.isfinite(value) and value > 0 for value in
                        (opening, current_rail, lower, upper, high, low))
                    or lower >= upper
                    or low > min(opening, quote)
                    or high < max(opening, quote)):
                continue

            first_crossed = (
                first_lower <= first_open <= first_rail < first_close
                if side == "LONG" else
                first_upper >= first_open >= first_rail > first_close
            )
            # The third candle may continue in the breakout direction, or be a
            # small counter candle that holds the first two candles' structure.
            # Counter-body size is capped at half the breakout candle's ATR.
            third_body = abs(quote - opening)
            third_is_aligned = sign * (quote - opening) > 0
            third_is_small_counter = (
                sign * (quote - opening) < 0 and third_body <= 0.5 * float(first["atr"])
            )
            third_holds_structure = (
                quote > min(first_low, second_low)
                if side == "LONG" else
                quote < max(first_high, second_high)
            )
            third_outside = sign * (quote - current_rail) > 0
            if not (first_crossed
                    and (third_is_aligned or
                         (third_is_small_counter and third_holds_structure))
                    and third_outside):
                continue

            atr = float(first["atr"])
            if not math.isfinite(atr) or atr <= 0:
                continue
            return dict(
                action="ENTER", side=side, type="TRIGGER_A_KC_BREAKOUT",
                reason="KC_THREE_BAR_OUTER_BREAKOUT",
                price=quote, entry_atr=atr,
                confirmation_bar_id=stamps[2],
                breakout_bar_id=stamps[0],
                pair_confirmation_bar_id=stamps[1],
                exit_bar_id=None,
                close_price=float(second["close"]), intrabar=True,
                entry_phase="KC_THREE_BAR_BREAKOUT",
                pending_signal_id=(
                    f"{symbol}:KC_THREE_BAR_BREAKOUT:{int(stamps[0])}:"
                    f"{int(stamps[1])}:{side}"
                ),
                third_bar_id=stamps[2],
            )
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def entry_trend_alignment_ready(frame, side):
    """Require closed KC, MA3, MA5 and MA15 trends to agree with the entry side."""
    try:
        if side not in ("LONG", "SHORT"):
            return False
        closed = closed_entry_candles(frame)
        if len(closed) < 3 or not {
            "ma3", "ma5", "ma15", "kc_middle",
        }.issubset(closed.columns):
            return False
        recent = closed.tail(3)
        sign = 1 if side == 'LONG' else -1
        ma3 = [float(value) for value in recent['ma3']]
        ma15 = [float(value) for value in recent['ma15']]
        kc_middle = [float(value) for value in recent['kc_middle']]
        if not all(math.isfinite(value) and value > 0
                   for value in ma3 + ma15 + kc_middle):
            return False
        return (
            all(sign * (right - left) > 0
                for left, right in zip(kc_middle, kc_middle[1:]))
            and ma5_ma15_trend_confirmed(recent["ma5"], recent["ma15"], side)
            and all(sign * (right - left) > 0
                    for left, right in zip(ma3, ma3[1:]))
            and sign * (ma3[-1] - ma15[-1]) > 0
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def entry_consolidation_problem(frame, quote, *, allow_directional_breakout=False,
                               side=None):
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

        # Repeated MA5/MA15 crosses are a chop regime. A fresh cross alone is
        # insufficient: require three completed MA5 steps to persist in the
        # requested direction and MA5 to remain on that side of MA15.
        spreads = [left - right for left, right in zip(ma5_values, ma15_values)]
        cross_history = closed.tail(10)
        cross_spreads = (
            [float(ma5) - float(ma15)
             for ma5, ma15 in zip(cross_history["ma5"], cross_history["ma15"])]
            if len(cross_history) == 10 else []
        )
        cross_count = sum(
            (left < 0 < right) or (left > 0 > right)
            for left, right in zip(cross_spreads, cross_spreads[1:])
        )
        if cross_count >= 3:
            return "BLOCKED_MA5_MA15_CROSS_CHOP"
        if cross_count >= 2:
            changes = [right - left for left, right in zip(ma5_values, ma5_values[1:])]
            sign = 1 if side == "LONG" else -1 if side == "SHORT" else 0
            aligned = (
                sign != 0 and sign * spreads[-1] > 0
                and len(changes) >= 3
                and all(sign * change > 0 for change in changes[-3:])
            )
            if not aligned:
                return "BLOCKED_MA5_MA15_CROSS_CHOP"

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
        kc_problem = continuation_kc_problem(closed_frame, side)
        if kc_problem:
            return kc_problem
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

        direction_problem = continuation_direction_problem(
            side, opening, close, ma5, previous_ma5,
        )
        if direction_problem:
            return direction_problem

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
    """延續開倉（Re-entry 擇時機制）：
    平倉後趨勢未破壞時，符合以下結構即可再次進場：
      * 機會 A（回踩支撐確認）：MA5 > MA15 且 MA15 向上，價格回踩 MA5 或 MA15 獲得支撐不破，隨後當根收陽且站回 MA5。
      * 機會 B（結構新突破）：價格以帶動能之實體 K 線（>= 0.6 ATR）收盤突破前一波高點。
    空單完全對稱。
    """
    if len(closed_frame) < 3: return None, None
    if not last_exit_side: return None, "NO_HISTORY"

    curr = closed_frame.iloc[-1]
    prev = closed_frame.iloc[-2]
    atr = float(curr.get('atr', 0.0))

    if last_exit_side == "LONG":
        ma5_curr = float(curr.get('ma5', 0.0))
        ma15_curr = float(curr.get('ma15', 0.0))
        ma15_prev = float(prev.get('ma15', 0.0))
        close_p = float(curr['close'])
        open_p = float(curr['open'])
        low_p = float(curr['low'])
        prev_high = float(prev['high'])
        body = close_p - open_p

        # 機會 A（回踩支撐確認）：MA5 > MA15 且 MA15 向上，價格回踩 MA5 或 MA15 獲得支撐不破，隨後當根收陽且站回 MA5
        pullback_support = (
            ma5_curr > ma15_curr and ma15_curr >= ma15_prev and
            (low_p <= ma5_curr or low_p <= ma15_curr) and
            close_p >= ma15_curr and
            close_p > open_p and close_p >= ma5_curr
        )
        # 機會 B（結構新突破）：價格以帶動能之實體 K 線（>= 0.6 ATR）收盤突破前一波高點
        momentum_breakout = (
            body >= 0.6 * atr and close_p > prev_high and close_p > open_p
        )
        if pullback_support or momentum_breakout:
            return "LONG", "RE_ENTRY_LONG"

    elif last_exit_side == "SHORT":
        ma5_curr = float(curr.get('ma5', 0.0))
        ma15_curr = float(curr.get('ma15', 0.0))
        ma15_prev = float(prev.get('ma15', 0.0))
        close_p = float(curr['close'])
        open_p = float(curr['open'])
        high_p = float(curr['high'])
        prev_low = float(prev['low'])
        body = open_p - close_p

        # 機會 A（回抽阻力確認）：MA5 < MA15 且 MA15 向下，價格回抽 MA5 或 MA15 獲得阻力不破，隨後當根收陰且跌回 MA5
        pullback_resistance = (
            ma5_curr < ma15_curr and ma15_curr <= ma15_prev and
            (high_p >= ma5_curr or high_p >= ma15_curr) and
            close_p <= ma15_curr and
            close_p < open_p and close_p <= ma5_curr
        )
        # 機會 B（結構新突破）：價格以帶動能之實體 K 線（>= 0.6 ATR）收盤跌破前一波低點
        momentum_breakdown = (
            body >= 0.6 * atr and close_p < prev_low and close_p < open_p
        )
        if pullback_resistance or momentum_breakdown:
            return "SHORT", "RE_ENTRY_SHORT"

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

    # 引擎二：一般 KC 收線破軌開倉（單邊爆發點）
    # 多單：當根 1m 實體陽線收盤突破 KC 上軌（close > upper_band 且 close > open），實體 >= 0.5 ATR。限制：kc_trend != 'DOWN'，上影線 < 0.7 倍實體。
    # 空單：當根 1m 實體陰線收盤跌破 KC 下軌（close < lower_band 且 close < open），實體 >= 0.5 ATR。限制：kc_trend != 'UP'，下影線 < 0.7 倍實體。
    kc_up = curr['kc_middle'] >= prev['kc_middle']
    kc_down = curr['kc_middle'] <= prev['kc_middle']
    kc_trend = str(curr.get('kc_trend', '')).upper()

    body_long = curr['close'] - curr['open']
    upper_wick_long = curr['high'] - curr['close']
    long_breakout = (
        curr['close'] > curr['kc_upper'] and
        curr['close'] > curr['open'] and
        curr['ma5'] > curr['ma15'] and
        curr['close'] > curr['ma5'] and
        body_long >= 0.5 * atr and
        not ('DOWN' in kc_trend or not kc_up) and
        upper_wick_long < 0.7 * max(body_long, 1e-9)
    )

    body_short = curr['open'] - curr['close']
    lower_wick_short = curr['close'] - curr['low']
    short_breakout = (
        curr['close'] < curr['kc_lower'] and
        curr['close'] < curr['open'] and
        curr['ma5'] < curr['ma15'] and
        curr['close'] < curr['ma5'] and
        body_short >= 0.5 * atr and
        not ('UP' in kc_trend or not kc_down) and
        lower_wick_short < 0.7 * max(body_short, 1e-9)
    )

    # 引擎一：MA5 / MA15 交叉敏捷開倉（起跑點）
    golden_cross = prev['ma5'] <= prev['ma15'] and curr['ma5'] > curr['ma15']
    death_cross = prev['ma5'] >= prev['ma15'] and curr['ma5'] < curr['ma15']
    long_ma_cross = golden_cross and curr['close'] > curr['ma5'] and curr['close'] > curr['open'] and curr['ma5'] > curr['ma15']
    short_ma_cross = death_cross and curr['close'] < curr['ma5'] and curr['close'] < curr['open'] and curr['ma5'] < curr['ma15']

    # 引擎三及順勢延續
    long_cont = (
        curr['close'] > curr['kc_upper'] and
        curr['close'] > curr['ma5'] and
        curr['ma5'] > curr['ma15'] and
        curr['ma5'] >= prev['ma5'] and
        curr['close'] > curr['open']
    )

    short_cont = (
        curr['close'] < curr['kc_lower'] and
        curr['close'] < curr['ma5'] and
        curr['ma5'] < curr['ma15'] and
        curr['ma5'] <= prev['ma5'] and
        curr['close'] < curr['open']
    )

    if long_breakout: return "LONG", "TRIGGER_A_KC_BREAKOUT"
    if short_breakout: return "SHORT", "TRIGGER_A_KC_BREAKOUT"
    if long_ma_cross: return "LONG", "TRIGGER_B_MA_CROSS"
    if short_ma_cross: return "SHORT", "TRIGGER_B_MA_CROSS"
    if long_cont: return "LONG", "TRIGGER_C_CONTINUATION"
    if short_cont: return "SHORT", "TRIGGER_C_CONTINUATION"

    return None, None

def check_entry_gates(account, symbol, closed_frame, side, trigger_type):
    if len(closed_frame) < 12: return False, "WAIT_ENOUGH_DATA_FOR_GATES"
    curr = closed_frame.iloc[-1]
    prev = closed_frame.iloc[-2]

    # 【均線相對位置的絕對限制】第一道開倉閘門底線
    if trigger_type not in (REALTIME_RAIL_BREACH_SHORT_CODE, 'KC_REALTIME_RAIL_BREACH_SHORT'):
        passed, reason = validate_ma_alignment(closed_frame, side)
        if not passed:
            return False, reason

    if account is not None and symbol in getattr(account, "positions", {}):
        return False, "BLOCKED_BY_POSITION_GATE"

    meme_realtime_breakout = (
        is_meme_breakout_symbol(symbol)
        and trigger_type == REALTIME_RAIL_BREACH_SHORT_CODE
    )

    # ================= 破軌專屬防護 (FRESH & QUALITY GATE) =================
    extended_relaxed_for_trend = False
    if trigger_type == "TRIGGER_A_KC_BREAKOUT":
        # FRESH_BREAKOUT_GATE: 防止高位連拉盲目追高
        strong_directional_trend = (
            side == 'SHORT'
            and float(curr['kc_middle']) < float(prev['kc_middle'])
            and float(curr['ma5']) < float(prev['ma5'])
        ) or (
            side == 'LONG'
            and float(curr['kc_middle']) > float(prev['kc_middle'])
            and float(curr['ma5']) > float(prev['ma5'])
        )
        extended = (
            side == "LONG" and prev['close'] > prev['kc_upper'] and prev['open'] > prev['kc_upper']
        ) or (
            side == "SHORT" and prev['close'] < prev['kc_lower'] and prev['open'] < prev['kc_lower']
        )
        if extended and not strong_directional_trend:
            return False, "BLOCKED_BY_EXTENDED_BREAKOUT_GATE"
        extended_relaxed_for_trend = extended and strong_directional_trend

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
        return True, (
            "GATE_PASSED_FAST_LANE_EXTENDED_TREND"
            if extended_relaxed_for_trend else "GATE_PASSED_FAST_LANE"
        )

    # ================= 常規進場檢查 (MA_CROSS) =================
    tolerance = curr['atr'] * 0.05
    is_golden_cross_fast = trigger_type == GOLDEN_CROSS_FAST_LONG_CODE
    is_death_cross_fast = trigger_type == DEATH_CROSS_FAST_SHORT_CODE
    is_momentum_burst = trigger_type in (
        MOMENTUM_BURST_LONG_CODE,
        MOMENTUM_BURST_SHORT_CODE,
    )
    is_ma_cross = (
        trigger_type == "TRIGGER_B_MA_CROSS"
        or is_golden_cross_fast
        or is_death_cross_fast
        or is_momentum_burst
        or meme_realtime_breakout
    )
    if side == "LONG":
        if not is_ma_cross and curr['kc_middle'] < prev['kc_middle'] - tolerance: return False, "BLOCKED_BY_BEARISH_KC_SLOPE"
        if curr['ma5'] < curr['ma15'] and not (is_golden_cross_fast or is_momentum_burst): return False, "BLOCKED_BY_MA_DIVERGENCE"
    elif side == "SHORT":
        if not is_ma_cross and curr['kc_middle'] > prev['kc_middle'] + tolerance: return False, "BLOCKED_BY_BULLISH_KC_SLOPE"
        if curr['ma5'] > curr['ma15'] and not (is_death_cross_fast or is_momentum_burst): return False, "BLOCKED_BY_MA_DIVERGENCE"

    min_volatility_atr = 0.8 if meme_realtime_breakout else 1.2
    if (curr['kc_upper'] - curr['kc_lower']) / curr['atr'] < min_volatility_atr: return False, "BLOCKED_BY_VOLATILITY_GATE"

    past_k = closed_frame.iloc[-4]
    atr_norm = curr['atr'] + 1e-6
    if (not meme_realtime_breakout and abs(curr['ma15'] - past_k['ma15']) / atr_norm < 0.10 and abs(curr['kc_middle'] - past_k['kc_middle']) / atr_norm < 0.10):
        return False, "BLOCKED_BY_FLAT_MARKET_GATE"

    if not meme_realtime_breakout and count_ma_crosses(closed_frame.iloc[-8:]) >= 4: return False, "BLOCKED_BY_WHIPSAW_CHOP_GATE"

    is_trend_bypassed = is_ma_cross
    if not is_trend_bypassed and not entry_trend_alignment_ready(closed_frame, side):
        return False, "BLOCKED_BY_TREND_GATE"

    if is_doji_candle(curr) and not (is_golden_cross_fast or is_death_cross_fast or is_momentum_burst or meme_realtime_breakout): return False, "BLOCKED_BY_DOJI_GATE"
    if side == "LONG" and curr['close'] < curr['open'] and not (is_golden_cross_fast or is_momentum_burst): return False, "BLOCKED_BY_RED_CANDLE_GATE"
    if side == "SHORT" and curr['close'] > curr['open'] and not (is_death_cross_fast or is_momentum_burst or meme_realtime_breakout): return False, "BLOCKED_BY_GREEN_CANDLE_GATE"

    return True, "GATE_PASSED_STANDARD"

def pre_flight_safety_check(
    frame, quote, side, decision_type=None, account=None, symbol=None,
    entry_phase=None,
):
    """最頂層進場門控：MA5 幾何生死鐵律、冷卻與安全審核（Pre-Flight Check）。
    
    一票否決權：
    一、MA5 幾何生死鐵律：
        1. 【MA5 向下，100% 絕對禁止開多（向下不開多）】：
           - 若 ma5_slope < 0（或 MA5 現值 < 前一根 MA5）：
             * 嚴禁發送任何 BUY/LONG 訂單！回傳 BLOCKED_BY_MA5_SLOPE_DOWN。
        2. 【MA5 向上，100% 絕對禁止開空（向上不開空）】：
           - 若 ma5_slope > 0（或 MA5 現值 > 前一根 MA5）：
             * 嚴禁發送任何 SELL/SHORT 訂單！回傳 BLOCKED_BY_MA5_SLOPE_UP。
        3. 【MA5 走平，100% 全面休眠禁止開倉（平行不開倉）】：
           - 斜率閾值：MA5 單根變化未達前一根已收線 ATR 的 0.05 倍：
             * 多單與空單一律全面禁止開倉！回傳 BLOCKED_BY_MA5_PARALLEL_CHOP。
    二、防反覆開平刷單冷卻（Anti-Churn Cooldown）：
        1. 相同方向 10 根 K 棒內至少 3 次開倉且仍在最近開倉價 1 ATR 內時，
           平倉後 600 秒內禁止同向重開。
        2. 其他同向平倉仍依盈虧採用 60／180 秒冷卻。
        2. 同一根 K 棒禁止同方向平倉後重開；以平倉成交時間與 K 棒時間戳精確比對。
           - 回傳 BLOCKED_SAME_BAR_REENTRY。
        3. 多單平倉後的空單反手，須由當根實體直接跌破 KC 中軌且 KC 轉空確認。
    """
    try:
        import time
        is_flip = decision_type in (
            'AUTHORIZED_BY_PEAK_FLIP_SHORT',
            'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
            'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
            'CLIMAX_REVERSAL_FLIP',
            'AUTHORIZED_TOP_REVERSAL_SHORT',
            'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
            'TOP_WATERFALL_FLIP',
            BEARISH_INSTANT_BREAKOUT_CODE,
            BULLISH_INSTANT_BREAKOUT_CODE,
        )
        if account is not None and symbol:
            last_closed_ts = float(getattr(account, 'last_closed_at', {}).get(symbol, 0.0))
            last_side = str(getattr(account, 'last_closed_side', {}).get(symbol, '')).upper()
            now_ts = time.time()
            # Apply the timestamp cooldown before frame-dependent anti-churn
            # checks so an active same-side cooldown has deterministic priority.
            cooldown_period = 180.0
            try:
                matching_closes = [
                    t for t in getattr(account, 'trades', [])
                    if t.get('symbol') == symbol
                    and t.get('action') in ('CLOSE_LONG', 'CLOSE_SHORT')
                    and t.get('status') == 'CLOSED'
                ]
                if matching_closes:
                    last_trade = matching_closes[0]
                    pnl = float(last_trade.get('pnl', last_trade.get('realized_pnl', 0.0)))
                    reason_upper = str(last_trade.get('reason', '')).upper()
                    is_profit_exit = (
                        pnl > 0
                        or any(k in reason_upper for k in (
                            'PROFIT', 'TAKE-PROFIT', 'TP', 'RATCHET',
                            'PEAK', 'VALLEY', 'STAGE', 'LOCK', '獲利'
                        ))
                    )
                    is_stop_loss = (
                        pnl < 0
                        or any(k in reason_upper for k in (
                            'STOP', 'HARD_STOP', 'STOP-LOSS', 'WATERFALL', 'SL', '止損'
                        ))
                    )
                    if is_profit_exit and not is_stop_loss:
                        cooldown_period = 60.0
            except Exception:
                cooldown_period = 180.0

            if now_ts - last_closed_ts < cooldown_period and last_side == side:
                return False, "BLOCKED_BY_COOLDOWN_SAME_SIDE"
            churn_problem = same_side_anti_churn_problem(
                account, symbol, frame, side, quote,
            )
            if churn_problem:
                return False, churn_problem
            same_bar_problem = same_bar_close_entry_problem(
                account, symbol, frame, side,
            )
            if same_bar_problem:
                return False, same_bar_problem
            opposite_cooldown_problem = (
                None if decision_type in (
                    BEARISH_INSTANT_BREAKOUT_CODE,
                    BULLISH_INSTANT_BREAKOUT_CODE,
                ) else opposite_side_close_cooldown_problem(
                    account, symbol, frame, side,
                )
            )
            if opposite_cooldown_problem:
                return False, opposite_cooldown_problem
            if (side == 'SHORT' and is_flip and last_side == 'LONG'
                    and decision_type != BEARISH_INSTANT_BREAKOUT_CODE):
                from core.services.strategies.outer_strategy import kc_bearish_midline_turn
                if not kc_bearish_midline_turn(frame, quote):
                    return False, 'BLOCKED_SHORT_FLIP_WITHOUT_BEARISH_KC_TURN'
        # Cooldown is a timestamp-only safety gate. Evaluate it before market
        # data gates so an active cooldown is reported consistently even when
        # the caller has no candle frame available yet.
        direction_problem = live_ma5_entry_direction_problem(
            frame, quote, side,
        )
        if direction_problem:
            return False, direction_problem
        if frame is None or len(frame) < 2:
            return False, "BLOCKED_BY_MA5_SLOPE_INVALID"
        if side == 'LONG':
            bearish_candle_problem = long_large_bearish_candle_problem(frame, quote)
            if bearish_candle_problem:
                return False, bearish_candle_problem
        curr = frame.iloc[-1]
        prev = frame.iloc[-2]
        quote = float(quote)
        market_structure_problem = entry_market_structure_problem(
            frame, quote, side, entry_phase=entry_phase,
        )
        if market_structure_problem:
            return False, market_structure_problem
        
        # 1. 計算即時 MA5 斜率
        live_close = float(curr.get('close', quote))
        is_closed = bool(curr.get('is_closed', False))
        curr_ma5 = float(curr.get('ma5', live_close))
        prev_ma5 = float(prev.get('ma5', curr_ma5))
        
        if is_closed:
            live_ma5 = curr_ma5
        else:
            live_ma5 = curr_ma5 + (quote - live_close) / 5.0
            
        ma5_slope = live_ma5 - prev_ma5
        
        # 2. ATR normalized MA5 slope threshold; only head-chop flips may be shallow.
        atr = float(prev.get('atr', curr.get('atr', 1.0)))
        if atr <= 0:
            atr = 1.0
        slope_atr = live_ma5_slope_atr(frame, quote)
        slope_flip_exempt = decision_type in (
            CLIMAX_REVERSAL_FLIP_CODE, TOP_WATERFALL_FLIP_CODE, "TOP_WATERFALL_FLIP",
        )
        is_midline_break = (
            decision_type in (
                'AUTHORIZED_BY_MIDLINE_BREAK_SHORT',
                'AUTHORIZED_BY_MIDLINE_BREAK_LONG',
                MOMENTUM_BURST_LONG_CODE,
                MOMENTUM_BURST_SHORT_CODE,
                GOLDEN_CROSS_FAST_LONG_CODE,
                DEATH_CROSS_FAST_SHORT_CODE,
            )
            or entry_phase in (
                'MA_GOLDEN_CROSS_FAST_LANE',
                'MA_DEATH_CROSS_FAST_LANE',
                'KC_MOMENTUM_BURST_LONG',
                'KC_MOMENTUM_BURST_SHORT',
                'KC_MIDLINE_BREAK_LONG',
                'KC_MIDLINE_BREAK_SHORT',
            )
            or entry_phase == 'POST_CLOSE_CONTINUATION_ENTRY'
        )
        is_trend_continuation = entry_phase == 'POST_CLOSE_CONTINUATION_ENTRY'
        is_confirmed_rail_breakout = (
            (decision_type in ('KC_2BAR_CONFIRM_LONG', 'KC_2BAR_CONFIRM_SHORT')
             and entry_phase == 'KC_2BAR_CLOSED_CONFIRM')
            or (decision_type == 'TRIGGER_A_KC_BREAKOUT'
                and entry_phase == 'KC_THREE_BAR_BREAKOUT')
            or (decision_type in LIVE_BODY_BREAKOUT_CODES)
            or decision_type == REALTIME_RAIL_BREACH_SHORT_CODE
        )
        is_strong_breakout = (
            (side == 'LONG' and pipeline.has_strong_bullish_breakout(frame, quote))
            or (side == 'SHORT' and (
                pipeline.has_strong_bearish_breakout(frame, quote)
                or pipeline.has_bearish_chop_breakout_override(frame, quote)
            ))
        )
        if slope_atr is None:
            return False, "BLOCKED_BY_MA5_SLOPE_INVALID"
        if is_trend_continuation:
            if ma5_slope < 0:
                return False, "BLOCKED_BY_MA5_SLOPE_DOWN"
        elif is_midline_break:
            if ((side == 'LONG' and ma5_slope <= 0)
                    or (side == 'SHORT' and ma5_slope >= 0)):
                return False, (
                    "BLOCKED_BY_MA5_SLOPE_DOWN" if side == 'LONG'
                    else "BLOCKED_BY_MA5_SLOPE_UP"
                )
        elif not slope_flip_exempt:
            if side == 'LONG' and ma5_slope < 0:
                return False, "BLOCKED_BY_MA5_SLOPE_DOWN"
            if side == 'SHORT' and ma5_slope > 0:
                return False, "BLOCKED_BY_MA5_SLOPE_UP"
            if not is_strong_breakout:
                slope_problem = live_ma5_slope_problem(frame, quote, side)
                if slope_problem:
                    return False, slope_problem

        kc_upper = float(curr.get('kc_upper', 0.0))
        kc_lower = float(curr.get('kc_lower', 0.0))
        open_p = float(curr.get('open', live_close))
        curr_is_bearish = quote < open_p or live_close < open_p
        curr_is_bullish = quote > open_p or live_close > open_p
        
        prev_open = float(prev.get('open', 0.0))
        prev_close = float(prev.get('close', 0.0))
        prev_is_bearish = prev_close < prev_open
        prev_is_bullish = prev_close > prev_open
        
        from core.intelligence.spatial_brain import SpatialBrain
        spatial_ctx = SpatialBrain.analyze(frame, quote)
        is_explosive = (spatial_ctx.state == 'EXPLOSIVE_EXPANSION')

        is_shadow_retest = (decision_type == 'AUTHORIZED_SHADOW_RETEST_ENTRY')

        from core.gates.meme_coin_gate import MemeCoinGate
        is_meme = (decision_type in MEME_ENTRY_CODES) or (bool(symbol) and MemeCoinGate.is_meme_coin(symbol))

        if side == "LONG":
            # 1. 【MA5 向下，100% 絕對禁止開多（向下不開多）】
            if not is_shadow_retest:
                if ma5_slope < 0:
                    return False, "BLOCKED_BY_MA5_SLOPE_DOWN"
                
            # 2. 獲利空間審核：ALLOW_EXPLOSIVE_BREAKOUT / is_shadow_retest / is_midline_break / is_meme 100% 豁免上軌空間限制
            if (not is_explosive and not is_shadow_retest and not is_midline_break
                    and not is_confirmed_rail_breakout and not is_meme):
                is_volume_breakout = (quote > kc_upper and (quote - open_p) >= 0.5 * atr)
                if not is_volume_breakout:
                    if kc_upper > 0 and (kc_upper - quote) < 0.5 * atr:
                        return False, "REJECT_INSUFFICIENT_ROOM_TO_UPPER_KC"
                    
            # 3. 逆勢長陰不接刀：若前一根或當前為實體陰線且價格跌破 MA5，嚴禁在回落中抄底開多！
            # 均線下影線回踩蓄勢開多 (AUTHORIZED_SHADOW_RETEST_ENTRY) 與中軌突破豁免「紅 K 一票否決」
            if (not is_confirmed_rail_breakout and not is_shadow_retest
                    and not is_midline_break and not is_meme and (curr_is_bearish or prev_is_bearish)
                    and quote < live_ma5):
                return False, "REJECT_FALLING_KNIFE_BEARISH_BELOW_MA5"

            # 4. 【即時破軌開多嚴格限制條件（Live Body Breakout Strict Filter）】：
            is_breakout_long = (
                decision_type in LIVE_BODY_BREAKOUT_CODES
                or decision_type in ('TRIGGER_A_KC_BREAKOUT', 'AUTHORIZED_REALTIME_BREAKOUT', 'KC_2BAR_CONFIRM_LONG')
                or entry_phase in ('KC_LIVE_OUTER_BREAKOUT', 'KC_THREE_BAR_BREAKOUT')
            )
            if is_breakout_long:
                # 1) 禁止逆勢破軌（KC 趨勢強制對齊：kc_trend != 'DOWN'）
                channel_st = str(curr.get('channel_state', '')).strip().upper()
                closed_candles = closed_entry_candles(frame)
                is_kc_down = (
                    channel_st in ('KC向下', 'KC_TREND_DOWN', 'DOWN', 'DOWNTREND', 'BEARISH', 'SHORT')
                    or (len(closed_candles) >= 2 and float(closed_candles.iloc[-1].get('kc_middle', 0.0)) < float(closed_candles.iloc[-2].get('kc_middle', 0.0)))
                )
                if is_kc_down:
                    return False, "BLOCKED_BY_KC_TREND_DOWN"

                # 2) 棕色 MA15 斜率不得為負（MA15 必須走平或向上，禁止在均線向下壓制時開多）
                prev_ma15_val = float(prev.get('ma15', 0.0))
                curr_ma15_val = float(curr.get('ma15', prev_ma15_val))
                live_ma15_val = curr_ma15_val if is_closed else curr_ma15_val + (quote - live_close) / 15.0
                if prev_ma15_val > 0 and live_ma15_val < prev_ma15_val:
                    return False, "BLOCKED_BY_MA15_SLOPE_DOWN"

                # 3) 破軌 K 棒實體必須 >= 0.6 ATR 的飽滿大陽線
                body_long = quote - open_p
                if atr > 0 and body_long < 0.6 * atr:
                    return False, "BLOCKED_INSUFFICIENT_BREAKOUT_BODY"

                # 4) 排除假突破 / 上影線阻力形態（上影線 >= 實體 0.7 倍）
                high_p = max(float(curr.get('high', quote)), quote)
                upper_wick_p = high_p - quote
                if upper_wick_p >= 0.7 * body_long:
                    return False, "BLOCKED_UPPER_WICK_RESISTANCE"

            # 5. 【高檔避雷針防追高限制（Anti-Top Chase）】：
            # 若當前 1m K 棒已遠離均線（價格高出 MA5 超過 0.6 ATR），且拉出上影線 >= 實體 0.8 倍，禁止在高點盲目追多！
            high_p = max(float(curr.get('high', quote)), quote)
            body_p = max(quote - open_p, 0.0)
            upper_wick_p = high_p - max(open_p, quote)
            if atr > 0 and (quote - live_ma5) > 0.6 * atr:
                if upper_wick_p >= 0.8 * body_p and upper_wick_p >= 0.3 * atr:
                    return False, "BLOCKED_ANTI_TOP_CHASE_PINBAR"
                
        elif side == "SHORT":
            # 1. 【MA5 向上，100% 絕對禁止開空（向上不開空）】
            if ma5_slope > 0:
                return False, "BLOCKED_BY_MA5_SLOPE_UP"
                
            # 2. 獲利空間審核：ALLOW_EXPLOSIVE_BREAKOUT / is_flip / is_midline_break / fast_cross / breakout / is_meme 100% 豁免下軌空間限制
            is_exempt_space = (
                is_explosive or is_flip or is_midline_break or is_confirmed_rail_breakout
                or is_meme
                or decision_type in (DEATH_CROSS_FAST_SHORT_CODE, 'TRIGGER_B_MA_CROSS', 'TRIGGER_A_KC_BREAKOUT', 'KC_LIVE_BODY_BREAKOUT_SHORT')
            )
            if not is_exempt_space:
                is_volume_breakout = (quote < kc_lower and (open_p - quote) >= 0.5 * atr)
                if not is_volume_breakout:
                    if kc_lower > 0 and quote > kc_lower and (quote - kc_lower) < 0.5 * atr:
                        return False, "REJECT_INSUFFICIENT_ROOM_TO_LOWER_KC"
                    
            # 3. 陽線回抽且價格站上 MA5 時嚴禁追空！
            if (not is_confirmed_rail_breakout and not is_midline_break
                    and (curr_is_bullish or prev_is_bullish) and quote > live_ma5):
                return False, "REJECT_BOUNCE_BULLISH_ABOVE_MA5"

            # 4. 【KC 通道趨勢強制對齊（KC 沒往下絕對禁止開空）】
            channel_st = str(curr.get('channel_state', '')).strip().upper()
            closed_candles = closed_entry_candles(frame)
            is_kc_up = (
                channel_st in ('KC向上', 'KC_TREND_UP', 'UP', 'UPTREND', 'BULLISH', 'LONG')
                or (len(closed_candles) >= 2 and float(closed_candles.iloc[-1].get('kc_middle', 0.0)) > float(closed_candles.iloc[-2].get('kc_middle', 0.0)))
            )
            if is_kc_up:
                return False, "BLOCKED_BY_KC_TREND_UP"
                
        return True, None
    except Exception:
        return False, "BLOCKED_BY_MA5_SLOPE_INVALID"


def evaluate_entry_contract(frame, price=None, code=None, *, account=None, symbol="", diagnostics=None):
    def reject(reason):
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics["reason"] = reason
        return None

    def authorize(decision, quote):
        # Normalize the execution snapshot fields for every entry authority.
        # Meme breakout candidates provide `price` but older routes omit
        # execution-context fields; normalize them here so the engine can
        # build its final order context for either long or short entries.
        decision.setdefault('close_price', quote)
        confirmation_bar_id = decision.get('confirmation_bar_id')
        decision.setdefault('breakout_bar_id', confirmation_bar_id)
        decision.setdefault('pair_confirmation_bar_id', confirmation_bar_id)
        passed, reason = validate_ma_alignment(frame, decision.get('side'), quote)
        if not passed:
            return reject(reason)

        # ──【順勢交易鐵律】趨勢是空頭不要開多，趨勢是多頭不要開空──
        from core.gates.meme_coin_gate import MemeCoinGate
        market_trend = MemeCoinGate.get_market_trend(frame, quote)
        side = decision.get('side')
        if side == 'LONG' and market_trend == 'BEARISH':
            return reject('BLOCKED_LONG_ON_BEARISH_TREND')
        if side == 'SHORT' and market_trend == 'BULLISH':
            return reject('BLOCKED_SHORT_ON_BULLISH_TREND')
        if decision.get('side') == 'LONG' and decision.get('type') not in MEME_ENTRY_CODES:
            top_problem = long_top_chase_problem(frame, quote)
            if top_problem:
                return reject(top_problem)
            if decision.get('entry_phase') in (
                    'POST_CLOSE_CONTINUATION_ENTRY', 'KC_LIVE_OUTER_BREAKOUT',
                    'KC_LIVE_PATTERN_BREAKOUT'):
                reentry_problem = long_post_impulse_reentry_problem(frame, quote)
                if reentry_problem:
                    return reject(reentry_problem)
        direction_problem = live_ma5_entry_direction_problem(
            frame, quote, decision.get('side'),
        )
        if direction_problem:
            return reject(direction_problem)
        is_post_close_continuation = (
            decision.get('type') == 'TRIGGER_C_CONTINUATION'
            and decision.get('entry_phase') == 'POST_CLOSE_CONTINUATION_ENTRY'
        )
        if (decision.get('type') not in FLAT_LINE_BREAKOUT_TYPES
                and not is_post_close_continuation
                and decision.get('type') not in (
                    BEARISH_INSTANT_BREAKOUT_CODE,
                    BULLISH_INSTANT_BREAKOUT_CODE,
                )):
            return reject('BLOCKED_ENTRY_NOT_OUTER_RAIL_BREAKOUT')
        strategy_type = entry_strategy_type(decision.get('entry_phase'))
        if strategy_type is not None:
            decision['strategy_type'] = strategy_type

        # Ordinary KC breakouts and post-close re-entries share the same
        # three-bar authorization at the final order boundary.
        entry_phase = decision.get('entry_phase')
        requires_three_bar_gate = (
            entry_phase in (
                'KC_2BAR_CLOSED_CONFIRM', 'KC_LIVE_OUTER_BREAKOUT',
                'KC_LIVE_PATTERN_BREAKOUT', 'KC_THREE_BAR_BREAKOUT',
                'POST_CLOSE_CONTINUATION_ENTRY', 'KC_CONTINUATION_ENTRY',
            )
            or str(decision.get('type', '')).startswith('CLOSED_TREND_BREAKOUT_')
        )
        if requires_three_bar_gate:
            rail_problem = three_bar_rail_gate_problem(
                frame, quote, decision.get('side'),
            )
            if rail_problem:
                return reject(rail_problem)

        shape_problem = entry_candle_shape_problem(frame, quote, decision)
        if shape_problem:
            return reject(shape_problem)

        market_structure_problem = entry_market_structure_problem(
            frame, quote, decision.get('side'),
            entry_phase=decision.get('entry_phase'),
            strategy_type=decision.get('strategy_type'),
        )
        if market_structure_problem:
            return reject(market_structure_problem)

        cross_chop_problem = (
            None if decision.get('entry_phase') in (
                'KC_2BAR_CLOSED_CONFIRM', 'KC_LIVE_OUTER_BREAKOUT',
                'KC_LIVE_PATTERN_BREAKOUT', 'MEME_BREAKOUT_ENTRY',
                'MEME_AMBUSH_ENTRY', 'MEME_PYRAMID_ADDITION',
            )
            or decision.get('type') in MEME_ENTRY_CODES
            or decision.get('type') == REALTIME_RAIL_BREACH_SHORT_CODE
            or decision.get('type') in (
                BEARISH_INSTANT_BREAKOUT_CODE,
                BULLISH_INSTANT_BREAKOUT_CODE,
                GOLDEN_CROSS_FAST_LONG_CODE,
                DEATH_CROSS_FAST_SHORT_CODE,
                'TRIGGER_B_MA_CROSS',
                'TRIGGER_A_KC_BREAKOUT',
                'TRIGGER_C_CONTINUATION',
                'KC_LIVE_BODY_BREAKOUT_LONG',
                'KC_LIVE_BODY_BREAKOUT_SHORT',
                'CLOSED_BODY_BREAKOUT_LONG',
                'CLOSED_BODY_BREAKOUT_SHORT',
                'RE_ENTRY_LONG',
                'RE_ENTRY_SHORT',
            )
            else ma5_ma15_cross_chop_problem(
                frame, quote, symbol=symbol, log_result=True,
                side=decision.get('side'),
            )
        )
        if cross_chop_problem:
            return reject(cross_chop_problem)

        # 強制 Pre-Flight 安全門閥審核
        passed, pre_flight_reason = pre_flight_safety_check(
            frame, quote, decision.get('side'), decision_type=decision.get('type'),
            account=account, symbol=symbol,
            entry_phase=decision.get('entry_phase'),
        )
        if not passed:
            return reject(pre_flight_reason)

        if not decision.get('_is_authorized'):
            route = decision.get('type')
            if code is not None and route is not None and route != code and not (
                code in ('AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP')
                and route in ('AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP')
            ):
                return reject('BLOCKED_ENTRY_ROUTE_NOT_AUTHORIZED')
            # KC pending and the currently authorized live/continuation/reversal
            # routes pass through this shared contract only when the requested route
            # matches the exact decision type. Common legacy fast lanes remain
            # fail-closed unless revalidated by the same code path.
            if route not in KC_PENDING_CODES | LEGACY_ROUTE_CODES | PIPELINE_ENTRY_TYPES:
                return reject('BLOCKED_ENTRY_ROUTE_NOT_AUTHORIZED')
            decision['_is_authorized'] = True

        if decision.get('type') in PIPELINE_ENTRY_TYPES:
            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(decision)
            return decision

        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(decision)
        return decision


    reject("WAIT_VALID_ENTRY_DATA")
    try:
        if frame is None or frame.empty or frame.attrs.get('timeframe_ms', 60000) != 60000:
            return None

        quote = price if price is not None else float(frame.iloc[-1].close)
        forming_bar = not bool(frame.iloc[-1].get('is_closed', True))
        closed = closed_entry_candles(frame)

        # 【均線相對位置的絕對限制】第一道開倉閘門底線（已知方向或指定訊號代碼）
        known_side = (
            'LONG' if code and 'LONG' in code else
            'SHORT' if code and 'SHORT' in code else
            None
        )
        if known_side:
            passed, reason = validate_ma_alignment(frame, known_side, quote)
            if not passed:
                return reject(reason)

        if code is not None and code not in ENTRY_CODES:
            return reject("BLOCKED_OBSOLETE_ENTRY_SIGNAL")

        # ── 妖幣專屬極速破軌、延續補倉與提前埋伏入口（Meme Coin Authority）──
        from core.gates.meme_coin_gate import MemeCoinGate
        if symbol and MemeCoinGate.is_meme_coin(symbol):
            if code is None or code in MEME_ENTRY_CODES:
                target_sides = ['LONG', 'SHORT']
                if known_side:
                    target_sides = [known_side]
                elif code:
                    if 'LONG' in code:
                        target_sides = ['LONG']
                    elif 'SHORT' in code:
                        target_sides = ['SHORT']

                has_pos = account is not None and symbol in getattr(account, 'positions', {})
                pos_side = getattr(account, 'positions', {}).get(symbol, {}).get('side') if has_pos else None

                # 1. 若已有底倉，審查「破軌加倉做滿 70%」
                if has_pos and pos_side in target_sides:
                    pyr_passed, pyr_reason, pyr_meta = MemeCoinGate.evaluate_pyramid_addition(
                        frame, quote, pos_side, symbol=symbol,
                    )
                    if pyr_passed and (code is None or code == pyr_reason):
                        cand_bar_id = float(frame.iloc[-1].get('timestamp', 0))
                        decision = {
                            'action': 'ENTER',
                            'type': pyr_reason,
                            'side': pos_side,
                            'price': quote,
                            'confirmation_bar_id': cand_bar_id,
                            'breakout_bar_id': cand_bar_id,
                            'pending_signal_id': f"{pyr_reason}:{pos_side}:{cand_bar_id}",
                            'entry_phase': 'MEME_PYRAMID_ADDITION',
                            'reason': pyr_reason,
                            'entry_atr': float(closed.iloc[-1]['atr']) if len(closed) > 0 else 1.0,
                            'size_fraction': (pyr_meta or {}).get('suggested_size_ratio', 0.65),
                            '_is_authorized': True,
                        }
                        return authorize(decision, quote)

                # 2. 若無持倉，優先審查「兩根流極速破軌與延續開倉（強制上車）」
                if not has_pos:
                    for s in target_sides:
                        m_passed, m_reason, m_meta = MemeCoinGate.evaluate_meme_entry(
                            frame, quote, s, symbol=symbol,
                        )
                        if m_passed and (code is None or code == m_reason):
                            cand_bar_id = float(frame.iloc[-1].get('timestamp', 0))
                            decision = {
                                'action': 'ENTER',
                                'type': m_reason,
                                'side': s,
                                'price': quote,
                                'confirmation_bar_id': cand_bar_id,
                                'breakout_bar_id': cand_bar_id,
                                'pending_signal_id': f"{m_reason}:{s}:{cand_bar_id}",
                                'entry_phase': 'MEME_BREAKOUT_ENTRY',
                                'reason': m_reason,
                                'entry_atr': float(closed.iloc[-1]['atr']) if len(closed) > 0 else 1.0,
                                'size_fraction': 1.0,
                                '_is_authorized': True,
                            }
                            passed_pf, pf_reason = pre_flight_safety_check(
                                frame, quote, s, decision_type=m_reason,
                                account=account, symbol=symbol,
                                entry_phase='MEME_BREAKOUT_ENTRY',
                            )
                            if not passed_pf:
                                return reject(pf_reason)
                            return authorize(decision, quote)

                    # 3. 若未觸發破軌，審查「提前埋伏 30% 底倉」
                    for s in target_sides:
                        amb_passed, amb_reason, amb_meta = MemeCoinGate.evaluate_ambush_entry(
                            frame, quote, s, symbol=symbol,
                        )
                        if amb_passed and (code is None or code == amb_reason):
                            cand_bar_id = float(frame.iloc[-1].get('timestamp', 0))
                            decision = {
                                'action': 'ENTER',
                                'type': amb_reason,
                                'side': s,
                                'price': quote,
                                'confirmation_bar_id': cand_bar_id,
                                'breakout_bar_id': cand_bar_id,
                                'pending_signal_id': f"{amb_reason}:{s}:{cand_bar_id}",
                                'entry_phase': 'MEME_AMBUSH_ENTRY',
                                'reason': amb_reason,
                                'entry_atr': float(closed.iloc[-1]['atr']) if len(closed) > 0 else 1.0,
                                'size_fraction': (amb_meta or {}).get('suggested_size_ratio', 0.35),
                                '_is_authorized': True,
                            }
                            passed_pf, pf_reason = pre_flight_safety_check(
                                frame, quote, s, decision_type=amb_reason,
                                account=account, symbol=symbol,
                                entry_phase='MEME_AMBUSH_ENTRY',
                            )
                            if not passed_pf:
                                return reject(pf_reason)
                            return authorize(decision, quote)

                if code is not None and code in MEME_ENTRY_CODES:
                    return reject("WAIT_MEME_ENTRY_CONDITIONS")

        # The live scanner and final order firewall share this closed set of
        # entry authorities. Do not evaluate legacy, continuation, or reversal
        # candidates when no general breakout is present.
        if code in (None, "TRIGGER_A_KC_BREAKOUT"):
            if not forming_bar:
                return reject("BLOCKED_GENERAL_BREAKOUT_REQUIRES_LIVE_THIRD_BAR")
            candidate = evaluate_three_bar_outer_breakout(
                frame, quote, symbol=symbol,
            )
            if candidate is None:
                return reject("WAIT_GENERAL_THREE_BAR_OUTER_BREAKOUT")
            side = candidate["side"]
            passed, gate_reason = check_entry_gates(
                account, symbol, closed, side, "TRIGGER_A_KC_BREAKOUT",
            )
            if not passed:
                return reject(gate_reason)
            duplicate = any(
                trade.get("symbol") == symbol
                and trade.get("action") in ("OPEN_LONG", "OPEN_SHORT")
                and (trade.get("entry_snapshot") or {}).get("pending_signal_id")
                    == candidate["pending_signal_id"]
                for trade in getattr(account, "trades", [])
            )
            if duplicate:
                return reject("BLOCKED_KC_BREAKOUT_ALREADY_FILLED")
            shadow_problem = excessive_upper_shadow_problem(
                frame, quote, side, trigger_type="TRIGGER_A_KC_BREAKOUT",
            )
            if shadow_problem:
                return reject(shadow_problem)
            return authorize(candidate, quote)

        if code in (TOP_WATERFALL_FLIP_CODE, "TOP_WATERFALL_FLIP"):
            position = getattr(account, "positions", {}).get(symbol) if account else None
            open_long = bool(
                position and str(position.get("side", "")).upper() == "LONG"
            )
            closed_at = float(
                getattr(account, "last_closed_at", {}).get(symbol, 0.0)
                if account else 0.0
            )
            matched_waterfall_close = bool(
                account
                and not position
                and str(getattr(account, "last_closed_side", {}).get(symbol, "")).upper() == "LONG"
                and math.isfinite(closed_at)
                and 0 <= time.time() - closed_at <= 30
                and any(
                    trade.get("symbol") == symbol
                    and trade.get("action") == "CLOSE_LONG"
                    and "EXIT_LONG_ON_TOP_WATERFALL_DUMP" in str(trade.get("reason", ""))
                    for trade in reversed(getattr(account, "trades", []))
                )
            )
            if not (open_long or matched_waterfall_close):
                return reject("BLOCKED_TOP_WATERFALL_FLIP_REQUIRES_OPEN_LONG")
            decision = pipeline.detect_top_waterfall_flip_short(
                frame, quote, side="SHORT",
            )
            if decision is None:
                return reject("BLOCKED_TOP_WATERFALL_FLIP_EVIDENCE")
            pipeline_decision = pipeline.authorize(
                decision, frame, quote, symbol=symbol, account=account,
                requested_side="SHORT", diagnostics=diagnostics,
            )
            if pipeline_decision is None:
                reason = (diagnostics or {}).get(
                    "reason", "BLOCKED_TOP_WATERFALL_PIPELINE",
                )
                return reject(reason)
            return authorize(pipeline_decision, quote)

        # A live lower-rail breach is its own entry authority. It does not
        # inherit the separate two-closed-bar breakout confirmation rule.
        if forming_bar and code in (None, REALTIME_RAIL_BREACH_SHORT_CODE):
            realtime_short = evaluate_realtime_short_rail_breach(
                frame, quote, symbol=symbol,
            )
            if code == REALTIME_RAIL_BREACH_SHORT_CODE and realtime_short is None:
                return reject('BLOCKED_REALTIME_SHORT_BREACH_NOT_QUALIFIED')
            if realtime_short is not None:
                if realtime_short.get('action') != 'ENTER':
                    return reject(realtime_short.get('reason',
                                                       'BLOCKED_REALTIME_SHORT_BREACH'))
                if code is not None and code != realtime_short.get('type'):
                    return reject('BLOCKED_ENTRY_ROUTE_NOT_AUTHORIZED')
                passed, gate_reason = check_entry_gates(
                    account, symbol, closed, 'SHORT',
                    REALTIME_RAIL_BREACH_SHORT_CODE,
                )
                if not passed:
                    return reject(gate_reason)
                return authorize(realtime_short, quote)

        live_pattern_candidate = None
        if forming_bar and code in (None, *KC_REALTIME_PATTERN_CODES):
            pattern = evaluate_kc_live_pattern_entry(
                closed, quote, frame.iloc[-1],
                code=code if code in KC_REALTIME_PATTERN_CODES else None,
                symbol=symbol,
            )
            if pattern.get('action') == 'ENTER':
                live_pattern_candidate = pattern
        live_body_candidate = None
        if (forming_bar and code in (
                None, 'KC_LIVE_BODY_BREAKOUT_LONG', 'KC_LIVE_BODY_BREAKOUT_SHORT')):
            requested_side = (
                'LONG' if code == 'KC_LIVE_BODY_BREAKOUT_LONG' else
                'SHORT' if code == 'KC_LIVE_BODY_BREAKOUT_SHORT' else None
            )
            live_body_candidate = evaluate_live_body_breakout(
                frame, quote, symbol=symbol, requested_side=requested_side,
            )
        is_instant_reverse = code in (
            BEARISH_INSTANT_BREAKOUT_CODE,
            BULLISH_INSTANT_BREAKOUT_CODE,
        )
        pending = None
        if code is None or code in KC_PENDING_CODES:
            pending = evaluate_kc_pending_entry(
                closed, quote,
                code=code if code in KC_PENDING_CODES else None,
                symbol=symbol,
                live=frame.iloc[-1] if forming_bar else None,
            )
        confirmed_pending_breakout = bool(
            pending and pending.get('action') == 'ENTER'
        )
        post_close_diagnostics = {}
        post_close_candidate = (
            evaluate_post_close_continuation(
                frame, quote, symbol, account,
                diagnostics=post_close_diagnostics,
            )
            if forming_bar and code in (None, 'TRIGGER_C_CONTINUATION')
            else None
        )
        live_golden_cross = evaluate_golden_cross_fast_lane(
            frame, quote, symbol,
        )
        live_death_cross = evaluate_death_cross_fast_lane(
            frame, quote, symbol,
        )
        active_fast_cross = (
            live_golden_cross
            if code in (None, GOLDEN_CROSS_FAST_LONG_CODE)
            and live_golden_cross is not None
            else live_death_cross
            if code in (None, DEATH_CROSS_FAST_SHORT_CODE)
            and live_death_cross is not None
            else None
        )
        cross_chop_problem = (
            None if (confirmed_pending_breakout or is_instant_reverse
                     or live_body_candidate is not None
                     or live_pattern_candidate is not None
                     or active_fast_cross is not None) else
            ma5_ma15_cross_chop_problem(
                frame, quote, symbol=symbol, log_result=True,
                side=(
                    active_fast_cross.get('side')
                    if active_fast_cross is not None
                    else 'LONG' if code == GOLDEN_CROSS_FAST_LONG_CODE
                    else 'SHORT' if code == DEATH_CROSS_FAST_SHORT_CODE
                    else None
                ),
            )
        )
        if cross_chop_problem:
            return reject(cross_chop_problem)
        kc_problem = strict_kc_entry_problem(
            frame, quote,
            side=(
                pending.get('side') if confirmed_pending_breakout
                else live_pattern_candidate.get('side') if live_pattern_candidate
                else live_body_candidate.get('side') if live_body_candidate
                else post_close_candidate.get('side') if post_close_candidate
                else None
            ),
            strategy_type=(
                'BREAKOUT' if (confirmed_pending_breakout or live_body_candidate
                               or live_pattern_candidate) else
                entry_strategy_type(post_close_candidate.get('entry_phase'))
                if post_close_candidate else None
            ),
        )
        if kc_problem:
            return reject(kc_problem)
        if code == GOLDEN_CROSS_FAST_LONG_CODE and live_golden_cross is None:
            return reject('BLOCKED_GOLDEN_CROSS_FAST_LANE_NOT_QUALIFIED')
        if code == DEATH_CROSS_FAST_SHORT_CODE and live_death_cross is None:
            return reject('BLOCKED_DEATH_CROSS_FAST_LANE_NOT_QUALIFIED')
        momentum_burst = (
            pipeline.detect_momentum_burst(frame, quote, side='LONG')
            or pipeline.detect_momentum_burst(frame, quote, side='SHORT')
        )
        live_breakout = pipeline.detect_realtime_breakout(frame, quote)
        verified_live_breakout = bool(
            (live_breakout and live_breakout.get('ma5_direction_aligned') is True)
            or live_body_candidate is not None
            or live_pattern_candidate is not None
            or pipeline.has_bearish_chop_breakout_override(frame, quote)
            or active_fast_cross is not None
            or momentum_burst is not None
            or post_close_candidate is not None
            or confirmed_pending_breakout
        )
        midline_break = (
            pipeline.detect_midline_break_short(frame, quote)
            or pipeline.detect_midline_break_long(frame, quote)
        )
        is_midline_breakout = bool(
            midline_break is not None
            or active_fast_cross is not None
            or momentum_burst is not None
            or post_close_candidate is not None
        )
        is_flip_or_shadow = (
            code in ('AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP',
                     CLIMAX_REVERSAL_FLIP_CODE, AUTHORIZED_SHADOW_RETEST_ENTRY_CODE,
                     'SHADOW_RETEST_ENTRY',
                     'AUTHORIZED_BY_MIDLINE_BREAK_SHORT',
                     'AUTHORIZED_BY_MIDLINE_BREAK_LONG',
                     MOMENTUM_BURST_LONG_CODE,
                     MOMENTUM_BURST_SHORT_CODE,
                     'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
                     'AUTHORIZED_BY_PEAK_FLIP_SHORT',
                     'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
                     'AUTHORIZED_TOP_REVERSAL_SHORT')
            or is_instant_reverse
        )
        regime_problem = pipeline.market_regime_problem(symbol, frame, quote)
        if regime_problem and not verified_live_breakout and not is_flip_or_shadow and not is_midline_breakout:
            return reject(regime_problem)

        is_outer_rail_breakout = (
            code in FLAT_LINE_BREAKOUT_TYPES
            or live_body_candidate is not None
            or live_pattern_candidate is not None
            or code == CLIMAX_REVERSAL_FLIP_CODE
            or verified_live_breakout
            or is_midline_breakout
            or confirmed_pending_breakout
        )
        if (not confirmed_pending_breakout and active_fast_cross is None
                and live_body_candidate is None
                and live_pattern_candidate is None
                and code not in (CLIMAX_REVERSAL_FLIP_CODE, AUTHORIZED_SHADOW_RETEST_ENTRY_CODE,
                                 'AUTHORIZED_BY_MIDLINE_BREAK_SHORT',
                                 'AUTHORIZED_BY_MIDLINE_BREAK_LONG',
                                 MOMENTUM_BURST_LONG_CODE,
                                 MOMENTUM_BURST_SHORT_CODE)):
            try:
                chop_problem = pipeline.chop_lockout_problem(
                    frame, quote, allow_flat_lines=is_outer_rail_breakout,
                    allow_unknown_direction=(verified_live_breakout or is_midline_breakout),
                    allow_ma_tangle=active_fast_cross is not None,
                    signal_code=code,
                )
            except TypeError:
                chop_problem = pipeline.chop_lockout_problem(frame, quote)
            if chop_problem:
                return reject(chop_problem)

        if post_close_candidate is not None:
            return authorize(post_close_candidate, quote)

        if active_fast_cross is not None:
            closed = closed_entry_candles(frame)
            fast_side = active_fast_cross['side']
            fast_code = active_fast_cross['type']
            passed, gate_reason = check_entry_gates(
                account, symbol, closed, fast_side, fast_code,
            )
            if not passed:
                return reject(gate_reason)
            if any(
                trade.get('symbol') == symbol
                and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                and (trade.get('entry_snapshot') or {}).get('pending_signal_id')
                    == active_fast_cross['pending_signal_id']
                for trade in getattr(account, 'trades', [])
            ):
                return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')
            return authorize(active_fast_cross, quote)

        if code in (
            'AUTHORIZED_BY_MIDLINE_BREAK_SHORT',
            'AUTHORIZED_BY_MIDLINE_BREAK_LONG',
            MOMENTUM_BURST_LONG_CODE,
            MOMENTUM_BURST_SHORT_CODE,
        ):
            side = (
                'SHORT'
                if code in ('AUTHORIZED_BY_MIDLINE_BREAK_SHORT',
                            MOMENTUM_BURST_SHORT_CODE)
                else 'LONG'
            )
            decision = (
                pipeline.detect_momentum_burst(frame, quote, side=side)
                if code in MOMENTUM_BURST_CODES
                else pipeline.detect_midline_break_short(frame, quote, side='SHORT')
                if side == 'SHORT'
                else pipeline.detect_midline_break_long(frame, quote, side='LONG')
            )
            if decision is None:
                return reject('BLOCKED_MIDLINE_BREAK_EVIDENCE')
            pipeline_decision = pipeline.authorize(
                decision, frame, quote, symbol=symbol, account=account,
                requested_side=side, diagnostics=diagnostics,
            )
            if pipeline_decision is None:
                reason = (diagnostics or {}).get('reason', 'BLOCKED_MIDLINE_BREAK_PIPELINE')
                return reject(reason)
            return authorize(pipeline_decision, quote)

        if code in ('AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP'):
            decision = pipeline.detect_top_waterfall_flip_short(
                frame, quote, side='SHORT',
            )
            if decision is None:
                return reject('BLOCKED_TOP_WATERFALL_FLIP_EVIDENCE')
            pipeline_decision = pipeline.authorize(
                decision, frame, quote, symbol=symbol, account=account,
                requested_side='SHORT', diagnostics=diagnostics,
            )
            if pipeline_decision is None:
                reason = (diagnostics or {}).get('reason', 'BLOCKED_TOP_WATERFALL_PIPELINE')
                return reject(reason)
            return authorize(pipeline_decision, quote)

        if code == CLIMAX_REVERSAL_FLIP_CODE:
            decision = evaluate_climax_flip_entry(frame, quote, symbol, account)
            if decision is None:
                return reject('BLOCKED_CLIMAX_FLIP_NO_MATCHED_EXTREME_REVERSAL_CLOSE')
            return authorize(decision, quote)

        if code in ('AUTHORIZED_SHADOW_RETEST_ENTRY', 'SHADOW_RETEST_ENTRY'):
            decision = pipeline.detect_shadow_retest_long(
                frame, quote, side='LONG',
            )
            if decision is None:
                return reject('BLOCKED_SHADOW_RETEST_EVIDENCE')
            pipeline_decision = pipeline.authorize(
                decision, frame, quote, symbol=symbol, account=account,
                requested_side='LONG', diagnostics=diagnostics,
            )
            if pipeline_decision is None:
                reason = (diagnostics or {}).get('reason', 'BLOCKED_SHADOW_RETEST_PIPELINE')
                return reject(reason)
            return authorize(pipeline_decision, quote)

        # A qualified forming-bar rail break is time-sensitive. Evaluate it
        # before the generic pipeline can return a slower closed-bar candidate.
        if live_pattern_candidate is not None:
            side = live_pattern_candidate['side']
            meme_impulse = pipeline.has_meme_impulse_breakout(symbol, frame, quote)
            if not meme_impulse and not entry_trend_alignment_ready(frame, side):
                return reject('BLOCKED_KC_MA5_MA15_TREND_MISMATCH')
            direction_problem = entry_direction_problem(frame, quote, side)
            if direction_problem:
                return reject(direction_problem)
            if not live_ma3_direction_ready(frame, quote, side):
                return reject('BLOCKED_BY_LIVE_MA3_DIRECTION')
            ma5_direction = evaluate_live_ma5_direction(frame, quote, side)
            if ma5_direction is None:
                return reject('BLOCKED_STRICT_MA5_DIRECTION')
            live = frame.iloc[-1]
            live_ma5 = ma5_direction[1]
            live_ma15 = float(live['ma15']) + (float(quote) - float(live['close'])) / 15.0
            upper, lower = float(live['kc_upper']), float(live['kc_lower'])
            width = upper - lower
            max_width = float((closed.tail(20)['kc_upper'] - closed.tail(20)['kc_lower']).max())
            edge = upper if side == 'LONG' else lower
            if (not all(math.isfinite(value) and value > 0 for value in
                        (live_ma5, live_ma15, upper, lower, width, max_width))
                    or width <= 0 or width <= 0.25 * max_width):
                return reject('BLOCKED_STRICT_CHANNEL_CONVERGENCE')
            consecutive_momentum = has_consecutive_momentum_breakout(frame, quote, side)
            if not consecutive_momentum:
                min_spacing = (0.10 if meme_impulse else 0.25) * width
                if (abs(live_ma5 - live_ma15) < min_spacing
                        or abs(live_ma5 - edge) < min_spacing):
                    return reject('BLOCKED_STRICT_MA_SPACING')
            if account is not None and symbol in getattr(account, 'positions', {}):
                return reject('BLOCKED_BY_POSITION_GATE')
            if any(
                trade.get('symbol') == symbol
                and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                and (trade.get('entry_snapshot') or {}).get('pending_signal_id')
                    == live_pattern_candidate['pending_signal_id']
                for trade in getattr(account, 'trades', [])
            ):
                return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')
            return authorize(live_pattern_candidate, quote)

        live_breakout = live_body_candidate
        if (not bool(frame.iloc[-1].get('is_closed', True))
                and code in (None, 'KC_LIVE_BODY_BREAKOUT_LONG',
                             'KC_LIVE_BODY_BREAKOUT_SHORT')):
            requested_side = (
                'LONG' if code == 'KC_LIVE_BODY_BREAKOUT_LONG' else
                'SHORT' if code == 'KC_LIVE_BODY_BREAKOUT_SHORT' else None
            )
            if live_breakout is None:
                live_breakout = evaluate_live_body_breakout(
                    frame, quote, symbol=symbol, requested_side=requested_side,
                )
            if live_breakout is not None:
                side = live_breakout['side']
                if side == 'LONG':
                    live_row = frame.iloc[-1]
                    closed_candles = closed_entry_candles(frame)
                    channel_st = str(live_row.get('channel_state', '')).strip().upper()
                    is_kc_trend_down = (
                        channel_st in ('KC向下', 'KC_TREND_DOWN', 'DOWN', 'DOWNTREND', 'BEARISH', 'SHORT')
                        or (len(closed_candles) >= 2 and float(closed_candles.iloc[-1].get('kc_middle', 0.0)) < float(closed_candles.iloc[-2].get('kc_middle', 0.0)))
                    )
                    if is_kc_trend_down:
                        return reject('BLOCKED_BY_KC_TREND_DOWN')

                    prev_ma15 = float(closed_candles.iloc[-1].get('ma15', 0.0)) if len(closed_candles) >= 1 else 0.0
                    live_ma15 = float(live_row.get('ma15', prev_ma15)) + (float(quote) - float(live_row.get('close', quote))) / 15.0
                    if prev_ma15 > 0 and live_ma15 < prev_ma15:
                        return reject('BLOCKED_BY_MA15_SLOPE_DOWN')

                    open_price = float(live_row.get('open', quote))
                    candle_atr = float(closed_candles.iloc[-1].get('atr', 0.0)) if len(closed_candles) >= 1 else 0.0
                    body = quote - open_price
                    if candle_atr > 0 and body < 0.6 * candle_atr:
                        return reject('BLOCKED_INSUFFICIENT_BREAKOUT_BODY')

                    high_price = max(float(live_row.get('high', quote)), quote)
                    upper_wick = high_price - quote
                    if upper_wick >= 0.7 * body:
                        return reject('BLOCKED_UPPER_WICK_RESISTANCE')

                meme_impulse = pipeline.has_meme_impulse_breakout(symbol, frame, quote)
                consecutive_momentum = has_consecutive_momentum_breakout(frame, quote, side)
                if not meme_impulse and not consecutive_momentum and not entry_trend_alignment_ready(frame, side):
                    return reject('BLOCKED_KC_MA5_MA15_TREND_MISMATCH')
                direction_problem = entry_direction_problem(closed, quote, side)
                if direction_problem:
                    return reject(direction_problem)
                if not live_ma3_direction_ready(frame, quote, side):
                    return reject('BLOCKED_BY_LIVE_MA3_DIRECTION')
                ma5_direction = evaluate_live_ma5_direction(frame, quote, side)
                fast_lane_problem = (
                    'BLOCKED_STRICT_MA5_DIRECTION'
                    if ma5_direction is None else None
                )
                if fast_lane_problem is None:
                    live = frame.iloc[-1]
                    live_ma5 = ma5_direction[1]
                    live_ma15 = float(live['ma15']) + (float(quote) - float(live['close'])) / 15.0
                    upper, lower = float(live['kc_upper']), float(live['kc_lower'])
                    width = upper - lower
                    max_width = float((closed.tail(20)['kc_upper'] - closed.tail(20)['kc_lower']).max())
                    is_kc_trend_down = bool(
                        (len(closed) >= 2 and float(closed.iloc[-1].get('kc_middle', 0.0)) < float(closed.iloc[-2].get('kc_middle', 0.0)))
                        or str(live.get('channel_state', '')).upper() in ('KC向下', 'KC_TREND_DOWN', 'TREND_DOWN')
                    )
                    is_short_kc_breakout = (
                        side == 'SHORT' and is_kc_trend_down and quote < lower
                    )
                    if (not all(math.isfinite(value) and value > 0 for value in
                                (live_ma5, live_ma15, upper, lower, width, max_width))
                            or width <= 0 or (width <= 0.25 * max_width and not is_short_kc_breakout)):
                        fast_lane_problem = 'BLOCKED_STRICT_CHANNEL_CONVERGENCE'
                    if not consecutive_momentum and not is_short_kc_breakout:
                        min_spacing = (0.10 if meme_impulse else 0.25) * width
                        if (fast_lane_problem is None
                                and (abs(live_ma5 - live_ma15) < min_spacing
                                     or abs(live_ma5 - (upper if side == 'LONG' else lower))
                                     < min_spacing)):
                            fast_lane_problem = 'BLOCKED_STRICT_MA_SPACING'
                    # A live first-bar breakout is specifically the early
                    # entry path; requiring the slower spacing confirmation
                    # here would defer it to the later closed-bar route.
                if fast_lane_problem:
                    return reject(fast_lane_problem)
                if account is not None and symbol in getattr(account, 'positions', {}):
                    return reject('BLOCKED_BY_POSITION_GATE')
                if any(
                    trade.get('symbol') == symbol
                    and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                    and (trade.get('entry_snapshot') or {}).get('pending_signal_id')
                        == live_breakout['pending_signal_id']
                    for trade in getattr(account, 'trades', [])
                ):
                    return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')
                return authorize(live_breakout, quote)

        # Restore the currently supported pipeline fast-paths before forcing the
        # legacy 2-bar KC gate. This keeps the Channel Swing breakouts strict while
        # re-enabling the legitimate realtime/continuation/reversal autorizations.
        if code is None or code in PIPELINE_ENTRY_TYPES:
            decision = pipeline.authorize(
                None,
                frame,
                quote,
                symbol=symbol,
                account=account,
                diagnostics=diagnostics,
            )
            if decision is not None:
                if code is not None and decision.get('type') != code and not (
                    code in ('AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP')
                    and decision.get('type') in ('AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP')
                ):
                    return reject('BLOCKED_ENTRY_ROUTE_NOT_AUTHORIZED')
                # 經過強制 Pre-Flight 審核
                passed, pre_flight_reason = pre_flight_safety_check(
                    frame, quote, decision.get('side'), decision_type=decision.get('type'),
                    account=account, symbol=symbol,
                    entry_phase=decision.get('entry_phase'),
                )
                if not passed:
                    return reject(pre_flight_reason)
                return authorize(decision, quote)

        forming_bar = not bool(frame.iloc[-1].get('is_closed', True))
        minimum_closed = 2 if forming_bar else 3
        if len(closed) < minimum_closed:
            return reject('WAIT_ENOUGH_CLOSED_CANDLES')

        # One entry authority: confirmed two-bar breakout. Do not let the
        # generic pipeline authorize continuation, realtime-only, or reversal
        # alternatives when the strict breakout candidate is not ready.
        if code is not None and code not in KC_PENDING_CODES and code not in LEGACY_ROUTE_CODES:
            return reject('BLOCKED_ENTRY_ROUTE_NOT_AUTHORIZED')

        # The authorized outer-rail route is based on two completed candles.
        # Do not require a third live candle to have a directional body: the
        # live quote only has to remain beyond the confirmed outer rail.
        if pending is None:
            pending = evaluate_kc_pending_entry(
                closed, quote, code=code, symbol=symbol,
                live=(frame.iloc[-1] if not bool(frame.iloc[-1].get('is_closed', True)) else None),
            )
        if pending.get('action') == 'ENTER':
            side = pending['side']
            meme_impulse = pipeline.has_meme_impulse_breakout(symbol, frame, quote)
            consecutive_momentum = has_consecutive_momentum_breakout(frame, quote, side)
            direction_problem = entry_direction_problem(closed, quote, side)
            if direction_problem:
                return reject(direction_problem)
            if not live_ma3_direction_ready(frame, quote, side):
                return reject('BLOCKED_BY_LIVE_MA3_DIRECTION')
            if evaluate_live_ma5_direction(frame, quote, side) is None:
                return reject('BLOCKED_STRICT_MA5_DIRECTION')
            if not meme_impulse and not consecutive_momentum and not entry_trend_alignment_ready(frame, side):
                return reject('BLOCKED_KC_MA5_MA15_TREND_MISMATCH')
            if len(closed) < 20:
                return reject('BLOCKED_STRICT_CHANNEL_CONVERGENCE')
            live = frame.iloc[-1]
            try:
                upper, lower = float(live['kc_upper']), float(live['kc_lower'])
                ma5, ma15 = float(live['ma5']), float(live['ma15'])
                width = upper - lower
                max_width = float((closed.tail(20)['kc_upper']
                                   - closed.tail(20)['kc_lower']).max())
                edge = upper if side == 'LONG' else lower
                if (not all(math.isfinite(value) and value > 0 for value in
                            (upper, lower, ma5, ma15, width, max_width))
                        or width <= 0 or max_width <= 0):
                    return reject('BLOCKED_STRICT_CHANNEL_CONVERGENCE')
                if width <= 0.25 * max_width:
                    return reject('BLOCKED_STRICT_CHANNEL_CONVERGENCE')
                if not consecutive_momentum:
                    min_spacing = (0.10 if meme_impulse else 0.25) * width
                    if (abs(ma5 - ma15) < min_spacing
                            or abs(ma5 - edge) < min_spacing):
                        return reject('BLOCKED_STRICT_MA_SPACING')
            except (KeyError, TypeError, ValueError, IndexError, OverflowError):
                return reject('BLOCKED_STRICT_CHANNEL_CONVERGENCE')
            if is_chop_filter_symbol(symbol) and not confirmed_pending_breakout:
                consolidation_problem = entry_consolidation_problem(
                    frame, quote, allow_directional_breakout=True, side=side,
                )
                if consolidation_problem:
                    return reject(consolidation_problem)
            if account is not None and symbol in getattr(account, 'positions', {}):
                return reject('BLOCKED_BY_POSITION_GATE')
            if side == 'SHORT':
                live = frame.iloc[-1]
                try:
                    bearish_alignment = (
                        float(live['kc_middle']) < float(closed.iloc[-1]['kc_middle'])
                        and float(live['ma5']) < float(live['ma15'])
                        and float(live['ma5']) < float(closed.iloc[-1]['ma5'])
                        and float(quote) < float(live['kc_lower'])
                        and float(quote) < float(live['ma5'])
                    )
                except (KeyError, TypeError, ValueError, IndexError, OverflowError):
                    bearish_alignment = False
                anti_bottom_problem = anti_bottom_short_problem(
                    frame, quote, account=account, symbol=symbol,
                    allow_confirmed_rail_break=(
                        three_bar_rail_gate_problem(frame, quote, 'SHORT') is None
                        or bearish_alignment
                    ),
                )
                if anti_bottom_problem:
                    return reject(anti_bottom_problem)
            if any(
                trade.get('symbol') == symbol
                and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                and (trade.get('entry_snapshot') or {}).get('pending_signal_id')
                    == pending['pending_signal_id']
                for trade in getattr(account, 'trades', [])
            ):
                return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')
            return authorize(pending, quote)
        if code is not None and code in KC_PENDING_CODES and pending.get('action') != 'ENTER':
            return reject(pending.get('reason', 'WAIT_NEW_KC_BREAKOUT'))

        if (forming_bar and code in (
                None, 'TRIGGER_A_KC_BREAKOUT', 'KC_LIVE_BODY_BREAKOUT_LONG',
                'KC_LIVE_BODY_BREAKOUT_SHORT', GOLDEN_CROSS_FAST_LONG_CODE,
                DEATH_CROSS_FAST_SHORT_CODE,
        )):
            requested_side = (
                'LONG' if code == 'KC_LIVE_BODY_BREAKOUT_LONG' else
                'SHORT' if code == 'KC_LIVE_BODY_BREAKOUT_SHORT' else None
            )
            general_breakout = evaluate_three_bar_outer_breakout(
                frame, quote, symbol=symbol, requested_side=requested_side,
            )
            if general_breakout is not None:
                side = general_breakout['side']
                passed, gate_reason = check_entry_gates(
                    account, symbol, closed, side, 'TRIGGER_A_KC_BREAKOUT',
                )
                if not passed:
                    return reject(gate_reason)
                if any(
                    trade.get('symbol') == symbol
                    and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                    and (trade.get('entry_snapshot') or {}).get('pending_signal_id')
                        == general_breakout['pending_signal_id']
                    for trade in getattr(account, 'trades', [])
                ):
                    return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')
                shadow_problem = excessive_upper_shadow_problem(
                    frame, quote, side, trigger_type='TRIGGER_A_KC_BREAKOUT',
                )
                if shadow_problem:
                    return reject(shadow_problem)
                return authorize(general_breakout, quote)

        if code == 'TRIGGER_A_KC_BREAKOUT':
            return reject('BLOCKED_ENTRY_ROUTE_NOT_AUTHORIZED')

        if (forming_bar and code in (None, 'TRIGGER_C_CONTINUATION')):
            post_close = post_close_candidate
            if post_close is not None:
                if any(
                    trade.get('symbol') == symbol
                    and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                    and (trade.get('entry_snapshot') or {}).get('pending_signal_id')
                        == post_close['pending_signal_id']
                    for trade in getattr(account, 'trades', [])
                ):
                    return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')
                return authorize(post_close, quote)
            if diagnostics is not None and post_close_diagnostics.get('reason') in (
                'BLOCKED_BY_MA5_DOWNWARD_SLOPE', 'BLOCKED_BY_BEARISH_CANDLE',
                'BLOCKED_BY_MA5_UPWARD_SLOPE', 'BLOCKED_BY_BULLISH_CANDLE',
                'BLOCKED_BY_FLAT_KC_EDGE',
                'REJECT_FLAT_CHANNEL',
                'BLOCKED_CONTINUATION_NOT_CLOSED_CONFIRMATION',
                'BLOCKED_POST_CLOSE_NO_LONG_OPPORTUNITY',
                'BLOCKED_POST_CLOSE_STALE_OPPORTUNITY',
                'BLOCKED_POST_CLOSE_UPPER_WICK_COOLDOWN',
                'BLOCKED_POST_CLOSE_LIVE_UPPER_WICK',
                'BLOCKED_POST_CLOSE_LONG_STRENGTH_LOST',
            ):
                return reject(post_close_diagnostics['reason'])

        if code in (BEARISH_INSTANT_BREAKOUT_CODE, BULLISH_INSTANT_BREAKOUT_CODE):
            reverse_side = 'LONG' if code == BULLISH_INSTANT_BREAKOUT_CODE else 'SHORT'
            closed_side = 'SHORT' if reverse_side == 'LONG' else 'LONG'
            decision = evaluate_instant_reverse_breakout(
                frame, quote, symbol, account, closed_side=closed_side,
            )
            if decision is None:
                return reject('BLOCKED_INSTANT_REVERSE_WITHOUT_MATCHED_SHARP_CLOSE')
            if decision.get('side') != reverse_side:
                return reject('BLOCKED_INSTANT_REVERSE_SIDE_MISMATCH')
            decision['strategy_type'] = 'BREAKOUT'
            return authorize(decision, quote)

        if code in ('KC_LIVE_BODY_BREAKOUT_LONG', 'KC_LIVE_BODY_BREAKOUT_SHORT'):
            return reject('BLOCKED_LIVE_BODY_BREAKOUT_NOT_QUALIFIED')

        if code in ('RE_ENTRY_LONG', 'RE_ENTRY_SHORT'):
            return reject('BLOCKED_ENTRY_ROUTE_NOT_AUTHORIZED')

        continuation = evaluate_continuation_entry(
            frame, quote,
            code='TRIGGER_C_CONTINUATION' if code == 'TRIGGER_C_CONTINUATION' else None,
            symbol=symbol, account=account, diagnostics=diagnostics,
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
            return authorize(continuation, quote)
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
            diagnostic_reason = (
                diagnostics.get('reason') if diagnostics is not None else None
            )
            if diagnostic_reason in (
                'BLOCKED_BY_MA5_DOWNWARD_SLOPE', 'BLOCKED_BY_BEARISH_CANDLE',
                'BLOCKED_BY_MA5_UPWARD_SLOPE', 'BLOCKED_BY_BULLISH_CANDLE',
                'BLOCKED_BY_FLAT_KC_EDGE',
                'REJECT_FLAT_CHANNEL',
                'BLOCKED_CONTINUATION_NOT_CLOSED_CONFIRMATION',
            ):
                problem = diagnostic_reason
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

        shadow_problem = excessive_upper_shadow_problem(
            frame, quote, side, trigger_type=decision.get("type"),
        )
        if shadow_problem:
            return reject(shadow_problem)

        return authorize(decision, quote)

    except Exception as e:
        return reject(f"ENTRY_ERROR_{str(e)}")
