import pytest

from core.gates.entry_prefilter_gate import can_execute_entry, EntryPreFilterGate


def test_channel_squeeze_rejects_both_sides():
    """通道寬度過濾：若 kc_width < 1.5 * avg_atr，判定為無方向盤整泥淖，禁止開多也禁止開空"""
    candle = {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}
    # kc_width = 100.5 - 99.5 = 1.0 < 1.5 * 1.0 (1.5)
    kc = {"upper": 100.5, "lower": 99.5, "mid": 100.0}

    # 測試 LONG
    allowed_l, reason_l = can_execute_entry(
        signal_type="LONG", candle=candle, kc=kc, ma5=100.2, ma15=100.1,
        kc_mid_slope=0.1, ma15_slope=0.1, avg_atr=1.0
    )
    assert not allowed_l
    assert "BLOCKED_ENTRY_KC_CHANNEL_SQUEEZE_CHOP" in reason_l

    # 測試 SHORT
    allowed_s, reason_s = can_execute_entry(
        signal_type="SHORT", candle=candle, kc=kc, ma5=99.8, ma15=99.9,
        kc_mid_slope=-0.1, ma15_slope=-0.1, avg_atr=1.0
    )
    assert not allowed_s
    assert "BLOCKED_ENTRY_KC_CHANNEL_SQUEEZE_CHOP" in reason_s


def test_long_below_midline_or_ma15_blocked():
    """多單防護：價格在 KC 中軌或 MA15 之下，嚴禁逆勢摸多接飛刀"""
    kc = {"upper": 105.0, "lower": 95.0, "mid": 100.0}
    # 情境 1：Close 在 KC 中軌之下 (99.0 < 100.0)
    candle_1 = {"open": 98.0, "high": 99.5, "low": 97.5, "close": 99.0}
    allowed_1, reason_1 = can_execute_entry(
        signal_type="LONG", candle=candle_1, kc=kc, ma5=99.2, ma15=98.5,
        kc_mid_slope=0.1, ma15_slope=0.1, avg_atr=2.0
    )
    assert not allowed_1
    assert "BLOCKED_LONG_BELOW_MIDLINE_OR_MA15" in reason_1

    # 情境 2：Close 在 MA15 之下 (100.5 < 101.0)
    candle_2 = {"open": 100.0, "high": 101.5, "low": 99.5, "close": 100.5}
    allowed_2, reason_2 = can_execute_entry(
        signal_type="LONG", candle=candle_2, kc=kc, ma5=100.8, ma15=101.0,
        kc_mid_slope=0.1, ma15_slope=0.1, avg_atr=2.0
    )
    assert not allowed_2
    assert "BLOCKED_LONG_BELOW_MIDLINE_OR_MA15" in reason_2


def test_long_ma15_slope_down_blocked():
    """多單防護：雖然站在中軌與 MA15 之上，但 MA15 仍向下傾斜，結構未確認，禁止做多"""
    kc = {"upper": 105.0, "lower": 95.0, "mid": 100.0}
    candle = {"open": 101.0, "high": 102.5, "low": 100.5, "close": 102.0}
    # ma15_slope = -0.05 < 0
    allowed, reason = can_execute_entry(
        signal_type="LONG", candle=candle, kc=kc, ma5=101.8, ma15=101.0,
        kc_mid_slope=0.1, ma15_slope=-0.05, avg_atr=2.0
    )
    assert not allowed
    assert "BLOCKED_LONG_MA15_SLOPE_DOWN" in reason


def test_short_above_midline_or_ma15_blocked():
    """空單防護：價格在 KC 中軌或 MA15 之上，嚴禁逆勢摸頂做空"""
    kc = {"upper": 105.0, "lower": 95.0, "mid": 100.0}
    # 情境 1：Close 在 KC 中軌之上 (101.0 > 100.0)
    candle_1 = {"open": 100.0, "high": 102.0, "low": 99.5, "close": 101.0}
    allowed_1, reason_1 = can_execute_entry(
        signal_type="SHORT", candle=candle_1, kc=kc, ma5=100.5, ma15=102.0,
        kc_mid_slope=-0.1, ma15_slope=-0.1, avg_atr=2.0
    )
    assert not allowed_1
    assert "BLOCKED_SHORT_ABOVE_MIDLINE_OR_MA15" in reason_1

    # 情境 2：Close 在 MA15 之上 (99.5 > 99.0)
    candle_2 = {"open": 99.0, "high": 100.0, "low": 98.0, "close": 99.5}
    allowed_2, reason_2 = can_execute_entry(
        signal_type="SHORT", candle=candle_2, kc=kc, ma5=99.2, ma15=99.0,
        kc_mid_slope=-0.1, ma15_slope=-0.1, avg_atr=2.0
    )
    assert not allowed_2
    assert "BLOCKED_SHORT_ABOVE_MIDLINE_OR_MA15" in reason_2


