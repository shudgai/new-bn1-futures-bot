"""Tests for mandatory post-exit cooldown, profit room validation, and trend exhaustion VETO.

依據使用者鐵律：
「開倉平倉後冷靜一下有利潤在開倉，若已在漲勢末端就不要再開倉」
"""
import pytest
import pandas as pd
from core.gates.meme_shared_protection_gate import MemeSharedProtectionGate


def test_bullish_exhaustion_vetoes_long_on_extreme_bias():
    """一般趨勢下，MA15 乖離超過 1.5 ATR 阻止追多。"""
    df = pd.DataFrame([
        {'open': 100.0, 'close': 101.0, 'high': 101.2, 'low': 99.8, 'ma5': 101.0, 'ma15': 100.0, 'atr': 1.0,
         'kc_upper': 102.0, 'kc_lower': 98.0, 'kc_middle': 100.0},
        {'open': 101.0, 'close': 102.0, 'high': 102.2, 'low': 100.8, 'ma5': 101.5, 'ma15': 100.2, 'atr': 1.0,
         'kc_upper': 102.5, 'kc_lower': 98.5, 'kc_middle': 100.2},
        # 價格衝到 103.5，相對於 ma15 (100.5) 乖離達 3 ATR。
        {'open': 102.0, 'close': 103.5, 'high': 103.6, 'low': 101.9, 'ma5': 102.8, 'ma15': 100.5, 'atr': 1.0,
         'kc_upper': 103.0, 'kc_lower': 99.0, 'kc_middle': 100.5},
    ])
    passed, reason = MemeSharedProtectionGate.validate_trend_exhaustion(df, side='LONG', quote=103.5)
    assert not passed
    assert 'VETO_LONG_BULLISH_EXHAUSTION' in reason
    assert 'MA15 乖離' in reason


def test_bullish_exhaustion_does_not_veto_on_one_upper_wick():
    """單根上影線沒有爆量及後續轉弱確認時，不單獨否決開多。"""
    df = pd.DataFrame([
        {'open': 100.0, 'close': 101.0, 'high': 101.2, 'low': 99.8, 'ma5': 100.5, 'ma15': 100.0, 'atr': 1.0,
         'kc_upper': 102.0, 'kc_lower': 98.0, 'kc_middle': 100.0},
        {'open': 101.0, 'close': 102.0, 'high': 102.2, 'low': 100.8, 'ma5': 101.2, 'ma15': 101.0, 'atr': 1.0,
         'kc_upper': 102.5, 'kc_lower': 98.5, 'kc_middle': 100.2},
        # 開盤 102.0, 最高衝到 103.5, 隨後大幅跳水收在 102.3 (body=0.3, upper_wick=1.2 > 0.7*body, bias=0.8)
        {'open': 102.0, 'close': 102.3, 'high': 103.5, 'low': 101.9, 'ma5': 102.0, 'ma15': 101.5, 'atr': 1.0,
         'kc_upper': 102.8, 'kc_lower': 99.0, 'kc_middle': 101.0},
    ])
    passed, reason = MemeSharedProtectionGate.validate_trend_exhaustion(df, side='LONG', quote=102.3)
    assert passed
    assert reason == 'PASS'


def test_bearish_exhaustion_vetoes_short_on_extreme_bias():
    """一般趨勢下，MA15 乖離超過 1.5 ATR 阻止追空。"""
    df = pd.DataFrame([
        {'open': 100.0, 'close': 99.0, 'high': 100.2, 'low': 98.8, 'ma5': 99.0, 'ma15': 100.0, 'atr': 1.0,
         'kc_upper': 102.0, 'kc_lower': 98.0, 'kc_middle': 100.0},
        {'open': 99.0, 'close': 98.0, 'high': 99.2, 'low': 97.8, 'ma5': 98.5, 'ma15': 99.8, 'atr': 1.0,
         'kc_upper': 101.5, 'kc_lower': 97.5, 'kc_middle': 99.8},
        # 價格跌至 96.5，相對於 ma15 (99.5) 乖離達 3 ATR。
        {'open': 98.0, 'close': 96.5, 'high': 98.1, 'low': 96.4, 'ma5': 97.2, 'ma15': 99.5, 'atr': 1.0,
         'kc_upper': 101.0, 'kc_lower': 97.0, 'kc_middle': 99.5},
    ])
    passed, reason = MemeSharedProtectionGate.validate_trend_exhaustion(df, side='SHORT', quote=96.5)
    assert not passed
    assert 'VETO_SHORT_BEARISH_EXHAUSTION' in reason
    assert 'MA15 乖離' in reason


