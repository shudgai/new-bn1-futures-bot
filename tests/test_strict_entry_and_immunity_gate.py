import pytest
import pandas as pd
from core.gates.strict_entry_and_immunity_gate import StrictEntryAndImmunityGate

def test_strict_entry_cooling_period():
    gate = StrictEntryAndImmunityGate(cooldown_bars=3)
    gate.record_exit(current_bar_index=10)

    # 剛平倉後 1~3 根內嘗試開倉，必須 100% 被 VETO
    df = pd.DataFrame([
        {'close': 100, 'ma5': 100, 'ma15': 99},
        {'close': 101, 'ma5': 101, 'ma15': 99},
        {'close': 102, 'ma5': 102, 'ma15': 99},
        {'close': 103, 'ma5': 103, 'ma15': 99},
        {'close': 104, 'ma5': 104, 'ma15': 99},
        {'close': 105, 'ma5': 105, 'ma15': 99},
    ])
    can_open, reason = gate.can_open('LONG', df, current_bar_index=12)
    assert can_open is False
    assert "平倉冷卻中" in reason

    # 超過 3 根冷卻期 (10 + 4 = 14)
    can_open_after, reason_after = gate.can_open('LONG', df, current_bar_index=14)
    assert can_open_after is True


def test_strict_entry_slope_blocks_flat_and_arc_top():
    gate = StrictEntryAndImmunityGate(min_slope_pct=0.0010)

    # 構造走平 / 弧頂橫盤：MA5 幾乎沒有拉升 (Slope <= 0.0010)
    # iloc[-2] 為收盤棒, iloc[-4] 為前兩根
    # price = 100, ma5_curr = 100.05, ma5_prev2 = 100.00 -> slope = 0.05 / 100 = 0.0005 <= 0.0010
    df_flat = pd.DataFrame([
        {'close': 99.0, 'ma5': 99.8, 'ma15': 98.0},
        {'close': 99.5, 'ma5': 100.0, 'ma15': 98.2},
        {'close': 100.0, 'ma5': 100.02, 'ma15': 98.5},
        {'close': 100.1, 'ma5': 100.05, 'ma15': 98.8},
        {'close': 100.2, 'ma5': 100.06, 'ma15': 99.0}, # live bar
    ])
    can_open, reason = gate.can_open('LONG', df_flat, current_bar_index=20)
    assert can_open is False
    assert "走平或傾斜不足" in reason
    assert "頂部接盤" in reason

    # 構造強勁拉升多單：slope = (102.5 - 100.0) / 102.0 = 2.5 / 102 = 0.0245 > 0.0010
    df_strong_long = pd.DataFrame([
        {'close': 99.0, 'ma5': 99.5, 'ma15': 98.0},
        {'close': 100.0, 'ma5': 100.0, 'ma15': 98.5},
        {'close': 101.0, 'ma5': 101.2, 'ma15': 99.0},
        {'close': 102.0, 'ma5': 102.5, 'ma15': 99.5},
        {'close': 102.5, 'ma5': 103.0, 'ma15': 99.8},
    ])
    can_open_long, reason_long = gate.can_open('LONG', df_strong_long, current_bar_index=20)
    assert can_open_long is True
    assert reason_long == "PASSED_ENTRY_LONG"


def test_strict_entry_slope_blocks_flat_and_bottom_short():
    gate = StrictEntryAndImmunityGate(min_slope_pct=0.0010)

    # 構造走平空單：slope = (99.98 - 100.0) / 100.0 = -0.0002 >= -0.0010 (下殺動能不足)
    df_flat_short = pd.DataFrame([
        {'close': 101.0, 'ma5': 100.5, 'ma15': 102.0},
        {'close': 100.5, 'ma5': 100.0, 'ma15': 101.8},
        {'close': 100.2, 'ma5': 99.99, 'ma15': 101.5},
        {'close': 100.0, 'ma5': 99.98, 'ma15': 101.2},
        {'close': 99.8, 'ma5': 99.95, 'ma15': 101.0},
    ])
    can_open, reason = gate.can_open('SHORT', df_flat_short, current_bar_index=20)
    assert can_open is False
    assert "走平或傾斜不足" in reason
    assert "底部追空" in reason

    # 構造強勁俯衝空單：slope = (97.0 - 100.0) / 97.0 = -3.0 / 97.0 = -0.0309 < -0.0010
    df_strong_short = pd.DataFrame([
        {'close': 101.0, 'ma5': 101.0, 'ma15': 102.0},
        {'close': 100.0, 'ma5': 100.0, 'ma15': 101.8},
        {'close': 98.5, 'ma5': 98.5, 'ma15': 101.5},
        {'close': 97.0, 'ma5': 97.0, 'ma15': 101.0},
        {'close': 96.5, 'ma5': 96.0, 'ma15': 100.5},
    ])
    can_open_short, reason_short = gate.can_open('SHORT', df_strong_short, current_bar_index=20)
    assert can_open_short is True
    assert reason_short == "PASSED_ENTRY_SHORT"


