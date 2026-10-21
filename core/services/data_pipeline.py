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
    kc_upper: float
    kc_middle: float
    kc_lower: float
    kc_middle_slope: float
    atr: float
    peak_roe: float = 0.0
    min_price: float = 0.0
    max_price: float = 0.0
    raw_df: Optional[pd.DataFrame] = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "current_price": self.current_price,
            "curr_k": self.curr_k,
            "prev_k": self.prev_k,
            "ma5": self.ma5,
            "ma15": self.ma15,
            "kc_upper": self.kc_upper,
            "kc_middle": self.kc_middle,
            "kc_lower": self.kc_lower,
            "kc_middle_slope": self.kc_middle_slope,
            "atr": self.atr,
            "peak_roe": self.peak_roe,
            "min_price": self.min_price,
            "max_price": self.max_price,
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
        ma15 = float(ma15_series.iloc[-1]) if not pd.isna(ma15_series.iloc[-1]) else curr_k["close"]
        kc_up = float(kc_upper_series.iloc[-1]) if not pd.isna(kc_upper_series.iloc[-1]) else curr_k["close"]
        kc_mid = float(kc_basis.iloc[-1]) if not pd.isna(kc_basis.iloc[-1]) else curr_k["close"]
        kc_low = float(kc_lower_series.iloc[-1]) if not pd.isna(kc_lower_series.iloc[-1]) else curr_k["close"]

        prev_kc_mid = float(kc_basis.iloc[-2]) if len(kc_basis) > 1 and not pd.isna(kc_basis.iloc[-2]) else kc_mid
        kc_middle_slope = kc_mid - prev_kc_mid

        atr_val = float(atr_safe.iloc[-1]) if not pd.isna(atr_safe.iloc[-1]) and atr_safe.iloc[-1] > 0 else (curr_k["close"] * 0.01)

        quote = float(current_price) if current_price is not None and current_price > 0 else curr_k["close"]

        # 持倉歷史極值與 peak_roe
        peak_roe = 0.0
        min_price = float(df_calc["low"].iloc[-20:].min())
        max_price = float(df_calc["high"].iloc[-20:].max())

        if position:
            entry_p = float(position.get("entry_price") or 0.0)
            side = str(position.get("side", "")).upper()
            if entry_p > 0:
                cur_roe = ((quote - entry_p) / entry_p) if side == "LONG" else ((entry_p - quote) / entry_p)
                stored_peak = float(position.get("peak_roe", 0.0) or position.get("highest_pnl_pct", 0.0) or 0.0)
                peak_roe = max(stored_peak, cur_roe)

        return MarketDataSnapshot(
            symbol=symbol,
            current_price=quote,
            curr_k=curr_k,
            prev_k=prev_k,
            ma5=ma5,
            ma15=ma15,
            kc_upper=kc_up,
            kc_middle=kc_mid,
            kc_lower=kc_low,
            kc_middle_slope=kc_middle_slope,
            atr=atr_val,
            peak_roe=peak_roe,
            min_price=min_price,
            max_price=max_price,
            raw_df=df_calc,
        )
