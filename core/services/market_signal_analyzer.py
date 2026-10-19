"""市場信號與波段持倉防洗分析器 (Market Signal Analyzer).

基於多均線結構 (MA5, MA15, MA60) 與籌碼基準線 (VWAP / KC Middle)，
精準判定強烈多頭、強烈空頭，以及結構內的健康回踩 (HOLD_LONG) 與健康反彈 (HOLD_SHORT)。
"""
from typing import Any, Optional, Union
import numpy as np
import pandas as pd


def calculate_vwap_estimate(frame: pd.DataFrame) -> float:
    """計算即時 VWAP，若無成交量或累積數據則以典型價格 (High+Low+Close)/3 平滑代表。"""
    try:
        if len(frame) == 0:
            return 0.0
        if "volume" in frame.columns and (frame["volume"] > 0).any():
            typical_price = (frame["high"] + frame["low"] + frame["close"]) / 3.0
            cum_vol = frame["volume"].cumsum()
            cum_tp_vol = (typical_price * frame["volume"]).cumsum()
            if cum_vol.iloc[-1] > 0:
                return float(cum_tp_vol.iloc[-1] / cum_vol.iloc[-1])
        # 若無 volume 則使用 KC 中軌或典型價格均線
        if "kc_middle" in frame.columns:
            return float(frame["kc_middle"].iloc[-1])
        return float((frame["high"].iloc[-1] + frame["low"].iloc[-1] + frame["close"].iloc[-1]) / 3.0)
    except Exception:
        return float(frame["close"].iloc[-1]) if len(frame) > 0 else 0.0


def calculate_ma(series: pd.Series, period: int) -> float:
    """計算指定週期簡單移動平均線。"""
    if len(series) < period:
        return float(series.mean()) if len(series) > 0 else 0.0
    return float(series.iloc[-period:].mean())


def analyze_market_signal(
    candle: Union[dict[str, Any], pd.Series],
    vwap: float,
    ma5: float,
    ma15: float,
    ma60: float,
) -> dict[str, str]:
    """
    輸入 1分K 與各指標數值，自動輸出【做多/做空/觀望/多頭續抱/空頭續抱】
    """
    close = float(candle.get("close", 0.0) if isinstance(candle, dict) else candle["close"])
    
    # 1. 均線排列判斷
    is_ma_bullish = (ma5 > ma15) and (ma15 > ma60)
    is_ma_bearish = (ma5 < ma15) and (ma15 < ma60)
    
    # 2. 價格與基準線位置
    above_vwap = close > vwap
    below_vwap = close < vwap

    # ==========================
    # 多頭波段判定
    # ==========================
    if is_ma_bullish and above_vwap and close > ma5:
        return {
            "status": "【做多】",
            "reason": "強烈多頭：Close > 5MA > 15MA > 60MA 且高於 VWAP，波段動能強勁",
            "action": "ALLOW_LONG"
        }
    
    # ==========================
    # 空頭波段判定
    # ==========================
    elif is_ma_bearish and below_vwap and close < ma5:
        return {
            "status": "【做空】",
            "reason": "強烈空頭：Close < 5MA < 15MA < 60MA 且低於 VWAP，空方全面控盤",
            "action": "ALLOW_SHORT"
        }

    # ==========================
    # 多頭結構內的正常回踩 (防被洗出場)
    # ==========================
    elif close > ma60 and close >= min(ma15, vwap):
        return {
            "status": "【觀望 (多頭回踩)】",
            "reason": "多頭健康回調，支撐未破，持多者續抱，不盲目加倉，嚴禁做空",
            "action": "HOLD_LONG"
        }

    # ==========================
    # 空頭結構內的正常反彈 (防被洗出場)
    # ==========================
    elif close < ma60 and close <= max(ma15, vwap):
        return {
            "status": "【觀望 (空頭反彈)】",
            "reason": "空頭次級反彈，壓力未過，持空者續抱，不盲目加空，嚴禁做多",
            "action": "HOLD_SHORT"
        }

    # ==========================
    # 震盪雜訊區
    # ==========================
    else:
        return {
            "status": "【觀望】",
            "reason": "均線或籌碼糾纏，多空方向不明，保持空手防守",
            "action": "WAIT"
        }


def evaluate_frame_market_signal(frame: pd.DataFrame, current_price: Optional[float] = None) -> dict[str, str]:
    """從 1 分鐘 K 線數據幀提取最新數值並分析市場信號。"""
    if frame is None or len(frame) == 0:
        return {
            "status": "【觀望】",
            "reason": "數據不足",
            "action": "WAIT",
        }
    
    last_candle = frame.iloc[-1].to_dict()
    if current_price is not None:
        last_candle["close"] = float(current_price)
        
    closes = frame["close"].copy()
    if current_price is not None and len(closes) > 0:
        closes.iloc[-1] = float(current_price)
        
    ma5 = float(last_candle.get("ma5") or calculate_ma(closes, 5))
    ma15 = float(last_candle.get("ma15") or calculate_ma(closes, 15))
    ma60 = float(last_candle.get("ma60") or calculate_ma(closes, 60))
    vwap = float(last_candle.get("vwap") or calculate_vwap_estimate(frame))
    
    return analyze_market_signal(last_candle, vwap, ma5, ma15, ma60)
