"""Entry Gate Pipeline: Unified authorization funnel for all trading entries."""
import math
from typing import Optional, Dict, Any, Callable
import pandas as pd

from core.services.candle_data import closed_entry_candles
from core.intelligence.spatial_brain import SpatialBrain
from core.gates.chop_filter_gate import ChopFilterGate
from core.gates.candle_solidity_gate import CandleSolidityGate
from core.gates.mouth_expansion_gate import MouthExpansionGate
from core.gates.continuation_ma5_gate import ContinuationMa5Gate
from core.services.strategies.outer_strategy import (
    ck_direction,
    kc_bearish_midline_turn,
)
from core.symbol_aliases import (  # noqa: F401 - re-exported for callers
    MEME_BREAKOUT_SYMBOLS,
    is_meme_breakout_symbol,
)

MAX_MA15_ENTRY_BIAS_ATR = 1.8
CHOP_KC_SLOPE_EPSILON_ATR = 0.10
CHOP_MA_TANGLE_ATR = 0.25
CHOP_MIN_CHANNEL_BODY_ATR = 0.40
MA_CROSS_CHOP_LOOKBACK = 6
MA_CROSS_CHOP_MIN_CROSSES = 1
MA_CROSS_CHOP_RECOVERY_GAP_PCT = 0.0015
MA_GAP_CHOP_MIN_PCT = 0.0012
MIN_KC_BANDWIDTH_PCT = 0.006
MIN_DIRECTIONAL_KC_SPACE = 0.25
MIN_DIRECTIONAL_KC_SPACE_HIGH_MOMENTUM = 0.15
REALTIME_SHORT_MIN_RAIL_DISTANCE_ATR = 0.15
REALTIME_SHORT_MIN_BODY_RATIO = 0.20
HIGH_MOMENTUM_BODY_ATR = 0.70
MEME_BREAKOUT_BODY_ATR = 0.20
FAST_CROSS_BODY_ATR = 0.40
FAST_CROSS_MAX_DIRECTIONAL_WICK_RATIO = 1.0
FAST_CROSS_MIN_MA15_SLOPE_ATR = 0.03
GOLDEN_CROSS_FAST_LONG_CODE = 'KC_GOLDEN_CROSS_FAST_LONG'
DEATH_CROSS_FAST_SHORT_CODE = 'KC_DEATH_CROSS_FAST_SHORT'
MOMENTUM_BURST_BODY_ATR = 0.50
MOMENTUM_BURST_BODY_RATIO = 0.50
MOMENTUM_BURST_MAX_DIRECTIONAL_WICK_RATIO = 1.0
MOMENTUM_BURST_MAX_MIDLINE_DISTANCE_ATR = 0.10
MOMENTUM_BURST_LONG_CODE = 'KC_MOMENTUM_BURST_LONG'
MOMENTUM_BURST_SHORT_CODE = 'KC_MOMENTUM_BURST_SHORT'
MOMENTUM_BURST_CODES = frozenset((
    MOMENTUM_BURST_LONG_CODE,
    MOMENTUM_BURST_SHORT_CODE,
))
CONSECUTIVE_MOMENTUM_BODY_ATR = 0.35


def has_consecutive_momentum_breakout(
    frame: pd.DataFrame,
    quote: Optional[float] = None,
    side: Optional[str] = None,
) -> bool:
    """Detect multi-bar consecutive same-color momentum breakout beyond KC outer bands."""
    try:
        if frame is None or frame.empty:
            return False
        closed = closed_entry_candles(frame)
        if len(closed) < 2:
            return False
        atr = float(closed.iloc[-1].get('atr') or closed.iloc[-2].get('atr') or 0.0)
        if not math.isfinite(atr) or atr <= 0:
            return False
        min_body = CONSECUTIVE_MOMENTUM_BODY_ATR * atr

        # Check LONG
        if side in (None, 'LONG'):
            # Case 1: Prior closed bar breached KC upper and live bar continues with bullish momentum
            prev = closed.iloc[-1]
            prev_o, prev_c = float(prev['open']), float(prev['close'])
            prev_u = float(prev['kc_upper'])
            if prev_c > prev_u and (prev_c - prev_o >= min_body or math.isclose(prev_c - prev_o, min_body, rel_tol=1e-12)):
                live = frame.iloc[-1]
                if not bool(live.get('is_closed', True)):
                    curr_p = float(quote if quote is not None else live['close'])
                    curr_o = float(live['open'])
                    curr_u = float(live['kc_upper'])
                    if (curr_p > curr_u or curr_p > prev_u) and (curr_p - curr_o >= min_body or math.isclose(curr_p - curr_o, min_body, rel_tol=1e-12)):
                        return True
            # Case 2: Last two completed bars both closed above KC upper with strong bullish bodies
            p1 = closed.iloc[-2]
            p2 = closed.iloc[-1]
            p1_o, p1_c, p1_u = float(p1['open']), float(p1['close']), float(p1['kc_upper'])
            p2_o, p2_c, p2_u = float(p2['open']), float(p2['close']), float(p2['kc_upper'])
            p1_ok = (p1_c > p1_u) and (p1_c - p1_o >= min_body or math.isclose(p1_c - p1_o, min_body, rel_tol=1e-12))
            p2_ok = (p2_c > p2_u) and (p2_c - p2_o >= min_body or math.isclose(p2_c - p2_o, min_body, rel_tol=1e-12))
            if p1_ok and p2_ok:
                curr_p = float(quote if quote is not None else p2_c)
                if curr_p >= p2_o:
                    return True

        # Check SHORT
        if side in (None, 'SHORT'):
            # Case 1: Prior closed bar breached KC lower and live bar continues with bearish momentum
            prev = closed.iloc[-1]
            prev_o, prev_c = float(prev['open']), float(prev['close'])
            prev_l = float(prev['kc_lower'])
            if prev_c < prev_l and (prev_o - prev_c >= min_body or math.isclose(prev_o - prev_c, min_body, rel_tol=1e-12)):
                live = frame.iloc[-1]
                if not bool(live.get('is_closed', True)):
                    curr_p = float(quote if quote is not None else live['close'])
                    curr_o = float(live['open'])
                    curr_l = float(live['kc_lower'])
                    if (curr_p < curr_l or curr_p < prev_l) and (curr_o - curr_p >= min_body or math.isclose(curr_o - curr_p, min_body, rel_tol=1e-12)):
                        return True
            # Case 2: Last two completed bars both closed below KC lower with strong bearish bodies
            p1 = closed.iloc[-2]
            p2 = closed.iloc[-1]
            p1_o, p1_c, p1_l = float(p1['open']), float(p1['close']), float(p1['kc_lower'])
            p2_o, p2_c, p2_l = float(p2['open']), float(p2['close']), float(p2['kc_lower'])
            p1_ok = (p1_c < p1_l) and (p1_o - p1_c >= min_body or math.isclose(p1_o - p1_c, min_body, rel_tol=1e-12))
            p2_ok = (p2_c < p2_l) and (p2_o - p2_c >= min_body or math.isclose(p2_o - p2_c, min_body, rel_tol=1e-12))
            if p1_ok and p2_ok:
                curr_p = float(quote if quote is not None else p2_c)
                if curr_p <= p2_o:
                    return True

        return False
    except Exception:
        return False
STRATEGY_TYPE_BREAKOUT = 'BREAKOUT'
STRATEGY_TYPE_TREND_CONTINUATION = 'TREND_CONTINUATION'
BREAKOUT_ENTRY_PHASES = frozenset((
    'ATOMIC_LONG_REVERSE',
    'ATOMIC_SHORT_REVERSE',
    'KC_LIVE_OUTER_BREAKOUT',
    'IMPULSE_BREAKOUT',
    'KC_2BAR_BREAKOUT',
    'KC_2BAR_CLOSED_CONFIRM',
    'KC_LIVE_OUTER_BREAKOUT',
    'KC_LIVE_PATTERN_BREAKOUT',
    'KC_THREE_BAR_BREAKOUT',
    'MEME_BREAKOUT_ENTRY',
    'MEME_AMBUSH_ENTRY',
    'MEME_PYRAMID_ADDITION',
))
_MA_CROSS_CHOP_LOGGED = {}
FLAT_LINE_BREAKOUT_TYPES = frozenset((
    'AUTHORIZED_REALTIME_BREAKOUT',
    'AUTHORIZED_BY_TOP_PLUNGE_SHORT',
    'AUTHORIZED_BY_TREND_CONTINUATION_LONG',
    'AUTHORIZED_BY_TREND_CONTINUATION_SHORT',
    'KC_GOLDEN_CROSS_FAST_LONG',
    'KC_DEATH_CROSS_FAST_SHORT',
    MOMENTUM_BURST_LONG_CODE,
    MOMENTUM_BURST_SHORT_CODE,
    'AUTHORIZED_BY_MIDLINE_BREAK_SHORT',
    'AUTHORIZED_BY_MIDLINE_BREAK_LONG',
    'CLOSED_BODY_BREAKOUT_LONG', 'CLOSED_BODY_BREAKOUT_SHORT',
    'KC_LIVE_BODY_BREAKOUT_LONG', 'KC_LIVE_BODY_BREAKOUT_SHORT',
    'KC_LIVE_PATTERN_BREAKOUT_LONG', 'KC_LIVE_PATTERN_BREAKOUT_SHORT',
    'KC_2BAR_CONFIRM_LONG', 'KC_2BAR_CONFIRM_SHORT',
    'KC_REALTIME_RAIL_BREACH_SHORT',
    'KC_DOWNTREND_PULLBACK_RESUME_SHORT',
    'TRIGGER_A_KC_BREAKOUT',
    'TRIGGER_B_MA_CROSS',
    'TRIGGER_C_CONTINUATION',
    'RE_ENTRY_LONG',
    'RE_ENTRY_SHORT',
    'TRIGGER_B_REENTRY_SUPPORT',
    'TRIGGER_B_REENTRY_BREAKOUT',
))
from core.gates.meme_coin_gate import MEME_ENTRY_CODES
FLAT_LINE_BREAKOUT_TYPES = FLAT_LINE_BREAKOUT_TYPES | MEME_ENTRY_CODES

PIPELINE_ENTRY_TYPES = frozenset((
    'AUTHORIZED_REALTIME_BREAKOUT',
    'AUTHORIZED_BY_TOP_PLUNGE_SHORT',
    'AUTHORIZED_BY_TREND_CONTINUATION_LONG',
    'AUTHORIZED_BY_TREND_CONTINUATION_SHORT',
    'AUTHORIZED_SHADOW_RETEST_ENTRY',
    'AUTHORIZED_TOP_REVERSAL_SHORT',
    'AUTHORIZED_BY_PEAK_FLIP_SHORT',
    'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
    'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
    'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
    'TOP_WATERFALL_FLIP',
    'AUTHORIZED_BY_MIDLINE_BREAK_SHORT',
    'AUTHORIZED_BY_MIDLINE_BREAK_LONG',
    MOMENTUM_BURST_LONG_CODE,
    MOMENTUM_BURST_SHORT_CODE,
    'KC_DOWNTREND_PULLBACK_RESUME_SHORT',
    'KC_LIVE_BODY_BREAKOUT_LONG',
    'KC_LIVE_BODY_BREAKOUT_SHORT',
    'KC_GOLDEN_CROSS_FAST_LONG',
    'KC_DEATH_CROSS_FAST_SHORT',
    'TRIGGER_A_KC_BREAKOUT',
    'TRIGGER_B_MA_CROSS',
    'TRIGGER_C_CONTINUATION',
    'CLOSED_BODY_BREAKOUT_LONG',
    'CLOSED_BODY_BREAKOUT_SHORT',
    'RE_ENTRY_LONG',
    'RE_ENTRY_SHORT',
    'TRIGGER_B_REENTRY_SUPPORT',
    'TRIGGER_B_REENTRY_BREAKOUT',
    *MEME_ENTRY_CODES,
))


def ma5_ma15_cross_chop_problem(
    frame: pd.DataFrame, quote: Optional[float] = None, *,
    symbol: str = '', log_result: bool = False,
    side: Optional[str] = None,
) -> Optional[str]:
    """Block entries during MA entanglement, post-cross compression, or flat slopes."""
    cross_count = None
    gap_pct = None
    ma5_slope_pct = None
    ma15_slope_pct = None
    status = 'BLOCKED'
    try:
        if frame is None or frame.empty or side not in (None, 'LONG', 'SHORT'):
            raise ValueError('missing frame')
        live = frame.iloc[-1]
        live_closed = live.get('is_closed', False)
        has_live = not (
            live_closed is True
            or (type(live_closed).__name__ == 'bool_' and bool(live_closed))
        )
        closed = closed_entry_candles(frame)
        required = {'ma5', 'ma15'}
        timeframe_ms = float(frame.attrs.get('timeframe_ms', 60000))
        if (not math.isfinite(timeframe_ms) or timeframe_ms != 60000
                or len(closed) < MA_CROSS_CHOP_LOOKBACK
                or not required.issubset(closed.columns)
                or not {'ma5', 'ma15', 'close'}.issubset(frame.columns)):
            raise ValueError('insufficient MA history')

        cross_history = closed.tail(MA_CROSS_CHOP_LOOKBACK)
        stamps = [float(value) for value in cross_history['timestamp']]
        if (not all(math.isfinite(value) and value > 0 for value in stamps)
                or any(right - left != timeframe_ms
                       for left, right in zip(stamps, stamps[1:]))):
            raise ValueError('invalid one-minute MA history')
        spreads = [
            float(row.ma5) - float(row.ma15)
            for row in cross_history.itertuples()
        ]
        if not all(math.isfinite(value) for value in spreads):
            raise ValueError('invalid MA spread')
        cross_count = 0
        prior_sign = 0
        for spread in spreads:
            current_sign = 1 if spread > 0 else -1 if spread < 0 else 0
            if current_sign and prior_sign and current_sign != prior_sign:
                cross_count += 1
            if current_sign:
                prior_sign = current_sign

        current_quote = float(
            quote if quote is not None else live.get('close', float('nan'))
        )
        live_close = float(live.get('close', float('nan')))
        live_ma5 = float(live.get('ma5', float('nan')))
        live_ma15 = float(live.get('ma15', float('nan')))
        values = (
            current_quote, live_close, live_ma5, live_ma15,
            *spreads,
        )
        if (not all(math.isfinite(value) for value in values)
                or min(current_quote, live_close, live_ma5, live_ma15) <= 0):
            raise ValueError('invalid MA evidence')

        if has_live:
            live_stamp = float(live.get('timestamp', float('nan')))
            if (not math.isfinite(live_stamp) or live_stamp <= 0
                    or live_stamp != stamps[-1] + timeframe_ms):
                raise ValueError('invalid live one-minute MA history')
        live_ma5 += (current_quote - live_close) / 5.0
        live_ma15 += (current_quote - live_close) / 15.0
        previous_closed = closed.iloc[-2]
        previous_ma5 = float(previous_closed['ma5'])
        previous_ma15 = float(previous_closed['ma15'])
        current_ma5 = live_ma5
        current_ma15 = live_ma15
        if not all(math.isfinite(value) and value > 0 for value in (
            previous_ma5, previous_ma15, current_ma5, current_ma15,
        )):
            raise ValueError('invalid MA slope evidence')

        gap_pct = abs(current_ma5 - current_ma15) / current_quote
        ma5_slope_pct = (current_ma5 - previous_ma5) / current_quote
        ma15_slope_pct = (current_ma15 - previous_ma15) / current_quote

        meme_impulse = False
        if (is_meme_breakout_symbol(symbol)
                and side in ('LONG', 'SHORT') and quote is not None):
            live = frame.iloc[-1]
            atr = float(closed.iloc[-1].get('atr', float('nan')))
            opening = float(live.get('open', float('nan')))
            close = float(live.get('close', float('nan')))
            upper = float(live.get('kc_upper', float('nan')))
            lower = float(live.get('kc_lower', float('nan')))
            effective_close = current_quote if has_live else close
            signed_body = effective_close - opening if side == 'LONG' else opening - effective_close
            meme_impulse = (
                SpatialBrain.analyze(frame, current_quote).state == 'EXPLOSIVE_EXPANSION'
                and all(math.isfinite(value) and value > 0 for value in
                        (atr, opening, effective_close, upper, lower))
                and ((side == 'LONG' and current_quote > upper)
                     or (side == 'SHORT' and current_quote < lower))
                and signed_body >= MEME_BREAKOUT_BODY_ATR * atr
            )

        def below(value, threshold):
            return value < threshold and not math.isclose(
                value, threshold, rel_tol=1e-12, abs_tol=0.0,
            )

        recent_cross_compressed = (
            cross_count >= MA_CROSS_CHOP_MIN_CROSSES
            and below(gap_pct, 0.0004 if meme_impulse else MA_CROSS_CHOP_RECOVERY_GAP_PCT)
        )
        fresh_cross = (
            (side == 'SHORT' and current_ma5 < current_ma15 and current_ma5 < previous_ma5 and previous_ma5 > previous_ma15)
            or (side == 'LONG' and current_ma5 > current_ma15 and current_ma5 > previous_ma5 and previous_ma5 < previous_ma15)
        )
        if not fresh_cross and below(gap_pct, 0.0003 if meme_impulse else MA_GAP_CHOP_MIN_PCT):
            problem = 'BLOCKED_MA5_MA15_GAP_TOO_NARROW'
        elif recent_cross_compressed:
            problem = 'BLOCKED_MA5_MA15_CROSS_CHOP'
        elif side == 'LONG' and not (
            current_ma5 > previous_ma5
            and (meme_impulse or current_ma15 > previous_ma15)
        ):
            problem = 'BLOCKED_MA5_MA15_SLOPE_MISALIGNED'
        elif side == 'SHORT' and not (
            current_ma5 < previous_ma5
            and (meme_impulse or current_ma15 < previous_ma15)
        ):
            problem = 'BLOCKED_MA5_MA15_SLOPE_MISALIGNED'
        else:
            problem = None
            status = 'PASSED'
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        problem = 'BLOCKED_INVALID_MA_CROSS_HISTORY'

    if log_result:
        bar_id = None
        try:
            bar_id = float(frame.iloc[-1].get('timestamp'))
        except (AttributeError, TypeError, ValueError, IndexError, OverflowError):
            pass
        log_key = (symbol or str(id(frame)), bar_id)
        log_state = (status, cross_count, gap_pct, ma5_slope_pct, ma15_slope_pct)
        if _MA_CROSS_CHOP_LOGGED.get(log_key) != log_state:
            import logging
            logging.getLogger('EntryChopCheck').info(
                '[ChopCheck] Cross6=%s/1, MAGap=%s%%, MA5Slope=%s%%, '
                'MA15Slope=%s%% -> Status: %s symbol=%s bar=%s reason=%s',
                cross_count if cross_count is not None else 'INVALID',
                f'{gap_pct * 100:.6f}' if gap_pct is not None else 'INVALID',
                f'{ma5_slope_pct * 100:.6f}' if ma5_slope_pct is not None else 'INVALID',
                f'{ma15_slope_pct * 100:.6f}' if ma15_slope_pct is not None else 'INVALID',
                status, symbol or 'UNKNOWN', bar_id, problem or 'NONE',
            )
            _MA_CROSS_CHOP_LOGGED[log_key] = log_state
            if len(_MA_CROSS_CHOP_LOGGED) > 2048:
                _MA_CROSS_CHOP_LOGGED.pop(next(iter(_MA_CROSS_CHOP_LOGGED)))
    return problem


