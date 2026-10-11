"""Entry Gate Pipeline: Unified authorization funnel for all trading entries."""
import math
from typing import Optional, Dict, Any, Callable
import pandas as pd

from core.services.candle_data import closed_entry_candles
from core.intelligence.spatial_brain import SpatialBrain
from core.gates.chop_filter_gate import ChopFilterGate
from core.gates.candle_solidity_gate import CandleSolidityGate
from core.gates.mouth_expansion_gate import MouthExpansionGate
from core.services.strategies.outer_strategy import ck_direction

MAX_MA15_ENTRY_BIAS_ATR = 1.8
CHOP_KC_SLOPE_EPSILON_ATR = 0.10
CHOP_MA_TANGLE_ATR = 0.25
CHOP_MIN_CHANNEL_BODY_ATR = 0.40
PIPELINE_ENTRY_TYPES = frozenset((
    'AUTHORIZED_REALTIME_BREAKOUT',
    'AUTHORIZED_BY_TREND_CONTINUATION_LONG',
    'AUTHORIZED_BY_TREND_CONTINUATION_SHORT',
    'AUTHORIZED_SHADOW_RETEST_ENTRY',
    'AUTHORIZED_TOP_REVERSAL_SHORT',
    'AUTHORIZED_BY_PEAK_FLIP_SHORT',
    'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
    'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
    'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
    'TOP_WATERFALL_FLIP',
))


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
                and current_ma5 - previous_ma5 > 0
            )
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

    def market_regime_problem(self, symbol: Optional[str], frame=None,
                              quote: Optional[float] = None) -> Optional[str]:
        if not callable(self.market_regime_provider):
            return 'BLOCKED_BY_AI_CHOP_REGIME'
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
        if (regime == 'CHOPPY' and frame is not None and quote is not None
                and self.has_strong_bullish_breakout(frame, quote)):
            return None
        return 'BLOCKED_BY_AI_CHOP_REGIME'

    @staticmethod
    def chop_lockout_problem(frame: pd.DataFrame, quote: float) -> Optional[str]:
        """Fail closed on unknown/flat KC, tangled MAs, or weak in-channel candles."""
        try:
            if frame is None or frame.empty:
                return 'BLOCKED_BY_CHOP_GATE_DATA'
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
            if (channel_state not in known_directions
                    or (abs(upper_slope) <= CHOP_KC_SLOPE_EPSILON_ATR * atr
                        and abs(lower_slope) <= CHOP_KC_SLOPE_EPSILON_ATR * atr)):
                if not EntryGatePipeline.has_explosive_bullish_kc_breakout(frame, quote):
                    return 'BLOCKED_BY_CHOPPY_UNKNOWN_DIRECTION'

            ma5 = float(live['ma5'])
            ma15 = float(live['ma15'])
            if not bool(live.get('is_closed', True)):
                adjustment = quote - float(live['close'])
                ma5 += adjustment / 5.0
                ma15 += adjustment / 15.0
            if (not all(math.isfinite(value) and value > 0 for value in (ma5, ma15))
                    or abs(ma5 - ma15) < CHOP_MA_TANGLE_ATR * atr):
                return 'BLOCKED_BY_MA_TANGLING'

            opening = float(live['open'])
            close = float(live['close']) if bool(live.get('is_closed', True)) else quote
            if not math.isfinite(opening) or opening <= 0:
                return 'BLOCKED_BY_CHOP_GATE_DATA'
            if lower <= quote <= upper and abs(close - opening) <= CHOP_MIN_CHANNEL_BODY_ATR * atr:
                return 'BLOCKED_BY_CHOPPY_CHANNEL_BODY'
            return None
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            return 'BLOCKED_BY_CHOP_GATE_DATA'

    @staticmethod
    def _ma15_bias_problem(frame: pd.DataFrame, quote: float, side: str,
                           allow_flat_slope: bool = False) -> Optional[str]:
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
            if side == 'LONG' and (quote <= ma15 or ma15_slope < 0):
                return 'BLOCKED_MA_BIAS_DIRECTION'
            if side == 'SHORT' and (quote >= ma15 or ma15_slope > 0):
                return 'BLOCKED_MA_BIAS_DIRECTION'
            return None
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            return 'BLOCKED_MA_BIAS_DATA'

    @staticmethod
    def detect_realtime_breakout(frame: pd.DataFrame, quote: float,
                                 side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """盤中 Tick 級破軌即時開倉（無需等收線）。
        
        做多：即時價格突破上軌，且順向實體至少 0.15 ATR。
        做空：即時價格跌破下軌，且順向實體至少 0.15 ATR。
        """
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
        candle_range = high_p - low_p
        if candle_range <= 0:
            return None
        try:
            live_close = float(curr['close'])
            ma5 = float(curr['ma5']) + (quote - live_close) / 5.0
            ma15 = float(curr['ma15']) + (quote - live_close) / 15.0
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if not all(math.isfinite(value) and value > 0 for value in (ma5, ma15)):
            return None

        # Check Long
        if (side is None or side == 'LONG') and kc_upper > 0:
            if quote > kc_upper:
                body_long = quote - open_p
                distance_atr = (quote - kc_upper) / atr
                if body_long >= 0.15 * atr and ma5 >= ma15:
                    return {
                        'type': 'AUTHORIZED_REALTIME_BREAKOUT',
                        'side': 'LONG',
                        'price': quote,
                        'realtime_body_atr': body_long / atr,
                        'realtime_body_ratio': body_long / candle_range,
                        'realtime_distance_atr': distance_atr,
                        'is_breakout': True,
                        'override_cooldown': True,
                        'confirmation_bar_id': float(curr.get('timestamp', 0)),
                        'breakout_bar_id': float(curr.get('timestamp', 0)),
                        'pending_signal_id': f"RT_BREAKOUT:LONG:{curr.get('timestamp', 0)}",
                        'entry_phase': 'KC_LIVE_OUTER_BREAKOUT',
                        'reason': 'AUTHORIZED_REALTIME_BREAKOUT',
                    }

        # Check Short
        if (side is None or side == 'SHORT') and kc_lower > 0:
            if quote < kc_lower:
                body_short = open_p - quote
                distance_atr = (kc_lower - quote) / atr
                if body_short >= 0.15 * atr and ma5 <= ma15:
                    return {
                        'type': 'AUTHORIZED_REALTIME_BREAKOUT',
                        'side': 'SHORT',
                        'price': quote,
                        'realtime_body_atr': body_short / atr,
                        'realtime_body_ratio': body_short / candle_range,
                        'realtime_distance_atr': distance_atr,
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
    def detect_trend_continuation(frame: pd.DataFrame, quote: float,
                                  side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """情境 1：順勢延續（Trend Ride - 均線之上只做多，均線之下只做空）。
        
        - 當 MA5 > MA15 且 MA5 斜率向上 (ma5 >= prev_ma5)：
          * 【全面取消破 KC 軌道要求】！
          * 只要 1M 實體站上 MA5（Close > MA5 且陽線 Close > Open，實體佔比 >= 50%）：
          * 直接授權開多！(AUTHORIZED_BY_TREND_CONTINUATION_LONG)
        - 當 MA5 <= MA15 且 MA15 斜率非正：
          * 【全面取消破 KC 軌道要求】！
          * 只要即時價格壓在 MA5 下且當根為陰線實體：
          * 直接授權開空！(AUTHORIZED_BY_TREND_CONTINUATION_SHORT)
        """
        if frame is None or len(frame) < 2:
            return None

        curr = frame.iloc[-1]
        prev = frame.iloc[-2]

        try:
            quote = float(quote)
            open_p = float(curr['open'])
            high_p = max(float(curr['high']), quote)
            low_p = min(float(curr['low']), quote)
            live_close = float(curr['close'])
            ma5 = float(curr['ma5']) + (quote - live_close) / 5.0
            ma15 = float(curr['ma15']) + (quote - live_close) / 15.0
            prev_ma15 = float(prev['ma15'])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if not all(math.isfinite(value) and value > 0
                   for value in (
                       quote, open_p, high_p, low_p, live_close,
                       ma5, ma15, prev_ma15,
                   )):
            return None
        candle_range = high_p - low_p
        if candle_range <= 0 or high_p < max(open_p, quote) or low_p > min(open_p, quote):
            return None
        body = abs(quote - open_p)
        solidity_ratio = body / candle_range

        # ── 1. 做多順勢延續 ──
        if side is None or side == 'LONG':
            if (solidity_ratio >= 0.45 and ma5 > ma15
                    and ma15 > prev_ma15 and quote > ma5 and quote > open_p):
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

        # ── 2. 做空順勢延續 ──
        if side is None or side == 'SHORT':
            if (ma5 <= ma15 and ma15 <= prev_ma15
                    and quote < ma5 and quote < open_p):
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
    def detect_top_waterfall_flip_short(frame: pd.DataFrame, quote: float,
                                        side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """頂部斷頭鍘特例：授權「平多 + 秒反手開空」（Top Waterfall Flip）。
        
        在持有多單的高位環境下，若出現極端見頂殺盤：
        1. 【觸發條件】：
           - 當根為強勢實體陰線（Open - Close >= 0.45 * ATR）。
           - 收盤價強勢貫穿跌破 MA5，並吞噬前棒低點。
        2. 【執行授權】：
           - 授權 FLIP_ENTER_SHORT！
           - 【豁免】「禁止同根反手」限制，且【豁免】大級別「KC向上禁止開空」限制！
           - 允許在該根收線瞬間完成【平多 + 立即開空】，順勢咬住高位主跌浪！
        """
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
            
            if not (is_strong_bearish and pierces_ma5_and_engulfs_low):
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

    def authorize(self, decision: Optional[Dict[str, Any]], frame: pd.DataFrame,
                  quote: float, symbol: Optional[str] = None,
                  requested_side: Optional[str] = None,
                  account=None,
                  diagnostics: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        regime_problem = self.market_regime_problem(symbol, frame, quote)
        if regime_problem:
            if diagnostics is not None:
                diagnostics['reason'] = regime_problem
            return None
        chop_problem = self.chop_lockout_problem(frame, quote)
        if chop_problem:
            is_flip_candidate = (
                (decision is not None and (decision.get('is_reversal_flip') or decision.get('type') in (
                    'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP',
                )))
                or (decision is None and self.detect_top_waterfall_flip_short(frame, quote, side=requested_side) is not None)
            )
            is_shadow_candidate = (
                (decision is not None and (decision.get('is_shadow_retest') or decision.get('type') == 'AUTHORIZED_SHADOW_RETEST_ENTRY'))
                or (decision is None and self.detect_shadow_retest_long(frame, quote, side=requested_side) is not None)
            )
            if not is_flip_candidate and not is_shadow_candidate:
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
                waterfall_flip = (
                    self.detect_top_waterfall_flip_short(frame, quote, side=requested_side)
                    if requested_side in (None, 'SHORT') else None
                )
                if waterfall_flip is not None:
                    decision = waterfall_flip
                else:
                    peak_flip = (
                        self.detect_peak_flip_short(frame, quote, account, symbol)
                        if requested_side in (None, 'SHORT') else None
                    )
                    if peak_flip is not None:
                        decision = peak_flip
                    else:
                        cont_decision = self.detect_trend_continuation(frame, quote, side=requested_side)
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
            if supplied_type == 'AUTHORIZED_REALTIME_BREAKOUT':
                refreshed = self.detect_realtime_breakout(frame, quote, side=side)
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
                refreshed = self.detect_trend_continuation(frame, quote, side=side)
            else:
                refreshed = self.detect_reversal_flip(frame, quote, side=side)
            if (refreshed is None or refreshed.get('type') != supplied_type
                    or refreshed.get('pending_signal_id') != decision.get('pending_signal_id')):
                if diagnostics is not None:
                    diagnostics['reason'] = 'BLOCKED_PIPELINE_SIGNAL_CHANGED'
                return None
            decision = refreshed

        side = decision.get('side')
        if side not in ('LONG', 'SHORT'):
            if diagnostics is not None:
                diagnostics['reason'] = 'INVALID_SIDE'
            return None

        # ── 最頂層進場門控：MA5 幾何生死鐵律（一票否決權） ──
        if frame is not None and len(frame) >= 2:
            try:
                curr_b = frame.iloc[-1]
                prev_b = frame.iloc[-2]
                raw_cl = float(curr_b.get('close', quote))
                is_cl = bool(curr_b.get('is_closed', False))
                curr_m5 = float(curr_b.get('ma5', raw_cl))
                prev_m5 = float(prev_b.get('ma5', curr_m5))
                live_m5 = curr_m5 if is_cl else curr_m5 + (float(quote) - raw_cl) / 5.0
                m5_slope = live_m5 - prev_m5
                
                atr_val = float(prev_b.get('atr', curr_b.get('atr', 1.0)))
                if atr_val <= 0:
                    atr_val = 1.0
                close_ref = float(quote) if float(quote) > 0 else 1.0
                chop_threshold = 0.05 * (atr_val / close_ref)
                
                is_shadow_entry = (decision.get('type') == 'AUTHORIZED_SHADOW_RETEST_ENTRY')

                # 3. MA5 走平，100% 全面休眠禁止開倉（平行不開倉）
                if abs(m5_slope) < chop_threshold and not is_shadow_entry:
                    if diagnostics is not None:
                        diagnostics['reason'] = 'BLOCKED_BY_MA5_PARALLEL_CHOP'
                    return None
                    
                # 1. MA5 向下，100% 絕對禁止開多（向下不開多）
                if side == 'LONG' and m5_slope < 0 and not is_shadow_entry:
                    if diagnostics is not None:
                        diagnostics['reason'] = 'BLOCKED_BY_MA5_SLOPE_DOWN'
                    return None
                    
                # 2. MA5 向上，100% 絕對禁止開空（向上不開空）
                if side == 'SHORT' and m5_slope > 0:
                    if diagnostics is not None:
                        diagnostics['reason'] = 'BLOCKED_BY_MA5_SLOPE_UP'
                    return None
            except Exception:
                pass

        # 1. Spatial Brain Perception
        context = SpatialBrain.analyze(frame, quote)
        is_explosive_breakout = (context.state == 'EXPLOSIVE_EXPANSION')

        # ── 爆發突破（EXPLOSIVE_BREAKOUT）：100% 豁免 ma15_slope 走平阻斷 ──
        # ── 頂部斷頭鍘特例 (Top Waterfall Flip)：100% 豁免大級別「KC/MA 向上禁止開空」限制 ──
        is_top_waterfall_flip = (
            decision.get('type') in ('AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP')
            or decision.get('reason') in ('AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP')
        )
        if not is_top_waterfall_flip:
            ma_bias_problem = self._ma15_bias_problem(
                frame, quote, side,
                allow_flat_slope=(is_explosive_breakout or decision.get('type') == 'AUTHORIZED_SHADOW_RETEST_ENTRY')
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
        is_reversal_flip = bool(
            decision.get('is_reversal_flip')
            or decision.get('type') in (
                'AUTHORIZED_BY_PEAK_FLIP_SHORT',
                'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
                'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
                'AUTHORIZED_TOP_REVERSAL_SHORT',
                'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
                'TOP_WATERFALL_FLIP',
            )
            or decision.get('reason') in (
                'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
                'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
                'AUTHORIZED_TOP_REVERSAL_SHORT',
                'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
                'TOP_WATERFALL_FLIP',
            )
        )

        # 2. Iterate through gates
        for name, gate_func in self.gates:
            if (is_trend_continuation or is_shadow_retest) and name == 'CANDLE_SOLIDITY':
                continue
            # Realtime breakouts already pass the dedicated 15% solidity and
            # 0.15 ATR body checks in detect_realtime_breakout.
            if is_rt_breakout and name == 'CANDLE_SOLIDITY':
                continue
            # 快車道、順勢延續、下影線回踩與反手翻轉全面豁免 MOUTH_EXPANSION（全面取消破 KC 軌道要求）
            if (is_rt_breakout or is_trend_continuation or is_reversal_flip or is_shadow_retest) and name == 'MOUTH_EXPANSION':
                continue
            # 快車道、順勢延續、下影線回踩與反手翻轉全面豁免 CHOP_FILTER（順勢已由均線確認，反手為單棒吞噬）
            if (is_rt_breakout or is_trend_continuation or is_reversal_flip or is_shadow_retest) and name == 'CHOP_FILTER':
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

        # 3. Anti-trend hard structure check (反手翻轉與順勢延續豁免舊趨勢排列)
        if not (is_reversal_flip or is_trend_continuation or is_rt_breakout):
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
