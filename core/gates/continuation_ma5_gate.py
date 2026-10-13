"""Gate 4: 平倉後的「延續開倉」與「MA5 動能過濾」閘門。

規則依據（2026-10-13 最新授權）：
1. MA5 逆勢過濾禁令（避免開在動能轉弱時）：
   - 禁開多單：若當前 MA5 斜率向下（或短線走弱下拐，live_ma5 <= prev_ma5），即便滿足其他條件，一律嚴禁開多單。
   - 禁開空單：若當前 MA5 斜率向上（或短線走強上拐，live_ma5 >= prev_ma5），即便滿足其他條件，一律嚴禁開空單。
   - 價格位置檢驗：
     * 多單：價格必須站上 MA5（quote > live_ma5），低於或等於均線嚴禁開多。
     * 空單：價格必須壓制在 MA5 之下（quote < live_ma5），高於或等於均線嚴禁開空。
2. 平倉後的「延續開倉（Re-entry）」與「破軌沒開倉後面延續開倉」情境：
   - 在部位平倉出場後，如果大趨勢未變（KC 中軌與整體趨勢順向）且後續再次出現順勢擴張/突破（仍有繼續走單邊的空間），允許觸發「延續開倉（Re-entry）」。
   - 若破軌當下未開倉，後面只要大趨勢順向、行情在同側軌外/均線外推進，也允許延續開倉。
   - 鐵律：必須等到短期均線方向與主趨勢同步（多頭 MA5 向上、空頭 MA5 向下）時，才允許執行延續開倉。
"""
from typing import Tuple, Optional
import math
import pandas as pd
from core.intelligence.spatial_brain import SpatialContext


class ContinuationMa5Gate:
    """Gate 4: 平倉後的延續開倉與 MA5 動能過濾閘門。"""

    @staticmethod
    def calculate_live_ma5(frame: pd.DataFrame, quote: float) -> Tuple[float, float]:
        """計算即時 MA5 與前一根已收線 MA5。"""
        if frame is None or len(frame) < 1:
            return float('nan'), float('nan')
        curr = frame.iloc[-1]
        prev = frame.iloc[-2] if len(frame) >= 2 else curr

        quote = float(quote)
        live_close = float(curr.get('close', quote))
        is_closed = bool(curr.get('is_closed', False))

        prev_ma5 = float(prev.get('ma5', 0.0))
        curr_ma5 = float(curr.get('ma5', 0.0))

        # 若缺乏指標欄位，從收盤價序列計算
        if prev_ma5 <= 0 and len(frame) >= 6:
            prev_ma5 = float(frame['close'].iloc[-6:-1].mean())
        if curr_ma5 <= 0 and len(frame) >= 5:
            curr_ma5 = float(frame['close'].iloc[-5:].mean())
        elif curr_ma5 <= 0:
            curr_ma5 = float(quote)

        live_ma5 = curr_ma5 if is_closed else curr_ma5 + (quote - live_close) / 5.0
        return live_ma5, prev_ma5

    @classmethod
    def evaluate(cls, frame: pd.DataFrame, quote: float, side: str,
                 context: Optional[SpatialContext] = None) -> Tuple[bool, Optional[str]]:
        """執行 Gate 4 的 MA5 動能過濾審查（一票否決權）。"""
        if frame is None or len(frame) < 2 or side not in ('LONG', 'SHORT'):
            return False, 'BLOCKED_BY_INVALID_MA5_DATA'

        try:
            live_ma5, prev_ma5 = cls.calculate_live_ma5(frame, quote)
            if not (math.isfinite(live_ma5) and math.isfinite(prev_ma5) and prev_ma5 > 0):
                return False, 'BLOCKED_BY_INVALID_MA5_DATA'

            quote = float(quote)

            # ── 1. 多單 MA5 逆勢過濾禁令 ──
            if side == 'LONG':
                # 若當前 MA5 斜率向下（或走弱下拐/走平），一律嚴禁開多單
                if live_ma5 <= prev_ma5:
                    return False, 'BLOCKED_BY_MA5_DOWNWARD_SLOPE'
                # 價格必須站上 MA5
                if quote <= live_ma5:
                    return False, 'BLOCKED_BY_PRICE_BELOW_MA5'

            # ── 2. 空單 MA5 逆勢過濾禁令 ──
            elif side == 'SHORT':
                # 若當前 MA5 斜率向上（或走強上拐/走平），一律嚴禁開空單
                if live_ma5 >= prev_ma5:
                    return False, 'BLOCKED_BY_MA5_UPWARD_SLOPE'
                # 價格必須壓制在 MA5 之下
                if quote >= live_ma5:
                    return False, 'BLOCKED_BY_PRICE_ABOVE_MA5'

            return True, None
        except Exception:
            return False, 'BLOCKED_BY_INVALID_MA5_DATA'

    @classmethod
    def evaluate_continuation_reentry(
        cls, frame: pd.DataFrame, quote: float, side: str,
        is_reentry: bool = False, was_breakout_missed: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """評估平倉後的延續開倉（Re-entry）或破軌沒開倉後的延續開倉。"""
        # 首先必須通過 Gate 4 的 MA5 動能絕對過濾
        passed, reason = cls.evaluate(frame, quote, side)
        if not passed:
            return False, reason

        if frame is None or len(frame) < 2:
            return False, 'BLOCKED_BY_INVALID_DATA'

        curr = frame.iloc[-1]
        prev = frame.iloc[-2]

        try:
            quote = float(quote)
            live_ma5, _ = cls.calculate_live_ma5(frame, quote)

            curr_kc_mid = float(curr.get('kc_middle', 0.0))
            prev_kc_mid = float(prev.get('kc_middle', 0.0))
            curr_kc_upper = float(curr.get('kc_upper', 0.0))
            curr_kc_lower = float(curr.get('kc_lower', 0.0))

            channel_state = str(curr.get('channel_state', '')).strip().upper()

            if side == 'LONG':
                # 大趨勢未變檢查：KC 中軌未向下，且通道狀態非空頭
                if curr_kc_mid > 0 and prev_kc_mid > 0 and curr_kc_mid < prev_kc_mid:
                    return False, 'BLOCKED_BY_KC_MIDDLE_FALLING'
                if channel_state in ('KC向下', 'KC_TREND_DOWN', 'DOWN', 'DOWNTREND', 'BEARISH', 'SHORT'):
                    return False, 'BLOCKED_BY_KC_CHANNEL_DOWNTREND'

                # 退回防護：若價格退回通道內且跌回 MA5，一票否決
                if curr_kc_upper > 0 and quote <= curr_kc_upper and quote <= live_ma5:
                    return False, 'BLOCKED_CONTINUATION_QUOTE_RETURNED_INSIDE_KC'

            elif side == 'SHORT':
                # 大趨勢未變檢查：KC 中軌未向上，且通道狀態非多頭
                if curr_kc_mid > 0 and prev_kc_mid > 0 and curr_kc_mid > prev_kc_mid:
                    return False, 'BLOCKED_BY_KC_MIDDLE_RISING'
                if channel_state in ('KC向上', 'KC_TREND_UP', 'UP', 'UPTREND', 'BULLISH', 'LONG'):
                    return False, 'BLOCKED_BY_KC_CHANNEL_UPTREND'

                # 退回防護：若價格退回通道內且漲回 MA5，一票否決
                if curr_kc_lower > 0 and quote >= curr_kc_lower and quote >= live_ma5:
                    return False, 'BLOCKED_CONTINUATION_QUOTE_RETURNED_INSIDE_KC'

            return True, None
        except Exception:
            return False, 'BLOCKED_BY_INVALID_DATA'