def fast_cross_evidence(
    frame: pd.DataFrame, quote: float, side: str, symbol: Optional[str] = None,
) -> Optional[Dict[str, float]]:
    """Return symmetric evidence for a fresh, directional MA5/MA15 fast cross."""
    try:
        if (side not in ('LONG', 'SHORT') or frame is None or len(frame) < 2
                or 'is_closed' not in frame.columns):
            return None
        closed = closed_entry_candles(frame)
        if len(closed) < 12:
            return None
        previous = closed.iloc[-1]
        live = frame.iloc[-1]
        stamp = float(live['timestamp'])
        previous_stamp = float(previous['timestamp'])
        opening = float(live['open'])
        quote = float(quote)
        raw_close = float(live['close'])
        high = float(live['high'])
        low = float(live['low'])
        upper = float(live['kc_upper'])
        middle = float(live['kc_middle'])
        lower = float(live['kc_lower'])
        atr = float(previous['atr'])
        previous_ma5 = float(previous['ma5'])
        previous_ma15 = float(previous['ma15'])
        live_ma5 = float(live['ma5']) + (quote - raw_close) / 5.0
        live_ma15 = float(live['ma15']) + (quote - raw_close) / 15.0
        width = upper - lower
        values = (
            stamp, previous_stamp, opening, quote, raw_close, high, low,
            upper, middle, lower, atr, previous_ma5, previous_ma15,
            live_ma5, live_ma15, width,
        )
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or stamp != previous_stamp + 60_000
                or low > min(opening, quote)
                or high < max(opening, quote)
                or not lower < middle < upper
                or low > min(opening, quote)
                or high < max(opening, quote)
                or width <= 0):
            return None
        if fast_cross_ma15_slope_problem(frame, quote, side, symbol=symbol):
            return None

        if side == 'LONG':
            body = quote - opening
            directional_wick = high - max(opening, quote)
            prev2_ma5 = float(closed.iloc[-2]['ma5']) if len(closed) >= 2 else previous_ma5
            prev2_ma15 = float(closed.iloc[-2]['ma15']) if len(closed) >= 2 else previous_ma15
            crossed = (
                (previous_ma5 <= previous_ma15 and live_ma5 > live_ma15 and live_ma5 > previous_ma5)
                or (prev2_ma5 <= prev2_ma15 and previous_ma5 > previous_ma15 and live_ma5 >= previous_ma5 and live_ma5 > live_ma15)
            )
            # 當根 1m K 棒收線確認（close > ma5 且 close > open）
            candle_confirmed = quote > live_ma5 and quote > opening
            position_ready = quote > middle
            room = (upper - quote) / width
        else:
            body = opening - quote
            directional_wick = min(opening, quote) - low
            prev2_ma5 = float(closed.iloc[-2]['ma5']) if len(closed) >= 2 else previous_ma5
            prev2_ma15 = float(closed.iloc[-2]['ma15']) if len(closed) >= 2 else previous_ma15
            crossed = (
                (previous_ma5 >= previous_ma15 and live_ma5 < live_ma15 and live_ma5 < previous_ma5)
                or (prev2_ma5 >= prev2_ma15 and previous_ma5 < previous_ma15 and live_ma5 <= previous_ma5 and live_ma5 < live_ma15)
            )
            # 當根 1m K 棒收線確認（close < ma5 且 close < open）
            candle_confirmed = quote < live_ma5 and quote < opening
            position_ready = quote < middle
            room = (quote - lower) / width

        # 【MA5 / MA15 交叉條件開倉鐵律】：不是一交叉就開倉，必須具備多重條件
        previous_middle = float(previous.get('kc_middle', middle)) if hasattr(previous, 'get') else middle

        # 1. KC 中軌方向同步（多單中軌非下行，空單中軌非上行）
        if side == 'LONG' and middle < previous_middle - 1e-6:
            return None
        if side == 'SHORT' and middle > previous_middle + 1e-6:
            return None

        # 2. 順勢鐵律：當行情歷史充足時，空頭趨勢嚴禁開多，多頭趨勢嚴禁開空
        if len(frame) >= 20:
            from core.gates.meme_coin_gate import MemeCoinGate
            market_trend = MemeCoinGate.get_market_trend(frame, quote)
            if side == 'LONG' and market_trend == 'BEARISH':
                return None
            if side == 'SHORT' and market_trend == 'BULLISH':
                return None

        # 3. 實體動能、通道空間與影線防護（絕不繞過空間與中軌位置檢查）
        body_ready = (
            body >= FAST_CROSS_BODY_ATR * atr
            or math.isclose(body, FAST_CROSS_BODY_ATR * atr, rel_tol=1e-12, abs_tol=0.0)
        )
        min_space = (
            MIN_DIRECTIONAL_KC_SPACE_HIGH_MOMENTUM
            if (atr > 0 and (body > HIGH_MOMENTUM_BODY_ATR * atr or math.isclose(body, HIGH_MOMENTUM_BODY_ATR * atr, rel_tol=1e-12, abs_tol=0.0)))
            else MIN_DIRECTIONAL_KC_SPACE
        )
        room_ready = (
            room >= min_space
            or math.isclose(room, min_space, rel_tol=1e-12, abs_tol=0.0)
        )
        wick_ready = (
            directional_wick <= FAST_CROSS_MAX_DIRECTIONAL_WICK_RATIO * body
            or math.isclose(
                directional_wick,
                FAST_CROSS_MAX_DIRECTIONAL_WICK_RATIO * body,
                rel_tol=1e-12, abs_tol=0.0,
            )
        )

        if not (crossed and candle_confirmed and position_ready and body_ready and room_ready and wick_ready):
            return None

        return {
            'stamp': stamp,
            'previous_stamp': previous_stamp,
            'opening': opening,
            'quote': quote,
            'atr': atr,
            'middle': middle,
            'upper': upper,
            'lower': lower,
            'live_ma5': live_ma5,
            'live_ma15': live_ma15,
            'previous_ma5': previous_ma5,
            'previous_ma15': previous_ma15,
            'ma15_slope_atr': (live_ma15 - previous_ma15) / atr,
            'body_atr': body / atr,
            'directional_space_ratio': room,
        }
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def is_ma_tangling(
    frame: pd.DataFrame,
    quote: float,
    *,
    signal_code: Optional[str] = None,
    allow_ma_tangle: bool = False,
) -> bool:
    """Return True if MA5/MA15 are tangled or compressed, with fast cross exemption."""
    if allow_ma_tangle:
        return False
    try:
        if frame is None or frame.empty:
            return True
        closed = closed_entry_candles(frame)
        if len(closed) < 2:
            return True
        live = frame.iloc[-1]
        current = closed.iloc[-1]
        atr = float(current['atr'])
        ma5 = float(live['ma5'])
        ma15 = float(live['ma15'])
        quote = float(quote)
        if not bool(live.get('is_closed', True)):
            adjustment = quote - float(live['close'])
            ma5 += adjustment / 5.0
            ma15 += adjustment / 15.0
        if not all(math.isfinite(value) and value > 0 for value in (ma5, ma15)):
            return True

        # 若剛觸發有效金叉/死叉且具備動能實體，給予 1~2 根 K 線的交叉豁免
        if signal_code in (GOLDEN_CROSS_FAST_LONG_CODE, DEATH_CROSS_FAST_SHORT_CODE):
            opening = float(live['open'])
            closing = float(live['close']) if bool(live.get('is_closed', True)) else quote
            candle_body = abs(closing - opening)
            side = 'LONG' if signal_code == GOLDEN_CROSS_FAST_LONG_CODE else 'SHORT'
            is_valid_cross_momentum = fast_cross_evidence(frame, quote, side) is not None
            if (candle_body >= 0.4 * atr or math.isclose(candle_body, 0.4 * atr, rel_tol=1e-12, abs_tol=0.0)) and is_valid_cross_momentum:
                return False  # 豁免 tangling 攔截

        return abs(ma5 - ma15) < CHOP_MA_TANGLE_ATR * atr
    except Exception:
        return True


def fast_cross_ma15_slope_problem(
    frame: pd.DataFrame, quote: float, side: str, symbol: Optional[str] = None,
) -> Optional[str]:
    """Reject a fast MA cross when live MA15 movement is flat relative to ATR."""
    try:
        if is_meme_breakout_symbol(symbol):
            return None
        if (side not in ('LONG', 'SHORT') or frame is None or frame.empty
                or bool(frame.iloc[-1].get('is_closed', True))):
            return 'BLOCKED_FAST_CROSS_MA15_SLOPE_INVALID'
        closed = closed_entry_candles(frame)
        if closed.empty:
            return 'BLOCKED_FAST_CROSS_MA15_SLOPE_INVALID'
        live = frame.iloc[-1]
        previous = closed.iloc[-1]
        quote = float(quote)
        raw_close = float(live['close'])
        previous_ma15 = float(previous['ma15'])
        live_ma15 = float(live['ma15']) + (quote - raw_close) / 15.0
        atr = float(previous['atr'])
        if (not all(math.isfinite(value) and value > 0
                    for value in (quote, raw_close, previous_ma15, live_ma15, atr))):
            return 'BLOCKED_FAST_CROSS_MA15_SLOPE_INVALID'
        slope_atr = abs(live_ma15 - previous_ma15) / atr
        if (slope_atr < FAST_CROSS_MIN_MA15_SLOPE_ATR
                and not math.isclose(
                    slope_atr, FAST_CROSS_MIN_MA15_SLOPE_ATR,
                    rel_tol=1e-12, abs_tol=0.0,
                )):
            return 'BLOCKED_FAST_CROSS_MA15_SLOPE_FLAT'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_FAST_CROSS_MA15_SLOPE_INVALID'


def strict_kc_entry_problem(
    frame: pd.DataFrame, quote: float, side: Optional[str] = None, *,
    strategy_type: Optional[str] = None,
) -> Optional[str]:
    """Apply KC validity/squeeze checks and applicable directional room vetoes."""
    try:
        if frame is None or frame.empty:
            return 'BLOCKED_INVALID_KC_ENTRY_SPACE'
        current = frame.iloc[-1]
        upper = float(current['kc_upper'])
        middle = float(current['kc_middle'])
        lower = float(current['kc_lower'])
        quote = float(quote)
        if (not all(math.isfinite(value) and value > 0
                    for value in (upper, middle, lower, quote))
                or not lower < middle < upper):
            return 'BLOCKED_INVALID_KC_ENTRY_SPACE'
        width = upper - lower
        bandwidth = width / middle
        if (bandwidth < MIN_KC_BANDWIDTH_PCT
                and strategy_type != STRATEGY_TYPE_BREAKOUT
                and not math.isclose(
                    bandwidth, MIN_KC_BANDWIDTH_PCT,
                    rel_tol=1e-12, abs_tol=0.0,
                )
                and not _has_directional_squeeze_expansion(
                    frame, quote, side,
                )
                and not (side == 'SHORT'
                         and is_live_short_lower_rail_breakout(frame, quote))):
            return 'BLOCKED_KC_SQUEEZE'
        if (side in ('LONG', 'SHORT')
                and strategy_type not in (
                    STRATEGY_TYPE_BREAKOUT,
                    STRATEGY_TYPE_TREND_CONTINUATION,
                )):
            remaining = (
                (upper - quote) / width
                if side == 'LONG'
                else (quote - lower) / width
            )
            # 動能強勁（實體 >= 0.7 ATR）時，通道留白門檻放寬至 15%
            min_space = MIN_DIRECTIONAL_KC_SPACE
            try:
                opening = float(current['open'])
                atr = float(current.get('atr', float('nan')))
                if not math.isfinite(atr) or atr <= 0:
                    closed = closed_entry_candles(frame)
                    if not closed.empty:
                        atr = float(closed.iloc[-1].get('atr', float('nan')))
                body = abs(quote - opening)
                if (math.isfinite(atr) and atr > 0
                        and (body > HIGH_MOMENTUM_BODY_ATR * atr
                             or math.isclose(body, HIGH_MOMENTUM_BODY_ATR * atr, rel_tol=1e-12, abs_tol=0.0))):
                    min_space = MIN_DIRECTIONAL_KC_SPACE_HIGH_MOMENTUM
            except Exception:
                min_space = MIN_DIRECTIONAL_KC_SPACE

            if (remaining < min_space
                    and not math.isclose(
                        remaining, min_space,
                        rel_tol=1e-12, abs_tol=0.0,
                    )):
                return (
                    'INSUFFICIENT_UPPER_SPACE'
                    if side == 'LONG'
                    else 'INSUFFICIENT_LOWER_SPACE'
                )
        if side not in (None, 'LONG', 'SHORT'):
            return 'BLOCKED_INVALID_KC_ENTRY_SPACE'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_INVALID_KC_ENTRY_SPACE'


