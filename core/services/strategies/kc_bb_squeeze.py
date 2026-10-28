"""kc_bb_squeeze.py

此模組實作基於 Keltner Channel (KC) 的交易策略，額外加入
- 布林通道擠壓 (BB Squeeze) 判斷
- KC 中軌斜率過濾
- 順勢突破開倉 (Trend Breakout Entry)
- 跌破/突破中軌與移動止盈離場 (Trailing Stop Exit)

目的：在盤整期間避免因 KC 中軌走平而產生的假突破，並在趨勢發動突破外軌時順勢進場。
"""

import pandas as pd
import numpy as np
from typing import Optional

from core.services.strategies.kc_bb_squeeze_trailing import (
    calculate_squeeze_kc_breakout_strategy,
    calculate_squeeze_kc_strategy,
)


class KCBBStrategy:
    """KC + BB Squeeze + KC 中軌斜率過濾 + 順勢突破開倉策略

    參數皆可在實例化時調整，方便回測與優化。
    """

    def __init__(
        self,
        kc_len: int = 20,
        kc_mult: float = 2.0,
        atr_len: int = 20,
        bb_len: int = 20,
        bb_std_mult: float = 2.0,
        squeeze_min_slope: float = 0.0003,
        squeeze_lookback: int = 3,
        min_ma_dist: float = 0.001,
        pivot_lookback: int = 10,
        min_pivot_rebound_pct: float = 0.0025,
        fast_ma_len: int = 3,
        slow_ma_len: int = 15,
        trailing_stop_pct: float = 0.015,
        overbought_trailing_pct: Optional[float] = None,
        waterfall_drop_pct: float = 0.012,
    ) -> None:
        self.kc_len = kc_len
        self.kc_mult = kc_mult
        self.atr_len = atr_len
        self.bb_len = bb_len
        self.bb_std_mult = bb_std_mult
        self.squeeze_min_slope = squeeze_min_slope
        self.squeeze_lookback = squeeze_lookback
        self.min_ma_dist = min_ma_dist
        self.pivot_lookback = pivot_lookback
        self.min_pivot_rebound_pct = min_pivot_rebound_pct
        self.fast_ma_len = fast_ma_len
        self.slow_ma_len = slow_ma_len
        self.trailing_stop_pct = trailing_stop_pct
        self.overbought_trailing_pct = overbought_trailing_pct
        self.waterfall_drop_pct = waterfall_drop_pct

    # ---------------------------------------------------------------------
    # 內部工具函式
    # ---------------------------------------------------------------------
    @staticmethod
    def _ema(series: pd.Series, span: int) -> pd.Series:
        """計算 EMA，使用 pandas 的 ewm 方法。"""
        return series.ewm(span=span, adjust=False).mean()

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        """Average True Range (ATR) 計算。
        依照傳統定義：TR = max(high-low, abs(high-prev_close), abs(low-prev_close))
        """
        high_low = df['high'] - df['low']
        high_prev = (df['high'] - df['close'].shift(1)).abs()
        low_prev = (df['low'] - df['close'].shift(1)).abs()
        tr = pd.concat([high_low, high_prev, low_prev], axis=1).max(axis=1)
        atr = tr.rolling(window=period, min_periods=1).mean()
        return atr

    def _calc_kc(self, df: pd.DataFrame) -> pd.DataFrame:
        """計算 Keltner Channel 指標。
        - kc_middle: EMA(close, kc_len)
        - kc_upper / kc_lower: 中軌 ± kc_mult * ATR
        """
        df['kc_middle'] = self._ema(df['close'], self.kc_len)
        df['atr'] = self._atr(df, self.atr_len)
        df['kc_upper'] = df['kc_middle'] + self.kc_mult * df['atr']
        df['kc_lower'] = df['kc_middle'] - self.kc_mult * df['atr']
        return df

    def _calc_bb(self, df: pd.DataFrame) -> pd.DataFrame:
        """計算布林通道 (Bollinger Bands)。
        - bb_middle: SMA(close, bb_len)
        - bb_upper / bb_lower: 中軌 ± bb_std_mult * std(close)
        """
        df['bb_middle'] = df['close'].rolling(window=self.bb_len, min_periods=1).mean()
        std = df['close'].rolling(window=self.bb_len, min_periods=1).std()
        df['bb_std'] = std
        df['bb_upper'] = df['bb_middle'] + self.bb_std_mult * std
        df['bb_lower'] = df['bb_middle'] - self.bb_std_mult * std
        return df

    def _calc_squeeze(self, df: pd.DataFrame) -> pd.DataFrame:
        """判斷 BB 是否被 KC 完全包住。
        is_squeeze = (bb_upper < kc_upper) and (bb_lower > kc_lower)
        """
        is_sqz = (df['bb_upper'] < df['kc_upper']) & (df['bb_lower'] > df['kc_lower'])
        df['is_squeeze'] = is_sqz
        df['is_sqz'] = is_sqz
        return df

    def _calc_kc_slope(self, df: pd.DataFrame) -> pd.DataFrame:
        """計算 KC 中軌的斜率。
        使用最近 `squeeze_lookback` 根 K 棒的變動率：
        正值代表上行，負值代表下行，供多空各自的順勢門檻使用。
        """
        shift_series = df['kc_middle'].shift(self.squeeze_lookback)
        df['kc_slope'] = (df['kc_middle'] - shift_series) / shift_series.replace(0, np.nan)
        return df

    def _calc_can_trade(self, df: pd.DataFrame) -> pd.DataFrame:
        """擠壓時或 MA3 貼著中軌黏著時 (`ma_dist <= min_ma_dist`) 封鎖開倉，並計算過去 10 棒峰谷。"""
        df['ma3'] = df['close'].rolling(window=self.fast_ma_len, min_periods=1).mean()
        df['ma15'] = df['close'].rolling(window=self.slow_ma_len, min_periods=1).mean()
        df['peak'] = df['high'].rolling(window=self.pivot_lookback, min_periods=1).max()
        df['trough'] = df['low'].rolling(window=self.pivot_lookback, min_periods=1).min()
        df['valid_trough_rebound'] = df['close'] > (df['trough'] * (1.0 + self.min_pivot_rebound_pct))
        df['valid_peak_pullback'] = df['close'] < (df['peak'] * (1.0 - self.min_pivot_rebound_pct))

        safe_kc_mid = df['kc_middle'].replace(0, np.nan)
        df['ma_dist'] = (df['ma3'] - df['kc_middle']).abs() / safe_kc_mid
        df['can_trade'] = (~df['is_squeeze']) & (df['ma_dist'] > self.min_ma_dist)
        df['can_open'] = df['can_trade']
        return df

    # ---------------------------------------------------------------------
    # 主要公開方法
    # ---------------------------------------------------------------------
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """根據傳入的 OHLCV DataFrame 產生交易訊號。

        回傳的 DataFrame 會保留原始欄位，並額外加入指標與判斷欄位。
        `signal` 欄位說明：
        - "LONG"  : 符合多單順勢突破或峰谷金叉進場條件且 `can_trade` 為 True
        - "SHORT" : 符合空單順勢突破或峰谷死叉進場條件且 `can_trade` 為 True
        - None    : 其餘情況
        `position_signal` 欄位說明 (含部位狀態與移動止盈追蹤)：
        - 1  : 買多進場
        - -1 : 賣空進場
        - 2  : 平多出場
        - -2 : 平空出場
        - 0  : 無動作
        """
        required_cols = {'open', 'high', 'low', 'close'}
        if not required_cols.issubset(df.columns):
            missing = required_cols - set(df.columns)
            raise ValueError(f"輸入 DataFrame 缺少必要欄位: {missing}")

        # 1. 計算指標
        df = self._calc_kc(df.copy())
        df = self._calc_bb(df)
        df = self._calc_squeeze(df)
        df = self._calc_kc_slope(df)
        df = self._calc_can_trade(df)

        # 2. 多頭 can_buy / 空頭 can_sell 四合一核心條件：
        #    1. kc_slope > 0.0003 (或 < -0.0003)
        #    2. close > kc_middle (或 < kc_middle)
        #    3. ma_dist > 0.001
        #    4. ~is_squeeze
        kc_step_up = df['kc_middle'] > df['kc_middle'].shift(1)
        kc_step_down = df['kc_middle'] < df['kc_middle'].shift(1)
        df['can_buy'] = (
            (df['kc_slope'] > self.squeeze_min_slope)
            & kc_step_up
            & (df['close'] > df['kc_middle'])
            & (df['ma_dist'] > self.min_ma_dist)
            & (~df['is_squeeze'])
        )
        df['can_sell'] = (
            (df['kc_slope'] < -self.squeeze_min_slope)
            & kc_step_down
            & (df['close'] < df['kc_middle'])
            & (df['ma_dist'] > self.min_ma_dist)
            & (~df['is_squeeze'])
        )
        df['allow_long'] = (
            df['can_buy']
            & (df['ma3'] > df['kc_middle'])
            & df['valid_trough_rebound']
        )
        df['allow_short'] = (
            df['can_sell']
            & (df['ma3'] < df['kc_middle'])
            & df['valid_peak_pullback']
        )

        # 3. 順勢突破與峰谷確認交叉
        previous_close = df['close'].shift(1)
        previous_upper = df['kc_upper'].shift(1)
        previous_lower = df['kc_lower'].shift(1)
        breakout_upper = (previous_close <= previous_upper) & (df['close'] > df['kc_upper'])
        breakout_lower = (previous_close >= previous_lower) & (df['close'] < df['kc_lower'])

        raw_gold_cross = (df['ma3'].shift(1) <= df['ma15'].shift(1)) & (df['ma3'] > df['ma15'])
        raw_death_cross = (df['ma3'].shift(1) >= df['ma15'].shift(1)) & (df['ma3'] < df['ma15'])
        df['raw_gold_cross'] = raw_gold_cross
        df['raw_death_cross'] = raw_death_cross

        df['valid_trough_cross'] = (
            raw_gold_cross
            & df['can_buy']
            & df['valid_trough_rebound']
        )
        df['valid_peak_cross'] = (
            raw_death_cross
            & df['can_sell']
            & df['valid_peak_pullback']
        )

        df['raw_long'] = df['allow_long'] & (breakout_upper | df['valid_trough_cross'])
        df['raw_short'] = df['allow_short'] & (breakout_lower | df['valid_peak_cross'])

        # 4. 產生無狀態突破訊號
        df['signal'] = None
        df.loc[df['raw_long'], 'signal'] = 'LONG'
        df.loc[df['raw_short'], 'signal'] = 'SHORT'

        # 5. 結合部位與移動止盈產生完整進出場訊號 (position_signal)
        active_trailing_pct = (
            self.overbought_trailing_pct
            if self.overbought_trailing_pct is not None
            else (0.008 if self.trailing_stop_pct == 0.015 else self.trailing_stop_pct)
        )
        df['position_signal'] = 0
        df['exit_reason'] = None
        position = 0
        highest_price = 0.0
        lowest_price = 0.0
        reached_overbought = False
        reached_oversold = False

        for i in range(1, len(df)):
            current_open = float(df['open'].iloc[i])
            current_high = float(df['high'].iloc[i])
            current_low = float(df['low'].iloc[i])
            current_close = float(df['close'].iloc[i])
            prev_close = float(df['close'].iloc[i - 1])
            kc_mid = float(df['kc_middle'].iloc[i])
            kc_up = float(df['kc_upper'].iloc[i])
            kc_low = float(df['kc_lower'].iloc[i])
            kc_slope_val = float(df['kc_slope'].iloc[i]) if pd.notna(df['kc_slope'].iloc[i]) else 0.0
            ma_dist_val = float(df['ma_dist'].iloc[i]) if pd.notna(df['ma_dist'].iloc[i]) else 0.0

            if position == 0:
                if ma_dist_val <= self.min_ma_dist:
                    continue
                if bool(df['raw_long'].iloc[i]) and kc_slope_val > self.squeeze_min_slope and current_close > kc_mid:
                    df.loc[df.index[i], 'position_signal'] = 1
                    position = 1
                    highest_price = max(current_high, current_close)
                    reached_overbought = highest_price > kc_up
                elif bool(df['raw_short'].iloc[i]) and kc_slope_val < -self.squeeze_min_slope and current_close < kc_mid:
                    df.loc[df.index[i], 'position_signal'] = -1
                    position = -1
                    lowest_price = min(current_low, current_close)
                    reached_oversold = lowest_price < kc_low
            elif position == 1:
                bar_peak = max(current_high, current_close)
                highest_price = max(highest_price, bar_peak)
                if bar_peak > kc_up or highest_price > kc_up:
                    reached_overbought = True

                bar_body_drop_pct = (current_open - current_close) / current_open if current_open > 0 else 0.0
                bar_close_drop_pct = (prev_close - current_close) / prev_close if prev_close > 0 else 0.0
                is_waterfall_crash = (
                    (bar_body_drop_pct > self.waterfall_drop_pct)
                    or (bar_close_drop_pct > self.waterfall_drop_pct)
                    or (current_close < kc_low)
                )
                overbought_trailing_stop = highest_price * (1.0 - active_trailing_pct)
                is_overbought_pullback_exit = reached_overbought and (current_close < overbought_trailing_stop)
                is_midline_broken = current_close < kc_mid

                if is_waterfall_crash:
                    df.loc[df.index[i], 'position_signal'] = 2
                    df.loc[df.index[i], 'exit_reason'] = 'WATERFALL_EXIT'
                    position = 0
                    reached_overbought = False
                elif is_overbought_pullback_exit:
                    df.loc[df.index[i], 'position_signal'] = 2
                    df.loc[df.index[i], 'exit_reason'] = 'OVERBOUGHT_TRAILING_STOP'
                    position = 0
                    reached_overbought = False
                elif is_midline_broken:
                    df.loc[df.index[i], 'position_signal'] = 2
                    df.loc[df.index[i], 'exit_reason'] = 'MIDLINE_BREAK_EXIT'
                    position = 0
                    reached_overbought = False
            elif position == -1:
                bar_trough = min(current_low, current_close)
                lowest_price = min(lowest_price, bar_trough)
                if bar_trough < kc_low or lowest_price < kc_low:
                    reached_oversold = True

                bar_body_surge_pct = (current_close - current_open) / current_open if current_open > 0 else 0.0
                bar_close_surge_pct = (current_close - prev_close) / prev_close if prev_close > 0 else 0.0
                is_short_squeeze_spike = (
                    (bar_body_surge_pct > self.waterfall_drop_pct)
                    or (bar_close_surge_pct > self.waterfall_drop_pct)
                    or (current_close > kc_up)
                )
                oversold_trailing_stop = lowest_price * (1.0 + active_trailing_pct)
                is_oversold_rebound_exit = reached_oversold and (current_close > oversold_trailing_stop)
                is_midline_breached_up = current_close > kc_mid

                if is_short_squeeze_spike:
                    df.loc[df.index[i], 'position_signal'] = -2
                    df.loc[df.index[i], 'exit_reason'] = 'WATERFALL_SURGE_EXIT'
                    position = 0
                    reached_oversold = False
                elif is_oversold_rebound_exit:
                    df.loc[df.index[i], 'position_signal'] = -2
                    df.loc[df.index[i], 'exit_reason'] = 'OVERSOLD_TRAILING_STOP'
                    position = 0
                    reached_oversold = False
                elif is_midline_breached_up:
                    df.loc[df.index[i], 'position_signal'] = -2
                    df.loc[df.index[i], 'exit_reason'] = 'MIDLINE_BREAK_EXIT'
                    position = 0
                    reached_oversold = False

        # 清理暫時欄位
        df.drop(columns=['raw_long', 'raw_short'], inplace=True)

        return df

