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
from core.services.technical_indicators import compute_keltner_channels


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
    bandwidth: float = 0.0            # 通道帶寬 (kc_upper - kc_lower) / kc_middle
    ema5: float = 0.0                 # EMA 5
    ema13: float = 0.0                # EMA 13
    ma3: float = 0.0                  # MA 3 短期動能線
    prev_ma3: float = 0.0             # 前一根 MA 3
    ma3_slope: float = 0.0            # MA 3 斜率 (當前 - 前一根)
    kc_trend: str = "NEUTRAL"         # KC slope plus closed-bar price location
    raw_df: Optional[pd.DataFrame] = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "current_price": self.current_price,
            "curr_k": self.curr_k,
            "prev_k": self.prev_k,
            "ma3": self.ma3,
            "prev_ma3": self.prev_ma3,
            "ma3_slope": self.ma3_slope,
            "ma5": self.ma5,
            "ma15": self.ma15,
            "ema5": self.ema5,
            "ema13": self.ema13,
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "ma_spread_pct": self.ma_spread_pct,
            "fast_slope": self.fast_slope,
            "slow_slope": self.slow_slope,
            "kc_upper": self.kc_upper,
            "kc_middle": self.kc_middle,
            "kc_lower": self.kc_lower,
            "kc_middle_slope": self.kc_middle_slope,
            "kc_trend": self.kc_trend,
            "atr": self.atr,
            "peak_roe": self.peak_roe,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "macro_trend": self.macro_trend,
            "macro_slope": self.macro_slope,
            "slow_slope_5": self.slow_slope_5,
            "bandwidth": self.bandwidth,
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

        # 均線 MA3, MA5, MA15
        ma3_series = close.rolling(window=3).mean()
        ma5_series = close.rolling(window=5).mean()
        ma15_series = close.rolling(window=15).mean()

        # EMA5 (極限動能線) 與 EMA13 (防守線)
        ema5_series = close.ewm(span=5, adjust=False).mean()
        ema13_series = close.ewm(span=13, adjust=False).mean()

        # EMA 15 (快線) 與 EMA 40 (慢線)
        ema_fast_series = close.ewm(span=15, adjust=False).mean()
        ema_slow_series = close.ewm(span=40, adjust=False).mean()

        # 均線間距比例 (%)
        ma_spread_series = ((ema_fast_series - ema_slow_series).abs() / ema_slow_series) * 100.0

        # KC 通道與 ATR14 計算 (統一標準單一來源，杜絕前視偏差)
        kc_res = compute_keltner_channels(
            df_calc,
            ema_period=20,
            atr_period=14,
            multiplier=1.5,
        )
        atr_series = kc_res["atr"]
        kc_basis = kc_res["kc_middle"]
        kc_upper_series = kc_res["kc_upper"]
        kc_lower_series = kc_res["kc_lower"]

        # 暖機檢查：嚴禁用 bfill() 將未來資料向前回填
        # 若最新 K 棒或前一根 K 棒的 ATR 尚未就緒 (NaN 或 <= 0)，則視為指標尚未準備好
        if (
            len(atr_series) < 14
            or pd.isna(atr_series.iloc[-1])
            or pd.isna(atr_series.iloc[-2])
            or float(atr_series.iloc[-1]) <= 0
        ):
            return None

        # 將指標列注入 df_calc 以供下游閘門驗證 (前 13 根保持 NaN，杜絕前視偏差)
        df_calc["ma3"] = ma3_series
        df_calc["ma5"] = ma5_series
        df_calc["ma15"] = ma15_series
        df_calc["ema5"] = ema5_series
        df_calc["ema13"] = ema13_series
        df_calc["ema_fast"] = ema_fast_series
        df_calc["ema_slow"] = ema_slow_series
        df_calc["atr"] = atr_series
        df_calc["kc_middle"] = kc_basis
        df_calc["kc_upper"] = kc_upper_series
        df_calc["kc_lower"] = kc_lower_series
        df_calc["bandwidth"] = kc_res["bandwidth"]
        df_calc["ma_slow"] = ema_slow_series
        df_calc["ma_slow_slope"] = ema_slow_series - ema_slow_series.shift(2)
        df_calc["body_size"] = (df_calc["close"] - df_calc["open"]).abs()
        df_calc["upper_band"] = kc_upper_series
        df_calc["lower_band"] = kc_lower_series
        df_calc["middle_band"] = kc_basis
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

        ma3 = float(ma3_series.iloc[-1]) if not pd.isna(ma3_series.iloc[-1]) else curr_k["close"]
        prev_ma3 = float(ma3_series.iloc[-2]) if len(ma3_series) > 1 and not pd.isna(ma3_series.iloc[-2]) else ma3
        ma3_slope = ma3 - prev_ma3

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

        from core.services.candle_data import closed_entry_candles
        closed_for_slope = closed_entry_candles(df_calc)
        if closed_for_slope.empty:
            return None
        closed_positions = [int(df_calc.index.get_loc(index)) for index in closed_for_slope.index]
        closed_latest_pos = closed_positions[-1]
        closed_prior_pos = closed_positions[-2] if len(closed_positions) >= 2 else closed_latest_pos
        closed_slope_base_pos = closed_positions[-3] if len(closed_positions) >= 3 else closed_prior_pos
        prev_kc_mid = float(kc_basis.iloc[closed_prior_pos])
        closed_kc_mid = float(kc_basis.iloc[closed_latest_pos])
        closed_slope_base = float(kc_basis.iloc[closed_slope_base_pos])
        n_kc_step = max(1, len(closed_positions[-3:]) - 1)
        # Short 3-closed-bar (two-step) slope keeps 1m reversal state responsive.
        kc_middle_slope = (closed_kc_mid - closed_slope_base) / n_kc_step
        closed_price = float(df_calc.iloc[closed_latest_pos]["close"])
        kc_trend = (
            "BULLISH" if kc_middle_slope > 0 and closed_price >= closed_kc_mid
            else "BEARISH" if kc_middle_slope < 0 and closed_price <= closed_kc_mid
            else "NEUTRAL"
        )

        ema5_val = float(ema5_series.iloc[-1]) if not pd.isna(ema5_series.iloc[-1]) else curr_k["close"]
        ema13_val = float(ema13_series.iloc[-1]) if not pd.isna(ema13_series.iloc[-1]) else curr_k["close"]
        prev_ema5 = float(ema5_series.iloc[-2]) if len(ema5_series) > 1 and not pd.isna(ema5_series.iloc[-2]) else ema5_val
        prev_ema13 = float(ema13_series.iloc[-2]) if len(ema13_series) > 1 and not pd.isna(ema13_series.iloc[-2]) else ema13_val

        curr_k["ma3"] = ma3
        curr_k["ma3_slope"] = ma3_slope
        curr_k["ma5"] = ma5
        curr_k["ma15"] = ma15
        curr_k["ema5"] = ema5_val
        curr_k["ema13"] = ema13_val
        curr_k["kc_middle"] = kc_mid
        prev_k["ma3"] = prev_ma3
        prev_k["ma3_slope"] = ma3_slope
        prev_k["ma5"] = prev_ma5
        prev_k["ma15"] = prev_ma15
        prev_k["ema5"] = prev_ema5
        prev_k["ema13"] = prev_ema13
        prev_k["kc_middle"] = prev_kc_mid

        atr_val = float(atr_series.iloc[-1])

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

        # KC labels require both slope and price location; a lagging middle
        # line cannot keep its old directional label after price crosses it.
        kc_trend_up = kc_trend == "BULLISH"
        kc_trend_down = kc_trend == "BEARISH"
        is_htf_bull = (ema_fast >= ema_slow and (macro_slope_10 >= 0 or slow_slope_10 >= 0 or kc_trend_up)) or (quote > ema60 and macro_slope_10 > 0)
        is_htf_bear = (ema_fast <= ema_slow and (macro_slope_10 <= 0 or slow_slope_10 <= 0 or kc_trend_down)) or (quote < ema60 and macro_slope_10 < 0)

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
            ma3=ma3,
            prev_ma3=prev_ma3,
            ma3_slope=ma3_slope,
            kc_trend=kc_trend,
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
            bandwidth=((kc_up - kc_low) / kc_mid if kc_mid > 0 else 0.0),
            ema5=ema5_val,
            ema13=ema13_val,
            raw_df=df_calc,
        )
