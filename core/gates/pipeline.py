"""Entry Gate Pipeline: Unified authorization funnel for all trading entries."""
import math
from typing import Optional, Dict, Any
import pandas as pd

from core.intelligence.spatial_brain import SpatialBrain
from core.gates.chop_filter_gate import ChopFilterGate
from core.gates.candle_solidity_gate import CandleSolidityGate
from core.gates.mouth_expansion_gate import MouthExpansionGate

REALTIME_BREAKOUT_MAX_DISTANCE_ATR = 3.5
PIPELINE_ENTRY_TYPES = frozenset((
    'AUTHORIZED_REALTIME_BREAKOUT',
    'AUTHORIZED_BY_TREND_CONTINUATION_LONG',
    'AUTHORIZED_BY_TREND_CONTINUATION_SHORT',
    'AUTHORIZED_BY_PEAK_FLIP_SHORT',
    'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
    'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
))


class EntryGatePipeline:
    """The single authorization funnel for all trade entries.

    Ensures that no entry can bypass the spatial geometric checks,
    candle solidity gate, or mouth expansion gate.
    """

    def __init__(self):
        self.gates = [
            ('CANDLE_SOLIDITY', CandleSolidityGate.evaluate),
            ('CHOP_FILTER', ChopFilterGate.evaluate),
            ('MOUTH_EXPANSION', MouthExpansionGate.evaluate),
        ]

    @staticmethod
    def detect_realtime_breakout(frame: pd.DataFrame, quote: float,
                                 side: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """盤中 Tick 級破軌即時開倉（無需等收線）。
        
        做多：Price > KC_Upper 且 (Price - Open) >= 0.35 * ATR 且 (Price - Open) / (Price - Low + 1e-9) >= 0.60
        做空：Price < KC_Lower 且 (Open - Price) >= 0.35 * ATR 且 (Open - Price) / (High - Price + 1e-9) >= 0.60
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

        # Check Long
        if (side is None or side == 'LONG') and kc_upper > 0:
            if quote > kc_upper:
                body_long = quote - open_p
                distance_atr = (quote - kc_upper) / atr
                if (body_long >= 0.35 * atr and body_long / candle_range >= 0.60
                        and distance_atr <= REALTIME_BREAKOUT_MAX_DISTANCE_ATR):
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
                if (body_short >= 0.35 * atr and body_short / candle_range >= 0.60
                        and distance_atr <= REALTIME_BREAKOUT_MAX_DISTANCE_ATR):
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
        - 當 MA5 < MA15 且 MA5 斜率向下 (ma5 <= prev_ma5)：
          * 【全面取消破 KC 軌道要求】！
          * 只要 1M 實體壓在 MA5 下（Close < MA5 且陰線 Close < Open，實體佔比 >= 50%）：
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
            ma5 = float(curr['ma5'])
            ma15 = float(curr['ma15'])
            prev_ma15 = float(prev['ma15'])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if not all(math.isfinite(value) and value > 0
                   for value in (quote, open_p, high_p, low_p, ma5, ma15, prev_ma15)):
            return None
        candle_range = high_p - low_p
        if candle_range <= 0 or high_p < max(open_p, quote) or low_p > min(open_p, quote):
            return None
        body = abs(quote - open_p)
        solidity_ratio = body / candle_range
        if solidity_ratio < 0.45:
            return None

        # ── 1. 做多順勢延續 ──
        if side is None or side == 'LONG':
            if ma5 > ma15 and ma15 > prev_ma15 and quote > ma5 and quote > open_p:
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
            if ma5 < ma15 and ma15 < prev_ma15 and quote < ma5 and quote < open_p:
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
        if decision is None:
            peak_flip = (
                self.detect_peak_flip_short(frame, quote, account, symbol)
                if requested_side in (None, 'SHORT') else None
            )
            if peak_flip is not None:
                decision = peak_flip
            else:
                # 優先 1：即時破軌快車道 (Tick 級大實體噴出)
                rt_decision = self.detect_realtime_breakout(frame, quote, side=requested_side)
                if rt_decision is not None:
                    decision = rt_decision
                else:
                    # 優先 2：順勢延續開倉，避免一般轉折標記遮蔽已成立的順勢入口
                    cont_decision = self.detect_trend_continuation(frame, quote, side=requested_side)
                    if cont_decision is not None:
                        decision = cont_decision
                    else:
                        # 優先 3：一般頂底轉折翻轉
                        flip_decision = self.detect_reversal_flip(frame, quote, side=requested_side)
                        if flip_decision is not None:
                            decision = flip_decision
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
            elif supplied_type == 'AUTHORIZED_BY_PEAK_FLIP_SHORT':
                refreshed = self.detect_peak_flip_short(frame, quote, account, symbol)
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

        # 檢查是否為即時破軌、順勢延續或反手翻轉
        is_rt_breakout = bool(
            decision.get('is_breakout')
            or decision.get('type') == 'AUTHORIZED_REALTIME_BREAKOUT'
            or decision.get('reason') == 'AUTHORIZED_REALTIME_BREAKOUT'
        )
        is_trend_continuation = bool(
            decision.get('is_trend_continuation')
            or decision.get('type') in ('AUTHORIZED_BY_TREND_CONTINUATION_LONG', 'AUTHORIZED_BY_TREND_CONTINUATION_SHORT')
            or decision.get('reason') in ('AUTHORIZED_BY_TREND_CONTINUATION_LONG', 'AUTHORIZED_BY_TREND_CONTINUATION_SHORT')
        )
        is_reversal_flip = bool(
            decision.get('is_reversal_flip')
            or decision.get('type') in (
                'AUTHORIZED_BY_PEAK_FLIP_SHORT',
                'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
                'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
            )
            or decision.get('reason') in ('AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT', 'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG')
        )

        # 1. Spatial Brain Perception
        context = SpatialBrain.analyze(frame, quote)

        # 2. Iterate through gates
        for name, gate_func in self.gates:
            if is_trend_continuation and name == 'CANDLE_SOLIDITY':
                continue
            # 快車道、順勢延續與反手翻轉全面豁免 MOUTH_EXPANSION（全面取消破 KC 軌道要求）
            if (is_rt_breakout or is_trend_continuation or is_reversal_flip) and name == 'MOUTH_EXPANSION':
                continue
            # 快車道、順勢延續與反手翻轉全面豁免 CHOP_FILTER（順勢已由均線確認，反手為單棒吞噬）
            if (is_rt_breakout or is_trend_continuation or is_reversal_flip) and name == 'CHOP_FILTER':
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
