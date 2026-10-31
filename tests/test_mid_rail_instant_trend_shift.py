import pytest
import pandas as pd
from core.services.market_structure_break import check_macro_trend_end


def test_image1_ma3_cross_above_mid_instantly_turns_bullish():
    """
    重現圖1場景：原為大週期熊市，MA3 與最新收盤價穿過中軌快到上軌。
    系統必須立即觸發 MACRO_BEARISH_ENDED 轉為 BULLISH，絕不可延誤等到圖3！
    """
    # 模擬 1000PEPE 剛從底部 0.00492 反彈至 0.00495，中軌為 0.00494，MA3 為 0.004952
    res = check_macro_trend_end(
        current_macro_trend="BEARISH",
        htf_candles=None,
        htf_ma15=0.0050,  # 慢速 HTF 均線仍偏高
        key_pivot_price=0.00498,  # 前波高點高於當前價
        live_1m_candles=None,
        current_1m_close=0.00495,
        current_1m_ma15=0.00493,
        current_1m_kc_mid=0.00494,
        current_1m_ma3=0.004952,
    )
    assert res["macro_trend"] == "BULLISH"
    assert res["event"] == "MACRO_BEARISH_ENDED"
    assert "大週期牛市 (順勢做多)" in res["status_label"]
    assert "進入上半通道" in res["reason"]


def test_image2_ma3_cross_below_mid_instantly_turns_bearish():
    """
    重現圖2場景：原為大週期牛市，MA3 與最新收盤價跌穿中軌快到下軌。
    系統必須立即觸發 MACRO_BULLISH_ENDED 轉為 BEARISH，絕不可延誤等到圖4！
    """
    # 模擬 龙虾/USDT 剛從高點 0.01940 下殺至 0.01928，中軌為 0.01931，MA3 為 0.01927
    res = check_macro_trend_end(
        current_macro_trend="BULLISH",
        htf_candles=None,
        htf_ma15=0.01920,  # 慢速 HTF 均線仍偏低
        key_pivot_price=0.01922,  # 前波低點低於當前價
        live_1m_candles=None,
        current_1m_close=0.01928,
        current_1m_ma15=0.01932,
        current_1m_kc_mid=0.01931,
        current_1m_ma3=0.01927,
    )
    assert res["macro_trend"] == "BEARISH"
    assert res["event"] == "MACRO_BULLISH_ENDED"
    assert "大週期熊市 (順勢做空)" in res["status_label"]
    assert "進入下半通道" in res["reason"]
