import pytest
import pandas as pd
from core.gates.meme_shared_protection_gate import MemeSharedProtectionGate


def test_short_blocked_when_ma5_flat():
    """當 MA5 走平 (Slope >= -0.0001) 時，做空必須 100% 一票否決 (REJECT: MA5_SLOPE_FLAT_FOR_SHORT)"""
    # ma5_curr = 0.04000, ma5_prev = 0.04000 -> slope = 0.0 >= -0.0001
    df = pd.DataFrame([
        {'open': 0.0405, 'close': 0.0400, 'high': 0.0408, 'low': 0.0398, 'ma5': 0.04000, 'ma15': 0.04050, 'atr': 0.0005, 'kc_middle': 0.0403, 'kc_upper': 0.0415, 'kc_lower': 0.0390, 'is_closed': True},
        {'open': 0.0400, 'close': 0.0388, 'high': 0.0401, 'low': 0.0385, 'ma5': 0.04000, 'ma15': 0.04050, 'atr': 0.0005, 'kc_middle': 0.0403, 'kc_upper': 0.0415, 'kc_lower': 0.0390, 'is_closed': True},
    ])

    passed, reason = MemeSharedProtectionGate.validate_short_ma_condition(df)
    assert not passed
    assert "MA5_SLOPE_FLAT_FOR_SHORT" in reason


def test_short_blocked_when_ma_entangled():
    """當 MA5 與 MA15 間距 < 0.15% (ma_spread < 0.0015) 黏合重合時，100% 一票否決 (REJECT: MA5_MA15_ENTANGLED)"""
    # ma5_curr = 0.03998, ma15_curr = 0.04000 -> spread = 0.00002 / 0.04000 = 0.0005 < 0.0015
    df = pd.DataFrame([
        {'open': 0.0405, 'close': 0.0400, 'high': 0.0408, 'low': 0.0398, 'ma5': 0.04010, 'ma15': 0.04005, 'atr': 0.0005, 'kc_middle': 0.0403, 'kc_upper': 0.0415, 'kc_lower': 0.0390, 'is_closed': True},
        {'open': 0.0400, 'close': 0.0388, 'high': 0.0401, 'low': 0.0385, 'ma5': 0.03998, 'ma15': 0.04000, 'atr': 0.0005, 'kc_middle': 0.0403, 'kc_upper': 0.0415, 'kc_lower': 0.0390, 'is_closed': True},
    ])

    passed, reason = MemeSharedProtectionGate.validate_short_ma_condition(df)
    assert not passed
    assert "MA5_MA15_ENTANGLED" in reason


def test_long_blocked_when_ma5_flat():
    """當 MA5 走平 (Slope <= +0.0001) 時，做多必須 100% 一票否決 (REJECT: MA5_SLOPE_FLAT_FOR_LONG)"""
    # ma5_curr = 0.04000, ma5_prev = 0.04000 -> slope = 0.0 <= 0.0001
    df = pd.DataFrame([
        {'open': 0.0395, 'close': 0.0400, 'high': 0.0402, 'low': 0.0392, 'ma5': 0.04000, 'ma15': 0.03950, 'atr': 0.0005, 'kc_middle': 0.0398, 'kc_upper': 0.0410, 'kc_lower': 0.0385, 'is_closed': True},
        {'open': 0.0400, 'close': 0.0412, 'high': 0.0415, 'low': 0.0399, 'ma5': 0.04000, 'ma15': 0.03950, 'atr': 0.0005, 'kc_middle': 0.0398, 'kc_upper': 0.0410, 'kc_lower': 0.0385, 'is_closed': True},
    ])

    passed, reason = MemeSharedProtectionGate.validate_long_ma_condition(df)
    assert not passed
    assert "MA5_SLOPE_FLAT_FOR_LONG" in reason


def test_long_blocked_when_ma_entangled():
    """當 MA5 與 MA15 間距 < 0.15% 黏合重合時，做多 100% 一票否決 (REJECT: MA5_MA15_ENTANGLED)"""
    df = pd.DataFrame([
        {'open': 0.0395, 'close': 0.0400, 'high': 0.0402, 'low': 0.0392, 'ma5': 0.03990, 'ma15': 0.03995, 'atr': 0.0005, 'kc_middle': 0.0398, 'kc_upper': 0.0410, 'kc_lower': 0.0385, 'is_closed': True},
        {'open': 0.0400, 'close': 0.0412, 'high': 0.0415, 'low': 0.0399, 'ma5': 0.04002, 'ma15': 0.04000, 'atr': 0.0005, 'kc_middle': 0.0398, 'kc_upper': 0.0410, 'kc_lower': 0.0385, 'is_closed': True},
    ])

    passed, reason = MemeSharedProtectionGate.validate_long_ma_condition(df)
    assert not passed
    assert "MA5_MA15_ENTANGLED" in reason


