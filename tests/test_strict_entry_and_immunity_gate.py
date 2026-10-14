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
