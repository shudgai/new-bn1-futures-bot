"""DualTimeframeProvider
提供 5M（操作級別）+ 15M（大趨勢級別）跨週期指標計算與共振分析。
"""
from typing import Dict, Any, Optional, Tuple, List
import pandas as pd
import numpy as np


class DualTimeframeProvider:
    @staticmethod
    def compute_indicators_df(df: pd.DataFrame) -> pd.DataFrame:
        """計算基礎技術指標 (MA5, MA15, KC, ATR)"""
        if df is None or len(df) == 0:
            return df
        df = df.copy()
        
        # 確保必要欄位為 float
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # 均線計算
        if 'close' in df.columns:
            if 'ma5' not in df.columns or df['ma5'].isna().all():
                df['ma5'] = df['close'].rolling(window=5, min_periods=1).mean()
            if 'ma15' not in df.columns or df['ma15'].isna().all():
                df['ma15'] = df['close'].rolling(window=15, min_periods=1).mean()

        # ATR 計算 (預設 14)
        if 'atr' not in df.columns or df['atr'].isna().all():
            high = df['high']
            low = df['low']
            close_prev = df['close'].shift(1)
            tr1 = high - low
            tr2 = (high - close_prev).abs()
            tr3 = (low - close_prev).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            df['atr'] = tr.rolling(window=14, min_periods=1).mean()
            # 若仍有 NaN，以 high - low 或 1e-4 填充
            df['atr'] = df['atr'].fillna(tr).fillna(1e-4)

        # KC 通道 (Keltner Channel: Middle=EMA20/SMA20, Range=2.0 * ATR)
        if 'kc_middle' not in df.columns or df['kc_middle'].isna().all():
            df['kc_middle'] = df['close'].rolling(window=20, min_periods=1).mean()
        if 'kc_upper' not in df.columns or df['kc_upper'].isna().all():
            df['kc_upper'] = df['kc_middle'] + 2.0 * df['atr']
        if 'kc_lower' not in df.columns or df['kc_lower'].isna().all():
            df['kc_lower'] = df['kc_middle'] - 2.0 * df['atr']

        return df

    @classmethod
    def get_15m_trend(cls, df_15m: pd.DataFrame) -> Dict[str, Any]:
        """計算 15M 大週期指標與大趨勢方向：
        * 15M 多頭大氣候（UP）：15m_close >= 15m_ma15 且 15m_kc_trend == "UP" 且 15m_ma15_slope >= 0
        * 15M 空頭大氣候（DOWN）：15m_close <= 15m_ma15 且 15m_kc_trend == "DOWN" 且 15m_ma15_slope <= 0
        * 否則為 "RANGE"
        """
        if df_15m is None or len(df_15m) < 2:
            return {
                '15m_big_trend': 'RANGE',
                '15m_kc_trend': 'RANGE',
                '15m_close': 0.0,
                '15m_ma5': 0.0,
                '15m_ma15': 0.0,
                '15m_ma15_slope': 0.0,
                '15m_kc_middle': 0.0,
                '15m_kc_upper': 0.0,
                '15m_kc_lower': 0.0,
            }

        df = cls.compute_indicators_df(df_15m)
        # 嚴格取已收盤棒 (若最後一根尚未收盤取 iloc[-2]，若已收盤取 iloc[-1])
        if 'is_closed' in df.columns:
            closed = df[df['is_closed'] == True]
            if len(closed) >= 2:
                curr_bar = closed.iloc[-1]
                prev_bar = closed.iloc[-2]
                prev2_bar = closed.iloc[-3] if len(closed) >= 3 else prev_bar
            else:
                curr_bar = df.iloc[-1]
                prev_bar = df.iloc[-2]
                prev2_bar = prev_bar
        else:
            curr_bar = df.iloc[-1]
            prev_bar = df.iloc[-2]
            prev2_bar = df.iloc[-3] if len(df) >= 3 else prev_bar

        close_15m = float(curr_bar['close'])
        ma5_15m = float(curr_bar['ma5'])
        ma15_15m = float(curr_bar['ma15'])
        prev_ma15 = float(prev_bar['ma15'])
        ma15_slope_15m = ma15_15m - prev_ma15

        mid_15m = float(curr_bar['kc_middle'])
        prev_mid = float(prev_bar['kc_middle'])
        prev2_mid = float(prev2_bar['kc_middle'])

        upper_15m = float(curr_bar['kc_upper'])
        lower_15m = float(curr_bar['kc_lower'])

        # 顯式 channel_state / kc_trend
        c_state = str(curr_bar.get('channel_state', '') or curr_bar.get('kc_trend', '')).strip().upper()
        if any(w in c_state for w in ('KC向下', 'DOWN', 'DOWNTREND', 'BEARISH', 'SHORT', 'KC_TREND_DOWN')):
            kc_trend_15m = 'DOWN'
        elif any(w in c_state for w in ('KC向上', 'UP', 'UPTREND', 'BULLISH', 'LONG', 'KC_TREND_UP')):
            kc_trend_15m = 'UP'
        else:
            # 中軌方向判定（加入死區，避免中軌水平走平微幅浮點抖動被誤判為單邊趨勢）
            mid_diff = mid_15m - prev_mid
            atr_15m = float(curr_bar.get('atr', mid_15m * 0.01))
            deadband = max(atr_15m * 0.04, mid_15m * 0.0001)
            is_mid_up = mid_diff > deadband
            is_mid_down = mid_diff < -deadband
            if is_mid_up and (prev_mid >= prev2_mid - deadband or ma5_15m > ma15_15m):
                kc_trend_15m = 'UP'
            elif is_mid_down and (prev_mid <= prev2_mid + deadband or ma5_15m < ma15_15m):
                kc_trend_15m = 'DOWN'
            else:
                kc_trend_15m = 'RANGE'

        # 15M 大趨勢判定 (15m_big_trend)
        is_bull = (close_15m >= ma15_15m) and (kc_trend_15m == 'UP') and (ma15_slope_15m >= -1e-6)
        is_bear = (close_15m <= ma15_15m) and (kc_trend_15m == 'DOWN') and (ma15_slope_15m <= 1e-6)

        if is_bull and not is_bear:
            big_trend = 'UP'
        elif is_bear and not is_bull:
            big_trend = 'DOWN'
        else:
            big_trend = 'RANGE'

        return {
            '15m_big_trend': big_trend,
            '15m_kc_trend': kc_trend_15m,
            '15m_close': close_15m,
            '15m_ma5': ma5_15m,
            '15m_ma15': ma15_15m,
            '15m_ma15_slope': ma15_slope_15m,
            '15m_kc_middle': mid_15m,
            '15m_kc_upper': upper_15m,
            '15m_kc_lower': lower_15m,
        }

    @classmethod
    def get_5m_metrics(cls, df_5m: pd.DataFrame) -> Dict[str, Any]:
        """計算 5M 操作週期指標，嚴格基於已確認收盤 K 棒：
        - 5m_close, 5m_open, 5m_high, 5m_low, 5m_body, 5m_atr
        - 5m_ma5, 5m_ma15, 5m_ma5_slope, 5m_ma15_slope
        - 5m_kc_upper, 5m_kc_middle, 5m_kc_lower
        - ma_cross: 1~3 根內 MA5/MA15 真金叉 (GOLDEN) / 死叉 (DEATH) / NONE
        """
        if df_5m is None or len(df_5m) < 2:
            return {
                '5m_close': 0.0, '5m_open': 0.0, '5m_high': 0.0, '5m_low': 0.0,
                '5m_body': 0.0, '5m_atr': 0.0, '5m_ma5': 0.0, '5m_ma15': 0.0,
                '5m_ma5_slope': 0.0, '5m_ma15_slope': 0.0,
                '5m_kc_upper': 0.0, '5m_kc_middle': 0.0, '5m_kc_lower': 0.0,
                'ma_cross': 'NONE', 'cross_bars_ago': 999,
            }

        df = cls.compute_indicators_df(df_5m)
        
        # 判斷最後一根是否已收盤
        # 依規格：一般情況取已確認收盤 K 棒，但允許 5M 盤中強動能即時觸發 (Intra-bar Early Trigger)：
        # 若最後一根未收盤 (is_closed == False)，檢查即時 K 棒是否具備強動能 (累計波動 >= 0.35 ATR 且穿破軌道)
        last_is_closed = bool(df.iloc[-1].get('is_closed', True))
        use_live_bar = False
        if not last_is_closed and len(df) >= 2:
            live_bar = df.iloc[-1]
            c_live = float(live_bar.get('close', 0.0))
            o_live = float(live_bar.get('open', 0.0))
            body_live = abs(c_live - o_live)
            atr_live = float(live_bar.get('atr', 1e-4))
            upper_live = float(live_bar.get('kc_upper', 0.0))
            lower_live = float(live_bar.get('kc_lower', 0.0))
            # 盤中強動能條件：波動 >= 0.35 ATR 且實體同向穿破 KC 軌道
            if body_live >= 0.35 * atr_live:
                if (c_live > upper_live and c_live > o_live) or (c_live < lower_live and c_live < o_live):
                    use_live_bar = True

        if not last_is_closed and len(df) >= 3 and not use_live_bar:
            closed_df = df.iloc[:-1]
        else:
            closed_df = df

        curr = closed_df.iloc[-1]
        prev = closed_df.iloc[-2] if len(closed_df) >= 2 else curr
        prev2 = closed_df.iloc[-3] if len(closed_df) >= 3 else prev

        c = float(curr['close'])
        o = float(curr['open'])
        h = float(curr['high'])
        l = float(curr['low'])
        body = abs(c - o)
        atr = float(curr.get('atr', 1e-4))
        if atr <= 0:
            atr = 1e-4

        ma5 = float(curr['ma5'])
        prev_ma5 = float(prev['ma5'])
        # 立即斜率: 當前 MA5 減去上一根 MA5 (對齊 AGENTS.md 當前 MA5 > 上一根 MA5 鐵律)
        ma5_slope = (ma5 - prev_ma5) / (c + 1e-9)

        ma15 = float(curr['ma15'])
        prev_ma15 = float(prev['ma15'])
        ma15_slope = (ma15 - prev_ma15) / (c + 1e-9)

        mid = float(curr['kc_middle'])
        upper = float(curr['kc_upper'])
        lower = float(curr['kc_lower'])

        # 檢測過去 1~6 根內是否有 MA5/MA15 真交叉
        ma_cross = 'NONE'
        cross_bars_ago = 999
        check_len = min(len(closed_df), 7)
        for i in range(1, check_len):
            idx_curr = -i
            idx_prev = -i - 1
            if abs(idx_prev) <= len(closed_df):
                c_m5 = float(closed_df.iloc[idx_curr]['ma5'])
                c_m15 = float(closed_df.iloc[idx_curr]['ma15'])
                p_m5 = float(closed_df.iloc[idx_prev]['ma5'])
                p_m15 = float(closed_df.iloc[idx_prev]['ma15'])

                if p_m5 <= p_m15 and c_m5 > c_m15:
                    ma_cross = 'GOLDEN'
                    cross_bars_ago = i - 1
                    break
                elif p_m5 >= p_m15 and c_m5 < c_m15:
                    ma_cross = 'DEATH'
                    cross_bars_ago = i - 1
                    break

        return {
            '5m_close': c,
            '5m_open': o,
            '5m_high': h,
            '5m_low': l,
            '5m_body': body,
            '5m_atr': atr,
            '5m_ma5': ma5,
            '5m_ma15': ma15,
            '5m_ma5_slope': ma5_slope,
            '5m_ma15_slope': ma15_slope,
            '5m_kc_upper': upper,
            '5m_kc_middle': mid,
            '5m_kc_lower': lower,
            'ma_cross': ma_cross,
            'cross_bars_ago': cross_bars_ago,
        }
