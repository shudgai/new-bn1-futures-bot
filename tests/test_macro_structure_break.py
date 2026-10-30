import pytest
import pandas as pd
from core.services.market_structure_break import (
    check_macro_trend_end,
    find_htf_key_pivot,
    calculate_htf_ma15,
)
from core.services.execution_manager import check_macro_trend_end as exec_check_macro_trend_end


def test_import_compatibility():
    """驗證 check_macro_trend_end 可自 execution_manager 直接匯入"""
    assert check_macro_trend_end is exec_check_macro_trend_end


def test_macro_bullish_continues_on_minor_pullback():
    """牛市小幅回調 (未破前低 HL 且未跌破 MA15) -> 維持牛市防護罩"""
    # Key Pivot HL = 100.0, HTF MA15 = 102.0, Latest Close = 103.0
    candles = [
        {"open": 98.0, "high": 102.0, "low": 97.0, "close": 100.0},
        {"open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0},
        {"open": 104.0, "high": 106.0, "low": 102.0, "close": 103.0},
    ]
    res = check_macro_trend_end(
        current_macro_trend="BULLISH",
        htf_candles=candles,
        htf_ma15=102.0,
        key_pivot_price=100.0,
    )
    assert res["macro_trend"] == "BULLISH"
    assert res["event"] == "MACRO_TREND_CONTINUES"
    assert "大週期結構完好" in res["reason"]


def test_macro_bullish_continues_if_only_ma15_broken_but_pivot_holds():
    """牛市微幅跌破 MA15 但守住結構關鍵 HL -> 依然維持牛市防護罩，不被毛刺洗掉"""
    # Key Pivot HL = 100.0, HTF MA15 = 102.0, Latest Close = 101.0 (跌破 MA15 但守穩 HL 100.0)
    candles = [
        {"close": 105.0},
        {"close": 101.0},
    ]
    res = check_macro_trend_end(
        current_macro_trend="BULLISH",
        htf_candles=candles,
        htf_ma15=102.0,
        key_pivot_price=100.0,
    )
    assert res["macro_trend"] == "BULLISH"
    assert res["event"] == "MACRO_TREND_CONTINUES"


def test_macro_bullish_ended_on_double_confirmation():
    """牛市結束：收盤價實體跌破關鍵 HL 支撐且跌破 HTF MA15 均線 -> 宣告牛市結束轉為熊市"""
    # Key Pivot HL = 100.0, HTF MA15 = 102.0, Latest Close = 99.0 (同時跌破 HL 與 MA15)
    candles = [
        {"close": 105.0},
        {"close": 99.0},
    ]
    res = check_macro_trend_end(
        current_macro_trend="BULLISH",
        htf_candles=candles,
        htf_ma15=102.0,
        key_pivot_price=100.0,
    )
    assert res["macro_trend"] == "BEARISH"
    assert res["event"] == "MACRO_BULLISH_ENDED"
    assert "大週期牛市結束" in res["reason"]
    assert "大週期熊市 (順勢做空)" in res["status_label"]


def test_macro_bearish_continues_on_minor_rebound():
    """熊市小幅反彈 (未突破前高 LH 且未突破 MA15) -> 維持熊市防護罩"""
    # Key Pivot LH = 100.0, HTF MA15 = 98.0, Latest Close = 97.0
    candles = [
        {"close": 95.0},
        {"close": 97.0},
    ]
    res = check_macro_trend_end(
        current_macro_trend="BEARISH",
        htf_candles=candles,
        htf_ma15=98.0,
        key_pivot_price=100.0,
    )
    assert res["macro_trend"] == "BEARISH"
    assert res["event"] == "MACRO_TREND_CONTINUES"
    assert "大週期結構完好" in res["reason"]


def test_macro_bearish_continues_if_only_ma15_touched_but_pivot_holds():
    """熊市反彈穿過 MA15 但受阻於關鍵 LH 壓力 -> 依然維持熊市防護罩，不輕易翻多"""
    # Key Pivot LH = 100.0, HTF MA15 = 98.0, Latest Close = 99.0 (穿過 MA15 但未破 LH 100.0)
    candles = [
        {"close": 96.0},
        {"close": 99.0},
    ]
    res = check_macro_trend_end(
        current_macro_trend="BEARISH",
        htf_candles=candles,
        htf_ma15=98.0,
        key_pivot_price=100.0,
    )
    assert res["macro_trend"] == "BEARISH"
    assert res["event"] == "MACRO_TREND_CONTINUES"


def test_macro_bearish_ended_on_double_confirmation():
    """熊市結束：收盤價實體強勢突破關鍵 LH 壓力且站上 HTF MA15 均線 -> 宣告熊市結束轉為牛市"""
    # Key Pivot LH = 100.0, HTF MA15 = 98.0, Latest Close = 101.5 (同時突破 LH 與 MA15)
    candles = [
        {"close": 95.0},
        {"close": 101.5},
    ]
    res = check_macro_trend_end(
        current_macro_trend="BEARISH",
        htf_candles=candles,
        htf_ma15=98.0,
        key_pivot_price=100.0,
    )
    assert res["macro_trend"] == "BULLISH"
    assert res["event"] == "MACRO_BEARISH_ENDED"
    assert "大週期熊市結束" in res["reason"]
    assert "大週期牛市 (順勢做多)" in res["status_label"]


def test_dataframe_and_auto_derivation():
    """測試傳入 pandas DataFrame 且自動計算 key pivot 與 MA15"""
    df = pd.DataFrame({
        "open": [10.0, 11.0, 10.5, 12.0, 13.0, 12.5, 14.0],
        "high": [11.5, 12.0, 11.5, 13.5, 14.0, 13.5, 15.0],
        "low": [9.5, 10.2, 9.8, 11.0, 12.0, 11.5, 13.0], # 9.8 是三點局部谷底 HL
        "close": [11.0, 10.8, 11.2, 13.0, 12.8, 13.5, 14.2],
    })
    pivot = find_htf_key_pivot(df, "BULLISH")
    assert pivot > 0
    ma15 = calculate_htf_ma15(df)
    assert ma15 > 0

    res = check_macro_trend_end(
        current_macro_trend="BULLISH",
        htf_candles=df,
    )
    assert res["macro_trend"] == "BULLISH"
    assert res["event"] == "MACRO_TREND_CONTINUES"
