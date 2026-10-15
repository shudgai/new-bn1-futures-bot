"""
tests/test_trend_riding_strategy.py
多空對稱趨勢鎖倉模式 (Trend-Riding Mode) 與對稱防洗盤規則單元測試
"""

import pytest
import pandas as pd
import numpy as np

from core.gates.trend_riding_strategy import TrendRidingStrategy


def make_candle_df(records):
    df = pd.DataFrame(records)
    for col in ('open', 'high', 'low', 'close', 'atr', 'kc_middle', 'kc_upper', 'kc_lower', 'ma5', 'ma15', 'ema3', 'volume'):
        if col in df.columns:
            df[col] = df[col].astype(float)
    if 'is_closed' not in df.columns:
        df['is_closed'] = True
    return df


# ══════════════════════════════════════════════════════════════════════
# 1. 趨勢狀態判定測試 (Market Regime Filter)
# ══════════════════════════════════════════════════════════════════════
def test_evaluate_market_regime_bull_trend():
    """多頭趨勢：基準線向上發散、三軌齊升、收盤皆在中軌上方"""
    strategy = TrendRidingStrategy()
    df = make_candle_df([
        {'close': 101.0, 'open': 100.0, 'high': 101.5, 'low': 100.0, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'close': 102.0, 'open': 101.0, 'high': 102.5, 'low': 101.0, 'atr': 1.0, 'kc_middle': 100.3, 'kc_upper': 102.3, 'kc_lower': 98.3},
        {'close': 103.0, 'open': 102.0, 'high': 103.5, 'low': 102.0, 'atr': 1.0, 'kc_middle': 100.7, 'kc_upper': 102.7, 'kc_lower': 98.7},
        {'close': 104.0, 'open': 103.0, 'high': 104.5, 'low': 103.0, 'atr': 1.0, 'kc_middle': 101.2, 'kc_upper': 103.2, 'kc_lower': 99.2},
        {'close': 105.0, 'open': 104.0, 'high': 105.5, 'low': 104.0, 'atr': 1.0, 'kc_middle': 101.8, 'kc_upper': 103.8, 'kc_lower': 99.8},
    ])
    regime, details = strategy.evaluate_market_regime(df)
    assert regime == "BULL_TREND"
    assert details['basis_slope_atr'] > 0


def test_evaluate_market_regime_bear_trend():
    """空頭趨勢：基準線向下發散、三軌齊跌、收盤皆在中軌下方"""
    strategy = TrendRidingStrategy()
    df = make_candle_df([
        {'close': 99.0, 'open': 100.0, 'high': 100.0, 'low': 98.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'close': 98.0, 'open': 99.0, 'high': 99.0, 'low': 97.5, 'atr': 1.0, 'kc_middle': 99.7, 'kc_upper': 101.7, 'kc_lower': 97.7},
        {'close': 97.0, 'open': 98.0, 'high': 98.0, 'low': 96.5, 'atr': 1.0, 'kc_middle': 99.3, 'kc_upper': 101.3, 'kc_lower': 97.3},
        {'close': 96.0, 'open': 97.0, 'high': 97.0, 'low': 95.5, 'atr': 1.0, 'kc_middle': 98.8, 'kc_upper': 100.8, 'kc_lower': 96.8},
        {'close': 95.0, 'open': 96.0, 'high': 96.0, 'low': 94.5, 'atr': 1.0, 'kc_middle': 98.2, 'kc_upper': 100.2, 'kc_lower': 96.2},
    ])
    regime, details = strategy.evaluate_market_regime(df)
    assert regime == "BEAR_TREND"
    assert details['basis_slope_atr'] < 0


