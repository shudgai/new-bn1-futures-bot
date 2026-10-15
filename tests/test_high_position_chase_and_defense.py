"""Unit tests for HighPositionChaseAndDefenseFilter (Anti-Top Chase & Tiered Defense Exit)"""
import pytest
import pandas as pd
from core.gates.high_position_chase_and_defense_filter import HighPositionChaseAndDefenseFilter


def test_cooldown_blocks_immediate_reentry():
    """改進 A-1：平倉冷卻期：平多後不足 5 根 K 棒，禁止在同一個頂部連續進場"""
    filter_gate = HighPositionChaseAndDefenseFilter(cooldown_bars=5)

    df = pd.DataFrame([
        {'close': 105.0, 'open': 104.0, 'high': 105.5, 'low': 103.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'ma5': 104.0, 'timestamp': 1700000120000}, # 2分鐘後 (2 bars)
    ])

    can_enter, reason, details = filter_gate.validate_long_entry(
        df=df,
        quote=105.0,
        last_closed_action='CLOSE_LONG',
        last_closed_ts=1700000000000, # 基準平倉時間
        timeframe_ms=60_000.0,
    )

    assert can_enter is False
    assert "LONG_COOLDOWN_ACTIVE" in reason
    assert details['bars_since_close'] == 2.0


def test_overextended_bias_blocks_floating_chase():
    """改進 A-2：中軌正乖離過大：價格懸空在 KC 上軌外遠離中軌時，禁止追多"""
    filter_gate = HighPositionChaseAndDefenseFilter(cooldown_bars=5, max_bias_atr=1.2)

    # 中軌 100，ATR 1.0，現價 102.5 -> 乖離 2.5 ATR > 1.2 ATR
    df = pd.DataFrame([
        {'close': 102.5, 'open': 102.0, 'high': 103.0, 'low': 101.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'ma5': 102.0},
    ])

    can_enter, reason, details = filter_gate.validate_long_entry(
        df=df, quote=102.5
    )

    assert can_enter is False
    assert "OVEREXTENDED_BIAS_TOO_HIGH" in reason
    assert details['bias_atr'] > 1.2


def test_floating_without_pullback_blocks_entry():
    """改進 A-3：回踩健康度：近 3 根從未回踩測試過 EMA3/MA5，浮空追多一票否決"""
    filter_gate = HighPositionChaseAndDefenseFilter(cooldown_bars=5, max_bias_atr=2.0)

    # 現價 101.5，近 3 根最低價都在 101.2 以上，EMA3 為 100.9，MA5 為 100.4，從未摸到
    df = pd.DataFrame([
        {'close': 101.0, 'open': 101.0, 'high': 101.3, 'low': 101.0, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'ma5': 100.0, 'ema3': 100.5},
        {'close': 101.3, 'open': 101.1, 'high': 101.5, 'low': 101.1, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'ma5': 100.2, 'ema3': 100.7},
        {'close': 101.5, 'open': 101.3, 'high': 101.6, 'low': 101.2, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'ma5': 100.4, 'ema3': 100.9},
    ])

    can_enter, reason, details = filter_gate.validate_long_entry(
        df=df, quote=101.5
    )

    assert can_enter is False
    assert "FLOATING_CHASE_WITHOUT_PULLBACK" in reason


def test_top_stalling_upper_wick_alone_does_not_block_strong_trend_entry():
    """強勢趨勢中，單根長上影線不構成已確認的耗竭否決。"""
    filter_gate = HighPositionChaseAndDefenseFilter(cooldown_bars=5, max_bias_atr=3.0, wick_exhaustion_ratio=0.50)

    # Bar[-1] 留長上影線：High 105.0, Open 102.0, Close 102.5, Low 101.8
    # Range = 3.2, Upper Wick = 105.0 - 102.5 = 2.5 (2.5 / 3.2 = 78.1% > 50%)
    df = pd.DataFrame([
        {'close': 101.0, 'open': 100.0, 'high': 101.5, 'low': 99.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'ma5': 100.5, 'ema3': 100.5},
        {'close': 102.5, 'open': 102.0, 'high': 105.0, 'low': 101.8, 'atr': 1.0, 'kc_middle': 100.0, 'kc_upper': 102.0, 'ma5': 101.0, 'ema3': 101.0},
    ])

    can_enter, reason, details = filter_gate.validate_long_entry(
        df=df, quote=102.5, strong_trend=True
    )

    assert can_enter is True
    assert reason == "PASS: HEALTHY_LONG_ENTRY"


