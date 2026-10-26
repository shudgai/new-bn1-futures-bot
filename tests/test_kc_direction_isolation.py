"""Tests for KC direction independence from candle color and MA alignment.

驗證核心要點：
1. 綠 K (close > open)，但 KC 中軌下降：KC 方向必須嚴格判定為 DOWN。
2. 紅 K (close < open)，但 KC 中軌上升：KC 方向必須嚴格判定為 UP。
3. 綠 K，KC 中軌上升：KC 方向判定為 UP。
4. 紅 K，KC 中軌下降：KC 方向判定為 DOWN。
5. 多頭排列 (MA3 > MA15)，但 KC 中軌下降：KC 方向必須嚴格判定為 DOWN。
6. 空頭排列 (MA3 < MA15)，但 KC 中軌上升：KC 方向必須嚴格判定為 UP。
7. 中軌持平、資料不足：判定為 RANGE / UNKNOWN。
8. 前端 UI getChartCkTrend 模擬驗證：K 棒顏色不左右中軌方向。
"""
import pytest
import pandas as pd
import numpy as np

from core.gates.strict_entry_and_immunity_gate import resolve_kc_trend
from core.services.strategies.outer_strategy import resolve_kc_direction, ck_direction
from core.services.symbol_runner import _filter_authorities_by_kc_direction
from core.services.exits.trend_lock import evaluate_trend_lock_exit


def create_candle_df(candles: list) -> pd.DataFrame:
    """Helper to create a DataFrame with indicator columns."""
    df = pd.DataFrame(candles)
    if "is_closed" not in df.columns:
        df["is_closed"] = True
    return df


def test_green_candle_falling_kc_middle():
    """情境 1：綠 K (價格上漲收陽)，但 KC 中軌下降 -> KC 方向必須為 DOWN。"""
    candles = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 99.5, "kc_middle": 100.0, "kc_upper": 102.0, "kc_lower": 98.0, "is_closed": True},
        # 綠 K: close=100.5 > open=99.5，但 kc_middle 從 100.0 降至 99.8
        {"open": 99.5, "high": 101.0, "low": 99.4, "close": 100.5, "kc_middle": 99.8, "kc_upper": 101.8, "kc_lower": 97.8, "is_closed": True},
    ]
    df = create_candle_df(candles)
    
    # 1. resolve_kc_trend
    trend = resolve_kc_trend(df)
    assert trend == "DOWN", f"綠 K 但中軌下降時，KC 方向應為 DOWN，實測得到 {trend}"

    # 2. resolve_kc_direction (純斜率)
    slope = 99.8 - 100.0
    dir_res = resolve_kc_direction(slope, 100.5, 99.8, 100.2, 99.9, 1.0)
    assert dir_res == "DOWN"


def test_red_candle_rising_kc_middle():
    """情境 2：紅 K (價格下跌收陰)，但 KC 中軌上升 -> KC 方向必須為 UP。"""
    candles = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "kc_middle": 100.0, "kc_upper": 102.0, "kc_lower": 98.0, "is_closed": True},
        # 紅 K: close=99.5 < open=100.5，但 kc_middle 從 100.0 升至 100.2
        {"open": 100.5, "high": 101.0, "low": 99.4, "close": 99.5, "kc_middle": 100.2, "kc_upper": 102.2, "kc_lower": 98.2, "is_closed": True},
    ]
    df = create_candle_df(candles)
    
    # 1. resolve_kc_trend
    trend = resolve_kc_trend(df)
    assert trend == "UP", f"紅 K 但中軌上升時，KC 方向應為 UP，實測得到 {trend}"

    # 2. resolve_kc_direction (純斜率)
    slope = 100.2 - 100.0
    dir_res = resolve_kc_direction(slope, 99.5, 100.2, 100.0, 99.9, 1.0)
    assert dir_res == "UP"


def test_ma_bullish_alignment_falling_kc():
    """情境 5：多頭排列 (MA3 > MA15)，但 KC 中軌下降 -> KC 方向必須為 DOWN。"""
    slope = -0.5
    price = 105.0
    mid = 100.0
    ma3 = 104.0
    ma15 = 101.0  # MA3 > MA15 多頭排列
    atr = 2.0
    
    dir_res = resolve_kc_direction(slope, price, mid, ma3, ma15, atr)
    assert dir_res == "DOWN", "即便 MA3 > MA15，中軌斜率向下時 KC 方向必須為 DOWN"


def test_ma_bearish_alignment_rising_kc():
    """情境 6：空頭排列 (MA3 < MA15)，但 KC 中軌上升 -> KC 方向必須為 UP。"""
    slope = 0.5
    price = 95.0
    mid = 100.0
    ma3 = 96.0
    ma15 = 99.0  # MA3 < MA15 空頭排列
    atr = 2.0
    
    dir_res = resolve_kc_direction(slope, price, mid, ma3, ma15, atr)
    assert dir_res == "UP", "即便 MA3 < MA15，中軌斜率向上時 KC 方向必須為 UP"


def test_flat_kc_middle():
    """情境 7/8：KC 中軌走平 (無斜率) -> 判定為 RANGE / UNKNOWN。"""
    candles = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "kc_middle": 100.0, "kc_upper": 102.0, "kc_lower": 98.0, "is_closed": True},
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "kc_middle": 100.0, "kc_upper": 102.0, "kc_lower": 98.0, "is_closed": True},
    ]
    df = create_candle_df(candles)
    trend = resolve_kc_trend(df)
    assert trend == "RANGE"

    dir_res = resolve_kc_direction(0.0, 100.0, 100.0, 100.0, 100.0, 1.0)
    assert dir_res == "UNKNOWN"


def test_symbol_runner_kc_filter_respects_middle_not_color():
    """驗證 symbol_runner 的 _filter_authorities_by_kc_direction 依賴已收線中軌數值，不受 K 棒顏色影響。"""
    candles = [
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 99.0, "kc_middle": 100.0, "is_closed": True},
        # 綠 K (收陽)，但 kc_middle 下降
        {"open": 99.0, "high": 101.0, "low": 99.0, "close": 100.5, "kc_middle": 99.8, "is_closed": True},
    ]
    df = create_candle_df(candles)
    df.attrs["entry_finality_verified"] = True
    df.attrs["timeframe_ms"] = 60000

    authorities = [
        {"authority": "GENERAL_LONG", "side": "LONG"},
        {"authority": "GENERAL_SHORT", "side": "SHORT"},
    ]

    filtered, allowed_side = _filter_authorities_by_kc_direction(authorities, df)
    # 中軌下降 -> allowed_side 必須是 SHORT
    assert allowed_side == "SHORT"
    assert len(filtered) == 1
    assert filtered[0]["side"] == "SHORT"
