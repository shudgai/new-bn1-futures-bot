"""
單元測試：中央開倉物理閘門 (CentralEntryGate)
Path: tests/test_central_entry_gate.py
"""

import pytest
import pandas as pd
import numpy as np

from core.gates.central_entry_gate import CentralEntryGate, GateCheckResult


def _build_valid_long_frame():
    # 建立一個完全符合 7 大條件的多單 DataFrame
    # 帶寬 = (105 - 95) / 100 = 10% >= 1%
    # 中軌斜率 = 100 - 99.7 = 0.3 >= 0.1 * 2.0 = 0.2
    # MA5 斜率 = 101 - 100.5 = 0.5 > 0
    # 實體 = 101.5 - 100.0 = 1.5 >= 0.35 * 2.0 = 0.7
    # 上影線 = 102.0 - 101.5 = 0.5 <= 0.7 * 1.5 = 1.05
    # 乖離 = 101.5 - 105.0 = -3.5 <= 0.5 * 2.0 = 1.0
    # 量能 = 1500 >= 1.2 * 1000 = 1200
    rows = []
    for i in range(15):
        rows.append({
            "timestamp": 1700000000 + i * 60,
            "open": 98.0 + i * 0.1,
            "high": 99.0 + i * 0.1,
            "low": 97.5 + i * 0.1,
            "close": 98.5 + i * 0.1,
            "volume": 1000.0,
            "is_closed": True,
            "atr": 2.0,
            "kc_upper": 104.0,
            "kc_lower": 96.0,
            "kc_middle": 99.7,
            "kc_middle_slope": 0.25,
            "ma5": 100.5,
        })
    # 最後一根符合突破條件
    rows[-1] = {
        "timestamp": 1700000000 + 14 * 60,
        "open": 100.0,
        "high": 102.0,
        "low": 99.8,
        "close": 101.5,
        "volume": 1500.0,
        "is_closed": True,
        "atr": 2.0,
        "kc_upper": 105.0,
        "kc_lower": 95.0,
        "kc_middle": 100.0,
        "kc_middle_slope": 0.30,  # >= 0.1 * 2.0 = 0.2
        "ma5": 101.0,  # prev is 100.5, so delta = 0.5 > 0
    }
    return pd.DataFrame(rows)


def test_central_entry_gate_valid_long():
    df = _build_valid_long_frame()
    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=101.5,
        klines_or_snapshot=df,
        source="TEST_STRATEGY",
    )
    assert res.passed is True
    assert res.reason == "PASSED"
    assert res.signal_id.startswith("SIG_BTCUSDT_LONG_")
    assert res.checks["closed_bar"]["status"] == "PASS"
    assert res.checks["kc_middle_slope"]["status"] == "PASS"
    assert res.checks["ma5_slope"]["status"] == "PASS"
    assert res.checks["bandwidth"]["status"] == "PASS"


def test_veto_intrabar_not_closed():
    df = _build_valid_long_frame()
    # 模擬最後一根尚未收線
    df.loc[df.index[-1], "is_closed"] = False
    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=101.5,
        klines_or_snapshot=df,
    )
    assert res.passed is False
    assert res.reason == "VETO_INTRABAR_NOT_CLOSED"


def test_authorized_intrabar_entry_still_runs_all_market_checks():
    df = _build_valid_long_frame()
    df.loc[df.index[-1], "is_closed"] = False

    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=101.5,
        klines_or_snapshot=df,
        source="UNIFIED_PIPELINE|BREAKOUT_LONG",
        allow_intrabar=True,
    )

    assert res.passed is True, res.reason
    assert res.checks["closed_bar"]["status"] == "PASS"


def test_veto_kc_middle_slope_insufficient():
    df = _build_valid_long_frame()
    # 中軌斜率僅 0.05 < 0.1 * 2.0 (0.2)
    df.loc[df.index[-1], "kc_middle_slope"] = 0.05
    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=101.5,
        klines_or_snapshot=df,
    )
    assert res.passed is False
    assert res.reason == "VETO_KC_SLOPE_INSUFFICIENT"