def test_bearish_exhaustion_does_not_veto_on_one_lower_wick():
    """單根下影線沒有爆量及後續轉強確認時，不單獨否決開空。"""
    df = pd.DataFrame([
        {'open': 100.0, 'close': 99.0, 'high': 100.2, 'low': 98.8, 'ma5': 99.5, 'ma15': 100.0, 'atr': 1.0,
         'kc_upper': 102.0, 'kc_lower': 98.0, 'kc_middle': 100.0},
        {'open': 99.0, 'close': 98.0, 'high': 99.2, 'low': 97.8, 'ma5': 98.8, 'ma15': 98.7, 'atr': 1.0,
         'kc_upper': 101.5, 'kc_lower': 97.5, 'kc_middle': 99.8},
        # 開盤 98.0, 最低跌到 96.5, 隨後強烈回抽收在 97.7 (body=0.3, lower_wick=1.2 > 0.7*body, bias=0.8)
        {'open': 98.0, 'close': 97.7, 'high': 98.1, 'low': 96.5, 'ma5': 98.0, 'ma15': 98.5, 'atr': 1.0,
         'kc_upper': 101.0, 'kc_lower': 97.2, 'kc_middle': 99.0},
    ])
    passed, reason = MemeSharedProtectionGate.validate_trend_exhaustion(df, side='SHORT', quote=97.7)
    assert passed
    assert reason == 'PASS'


@pytest.mark.parametrize(
    ('side', 'strong_kwargs', 'expected'),
    [
        ('LONG', {}, False),
        ('LONG', {'strong_bullish_trend': True}, True),
        ('SHORT', {}, False),
        ('SHORT', {'strong_bearish_trend': True}, True),
    ],
)
def test_strong_trend_uses_symmetric_extension_allowance(side, strong_kwargs, expected):
    direction = 1 if side == 'LONG' else -1
    ma15 = 100.0
    price = ma15 + direction * 1.6
    df = pd.DataFrame([
        {
            'open': price - direction * 0.1,
            'close': price,
            'high': price + 0.2,
            'low': price - 0.2,
            'ma5': 100.0,
            'ma15': ma15,
            'atr': 1.0,
            'kc_upper': 103.0,
            'kc_lower': 97.0,
        }
        for _ in range(3)
    ])
    passed, _ = MemeSharedProtectionGate.validate_trend_exhaustion(
        df, side=side, quote=price, **strong_kwargs
    )
    assert passed is expected


@pytest.mark.parametrize(
    ('side', 'continuation_kwargs'),
    [
        ('LONG', {'bullish_continuation': True}),
        ('SHORT', {'bearish_continuation': True}),
    ],
)
def test_qualified_continuation_bypasses_extension_filter(side, continuation_kwargs):
    direction = 1 if side == 'LONG' else -1
    price = 100.0 + direction * 3.2
    df = pd.DataFrame([
        {
            'open': price - direction * 0.1,
            'close': price,
            'high': price + 0.2,
            'low': price - 0.2,
            'ma5': 100.0,
            'ma15': 100.0,
            'atr': 1.0,
            'kc_upper': 101.0,
            'kc_lower': 99.0,
        }
        for _ in range(3)
    ])
    passed, reason = MemeSharedProtectionGate.validate_trend_exhaustion(
        df, side=side, quote=price, **continuation_kwargs
    )
    assert passed
    assert reason == 'PASS: QUALIFIED_STRONG_TREND_CONTINUATION'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_confirmed_volume_climax_requires_following_reversal_without_new_extreme(side):
    bars = [
        {
            'open': 100.0, 'close': 100.1, 'high': 100.2, 'low': 99.9,
            'volume': 100.0, 'ma5': 100.0, 'ma15': 100.0, 'atr': 1.0,
            'kc_upper': 103.0, 'kc_lower': 97.0,
        }
        for _ in range(10)
    ]
    if side == 'SHORT':
        bars.append({
            'open': 98.0, 'close': 96.0, 'high': 98.1, 'low': 93.8,
            'volume': 300.0, 'ma5': 96.8, 'ma15': 99.0, 'atr': 1.0,
            'kc_upper': 102.0, 'kc_lower': 95.0,
        })
        bars.append({
            'open': 96.0, 'close': 96.5, 'high': 96.7, 'low': 94.1,
            'volume': 100.0, 'ma5': 96.2, 'ma15': 96.2, 'atr': 1.0,
            'kc_upper': 100.0, 'kc_lower': 92.0,
        })
    else:
        bars.append({
            'open': 102.0, 'close': 104.0, 'high': 106.2, 'low': 101.9,
            'volume': 300.0, 'ma5': 103.0, 'ma15': 101.0, 'atr': 1.0,
            'kc_upper': 107.0, 'kc_lower': 98.0,
        })
        bars.append({
            'open': 104.0, 'close': 103.5, 'high': 106.1, 'low': 103.2,
            'volume': 100.0, 'ma5': 103.8, 'ma15': 103.8, 'atr': 1.0,
            'kc_upper': 108.0, 'kc_lower': 99.0,
        })
    df = pd.DataFrame(bars)

    passed, reason = MemeSharedProtectionGate.validate_trend_exhaustion(
        df, side=side, quote=float(df.iloc[-1]['close'])
    )
    assert not passed
    assert f'VETO_{side}_' in reason and 'CONFIRMED_VOLUME_CLIMAX' in reason