def test_evaluate_entry_blocks_mode1_breakout_when_ma5_flat():
    """即便價格破軌 (Mode 1)，若 MA5 走平 (Slope >= -0.0001)，最外層防禦門必須 100% 阻斷，不得放行！"""
    df_15m = pd.DataFrame([
        {'open': 0.0410, 'close': 0.0405, 'high': 0.0415, 'low': 0.0400, 'ma5': 0.0408, 'ma15': 0.0408, 'kc_middle': 0.0407, 'kc_upper': 0.0420, 'kc_lower': 0.0394, 'atr': 0.0009, 'is_closed': True},
        {'open': 0.0405, 'close': 0.0400, 'high': 0.0408, 'low': 0.0398, 'ma5': 0.0405, 'ma15': 0.0407, 'kc_middle': 0.0405, 'kc_upper': 0.0420, 'kc_lower': 0.0390, 'atr': 0.0009, 'is_closed': True, 'channel_state': 'RANGE'},
    ])

    # 價格大陰線破軌 (跌破 kc_lower 0.0390)，但 MA5 走平 (0.0400 -> 0.0400)
    df_5m = pd.DataFrame([
        {'open': 0.0405, 'close': 0.0400, 'high': 0.0408, 'low': 0.0398, 'ma5': 0.0400, 'ma15': 0.0405, 'kc_middle': 0.0404, 'kc_upper': 0.0416, 'kc_lower': 0.0392, 'atr': 0.0006, 'is_closed': True},
        {'open': 0.0400, 'close': 0.0388, 'high': 0.0402, 'low': 0.0385, 'ma5': 0.0400, 'ma15': 0.0405, 'kc_middle': 0.0402, 'kc_upper': 0.0414, 'kc_lower': 0.0390, 'atr': 0.0006, 'is_closed': True},
    ])

    passed, reason, meta = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m, df_15m=df_15m, side='SHORT', symbol='LOBSTER/USDT', quote=0.0388
    )

    assert not passed
    assert "MA5_SLOPE_FLAT_FOR_SHORT" in reason


def test_evaluate_entry_blocks_mode1_breakout_when_ma_entangled():
    """即便價格破軌，若 MA5 與 MA15 黏合 (Spread < 0.15%)，最外層防禦門必須 100% 阻斷！"""
    df_15m = pd.DataFrame([
        {'open': 0.0410, 'close': 0.0405, 'high': 0.0415, 'low': 0.0400, 'ma5': 0.0408, 'ma15': 0.0408, 'kc_middle': 0.0407, 'kc_upper': 0.0420, 'kc_lower': 0.0394, 'atr': 0.0009, 'is_closed': True},
        {'open': 0.0405, 'close': 0.0400, 'high': 0.0408, 'low': 0.0398, 'ma5': 0.0405, 'ma15': 0.0407, 'kc_middle': 0.0405, 'kc_upper': 0.0420, 'kc_lower': 0.0390, 'atr': 0.0009, 'is_closed': True, 'channel_state': 'RANGE'},
    ])

    # MA5 有下行斜率 (0.04004 -> 0.04000)，但兩線間距極窄 (0.04000 vs 0.04003 = spread 0.00075 < 0.0015)
    df_5m = pd.DataFrame([
        {'open': 0.0405, 'close': 0.0400, 'high': 0.0408, 'low': 0.0398, 'ma5': 0.04004, 'ma15': 0.04005, 'kc_middle': 0.0404, 'kc_upper': 0.0416, 'kc_lower': 0.0392, 'atr': 0.0006, 'is_closed': True},
        {'open': 0.0400, 'close': 0.0388, 'high': 0.0402, 'low': 0.0385, 'ma5': 0.04000, 'ma15': 0.04003, 'kc_middle': 0.0402, 'kc_upper': 0.0414, 'kc_lower': 0.0390, 'atr': 0.0006, 'is_closed': True},
    ])

    passed, reason, meta = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m, df_15m=df_15m, side='SHORT', symbol='LOBSTER/USDT', quote=0.0388
    )

    assert not passed
    assert "MA5_MA15_ENTANGLED" in reason