def test_veto_ma5_slope_opposite():
    df = _build_valid_long_frame()
    # MA5 向下拐頭
    df.loc[df.index[-1], "ma5"] = 99.0  # prev is 100.5, delta < 0
    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=101.5,
        klines_or_snapshot=df,
    )
    assert res.passed is False
    assert res.reason == "VETO_MA5_SLOPE_OPPOSITE"


def test_veto_kc_outer_overextension():
    df = _build_valid_long_frame()
    # 價格衝到 107.0 (kc_upper is 105.0, 乖離 2.0 > 0.5 * 2.0 = 1.0)
    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=107.0,
        klines_or_snapshot=df,
    )
    assert res.passed is False
    assert res.reason == "VETO_KC_OUTER_OVEREXTENSION"


def test_veto_kc_bandwidth_too_narrow():
    df = _build_valid_long_frame()
    # 帶寬僅 0.2% (upper=100.10, lower=99.90, middle=100.0) -> width=0.2 / 100 = 0.002 < 0.0035
    df.loc[df.index[-1], "kc_upper"] = 100.10
    df.loc[df.index[-1], "kc_lower"] = 99.90
    df.loc[df.index[-1], "kc_middle"] = 100.0
    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=100.1,
        klines_or_snapshot=df,
    )
    assert res.passed is False
    assert res.reason == "VETO_KC_BANDWIDTH_TOO_NARROW"


def test_veto_bearish_candle_for_long():
    df = _build_valid_long_frame()
    # 收陰線
    df.loc[df.index[-1], "open"] = 102.0
    df.loc[df.index[-1], "close"] = 100.5
    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=100.5,
        klines_or_snapshot=df,
    )
    assert res.passed is False
    assert res.reason == "VETO_BEARISH_CANDLE_FOR_LONG"


def test_veto_upper_wick_too_long():
    df = _build_valid_long_frame()
    # 實體 1.0 (open=100, close=101), 上影線 1.5 (high=102.5) > 0.7 * 1.0
    df.loc[df.index[-1], "open"] = 100.0
    df.loc[df.index[-1], "close"] = 101.0
    df.loc[df.index[-1], "high"] = 102.5
    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=101.0,
        klines_or_snapshot=df,
    )
    assert res.passed is False
    assert res.reason == "VETO_UPPER_WICK_TOO_LONG"


def test_veto_volume_insufficient():
    df = _build_valid_long_frame()
    # 突破棒量能僅 800 < 1.2 * 1000 = 1200
    df.loc[df.index[-1], "volume"] = 800.0
    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=101.5,
        klines_or_snapshot=df,
        enforce_volume=True,
    )
    assert res.passed is False
    assert res.reason == "VETO_VOLUME_INSUFFICIENT"


def test_cross_mid_short_allowed_when_flat_slope_and_below_midline():
    """均線死叉轉折做空：中軌走平 (斜率 0.0，不足 0.1 ATR) 但在中軌下方，放行做空"""
    rows = []
    for i in range(15):
        rows.append({
            "timestamp": 1700000000 + i * 60,
            "open": 101.0,
            "high": 101.5,
            "low": 100.5,
            "close": 101.0,
            "volume": 1000.0,
            "is_closed": True,
            "atr": 2.0,
            "kc_upper": 104.0,
            "kc_lower": 96.0,
            "kc_middle": 100.0,
            "kc_middle_slope": 0.0,
            "ma5": 100.5,
        })
    # 頂部轉折死叉棒：收黑陰線 (open=100.0, close=98.5, low=98.2, high=100.2)，在 KC 中軌 (100.0) 下方
    # 中軌斜率僅 -0.02 (不足 0.1 * 2.0 = 0.2)，MA5 拐頭向下 (99.0 < 100.5)
    rows[-1] = {
        "timestamp": 1700000000 + 14 * 60,
        "open": 100.0,
        "high": 100.2,
        "low": 98.2,
        "close": 98.5,
        "volume": 1500.0,
        "is_closed": True,
        "atr": 2.0,
        "kc_upper": 105.0,
        "kc_lower": 95.0,
        "kc_middle": 100.0,
        "kc_middle_slope": -0.02,  # 接近 0，破軌單會被擋，但死叉轉折單放行
        "ma5": 99.0,  # 下彎
    }
    df = pd.DataFrame(rows)
    # 常規破軌追空：被擋
    res_breakout = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="SHORT",
        price=98.5,
        klines_or_snapshot=df,
        source="BREAKDOWN_SHORT",
    )
    assert res_breakout.passed is False
    assert res_breakout.reason == "VETO_KC_SLOPE_INSUFFICIENT"

    # 均線死叉轉折開空：動態放寬放行！
    res_cross = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="SHORT",
        price=98.5,
        klines_or_snapshot=df,
        source="ENTRY_SHORT_EMA_CROSS_MID",
    )
    assert res_cross.passed is True