def test_profit_room_filter_blocks_long_near_upper_rail():
    """獲利空間：距離 KC 上軌剩餘空間不足 20% 且不足 0.35 ATR，100% 阻斷貼軌追多！"""
    df = pd.DataFrame([
        {'open': 100.0, 'close': 101.0, 'high': 101.2, 'low': 99.8, 'atr': 1.0,
         'kc_upper': 104.0, 'kc_lower': 100.0, 'kc_middle': 102.0},
        # 通道寬度 4.0, 價格 103.8 (距上軌剩餘 0.2 < 0.20*4.0 = 0.8 且 < 0.35 ATR)
        {'open': 101.0, 'close': 103.8, 'high': 103.9, 'low': 100.8, 'atr': 1.0,
         'kc_upper': 104.0, 'kc_lower': 100.0, 'kc_middle': 102.0},
    ])
    passed, reason = MemeSharedProtectionGate.validate_profit_room(df, side='LONG', quote=103.8)
    assert not passed
    assert 'INSUFFICIENT_PROFIT_ROOM_FOR_LONG' in reason


def test_profit_room_filter_blocks_short_near_lower_rail():
    """獲利空間：距離 KC 下軌剩餘空間不足 20% 且不足 0.35 ATR，100% 阻斷貼軌追空！"""
    df = pd.DataFrame([
        {'open': 100.0, 'close': 99.0, 'high': 100.2, 'low': 98.8, 'atr': 1.0,
         'kc_upper': 104.0, 'kc_lower': 100.0, 'kc_middle': 102.0},
        # 通道寬度 4.0, 價格 100.2 (距下軌剩餘 0.2 < 0.20*4.0 = 0.8 且 < 0.35 ATR)
        {'open': 99.0, 'close': 100.2, 'high': 100.5, 'low': 100.1, 'atr': 1.0,
         'kc_upper': 104.0, 'kc_lower': 100.0, 'kc_middle': 102.0},
    ])
    passed, reason = MemeSharedProtectionGate.validate_profit_room(df, side='SHORT', quote=100.2)
    assert not passed
    assert 'INSUFFICIENT_PROFIT_ROOM_FOR_SHORT' in reason


def test_profit_room_filter_passes_when_room_is_healthy():
    """獲利空間：價格位於中軌附近，向上/向下有充足利潤空間時放行！"""
    df = pd.DataFrame([
        {'open': 100.0, 'close': 101.0, 'high': 101.2, 'low': 99.8, 'atr': 1.0,
         'kc_upper': 104.0, 'kc_lower': 100.0, 'kc_middle': 102.0},
        {'open': 101.0, 'close': 101.5, 'high': 101.8, 'low': 100.8, 'atr': 1.0,
         'kc_upper': 104.0, 'kc_lower': 100.0, 'kc_middle': 102.0},
    ])
    # 做多：現價 101.5 到 104.0 有 2.5 空間 (62.5% > 20%)
    passed_long, _ = MemeSharedProtectionGate.validate_profit_room(df, side='LONG', quote=101.5)
    assert passed_long

    # 做空：現價 102.5 到 100.0 有 2.5 空間 (62.5% > 20%)
    passed_short, _ = MemeSharedProtectionGate.validate_profit_room(df, side='SHORT', quote=102.5)
    assert passed_short


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_shared_entry_evaluation_does_not_veto_on_single_wick(side):
    direction = 1 if side == 'LONG' else -1
    open_price = 100.0
    close_price = open_price + direction * 0.8
    high = max(open_price, close_price) + (1.0 if side == 'LONG' else 0.2)
    low = min(open_price, close_price) - (1.0 if side == 'SHORT' else 0.2)
    df = pd.DataFrame([{
        'open': open_price,
        'close': close_price,
        'high': high,
        'low': low,
        'ma5': 100.0,
        'ma15': 100.0,
        'atr': 1.0,
        'volume': 100.0,
    }])
    passed, reason = MemeSharedProtectionGate.evaluate(
        df, side=side, quote=close_price
    )
    assert passed
    assert reason == 'PASSED'
