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
    prev_ma15: float = 0.0            # 前一根 MA 15
    ma3_slope: float = 0.0            # MA 3 斜率 (當前 - 前一根)
    kc_trend: str = "NEUTRAL"         # Latest two completed KC basis values only
    ema200: float = 0.0               # EMA 200 大趨勢基底
    volume_ma20: float = 0.0          # 成交量 20 期均量
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
            "ema200": self.ema200,
            "volume_ma20": self.volume_ma20,
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

        from core.services.candle_data import closed_entry_candles, confirmed_kc_midline_slope

        # 1. 建立通過收線驗證與完整性檢查的 closed_df (統一收線基準，杜絕 live bar 污染)
        closed_df = closed_entry_candles(df)
        is_fallback_for_position = False
        if closed_df is None or len(closed_df) < 20:
            # 若為無持倉的新進場評估，收線驗證失敗必須拒絕新進場 Snapshot (Fail-Closed)
            if not position:
                return None
            # 若為既有持倉：安全隔離，保護既有持倉之平倉與止盈止損評估，相容無標記之回測歷史數據
            # 但明確嚴格禁止授予 entry_finality_verified！
            if "timestamp" in df.columns:
                stamps = pd.to_numeric(df["timestamp"], errors="coerce")
                if stamps.isna().any() or not stamps.is_monotonic_increasing or stamps.duplicated().any():
                    return None
            closed_df = df.copy()
            if len(closed_df) < 20:
                return None
            is_fallback_for_position = True

        # 計算必要均線與指標 (純數學，嚴格只在 closed_df 上運算)
        close = closed_df["close"]
        high = closed_df["high"]
        low = closed_df["low"]

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

        # EMA 200 大趨勢基準線
        ema200_series = close.ewm(span=200, adjust=False).mean() if len(close) >= 5 else close

        # 成交量 MA 20 均量線
        vol_col = closed_df["volume"] if "volume" in closed_df.columns else pd.Series(0.0, index=closed_df.index)
        volume_ma20_series = vol_col.rolling(window=20, min_periods=1).mean()

        # 均線間距比例 (%)
        ma_spread_series = ((ema_fast_series - ema_slow_series).abs() / ema_slow_series) * 100.0

        # KC 通道與 ATR14 計算 (統一標準單一來源，杜絕前視偏差與 live bar 污染)
        kc_res = compute_keltner_channels(
            closed_df,
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

        # 將指標列注入 closed_df_with_ind 以供下游閘門驗證 (前 13 根保持 NaN，杜絕前視偏差)
        closed_df_with_ind = closed_df.copy()
        closed_df_with_ind["ma3"] = ma3_series
        closed_df_with_ind["ma5"] = ma5_series
        closed_df_with_ind["ma15"] = ma15_series
        closed_df_with_ind["ema5"] = ema5_series
        closed_df_with_ind["ema13"] = ema13_series
        closed_df_with_ind["ema_fast"] = ema_fast_series
        closed_df_with_ind["ema_slow"] = ema_slow_series
        closed_df_with_ind["ema200"] = ema200_series
        closed_df_with_ind["volume_ma20"] = volume_ma20_series
        closed_df_with_ind["atr"] = atr_series
        closed_df_with_ind["kc_middle"] = kc_basis
        closed_df_with_ind["kc_upper"] = kc_upper_series
        closed_df_with_ind["kc_lower"] = kc_lower_series
        closed_df_with_ind["bandwidth"] = kc_res["bandwidth"]
        closed_df_with_ind["ma_slow"] = ema_slow_series
        closed_df_with_ind["ma_slow_slope"] = ema_slow_series - ema_slow_series.shift(2)
        closed_df_with_ind["body_size"] = (closed_df_with_ind["close"] - closed_df_with_ind["open"]).abs()
        closed_df_with_ind["upper_band"] = kc_upper_series
        closed_df_with_ind["lower_band"] = kc_lower_series
        closed_df_with_ind["middle_band"] = kc_basis

        # 提取最新與前一根有效收線 K 棒 (嚴格保留 is_closed=True)
        last_row = closed_df.iloc[-1]
        prev_row = closed_df.iloc[-2]

        curr_k = {
            "open": float(last_row.get("open", 0.0)),
            "high": float(last_row.get("high", 0.0)),
            "low": float(last_row.get("low", 0.0)),
            "close": float(last_row.get("close", 0.0)),
            "volume": float(last_row.get("volume", 0.0)),
            "is_closed": True,
        }
        prev_k = {
            "open": float(prev_row.get("open", 0.0)),
            "high": float(prev_row.get("high", 0.0)),
            "low": float(prev_row.get("low", 0.0)),
            "close": float(prev_row.get("close", 0.0)),
            "volume": float(prev_row.get("volume", 0.0)),
            "is_closed": True,
        }
        if "timestamp" in last_row:
            curr_k["timestamp"] = int(last_row["timestamp"])
        if "timestamp" in prev_row:
            prev_k["timestamp"] = int(prev_row["timestamp"])

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

        prev_kc_mid = float(kc_basis.iloc[-2]) if len(kc_basis) >= 2 else kc_mid
        confirmed_slope = confirmed_kc_midline_slope(closed_df_with_ind)
        kc_middle_slope = confirmed_slope if confirmed_slope is not None else 0.0
        kc_trend = (
            "BULLISH" if kc_middle_slope > 0
            else "BEARISH" if kc_middle_slope < 0 else "NEUTRAL"
        )
        closed_price = curr_k["close"]

        # ─── 拉遠視角 (Zoomed-Out 10~60 根 K 棒) 結構與趨勢審查 ───
        kc_base_10_pos = -11 if len(kc_basis) >= 11 else 0
        kc_slope_10 = (kc_mid - float(kc_basis.iloc[kc_base_10_pos])) / max(1, len(kc_basis[-11:]) - 1)
        ema60_early_series = close.ewm(span=60, adjust=False).mean()
        ema60_early = float(ema60_early_series.iloc[-1]) if not pd.isna(ema60_early_series.iloc[-1]) else closed_price
        prev_ema60_early = float(ema60_early_series.iloc[-11]) if len(ema60_early_series) >= 11 else ema60_early
        ema60_slope_10 = ema60_early - prev_ema60_early

        # 若拉遠看 EMA60 向下且價格或慢線位於 EMA60 之下（或 10 根 KC 中軌斜率向下），屬於空頭結構反彈，絕不可標記為 BULLISH！
        zoomed_out_bearish = (ema60_slope_10 < 0 and (closed_price < ema60_early or ema_slow < ema60_early)) or (kc_slope_10 < 0 and closed_price < ema60_early)
        zoomed_out_bullish = (ema60_slope_10 > 0 and (closed_price > ema60_early or ema_slow > ema60_early)) or (kc_slope_10 > 0 and closed_price > ema60_early)

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
        curr_k["kc_upper"] = kc_up
        curr_k["kc_lower"] = kc_low
        prev_k["ma3"] = prev_ma3
        prev_k["ma3_slope"] = ma3_slope
        prev_k["ma5"] = prev_ma5
        prev_k["ma15"] = prev_ma15
        prev_k["ema5"] = prev_ema5
        prev_k["ema13"] = prev_ema13
        prev_k["kc_middle"] = prev_kc_mid
        prev_k["kc_upper"] = float(kc_upper_series.iloc[-2]) if len(kc_upper_series) > 1 and not pd.isna(kc_upper_series.iloc[-2]) else kc_up
        prev_k["kc_lower"] = float(kc_lower_series.iloc[-2]) if len(kc_lower_series) > 1 and not pd.isna(kc_lower_series.iloc[-2]) else kc_low

        ema200_val = float(ema200_series.iloc[-1]) if not pd.isna(ema200_series.iloc[-1]) else curr_k["close"]
        vol_ma20_val = float(volume_ma20_series.iloc[-1]) if not pd.isna(volume_ma20_series.iloc[-1]) else 0.0
        curr_k["ema200"] = ema200_val
        curr_k["volume_ma20"] = vol_ma20_val
        prev_k["ema200"] = float(ema200_series.iloc[-2]) if len(ema200_series) > 1 and not pd.isna(ema200_series.iloc[-2]) else ema200_val
        prev_k["volume_ma20"] = float(volume_ma20_series.iloc[-2]) if len(volume_ma20_series) > 1 and not pd.isna(volume_ma20_series.iloc[-2]) else vol_ma20_val

        atr_val = float(atr_series.iloc[-1])

        # 即時報價分離：策略指標取自 closed_df，即時報價取自 current_price 或 live bar close
        has_live_bar = len(df) > len(closed_df)
        if current_price is not None and float(current_price) > 0:
            quote = float(current_price)
        elif has_live_bar and "close" in df.columns:
            quote = float(df.iloc[-1]["close"])
        else:
            quote = curr_k["close"]

        # 持倉歷史極值與 peak_roe (包含 live 價格範圍與持倉追蹤)
        peak_roe = 0.0
        min_price = float(df["low"].iloc[-20:].min()) if "low" in df.columns else float(closed_df["low"].iloc[-20:].min())
        max_price = float(df["high"].iloc[-20:].max()) if "high" in df.columns else float(closed_df["high"].iloc[-20:].max())

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

        # Retain the separate HTF regime for existing emergency-exit evidence.
        is_htf_bull = (
            not zoomed_out_bearish
            and (
                (ema_fast >= ema_slow and (macro_slope_10 >= 0 or slow_slope_10 >= 0) and quote >= ema60)
                or (quote > ema60 and macro_slope_10 > 0)
            )
        )
        is_htf_bear = (
            not zoomed_out_bullish
            and (
                (ema_fast <= ema_slow and (macro_slope_10 <= 0 or slow_slope_10 <= 0) and quote <= ema60)
                or (quote < ema60 and macro_slope_10 < 0)
                or zoomed_out_bearish
            )
        )

        if is_htf_bull and not is_htf_bear:
            macro_trend = "BULLISH"
        elif is_htf_bear and not is_htf_bull:
            macro_trend = "BEARISH"
        else:
            macro_trend = "NEUTRAL"

        # 構建帶有指標的 df_calc 並根據實際驗證結果設置 entry_finality_verified
        # 嚴格禁止無條件設 True：只有收線資料通過完整性驗證且足夠計算指標，非異常 fallback 時方為 True
        is_verified = bool(not is_fallback_for_position and len(closed_df) >= 20)
        if has_live_bar or is_fallback_for_position:
            df_calc = df.copy()
            for col in closed_df_with_ind.columns:
                if col not in df_calc.columns or col in ("ma3", "ma5", "ma15", "atr", "kc_upper", "kc_middle", "kc_lower"):
                    df_calc[col] = closed_df_with_ind[col]
            if hasattr(df_calc, "attrs"):
                df_calc.attrs["entry_finality_verified"] = is_verified
                df_calc.attrs["macro_trend"] = macro_trend
                df_calc.attrs.setdefault("timeframe_ms", df.attrs.get("timeframe_ms", 60_000))
        else:
            df_calc = closed_df_with_ind
            if hasattr(df_calc, "attrs"):
                df_calc.attrs["entry_finality_verified"] = is_verified
                df_calc.attrs["macro_trend"] = macro_trend
                df_calc.attrs.setdefault("timeframe_ms", df.attrs.get("timeframe_ms", 60_000))

        # Carry the snapshot's explicitly computed regime with its frame so
        # downstream gates can distinguish a valid neutral market from a
        # missing macro-trend calculation.
        df_calc.attrs["macro_trend"] = macro_trend

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
            prev_ma15=prev_ma15,
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
            ema200=ema200_val,
            volume_ma20=vol_ma20_val,
            raw_df=df_calc,
        )