def test_cross_mid_short_veto_if_above_midline():
    """做空安全底線：若價格仍在 KC 中軌上方，哪怕死叉也一票否決 VETO_SHORT_ABOVE_KC_MIDDLE"""
    rows = []
    for i in range(15):
        rows.append({
            "timestamp": 1700000000 + i * 60,
            "open": 102.0,
            "high": 103.0,
            "low": 101.5,
            "close": 102.5,
            "volume": 1000.0,
            "is_closed": True,
            "atr": 2.0,
            "kc_upper": 106.0,
            "kc_lower": 94.0,
            "kc_middle": 100.0,
            "kc_middle_slope": -0.01,
            "ma5": 102.0,
        })
    # 價格 101.5 仍在 KC 中軌 100.0 上方
    rows[-1] = {
        "timestamp": 1700000000 + 14 * 60,
        "open": 102.5,
        "high": 102.6,
        "low": 101.0,
        "close": 101.5,
        "volume": 1500.0,
        "is_closed": True,
        "atr": 2.0,
        "kc_upper": 106.0,
        "kc_lower": 94.0,
        "kc_middle": 100.0,
        "kc_middle_slope": -0.01,
        "ma5": 101.0,
    }
    df = pd.DataFrame(rows)
    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="SHORT",
        price=101.5,
        klines_or_snapshot=df,
        source="ENTRY_SHORT_EMA_CROSS_MID",
    )
    assert res.passed is False
    assert res.reason == "VETO_SHORT_ABOVE_KC_MIDDLE"


def test_cross_mid_long_allowed_when_flat_slope_and_above_midline():
    """均線金叉轉折做多：中軌走平 (斜率 0.0) 但在中軌上方，放行做多"""
    rows = []
    for i in range(15):
        rows.append({
            "timestamp": 1700000000 + i * 60,
            "open": 99.0,
            "high": 99.5,
            "low": 98.5,
            "close": 99.0,
            "volume": 1000.0,
            "is_closed": True,
            "atr": 2.0,
            "kc_upper": 104.0,
            "kc_lower": 96.0,
            "kc_middle": 100.0,
            "kc_middle_slope": 0.0,
            "ma5": 99.5,
        })
    # 底部轉折金叉棒：收紅陽線 (open=100.0, close=101.5, low=99.8, high=101.8)，在 KC 中軌 (100.0) 上方
    rows[-1] = {
        "timestamp": 1700000000 + 14 * 60,
        "open": 100.0,
        "high": 101.8,
        "low": 99.8,
        "close": 101.5,
        "volume": 1500.0,
        "is_closed": True,
        "atr": 2.0,
        "kc_upper": 105.0,
        "kc_lower": 95.0,
        "kc_middle": 100.0,
        "kc_middle_slope": 0.02,  # 走平微翹，不足 0.2
        "ma5": 101.0,  # 上翹
    }
    df = pd.DataFrame(rows)
    res = CentralEntryGate.verify_and_authorize(
        symbol="BTC/USDT",
        side="LONG",
        price=101.5,
        klines_or_snapshot=df,
        source="ENTRY_LONG_EMA_CROSS_MID",
    )
    assert res.passed is True
