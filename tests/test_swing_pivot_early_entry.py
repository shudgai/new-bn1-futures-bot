"""
tests/test_swing_pivot_early_entry.py
=====================================
驗證谷底提前做多 (SWING_PIVOT_LONG) 與頂部提前做空 (SWING_PIVOT_SHORT)：
1. 谷底轉折做多：價格在 KC 通道下半區，MA3 向上拐頭且即將金叉/已金叉 MA15，
   K 棒收紅陽線且站穩 MA3，即使 KC 中軌向下，依然提前授權做多！
2. 頂部轉折做空：價格在 KC 通道上半區，MA3 向下拐頭且即將死叉/已死叉 MA15，
   K 棒收黑陰線且受壓於 MA3，即使 KC 中軌向上，依然提前授權做空！
3. 停損嚴格設定在局部谷底最低點 / 局部頂部最高點，風報比極優且絕不死扛。
"""
import pandas as pd
import pytest
from core.services.swing_pivot_rules import evaluate_swing_pivot_entry


def test_valley_early_entry_long_with_kc_slope_down():
    """谷底提前做多：即使中軌向下，只要谷底轉折確立，提前授權開多！"""
    bars_valley = [
        dict(timestamp=60_000, open=98.0, high=98.5, low=96.0, close=96.5, atr=1.0, ma3=97.0, ma15=98.0, kc_middle=99.5, kc_upper=101.5, kc_lower=97.5, is_closed=True),
        dict(timestamp=120_000, open=96.5, high=97.0, low=95.0, close=95.5, atr=1.0, ma3=96.0, ma15=97.5, kc_middle=99.0, kc_upper=101.0, kc_lower=97.0, is_closed=True), # 探底 (low=95.0)
        dict(timestamp=180_000, open=95.5, high=98.0, low=95.4, close=97.8, atr=1.0, ma3=96.8, ma15=96.5, kc_middle=98.5, kc_upper=100.5, kc_lower=96.5, is_closed=True), # 轉強確認 (close=97.8 > open=95.5, ma3向上)
    ]
    df = pd.DataFrame(bars_valley)
    df.attrs["timeframe_ms"] = 60_000.0

    res = evaluate_swing_pivot_entry(df)
    assert res is not None, "應觸發谷底提前做多"
    assert res["side"] == "LONG"
    assert res["entry_type"] == "SWING_PIVOT"
    assert res["direction_override"] is True
    assert res["stop_loss_price"] == 95.0


def test_peak_early_entry_short_with_kc_slope_up():
    """頂部提前做空：即使中軌向上，只要頂部轉折確立，提前授權開空！"""
    bars_peak = [
        dict(timestamp=60_000, open=102.0, high=103.5, low=101.5, close=103.0, atr=1.0, ma3=102.5, ma15=101.5, kc_middle=100.5, kc_upper=102.5, kc_lower=98.5, is_closed=True),
        dict(timestamp=120_000, open=103.0, high=105.0, low=102.8, close=104.5, atr=1.0, ma3=103.8, ma15=102.0, kc_middle=101.0, kc_upper=103.0, kc_lower=99.0, is_closed=True), # 摸頂 (high=105.0)
        dict(timestamp=180_000, open=104.5, high=104.6, low=102.0, close=102.2, atr=1.0, ma3=103.0, ma15=103.2, kc_middle=101.5, kc_upper=103.5, kc_lower=99.5, is_closed=True), # 轉弱確認 (close=102.2 < open=104.5, ma3向下)
    ]
    df = pd.DataFrame(bars_peak)
    df.attrs["timeframe_ms"] = 60_000.0

    res = evaluate_swing_pivot_entry(df)
    assert res is not None, "應觸發頂部提前做空"
    assert res["side"] == "SHORT"
    assert res["entry_type"] == "SWING_PIVOT"
    assert res["direction_override"] is True
    assert res["stop_loss_price"] == 105.0

