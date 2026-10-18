import pytest
import pandas as pd
from core.gates.strict_entry_and_immunity_gate import (
    StrictEntryAndImmunityGate,
    resolve_kc_trend,
    check_kc_flat_or_squeezing,
)
from core.gates.holding_protection_gate import HoldingProtectionExitGate
from core.services.entry_contract import check_entry_gates


def make_frame(bars):
    df = pd.DataFrame(bars)
    df.attrs['timeframe_ms'] = 60000
    return df


def test_entry_veto_cannot_long_when_kc_down():
    """驗證：當大趨勢/通道標記為 KC向下，開多 100% 一票否決 (VETO_CANNOT_LONG_WHEN_KC_DOWN)"""
    df = make_frame([
        {'open': 100.0, 'close': 98.0, 'high': 100.5, 'low': 97.5,
         'ma5': 98.5, 'ma15': 101.0, 'kc_middle': 100.0, 'kc_upper': 105.0, 'kc_lower': 95.0,
         'atr': 2.0, 'is_closed': True, 'channel_state': 'KC向下'},
        {'open': 98.0, 'close': 96.0, 'high': 98.5, 'low': 95.5,
         'ma5': 97.0, 'ma15': 100.0, 'kc_middle': 99.0, 'kc_upper': 104.0, 'kc_lower': 94.0,
         'atr': 2.0, 'is_closed': True, 'channel_state': 'KC向下'},
    ])
    trend = resolve_kc_trend(df)
    assert trend == 'DOWN'

    passed, reason = check_entry_gates(None, 'LOBSTER/USDT', df, 'LONG', 'TRIGGER_A_KC_BREAKOUT')
    assert passed is False
    assert reason == 'VETO_CANNOT_LONG_WHEN_KC_DOWN'


def test_entry_veto_cannot_short_when_kc_up():
    """驗證：當大趨勢/通道標記為 KC向上，開空 100% 一票否決 (VETO_CANNOT_SHORT_WHEN_KC_UP)"""
    df = make_frame([
        {'open': 100.0, 'close': 102.0, 'high': 102.5, 'low': 99.5,
         'ma5': 101.5, 'ma15': 99.0, 'kc_middle': 100.0, 'kc_upper': 105.0, 'kc_lower': 95.0,
         'atr': 2.0, 'is_closed': True, 'channel_state': 'KC向上'},
        {'open': 102.0, 'close': 104.0, 'high': 104.5, 'low': 101.5,
         'ma5': 103.0, 'ma15': 100.0, 'kc_middle': 101.0, 'kc_upper': 106.0, 'kc_lower': 96.0,
         'atr': 2.0, 'is_closed': True, 'channel_state': 'KC向上'},
    ])
    trend = resolve_kc_trend(df)
    assert trend == 'UP'

    passed, reason = check_entry_gates(None, 'LOBSTER/USDT', df, 'SHORT', 'TRIGGER_A_KC_BREAKOUT')
    assert passed is False
    assert reason == 'VETO_CANNOT_SHORT_WHEN_KC_UP'


def test_conflicting_bullish_tag_cannot_override_falling_kc_midline():
    df = make_frame([
        {'open': 100.0, 'close': 101.0, 'high': 102.0, 'low': 99.0,
         'ma5': 102.0, 'ma15': 101.0, 'kc_middle': 100.0,
         'kc_upper': 105.0, 'kc_lower': 95.0, 'atr': 2.0,
         'is_closed': True, 'channel_state': 'KC向上'},
        {'open': 101.0, 'close': 100.5, 'high': 102.0, 'low': 99.5,
         'ma5': 102.0, 'ma15': 101.0, 'kc_middle': 99.9,
         'kc_upper': 104.9, 'kc_lower': 94.9, 'atr': 2.0,
         'is_closed': True, 'channel_state': 'KC向上'},
    ])
    assert resolve_kc_trend(df) == 'DOWN'
    passed, reason = check_entry_gates(
        None, 'CAP/USDT', df, 'LONG', 'TRIGGER_A_KC_BREAKOUT',
    )
    assert passed is False
    assert reason == 'VETO_CANNOT_LONG_WHEN_KC_DOWN'


