"""
檔案位置: core/services/strategies/dual_tf_ema_kc_strategy.py
架構說明:
1. 大週期 (15m): EMA15/EMA40 趨勢與價格結構多空裁決官。
2. 小週期 (1m): 回踩/反彈至 EMA15/EMA40/KC 中軌 + 結構突破進場。
3. 風險管理: 0.5% 權益動態倉位換算 (含手續費與滑價緩衝)。
4. 分批止盈: +1R 平 30%、+2R 平 30%、40% 尾倉結構移動停利 (左右各 2 根已收線確認)。
5. 嚴禁事項: 1m EMA 反向交叉僅作警告，絕不提早平倉！
"""

import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


class DualTimeframeEmaKcStrategy:

    def __init__(
        self,
        risk_per_trade_pct: float = 0.005,  # 單筆最大承受風險 0.5% 帳戶權益
        daily_max_loss_pct: float = 0.02,   # 每日最大虧損上限 2.0%
        taker_fee_rate: float = 0.0005,     # Taker 單邊手續費 0.05%
        slippage_buffer_pct: float = 0.0005,# 預估滑價緩衝 0.05%
        kc_length: int = 20,                # KC 通道週期
        kc_mult: float = 1.5,               # KC 通道 ATR 倍數
        atr_period: int = 14,               # ATR 週期
        left_bars: int = 2,                 # 分形確認：左側已收線 K 棒數
        right_bars: int = 2,                # 分形確認：右側已收線 K 棒數
    ):
        self.risk_per_trade_pct = risk_per_trade_pct
        self.daily_max_loss_pct = daily_max_loss_pct
        self.taker_fee_rate = taker_fee_rate
        self.slippage_buffer_pct = slippage_buffer_pct
        self.total_cost_pct = (taker_fee_rate * 2) + (slippage_buffer_pct * 2)
        self.kc_length = kc_length
        self.kc_mult = kc_mult
        self.atr_period = atr_period
        self.left_bars = left_bars
        self.right_bars = right_bars

    @staticmethod
    def compute_indicators(df: pd.DataFrame, kc_length: int = 20, kc_mult: float = 1.5, atr_len: int = 14) -> pd.DataFrame:
        df = df.copy()
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # EMA15 & EMA40
        df["ema15"] = close.ewm(span=15, adjust=False).mean()
        df["ema40"] = close.ewm(span=40, adjust=False).mean()
        df["ema15_slope"] = df["ema15"].diff(2)
        df["ema40_slope"] = df["ema40"].diff(2)

        # ATR 14
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr"] = tr.rolling(atr_len).mean()

        # KC 通道 (中軌 EMA20, 上下軌 +/- kc_mult * ATR)
        df["kc_middle"] = close.ewm(span=kc_length, adjust=False).mean()
        df["kc_upper"] = df["kc_middle"] + (kc_mult * df["atr"])
        df["kc_lower"] = df["kc_middle"] - (kc_mult * df["atr"])
        df["kc_middle_slope"] = df["kc_middle"].diff(2)

        return df

    @staticmethod
    def precompute_confirmed_pivots(highs: np.ndarray, lows: np.ndarray, left: int = 2, right: int = 2) -> Tuple[np.ndarray, np.ndarray]:
        """
        向量化/前向滾動計算每個 Bar 截止時已確認的最近分形點。
        在索引 i 處，中心點 c = i - right 剛好完成右側 right 根收盤確認。
        因此在 i 處可獲知並確認中心點 c 是否為局部高/低點。
        返回值: pivot_highs_confirmed, pivot_lows_confirmed (長度為 N 的陣列，儲存截至 i 棒確認的最新點位)
        保證在任何 i 處，絕不讀取 > i 的資料！
        """
        n = len(highs)
        p_highs = np.full(n, np.nan, dtype=np.float64)
        p_lows = np.full(n, np.nan, dtype=np.float64)

        latest_high = np.nan
        latest_low = np.nan

        for i in range(left + right, n):
            c = i - right  # 正在被確認的中心棒索引

            # 檢驗中心棒 c 是否為局部高點
            is_peak = True
            c_high = highs[c]
            for l_idx in range(c - left, c):
                if highs[l_idx] >= c_high:
                    is_peak = False
                    break
            if is_peak:
                for r_idx in range(c + 1, c + right + 1):
                    if highs[r_idx] >= c_high:
                        is_peak = False
                        break
            if is_peak:
                latest_high = c_high

            # 檢驗中心棒 c 是否為局部低點
            is_valley = True
            c_low = lows[c]
            for l_idx in range(c - left, c):
                if lows[l_idx] <= c_low:
                    is_valley = False
                    break
            if is_valley:
                for r_idx in range(c + 1, c + right + 1):
                    if lows[r_idx] <= c_low:
                        is_valley = False
                        break
            if is_valley:
                latest_low = c_low

            p_highs[i] = latest_high
            p_lows[i] = latest_low

        return p_highs, p_lows

    def evaluate_15m_macro_trend(self, row_15m: Any) -> str:
        """
        大週期趨勢評估 (傳入最新已收盤 15m row 或 Series):
        - BULLISH: EMA15 > EMA40，兩線均線向上，價格在 KC 中軌上方。
        - BEARISH: EMA15 < EMA40，兩線均線向下，價格在 KC 中軌下方。
        - NEUTRAL: 均線糾纏、走平或方向矛盾。
        """
        ema15 = float(row_15m["ema15"])
        ema40 = float(row_15m["ema40"])
        slope15 = float(row_15m["ema15_slope"])
        slope40 = float(row_15m["ema40_slope"])
        close_15 = float(row_15m["close"])
        mid_15 = float(row_15m["kc_middle"])
        atr_15 = float(row_15m["atr"]) if not pd.isna(row_15m["atr"]) else 0.0

        ema_gap = abs(ema15 - ema40)
        min_gap = 0.20 * atr_15

        if ema_gap < min_gap:
            return "NEUTRAL"

        if ema15 > ema40 and slope15 > 0 and slope40 >= -1e-6 and close_15 >= mid_15:
            return "BULLISH"
        elif ema15 < ema40 and slope15 < 0 and slope40 <= 1e-6 and close_15 <= mid_15:
            return "BEARISH"

        return "NEUTRAL"

    def evaluate_1m_entry(
        self,
        last_k: Any,
        prev_k: Any,
        macro_trend: str,
        account_equity: float,
        pivot_high: Optional[float],
        pivot_low: Optional[float],
    ) -> Optional[Dict[str, Any]]:
        if macro_trend not in ("BULLISH", "BEARISH"):
            return None

        curr_p = float(last_k["close"])
        atr_1m = float(last_k["atr"]) if not pd.isna(last_k["atr"]) else 0.0
        if atr_1m <= 0:
            return None

        # 做多
        if macro_trend == "BULLISH":
            support_zone_high = max(last_k["ema15"], last_k["kc_middle"]) * 1.002
            support_zone_low = min(last_k["ema40"], last_k["kc_middle"]) * 0.998
            touched_support = (last_k["low"] <= support_zone_high and last_k["high"] >= support_zone_low) or (prev_k["low"] <= support_zone_high)
            reclaimed = (last_k["close"] > last_k["open"]) and (last_k["close"] >= last_k["ema15"])

            # 突破確認：突破已確認的分形高點，或突破前棒高點
            has_breakout = (pivot_high is not None and not np.isnan(pivot_high) and last_k["close"] > pivot_high) or (last_k["close"] > prev_k["high"])

            if touched_support and reclaimed and has_breakout:
                base_low = pivot_low if (pivot_low is not None and not np.isnan(pivot_low)) else float(min(last_k["low"], prev_k["low"]))
                struct_sl = base_low - (0.5 * atr_1m)
                risk_dist = curr_p - struct_sl

                if risk_dist > 0:
                    risk_amount = account_equity * self.risk_per_trade_pct
                    eff_risk_per_unit = risk_dist + (curr_p * self.total_cost_pct)
                    qty = risk_amount / eff_risk_per_unit
                    return {
                        "side": "LONG",
                        "entry_price": curr_p,
                        "stop_loss": struct_sl,
                        "r_distance": risk_dist,
                        "qty": qty,
                        "tp1_price": curr_p + (1.0 * risk_dist),
                        "tp2_price": curr_p + (2.0 * risk_dist),
                        "reason": "15m多頭 + 1m回踩守穩EMA/KC + 結構轉強突破",
                    }

        # 做空
        elif macro_trend == "BEARISH":
            res_zone_low = min(last_k["ema15"], last_k["kc_middle"]) * 0.998
            res_zone_high = max(last_k["ema40"], last_k["kc_middle"]) * 1.002
            touched_res = (last_k["high"] >= res_zone_low and last_k["low"] <= res_zone_high) or (prev_k["high"] >= res_zone_low)
            weakened = (last_k["close"] < last_k["open"]) and (last_k["close"] <= last_k["ema15"])

            # 跌破確認：跌破已確認的分形低點，或跌破前棒低點
            has_breakdown = (pivot_low is not None and not np.isnan(pivot_low) and last_k["close"] < pivot_low) or (last_k["close"] < prev_k["low"])

            if touched_res and weakened and has_breakdown:
                base_high = pivot_high if (pivot_high is not None and not np.isnan(pivot_high)) else float(max(last_k["high"], prev_k["high"]))
                struct_sl = base_high + (0.5 * atr_1m)
                risk_dist = struct_sl - curr_p

                if risk_dist > 0:
                    risk_amount = account_equity * self.risk_per_trade_pct
                    eff_risk_per_unit = risk_dist + (curr_p * self.total_cost_pct)
                    qty = risk_amount / eff_risk_per_unit
                    return {
                        "side": "SHORT",
                        "entry_price": curr_p,
                        "stop_loss": struct_sl,
                        "r_distance": risk_dist,
                        "qty": qty,
                        "tp1_price": curr_p - (1.0 * risk_dist),
                        "tp2_price": curr_p - (2.0 * risk_dist),
                        "reason": "15m空頭 + 1m反抽受阻EMA/KC + 結構轉弱破位",
                    }

        return None
