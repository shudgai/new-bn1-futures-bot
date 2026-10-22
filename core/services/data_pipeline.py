"""
core/services/data_pipeline.py
==============================
三層流水線之【數據層 (Data Layer)】
職責：
- 僅負責抓取與計算最新 K 棒數據與指標。
- 回傳純數值物件 (MarketDataSnapshot)，完全不帶任何決策與業務邏輯。
"""
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np


@dataclass
class MarketDataSnapshot:
    symbol: str
    current_price: float
    curr_k: Dict[str, float]
    prev_k: Dict[str, float]
    ma5: float
    ma15: float
    ema_fast: float           # EMA 15
    ema_slow: float           # EMA 40
    ma_spread_pct: float      # |EMA15 - EMA40| / EMA40 * 100
    fast_slope: float         # EMA15 斜率
    slow_slope: float         # EMA40 斜率
    kc_upper: float
    kc_middle: float
    kc_lower: float
    kc_middle_slope: float
    atr: float
    peak_roe: float = 0.0
    min_price: float = 0.0
    max_price: float = 0.0
    macro_trend: str = "NEUTRAL"      # "BULLISH", "BEARISH", "NEUTRAL"
    macro_slope: float = 0.0          # EMA60 長週期斜率 (1小時級別波段)
    slow_slope_5: float = 0.0         # EMA40 過去 5 根斜率
    raw_df: Optional[pd.DataFrame] = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "current_price": self.current_price,
            "curr_k": self.curr_k,
            "prev_k": self.prev_k,
            "ma5": self.ma5,
            "ma15": self.ma15,
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "ma_spread_pct": self.ma_spread_pct,
            "fast_slope": self.fast_slope,
            "slow_slope": self.slow_slope,
            "kc_upper": self.kc_upper,
            "kc_middle": self.kc_middle,
            "kc_lower": self.kc_lower,
            "kc_middle_slope": self.kc_middle_slope,
            "atr": self.atr,
            "peak_roe": self.peak_roe,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "macro_trend": self.macro_trend,
            "macro_slope": self.macro_slope,
            "slow_slope_5": self.slow_slope_5,
        }


