"""Closed price True Peak/Trough 3 points with spatial filtering and CKS trend direction."""
import math
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy

PIVOT_CODES = {"KC_MA15_TROUGH_LONG", "KC_MA15_PEAK_SHORT"}

def calculate_ck_trend(df: pd.DataFrame) -> str:
    """判斷 CK (Chande Kroll / 通道指標) 當前走向"""
    if df is None or len(df) < 2:
        return "FLAT"
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    ck_long = latest.get("ck_long", 0.0)
    ck_short = latest.get("ck_short", float('inf'))
    close = latest["close"]
    prev_ck_long = prev.get("ck_long", 0.0)
    prev_ck_short = prev.get("ck_short", float('inf'))

    if close > ck_short and ck_long >= prev_ck_long:
        return "BULL"
    elif close < ck_long and ck_short <= prev_ck_short:
        return "BEAR"
    return "FLAT"

def is_pivot_peak(df: pd.DataFrame, require_rail_touch: bool = False) -> bool:
    """真頂峰三點檢驗：t-2, t-1, t (已收盤)"""
    if df is None or len(df) < 3:
        return False
    b0, b1, b2 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    is_peak = (b1["high"] > b0["high"]) and (b1["high"] > b2["high"])
    confirmed = b2["close"] < b1["low"]
    
    if require_rail_touch:
        if b1["high"] < b1.get("kc_upper", float('inf')):
            return False
            
    return bool(is_peak and confirmed)

def is_pivot_trough(df: pd.DataFrame, require_rail_touch: bool = False) -> bool:
    """真谷底三點檢驗：t-2, t-1, t (已收盤)"""
    if df is None or len(df) < 3:
        return False
    b0, b1, b2 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    is_trough = (b1["low"] < b0["low"]) and (b1["low"] < b2["low"])
    confirmed = b2["close"] > b1["high"]
    
    if require_rail_touch:
        if b1["low"] > b1.get("kc_lower", 0.0):
            return False
            
    return bool(is_trough and confirmed)

def has_enough_profit_space(entry_price: float, target_level: float, atr: float, min_atr_mult: float = 1.5) -> bool:
    """空間距離過濾：計算到下一個預期阻力/支撐的空間是否足夠獲利"""
    distance = abs(target_level - entry_price)
    return distance >= (atr * min_atr_mult)

def check_two_bar_breakout(df: pd.DataFrame, band_type: str = "upper") -> bool:
    """平倉後防假破軌：必須連續兩根 K 線實體完整收在軌道外"""
    if df is None or len(df) < 2:
        return False
    b1 = df.iloc[-2]
    b2 = df.iloc[-1]

    if band_type == "upper":
        two_closes = (b1["close"] > b1["kc_upper"]) and (b2["close"] > b2["kc_upper"])
        is_bullish = b2["close"] > b2["open"]
        return bool(two_closes and is_bullish)

    elif band_type == "lower":
        two_closes = (b1["close"] < b1["kc_lower"]) and (b2["close"] < b2["kc_lower"])
        is_bearish = b2["close"] < b2["open"]
        return bool(two_closes and is_bearish)

    return False

def get_recent_levels(df: pd.DataFrame) -> dict:
    """從歷史 K 線中找出最近的真峰頂與真谷底價格"""
    levels = {"last_peak": None, "last_trough": None}
    if df is None or len(df) < 5:
        return levels
        
    # 回溯尋找最近的 peak/trough
    for i in range(len(df)-1, 2, -1):
        window = df.iloc[i-3:i]
        if levels["last_peak"] is None and is_pivot_peak(window):
            levels["last_peak"] = window.iloc[-2]["high"]
        if levels["last_trough"] is None and is_pivot_trough(window):
            levels["last_trough"] = window.iloc[-2]["low"]
            
        if levels["last_peak"] is not None and levels["last_trough"] is not None:
            break
            
    # 若找不到，給予預設極端值避免報錯
    if levels["last_peak"] is None:
        levels["last_peak"] = df.iloc[-1]["close"] * 1.5
    if levels["last_trough"] is None:
        levels["last_trough"] = df.iloc[-1]["close"] * 0.5
        
    return levels

def evaluate_strategy(df: pd.DataFrame, position: str) -> str:
    """交易決策主函數 (由外部調用)"""
    if df is None or len(df) < 3:
        return "WAIT"
        
    latest = df.iloc[-1]
    close = latest["close"]
    atr = latest["atr"]
    trend = calculate_ck_trend(df)
    recent_levels = get_recent_levels(df)

    if position == "LONG":
        if is_pivot_peak(df):
            return "CLOSE_LONG"
        return "HOLD_LONG"

    if position == "SHORT":
        if is_pivot_trough(df):
            return "CLOSE_SHORT"
        return "HOLD_SHORT"

    if position == "NONE":
        if trend == "BULL":
            if is_pivot_trough(df, require_rail_touch=True):
                target = recent_levels.get("last_peak", close + 3 * atr)
                if has_enough_profit_space(close, target, atr, min_atr_mult=1.5):
                    return "OPEN_LONG_FROM_TROUGH"
                return "SKIP_SPACE_TOO_CLOSE"
            if check_two_bar_breakout(df, band_type="upper"):
                return "OPEN_LONG_REBREAKOUT"

        elif trend == "BEAR":
            if is_pivot_peak(df, require_rail_touch=True):
                target = recent_levels.get("last_trough", close - 3 * atr)
                if has_enough_profit_space(close, target, atr, min_atr_mult=1.5):
                    return "OPEN_SHORT_FROM_PEAK"
                return "SKIP_SPACE_TOO_CLOSE"
            if check_two_bar_breakout(df, band_type="lower"):
                return "OPEN_SHORT_REBREAKOUT"

    return "WAIT"

class PivotChannelEntryStrategy(IEntryStrategy):
    """Legacy interface mapping"""
    def evaluate_entry(
        self,
        frame: pd.DataFrame,
        price: float,
        side: str,
        **kwargs: Any
    ) -> Tuple[bool, str, Dict[str, Any]]:
        # This is overridden by the global evaluate_strategy mechanism now.
        return False, "DEPRECATED", {}