def test_entry_veto_choppy_market_squeezing():
    """驗證：通道扁平極窄震盪期，100% 一票否決開倉 (VETO_CHOPPY_MARKET_NO_ENTRY)"""
    # 構造帶寬佔價格比例極小 (< 0.6%) 的死水震盪
    df = make_frame([
        {'open': 100.0, 'close': 100.05, 'high': 100.1, 'low': 99.95,
         'ma5': 100.0, 'ma15': 100.0, 'kc_middle': 100.0, 'kc_upper': 100.2, 'kc_lower': 99.8,
         'atr': 0.1, 'is_closed': True, 'channel_state': 'RANGE'},
        {'open': 100.05, 'close': 100.02, 'high': 100.1, 'low': 99.95,
         'ma5': 100.0, 'ma15': 100.0, 'kc_middle': 100.0, 'kc_upper': 100.2, 'kc_lower': 99.8,
         'atr': 0.1, 'is_closed': True, 'channel_state': 'RANGE'},
    ])
    assert check_kc_flat_or_squeezing(df) is True
    passed, reason = check_entry_gates(None, 'LOBSTER/USDT', df, 'LONG', 'TRIGGER_A_KC_BREAKOUT')
    assert passed is False
    assert reason == 'VETO_CHOPPY_MARKET_NO_ENTRY'


def test_exit_veto_live_bar_tick_unconfirmed():
    """驗證：浮盈未達 10% 階段，盤中即時跳動價（未收盤）嘗試平倉，100% 一票否決 (VETO_HOLDING_UNTIL_BAR_CLOSED)"""
    df = make_frame([
        {'open': 100.0, 'close': 102.0, 'high': 103.0, 'low': 99.5,
         'ma5': 101.5, 'ma15': 99.0, 'kc_middle': 100.0, 'kc_upper': 105.0, 'kc_lower': 95.0,
         'atr': 2.0, 'is_closed': True},
        # 盤中跳動中 K 棒
        {'open': 102.0, 'close': 101.8, 'high': 102.5, 'low': 101.5,
         'ma5': 101.8, 'ma15': 99.5, 'kc_middle': 100.5, 'kc_upper': 105.5, 'kc_lower': 95.5,
         'atr': 2.0, 'is_closed': False},
    ])
    position = {
        'symbol': 'BTC/USDT',
        'side': 'LONG',
        'entry_price': 100.0, # 浮盈 1.8% < 10%
    }
    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, df, quote=101.8, candidate_reason='EXIT_BY_PULLBACK'
    )
    assert allowed is False
    assert reason == 'VETO_HOLDING_UNTIL_BAR_CLOSED'


def test_exit_veto_healthy_pullback_above_mid():
    """驗證：浮盈未達 10% 階段，已收盤 K 棒只要在 KC 中軌 / MA15 之上，一律視為健康回踩，100% 一票否決平多"""
    df = make_frame([
        {'open': 100.0, 'close': 104.0, 'high': 105.0, 'low': 99.5,
         'ma5': 102.5, 'ma15': 100.0, 'kc_middle': 101.0, 'kc_upper': 106.0, 'kc_lower': 96.0,
         'atr': 2.0, 'is_closed': True},
        # 收盤微幅回測到 103.5，仍遠在 KC 中軌 101.5 與 MA15 101.0 之上
        {'open': 104.0, 'close': 103.5, 'high': 104.5, 'low': 103.0,
         'ma5': 103.0, 'ma15': 101.0, 'kc_middle': 101.5, 'kc_upper': 106.5, 'kc_lower': 96.5,
         'atr': 2.0, 'is_closed': True},
    ])
    position = {
        'symbol': 'BTC/USDT',
        'side': 'LONG',
        'entry_price': 100.0, # 浮盈 3.5% < 10%
    }
    # 無論候選原因是回踩、短均線拐頭、十字星或分形，均 100% 一票否決
    for cand in ['EXIT_BY_PULLBACK', 'EXIT_BY_DOJI', 'EXIT_BY_MA_CROSS', 'EXIT_LONG_ON_FRACTAL_PEAK']:
        allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
            position, df, quote=103.5, candidate_reason=cand
        )
        assert allowed is False
        assert 'VETO_HOLDING_HEALTHY_PULLBACK_ABOVE_MID' in reason