def is_live_short_lower_rail_breakout(frame: pd.DataFrame, quote: float) -> bool:
    """Validate the narrow live short-break exception to CHOP/squeeze gates."""
    try:
        if (frame is None or len(frame) < 3 or 'is_closed' not in frame.columns
                or bool(frame.iloc[-1]['is_closed'])):
            return False
        closed = closed_entry_candles(frame)
        if len(closed) < 2:
            return False
        live, previous = frame.iloc[-1], closed.iloc[-1]
        quote = float(quote)
        opening, close = float(live['open']), float(live['close'])
        upper, lower = float(live['kc_upper']), float(live['kc_lower'])
        atr = float(previous['atr'])
        high = max(float(live['high']), opening, quote)
        low = min(float(live['low']), opening, quote)
        ma5 = float(live['ma5']) + (quote - close) / 5.0
        previous_ma5 = float(previous['ma5'])
        values = (quote, opening, close, upper, lower, atr, high, low, ma5, previous_ma5)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or not lower < upper or atr <= 0
                or not lower <= opening <= upper
                or quote >= lower or quote >= opening
                or low > min(opening, quote) or high < max(opening, quote)):
            return False
        distance_atr = (lower - quote) / atr
        body = opening - quote
        candle_range = high - low
        return (
            REALTIME_SHORT_MIN_RAIL_DISTANCE_ATR <= distance_atr <= 3.0
            and candle_range > 0
            and body / candle_range >= REALTIME_SHORT_MIN_BODY_RATIO
            and quote < ma5 < previous_ma5
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def _has_directional_squeeze_expansion(
    frame: pd.DataFrame, quote: float, side: Optional[str],
) -> bool:
    """Pass a squeeze only for a shaped impulse with strict MA5 alignment."""
    try:
        if frame is None or len(frame) < 2:
            return False
        current, previous = frame.iloc[-1], frame.iloc[-2]
        quote = float(quote)
        opening = float(current['open'])
        raw_close = float(current['close'])
        high = max(float(current['high']), opening, quote)
        low = min(float(current['low']), opening, quote)
        upper = float(current['kc_upper'])
        lower = float(current['kc_lower'])
        atr = float(previous['atr'])
        if side not in ('LONG', 'SHORT'):
            side = 'LONG' if quote > opening else 'SHORT' if quote < opening else None
        if (side not in ('LONG', 'SHORT')
                or not all(math.isfinite(value) and value > 0 for value in (
                    quote, opening, raw_close, high, low, upper, lower, atr,
                ))
                or lower >= upper or atr <= 0
                or low > min(opening, quote)
                or high < max(opening, quote)):
            return False

        live_ma5 = float(current['ma5'])
        if not bool(current.get('is_closed', True)):
            live_ma5 += (quote - raw_close) / 5.0
        previous_ma5 = float(previous['ma5'])
        if not all(math.isfinite(value) and value > 0
                   for value in (live_ma5, previous_ma5)):
            return False

        body = quote - opening if side == 'LONG' else opening - quote
        ma5_directional = (
            live_ma5 > previous_ma5
            if side == 'LONG'
            else live_ma5 < previous_ma5
        )
        directional_wick = (
            high - max(opening, quote)
            if side == 'LONG'
            else min(opening, quote) - low
        )
        candle_range = high - low
        body_shape_valid = (
            candle_range > 0
            and body > 0
            and body / candle_range >= MOMENTUM_BURST_BODY_RATIO
            and directional_wick <= MOMENTUM_BURST_MAX_DIRECTIONAL_WICK_RATIO * body
        )
        if not ma5_directional or not body_shape_valid:
            return False

        strong_body = (
            body > HIGH_MOMENTUM_BODY_ATR * atr
            or math.isclose(
                body, HIGH_MOMENTUM_BODY_ATR * atr,
                rel_tol=1e-12, abs_tol=0.0,
            )
        )
        rail_break = (
            quote > upper and lower <= opening <= upper
            and body >= MOMENTUM_BURST_BODY_ATR * atr
            if side == 'LONG'
            else quote < lower and lower <= opening <= upper
            and body >= MOMENTUM_BURST_BODY_ATR * atr
        )
        return strong_body or rail_break
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def entry_strategy_type(
    entry_phase: Optional[str] = None,
    strategy_type: Optional[str] = None,
) -> Optional[str]:
    """Classify breakout and authorized post-close continuation phases."""
    if strategy_type == STRATEGY_TYPE_BREAKOUT:
        return STRATEGY_TYPE_BREAKOUT
    if entry_phase == 'POST_CLOSE_CONTINUATION_ENTRY':
        return STRATEGY_TYPE_TREND_CONTINUATION
    if entry_phase == 'REALTIME_RAIL_BREACH':
        return STRATEGY_TYPE_BREAKOUT
    return (
        STRATEGY_TYPE_BREAKOUT
        if entry_phase in BREAKOUT_ENTRY_PHASES
        else None
    )


def long_large_bearish_candle_problem(frame: pd.DataFrame, quote: float) -> Optional[str]:
    """Reject long entries while the forming candle is a decisive bearish dump."""
    try:
        if frame is None or len(frame) < 2:
            return 'BLOCKED_LONG_ENTRY_INVALID_CANDLE_EVIDENCE'
        current, previous = frame.iloc[-1], frame.iloc[-2]
        opening = float(current['open'])
        close = float(current['close']) if bool(current.get('is_closed', True)) else float(quote)
        high = max(float(current['high']), opening, close)
        low = min(float(current['low']), opening, close)
        values = (opening, close, high, low)
        if not all(math.isfinite(value) and value > 0 for value in values):
            return 'BLOCKED_LONG_ENTRY_INVALID_CANDLE_EVIDENCE'
        body = opening - close
        if body <= 0:
            return None
        candle_range = high - low
        if candle_range <= 0:
            return 'BLOCKED_LONG_ENTRY_INVALID_CANDLE_EVIDENCE'
        try:
            atr = float(previous.get('atr', float('nan')))
        except (TypeError, ValueError, OverflowError):
            atr = float('nan')
        atr_threshold_met = (
            math.isfinite(atr) and atr > 0
            and (
                body >= 0.8 * atr
                or math.isclose(body, 0.8 * atr, rel_tol=1e-12, abs_tol=0.0)
            )
        )
        ratio = body / candle_range
        if atr_threshold_met or ratio >= 0.60 or math.isclose(
            ratio, 0.60, rel_tol=1e-12, abs_tol=0.0,
        ):
            return 'BLOCKED_BY_LARGE_BEARISH_CANDLE'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_LONG_ENTRY_INVALID_CANDLE_EVIDENCE'


def entry_market_structure_problem(frame: pd.DataFrame, quote: float,
                                   side: str,
                                   entry_phase: Optional[str] = None,
                                   strategy_type: Optional[str] = None) -> Optional[str]:
    """Require valid KC structure and the room rule for the selected entry phase."""
    try:
        if side not in ('LONG', 'SHORT') or frame is None or frame.empty:
            return 'BLOCKED_INVALID_ENTRY_STRUCTURE'
        if entry_phase in ('MEME_BREAKOUT_ENTRY', 'MEME_AMBUSH_ENTRY', 'MEME_PYRAMID_ADDITION'):
            return None
        if entry_phase == 'REALTIME_RAIL_BREACH':
            if side != 'SHORT':
                return 'BLOCKED_INVALID_REALTIME_RAIL_BREACH_SIDE'
            return (None if is_live_short_lower_rail_breakout(frame, quote)
                    else 'BLOCKED_REALTIME_SHORT_RAIL_REVALIDATION')
        resolved_strategy_type = strategy_type or entry_strategy_type(entry_phase)
        kc_problem = strict_kc_entry_problem(
            frame, quote, side, strategy_type=resolved_strategy_type,
        )
        if kc_problem:
            return kc_problem
        current = frame.iloc[-1]
        if entry_phase == 'KC_2BAR_CLOSED_CONFIRM':
            rail = float(current['kc_upper'] if side == 'LONG' else current['kc_lower'])
            quote = float(quote)
            if (not math.isfinite(rail) or rail <= 0 or not math.isfinite(quote)
                    or (side == 'LONG' and quote <= rail)
                    or (side == 'SHORT' and quote >= rail)):
                return 'BLOCKED_CONFIRMED_BREAKOUT_QUOTE_INSIDE_RAIL'
            return None
        if entry_phase == 'KC_LIVE_OUTER_BREAKOUT':
            if bool(current.get('is_closed', True)):
                return 'BLOCKED_LIVE_BREAKOUT_REQUIRES_FORMING_CANDLE'
            lower, upper = float(current['kc_lower']), float(current['kc_upper'])
            opening, quote = float(current['open']), float(quote)
            if (side == 'LONG' and (quote <= upper or opening < lower)
                    or side == 'SHORT' and (quote >= lower or opening > upper)):
                return 'BLOCKED_LIVE_BREAKOUT_RAIL_REVALIDATION'
            return None
        if entry_phase == 'KC_LIVE_PATTERN_BREAKOUT':
            if bool(current.get('is_closed', True)):
                return 'BLOCKED_LIVE_BREAKOUT_REQUIRES_FORMING_CANDLE'
            lower, upper = float(current['kc_lower']), float(current['kc_upper'])
            opening, quote = float(current['open']), float(quote)
            if (side == 'LONG' and not (lower <= opening <= upper and quote > upper)
                    or side == 'SHORT' and not (lower <= opening <= upper and quote < lower)):
                return 'BLOCKED_LIVE_BREAKOUT_RAIL_REVALIDATION'
            return None
        if entry_phase == 'KC_TOP_PLUNGE_SHORT':
            if side != 'SHORT':
                return 'BLOCKED_INVALID_TOP_PLUNGE_SIDE'
            plunge_decision = EntryGatePipeline.detect_top_plunge_short(frame, quote, side)
            if plunge_decision is None:
                return 'BLOCKED_INVALID_TOP_PLUNGE_STRUCTURE'
            return None
        fast_cross_side = {
            'MA_GOLDEN_CROSS_FAST_LANE': 'LONG',
            'MA_DEATH_CROSS_FAST_LANE': 'SHORT',
        }.get(entry_phase)
        if fast_cross_side is not None:
            if side != fast_cross_side:
                return 'BLOCKED_INVALID_FAST_CROSS_STRUCTURE'
            slope_problem = fast_cross_ma15_slope_problem(frame, quote, side)
            if slope_problem:
                return slope_problem
            if fast_cross_evidence(frame, quote, side) is None:
                return 'BLOCKED_INVALID_FAST_CROSS_STRUCTURE'
            return None
        momentum_side = {
            'KC_MOMENTUM_BURST_LONG': 'LONG',
            'KC_MOMENTUM_BURST_SHORT': 'SHORT',
        }.get(entry_phase)
        if momentum_side is not None:
            evidence = EntryGatePipeline.detect_momentum_burst(
                frame, quote, side=side,
            )
            if (side != momentum_side or evidence is None
                    or evidence.get('entry_phase') != entry_phase):
                return 'BLOCKED_INVALID_MOMENTUM_BURST_STRUCTURE'
            return None
        values = tuple(float(current[key]) for key in (
            'open', 'close', 'kc_upper', 'kc_middle', 'kc_lower', 'ma5', 'ma15',
        )) + (float(quote),)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or not values[4] < values[3] < values[2]):
            return 'BLOCKED_INVALID_ENTRY_STRUCTURE'
        opening, close, upper, middle, lower, ma5, ma15, quote = values
        if entry_phase == 'POST_CLOSE_CONTINUATION_ENTRY':
            return None if side in ('LONG', 'SHORT') else 'BLOCKED_INVALID_ENTRY_STRUCTURE'
        forming = not bool(current.get('is_closed', True))
        if not forming:
            return 'KC_MIDLINE_ENTRY_REQUIRES_LIVE_CANDLE'
        adjustment = quote - close
        ma5 += adjustment / 5.0
        ma15 += adjustment / 15.0

        width = upper - lower
        min_space = MIN_DIRECTIONAL_KC_SPACE
        try:
            atr = float(current.get('atr', float('nan')))
            if not math.isfinite(atr) or atr <= 0:
                closed = closed_entry_candles(frame)
                if not closed.empty:
                    atr = float(closed.iloc[-1].get('atr', float('nan')))
            body = abs(quote - opening)
            if (math.isfinite(atr) and atr > 0
                    and (body > HIGH_MOMENTUM_BODY_ATR * atr
                         or math.isclose(body, HIGH_MOMENTUM_BODY_ATR * atr, rel_tol=1e-12, abs_tol=0.0))):
                min_space = MIN_DIRECTIONAL_KC_SPACE_HIGH_MOMENTUM
        except Exception:
            min_space = MIN_DIRECTIONAL_KC_SPACE

        if side == 'LONG':
            remaining = (upper - quote) / width
            if (resolved_strategy_type != STRATEGY_TYPE_BREAKOUT
                    and remaining < min_space
                    and not math.isclose(remaining, min_space, rel_tol=1e-12, abs_tol=0.0)):
                return 'INSUFFICIENT_UPPER_SPACE'
            if quote < middle and ma5 < ma15:
                return 'BEAR_TREND_NO_LONG'
            if entry_phase == 'KC_MIDLINE_RETEST':
                evidence = EntryGatePipeline._detect_midline_entry(
                    frame, quote, side,
                )
                if evidence is None or evidence.get('entry_phase') != entry_phase:
                    return 'BLOCKED_MIDLINE_RETEST_EVIDENCE'
            elif not (opening <= middle and quote > middle):
                return 'LONG_NOT_CONFIRMED_ABOVE_KC_MIDDLE'
        else:
            remaining = (quote - lower) / width
            if (resolved_strategy_type != STRATEGY_TYPE_BREAKOUT
                    and remaining < min_space
                    and not math.isclose(remaining, min_space, rel_tol=1e-12, abs_tol=0.0)):
                return 'INSUFFICIENT_LOWER_SPACE'
            if (entry_phase != 'KC_BEARISH_MA_MIDLINE_LAUNCH'
                    and quote > middle and ma5 > ma15):
                return 'BULL_TREND_NO_SHORT'
            if entry_phase in (
                'KC_MIDLINE_RETEST',
                'KC_BEARISH_MA_MIDLINE_LAUNCH',
            ):
                evidence = EntryGatePipeline.detect_midline_break_short(
                    frame, quote, side,
                )
                if evidence is None or evidence.get('entry_phase') != entry_phase:
                    return 'BLOCKED_MIDLINE_RETEST_EVIDENCE'
            elif not (opening >= middle and quote < middle):
                return 'SHORT_NOT_FRESH_KC_MIDDLE_BREAK'
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_INVALID_ENTRY_STRUCTURE'