def test_evaluate_market_regime_range_chop():
    """震盪區間：基準線走平、多空穿梭"""
    strategy = TrendRidingStrategy()
    df = make_candle_df([
        {'close': 100.2, 'open': 99.8, 'high': 100.5, 'low': 99.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'close': 99.7, 'open': 100.2, 'high': 100.4, 'low': 99.6, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'close': 100.3, 'open': 99.7, 'high': 100.6, 'low': 99.6, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'close': 99.8, 'open': 100.3, 'high': 100.4, 'low': 99.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'close': 100.1, 'open': 99.8, 'high': 100.5, 'low': 99.7, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
    ])
    regime, details = strategy.evaluate_market_regime(df)
    assert regime == "RANGE_CHOP"


# ══════════════════════════════════════════════════════════════════════
# 2. 開倉過濾測試 (can_open_position)
# ══════════════════════════════════════════════════════════════════════
def test_long_entry_blocked_in_unfavorable_half_or_bear_trend():
    """做多嚴禁在空頭趨勢或中軌下方進場"""
    strategy = TrendRidingStrategy()
    # 構造空頭趨勢背景，收盤在中軌下方
    df = make_candle_df([
        {'close': 99.0, 'open': 100.0, 'high': 100.0, 'low': 98.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'close': 98.0, 'open': 99.0, 'high': 99.0, 'low': 97.5, 'atr': 1.0, 'kc_middle': 99.7, 'kc_upper': 101.7, 'kc_lower': 97.7},
        {'close': 97.0, 'open': 98.0, 'high': 98.0, 'low': 96.5, 'atr': 1.0, 'kc_middle': 99.3, 'kc_upper': 101.3, 'kc_lower': 97.3},
        {'close': 96.0, 'open': 97.0, 'high': 97.0, 'low': 95.5, 'atr': 1.0, 'kc_middle': 98.8, 'kc_upper': 100.8, 'kc_lower': 96.8},
        {'close': 95.0, 'open': 96.0, 'high': 96.0, 'low': 94.5, 'atr': 1.0, 'kc_middle': 98.2, 'kc_upper': 100.2, 'kc_lower': 96.2},
    ])
    can_open, reason, _ = strategy.can_open_position(df, side='LONG')
    assert can_open is False
    assert "BLOCKED_LONG_ON_BEAR_TREND" in reason or "UNFAVORABLE_HALF" in reason


def test_short_entry_blocked_in_unfavorable_half_or_bull_trend():
    """做空嚴禁在多頭趨勢或中軌上方進場"""
    strategy = TrendRidingStrategy()
    # 構造多頭趨勢背景，收盤在中軌上方
    df = make_candle_df([
        {'close': 101.0, 'open': 100.0, 'high': 101.5, 'low': 100.0, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'close': 102.0, 'open': 101.0, 'high': 102.5, 'low': 101.0, 'atr': 1.0, 'kc_middle': 100.3, 'kc_upper': 102.3, 'kc_lower': 98.3},
        {'close': 103.0, 'open': 102.0, 'high': 103.5, 'low': 102.0, 'atr': 1.0, 'kc_middle': 100.7, 'kc_upper': 102.7, 'kc_lower': 98.7},
        {'close': 104.0, 'open': 103.0, 'high': 104.5, 'low': 103.0, 'atr': 1.0, 'kc_middle': 101.2, 'kc_upper': 103.2, 'kc_lower': 99.2},
        {'close': 105.0, 'open': 104.0, 'high': 105.5, 'low': 104.0, 'atr': 1.0, 'kc_middle': 101.8, 'kc_upper': 103.8, 'kc_lower': 99.8},
    ])
    can_open, reason, _ = strategy.can_open_position(df, side='SHORT')
    assert can_open is False
    assert "BLOCKED_SHORT_ON_BULL_TREND" in reason or "UNFAVORABLE_HALF" in reason


def test_cooldown_blocks_immediate_reentry():
    """平倉冷卻：平倉未滿 5 根 K 棒，同向嚴禁再度進場"""
    strategy = TrendRidingStrategy(cooldown_bars=5)
    df = make_candle_df([
        {'timestamp': 100000, 'close': 101.0, 'open': 100.0, 'high': 101.5, 'low': 100.0, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'timestamp': 160000, 'close': 102.0, 'open': 101.0, 'high': 102.5, 'low': 101.0, 'atr': 1.0, 'kc_middle': 100.3, 'kc_upper': 102.3, 'kc_lower': 98.3},
        {'timestamp': 220000, 'close': 103.0, 'open': 102.0, 'high': 103.5, 'low': 102.0, 'atr': 1.0, 'kc_middle': 100.7, 'kc_upper': 102.7, 'kc_lower': 98.7},
        {'timestamp': 280000, 'close': 103.5, 'open': 103.0, 'high': 104.0, 'low': 102.8, 'atr': 1.0, 'kc_middle': 101.2, 'kc_upper': 103.2, 'kc_lower': 99.2},
        {'timestamp': 340000, 'close': 104.0, 'open': 103.5, 'high': 104.2, 'low': 103.0, 'atr': 1.0, 'kc_middle': 101.8, 'kc_upper': 103.8, 'kc_lower': 99.8},
    ])
    # 上次平多時間在 220000，當前時間 340000，經過 2 根 K 棒 (< 5 根)
    can_open, reason, _ = strategy.can_open_position(
        df, side='LONG', last_closed_action='CLOSE_LONG', last_closed_ts=220000
    )
    assert can_open is False
    assert "COOLDOWN_ACTIVE" in reason


# ══════════════════════════════════════════════════════════════════════
# 3. 趨勢鎖倉模式 (Trend-Riding Lock) & 合法平倉測試
# ══════════════════════════════════════════════════════════════════════
def test_trend_riding_lock_holds_long_during_healthy_pullback():
    """【多單趨勢鎖倉】在多頭趨勢中，回踩收進通道內但收盤守住中軌，強制 HOLD 嚴禁平多"""
    strategy = TrendRidingStrategy()
    # 5M 向上多頭走勢，最新 K 棒從 106 回踩收在 103.5，但中軌在 101.8 (守穩)
    df = make_candle_df([
        {'close': 101.0, 'open': 100.0, 'high': 101.5, 'low': 100.0, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'close': 102.0, 'open': 101.0, 'high': 102.5, 'low': 101.0, 'atr': 1.0, 'kc_middle': 100.3, 'kc_upper': 102.3, 'kc_lower': 98.3},
        {'close': 103.5, 'open': 102.0, 'high': 104.0, 'low': 102.0, 'atr': 1.0, 'kc_middle': 100.7, 'kc_upper': 102.7, 'kc_lower': 98.7},
        {'close': 105.0, 'open': 103.5, 'high': 106.0, 'low': 103.5, 'atr': 1.0, 'kc_middle': 101.2, 'kc_upper': 103.2, 'kc_lower': 99.2},
        {'close': 103.5, 'open': 105.0, 'high': 105.5, 'low': 103.0, 'atr': 1.0, 'kc_middle': 101.8, 'kc_upper': 103.8, 'kc_lower': 99.8, 'volume': 100},
    ])
    pos = {'side': 'LONG', 'entry_price': 100.0}
    should_close, reason, _ = strategy.should_close_position(pos, df)
    assert should_close is False
    assert "TREND_RIDING_LOCK_BULL_INTACT" in reason


def test_trend_riding_lock_holds_short_during_weak_bounce():
    """【空單趨勢鎖倉】在空頭趨勢中，弱勢反彈抽回通道內但收盤壓制在中軌下方，強制 HOLD 嚴禁平空"""
    strategy = TrendRidingStrategy()
    # 空頭走勢，最新 K 棒從 94 反彈收在 96.5，但中軌在 98.2 (受壓)
    df = make_candle_df([
        {'close': 99.0, 'open': 100.0, 'high': 100.0, 'low': 98.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'close': 98.0, 'open': 99.0, 'high': 99.0, 'low': 97.5, 'atr': 1.0, 'kc_middle': 99.7, 'kc_upper': 101.7, 'kc_lower': 97.7},
        {'close': 96.5, 'open': 98.0, 'high': 98.0, 'low': 96.0, 'atr': 1.0, 'kc_middle': 99.3, 'kc_upper': 101.3, 'kc_lower': 97.3},
        {'close': 95.0, 'open': 96.5, 'high': 96.5, 'low': 94.0, 'atr': 1.0, 'kc_middle': 98.8, 'kc_upper': 100.8, 'kc_lower': 96.8},
        {'close': 96.5, 'open': 95.0, 'high': 97.0, 'low': 94.5, 'atr': 1.0, 'kc_middle': 98.2, 'kc_upper': 100.2, 'kc_lower': 96.2, 'volume': 100},
    ])
    pos = {'side': 'SHORT', 'entry_price': 100.0}
    should_close, reason, _ = strategy.should_close_position(pos, df)
    assert should_close is False
    assert "TREND_RIDING_LOCK_BEAR_INTACT" in reason


def test_long_exit_on_structure_broken():
    """多單合法平倉條件 1：收盤實體跌破基準線 (KC 中軌)"""
    strategy = TrendRidingStrategy()
    # 最新 K 棒摜破中軌 101.8，收盤在 101.0
    df = make_candle_df([
        {'close': 101.0, 'open': 100.0, 'high': 101.5, 'low': 100.0, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'close': 102.0, 'open': 101.0, 'high': 102.5, 'low': 101.0, 'atr': 1.0, 'kc_middle': 100.3, 'kc_upper': 102.3, 'kc_lower': 98.3},
        {'close': 103.5, 'open': 102.0, 'high': 104.0, 'low': 102.0, 'atr': 1.0, 'kc_middle': 100.7, 'kc_upper': 102.7, 'kc_lower': 98.7},
        {'close': 104.0, 'open': 103.5, 'high': 104.5, 'low': 103.0, 'atr': 1.0, 'kc_middle': 101.2, 'kc_upper': 103.2, 'kc_lower': 99.2},
        {'close': 101.0, 'open': 103.0, 'high': 103.0, 'low': 100.8, 'atr': 1.0, 'kc_middle': 101.8, 'kc_upper': 103.8, 'kc_lower': 99.8, 'volume': 100},
    ])
    pos = {'side': 'LONG', 'entry_price': 100.0}
    should_close, reason, _ = strategy.should_close_position(pos, df)
    assert should_close is True
    assert "EXIT_LONG_STRUCTURE_BROKEN" in reason


def test_short_exit_on_structure_broken():
    """空單合法平倉條件 1：收盤實體突破站上基準線 (KC 中軌)"""
    strategy = TrendRidingStrategy()
    # 最新 K 棒突破站上中軌 98.2，收盤在 99.0
    df = make_candle_df([
        {'close': 99.0, 'open': 100.0, 'high': 100.0, 'low': 98.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'close': 98.0, 'open': 99.0, 'high': 99.0, 'low': 97.5, 'atr': 1.0, 'kc_middle': 99.7, 'kc_upper': 101.7, 'kc_lower': 97.7},
        {'close': 96.5, 'open': 98.0, 'high': 98.0, 'low': 96.0, 'atr': 1.0, 'kc_middle': 99.3, 'kc_upper': 101.3, 'kc_lower': 97.3},
        {'close': 96.0, 'open': 96.5, 'high': 96.5, 'low': 95.0, 'atr': 1.0, 'kc_middle': 98.8, 'kc_upper': 100.8, 'kc_lower': 96.8},
        {'close': 99.0, 'open': 96.0, 'high': 99.2, 'low': 96.0, 'atr': 1.0, 'kc_middle': 98.2, 'kc_upper': 100.2, 'kc_lower': 96.2, 'volume': 100},
    ])
    pos = {'side': 'SHORT', 'entry_price': 100.0}
    should_close, reason, _ = strategy.should_close_position(pos, df)
    assert should_close is True
    assert "EXIT_SHORT_STRUCTURE_BROKEN" in reason


def test_long_exit_on_blow_off_top():
    """多單合法平倉條件 2：KC 上軌外加速頂部力竭 (爆 2 倍巨量且長上影線 >= 50%)"""
    strategy = TrendRidingStrategy(climax_volume_multiplier=2.0, exhaustion_wick_ratio=0.50)
    # 前面 10 根均量 100，當前 K 棒最高衝到 106 (上軌 103.8 外)，收 103.0，影線 (106-103=3)/(106-102.5=3.5) = 85.7%，成交量 250 (2.5倍)
    records = [{'close': 100.0 + i, 'open': 99.5 + i, 'high': 100.5 + i, 'low': 99.0 + i, 'atr': 1.0, 'kc_middle': 100.0 + i*0.2, 'kc_upper': 102.0 + i*0.2, 'kc_lower': 98.0 + i*0.2, 'volume': 100} for i in range(10)]
    records.append({
        'close': 103.0, 'open': 102.8, 'high': 106.0, 'low': 102.5, 'atr': 1.0,
        'kc_middle': 101.8, 'kc_upper': 103.8, 'kc_lower': 99.8, 'volume': 250
    })
    df = make_candle_df(records)
    pos = {'side': 'LONG', 'entry_price': 100.0}
    should_close, reason, details = strategy.should_close_position(pos, df)
    assert should_close is True
    assert "EXIT_LONG_BLOW_OFF_TOP" in reason
    assert details['volume_ratio'] >= 2.0


def test_short_exit_on_panic_climax_bottom():
    """空單合法平倉條件 2：KC 下軌外恐慌力竭砸盤 (爆 2 倍巨量且長下影線 >= 50%)"""
    strategy = TrendRidingStrategy(climax_volume_multiplier=2.0, exhaustion_wick_ratio=0.50)
    # 前面 10 根均量 100，當前 K 棒最低砸到 94 (下軌 96.2 外)，收 97.0，下影線 (97.0-94.0=3)/(97.5-94.0=3.5) = 85.7%，成交量 250 (2.5倍)
    records = [{'close': 100.0 - i, 'open': 100.5 - i, 'high': 101.0 - i, 'low': 99.5 - i, 'atr': 1.0, 'kc_middle': 100.0 - i*0.2, 'kc_upper': 102.0 - i*0.2, 'kc_lower': 98.0 - i*0.2, 'volume': 100} for i in range(10)]
    records.append({
        'close': 97.0, 'open': 97.2, 'high': 97.5, 'low': 94.0, 'atr': 1.0,
        'kc_middle': 98.2, 'kc_upper': 100.2, 'kc_lower': 96.2, 'volume': 250
    })
    df = make_candle_df(records)
    pos = {'side': 'SHORT', 'entry_price': 100.0}
    should_close, reason, details = strategy.should_close_position(pos, df)
    assert should_close is True
    assert "EXIT_SHORT_PANIC_CLIMAX_BOTTOM" in reason
    assert details['volume_ratio'] >= 2.0


def test_candle_not_closed_ignores_intrabar_spikes():
    """盤中 Tick 插針未收線：100% 忽略，強制等待收線確認"""
    strategy = TrendRidingStrategy()
    df = make_candle_df([
        {'close': 101.0, 'open': 100.0, 'high': 101.5, 'low': 100.0, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'kc_lower': 98.0},
        {'close': 102.0, 'open': 101.0, 'high': 102.5, 'low': 101.0, 'atr': 1.0, 'kc_middle': 100.3, 'kc_upper': 102.3, 'kc_lower': 98.3},
        {'close': 103.5, 'open': 102.0, 'high': 104.0, 'low': 102.0, 'atr': 1.0, 'kc_middle': 100.7, 'kc_upper': 102.7, 'kc_lower': 98.7},
        {'close': 104.0, 'open': 103.5, 'high': 104.5, 'low': 103.0, 'atr': 1.0, 'kc_middle': 101.2, 'kc_upper': 103.2, 'kc_lower': 99.2},
        # 盤中一度摜破中軌 101.8 到 100.5，但 is_closed 為 False
        {'close': 100.5, 'open': 103.0, 'high': 103.0, 'low': 100.0, 'atr': 1.0, 'kc_middle': 101.8, 'kc_upper': 103.8, 'kc_lower': 99.8, 'is_closed': False},
    ])
    pos = {'side': 'LONG', 'entry_price': 100.0}
    should_close, reason, _ = strategy.should_close_position(pos, df)
    assert should_close is False
    assert "CANDLE_NOT_CLOSED" in reason


# ══════════════════════════════════════════════════════════════════════
# 4. 通道收窄中繼再進場測試 (can_reenter_position)
# ══════════════════════════════════════════════════════════════════════
def test_long_squeeze_reentry_pullback_confirmed():
    """多單高位中繼蓄勢：通道收窄期間回踩中軌/均線未破收陽，確認再開多"""
    strategy = TrendRidingStrategy(cooldown_bars=5)
    # 大格局多頭，但最近通道帶寬明顯縮小 (is_squeeze)，價格回踩中軌 101.8 未破收陽 102.5
    records = []
    for i in range(10):
        records.append({
            'close': 100.0 + i * 0.3, 'open': 99.8 + i * 0.3, 'high': 100.5 + i * 0.3, 'low': 99.7 + i * 0.3,
            'atr': 1.0, 'kc_middle': 100.0 + i * 0.2, 'kc_upper': 102.5 + i * 0.1, 'kc_lower': 97.5 + i * 0.3,
            'ma5': 100.5 + i * 0.25, 'ema3': 100.6 + i * 0.25, 'volume': 100
        })
    # 最新 K 棒回踩中軌 101.8 (low 101.9)，收陽線 102.5 (close > open 102.0)
    records.append({
        'close': 102.5, 'open': 102.0, 'high': 102.8, 'low': 101.9, 'atr': 1.0,
        'kc_middle': 101.8, 'kc_upper': 103.0, 'kc_lower': 100.6,
        'ma5': 102.2, 'ema3': 102.3, 'volume': 120
    })
    df = make_candle_df(records)
    can_reenter, reason, details = strategy.can_reenter_position('LONG', df)
    assert can_reenter is True
    assert "SQUEEZE_PULLBACK_CONFIRMED_LONG" in reason
    assert details['reentry_type'] == 'PULLBACK_SUPPORT_CONFIRMED'


def test_long_squeeze_reentry_expansion_breakout_bypasses_cooldown():
    """多單高位中繼擴張突破：通道由窄開始放大且陽線突破 KC 上軌，無條件追多並解除冷卻！"""
    strategy = TrendRidingStrategy(cooldown_bars=5)
    records = []
    for i in range(10):
        records.append({
            'close': 100.0 + i * 0.2, 'open': 99.8 + i * 0.2, 'high': 100.4 + i * 0.2, 'low': 99.6 + i * 0.2,
            'atr': 1.0, 'kc_middle': 100.0 + i * 0.15, 'kc_upper': 101.0 + i * 0.15, 'kc_lower': 99.0 + i * 0.15,
            'ma5': 100.2 + i * 0.15, 'ema3': 100.2 + i * 0.15, 'volume': 100
        })
    # 最新 K 棒放量長陽突破 KC 上軌 103.0，帶寬由 2.0 擴大到 4.0
    records.append({
        'close': 103.5, 'open': 101.8, 'high': 103.8, 'low': 101.5, 'atr': 1.0,
        'kc_middle': 101.5, 'kc_upper': 103.0, 'kc_lower': 99.0,
        'ma5': 102.0, 'ema3': 102.2, 'volume': 300,
        'timestamp': 10000000000.0
    })
    df = make_candle_df(records)
    # 即便剛平倉 1 根 (距離冷卻未滿 5 根)，也必須解除冷卻直接追入！
    can_reenter, reason, details = strategy.can_reenter_position(
        'LONG', df,
        last_closed_action='CLOSE_LONG',
        last_closed_ts=10000000000.0 - 60000.0,  # 僅隔 1 根
        timeframe_ms=60000.0
    )
    assert can_reenter is True
    assert "SQUEEZE_EXPANSION_BREAKOUT_LONG" in reason
    assert details['bypass_cooldown'] is True


def test_short_squeeze_reentry_pullback_confirmed():
    """空單下跌中繼蓄勢：通道收窄期間回抽中軌/均線未破收陰，確認再開空"""
    strategy = TrendRidingStrategy(cooldown_bars=5)
    records = []
    for i in range(10):
        records.append({
            'close': 100.0 - i * 0.3, 'open': 100.2 - i * 0.3, 'high': 100.3 - i * 0.3, 'low': 99.5 - i * 0.3,
            'atr': 1.0, 'kc_middle': 100.0 - i * 0.2, 'kc_upper': 102.5 - i * 0.3, 'kc_lower': 97.5 - i * 0.1,
            'ma5': 99.5 - i * 0.25, 'ema3': 99.4 - i * 0.25, 'volume': 100
        })
    # 最新 K 棒回抽中軌 98.2 (high 98.1)，收陰線 97.5 (close < open 98.0)
    records.append({
        'close': 97.5, 'open': 98.0, 'high': 98.1, 'low': 97.2, 'atr': 1.0,
        'kc_middle': 98.2, 'kc_upper': 99.4, 'kc_lower': 97.0,
        'ma5': 97.8, 'ema3': 97.7, 'volume': 120
    })
    df = make_candle_df(records)
    can_reenter, reason, details = strategy.can_reenter_position('SHORT', df)
    assert can_reenter is True
    assert "SQUEEZE_PULLBACK_CONFIRMED_SHORT" in reason
    assert details['reentry_type'] == 'PULLBACK_RESISTANCE_CONFIRMED'


def test_short_squeeze_reentry_expansion_breakdown_bypasses_cooldown():
    """空單下跌中繼擴張跌破：通道由窄開始放大且陰線跌破 KC 下軌，無條件追空並解除冷卻！"""
    strategy = TrendRidingStrategy(cooldown_bars=5)
    records = []
    for i in range(10):
        records.append({
            'close': 100.0 - i * 0.2, 'open': 100.2 - i * 0.2, 'high': 100.4 - i * 0.2, 'low': 99.6 - i * 0.2,
            'atr': 1.0, 'kc_middle': 100.0 - i * 0.15, 'kc_upper': 101.0 - i * 0.15, 'kc_lower': 99.0 - i * 0.15,
            'ma5': 99.8 - i * 0.15, 'ema3': 99.8 - i * 0.15, 'volume': 100
        })
    # 最新 K 棒放量長陰跌破 KC 下軌 97.0，帶寬由 2.0 擴大到 4.0
    records.append({
        'close': 96.5, 'open': 98.2, 'high': 98.5, 'low': 96.2, 'atr': 1.0,
        'kc_middle': 98.5, 'kc_upper': 101.0, 'kc_lower': 97.0,
        'ma5': 98.0, 'ema3': 97.8, 'volume': 300,
        'timestamp': 10000000000.0
    })
    df = make_candle_df(records)
    can_reenter, reason, details = strategy.can_reenter_position(
        'SHORT', df,
        last_closed_action='CLOSE_SHORT',
        last_closed_ts=10000000000.0 - 60000.0,
        timeframe_ms=60000.0
    )
    assert can_reenter is True
    assert "SQUEEZE_EXPANSION_BREAKDOWN_SHORT" in reason
    assert details['bypass_cooldown'] is True


# ══════════════════════════════════════════════════════════════════════
# 5. 防滑價限價單計算測試 (AntiSlippageOrderExecutor)
# ══════════════════════════════════════════════════════════════════════
def test_anti_slippage_price_calculation():
    """防滑價限價計算：多單賣出折讓 0.2%，空單買入上浮 0.2%"""
    from core.gates.trend_riding_strategy import AntiSlippageOrderExecutor

    # 多單平倉 (賣出)：100.0 * (1 - 0.002) = 99.8
    long_exit_limit = AntiSlippageOrderExecutor.calculate_limit_close_price('LONG', 100.0, offset_pct=0.002)
    assert long_exit_limit == pytest.approx(99.8)

    # 空單平倉 (買入)：100.0 * (1 + 0.002) = 100.2
    short_exit_limit = AntiSlippageOrderExecutor.calculate_limit_close_price('SHORT', 100.0, offset_pct=0.002)
    assert short_exit_limit == pytest.approx(100.2)


# ══════════════════════════════════════════════════════════════════════
# 6. 空頭趨勢持倉鎖定專用平空測試 (should_close_short)
# ══════════════════════════════════════════════════════════════════════
def test_should_close_short_candle_not_closed_holds():
    """未收線鐵律：只要 K 棒未收盤 (is_candle_closed == False)，100% 駁回平空"""
    strategy = TrendRidingStrategy()
    df = make_candle_df([
        {'close': 95.0, 'open': 96.0, 'high': 96.5, 'low': 94.0, 'atr': 1.0, 'kc_middle': 100.0, 'kc_lower': 98.0, 'is_closed': False},
    ])
    should_close, reason, details = strategy.should_close_short({}, df, quote=95.0, is_candle_closed=False)
    assert should_close is False
    assert "CANDLE_NOT_CLOSED" in reason


def test_should_close_short_bear_trend_riding_blocks_minor_pullback():
    """空頭趨勢中持倉鎖定：盤中小綠棒反抽或微幅反彈，只要收盤未站上中軌，強制 HOLD 嚴禁平空！"""
    strategy = TrendRidingStrategy()
    # 構造單邊主跌浪背景，KC 中軌向下傾斜，最近 5 根均在中軌下方
    records = []
    for i in range(5):
        records.append({
            'close': 95.0 - i * 0.5, 'open': 95.5 - i * 0.5, 'high': 96.0 - i * 0.5, 'low': 94.5 - i * 0.5,
            'atr': 1.0, 'kc_middle': 98.0 - i * 0.4, 'kc_upper': 100.0 - i * 0.4, 'kc_lower': 96.0 - i * 0.4,
            'ma5': 96.0 - i * 0.4, 'ema3': 95.8 - i * 0.4, 'volume': 100, 'is_closed': True
        })
    # 最新 K 棒：出現小綠棒反抽（收 93.5，開 93.0，微幅反彈 0.5U），但仍遠在中軌 96.0 下方
    records.append({
        'close': 93.5, 'open': 93.0, 'high': 93.8, 'low': 92.8,
        'atr': 1.0, 'kc_middle': 96.0, 'kc_upper': 98.0, 'kc_lower': 94.0,
        'ma5': 94.5, 'ema3': 94.0, 'volume': 120, 'is_closed': True
    })
    df = make_candle_df(records)

    should_close, reason, details = strategy.should_close_short({}, df, quote=93.5, is_candle_closed=True)
    assert should_close is False
    assert "BEAR_TREND_LOCK_INTACT" in reason
    assert details['is_bear_trend'] is True


def test_should_close_short_exits_on_structure_broken():
    """唯二平倉條件 1：結構被破 (Trend Invalidation)：收盤價實體突破並站穩 KC 中軌，確認平空離場"""
    strategy = TrendRidingStrategy()
    records = []
    for i in range(5):
        records.append({
            'close': 95.0, 'open': 95.5, 'high': 96.0, 'low': 94.5,
            'atr': 1.0, 'kc_middle': 97.0, 'kc_upper': 99.0, 'kc_lower': 95.0,
            'ma5': 96.0, 'ema3': 95.8, 'volume': 100, 'is_closed': True
        })
    # 最新 K 棒強勢大陽線收在 98.0，實體收盤突破並站穩 KC 中軌 97.0
    records.append({
        'close': 98.0, 'open': 95.5, 'high': 98.5, 'low': 95.0,
        'atr': 1.0, 'kc_middle': 97.0, 'kc_upper': 99.0, 'kc_lower': 95.0,
        'ma5': 97.0, 'ema3': 97.5, 'volume': 200, 'is_closed': True
    })
    df = make_candle_df(records)

    should_close, reason, details = strategy.should_close_short({}, df, quote=98.0, is_candle_closed=True)
    assert should_close is True
    assert "EXIT_SHORT_STRUCTURE_BROKEN" in reason


def test_should_close_short_exits_on_panic_climax_bottom():
    """唯二平倉條件 2：極端恐慌天量插針：KC 下軌外超賣 + 2.5 倍天量 + 超長下影線 >= 實體 2 倍，平空收割"""
    strategy = TrendRidingStrategy()
    records = []
    for i in range(15):
        records.append({
            'close': 95.0 - i * 0.3, 'open': 95.3 - i * 0.3, 'high': 95.5 - i * 0.3, 'low': 94.5 - i * 0.3,
            'atr': 1.0, 'kc_middle': 97.0 - i * 0.2, 'kc_upper': 99.0 - i * 0.2, 'kc_lower': 95.0 - i * 0.2,
            'ma5': 96.0 - i * 0.2, 'ema3': 95.8 - i * 0.2, 'volume': 100, 'is_closed': True
        })
    # 最新 K 棒：跌破下軌 (low 90.0 < lower 92.0)，爆出 300 巨量 (均量 100，達 3.0 倍 > 2.5 倍)
    # 下影線：open 91.8, close 91.5 (實體 0.3), low 90.0 -> 下影線 1.5 >= 2.0 * 實體
    records.append({
        'close': 91.5, 'open': 91.8, 'high': 92.0, 'low': 90.0,
        'atr': 1.0, 'kc_middle': 94.0, 'kc_upper': 96.0, 'kc_lower': 92.0,
        'ma5': 93.0, 'ema3': 92.5, 'volume': 300, 'is_closed': True
    })
    df = make_candle_df(records)

    should_close, reason, details = strategy.should_close_short({}, df, quote=91.5, is_candle_closed=True)
    assert should_close is True
    assert "EXIT_SHORT_PANIC_CLIMAX_BOTTOM" in reason
    assert details['volume_ratio'] >= 2.5


def test_should_close_short_blocks_non_climax_wick_holds():
    """非天量之普通下影線：雖然有下影線但成交量僅為普通均量 (無 2.5 倍天量)，強制 HOLD 絕不平空！"""
    strategy = TrendRidingStrategy()
    records = []
    for i in range(15):
        records.append({
            'close': 95.0 - i * 0.3, 'open': 95.3 - i * 0.3, 'high': 95.5 - i * 0.3, 'low': 94.5 - i * 0.3,
            'atr': 1.0, 'kc_middle': 97.0 - i * 0.2, 'kc_upper': 99.0 - i * 0.2, 'kc_lower': 95.0 - i * 0.2,
            'ma5': 96.0 - i * 0.2, 'ema3': 95.8 - i * 0.2, 'volume': 100, 'is_closed': True
        })
    # 最新 K 棒：雖然 low 91.0，但 volume 僅 110 (無 2.5 倍量)
    records.append({
        'close': 91.5, 'open': 91.8, 'high': 92.0, 'low': 91.0,
        'atr': 1.0, 'kc_middle': 94.0, 'kc_upper': 96.0, 'kc_lower': 92.0,
        'ma5': 93.0, 'ema3': 92.5, 'volume': 110, 'is_closed': True
    })
    df = make_candle_df(records)

    should_close, reason, details = strategy.should_close_short({}, df, quote=91.5, is_candle_closed=True)
    assert should_close is False
    assert "BEAR_TREND_LOCK_INTACT" in reason


# ══════════════════════════════════════════════════════════════════════
# 7. 頂底破位即時反手開倉測試 (evaluate_swing_entry & FLIP)
# ══════════════════════════════════════════════════════════════════════
def test_swing_entry_bull_breakout_and_flip():
    """底部破位即時開多 / 平空反手開多 (FLIP_SHORT_TO_LONG)"""
    strategy = TrendRidingStrategy()
    df = make_candle_df([
        {'close': 97.0, 'open': 97.5, 'high': 97.8, 'low': 96.8, 'atr': 1.0, 'kc_middle': 98.0, 'kc_upper': 100.0, 'kc_lower': 96.0, 'ma5': 97.2, 'ema3': 97.0, 'is_closed': True},
        {'close': 97.5, 'open': 97.0, 'high': 97.8, 'low': 96.9, 'atr': 1.0, 'kc_middle': 98.0, 'kc_upper': 100.0, 'kc_lower': 96.0, 'ma5': 97.4, 'ema3': 97.5, 'is_closed': True},
        # 當前 K 棒：大陽線突破站上 KC 中軌 98.0 (收 98.5 > open 97.5)，短均線 ema3 達 98.2 > 98.0
        {'close': 98.5, 'open': 97.5, 'high': 98.8, 'low': 97.4, 'atr': 1.0, 'kc_middle': 98.0, 'kc_upper': 100.0, 'kc_lower': 96.0, 'ma5': 98.0, 'ema3': 98.2, 'is_closed': True},
    ])

    # 空倉時發出 BUY_LONG
    sig, reason, _ = strategy.evaluate_swing_entry({}, df)
    assert sig == 'BUY_LONG'

    # 持空單時觸發 FLIP_SHORT_TO_LONG 反手開多！
    sig_flip, reason_flip, _ = strategy.evaluate_swing_entry({'side': 'SHORT'}, df)
    assert sig_flip == 'FLIP_SHORT_TO_LONG'
    assert "反手開多" in reason_flip


def test_swing_entry_bear_breakdown_and_flip():
    """頂部破位即時開空 / 平多反手開空 (FLIP_LONG_TO_SHORT)"""
    strategy = TrendRidingStrategy()
    df = make_candle_df([
        {'close': 103.0, 'open': 102.5, 'high': 103.2, 'low': 102.0, 'atr': 1.0, 'kc_middle': 102.0, 'kc_upper': 104.0, 'kc_lower': 100.0, 'ma5': 102.8, 'ema3': 103.0, 'is_closed': True},
        {'close': 102.5, 'open': 103.0, 'high': 103.0, 'low': 102.2, 'atr': 1.0, 'kc_middle': 102.0, 'kc_upper': 104.0, 'kc_lower': 100.0, 'ma5': 102.6, 'ema3': 102.5, 'is_closed': True},
        # 當前 K 棒：大陰線跌破 KC 中軌 102.0 (收 101.2 < open 102.5)，短均線 ema3 跌至 101.6 < 102.0
        {'close': 101.2, 'open': 102.5, 'high': 102.5, 'low': 101.0, 'atr': 1.0, 'kc_middle': 102.0, 'kc_upper': 104.0, 'kc_lower': 100.0, 'ma5': 101.8, 'ema3': 101.6, 'is_closed': True},
    ])

    # 空倉時發出 SELL_SHORT
    sig, reason, _ = strategy.evaluate_swing_entry({}, df)
    assert sig == 'SELL_SHORT'

    # 持多單時觸發 FLIP_LONG_TO_SHORT 反手開空！
    sig_flip, reason_flip, _ = strategy.evaluate_swing_entry({'side': 'LONG'}, df)
    assert sig_flip == 'FLIP_LONG_TO_SHORT'
    assert "反手開空" in reason_flip


def test_should_close_long_holds_in_bull_trend():
    """多單趨勢鎖倉：回踩未破中軌，強制 HOLD 嚴禁平多"""
    strategy = TrendRidingStrategy()
    records = []
    for i in range(5):
        records.append({
            'close': 102.0 + i * 0.5, 'open': 101.5 + i * 0.5, 'high': 102.5 + i * 0.5, 'low': 101.2 + i * 0.5,
            'atr': 1.0, 'kc_middle': 101.0 + i * 0.4, 'kc_upper': 103.0 + i * 0.4, 'kc_lower': 99.0 + i * 0.4,
            'ma5': 102.0 + i * 0.4, 'ema3': 102.2 + i * 0.4, 'volume': 100, 'is_closed': True
        })
    # 最新 K 棒：小陰線回踩 104.2 (open 104.5)，但仍遠在中軌 103.0 之上
    records.append({
        'close': 104.2, 'open': 104.5, 'high': 104.8, 'low': 104.0,
        'atr': 1.0, 'kc_middle': 103.0, 'kc_upper': 105.0, 'kc_lower': 101.0,
        'ma5': 103.8, 'ema3': 104.0, 'volume': 100, 'is_closed': True
    })
    df = make_candle_df(records)

    should_close, reason, _ = strategy.should_close_long({}, df, quote=104.2, is_candle_closed=True)
    assert should_close is False
    assert "BULL_TREND_LOCK_INTACT" in reason



