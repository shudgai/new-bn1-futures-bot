import pandas as pd
import pytest

from core.services.key_level_dip_sniper import (
    KeyLevelDipSniperService,
    check_comprehensive_long_entry,
    check_comprehensive_short_entry,
)


def _create_frame(
    open_p: float, high_p: float, low_p: float, close_p: float,
    ma5: float, prev_ma5: float, ma15: float, prev_ma15: float,
    kc_mid: float, prev_kc_mid: float, atr: float = 0.0005
) -> pd.DataFrame:
    records = [
        {
            "timestamp": 1000.0,
            "open": open_p, "high": high_p, "low": low_p, "close": close_p,
            "ma5": prev_ma5, "ma15": prev_ma15, "kc_middle": prev_kc_mid,
            "atr": atr, "is_closed": True,
        },
        {
            "timestamp": 2000.0,
            "open": open_p, "high": high_p, "low": low_p, "close": close_p,
            "ma5": prev_ma5, "ma15": prev_ma15, "kc_middle": prev_kc_mid,
            "atr": atr, "is_closed": True,
        },
        {
            "timestamp": 3000.0,
            "open": open_p, "high": high_p, "low": low_p, "close": close_p,
            "ma5": ma5, "ma15": ma15, "kc_middle": kc_mid,
            "atr": atr, "is_closed": False,
        },
    ]
    return pd.DataFrame(records)


def test_comprehensive_short_death_cross():
    """死叉起跌：prev_ma5 >= prev_ma15 且 ma5 < ma15 且 close < kc_mid -> OPEN_SHORT"""
    candle = {"open": 0.0208, "high": 0.02085, "low": 0.02065, "close": 0.02068}
    prev_candle = {"open": 0.02085, "high": 0.0209, "low": 0.02075, "close": 0.0208}
    kc_mid = 0.02075
    ma5 = 0.02070
    prev_ma5 = 0.02080
    ma15 = 0.02072
    prev_ma15 = 0.02075

    action, pattern, reason = check_comprehensive_short_entry(
        candle, prev_candle, kc_mid, ma5, prev_ma5, ma15, prev_ma15
    )
    assert action == "OPEN_SHORT"
    assert pattern == "MA_CROSSOVER"
    assert "波段起跌" in reason


def test_comprehensive_short_rebound_rejection():
    """反彈遇阻摸高：ma5 < ma15 且 close < kc_mid 且 high >= ma5 且 close <= open -> OPEN_SHORT"""
    candle = {"open": 0.02065, "high": 0.02072, "low": 0.02060, "close": 0.02062}
    prev_candle = {}
    kc_mid = 0.02075
    ma5 = 0.02070
    prev_ma5 = 0.02075
    ma15 = 0.02074
    prev_ma15 = 0.02076

    action, pattern, reason = check_comprehensive_short_entry(
        candle, prev_candle, kc_mid, ma5, prev_ma5, ma15, prev_ma15
    )
    assert action == "OPEN_SHORT"
    assert pattern == "DIP_SNIPER"
    assert "高拋狙擊" in reason


def test_comprehensive_long_golden_cross():
    """金叉起漲：prev_ma5 <= prev_ma15 且 ma5 > ma15 且 close > kc_mid -> OPEN_LONG"""
    candle = {"open": 0.0700, "high": 0.0705, "low": 0.0699, "close": 0.0704}
    prev_candle = {}
    kc_mid = 0.0701
    ma5 = 0.0703
    prev_ma5 = 0.0700
    ma15 = 0.0702
    prev_ma15 = 0.0701

    action, pattern, reason = check_comprehensive_long_entry(
        candle, prev_candle, kc_mid, ma5, prev_ma5, ma15, prev_ma15
    )
    assert action == "OPEN_LONG"
    assert pattern == "MA_CROSSOVER"
    assert "波段起漲" in reason


def test_comprehensive_long_dip_support():
    """回踩支撐低吸：ma5 > ma15 且 close > kc_mid 且 low <= ma5 且 close >= open -> OPEN_LONG"""
    candle = {"open": 0.0702, "high": 0.0706, "low": 0.0701, "close": 0.0704}
    prev_candle = {}
    kc_mid = 0.0700
    ma5 = 0.0703
    prev_ma5 = 0.0701
    ma15 = 0.0699
    prev_ma15 = 0.0698

    action, pattern, reason = check_comprehensive_long_entry(
        candle, prev_candle, kc_mid, ma5, prev_ma5, ma15, prev_ma15
    )
    assert action == "OPEN_LONG"
    assert pattern == "DIP_SNIPER"
    assert "低吸狙擊" in reason


def test_service_evaluate_entry_integration():
    """測試 KeyLevelDipSniperService.evaluate_entry 對全面開倉邏輯的整合"""
    # 測試多單金叉起漲
    frame_long = _create_frame(
        open_p=0.0700, high_p=0.0705, low_p=0.0699, close_p=0.0704,
        ma5=0.0703, prev_ma5=0.0700,
        ma15=0.0702, prev_ma15=0.0701,
        kc_mid=0.0701, prev_kc_mid=0.0700,
    )
    res_long = KeyLevelDipSniperService.evaluate_entry("龙虾/USDT", frame_long, "LONG", tick_price=0.0704)
    assert res_long is not None
    assert res_long["side"] == "LONG"
    assert res_long["entry_pattern"] == "MA_CROSSOVER"

    # 測試空單死叉起跌
    frame_short = _create_frame(
        open_p=0.0208, high_p=0.02085, low_p=0.02065, close_p=0.02068,
        ma5=0.02070, prev_ma5=0.02080,
        ma15=0.02072, prev_ma15=0.02075,
        kc_mid=0.02075, prev_kc_mid=0.02076,
    )
    res_short = KeyLevelDipSniperService.evaluate_entry("CAP/USDT", frame_short, "SHORT", tick_price=0.02068)
    assert res_short is not None
    assert res_short["side"] == "SHORT"
    assert res_short["entry_pattern"] == "MA_CROSSOVER"
