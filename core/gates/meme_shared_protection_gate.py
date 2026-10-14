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
        kc_trend_15m = trend_15m_info.get('15m_kc_trend', 'RANGE')
        close_15m = float(trend_15m_info.get('15m_close', 0.0))
        ma15_15m = float(trend_15m_info.get('15m_ma15', 0.0))

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
        solid_ratio = body / candle_range
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l

        # ──【三大開倉模式判定】──
        # 模式一（5M KC 破軌開倉）：
        # 5m 實體 >= 0.4 ATR 突破上軌/下軌且均線方向一致
        # 放寬突破初期的 15M 宏觀趨勢認定：
        # - 多單：只要 15M KC 中軌未向下（kc_trend_15m != "DOWN"）且 15M 收盤價未跌破 15M MA15（close_15m >= ma15_15m 或 ma15_15m <= 0），即判定大氣候相容，允許開多。
        # - 空單：只要 15M KC 中軌未向上（kc_trend_15m != "UP"）且 15M 收盤價未突破 15M MA15（close_15m <= ma15_15m 或 ma15_15m <= 0），即判定大氣候相容，允許開空。
        is_mode1 = False
        if body >= 0.4 * atr:
            if side == 'LONG' and c > upper and ma5 >= ma15 and ma5_slope > 0:
                macro_15m_compat = (kc_trend_15m != 'DOWN') and (close_15m >= ma15_15m or ma15_15m <= 0)
                if macro_15m_compat:
                    is_mode1 = True
            elif side == 'SHORT' and c < lower and ma5 <= ma15 and ma5_slope < 0:
                macro_15m_compat = (kc_trend_15m != 'UP') and (close_15m <= ma15_15m or ma15_15m <= 0)
                if macro_15m_compat:
                    is_mode1 = True

        # 模式三（5M 斷頭鍘刀開倉）：
        # 實體 >= 0.85 ATR，佔比 >= 70%，無長影線強勢切穿均線或 KC 中軌
        is_mode3 = False
        if body >= 0.85 * atr and solid_ratio >= 0.70:
            if side == 'LONG':
                cut_ma15 = (o <= ma15 and c > ma15) or (o <= mid and c > mid)
                if cut_ma15 and c > o and upper_wick <= 0.25 * body:
                    macro_15m_compat = (kc_trend_15m != 'DOWN') and (close_15m >= ma15_15m or ma15_15m <= 0)
                    if macro_15m_compat:
                        is_mode3 = True
            elif side == 'SHORT':
                cut_ma15 = (o >= ma15 and c < ma15) or (o >= mid and c < mid)
                if cut_ma15 and c < o and lower_wick <= 0.25 * body:
                    macro_15m_compat = (kc_trend_15m != 'UP') and (close_15m <= ma15_15m or ma15_15m <= 0)
                    if macro_15m_compat:
                        is_mode3 = True

        # ──【豁免破軌/斷頭模式的乖離率與影線限制】──
        # 若觸發「模式一（KC 破軌）」或「模式三（斷頭鍘刀）」，設定 bypass_spatial_check = True
        # 當 bypass_spatial_check == True 時，100% 豁免「1.2% 均線乖離率檢核」與「影線佔比檢核」，直接放行突破進場
        bypass_spatial_check = is_mode1 or is_mode3

        meta = {
            'side': side,
            'price': p,
            '15m_big_trend': big_trend,
            '15m_kc_trend': kc_trend_15m,
            '5m_ma5_slope': ma5_slope,
            '5m_atr': atr,
            'bypass_spatial_check': bypass_spatial_check,
        }

        if is_mode1:
            meta['mode'] = 'MODE1_KC_BREAKOUT'
            return True, "AUTHORIZED_ENTRY_MODE1_KC_BREAKOUT", meta

        if is_mode3:
            meta['mode'] = 'MODE3_GUILLOTINE_CUT'
            return True, "AUTHORIZED_ENTRY_MODE3_GUILLOTINE", meta

        # ──【模式二：5M MA5/MA15 交叉震盪進場】──
        # 「15M 大趨勢順勢過濾」、「均線乖離率 > 1.2% 限制」與「影線比例限制」對模式二生效
        if side == 'LONG' and big_trend != 'UP':
            return False, f"REJECT_ENTRY: 15M_TREND_NOT_UP (15M={big_trend})", None
        elif side == 'SHORT' and big_trend != 'DOWN':
            return False, f"REJECT_ENTRY: 15M_TREND_NOT_DOWN (15M={big_trend})", None

        # 模式二：5M 影線實體過濾
        if solid_ratio < 0.35:
            return False, f"REJECT_ENTRY: WEAK_CANDLE_SOLIDITY ({solid_ratio:.2f} < 0.35)", None

        if side == 'LONG':
            if upper_wick > 0.6 * body:
                return False, f"REJECT_ENTRY: UPPER_WICK_TOO_LONG ({upper_wick:.4f} > 0.6 * {body:.4f})", None
        elif side == 'SHORT':
            if lower_wick > 0.6 * body:
                return False, f"REJECT_ENTRY: LOWER_WICK_TOO_LONG ({lower_wick:.4f} > 0.6 * {body:.4f})", None

        # 模式二：5M 均線乖離率檢核 (> 1.2% 一票否決)
        if ma5 > 0:
            bias_pct = abs(p - ma5) / ma5
            if bias_pct > 0.012:
                return False, f"REJECT_ENTRY: MA5_BIAS_EXCEEDED ({bias_pct:.4f} > 1.2%)", None

        # 模式二：判定過去 1~3 根真交叉且發散
        is_mode2 = False
        cross = m5['ma_cross']
        bars_ago = m5['cross_bars_ago']
        if bars_ago <= 3:
            if side == 'LONG' and cross == 'GOLDEN':
                if ma5_slope > 0.0005 and ma5 > ma15:
                    is_mode2 = True
            elif side == 'SHORT' and cross == 'DEATH':
                if ma5_slope < -0.0005 and ma5 < ma15:
                    is_mode2 = True

        if is_mode2:
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
