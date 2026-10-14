import pytest
from core.gates.macro_candle_structure_gate import MacroCandleStructureGate
from core.services.gates.entry_gate import MacroCandleStructureGate as ExportedGate

def test_gate_export():
    assert MacroCandleStructureGate is ExportedGate

def test_long_vetoed_when_kc_position_too_high():
    gate = MacroCandleStructureGate(kc_pos_long_max=0.35)
    # kc_upper = 110, kc_lower = 90 (range = 20)
    # close = 100 -> kc_pos = (100 - 90) / 20 = 0.50 > 0.35
    bar = {'open': 99.0, 'close': 100.0, 'high': 100.5, 'low': 98.8, 'volume': 100}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 99.5, 'atr': 2.0}
    
    can_open, reason = gate.can_open('LONG', bar, indicators)
    assert can_open is False
    assert "VETO_LONG: 價格位於通道偏高處" in reason

def test_long_vetoed_when_bias_too_large():
    gate = MacroCandleStructureGate(kc_pos_long_max=0.35, max_bias_atr=1.2)
    # close = 92, kc_lower = 90, kc_upper = 110 -> kc_pos = 2/20 = 0.10 <= 0.35
    # ma15 = 96, close = 92 -> abs(close - ma15) = 4.0. If atr = 2.0, bias = 4.0 / 2.0 = 2.0 > 1.2
    bar = {'open': 91.5, 'close': 92.0, 'high': 92.2, 'low': 91.0, 'volume': 100}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 96.0, 'atr': 2.0}

    can_open, reason = gate.can_open('LONG', bar, indicators)
    assert can_open is False
    assert "VETO_LONG: 乖離過大" in reason

def test_long_vetoed_when_upper_wick_rejection():
    gate = MacroCandleStructureGate(kc_pos_long_max=0.35, max_bias_atr=1.2)
    # close = 92.0, open = 91.8 (body = 0.2), high = 93.0 (upper_wick = 93.0 - 92.0 = 1.0 > 0.2)
    bar = {'open': 91.8, 'close': 92.0, 'high': 93.0, 'low': 91.7, 'volume': 100}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 92.5, 'atr': 2.0}

    can_open, reason = gate.can_open('LONG', bar, indicators)
    assert can_open is False
    assert "VETO_LONG: K 棒受阻留長上影線" in reason

def test_long_passed_when_all_conditions_met():
    gate = MacroCandleStructureGate(kc_pos_long_max=0.35, max_bias_atr=1.2)
    # close = 93.0, open = 91.5 (body = 1.5), high = 93.3 (upper_wick = 0.3 < 1.5), low = 91.4
    # kc_range = 20, kc_pos = 3/20 = 0.15 <= 0.35
    # ma15 = 93.5, diff = 0.5, atr = 2.0 -> bias = 0.25 <= 1.2
    bar = {'open': 91.5, 'close': 93.0, 'high': 93.3, 'low': 91.4, 'volume': 100}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 93.5, 'atr': 2.0}

    can_open, reason = gate.can_open('LONG', bar, indicators)
    assert can_open is True
    assert reason == "PASSED_MACRO_LONG_GATE"

def test_short_vetoed_when_kc_position_too_low():
    gate = MacroCandleStructureGate(kc_pos_short_min=0.65)
    # kc_upper = 110, kc_lower = 90 (range = 20)
    # close = 98 -> kc_pos = (98 - 90) / 20 = 0.40 < 0.65 (floor short chasing)
    bar = {'open': 99.0, 'close': 98.0, 'high': 99.2, 'low': 97.8, 'volume': 100}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 99.0, 'atr': 2.0}

    can_open, reason = gate.can_open('SHORT', bar, indicators)
    assert can_open is False
    assert "VETO_SHORT: 價格已跌入通道底部" in reason

def test_short_vetoed_when_bias_too_large():
    gate = MacroCandleStructureGate(kc_pos_short_min=0.65, max_bias_atr=1.2)
    # close = 108, kc_range = 20 -> kc_pos = 18/20 = 0.90 >= 0.65
    # ma15 = 104, diff = 4.0, atr = 2.0 -> bias = 2.0 > 1.2
    bar = {'open': 108.5, 'close': 108.0, 'high': 108.8, 'low': 107.8, 'volume': 100}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 104.0, 'atr': 2.0}

    can_open, reason = gate.can_open('SHORT', bar, indicators)
    assert can_open is False
    assert "VETO_SHORT: 乖離過大" in reason

def test_short_vetoed_when_lower_wick_exhaustion():
    gate = MacroCandleStructureGate(kc_pos_short_min=0.65, max_bias_atr=1.2)
    # close = 107.0, open = 107.2 (body = 0.2), low = 105.8 (lower_wick = 107.0 - 105.8 = 1.2 > 0.2)
    # high = 107.3
    bar = {'open': 107.2, 'close': 107.0, 'high': 107.3, 'low': 105.8, 'volume': 100}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 107.5, 'atr': 2.0}

    can_open, reason = gate.can_open('SHORT', bar, indicators)
    assert can_open is False
    assert "VETO_SHORT: K 棒見底留長下影線" in reason

def test_short_passed_when_all_conditions_met():
    gate = MacroCandleStructureGate(kc_pos_short_min=0.65, max_bias_atr=1.2)
    # close = 106.0, open = 107.5 (body = 1.5), high = 107.7, low = 105.7 (lower_wick = 0.3 < 1.5)
    # kc_range = 20, kc_pos = 16/20 = 0.80 >= 0.65
    # ma15 = 106.5, diff = 0.5, atr = 2.0 -> bias = 0.25 <= 1.2
    bar = {'open': 107.5, 'close': 106.0, 'high': 107.7, 'low': 105.7, 'volume': 100}
    indicators = {'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 100.0, 'ma15': 106.5, 'atr': 2.0}

    can_open, reason = gate.can_open('SHORT', bar, indicators)
    assert can_open is True
    assert reason == "PASSED_MACRO_SHORT_GATE"

def test_unknown_side():
    gate = MacroCandleStructureGate()
    bar = {'open': 100, 'close': 100, 'high': 101, 'low': 99}
    indicators = {'kc_upper': 110, 'kc_lower': 90, 'kc_middle': 100, 'ma15': 100, 'atr': 1}
    can_open, reason = gate.can_open('INVALID', bar, indicators)
    assert can_open is False
    assert reason == "UNKNOWN_SIDE"
