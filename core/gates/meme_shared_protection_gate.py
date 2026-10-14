"""MemeSharedProtectionGate
實現 5M（操作級別）+ 15M（大趨勢級別）跨週期共振開倉防護閘門。
涵蓋三大開倉模式與開倉前置強制審查。
"""
from typing import Dict, Any, Optional, Tuple
import pandas as pd
from core.indicators.dual_timeframe_provider import DualTimeframeProvider


class MemeSharedProtectionGate:
    @classmethod
    def evaluate_entry(
        cls,
        df_5m: pd.DataFrame,
        df_15m: Optional[pd.DataFrame] = None,
        quote: Optional[float] = None,
        side: str = 'LONG',
        symbol: Optional[str] = None,
        preferred_mode: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """審查跨週期共振開倉。
        
        Returns:
            (passed, reason, meta_dict)
        """
        if df_5m is None or len(df_5m) < 2:
            return False, "REJECT_ENTRY: INSUFFICIENT_5M_DATA", None

        side = side.upper()
        if side not in ('LONG', 'SHORT'):
            return False, f"REJECT_ENTRY: INVALID_SIDE_{side}", None

        # 1. 取得 15M 大趨勢指標 (若未傳入 df_15m，嘗試從 5m 重採樣或內部計算)
        if df_15m is None or len(df_15m) < 2:
            trend_15m_info = cls._resample_or_derive_15m(df_5m)
        else:
            trend_15m_info = DualTimeframeProvider.get_15m_trend(df_15m)

        big_trend = trend_15m_info.get('15m_big_trend', 'RANGE')

        # ──【核心檢核 A：15M 順勢過濾】──
        # 開多必須 15M 為 UP；開空必須 15M 為 DOWN
        if side == 'LONG' and big_trend != 'UP':
            return False, f"REJECT_ENTRY: 15M_TREND_NOT_UP (15M={big_trend})", None
        elif side == 'SHORT' and big_trend != 'DOWN':
            return False, f"REJECT_ENTRY: 15M_TREND_NOT_DOWN (15M={big_trend})", None

        # 2. 取得 5M 操作週期指標
        m5 = DualTimeframeProvider.get_5m_metrics(df_5m)
        p = float(quote) if quote is not None else m5['5m_close']
        c = m5['5m_close']
        o = m5['5m_open']
        h = m5['5m_high']
        l = m5['5m_low']
        body = m5['5m_body']
        atr = m5['5m_atr']
        ma5 = m5['5m_ma5']
        ma15 = m5['5m_ma15']
        ma5_slope = m5['5m_ma5_slope']
        upper = m5['5m_kc_upper']
        lower = m5['5m_kc_lower']
        mid = m5['5m_kc_middle']
        candle_range = max(h - l, 1e-9)

        # ──【核心檢核 B：5M 影線實體過濾】──
        # 5m 實體比例 >= 0.35；開多上影線 <= 0.6*實體；開空下影線 <= 0.6*實體
        solid_ratio = body / candle_range
        if solid_ratio < 0.35:
            return False, f"REJECT_ENTRY: WEAK_CANDLE_SOLIDITY ({solid_ratio:.2f} < 0.35)", None

        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l

        if side == 'LONG':
            if upper_wick > 0.6 * body:
                return False, f"REJECT_ENTRY: UPPER_WICK_TOO_LONG ({upper_wick:.4f} > 0.6 * {body:.4f})", None
        elif side == 'SHORT':
            if lower_wick > 0.6 * body:
                return False, f"REJECT_ENTRY: LOWER_WICK_TOO_LONG ({lower_wick:.4f} > 0.6 * {body:.4f})", None

        # ──【核心檢核 C：5M 乖離率上限】──
        # (價格與 5m_ma5 偏離幅度) > 1.2% 一票否決 (防止天花板追多或地板追空)
        if ma5 > 0:
            bias_pct = abs(p - ma5) / ma5
            if bias_pct > 0.012:
                return False, f"REJECT_ENTRY: MA5_BIAS_EXCEEDED ({bias_pct:.4f} > 1.2%)", None

        # ──【三大開倉模式判定】──
        # 模式一（5M KC 破軌開倉）：5m 實體 >= 0.4 ATR 突破上軌/下軌且均線方向一致
        is_mode1 = False
        if body >= 0.4 * atr:
            if side == 'LONG' and c > upper and ma5 >= ma15 and ma5_slope > 0:
                is_mode1 = True
            elif side == 'SHORT' and c < lower and ma5 <= ma15 and ma5_slope < 0:
                is_mode1 = True

        # 模式二（5M 真交叉開倉）：過去 1~3 根真交叉，且 5M MA5 必須保持同向發散（ma5_slope 走平或逆向一票否決）
        is_mode2 = False
        cross = m5['ma_cross']
        bars_ago = m5['cross_bars_ago']
        if bars_ago <= 3:
            if side == 'LONG' and cross == 'GOLDEN':
                # 均線必須同向發散且向上拉升（斜率 > 0.0005）
                if ma5_slope > 0.0005 and ma5 > ma15:
                    is_mode2 = True
            elif side == 'SHORT' and cross == 'DEATH':
                # 均線必須同向發散且向下傾斜（斜率 < -0.0005）
                if ma5_slope < -0.0005 and ma5 < ma15:
                    is_mode2 = True

        # 模式三（5M 斷頭開倉）：實體 >= 0.85 ATR，佔比 >= 70%，無長影線強勢切穿均線或 KC 中軌
        is_mode3 = False
        if body >= 0.85 * atr and solid_ratio >= 0.70:
            if side == 'LONG':
                # 實體陽線，收盤站上 MA15 或 KC 中軌，且由下方穿過
                cut_ma15 = (o <= ma15 and c > ma15) or (o <= mid and c > mid)
                if cut_ma15 and c > o and upper_wick <= 0.25 * body:
                    is_mode3 = True
            elif side == 'SHORT':
                # 實體陰線，收盤摜穿 MA15 或 KC 中軌，且由上方穿過
                cut_ma15 = (o >= ma15 and c < ma15) or (o >= mid and c < mid)
                if cut_ma15 and c < o and lower_wick <= 0.25 * body:
                    is_mode3 = True

        # 根據命中模式放行
        meta = {
            'side': side,
            'price': p,
            '15m_big_trend': big_trend,
            '5m_ma5_slope': ma5_slope,
            '5m_atr': atr,
        }

        if is_mode3:
            meta['mode'] = 'MODE3_GUILLOTINE_CUT'
            return True, "AUTHORIZED_ENTRY_MODE3_GUILLOTINE", meta
        elif is_mode1:
            meta['mode'] = 'MODE1_KC_BREAKOUT'
            return True, "AUTHORIZED_ENTRY_MODE1_KC_BREAKOUT", meta
        elif is_mode2:
            meta['mode'] = 'MODE2_TRUE_CROSS'
            return True, "AUTHORIZED_ENTRY_MODE2_TRUE_CROSS", meta

        return False, "REJECT_ENTRY: NO_VALID_5M_PATTERN_TRIGGERED", None

    @classmethod
    def _resample_or_derive_15m(cls, df_5m: pd.DataFrame) -> Dict[str, Any]:
        """若無獨立 15M 資料，由 5M K 棒合成或衍生 15M 趨勢指標"""
        try:
            if len(df_5m) < 6:
                return {'15m_big_trend': 'RANGE', '15m_kc_trend': 'RANGE'}

            # 簡易聚合為 15m (每 3 根 5m = 1 根 15m)
            df = df_5m.copy()
            # 若已有 15m 均線指標直接取用
            if 'ma15_15m' in df.columns or 'kc_trend_15m' in df.columns:
                c_last = df.iloc[-1]
                return {
                    '15m_big_trend': str(c_last.get('15m_big_trend', 'RANGE')),
                    '15m_kc_trend': str(c_last.get('15m_kc_trend', 'RANGE')),
                }

            # 模擬 15m 窗口 (長度為 3 倍 5m)
            c = float(df['close'].iloc[-1])
            ma15_proxy = float(df['close'].rolling(45, min_periods=5).mean().iloc[-1])
            prev_ma15_proxy = float(df['close'].rolling(45, min_periods=5).mean().iloc[-4])
            slope = ma15_proxy - prev_ma15_proxy

            kc_mid_proxy = float(df['close'].rolling(60, min_periods=5).mean().iloc[-1])
            prev_kc_mid = float(df['close'].rolling(60, min_periods=5).mean().iloc[-4])

            kc_trend = 'UP' if kc_mid_proxy > prev_kc_mid else ('DOWN' if kc_mid_proxy < prev_kc_mid else 'RANGE')

            if c >= ma15_proxy and kc_trend == 'UP' and slope >= 0:
                big_trend = 'UP'
            elif c <= ma15_proxy and kc_trend == 'DOWN' and slope <= 0:
                big_trend = 'DOWN'
            else:
                big_trend = 'RANGE'

            return {
                '15m_big_trend': big_trend,
                '15m_kc_trend': kc_trend,
                '15m_close': c,
                '15m_ma15': ma15_proxy,
                '15m_ma15_slope': slope,
            }
        except Exception:
            return {'15m_big_trend': 'RANGE', '15m_kc_trend': 'RANGE'}