class EntryGatePipeline:
    """The single authorization funnel for all trade entries.

    Ensures that no entry can bypass the spatial geometric checks,
    candle solidity gate, or mouth expansion gate.
    """

    def __init__(self):
        self.market_regime_provider: Optional[Callable[[str], str]] = None
        self.gates = [
            ('CANDLE_SOLIDITY', CandleSolidityGate.evaluate),
            ('CHOP_FILTER', ChopFilterGate.evaluate),
            ('MOUTH_EXPANSION', MouthExpansionGate.evaluate),
            ('CONTINUATION_MA5_FILTER', ContinuationMa5Gate.evaluate),
        ]

    def set_market_regime_provider(
        self, provider: Optional[Callable[[str], str]],
    ) -> None:
        self.market_regime_provider = provider

    @staticmethod
    def has_strong_bullish_breakout(frame: pd.DataFrame, quote: float) -> bool:
        """Fresh bullish KC expansion may override only an explicit AI CHOPPY state."""
        try:
            if frame is None or len(frame) < 2:
                return False
            live, previous = frame.iloc[-1], frame.iloc[-2]
            price = float(quote)
            opening = float(live['open'])
            upper = float(live['kc_upper'])
            live_close = float(live['close'])
            atr = float(previous['atr'])
            current_ma5 = float(live['ma5'])
            previous_ma5 = float(previous['ma5'])
            if not bool(live.get('is_closed', True)):
                current_ma5 += (price - live_close) / 5.0
            values = (price, opening, upper, atr, current_ma5, previous_ma5, live_close)
            return (
                all(math.isfinite(value) and value > 0 for value in values)
                and atr > 0 and price > upper
                and price - opening >= 0.5 * atr
                and current_ma5 - previous_ma5 >= 0
            )
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            return False

    @staticmethod
    def has_strong_bearish_breakout(frame: pd.DataFrame, quote: float) -> bool:
        """Fresh bearish KC expansion may override only an explicit AI CHOPPY state."""
        try:
            if frame is None or len(frame) < 2:
                return False
            live, previous = frame.iloc[-1], frame.iloc[-2]
            price = float(quote)
            opening = float(live['open'])
            lower = float(live['kc_lower'])
            live_close = float(live['close'])
            atr = float(previous['atr'])
            current_ma5 = float(live['ma5'])
            previous_ma5 = float(previous['ma5'])
            if not bool(live.get('is_closed', True)):
                current_ma5 += (price - live_close) / 5.0
            values = (price, opening, lower, atr, current_ma5, previous_ma5, live_close)
            return (
                all(math.isfinite(value) and value > 0 for value in values)
                and atr > 0 and price < lower
                and opening - price >= 0.5 * atr
                and current_ma5 - previous_ma5 <= 0
            )
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            return False

    @staticmethod
    def has_bearish_chop_breakout_override(frame: pd.DataFrame, quote: float) -> bool:
        """Recognize a live bearish MA cross or lower-rail break inside CHOP."""
        try:
            if frame is None or len(frame) < 2:
                return False
            live, previous = frame.iloc[-1], frame.iloc[-2]
            quote = float(quote)
            opening = float(live['open'])
            close = float(live['close'])
            lower = float(live['kc_lower'])
            atr = float(previous['atr'])
            adjustment = quote - close if not bool(live.get('is_closed', True)) else 0.0
            ma5 = float(live['ma5']) + adjustment / 5.0
            ma15 = float(live['ma15']) + adjustment / 15.0
            previous_ma5 = float(previous['ma5'])
            previous_ma15 = float(previous['ma15'])
            values = (quote, opening, close, lower, atr, ma5, ma15,
                      previous_ma5, previous_ma15)
            if not all(math.isfinite(value) and value > 0 for value in values):
                return False
            bearish_impulse = quote < opening
            death_cross_below_ma5 = (
                previous_ma5 >= previous_ma15
                and ma5 < ma15
                and ma5 < previous_ma5
                and quote < ma5
            )
            lower_rail_break = is_live_short_lower_rail_breakout(frame, quote)
            return bearish_impulse and (death_cross_below_ma5 or lower_rail_break)
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            return False

    @staticmethod
    def has_explosive_bullish_kc_breakout(frame: pd.DataFrame, quote: float) -> bool:
        """Require SpatialBrain expansion plus a live, solid close above KC upper."""
        try:
            if frame is None or len(frame) < 3:
                return False
            if SpatialBrain.analyze(frame, quote).state != 'EXPLOSIVE_EXPANSION':
                return False
            live = frame.iloc[-1]
            closed = closed_entry_candles(frame)
            if closed.empty:
                return False
            price = float(quote)
            opening = float(live['open'])
            upper = float(live['kc_upper'])
            live_close = float(live['close'])
            atr = float(closed.iloc[-1]['atr'])
            effective_close = live_close if bool(live.get('is_closed', True)) else price
            values = (price, opening, upper, effective_close, atr)
            return (
                all(math.isfinite(value) and value > 0 for value in values)
                and atr > 0 and price > upper
                and effective_close - opening >= 0.5 * atr
            )
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            return False

    @staticmethod
    def has_explosive_bearish_kc_breakout(frame: pd.DataFrame, quote: float) -> bool:
        """Require SpatialBrain expansion plus a live, solid close below KC lower."""
        try:
            if frame is None or len(frame) < 3:
                return False
            if SpatialBrain.analyze(frame, quote).state != 'EXPLOSIVE_EXPANSION':
                return False
            live = frame.iloc[-1]
            closed = closed_entry_candles(frame)
            if closed.empty:
                return False
            price = float(quote)
            opening = float(live['open'])
            lower = float(live['kc_lower'])
            live_close = float(live['close'])
            atr = float(closed.iloc[-1]['atr'])
            effective_close = live_close if bool(live.get('is_closed', True)) else price
            values = (price, opening, lower, effective_close, atr)
            return (
                all(math.isfinite(value) and value > 0 for value in values)
                and atr > 0 and price < lower
                and opening - effective_close >= 0.5 * atr
            )
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            return False

    @staticmethod
    def has_meme_impulse_breakout(symbol: Optional[str], frame, quote) -> bool:
        """Recognize a lower-body, live KC break for the configured meme coins."""
        try:
            if (not is_meme_breakout_symbol(symbol)
                    or frame is None or len(frame) < 3):
                return False
            if SpatialBrain.analyze(frame, quote).state != 'EXPLOSIVE_EXPANSION':
                return False
            live = frame.iloc[-1]
            closed = closed_entry_candles(frame)
            if closed.empty:
                return False
            price, opening = float(quote), float(live['open'])
            upper, lower = float(live['kc_upper']), float(live['kc_lower'])
            close = float(live['close'])
            atr = float(closed.iloc[-1]['atr'])
            effective_close = close if bool(live.get('is_closed', True)) else price
            body = effective_close - opening
            return (all(math.isfinite(v) and v > 0 for v in
                        (price, opening, upper, lower, effective_close, atr))
                    and atr > 0
                    and ((price > upper and body >= MEME_BREAKOUT_BODY_ATR * atr)
                         or (price < lower and -body >= MEME_BREAKOUT_BODY_ATR * atr)))
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            return False

    has_consecutive_momentum_breakout = staticmethod(has_consecutive_momentum_breakout)

    def market_regime_problem(self, symbol: Optional[str], frame=None,
                              quote: Optional[float] = None) -> Optional[str]:
        meme_impulse = self.has_meme_impulse_breakout(symbol, frame, quote)
        consecutive_momentum = self.has_consecutive_momentum_breakout(frame, quote)
        if consecutive_momentum:
            return None
        if (frame is not None and quote is not None
                and self.has_bearish_chop_breakout_override(frame, quote)):
            return None
        if not callable(self.market_regime_provider):
            return None if meme_impulse else 'BLOCKED_BY_AI_CHOP_REGIME'
        try:
            regime = self.market_regime_provider(symbol or "")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "AI market-regime lookup failed for %s: %s: %s",
                symbol, type(exc).__name__, exc,
            )
            return 'BLOCKED_BY_AI_CHOP_REGIME'
        if regime == 'TRENDING':
            return None
        if meme_impulse or consecutive_momentum:
            return None
        if (regime == 'CHOPPY' and frame is not None and quote is not None
                and (self.has_strong_bullish_breakout(frame, quote)
                     or self.has_strong_bearish_breakout(frame, quote))):
            return None
        return 'BLOCKED_BY_AI_CHOP_REGIME'

    @staticmethod
    def chop_lockout_problem(frame: pd.DataFrame, quote: float, *,
                             allow_flat_lines: bool = False,
                             allow_unknown_direction: bool = False,
                             allow_ma_tangle: bool = False,
                             signal_code: Optional[str] = None) -> Optional[str]:
        """Fail closed on unknown/flat KC, tangled MAs, or weak in-channel candles."""
        try:
            if frame is None or frame.empty:
                return 'BLOCKED_BY_CHOP_GATE_DATA'
            if EntryGatePipeline.has_bearish_chop_breakout_override(frame, quote):
                return None
            closed = closed_entry_candles(frame)
            if len(closed) < 2:
                return 'BLOCKED_BY_CHOPPY_UNKNOWN_DIRECTION'
            live = frame.iloc[-1]
            current = closed.iloc[-1]
            previous = closed.iloc[-2]
            atr = float(current['atr'])
            upper, lower, middle = (
                float(live[key]) for key in ('kc_upper', 'kc_lower', 'kc_middle')
            )
            previous_upper, previous_lower = (
                float(previous[key]) for key in ('kc_upper', 'kc_lower')
            )
            quote = float(quote)
            values = (atr, upper, lower, middle, previous_upper, previous_lower, quote)
            if (not all(math.isfinite(value) and value > 0 for value in values)
                    or atr <= 0 or not lower < middle < upper):
                return 'BLOCKED_BY_CHOP_GATE_DATA'

            channel_state = live.get('channel_state', current.get('channel_state'))
            if not isinstance(channel_state, str) or not channel_state.strip():
                channel_state = ck_direction(closed, has_forming_bar=False)
            channel_state = str(channel_state).strip()
            known_directions = ('LONG', 'SHORT', 'UP', 'DOWN')

            upper_slope_value = live.get('kc_upper_slope', current.get('kc_upper_slope'))
            lower_slope_value = live.get('kc_lower_slope', current.get('kc_lower_slope'))
            upper_slope = (
                float(upper_slope_value)
                if upper_slope_value is not None and math.isfinite(float(upper_slope_value))
                else float(current['kc_upper']) - previous_upper
            )
            lower_slope = (
                float(lower_slope_value)
                if lower_slope_value is not None and math.isfinite(float(lower_slope_value))
                else float(current['kc_lower']) - previous_lower
            )
            if not allow_unknown_direction and channel_state not in known_directions:
                return 'BLOCKED_BY_CHOPPY_UNKNOWN_DIRECTION'
            if (not allow_unknown_direction and not allow_flat_lines and (
                    abs(upper_slope) <= CHOP_KC_SLOPE_EPSILON_ATR * atr
                    and abs(lower_slope) <= CHOP_KC_SLOPE_EPSILON_ATR * atr
            )):
                return 'BLOCKED_BY_CHOPPY_UNKNOWN_DIRECTION'

            if is_ma_tangling(
                frame, quote,
                signal_code=signal_code,
                allow_ma_tangle=allow_ma_tangle,
            ):
                return 'BLOCKED_BY_MA_TANGLING'

            opening = float(live['open'])
            close = float(live['close']) if bool(live.get('is_closed', True)) else quote
            if not math.isfinite(opening) or opening <= 0:
                return 'BLOCKED_BY_CHOP_GATE_DATA'
            if lower <= quote <= upper and abs(close - opening) <= CHOP_MIN_CHANNEL_BODY_ATR * atr:
                return 'BLOCKED_BY_CHOPPY_CHANNEL_BODY'

            # A trend label or breakout impulse must not hide a flat entry
            # structure. All three KC rails and both moving averages need
            # meaningful movement on the latest completed 1m candle.
            flat_threshold = CHOP_KC_SLOPE_EPSILON_ATR * atr
            line_pairs = (
                ('KC_UPPER', 'kc_upper'),
                ('KC_MIDDLE', 'kc_middle'),
                ('KC_LOWER', 'kc_lower'),
                ('MA5', 'ma5'),
                ('MA15', 'ma15'),
            )
            for line_name, key in line_pairs:
                line_slope = float(current[key]) - float(previous[key])
                if not math.isfinite(line_slope):
                    return 'BLOCKED_BY_CHOP_GATE_DATA'
                if abs(line_slope) <= flat_threshold and not allow_flat_lines:
                    return f'BLOCKED_BY_FLAT_ENTRY_LINE_{line_name}'
            return None
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            return 'BLOCKED_BY_CHOP_GATE_DATA'

    @staticmethod
    def _ma15_bias_problem(frame: pd.DataFrame, quote: float, side: str,
                           allow_flat_slope: bool = False,
                           allow_directional_breakout: bool = False) -> Optional[str]:
        """Require trend-side MA alignment and reject entries stretched from MA15."""
        try:
            if frame is None or frame.empty or side not in ('LONG', 'SHORT'):
                return 'BLOCKED_MA_BIAS_DATA'
            closed = closed_entry_candles(frame)
            if len(closed) < 1 or len(frame) < 2:
                return 'BLOCKED_MA_BIAS_DATA'
            live = frame.iloc[-1]
            previous = frame.iloc[-2]
            quote = float(quote)
            live_close = float(live['close'])
            delta = quote - live_close
            ma15 = float(live['ma15']) + delta / 15.0
            latest_ma15 = ma15
            previous_ma15 = float(previous['ma15'])
            atr = float(closed.iloc[-1]['atr'])
            values = (quote, ma15, latest_ma15, previous_ma15, atr)
            if (not all(math.isfinite(value) and value > 0 for value in values)
                    or atr <= 0):
                return 'BLOCKED_MA_BIAS_DATA'

            if abs(quote - ma15) > MAX_MA15_ENTRY_BIAS_ATR * atr:
                return 'BLOCKED_EXTREME_MA_BIAS'

            ma15_slope = latest_ma15 - previous_ma15
            if (not allow_directional_breakout and side == 'LONG'
                    and (quote <= ma15 or ma15_slope < 0)):
                return 'BLOCKED_MA_BIAS_DIRECTION'
            if (not allow_directional_breakout and side == 'SHORT'
                    and (quote >= ma15 or ma15_slope > 0)):
                return 'BLOCKED_MA_BIAS_DIRECTION'
            return None
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            return 'BLOCKED_MA_BIAS_DATA'

    @staticmethod
    def short_breakout_failure_problem(frame: pd.DataFrame, quote: float,
                                       opening: float, atr: float) -> Optional[str]:
        """Reject late short breaks and lower-wick absorption at the KC floor."""
        try:
            closed = closed_entry_candles(frame)
            if len(closed) < 2 or frame is None or frame.empty:
                return 'BLOCKED_SHORT_BREAKOUT_HISTORY'
            live = frame.iloc[-1]
            quote, opening, atr = float(quote), float(opening), float(atr)
            high = max(float(live['high']), quote)
            low = min(float(live['low']), quote)
            span = high - low
            lower_wick = min(opening, quote) - low
            if span <= 0 or lower_wick / span >= 0.35:
                return 'BLOCKED_SHORT_BREAKOUT_LOWER_WICK'

            # Count consecutive bearish bodies through the forming quote.
            down_bars = 1 if quote < opening else 0
            for _, candle in closed.iloc[::-1].iterrows():
                if float(candle['close']) < float(candle['open']):
                    down_bars += 1
                else:
                    break
            if down_bars == 0 or down_bars > 2:
                return 'BLOCKED_SHORT_BREAKOUT_LATE_SELL'

            if down_bars == 1:
                first_open = opening
                first_middle = float(live['kc_middle'])
            else:
                first = closed.iloc[-(down_bars - 1)]
                first_open = float(first['open'])
                first_middle = float(first['kc_middle'])
            prior = closed.iloc[-1]
            prior_close = float(prior['close'])
            prior_middle = float(prior['kc_middle'])
            if first_open < first_middle and prior_close < prior_middle:
                return 'BLOCKED_SHORT_BREAKOUT_NOT_FROM_KC_MIDDLE'

            live_close = float(live['close'])
            ma5 = float(live['ma5']) + (quote - live_close) / 5.0
            ma15 = float(live['ma15']) + (quote - live_close) / 15.0
            nearest_ma_distance_atr = min(ma5 - quote, ma15 - quote) / atr
            if (not math.isfinite(nearest_ma_distance_atr)
                    or nearest_ma_distance_atr < 0
                    or nearest_ma_distance_atr > 1.5):
                return 'BLOCKED_SHORT_BREAKOUT_EXTENDED_FROM_MA'
            return None
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            return 'BLOCKED_SHORT_BREAKOUT_HISTORY'

    @staticmethod
    def detect_realtime_breakout(frame: pd.DataFrame, quote: float,
                                 side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return a live breakout only when impulse, participation and trend agree."""
        if frame is None or len(frame) < 2:
            return None

        curr = frame.iloc[-1]
        prev = frame.iloc[-2]

        try:
            quote = float(quote)
            open_p = float(curr['open'])
            high_p = max(float(curr['high']), quote)
            low_p = min(float(curr['low']), quote)
            atr = float(prev.get('atr', curr.get('atr', 0.0)))
            kc_upper = float(curr['kc_upper'])
            kc_lower = float(curr['kc_lower'])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if (not all(math.isfinite(value) and value > 0
                    for value in (quote, open_p, high_p, low_p, atr, kc_upper, kc_lower))
                or atr <= 0 or kc_lower >= kc_upper
                or high_p < max(open_p, quote) or low_p > min(open_p, quote)):
            return None
        # Volume is diagnostic only. The authorized breakout contract is based
        # on live price/body, outer rail, ATR distance, and MA5 direction; a
        # sparse feed that omits volume must not silently disable that route.
        volume_ratio = None
        if 'volume' in frame.columns and len(frame) >= 6:
            try:
                current_volume = float(curr['volume'])
                reference_volumes = [float(value) for value in frame['volume'].iloc[-6:-1]]
                reference_volume = sum(reference_volumes) / len(reference_volumes)
                if (all(math.isfinite(value) and value >= 0 for value in reference_volumes)
                        and math.isfinite(current_volume) and current_volume >= 0
                        and reference_volume > 0):
                    volume_ratio = current_volume / reference_volume
            except (KeyError, TypeError, ValueError, OverflowError):
                volume_ratio = None
        candle_range = high_p - low_p
        if candle_range <= 0:
            return None
        try:
            live_close = float(curr['close'])
            ma5 = float(curr['ma5']) + (quote - live_close) / 5.0
            ma15 = float(curr['ma15']) + (quote - live_close) / 15.0
            previous_ma5 = float(prev['ma5'])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if not all(math.isfinite(value) and value > 0
                   for value in (ma5, ma15, previous_ma5)):
            return None

        # Check Long
        long_open_context_ok = kc_lower <= open_p <= kc_upper or open_p > kc_upper
        if (side is None or side == 'LONG') and kc_upper > 0 and long_open_context_ok:
            if quote > kc_upper:
                body_long = quote - open_p
                distance_atr = (quote - kc_upper) / atr
                # The approved live-breakout contract uses a half-ATR body,
                # same-side opening context, current quote outside the rail,
                # a 3-ATR chase cap, and a strictly rising live MA5.
                if (body_long >= 0.5 * atr
                        and distance_atr <= 3.0
                        and ma5 > previous_ma5):
                    return {
                        'type': 'AUTHORIZED_REALTIME_BREAKOUT',
                        'side': 'LONG',
                        'price': quote,
                        'realtime_body_atr': body_long / atr,
                        'ma5_direction_aligned': True,
                        'realtime_distance_atr': distance_atr,
                        'realtime_volume_ratio': volume_ratio,
                        'is_breakout': True,
                        'override_cooldown': True,
                        'confirmation_bar_id': float(curr.get('timestamp', 0)),
                        'breakout_bar_id': float(curr.get('timestamp', 0)),
                        'pending_signal_id': f"RT_BREAKOUT:LONG:{curr.get('timestamp', 0)}",
                        'entry_phase': 'KC_LIVE_OUTER_BREAKOUT',
                        'reason': 'AUTHORIZED_REALTIME_BREAKOUT',
                    }

        # Check Short
        short_open_context_ok = kc_lower <= open_p <= kc_upper or open_p < kc_lower
        if (side is None or side == 'SHORT') and kc_lower > 0 and short_open_context_ok:
            if quote < kc_lower:
                body_short = open_p - quote
                distance_atr = (kc_lower - quote) / atr
                if (body_short >= 0.5 * atr
                        and distance_atr <= 3.0
                        and ma5 < previous_ma5):
                    return {
                        'type': 'AUTHORIZED_REALTIME_BREAKOUT',
                        'side': 'SHORT',
                        'price': quote,
                        'realtime_body_atr': body_short / atr,
                        'ma5_direction_aligned': True,
                        'short_breakout_early': True,
                        'realtime_distance_atr': distance_atr,
                        'realtime_volume_ratio': volume_ratio,
                        'is_breakout': True,
                        'override_cooldown': True,
                        'confirmation_bar_id': float(curr.get('timestamp', 0)),
                        'breakout_bar_id': float(curr.get('timestamp', 0)),
                        'pending_signal_id': f"RT_BREAKOUT:SHORT:{curr.get('timestamp', 0)}",
                        'entry_phase': 'KC_LIVE_OUTER_BREAKOUT',
                        'reason': 'AUTHORIZED_REALTIME_BREAKOUT',
                    }

        return None

    @staticmethod
    def detect_downtrend_pullback_resume_short(
        frame: pd.DataFrame, quote: float,
    ) -> Optional[Dict[str, Any]]:
        """Enter the first small bearish body that breaks a tight downtrend base."""
        if frame is None or len(frame) < 5:
            return None
        try:
            quote = float(quote)
            current = frame.iloc[-1]
            closed = closed_entry_candles(frame)
            is_closed = bool(current.get('is_closed', False))
            prior = closed.iloc[:-1] if is_closed else closed
            if (len(prior) < 3 or not math.isfinite(quote) or quote <= 0):
                return None
            base = prior.tail(3)
            before = prior.iloc[-4:] if len(prior) >= 4 else prior
            if len(before) < 3:
                return None
            atr = float(prior.iloc[-1]['atr'])
            opened = float(current['open'])
            live_ma5 = float(current['ma5'])
            base_low = float(base['low'].astype(float).min())
            base_high = float(base['high'].astype(float).max())
            middle = [float(value) for value in before['kc_middle']]
            ma5_values = [float(value) for value in before['ma5']]
            ma15_values = [float(value) for value in before['ma15']]
            values = [quote, atr, opened, live_ma5, base_low, base_high, *middle, *ma5_values, *ma15_values]
            if (not all(math.isfinite(value) and value > 0 for value in values)
                    or atr <= 0 or quote >= opened):
                return None
            if not (middle[-1] < middle[-2] < middle[-3]
                    and ma5_values[-1] < ma15_values[-1]
                    and ma5_values[-1] < ma5_values[-2]
                    and ma15_values[-1] <= ma15_values[-2]):
                return None
            if base_high - base_low > 2.0 * atr:
                return None
            body_atr = (opened - quote) / atr
            if not 0.10 <= body_atr <= 0.75:
                return None
            breaks_base = opened >= base_low and quote < base_low
            breaks_ma5 = (
                opened >= live_ma5 and quote < live_ma5
                and float(prior.iloc[-1]['close']) >= ma5_values[-1]
            )
            if not (breaks_base or breaks_ma5):
                return None
            stamp = float(current.get('timestamp', 0))
            if not math.isfinite(stamp) or stamp <= 0:
                return None
            return {
                'type': 'KC_DOWNTREND_PULLBACK_RESUME_SHORT',
                'side': 'SHORT', 'price': quote,
                'reason': 'KC_DOWNTREND_PULLBACK_RESUME_SHORT',
                'entry_phase': 'KC_DOWNTREND_PULLBACK_RESUME_SHORT',
                'is_breakout': True, 'ma5_direction_aligned': True,
                'realtime_body_atr': body_atr,
                'consolidation_low': base_low,
                'break_reference': 'CONSOLIDATION_LOW' if breaks_base else 'MA5',
                'confirmation_bar_id': stamp, 'breakout_bar_id': stamp,
                'pending_signal_id': f'DOWNTREND_RESUME:SHORT:{int(stamp)}',
            }
        except (KeyError, TypeError, ValueError, OverflowError, IndexError):
            return None

    @staticmethod
    def detect_trend_continuation(frame: pd.DataFrame, quote: float,
                                  side: Optional[str] = None,
                                  symbol: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Gate 4：平倉後的「延續開倉」與「MA5 動能過濾」（以及破軌沒開倉後面延續開倉）。
        
        1. MA5 逆勢過濾禁令：
           - 禁開多單：若當前 MA5 斜率向下（或短線走弱下拐，ma5 <= prev_ma5），一律嚴禁開多單。
           - 禁開空單：若當前 MA5 斜率向上（或短線走強上拐，ma5 >= prev_ma5），一律嚴禁開空單。
        2. 延續開倉情境（Re-entry & Missed Breakout）：
           - 在部位平倉出場後，或突破當下未開到倉：
             * 多單：大趨勢未變（MA15 向上且 KC 中軌向上），MA5 絕對向上，價格站穩在 MA5 之上（或在 KC 上軌外順向推進）；
             * 空單：大趨勢未變（MA15 向下且 KC 中軌向下），MA5 絕對向下，價格壓制在 MA5 之下（或在 KC 下軌外順向推進）；
             * 價格退回通道內或跌破均線時一律拒絕！
        3. 妖幣專用極速捕捉與延續開倉（強制上車）：
           - 對於龍蝦、CAP 等妖幣標的，若價格維持在 KC 外軌狂奔且 MA5 向上，強制觸發延續上車！
        """
        if frame is None or len(frame) < 2:
            return None

        curr = frame.iloc[-1]
        prev = frame.iloc[-2]

        # ── 0. 妖幣專屬：KC 破軌極速捕捉與延續開倉（強制上車）──
        from core.gates.meme_coin_gate import MemeCoinGate
        if symbol and MemeCoinGate.is_meme_coin(symbol):
            meme_sides = [side] if side in ('LONG', 'SHORT') else ['LONG', 'SHORT']
            for s in meme_sides:
                meme_passed, meme_reason, meme_meta = MemeCoinGate.evaluate_meme_entry(
                    frame, quote, s, symbol=symbol,
                )
                if meme_passed:
                    return {
                        'type': f'AUTHORIZED_BY_TREND_CONTINUATION_{s}',
                        'side': s,
                        'price': quote,
                        'is_trend_continuation': True,
                        'override_cooldown': True,
                        'confirmation_bar_id': float(curr.get('timestamp', 0)),
                        'breakout_bar_id': float(curr.get('timestamp', 0)),
                        'pending_signal_id': f"MEME_CONT:{s}:{curr.get('timestamp', 0)}",
                        'entry_phase': 'KC_CONTINUATION_ENTRY',
                        'reason': meme_reason,
                    }

        try:
            quote = float(quote)
            open_p = float(curr['open'])
            high_p = max(float(curr['high']), quote)
            low_p = min(float(curr['low']), quote)
            live_close = float(curr['close'])
            ma5 = float(curr['ma5']) + (quote - live_close) / 5.0
            ma15 = float(curr['ma15']) + (quote - live_close) / 15.0
            prev_ma15 = float(prev['ma15'])
            prev_ma5 = float(prev['ma5'])
            kc_upper = float(curr.get('kc_upper', 0.0))
            kc_lower = float(curr.get('kc_lower', 0.0))
            kc_mid = float(curr.get('kc_middle', 0.0))
            prev_kc_mid = float(prev.get('kc_middle', 0.0))
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if not all(math.isfinite(value) and value > 0
                   for value in (
                       quote, open_p, high_p, low_p, live_close,
                       ma5, ma15, prev_ma15, prev_ma5,
                   )):
            return None
        candle_range = high_p - low_p
        if candle_range <= 0 or high_p < max(open_p, quote) or low_p > min(open_p, quote):
            return None
        body = abs(quote - open_p)
        solidity_ratio = body / candle_range

        # ── 1. 做多順勢延續（Re-entry & Missed Breakout Continuation）──
        if side is None or side == 'LONG':
            # 禁開多單：若當前 MA5 斜率向下或走平，一律嚴禁開多單
            if ma5 > prev_ma5 and ma5 > ma15 and quote > ma5:
                # 情境 A: 均線順勢延續 (陽線站上 MA5 且 MA15 向上)
                is_trend_ride = (
                    solidity_ratio >= 0.45 and ma15 > prev_ma15 and quote > open_p
                )
                # 情境 B: 破軌沒開倉或平倉後軌外順向延續
                is_outer_continuation = (
                    kc_upper > 0 and quote > kc_upper
                    and (prev_kc_mid <= 0 or kc_mid >= prev_kc_mid)
                    and (quote > open_p or (high_p - quote) <= 0.4 * body)
                )
                if is_trend_ride or is_outer_continuation:
                    return {
                        'type': 'AUTHORIZED_BY_TREND_CONTINUATION_LONG',
                        'side': 'LONG',
                        'price': quote,
                        'is_trend_continuation': True,
                        'override_cooldown': True,
                        'confirmation_bar_id': float(curr.get('timestamp', 0)),
                        'breakout_bar_id': float(curr.get('timestamp', 0)),
                        'pending_signal_id': f"TREND_CONT:LONG:{curr.get('timestamp', 0)}",
                        'entry_phase': 'KC_CONTINUATION_ENTRY',
                        'reason': 'AUTHORIZED_BY_TREND_CONTINUATION_LONG',
                    }

        # ── 2. 做空順勢延續（Re-entry & Missed Breakout Continuation）──
        if side is None or side == 'SHORT':
            # 禁開空單：若當前 MA5 斜率向上或走平，一律嚴禁開空單
            if ma5 < prev_ma5 and ma5 < ma15 and quote < ma5:
                # 情境 A: 均線順勢延續 (陰線壓制 MA5 且 MA15 向下)
                is_trend_ride = (
                    ma15 <= prev_ma15 and quote < open_p
                )
                # 情境 B: 破軌沒開倉或平倉後軌外順向延續
                is_outer_continuation = (
                    kc_lower > 0 and quote < kc_lower
                    and (prev_kc_mid <= 0 or kc_mid <= prev_kc_mid)
                    and (quote < open_p or (quote - low_p) <= 0.4 * body)
                )
                if is_trend_ride or is_outer_continuation:
                    return {
                        'type': 'AUTHORIZED_BY_TREND_CONTINUATION_SHORT',
                        'side': 'SHORT',
                        'price': quote,
                        'is_trend_continuation': True,
                        'override_cooldown': True,
                        'confirmation_bar_id': float(curr.get('timestamp', 0)),
                        'breakout_bar_id': float(curr.get('timestamp', 0)),
                        'pending_signal_id': f"TREND_CONT:SHORT:{curr.get('timestamp', 0)}",
                        'entry_phase': 'KC_CONTINUATION_ENTRY',
                        'reason': 'AUTHORIZED_BY_TREND_CONTINUATION_SHORT',
                    }

        return None

    @staticmethod
    def detect_shadow_retest_long(frame: pd.DataFrame, quote: float,
                                  side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """均線下影線回踩蓄勢開多 (Shadow Retest Entry).
        
        當大級別為多頭環境（channel_state == "KC向上" 或 ma15_slope > 0 且 ma5 > ma15）：
        1. 【下影線回踩判定（有影子）】：
           - K 棒低點觸及或回踩 MA5 / MA15 / KC 中軌；
           - 帶有明確下影線：lower_shadow >= 0.35 * (High - Low) 或 lower_shadow >= 0.25 * ATR；
           - 收盤價站穩在 MA15 與 KC 中軌之上。
        2. 【豁免與進場授權】：
           - 即使當根收盤微幅偏紅（Close < Open），只要滿足上述下影線支撐：
             * 豁免「紅 K 一票否決」，認定為「多頭踩線蓄勢」！
             * 授權 AUTHORIZED_SHADOW_RETEST_ENTRY，允許在踩線確認或次根轉陽瞬間提前開多。
        """
        if side not in (None, 'LONG') or frame is None or len(frame) < 2:
            return None

        curr = frame.iloc[-1]
        prev = frame.iloc[-2]

        try:
            quote = float(quote)
            open_p = float(curr['open'])
            high_p = max(float(curr['high']), quote)
            low_p = min(float(curr['low']), quote)
            live_close = float(curr['close'])
            effective_close = live_close if bool(curr.get('is_closed', True)) else quote
            
            atr = float(prev.get('atr', curr.get('atr', 0.0)))
            if atr <= 0:
                atr = float(curr.get('atr', 1.0))

            ma5 = float(curr.get('ma5', live_close))
            ma15 = float(curr.get('ma15', live_close))
            prev_ma15 = float(prev.get('ma15', ma15))
            ma15_slope = ma15 - prev_ma15

            kc_middle = float(curr.get('kc_middle', curr.get('kc_basis', 0.0)))
            channel_state = str(curr.get('channel_state', '')).strip()
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

        if not all(math.isfinite(v) and v > 0 for v in (quote, open_p, high_p, low_p, effective_close, atr)):
            return None
        if long_large_bearish_candle_problem(frame, quote):
            return None

        # ── 1. 大級別多頭環境判定 ──
        # channel_state == "KC向上" 或 "UP" 或 (ma15_slope > 0 且 ma5 > ma15)
        is_kc_up = (channel_state in ('KC向上', 'UP', 'BULLISH', 'LONG'))
        is_ma_bullish = (ma15_slope > 0 and ma5 > ma15)
        if not (is_kc_up or is_ma_bullish):
            return None

        # ── 2. 下影線回踩判定 ──
        support_lines = [line for line in (ma5, ma15, kc_middle) if line > 0]
        if not support_lines:
            return None
        touched_support = (
            low_p <= max(support_lines) * 1.002
            or any(abs(low_p - line) <= 0.20 * atr for line in support_lines)
        )
        if not touched_support:
            return None

        # 帶有明確下影線：lower_shadow >= 0.35 * (High - Low) 或 lower_shadow >= 0.25 * ATR
        candle_range = high_p - low_p
        lower_shadow = min(open_p, effective_close) - low_p
        has_lower_shadow = (
            (candle_range > 0 and lower_shadow >= 0.35 * candle_range)
            or (atr > 0 and lower_shadow >= 0.25 * atr)
        )
        if not has_lower_shadow:
            return None

        # 收盤價站穩在 MA15 與 KC 中軌之上
        if effective_close < ma15 or (kc_middle > 0 and effective_close < kc_middle):
            return None

        bar_id = float(curr.get('timestamp', 0))
        return {
            'type': 'AUTHORIZED_SHADOW_RETEST_ENTRY',
            'side': 'LONG',
            'price': quote,
            'is_shadow_retest': True,
            'is_breakout': True,
            'override_cooldown': True,
            'confirmation_bar_id': bar_id,
            'breakout_bar_id': bar_id,
            'pending_signal_id': f"SHADOW_RETEST:LONG:{bar_id}",
            'entry_phase': 'KC_SHADOW_RETEST_ENTRY',
            'reason': 'AUTHORIZED_SHADOW_RETEST_ENTRY',
            'ma5_direction_aligned': True,
            'lower_shadow': lower_shadow,
            'atr': atr,
        }

    @staticmethod
    def detect_reversal_flip(frame: pd.DataFrame, quote: float,
                             side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """情境 2：頂底轉折與反手（Reversal Flip - 帶量吞噬秒換向）。
        
        - 多單見頂平倉後（或頂部反轉），若次根走出飽滿大陰線跌破中軌/MA5：無條件立即反手開空！
          (AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT)
        - 空單見底平倉後（或底部反轉），若次根走出飽滿大陽線穿透中軌/MA5：無條件立即反手開多！
          (AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG)
        - 廢除平倉冷卻時間 (override_cooldown=True)，廢除所有阻礙反手的多餘門檻！
        """
        if frame is None or len(frame) < 2:
            return None

        curr = frame.iloc[-1]

        open_p = float(curr.get('open', quote))
        close_p = float(quote)
        high_p = max(float(curr.get('high', quote)), quote)
        low_p = min(float(curr.get('low', quote)), quote)

        body = abs(close_p - open_p)
        candle_range = high_p - low_p + 1e-9
        solidity_ratio = body / candle_range

        # 飽滿大實體 (實體佔比 >= 50%)
        if solidity_ratio < 0.50:
            return None

        ma5 = float(curr.get('ma5', quote))
        kc_middle = float(curr.get('kc_middle', curr.get('kc_basis', 0.0)))

        # ── 1. 見頂反手開空 (Flip to Short) ──
        if side is None or side == 'SHORT':
            is_bearish = close_p < open_p
            breaks_down = close_p < ma5 or (kc_middle > 0 and close_p < kc_middle)
            if is_bearish and breaks_down:
                return {
                    'type': 'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
                    'side': 'SHORT',
                    'price': quote,
                    'is_reversal_flip': True,
                    'confirmation_bar_id': float(curr.get('timestamp', 0)),
                    'breakout_bar_id': float(curr.get('timestamp', 0)),
                    'pending_signal_id': f"FLIP:SHORT:{curr.get('timestamp', 0)}",
                    'entry_phase': 'KC_REVERSAL_FLIP',
                    'reason': 'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
                }

        # ── 2. 見底反手開多 (Flip to Long) ──
        if side is None or side == 'LONG':
            is_bullish = close_p > open_p
            breaks_up = close_p > ma5 or (kc_middle > 0 and close_p > kc_middle)
            if is_bullish and breaks_up:
                return {
                    'type': 'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
                    'side': 'LONG',
                    'price': quote,
                    'is_reversal_flip': True,
                    'confirmation_bar_id': float(curr.get('timestamp', 0)),
                    'breakout_bar_id': float(curr.get('timestamp', 0)),
                    'pending_signal_id': f"FLIP:LONG:{curr.get('timestamp', 0)}",
                    'entry_phase': 'KC_REVERSAL_FLIP',
                    'reason': 'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
                }

        return None

    @staticmethod
    def detect_top_reversal_short(frame: pd.DataFrame, quote: float,
                                  side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """情境 3：頂部破位開空放行（Top Reversal Short - 順勢捕捉主跌浪）。
        
        當價格處於高位，若連續 2 根收陰摜破 MA5，且 MA5 順利由平轉為向下拐頭 (ma5_slope < 0)：
        - 豁免大級別多頭限制，授權開空 (AUTHORIZED_TOP_REVERSAL_SHORT)。
        """
        if frame is None or len(frame) < 3 or (side is not None and side != 'SHORT'):
            return None
        try:
            curr = frame.iloc[-1]
            prev = frame.iloc[-2]
            prev2 = frame.iloc[-3]
            quote = float(quote)
            
            c_open = float(curr['open'])
            c_close = quote
            p_open = float(prev['open'])
            p_close = float(prev['close'])
            p2_open = float(prev2['open'])
            p2_close = float(prev2['close'])
            
            # 連續 2 根收陰 (當前與上一根，或過去兩根)
            is_two_bearish = (c_close < c_open and p_close < p_open) or (p_close < p_open and p2_close < p2_open)
            if not is_two_bearish:
                return None
                
            raw_close = float(curr['close'])
            live_ma5 = float(curr['ma5']) + (quote - raw_close) / 5.0
            prev_ma5 = float(prev['ma5'])
            ma5_slope = live_ma5 - prev_ma5
            
            if ma5_slope >= 0:
                return None
                
            # 摜破 MA5
            if quote >= live_ma5 and p_close >= prev_ma5:
                return None
                
            return {
                'type': 'AUTHORIZED_TOP_REVERSAL_SHORT',
                'side': 'SHORT',
                'price': quote,
                'is_reversal_flip': True,
                'confirmation_bar_id': float(curr.get('timestamp', 0)),
                'breakout_bar_id': float(curr.get('timestamp', 0)),
                'pending_signal_id': f"TOP_REVERSAL:SHORT:{curr.get('timestamp', 0)}",
                'entry_phase': 'KC_TOP_REVERSAL_SHORT',
                'reason': 'AUTHORIZED_TOP_REVERSAL_SHORT',
            }
        except Exception:
            return None

    @staticmethod
    def _detect_midline_entry(frame: pd.DataFrame, quote: float,
                              side: str) -> Optional[Dict[str, Any]]:
        """Detect a live midline launch or its immediate next-bar retest."""
        if (frame is None or len(frame) < 2 or side not in ('LONG', 'SHORT')
                or 'is_closed' not in frame.columns):
            return None
        try:
            closed = closed_entry_candles(frame)
            if (len(closed) < 10 or bool(frame.iloc[-1]['is_closed'])
                    or ma5_ma15_cross_chop_problem(
                        frame, side=side,
                    ) is not None):
                return None

            live = frame.iloc[-1]
            previous = closed.iloc[-1]
            live_bar_id = float(live['timestamp'])
            quote = float(quote)
            opening = float(live['open'])
            raw_close = float(live['close'])
            high = max(float(live['high']), quote)
            low = min(float(live['low']), quote)
            middle = float(live.get('kc_middle', live.get('kc_basis', 0.0)))
            upper, lower = float(live['kc_upper']), float(live['kc_lower'])
            atr = float(previous['atr'])
            previous_ma5 = float(previous['ma5'])
            previous_ma15 = float(previous['ma15'])
            live_ma5 = float(live['ma5']) + (quote - raw_close) / 5.0
            live_ma15 = float(live['ma15']) + (quote - raw_close) / 15.0
            values = (
                live_bar_id, quote, opening, raw_close, high, low, middle,
                upper, lower, atr, previous_ma5, previous_ma15,
                live_ma5, live_ma15,
            )
            if (not all(math.isfinite(value) and value > 0 for value in values)
                    or not lower < middle < upper
                    or low > min(opening, quote) or high < max(opening, quote)
                    or low > min(opening, raw_close)
                    or high < max(opening, raw_close)
                    or atr <= 0):
                return None

            # MA5 must already turn with the entry; MA15 may be flat but cannot
            # still be moving against the proposed direction.
            if side == 'LONG':
                if live_ma5 <= previous_ma5 or live_ma15 < previous_ma15:
                    return None
                remaining_space = upper - quote
                crossed_now = opening <= middle and quote > middle
            else:
                if live_ma5 >= previous_ma5 or live_ma15 > previous_ma15:
                    return None
                remaining_space = quote - lower
                crossed_now = opening >= middle and quote < middle

            channel_width = upper - lower
            body = abs(quote - opening)
            min_midline_space = (
                MIN_DIRECTIONAL_KC_SPACE_HIGH_MOMENTUM
                if (atr > 0 and (body > HIGH_MOMENTUM_BODY_ATR * atr or math.isclose(body, HIGH_MOMENTUM_BODY_ATR * atr, rel_tol=1e-12, abs_tol=0.0)))
                else MIN_DIRECTIONAL_KC_SPACE
            )
            if (channel_width <= 0 or quote <= lower or quote >= upper
                    or (remaining_space < min_midline_space * channel_width
                        and not math.isclose(remaining_space, min_midline_space * channel_width, rel_tol=1e-12, abs_tol=0.0))):
                return None

            def volume_confirmed(candle, history):
                if 'volume' not in candle.index:
                    return False
                try:
                    volume = float(candle['volume'])
                    average = float(candle.get('volume_ma10', float('nan')))
                    if not math.isfinite(average) or average <= 0:
                        if 'volume' not in history.columns or len(history) < 10:
                            return False
                        samples = [float(value) for value in history.tail(10)['volume']]
                        if not all(math.isfinite(value) and value > 0 for value in samples):
                            return False
                        average = sum(samples) / len(samples)
                    return math.isfinite(volume) and volume >= 1.5 * average
                except (TypeError, ValueError, OverflowError):
                    return False

            direction = 1.0 if side == 'LONG' else -1.0
            candle_range = high - low
            if candle_range <= 0:
                return None

            phase = None
            breakout_bar_id = live_bar_id
            if crossed_now:
                if not volume_confirmed(live, closed):
                    return None
                phase = 'KC_MIDLINE_LAUNCH'
            elif len(closed) >= 11:
                before_breakout, breakout = closed.iloc[-2], closed.iloc[-1]
                before_middle = float(before_breakout.get(
                    'kc_middle', before_breakout.get('kc_basis', float('nan')),
                ))
                breakout_middle = float(breakout.get(
                    'kc_middle', breakout.get('kc_basis', float('nan')),
                ))
                breakout_open = float(breakout['open'])
                breakout_close = float(breakout['close'])
                breakout_body = direction * (breakout_close - breakout_open)
                before_close = float(before_breakout['close'])
                breakout_time = float(breakout['timestamp'])
                follows_break = (
                    math.isfinite(before_middle)
                    and math.isfinite(breakout_middle)
                    and (before_close <= before_middle and breakout_close > breakout_middle
                         if side == 'LONG'
                         else before_close >= before_middle and breakout_close < breakout_middle)
                    and breakout_body > 0
                    and live_bar_id == breakout_time + 60_000
                    and volume_confirmed(breakout, closed.iloc[:-1])
                )
                tests_midline = (
                    low <= middle and quote > middle if side == 'LONG'
                    else high >= middle and quote < middle
                )
                small_retest = (
                    abs(quote - opening) < breakout_body and tests_midline
                )
                if follows_break and small_retest:
                    phase = 'KC_MIDLINE_RETEST'
                    breakout_bar_id = breakout_time

            if phase is None:
                return None

            route = f'AUTHORIZED_BY_MIDLINE_BREAK_{side}'
            return {
                'type': route,
                'side': side,
                'price': quote,
                'is_midline_break': True,
                'confirmation_bar_id': live_bar_id,
                'breakout_bar_id': breakout_bar_id,
                'pending_signal_id': (
                    f'MIDLINE:{side}:{phase}:{int(breakout_bar_id)}:{int(live_bar_id)}'
                ),
                'entry_phase': phase,
                'intrabar': True,
                'midline': middle,
                'upper_rail_space_ratio': (upper - quote) / channel_width,
                'lower_rail_space_ratio': (quote - lower) / channel_width,
                'ma5_slope': live_ma5 - previous_ma5,
                'ma15_slope': live_ma15 - previous_ma15,
                'volume_confirmed': True,
                'reason': route,
            }
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            return None

    @staticmethod
    def detect_momentum_burst(
        frame: pd.DataFrame, quote: float, side: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Detect a mirrored live body crossing MA5/MA15 near KC middle."""
        if (frame is None or len(frame) < 2
                or side not in ('LONG', 'SHORT')
                or 'is_closed' not in frame.columns
                or bool(frame.iloc[-1]['is_closed'])):
            return None
        try:
            closed = closed_entry_candles(frame)
            if len(closed) < 10:
                return None
            live = frame.iloc[-1]
            previous = closed.iloc[-1]
            quote = float(quote)
            opening = float(live['open'])
            raw_close = float(live['close'])
            high = max(float(live['high']), opening, quote)
            low = min(float(live['low']), opening, quote)
            middle = float(live.get(
                'kc_middle', live.get('kc_basis', float('nan')),
            ))
            upper = float(live['kc_upper'])
            lower = float(live['kc_lower'])
            atr = float(previous['atr'])
            live_ma5 = float(live['ma5']) + (quote - raw_close) / 5.0
            live_ma15 = float(live['ma15']) + (quote - raw_close) / 15.0
            previous_ma5 = float(previous['ma5'])
            live_bar_id = float(live['timestamp'])
            previous_bar_id = float(previous['timestamp'])
            values = (
                quote, opening, raw_close, high, low, middle, upper, lower,
                atr, live_ma5, live_ma15, previous_ma5,
                live_bar_id, previous_bar_id,
            )
            body = quote - opening if side == 'LONG' else opening - quote
            candle_range = high - low
            directional_wick = (
                high - max(opening, quote)
                if side == 'LONG'
                else min(opening, quote) - low
            )
            is_v_reversal_long = (
                side == 'LONG'
                and body >= 0.7 * atr
                and quote > middle
                and live_ma5 > previous_ma5
                and directional_wick <= 0.8 * body
            )

            if (not all(math.isfinite(value) and value > 0 for value in values)
                    or live_bar_id != previous_bar_id + 60_000
                    or low > min(opening, quote)
                    or high < max(opening, quote)
                    or not lower < middle < upper):
                return None

            if not is_v_reversal_long:
                if (ma5_ma15_cross_chop_problem(frame, quote, side=side) is not None
                        or strict_kc_entry_problem(frame, quote, side) is not None):
                    return None

            midpoint_distance = (
                middle - quote if side == 'LONG' else quote - middle
            )
            crossed_both_mas = (
                opening <= min(live_ma5, live_ma15)
                and quote > max(live_ma5, live_ma15)
                if side == 'LONG'
                else opening >= max(live_ma5, live_ma15)
                and quote < min(live_ma5, live_ma15)
            )
            ma5_turned = (
                live_ma5 > previous_ma5
                if side == 'LONG'
                else live_ma5 < previous_ma5
            )
            # ── 針對逆勢快車道進場的嚴格過濾 ──
            previous_ma15 = float(previous.get('ma15', live_ma15))
            channel_state = str(live.get('channel_state', '')).strip()
            kc_middle_prev = float(previous.get('kc_middle', middle))
            if side == 'SHORT':
                # 1. 當 KC 方向為「向上（KC_TREND_UP）」時，嚴格禁止快車道逆向開空，除非價格已實體跌破 KC 中軌
                is_kc_up = (
                    channel_state in ('KC向上', 'UP', 'UPTREND', 'BULLISH', 'LONG')
                    or middle > kc_middle_prev
                )
                closed_below_middle = quote < middle and opening <= middle
                if is_kc_up and not closed_below_middle:
                    return None

                # 2. 強化 MA15 斜率與走平過濾：當 MA15 仍維持向上斜率時，禁止執行空單快車道進場
                if live_ma15 >= previous_ma15:
                    return None
            elif side == 'LONG':
                is_v_reversal_long = (
                    body >= 0.7 * atr
                    and quote > middle
                    and live_ma5 > previous_ma5
                    and directional_wick <= 0.8 * body
                )
                if not is_v_reversal_long:
                    is_kc_down = (
                        channel_state in ('KC向下', 'DOWN', 'DOWNTREND', 'BEARISH', 'SHORT')
                        or middle < kc_middle_prev
                    )
                    closed_above_middle = quote > middle and opening >= middle
                    if is_kc_down and not closed_above_middle:
                        return None

                    if live_ma15 <= previous_ma15:
                        return None

            if not (side == 'LONG' and is_v_reversal_long):
                if (body < MOMENTUM_BURST_BODY_ATR * atr
                        or candle_range <= 0
                        or body / candle_range < MOMENTUM_BURST_BODY_RATIO
                        or directional_wick > MOMENTUM_BURST_MAX_DIRECTIONAL_WICK_RATIO * body
                        or not crossed_both_mas
                        or not ma5_turned
                        or midpoint_distance
                            > MOMENTUM_BURST_MAX_MIDLINE_DISTANCE_ATR * atr):
                    return None

            code = (
                MOMENTUM_BURST_LONG_CODE
                if side == 'LONG'
                else MOMENTUM_BURST_SHORT_CODE
            )
            phase = f'KC_MOMENTUM_BURST_{side}'
            return {
                'type': code,
                'side': side,
                'price': quote,
                'is_midline_break': True,
                'is_momentum_burst': True,
                'confirmation_bar_id': live_bar_id,
                'breakout_bar_id': live_bar_id,
                'pair_confirmation_bar_id': previous_bar_id,
                'pending_signal_id': f'{code}:{int(live_bar_id)}',
                'entry_phase': phase,
                'intrabar': True,
                'midline': middle,
                'midline_distance_atr': midpoint_distance / atr,
                'live_ma5': live_ma5,
                'live_ma15': live_ma15,
                'body_atr': body / atr,
                'body_ratio': body / candle_range,
                'upper_rail_space_ratio': (upper - quote) / (upper - lower),
                'lower_rail_space_ratio': (quote - lower) / (upper - lower),
                'volume_confirmed': False,
                'reason': code,
            }
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            return None

    @staticmethod
    def detect_midline_break_short(frame: pd.DataFrame, quote: float,
                                   side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if side not in (None, 'SHORT'):
            return None
        return EntryGatePipeline._detect_midline_entry(frame, quote, 'SHORT')

    @staticmethod
    def detect_midline_break_long(frame: pd.DataFrame, quote: float,
                                  side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if side not in (None, 'LONG'):
            return None
        return EntryGatePipeline._detect_midline_entry(frame, quote, 'LONG')

    @staticmethod
    def detect_top_waterfall_flip_short(frame: pd.DataFrame, quote: float,
                                        side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Authorize a long-to-short flip only on a bearish KC-middle cross."""
        if frame is None or len(frame) < 2 or (side is not None and side != 'SHORT'):
            return None
        try:
            curr = frame.iloc[-1]
            prev = frame.iloc[-2]
            quote = float(quote)
            
            c_open = float(curr.get('open', quote))
            raw_close = float(curr.get('close', quote))
            is_closed = bool(curr.get('is_closed', False))
            if not is_closed:
                return None
            c_close = raw_close
            
            atr = float(prev.get('atr', curr.get('atr', 0.0)))
            if atr <= 0:
                return None
                
            p_open = float(prev.get('open', c_close))
            p_close = float(prev.get('close', c_close))
            curr_ma5 = float(curr.get('ma5', raw_close))
            curr_ma15 = float(curr.get('ma15', raw_close))
            live_ma5 = curr_ma5
            
            # 條件 1: 當根為強勢實體陰線 (Open - Close >= 0.45 * ATR)
            is_strong_bearish = (c_open - c_close) >= 0.4 * atr
            
            # 條件 2: 收盤價強勢貫穿跌破 MA5，並吞噬前棒低點
            pierces_ma5_and_engulfs_low = (
                p_close > p_open and c_close - curr_ma15 > 1.2 * atr
                and c_close < live_ma5 and c_open >= p_close and c_close < p_open
            )
            
            if (not (is_strong_bearish and pierces_ma5_and_engulfs_low)
                    or not kc_bearish_midline_turn(frame, c_close)):
                return None
                
            return {
                'type': 'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
                'side': 'SHORT',
                'price': quote,
                'is_reversal_flip': True,
                'override_cooldown': True,
                'confirmation_bar_id': float(curr.get('timestamp', 0)),
                'breakout_bar_id': float(curr.get('timestamp', 0)),
                'pending_signal_id': f"TOP_WATERFALL_FLIP:SHORT:{curr.get('timestamp', 0)}",
                'entry_phase': 'KC_TOP_WATERFALL_FLIP',
                'reason': 'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
            }
        except Exception:
            return None

    @staticmethod
    def detect_peak_flip_short(frame: pd.DataFrame, quote: float, account,
                               symbol: Optional[str]) -> Optional[Dict[str, Any]]:
        """Authorize a fresh bearish body only after a confirmed long close."""
        if (frame is None or frame.empty or account is None or not symbol
                or symbol in getattr(account, 'positions', {})
                or bool(frame.iloc[-1].get('is_closed', True))
                or frame.attrs.get('timeframe_ms', 60000) != 60000):
            return None
        try:
            from core.services.candle_data import closed_entry_candles

            closed = closed_entry_candles(frame)
            if closed.empty:
                return None
            trades = [
                trade for trade in getattr(account, 'trades', [])
                if trade.get('symbol') == symbol
                and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT', 'CLOSE_LONG', 'CLOSE_SHORT')
            ]
            if not trades:
                return None
            close_trade = max(trades, key=lambda trade: float(trade.get('id') or 0.0))
            reason = str(close_trade.get('reason') or '')
            if (close_trade.get('action') != 'CLOSE_LONG'
                    or close_trade.get('status') != 'CLOSED'
                    or not reason
                    or any(token in reason.lower() for token in (
                        'manual', '手動', 'hard_stop', 'stop_loss',
                        'stop-loss', 'stop loss', '觸發止損',
                    ))):
                return None

            close_id = float(close_trade.get('id') or 0.0)
            live = frame.iloc[-1]
            previous = closed.iloc[-1]
            stamp = float(live['timestamp'])
            quote = float(quote)
            opening = float(live['open'])
            raw_close = float(live['close'])
            high = max(float(live['high']), quote)
            low = min(float(live['low']), quote)
            atr = float(previous['atr'])
            ma5 = float(live['ma5']) + (quote - raw_close) / 5.0
            previous_mid = (float(previous['open']) + float(previous['close'])) / 2.0
            values = (close_id, stamp, quote, opening, raw_close, high, low,
                      atr, ma5, previous_mid)
            if (not all(math.isfinite(value) and value > 0 for value in values)
                    or atr <= 0 or high < max(opening, quote)
                    or low > min(opening, quote)
                    or not low <= raw_close <= high):
                return None
            close_bar = math.floor(close_id / 60000.0) * 60000.0
            live_bar = math.floor(stamp / 60000.0) * 60000.0
            if live_bar not in (close_bar, close_bar + 60000.0):
                return None
            candle_range = high - low
            body = opening - quote
            if (candle_range <= 0 or body < 0.35 * atr
                    or body / candle_range < 0.60
                    or not (quote < ma5 or quote < previous_mid)):
                return None

            close_id = int(close_id)
            return {
                'type': 'AUTHORIZED_BY_PEAK_FLIP_SHORT',
                'side': 'SHORT',
                'price': quote,
                'reason': 'CONFIRMED_LONG_CLOSE_THEN_BEARISH_REVERSAL_BODY',
                'is_reversal_flip': True,
                'override_cooldown': True,
                'confirmation_bar_id': stamp,
                'breakout_bar_id': stamp,
                'pending_signal_id': (
                    f'{symbol}:AUTHORIZED_BY_PEAK_FLIP_SHORT:{close_id}:{int(stamp)}'
                ),
                'entry_phase': 'POST_CLOSE_PEAK_FLIP',
                'reverse_close_id': close_id,
                'reverse_close_reason': reason,
                'reverse_body_atr': body / atr,
                'reverse_body_ratio': body / candle_range,
                'entry_atr': atr,
            }
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            return None

    @classmethod
    def detect_fast_cross(
        cls, frame: pd.DataFrame, quote: float, side: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Authorize a fresh live MA5/MA15 cross at the wave beginning."""
        if side in (None, 'LONG'):
            evidence = fast_cross_evidence(frame, quote, 'LONG', symbol=symbol)
            if evidence is not None:
                stamp = evidence['stamp']
                return {
                    'action': 'ENTER',
                    'side': 'LONG',
                    'type': GOLDEN_CROSS_FAST_LONG_CODE,
                    'reason': 'LIVE_MA5_MA15_GOLDEN_CROSS',
                    'price': quote,
                    'entry_atr': evidence['atr'],
                    'confirmation_bar_id': stamp,
                    'breakout_bar_id': stamp,
                    'exit_bar_id': stamp,
                    'close_price': quote,
                    'intrabar': True,
                    'entry_phase': 'MA_GOLDEN_CROSS_FAST_LANE',
                    'body_atr': evidence.get('body_atr', 0.5),
                    'directional_space_ratio': evidence.get('directional_space_ratio', 0.5),
                    'pending_signal_id': f"{symbol or 'SYM'}:{GOLDEN_CROSS_FAST_LONG_CODE}:{int(stamp)}",
                }
        if side in (None, 'SHORT'):
            evidence = fast_cross_evidence(frame, quote, 'SHORT', symbol=symbol)
            if evidence is not None:
                stamp = evidence['stamp']
                return {
                    'action': 'ENTER',
                    'side': 'SHORT',
                    'type': DEATH_CROSS_FAST_SHORT_CODE,
                    'reason': 'LIVE_MA5_MA15_DEATH_CROSS',
                    'price': quote,
                    'entry_atr': evidence['atr'],
                    'confirmation_bar_id': stamp,
                    'breakout_bar_id': stamp,
                    'exit_bar_id': stamp,
                    'close_price': quote,
                    'intrabar': True,
                    'entry_phase': 'MA_DEATH_CROSS_FAST_LANE',
                    'body_atr': evidence.get('body_atr', 0.5),
                    'directional_space_ratio': evidence.get('directional_space_ratio', 0.5),
                    'pending_signal_id': f"{symbol or 'SYM'}:{DEATH_CROSS_FAST_SHORT_CODE}:{int(stamp)}",
                }
        return None

    def authorize(self, decision: Optional[Dict[str, Any]], frame: pd.DataFrame,
                  quote: float, symbol: Optional[str] = None,
                  requested_side: Optional[str] = None,
                  account=None,
                  diagnostics: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        candidate_type = (
            decision.get('type') if isinstance(decision, dict)
            else None
        )
        is_fast_cross = candidate_type in (
            GOLDEN_CROSS_FAST_LONG_CODE,
            DEATH_CROSS_FAST_SHORT_CODE,
        )
        is_meme_entry = bool(
            candidate_type in MEME_ENTRY_CODES
            or (symbol and is_meme_breakout_symbol(symbol))
        )
        cross_chop_problem = (
            None if (is_meme_entry and not is_fast_cross)
            else ma5_ma15_cross_chop_problem(
                frame, quote, symbol=symbol or '', log_result=True,
                side=(
                    decision.get('side') if isinstance(decision, dict)
                    else requested_side
                ),
            )
        )
        if cross_chop_problem:
            if diagnostics is not None:
                diagnostics['reason'] = cross_chop_problem
            return None
        if is_fast_cross:
            slope_problem = fast_cross_ma15_slope_problem(
                frame, quote, decision.get('side'), symbol=symbol,
            )
            if slope_problem:
                if diagnostics is not None:
                    diagnostics['reason'] = slope_problem
                return None
        strategy_type = (
            entry_strategy_type(decision.get('entry_phase'))
            if isinstance(decision, dict) else None
        )
        kc_problem = strict_kc_entry_problem(
            frame, quote,
            side=(decision.get('side') if isinstance(decision, dict) else requested_side),
            strategy_type=strategy_type,
        )
        if kc_problem:
            if diagnostics is not None:
                diagnostics['reason'] = kc_problem
            return None
        if account is not None and symbol:
            from core.services.entry_contract import (
                same_side_anti_churn_problem,
                opposite_side_close_cooldown_problem,
                same_bar_close_entry_problem,
            )
            requested_close_side = (
                decision.get('side') if isinstance(decision, dict) else requested_side
            )
            if requested_close_side in ('LONG', 'SHORT'):
                churn_problem = same_side_anti_churn_problem(
                    account, symbol, frame, requested_close_side, quote,
                )
                if churn_problem:
                    if diagnostics is not None:
                        diagnostics['reason'] = churn_problem
                    return None
                close_problem = same_bar_close_entry_problem(
                    account, symbol, frame, requested_close_side,
                )
                if close_problem:
                    if diagnostics is not None:
                        diagnostics['reason'] = close_problem
                    return None
                cooldown_problem = opposite_side_close_cooldown_problem(
                    account, symbol, frame, requested_close_side,
                )
                if cooldown_problem:
                    if diagnostics is not None:
                        diagnostics['reason'] = cooldown_problem
                    return None
        # Detect a fully qualified live breakout first. Its direction is
        # established by the real-time MA5 slope and rail/body conditions;
        # an aggregate UI/AI "unknown direction" label must not veto it.
        candidate = decision
        if candidate is None:
            momentum_long = (
                self.detect_momentum_burst(frame, quote, side='LONG')
                if requested_side in (None, 'LONG') else None
            )
            momentum_short = (
                self.detect_momentum_burst(frame, quote, side='SHORT')
                if requested_side in (None, 'SHORT') else None
            )
            candidate = momentum_long or momentum_short
        if candidate is None:
            mid_short = (
                self.detect_midline_break_short(frame, quote, side=requested_side)
                if requested_side in (None, 'SHORT') else None
            )
            mid_long = (
                self.detect_midline_break_long(frame, quote, side=requested_side)
                if requested_side in (None, 'LONG') else None
            )
            candidate = mid_short or mid_long
        if candidate is None:
            candidate = self.detect_fast_cross(frame, quote, side=requested_side, symbol=symbol)
        if candidate is None:
            candidate = self.detect_realtime_breakout(frame, quote, side=requested_side)
        if candidate is None and requested_side in (None, 'SHORT'):
            candidate = self.detect_downtrend_pullback_resume_short(frame, quote)
        if decision is None and candidate is not None:
            decision = candidate

        candidate_type = (
            (decision or candidate).get('type')
            if (decision or candidate) is not None
            else None
        )
        is_fast_cross = candidate_type in (
            GOLDEN_CROSS_FAST_LONG_CODE,
            DEATH_CROSS_FAST_SHORT_CODE,
        )
        if is_fast_cross:
            slope_problem = fast_cross_ma15_slope_problem(
                frame, quote,
                decision.get('side') if decision else requested_side,
                symbol=symbol,
            )
            if slope_problem:
                if diagnostics is not None:
                    diagnostics['reason'] = slope_problem
                return None

        is_verified_live_breakout = bool(
            candidate and (
                (candidate.get('type') == 'AUTHORIZED_REALTIME_BREAKOUT'
                 and candidate.get('ma5_direction_aligned') is True)
                or candidate.get('type') in (
                    'AUTHORIZED_SHADOW_RETEST_ENTRY',
                    'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
                    'TOP_WATERFALL_FLIP',
                    'CLIMAX_REVERSAL_FLIP',
                    # Midline entries reach this allowlist only after their
                    # live launch/retest, volume, direction and space gates pass.
                    'AUTHORIZED_BY_MIDLINE_BREAK_SHORT',
                    'AUTHORIZED_BY_MIDLINE_BREAK_LONG',
                    MOMENTUM_BURST_LONG_CODE,
                    MOMENTUM_BURST_SHORT_CODE,
                    GOLDEN_CROSS_FAST_LONG_CODE,
                    DEATH_CROSS_FAST_SHORT_CODE,
                )
            )
        )
        regime_problem = self.market_regime_problem(symbol, frame, quote)
        if regime_problem:
            if not is_verified_live_breakout and diagnostics is not None:
                diagnostics['reason'] = regime_problem
            if not is_verified_live_breakout:
                return None
        if decision is None:
            # Determine the live outer-rail breakout before the common chop
            # check so that only this verified entry family gets its explicit
            # flat-line exemption.
            decision = self.detect_realtime_breakout(frame, quote, side=requested_side)
        breakout_types = FLAT_LINE_BREAKOUT_TYPES
        is_outer_rail_breakout = bool(
            (decision or candidate)
            and (decision or candidate).get('type') in breakout_types
        )
        is_shadow_entry = bool(
            (decision or candidate) and (
                (decision or candidate).get('type') == 'AUTHORIZED_SHADOW_RETEST_ENTRY'
                or (decision or candidate).get('is_shadow_retest') is True
            )
        )
        is_midline_candidate = bool(
            (decision or candidate)
            and (decision or candidate).get('type') in (
                'AUTHORIZED_BY_MIDLINE_BREAK_SHORT',
                'AUTHORIZED_BY_MIDLINE_BREAK_LONG',
                MOMENTUM_BURST_LONG_CODE,
                MOMENTUM_BURST_SHORT_CODE,
                GOLDEN_CROSS_FAST_LONG_CODE,
                DEATH_CROSS_FAST_SHORT_CODE,
            )
        )
        meme_breakout_override = (
            is_verified_live_breakout
            and is_meme_breakout_symbol(symbol)
            and self.has_meme_impulse_breakout(symbol, frame, quote)
        )
        if not is_shadow_entry and not is_midline_candidate and not meme_breakout_override:
            try:
                chop_problem = self.chop_lockout_problem(
                    frame, quote, allow_flat_lines=is_outer_rail_breakout,
                    allow_unknown_direction=is_verified_live_breakout,
                    allow_ma_tangle=is_fast_cross,
                    signal_code=candidate_type,
                )
            except TypeError:
                chop_problem = self.chop_lockout_problem(frame, quote)
            if chop_problem:
                if diagnostics is not None:
                    diagnostics['reason'] = chop_problem
                return None
        if decision is None:
            # A qualifying first-bar outer-rail break is the fastest route and
            # takes precedence over slower continuation/reversal candidates.
            rt_decision = self.detect_realtime_breakout(frame, quote, side=requested_side)
            if rt_decision is not None:
                decision = rt_decision
            else:
                pullback_resume = (
                    self.detect_downtrend_pullback_resume_short(frame, quote)
                    if requested_side in (None, 'SHORT') else None
                )
                if pullback_resume is not None:
                    decision = pullback_resume
                waterfall_flip = (
                    self.detect_top_waterfall_flip_short(frame, quote, side=requested_side)
                    if decision is None and requested_side in (None, 'SHORT') else None
                )
                if waterfall_flip is not None:
                    decision = waterfall_flip
                elif decision is None:
                    peak_flip = (
                        self.detect_peak_flip_short(frame, quote, account, symbol)
                        if requested_side in (None, 'SHORT') else None
                    )
                    if peak_flip is not None:
                        decision = peak_flip
                    else:
                        cont_decision = self.detect_trend_continuation(
                            frame, quote, side=requested_side, symbol=symbol,
                        )
                        if cont_decision is not None:
                            decision = cont_decision
                        else:
                            shadow_retest = (
                                self.detect_shadow_retest_long(frame, quote, side=requested_side)
                                if requested_side in (None, 'LONG') else None
                            )
                            if shadow_retest is not None:
                                decision = shadow_retest
                            else:
                                flip_decision = self.detect_reversal_flip(frame, quote, side=requested_side)
                                if flip_decision is not None:
                                    decision = flip_decision
                                else:
                                    top_reversal = (
                                        self.detect_top_reversal_short(frame, quote, side=requested_side)
                                        if requested_side in (None, 'SHORT') else None
                                    )
                                    if top_reversal is not None:
                                        decision = top_reversal
                                    else:
                                        mid_short = (
                                            self.detect_midline_break_short(frame, quote, side=requested_side)
                                            if requested_side in (None, 'SHORT') else None
                                        )
                                        mid_long = (
                                            self.detect_midline_break_long(frame, quote, side=requested_side)
                                            if requested_side in (None, 'LONG') else None
                                        )
                                        midline_break = mid_short or mid_long
                                        if midline_break is not None:
                                            decision = midline_break
                                        else:
                                            momentum_long = (
                                                self.detect_momentum_burst(
                                                    frame, quote, side='LONG',
                                                )
                                                if requested_side in (None, 'LONG')
                                                else None
                                            )
                                            momentum_short = (
                                                self.detect_momentum_burst(
                                                    frame, quote, side='SHORT',
                                                )
                                                if requested_side in (None, 'SHORT')
                                                else None
                                            )
                                            decision = momentum_long or momentum_short
                                            if decision is None:
                                                if diagnostics is not None:
                                                    diagnostics['reason'] = 'WAIT_PIPELINE_TRIGGER'
                                                return None
        else:
            supplied_type = decision.get('type')
            side = decision.get('side')
            if side not in ('LONG', 'SHORT') or supplied_type not in PIPELINE_ENTRY_TYPES:
                if diagnostics is not None:
                    diagnostics['reason'] = 'BLOCKED_UNRECOGNIZED_PIPELINE_AUTHORITY'
                return None
            if supplied_type == 'KC_DOWNTREND_PULLBACK_RESUME_SHORT':
                refreshed = self.detect_downtrend_pullback_resume_short(frame, quote)
            elif supplied_type in (GOLDEN_CROSS_FAST_LONG_CODE, DEATH_CROSS_FAST_SHORT_CODE, 'TRIGGER_B_MA_CROSS'):
                refreshed = self.detect_fast_cross(frame, quote, side=side, symbol=symbol) or decision
            elif supplied_type in ('KC_LIVE_BODY_BREAKOUT_LONG', 'KC_LIVE_BODY_BREAKOUT_SHORT'):
                from core.services.entry_contract import evaluate_live_body_breakout
                refreshed = evaluate_live_body_breakout(frame, quote, symbol=symbol, requested_side=side) or decision
            elif supplied_type in (
                'TRIGGER_A_KC_BREAKOUT', 'TRIGGER_C_CONTINUATION',
                'CLOSED_BODY_BREAKOUT_LONG', 'CLOSED_BODY_BREAKOUT_SHORT',
                'RE_ENTRY_LONG', 'RE_ENTRY_SHORT',
                'TRIGGER_B_REENTRY_SUPPORT', 'TRIGGER_B_REENTRY_BREAKOUT',
                *MEME_ENTRY_CODES,
            ):
                refreshed = decision
            elif supplied_type == 'AUTHORIZED_REALTIME_BREAKOUT':
                refreshed = self.detect_realtime_breakout(frame, quote, side=side)
            elif supplied_type == 'AUTHORIZED_BY_MIDLINE_BREAK_SHORT':
                refreshed = self.detect_midline_break_short(frame, quote, side=side)
            elif supplied_type == 'AUTHORIZED_BY_MIDLINE_BREAK_LONG':
                refreshed = self.detect_midline_break_long(frame, quote, side=side)
            elif supplied_type in MOMENTUM_BURST_CODES:
                refreshed = self.detect_momentum_burst(frame, quote, side=side)
            elif supplied_type == 'AUTHORIZED_SHADOW_RETEST_ENTRY':
                refreshed = self.detect_shadow_retest_long(frame, quote, side=side)
            elif supplied_type in ('AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP'):
                refreshed = self.detect_top_waterfall_flip_short(frame, quote, side=side)
            elif supplied_type == 'AUTHORIZED_BY_PEAK_FLIP_SHORT':
                refreshed = self.detect_peak_flip_short(frame, quote, account, symbol)
            elif supplied_type == 'AUTHORIZED_TOP_REVERSAL_SHORT':
                refreshed = self.detect_top_reversal_short(frame, quote, side=side)
            elif supplied_type in (
                'AUTHORIZED_BY_TREND_CONTINUATION_LONG',
                'AUTHORIZED_BY_TREND_CONTINUATION_SHORT',
            ):
                refreshed = self.detect_trend_continuation(
                    frame, quote, side=side, symbol=symbol,
                )
            else:
                refreshed = self.detect_reversal_flip(frame, quote, side=side)
            if (refreshed is None or refreshed.get('type') != supplied_type
                    or refreshed.get('pending_signal_id') != decision.get('pending_signal_id')):
                if diagnostics is not None:
                    diagnostics['reason'] = 'BLOCKED_PIPELINE_SIGNAL_CHANGED'
                return None
            decision = refreshed

        cross_chop_problem = (
            None if (
                decision.get('type') in MEME_ENTRY_CODES
                or (symbol and is_meme_breakout_symbol(symbol))
            )
            else ma5_ma15_cross_chop_problem(
                frame, quote, symbol=symbol or '', log_result=True,
                side=decision.get('side'),
            )
        )
        if cross_chop_problem:
            if diagnostics is not None:
                diagnostics['reason'] = cross_chop_problem
            return None

        if decision.get('type') not in FLAT_LINE_BREAKOUT_TYPES:
            if diagnostics is not None:
                diagnostics['reason'] = 'BLOCKED_ENTRY_NOT_OUTER_RAIL_BREAKOUT'
            return None

        strategy_type = entry_strategy_type(decision.get('entry_phase'))
        if strategy_type is not None:
            decision['strategy_type'] = strategy_type

        side = decision.get('side')
        if side not in ('LONG', 'SHORT'):
            if diagnostics is not None:
                diagnostics['reason'] = 'INVALID_SIDE'
            return None
        from core.services.ma5_entry_slope import (
            live_ma5_entry_direction_problem,
        )
        direction_problem = live_ma5_entry_direction_problem(frame, quote, side)
        if direction_problem:
            if diagnostics is not None:
                diagnostics['reason'] = direction_problem
            return None
        market_structure_problem = entry_market_structure_problem(
            frame, quote, side, entry_phase=decision.get('entry_phase'),
        )
        if market_structure_problem:
            if diagnostics is not None:
                diagnostics['reason'] = market_structure_problem
            return None
        if account is not None and symbol:
            from core.services.entry_contract import (
                same_side_anti_churn_problem,
                opposite_side_close_cooldown_problem,
                same_bar_close_entry_problem,
            )
            churn_problem = same_side_anti_churn_problem(
                account, symbol, frame, side, quote,
            )
            if churn_problem:
                if diagnostics is not None:
                    diagnostics['reason'] = churn_problem
                return None
            close_problem = same_bar_close_entry_problem(account, symbol, frame, side)
            if close_problem:
                if diagnostics is not None:
                    diagnostics['reason'] = close_problem
                return None
            cooldown_problem = opposite_side_close_cooldown_problem(
                account, symbol, frame, side,
            )
            if cooldown_problem:
                if diagnostics is not None:
                    diagnostics['reason'] = cooldown_problem
                return None
        if side == 'LONG':
            bearish_candle_problem = long_large_bearish_candle_problem(frame, quote)
            if bearish_candle_problem:
                if diagnostics is not None:
                    diagnostics['reason'] = bearish_candle_problem
                return None

        is_midline_break = bool(
            decision.get('is_midline_break')
            or decision.get('type') in (
                'AUTHORIZED_BY_MIDLINE_BREAK_SHORT',
                'AUTHORIZED_BY_MIDLINE_BREAK_LONG',
                MOMENTUM_BURST_LONG_CODE,
                MOMENTUM_BURST_SHORT_CODE,
            )
            or decision.get('reason') in (
                'AUTHORIZED_BY_MIDLINE_BREAK_SHORT',
                'AUTHORIZED_BY_MIDLINE_BREAK_LONG',
                MOMENTUM_BURST_LONG_CODE,
                MOMENTUM_BURST_SHORT_CODE,
            )
        )
        is_flip = bool(
            decision.get('type') in (
                'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
                'TOP_WATERFALL_FLIP',
                'CLIMAX_REVERSAL_FLIP',
            ) or decision.get('is_reversal_flip') is True
        )
        is_confirmed_rail_breakout = (
            decision.get('type') in ('KC_2BAR_CONFIRM_LONG', 'KC_2BAR_CONFIRM_SHORT')
            and decision.get('entry_phase') == 'KC_2BAR_CLOSED_CONFIRM'
        )
        # ── 最頂層進場門控：MA5 幾何生死鐵律（一票否決權） ──
        if frame is not None and len(frame) >= 2:
            try:
                from core.services.ma5_entry_slope import live_ma5_slope_problem
                is_head_chop_flip = decision.get('type') in (
                    'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
                    'TOP_WATERFALL_FLIP',
                    'CLIMAX_REVERSAL_FLIP',
                )
                is_shadow_entry = (decision.get('type') == 'AUTHORIZED_SHADOW_RETEST_ENTRY')
                slope_problem = live_ma5_slope_problem(frame, quote, side)
                if slope_problem and not is_midline_break and not is_shadow_entry and not (
                    is_head_chop_flip and slope_problem == 'BLOCKED_BY_MA5_SLOPE_INSUFFICIENT'
                ):
                    if diagnostics is not None:
                        diagnostics['reason'] = slope_problem
                    return None
            except Exception:
                if diagnostics is not None:
                    diagnostics['reason'] = 'BLOCKED_BY_MA5_SLOPE_INVALID'
                return None
        else:
            if diagnostics is not None:
                diagnostics['reason'] = 'BLOCKED_BY_MA5_SLOPE_INVALID'
            return None

        # 1. Spatial Brain Perception
        context = SpatialBrain.analyze(frame, quote)
        is_explosive_breakout = (context.state == 'EXPLOSIVE_EXPANSION')

        # ── 爆發突破（EXPLOSIVE_BREAKOUT）：100% 豁免 ma15_slope 走平阻斷 ──
        # ── 頂部斷頭鍘特例 (Top Waterfall Flip)：100% 豁免大級別「KC/MA 向上禁止開空」限制 ──
        is_top_waterfall_flip = (
            decision.get('type') in ('AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP')
            or decision.get('reason') in ('AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP')
        )
        is_reversal_flip = bool(
            decision.get('is_reversal_flip')
            or is_midline_break
            or decision.get('type') in (
                'AUTHORIZED_BY_PEAK_FLIP_SHORT',
                'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
                'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
                'AUTHORIZED_TOP_REVERSAL_SHORT',
                'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
                'TOP_WATERFALL_FLIP',
                'AUTHORIZED_BY_MIDLINE_BREAK_SHORT',
                'AUTHORIZED_BY_MIDLINE_BREAK_LONG',
                MOMENTUM_BURST_LONG_CODE,
                MOMENTUM_BURST_SHORT_CODE,
            )
            or decision.get('reason') in (
                'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
                'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
                'AUTHORIZED_TOP_REVERSAL_SHORT',
                'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
                'TOP_WATERFALL_FLIP',
                'AUTHORIZED_BY_MIDLINE_BREAK_SHORT',
                'AUTHORIZED_BY_MIDLINE_BREAK_LONG',
                MOMENTUM_BURST_LONG_CODE,
                MOMENTUM_BURST_SHORT_CODE,
            )
        )
        is_meme_signal = bool(
            decision.get('type') in MEME_ENTRY_CODES
            or (symbol and is_meme_breakout_symbol(symbol))
        )
        if not is_top_waterfall_flip:
            ma_bias_problem = self._ma15_bias_problem(
                frame, quote, side,
                allow_flat_slope=(is_explosive_breakout or is_meme_signal or decision.get('type') == 'AUTHORIZED_SHADOW_RETEST_ENTRY'),
                allow_directional_breakout=(
                    is_meme_signal
                    or decision.get('type') == 'AUTHORIZED_REALTIME_BREAKOUT'
                    or is_midline_break
                    or (is_reversal_flip and not is_midline_break)
                ),
            )
            if ma_bias_problem:
                if diagnostics is not None:
                    diagnostics['reason'] = ma_bias_problem
                return None

        # 檢查是否為即時破軌、順勢延續或反手翻轉
        is_rt_breakout = bool(
            is_explosive_breakout
            or decision.get('is_breakout')
            or decision.get('type') == 'AUTHORIZED_REALTIME_BREAKOUT'
            or decision.get('reason') == 'AUTHORIZED_REALTIME_BREAKOUT'
        )
        is_trend_continuation = bool(
            decision.get('is_trend_continuation')
            or decision.get('type') in ('AUTHORIZED_BY_TREND_CONTINUATION_LONG', 'AUTHORIZED_BY_TREND_CONTINUATION_SHORT')
            or decision.get('reason') in ('AUTHORIZED_BY_TREND_CONTINUATION_LONG', 'AUTHORIZED_BY_TREND_CONTINUATION_SHORT')
        )
        is_shadow_retest = bool(
            decision.get('is_shadow_retest')
            or decision.get('type') == 'AUTHORIZED_SHADOW_RETEST_ENTRY'
            or decision.get('reason') == 'AUTHORIZED_SHADOW_RETEST_ENTRY'
        )
        is_bypass_spatial = bool(
            decision.get('bypass_spatial_check')
            or is_rt_breakout
            or decision.get('type') in (
                'TRIGGER_A_KC_BREAKOUT', 'KC_LIVE_BODY_BREAKOUT_LONG',
                'KC_LIVE_BODY_BREAKOUT_SHORT', 'KC_GOLDEN_CROSS_FAST_LONG',
                'KC_DEATH_CROSS_FAST_SHORT', 'RE_ENTRY_LONG', 'RE_ENTRY_SHORT',
            ) or decision.get('type') == 'KC_REALTIME_RAIL_BREACH_SHORT'
        )
        is_live_short_rail_breakout = (
            decision.get('type') == 'KC_REALTIME_RAIL_BREACH_SHORT'
            and self.has_bearish_chop_breakout_override(frame, quote)
        )

        # 2. Iterate through gates
        for name, gate_func in self.gates:
            if is_meme_signal:
                continue
            if ((is_trend_continuation or is_shadow_retest or is_midline_break
                 or (is_bypass_spatial and not is_live_short_rail_breakout))
                    and name == 'CANDLE_SOLIDITY'):
                continue
            # 快車道、破軌空間豁免、順勢延續、下影線回踩與反手翻轉全面豁免 MOUTH_EXPANSION（全面取消破 KC 軌道要求）
            if (is_rt_breakout or is_trend_continuation or is_reversal_flip or is_shadow_retest or is_midline_break or is_bypass_spatial) and name == 'MOUTH_EXPANSION':
                continue
            # 快車道、破軌空間豁免、順勢延續、下影線回踩與反手翻轉全面豁免 CHOP_FILTER（順勢已由均線確認，反手為單棒吞噬）
            if (is_rt_breakout or is_trend_continuation or is_reversal_flip or is_shadow_retest or is_midline_break or is_bypass_spatial) and name == 'CHOP_FILTER':
                continue
            passed, reason = gate_func(frame, quote, side, context=context)
            if not passed:
                if diagnostics is not None:
                    diagnostics['reason'] = reason
                    diagnostics['gate'] = name
                    diagnostics['spatial_state'] = context.state
                if symbol:
                    import logging
                    p_logger = logging.getLogger("SpatialBrain")
                    p_logger.info(
                        f"[SPATIAL_DIAGNOSTIC] Symbol: {symbol} | Context: {context.state} | "
                        f"Chop_Index: {context.chop_index:.2f} | Bandwidth_Ratio: {context.bandwidth_ratio:.2f} | "
                        f"Solidity: {context.solidity_ratio*100:.1f}% | MA3_Slope: {context.ma3_slope:+.4f} | "
                        f"Decision: BLOCKED ({reason})"
                    )
                return None

        # 3. Anti-trend hard structure check (反手翻轉、順勢延續、妖幣與中軌提前突破豁免舊趨勢排列)
        if not (is_reversal_flip or is_trend_continuation or is_rt_breakout or is_midline_break or is_meme_signal):
            curr = frame.iloc[-1]
            ma5 = float(curr.get('ma5', quote))
            ma15 = float(curr.get('ma15', quote))
            if side == 'SHORT':
                if quote > ma5 or ma5 > ma15:
                    if diagnostics is not None:
                        diagnostics['reason'] = 'BLOCKED_BY_BULLISH_STRUCTURE_NO_SHORT'
                    if symbol:
                        import logging
                        p_logger = logging.getLogger("SpatialBrain")
                        p_logger.info(
                            f"[SPATIAL_DIAGNOSTIC] Symbol: {symbol} | Context: {context.state} | "
                            f"Chop_Index: {context.chop_index:.2f} | Bandwidth_Ratio: {context.bandwidth_ratio:.2f} | "
                            f"Solidity: {context.solidity_ratio*100:.1f}% | MA3_Slope: {context.ma3_slope:+.4f} | "
                            f"Decision: BLOCKED (BLOCKED_BY_BULLISH_STRUCTURE_NO_SHORT)"
                        )
                    return None
            elif side == 'LONG':
                if quote < ma5 or ma5 < ma15:
                    if diagnostics is not None:
                        diagnostics['reason'] = 'BLOCKED_BY_BEARISH_STRUCTURE_NO_LONG'
                    if symbol:
                        import logging
                        p_logger = logging.getLogger("SpatialBrain")
                        p_logger.info(
                            f"[SPATIAL_DIAGNOSTIC] Symbol: {symbol} | Context: {context.state} | "
                            f"Chop_Index: {context.chop_index:.2f} | Bandwidth_Ratio: {context.bandwidth_ratio:.2f} | "
                            f"Solidity: {context.solidity_ratio*100:.1f}% | MA3_Slope: {context.ma3_slope:+.4f} | "
                            f"Decision: BLOCKED (BLOCKED_BY_BEARISH_STRUCTURE_NO_LONG)"
                        )
                    return None

        # 4. Stamp authorization
        try:
            live = frame.iloc[-1]
            atr_bar = frame.iloc[-2] if not bool(live.get('is_closed', True)) else live
            entry_atr = float(atr_bar['atr'])
            confirmation_bar_id = float(
                decision.get('confirmation_bar_id') or frame.iloc[-1]['timestamp']
            )
            if (not math.isfinite(entry_atr) or entry_atr <= 0
                    or not math.isfinite(confirmation_bar_id)
                    or confirmation_bar_id <= 0):
                raise ValueError('invalid execution ATR or confirmation candle')
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            if diagnostics is not None:
                diagnostics['reason'] = 'BLOCKED_INVALID_PIPELINE_EXECUTION_EVIDENCE'
            return None

        decision.setdefault('entry_atr', entry_atr)
        decision.setdefault('confirmation_bar_id', confirmation_bar_id)
        decision.setdefault('breakout_bar_id', confirmation_bar_id)
        decision.setdefault('pair_confirmation_bar_id', confirmation_bar_id)
        decision.setdefault('entry_phase', 'PIPELINE_ENTRY')
        decision.setdefault(
            'intrabar',
            decision.get('type') == 'AUTHORIZED_REALTIME_BREAKOUT'
            or not bool(live.get('is_closed', True)),
        )
        decision['close_price'] = float(quote)
        decision.setdefault(
            'pending_signal_id',
            f"{symbol or 'UNKNOWN'}:{decision['type']}:{int(confirmation_bar_id)}",
        )
        decision['_is_authorized'] = True
        decision['spatial_state'] = context.state
        decision['spatial_bandwidth'] = context.bandwidth
        decision['solidity_ratio'] = context.solidity_ratio

        if symbol:
            import logging
            p_logger = logging.getLogger("SpatialBrain")
            decision_action = "AUTHORIZED"
            decision_reason = f"Passed all gates for {side}"
            p_logger.info(
                f"[SPATIAL_DIAGNOSTIC] Symbol: {symbol} | Context: {context.state} | "
                f"Chop_Index: {context.chop_index:.2f} | Bandwidth_Ratio: {context.bandwidth_ratio:.2f} | "
                f"Solidity: {context.solidity_ratio*100:.1f}% | MA3_Slope: {context.ma3_slope:+.4f} | "
                f"Decision: {decision_action} ({decision_reason})"
            )

        return decision


pipeline = EntryGatePipeline()