class DataPipeline:
    @staticmethod
    def calculate_snapshot(
        symbol: str,
        df: pd.DataFrame,
        current_price: Optional[float] = None,
        position: Optional[Dict[str, Any]] = None,
    ) -> Optional[MarketDataSnapshot]:
        """
        從 K 線 DataFrame 計算出標準純數值 Snapshot。
        保證不帶任何業務判斷或下單邏輯。
        """
        if df is None or len(df) < 20:
            return None

        # 計算必要均線與指標 (純數學)
        df_calc = df.copy()
        close = df_calc["close"]
        high = df_calc["high"]
        low = df_calc["low"]

        # 均線 MA5, MA15
        ma5_series = close.rolling(window=5).mean()
        ma15_series = close.rolling(window=15).mean()

        # EMA 15 (快線) 與 EMA 40 (慢線)
        ema_fast_series = close.ewm(span=15, adjust=False).mean()
        ema_slow_series = close.ewm(span=40, adjust=False).mean()

        # 均線間距比例 (%)
        ma_spread_series = ((ema_fast_series - ema_slow_series).abs() / ema_slow_series) * 100.0

        # ATR 14
        tr = np.maximum(
            high - low,
            np.maximum(
                (high - close.shift(1)).abs(),
                (low - close.shift(1)).abs(),
            ),
        )
        atr_series = tr.rolling(window=14).mean()

        # KC 通道 (basis=EMA20, mult=1.5)
        kc_basis = close.ewm(span=20, adjust=False).mean()
        atr_safe = atr_series.bfill().fillna(close * 0.01)
        kc_upper_series = kc_basis + (atr_safe * 1.5)
        kc_lower_series = kc_basis - (atr_safe * 1.5)

        # 將指標列注入 df_calc 以供下游閘門驗證
        df_calc["ma5"] = ma5_series
        df_calc["ma15"] = ma15_series
        df_calc["ema_fast"] = ema_fast_series
        df_calc["ema_slow"] = ema_slow_series
        df_calc["atr"] = atr_safe
        df_calc["kc_middle"] = kc_basis
        df_calc["kc_upper"] = kc_upper_series
        df_calc["kc_lower"] = kc_lower_series
        if hasattr(df_calc, "attrs"):
            df_calc.attrs["entry_finality_verified"] = True
            df_calc.attrs.setdefault("timeframe_ms", 60_000)

        # 提取最新與前一根數值
        last_idx = df_calc.index[-1]
        prev_idx = df_calc.index[-2]

        last_row = df_calc.loc[last_idx]
        prev_row = df_calc.loc[prev_idx]

        curr_k = {
            "open": float(last_row.get("open", 0.0)),
            "high": float(last_row.get("high", 0.0)),
            "low": float(last_row.get("low", 0.0)),
            "close": float(last_row.get("close", 0.0)),
            "volume": float(last_row.get("volume", 0.0)),
        }
        prev_k = {
            "open": float(prev_row.get("open", 0.0)),
            "high": float(prev_row.get("high", 0.0)),
            "low": float(prev_row.get("low", 0.0)),
            "close": float(prev_row.get("close", 0.0)),
            "volume": float(prev_row.get("volume", 0.0)),
        }

        ma5 = float(ma5_series.iloc[-1]) if not pd.isna(ma5_series.iloc[-1]) else curr_k["close"]
        prev_ma5 = float(ma5_series.iloc[-2]) if len(ma5_series) > 1 and not pd.isna(ma5_series.iloc[-2]) else ma5
        ma15 = float(ma15_series.iloc[-1]) if not pd.isna(ma15_series.iloc[-1]) else curr_k["close"]
        prev_ma15 = float(ma15_series.iloc[-2]) if len(ma15_series) > 1 and not pd.isna(ma15_series.iloc[-2]) else ma15

        ema_fast = float(ema_fast_series.iloc[-1]) if not pd.isna(ema_fast_series.iloc[-1]) else curr_k["close"]
        ema_slow = float(ema_slow_series.iloc[-1]) if not pd.isna(ema_slow_series.iloc[-1]) else curr_k["close"]

        fast_prev3 = float(ema_fast_series.iloc[-4]) if len(ema_fast_series) >= 4 else ema_fast
        slow_prev3 = float(ema_slow_series.iloc[-4]) if len(ema_slow_series) >= 4 else ema_slow
        fast_slope = ema_fast - fast_prev3
        slow_slope = ema_slow - slow_prev3

        ma_spread_pct = float(ma_spread_series.iloc[-1]) if not pd.isna(ma_spread_series.iloc[-1]) else 0.0

        kc_up = float(kc_upper_series.iloc[-1]) if not pd.isna(kc_upper_series.iloc[-1]) else curr_k["close"]
        kc_mid = float(kc_basis.iloc[-1]) if not pd.isna(kc_basis.iloc[-1]) else curr_k["close"]
        kc_low = float(kc_lower_series.iloc[-1]) if not pd.isna(kc_lower_series.iloc[-1]) else curr_k["close"]

        prev_kc_mid = float(kc_basis.iloc[-2]) if len(kc_basis) > 1 and not pd.isna(kc_basis.iloc[-2]) else kc_mid
        prev_kc_mid_5 = float(kc_basis.iloc[-6]) if len(kc_basis) >= 6 and not pd.isna(kc_basis.iloc[-6]) else prev_kc_mid
        n_kc_step = 5 if len(kc_basis) >= 6 else 1
        kc_middle_slope = (kc_mid - prev_kc_mid_5) / n_kc_step

        curr_k["ma5"] = ma5
        curr_k["ma15"] = ma15
        curr_k["kc_middle"] = kc_mid
        prev_k["ma5"] = prev_ma5
        prev_k["ma15"] = prev_ma15
        prev_k["kc_middle"] = prev_kc_mid

        atr_val = float(atr_safe.iloc[-1]) if not pd.isna(atr_safe.iloc[-1]) and atr_safe.iloc[-1] > 0 else (curr_k["close"] * 0.01)

        quote = float(current_price) if current_price is not None and current_price > 0 else curr_k["close"]

        # 持倉歷史極值與 peak_roe
        peak_roe = 0.0
        min_price = float(df_calc["low"].iloc[-20:].min())
        max_price = float(df_calc["high"].iloc[-20:].max())

        if position:
            entry_p = float(position.get("entry_price") or 0.0)
            side = str(position.get("side", "")).upper()
            leverage = float(position.get("leverage", 5.0))
            if entry_p > 0:
                cur_move = ((quote - entry_p) / entry_p) if side == "LONG" else ((entry_p - quote) / entry_p)
                cur_roe = cur_move * leverage
                stored_peak_roe = float(position.get("peak_roe", 0.0) or 0.0)
                stored_peak_move = float(position.get("peak_price_move", 0.0) or position.get("highest_pnl_pct", 0.0) or 0.0)
                if stored_peak_roe <= 0 and stored_peak_move > 0:
                    stored_peak_roe = stored_peak_move * leverage
                peak_roe = max(stored_peak_roe, cur_roe)
                position["peak_roe"] = peak_roe
                position["unrealized_roe"] = cur_roe
                position["peak_price_move"] = peak_roe / leverage

        # ─── 大週期宏觀趨勢判定 (HTF Macro Trend) ───
        # 使用長週期 EMA60 (1 小時級別波段) 與過去 10 根長均線走向，杜絕單根 1m 毛刺騙線
        ema60_series = close.ewm(span=60, adjust=False).mean()
        ema60 = float(ema60_series.iloc[-1]) if not pd.isna(ema60_series.iloc[-1]) else curr_k["close"]
        prev_ema60_10 = float(ema60_series.iloc[-11]) if len(ema60_series) >= 11 else ema60
        macro_slope_10 = ema60 - prev_ema60_10

        prev_ema_slow_10 = float(ema_slow_series.iloc[-11]) if len(ema_slow_series) >= 11 else ema_slow
        slow_slope_10 = ema_slow - prev_ema_slow_10
        macro_slope = macro_slope_10
        slow_slope_5 = slow_slope_10

        is_htf_bull = (ema_fast >= ema_slow and (macro_slope_10 >= 0 or slow_slope_10 >= 0 or kc_middle_slope >= 0)) or (quote > ema60 and macro_slope_10 > 0)
        is_htf_bear = (ema_fast <= ema_slow and (macro_slope_10 <= 0 or slow_slope_10 <= 0 or kc_middle_slope <= 0)) or (quote < ema60 and macro_slope_10 < 0)

        if is_htf_bull and not is_htf_bear:
            macro_trend = "BULLISH"
        elif is_htf_bear and not is_htf_bull:
            macro_trend = "BEARISH"
        else:
            macro_trend = "NEUTRAL"

        return MarketDataSnapshot(
            symbol=symbol,
            current_price=quote,
            curr_k=curr_k,
            prev_k=prev_k,
            ma5=ma5,
            ma15=ma15,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            ma_spread_pct=ma_spread_pct,
            fast_slope=fast_slope,
            slow_slope=slow_slope,
            kc_upper=kc_up,
            kc_middle=kc_mid,
            kc_lower=kc_low,
            kc_middle_slope=kc_middle_slope,
            atr=atr_val,
            peak_roe=peak_roe,
            min_price=min_price,
            max_price=max_price,
            macro_trend=macro_trend,
            macro_slope=macro_slope,
            slow_slope_5=slow_slope_5,
            raw_df=df_calc,
        )