def test_tiered_defense_high_chase_exits_on_entry_bar_low_pierced():
    """改進 B-1：高位追單專用防守：跌破進場當根 K 棒 Low 立即砍倉避險"""
    filter_gate = HighPositionChaseAndDefenseFilter()

    position = {
        'side': 'LONG',
        'entry_price': 105.0,
        'entry_bar_low': 104.2,
        'is_high_chase': True, # 標記為高位追單
    }

    df = pd.DataFrame([
        {'close': 104.0, 'open': 104.5, 'high': 105.0, 'low': 103.8, 'kc_middle': 100.0, 'kc_upper': 103.0, 'ma5': 104.2, 'ema3': 104.4, 'atr': 1.0},
    ])

    # 現價 104.0 跌破進場棒 Low (104.2)
    should_exit, reason, details = filter_gate.evaluate_tiered_defense_exit(
        position=position, df=df, quote=104.0
    )

    assert should_exit is True
    assert reason == "EXIT_HIGH_CHASE_PIERCE_ENTRY_LOW"


def test_tiered_defense_high_chase_exits_on_close_below_ema3():
    """改進 B-2：高位追單專用防守：實體收盤摜破 3 EMA / 5 EMA 立即硬止損，絕不死等 KC 中軌！"""
    filter_gate = HighPositionChaseAndDefenseFilter()

    position = {
        'side': 'LONG',
        'entry_price': 105.0,
        'entry_bar_low': 103.0,
        'is_high_chase': True,
    }

    # 收盤價 103.8 跌破 3 EMA (104.2) 且收陰
    df = pd.DataFrame([
        {'close': 103.8, 'open': 104.6, 'high': 104.8, 'low': 103.5, 'kc_middle': 100.0, 'kc_upper': 103.0, 'ma5': 104.0, 'ema3': 104.2, 'atr': 1.0, 'is_closed': True},
    ])

    should_exit, reason, details = filter_gate.evaluate_tiered_defense_exit(
        position=position, df=df, quote=103.8
    )

    assert should_exit is True
    assert "EXIT_HIGH_CHASE_CLOSE_BREAK_EMA" in reason


def test_tiered_defense_breakout_initial_holds_until_kc_middle():
    """改進 B-3：起爆底倉具備成本優勢，仍寬鬆防守至 KC 中軌"""
    filter_gate = HighPositionChaseAndDefenseFilter()

    position = {
        'side': 'LONG',
        'entry_price': 100.5, # 起爆點進場
        'entry_bar_low': 99.8,
        'is_high_chase': False,
    }

    # 價格回踩至 101.0，高於 KC 中軌 100.0
    df = pd.DataFrame([
        {'close': 101.0, 'open': 101.5, 'high': 102.0, 'low': 100.8, 'kc_middle': 100.0, 'kc_upper': 103.0, 'ma5': 101.5, 'ema3': 101.8, 'atr': 1.0, 'is_closed': True},
    ])

    should_exit, reason, details = filter_gate.evaluate_tiered_defense_exit(
        position=position, df=df, quote=101.0
    )

    # 起爆底倉未破中軌，繼續持倉，不被微幅回調震出場！
    assert should_exit is False


# ══════════════════════════════════════════════════════════════════════
# 【空單硬性防護測試：禁止底部插針放量追空與平空後反手賣空】
# ══════════════════════════════════════════════════════════════════════

def test_short_cooldown_blocks_immediate_reentry():
    """空單規則 3：平空後強制冷卻：剛觸發平空離場後，必須強制等待 5 根 K 棒，禁止連續追空"""
    filter_gate = HighPositionChaseAndDefenseFilter(cooldown_bars=5)

    df = pd.DataFrame([
        {'close': 95.0, 'open': 96.0, 'high': 96.5, 'low': 94.5, 'atr': 1.0, 'kc_middle': 100.0, 'kc_lower': 98.0, 'ma5': 96.0, 'timestamp': 1700000120000}, # 2分鐘後 (2 bars)
    ])

    can_enter, reason, details = filter_gate.validate_short_entry(
        df=df,
        quote=95.0,
        last_closed_action='CLOSE_SHORT',
        last_closed_ts=1700000000000, # 基準平倉時間
        timeframe_ms=60_000.0,
    )

    assert can_enter is False
    assert "SHORT_COOLDOWN_ACTIVE" in reason
    assert details['bars_since_close'] == 2.0


