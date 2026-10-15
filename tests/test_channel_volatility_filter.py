"""Unit tests for ChannelVolatilityFilter (Volatility Filter Gate)"""
import pytest
import pandas as pd
from core.gates.channel_volatility_filter import ChannelVolatilityFilter


def test_channel_volatility_filter_extreme_squeeze_veto():
    """驗證：當帶寬壓縮至歷史最高帶寬的 25% 或以下時，強制一票否決開倉 (Extreme Squeeze Veto)"""
    v_filter = ChannelVolatilityFilter(lookback_period=20, ma_period=10, extreme_squeeze_pct=0.25)

    # 模擬過去 20 根 K 線：前面最高帶寬曾達 4% (0.04)，目前一路收窄至 0.8% (0.008 <= 0.04 * 0.25)
    rows = []
    for i in range(20):
        # 逐步收窄
        middle = 100.0
        width = 4.0 if i == 0 else max(0.8, 4.0 - i * 0.2)
        upper = middle + width / 2.0
        lower = middle - width / 2.0
        rows.append({
            'close': middle,
            'high': upper,
            'low': lower,
            'kc_middle': middle,
            'kc_upper': upper,
            'kc_lower': lower,
        })
    df = pd.DataFrame(rows)

    can_enter, reason, details = v_filter.evaluate(df)

    assert can_enter is False
    assert "EXTREME_VOLATILITY_COMPRESSION" in reason
    assert details['bw_to_max_ratio'] <= 0.25


def test_channel_volatility_filter_below_ma_veto():
    """驗證：當帶寬低於移動平均線的 0.85 倍時，判定為震盪磨損期，強制一票否決開倉"""
    v_filter = ChannelVolatilityFilter(lookback_period=20, ma_period=10, squeeze_ratio=0.85)

    rows = []
    # 前 15 根維持 2% 帶寬，最後幾根突然掉到 1.2% (1.2% < 2% * 0.85 = 1.7%)
    for i in range(15):
        rows.append({'kc_middle': 100.0, 'kc_upper': 101.0, 'kc_lower': 99.0})
    for i in range(5):
        rows.append({'kc_middle': 100.0, 'kc_upper': 100.6, 'kc_lower': 99.4})

    df = pd.DataFrame(rows)
    can_enter, reason, details = v_filter.evaluate(df)

    assert can_enter is False
    assert "CHANNEL_SQUEEZE_BELOW_MA" in reason


def test_channel_volatility_filter_expanding_pass():
    """驗證：當通道重新打開並向上突破其移動平均線且持續擴張時，放行開倉！"""
    v_filter = ChannelVolatilityFilter(lookback_period=20, ma_period=10, squeeze_ratio=0.85)

    rows = []
    # 前面平穩狹窄 1.5% 帶寬，最後兩根突然劇烈張口擴張到 2.5% -> 3.2%
    for i in range(18):
        rows.append({'kc_middle': 100.0, 'kc_upper': 100.75, 'kc_lower': 99.25})
    # 張口
    rows.append({'kc_middle': 100.0, 'kc_upper': 101.25, 'kc_lower': 98.75})
    rows.append({'kc_middle': 100.0, 'kc_upper': 101.60, 'kc_lower': 98.40})

    df = pd.DataFrame(rows)
    can_enter, reason, details = v_filter.evaluate(df)

    assert can_enter is True
    assert "PASS" in reason
    assert details['bandwidth'] > details['prev_bandwidth']