def test_short_ma15_or_mid_flat_blocked():
    """空單防護：MA15 或 KC 中軌走平（斜率接近 0 或大於等於 0），禁止無趨勢追空"""
    kc = {"upper": 105.0, "lower": 95.0, "mid": 100.0}
    candle = {"open": 98.5, "high": 99.0, "low": 97.5, "close": 98.0}

    # 情境 1：ma15_slope = 0.0 (走平)
    allowed_1, reason_1 = can_execute_entry(
        signal_type="SHORT", candle=candle, kc=kc, ma5=98.5, ma15=99.0,
        kc_mid_slope=-0.1, ma15_slope=0.0, avg_atr=2.0
    )
    assert not allowed_1
    assert "BLOCKED_SHORT_MA15_FLAT_OR_UP" in reason_1

    # 情境 2：kc_mid_slope = 0.0 (中軌走平)
    allowed_2, reason_2 = can_execute_entry(
        signal_type="SHORT", candle=candle, kc=kc, ma5=98.5, ma15=99.0,
        kc_mid_slope=0.0, ma15_slope=-0.1, avg_atr=2.0
    )
    assert not allowed_2
    assert "BLOCKED_SHORT_KC_MID_FLAT_OR_UP" in reason_2


def test_valid_entries_allowed():
    """標準順勢開倉：條件完全吻合時正常放行"""
    kc = {"upper": 105.0, "lower": 95.0, "mid": 100.0}

    # 標準多單：Close > mid & MA15, MA15向上, 通道寬度足夠
    candle_l = {"open": 101.0, "high": 103.0, "low": 100.5, "close": 102.5}
    allowed_l, _ = can_execute_entry(
        signal_type="LONG", candle=candle_l, kc=kc, ma5=102.0, ma15=101.0,
        kc_mid_slope=0.1, ma15_slope=0.08, avg_atr=2.0
    )
    assert allowed_l

    # 標準空單：Close < mid & MA15, MA15與中軌明顯向下, 通道寬度足夠
    candle_s = {"open": 98.5, "high": 99.0, "low": 97.0, "close": 97.5}
    allowed_s, _ = can_execute_entry(
        signal_type="SHORT", candle=candle_s, kc=kc, ma5=98.0, ma15=99.0,
        kc_mid_slope=-0.1, ma15_slope=-0.08, avg_atr=2.0
    )
    assert allowed_s


def test_low_priced_coin_downward_slope_not_blocked():
    """低價幣 (如 CAP 0.069) 斜率微小但確實向下 (-0.00002)，不應被誤判為走平阻擋"""
    kc = {"upper": 0.072, "lower": 0.067, "mid": 0.0695}
    candle = {"open": 0.069, "high": 0.0692, "low": 0.0678, "close": 0.068}
    allowed, reason = can_execute_entry(
        signal_type="SHORT",
        candle=candle,
        kc=kc,
        ma5=0.0685,
        ma15=0.0695,
        kc_mid_slope=-0.00002,
        ma15_slope=-0.00001,
        avg_atr=0.001,
    )
    assert allowed is True
    assert "PASSED_ENTRY_PREFILTER" in reason


def test_breakout_entry_allows_even_if_ma15_lagging():
    """破軌開倉：價格已跌破下軌，即使 MA15 滯後走平，破軌依然允許開倉"""
    kc = {"upper": 105.0, "lower": 95.0, "mid": 100.0}
    candle = {"open": 95.2, "high": 95.5, "low": 92.5, "close": 93.0}
    allowed, reason = can_execute_entry(
        signal_type="SHORT",
        candle=candle,
        kc=kc,
        ma5=94.0,
        ma15=98.0,
        kc_mid_slope=-0.05,
        ma15_slope=0.0,  # MA15 滯後
        avg_atr=2.0,
        entry_pattern="BREAKOUT",
    )
    assert allowed is True
    assert "PASSED_ENTRY_PREFILTER" in reason