def test_short_climax_pinbar_alone_does_not_block_entry():
    """單根長下影線在沒有確認訊號時不單獨阻止開空。"""
    filter_gate = HighPositionChaseAndDefenseFilter(cooldown_bars=5, max_bias_atr=10.0, wick_exhaustion_ratio=0.50)

    # 前一根有長下影線，但量能及後續收線並未形成確認。
    df = pd.DataFrame([
        {'close': 94.5, 'open': 95.0, 'high': 96.0, 'low': 91.0, 'atr': 1.0, 'kc_middle': 95.0, 'kc_lower': 93.0, 'ma5': 95.0, 'ema3': 95.0},
        {'close': 94.0, 'open': 94.2, 'high': 94.8, 'low': 93.8, 'atr': 1.0, 'kc_middle': 95.0, 'kc_lower': 93.0, 'ma5': 94.5, 'ema3': 94.2},
    ])

    can_enter, reason, details = filter_gate.validate_short_entry(
        df=df, quote=94.0
    )

    assert can_enter is True
    assert reason == "PASS: HEALTHY_SHORT_ENTRY"


def test_short_overextended_negative_bias_blocks_floor_chase():
    """空單規則 1：KC 下軌外負乖離過大（禁止地板追空）：超賣恐慌區嚴禁新開空單"""
    filter_gate = HighPositionChaseAndDefenseFilter(cooldown_bars=5, max_bias_atr=1.2)

    # 中軌 100，下軌 98，ATR 1.0，現價 97.5 -> 負乖離 2.5 ATR > 1.2 ATR
    df = pd.DataFrame([
        {'close': 97.5, 'open': 97.8, 'high': 98.0, 'low': 97.2, 'atr': 1.0, 'kc_middle': 100.0, 'kc_lower': 98.0, 'ma5': 98.0, 'ema3': 98.0},
    ])

    can_enter, reason, details = filter_gate.validate_short_entry(
        df=df, quote=97.5
    )

    assert can_enter is False
    assert "OVEREXTENDED_NEGATIVE_BIAS_TOO_HIGH" in reason
    assert details['bias_atr'] > 1.2


def test_short_floating_without_pullback_blocks_entry():
    """空單規則 1-2：回抽測試受阻收陰檢驗：近 3 根未曾回抽測試 EMA3/MA5 受阻，浮空追空一票否決"""
    filter_gate = HighPositionChaseAndDefenseFilter(cooldown_bars=5, max_bias_atr=5.0, max_bias_pct=0.10)

    # 現價 99.0 在下軌內但低於 MA5，近 3 根最高價均未測到均線，避免觸發外軌突破豁免。
    # 且每根 K 棒為飽滿實體陰線（無長下影線）
    df = pd.DataFrame([
        {'close': 99.2, 'open': 99.5, 'high': 99.5, 'low': 99.1, 'atr': 1.0, 'kc_middle': 100.0, 'kc_lower': 98.0, 'ma5': 100.0, 'ema3': 99.8},
        {'close': 99.1, 'open': 99.3, 'high': 99.3, 'low': 99.0, 'atr': 1.0, 'kc_middle': 100.0, 'kc_lower': 98.0, 'ma5': 100.0, 'ema3': 99.8},
        {'close': 99.0, 'open': 99.2, 'high': 99.2, 'low': 98.95, 'atr': 1.0, 'kc_middle': 100.0, 'kc_lower': 98.0, 'ma5': 100.0, 'ema3': 99.8},
    ])

    can_enter, reason, details = filter_gate.validate_short_entry(
        df=df, quote=99.0
    )

    assert can_enter is False
    assert "FLOATING_SHORT_CHASE_WITHOUT_PULLBACK" in reason


def test_short_healthy_entry_passes():
    """空單健康進場：回抽測試 EMA3/MA5 受阻收陰，無長下影線且冷卻完畢，允許順勢開空"""
    filter_gate = HighPositionChaseAndDefenseFilter(cooldown_bars=5, max_bias_atr=2.0)

    # 近 3 根最高價摸到 EMA3 / MA5，且當前收陰 98.0 受阻，中軌下行。
    df = pd.DataFrame([
        {'close': 98.6, 'open': 98.0, 'high': 98.8, 'low': 97.9, 'atr': 1.0, 'kc_middle': 99.7, 'kc_lower': 97.0, 'ma5': 98.8, 'ema3': 98.6},
        {'close': 98.4, 'open': 98.6, 'high': 98.7, 'low': 98.2, 'atr': 1.0, 'kc_middle': 99.4, 'kc_lower': 97.0, 'ma5': 98.6, 'ema3': 98.5},
        {'close': 98.0, 'open': 98.4, 'high': 98.5, 'low': 97.8, 'atr': 1.0, 'kc_middle': 99.2, 'kc_lower': 97.0, 'ma5': 98.5, 'ema3': 98.4},
    ])

    can_enter, reason, details = filter_gate.validate_short_entry(
        df=df, quote=98.0
    )

    assert can_enter is True
    assert "HEALTHY_SHORT_ENTRY" in reason