def test_check_long_entry_gate_five_defense_lines():
    from core.gates.strict_entry_and_immunity_gate import check_long_entry_gate, check_short_entry_gate

    # 1. 測試防線 1: KC 中軌下行 (Slope < 0) 100% 阻斷
    df_kc_down = pd.DataFrame([
        {'open': 99.0, 'high': 100.5, 'low': 98.8, 'close': 100.0, 'ma5': 99.5, 'atr': 1.0, 'kc_upper': 100.2, 'kc_middle': 99.5, 'kc_middle_slope': -0.05},
        {'open': 100.0, 'high': 102.5, 'low': 99.9, 'close': 102.3, 'ma5': 101.5, 'atr': 1.0, 'kc_upper': 101.0, 'kc_middle': 99.0, 'kc_middle_slope': -0.10},
    ])
    ok, reason = check_long_entry_gate(df_kc_down, current_bar_idx=10, last_exit_bar_idx=5)
    assert ok is False
    assert reason == "BLOCKED_KC_MIDDLE_SLOPE_DOWN"

    # 2. 測試防線 2: MA5 未抬頭或未站上 MA5
    df_ma5_not_up = pd.DataFrame([
        {'open': 99.0, 'high': 100.5, 'low': 98.8, 'close': 100.0, 'ma5': 102.0, 'atr': 1.0, 'kc_upper': 100.2, 'kc_middle': 99.5, 'kc_middle_slope': 0.1},
        {'open': 100.0, 'high': 101.5, 'low': 99.9, 'close': 101.2, 'ma5': 101.8, 'atr': 1.0, 'kc_upper': 101.0, 'kc_middle': 99.6, 'kc_middle_slope': 0.1},
    ])
    ok, reason = check_long_entry_gate(df_ma5_not_up, current_bar_idx=10, last_exit_bar_idx=5)
    assert ok is False
    assert reason == "BLOCKED_MA5_NOT_UPWARD"

    # 3. 測試防線 3: 高檔插針出貨 (長上影線攔截)
    # body = 101.5 - 100.0 = 1.5, high = 103.0 -> upper_wick = 1.5 > 0.6 * body (0.9) and > 0.3 * atr (0.3)
    df_upper_wick = pd.DataFrame([
        {'open': 99.0, 'high': 100.5, 'low': 98.8, 'close': 100.0, 'ma5': 99.5, 'atr': 1.0, 'kc_upper': 100.2, 'kc_middle': 99.5, 'kc_middle_slope': 0.1},
        {'open': 100.0, 'high': 103.0, 'low': 99.9, 'close': 101.5, 'ma5': 101.0, 'atr': 1.0, 'kc_upper': 101.0, 'kc_middle': 99.6, 'kc_middle_slope': 0.1},
    ])
    ok, reason = check_long_entry_gate(df_upper_wick, current_bar_idx=10, last_exit_bar_idx=5)
    assert ok is False
    assert reason == "BLOCKED_BY_EXCESSIVE_UPPER_WICK"

    # 4. 測試防線 4: 冷卻期阻斷普通兩根突破
    # prev_bar close 101.5 > upper 101.0, bar_t close 102.0 > upper 101.5, 但 body = 0.5 < 1.0 atr
    df_normal_in_cooldown = pd.DataFrame([
        {'open': 100.0, 'high': 101.6, 'low': 99.8, 'close': 101.5, 'ma5': 100.5, 'atr': 1.0, 'kc_upper': 101.0, 'kc_middle': 99.5, 'kc_middle_slope': 0.1},
        {'open': 101.5, 'high': 102.1, 'low': 101.4, 'close': 102.0, 'ma5': 101.2, 'atr': 1.0, 'kc_upper': 101.5, 'kc_middle': 99.6, 'kc_middle_slope': 0.1},
    ])
    # 剛平倉 (current=10, last_exit=9 -> 差 1 根 <= 3)
    ok, reason = check_long_entry_gate(df_normal_in_cooldown, current_bar_idx=10, last_exit_bar_idx=9, min_cooldown_bars=3)
    assert ok is False
    assert reason == "BLOCKED_BY_COOLDOWN"

    # 5. 測試防線 4 豁免: 觸發「強動能脈衝 (Super Impulse)」成功打斷冷卻期放行！
    # body = 103.5 - 101.0 = 2.5 >= 1.0 atr (1.0), high = 103.8 (wick = 0.3 <= 0.4 * 2.5 = 1.0), close 103.5 > upper 101.5
    df_super_impulse = pd.DataFrame([
        {'open': 100.0, 'high': 101.2, 'low': 99.8, 'close': 101.0, 'ma5': 100.5, 'atr': 1.0, 'kc_upper': 101.0, 'kc_middle': 99.5, 'kc_middle_slope': 0.1},
        {'open': 101.0, 'high': 103.8, 'low': 100.9, 'close': 103.5, 'ma5': 102.5, 'atr': 1.0, 'kc_upper': 101.5, 'kc_middle': 99.7, 'kc_middle_slope': 0.2},
    ])
    ok, reason = check_long_entry_gate(df_super_impulse, current_bar_idx=10, last_exit_bar_idx=9, min_cooldown_bars=3)
    assert ok is True
    assert reason == "ENTRY_APPROVED"

    # 6. 測試空單強動能豁免
    df_super_impulse_short = pd.DataFrame([
        {'open': 101.0, 'high': 101.2, 'low': 99.8, 'close': 100.0, 'ma5': 100.5, 'atr': 1.0, 'kc_lower': 100.5, 'kc_middle': 101.5, 'kc_middle_slope': -0.1},
        {'open': 100.0, 'high': 100.1, 'low': 97.2, 'close': 97.5, 'ma5': 98.5, 'atr': 1.0, 'kc_lower': 99.5, 'kc_middle': 101.2, 'kc_middle_slope': -0.2},
    ])
    ok_s, reason_s = check_short_entry_gate(df_super_impulse_short, current_bar_idx=10, last_exit_bar_idx=9, min_cooldown_bars=3)
    assert ok_s is True
    assert reason_s == "ENTRY_APPROVED"

