import pytest
from core.gates.five_lines_confluence_gate import FiveLinesConfluenceGate
from core.services.gates.entry_gate import FiveLinesConfluenceGate as ExportedGate

def test_gate_export():
    assert FiveLinesConfluenceGate is ExportedGate

def create_indicators(ma5, ma15, kc_mid, kc_upper, kc_lower, atr=1.0):
    return {
        'ma5': ma5,
        'ma15': ma15,
        'kc_middle': kc_mid,
        'kc_upper': kc_upper,
        'kc_lower': kc_lower,
        'atr': atr
    }

def test_long_five_lines_all_up_passed():
    gate = FiveLinesConfluenceGate(min_slope_pct=0.0002)
    price = 100.0
    bar = {'close': price, 'open': 99.5, 'high': 100.5, 'low': 99.0}
    prev_bar = {'close': 99.0}

    # All slopes > 0.0002 * 100 = 0.02
    curr_ind = create_indicators(ma5=101.0, ma15=100.0, kc_mid=100.1, kc_upper=102.1, kc_lower=98.1)
    prev_ind = create_indicators(ma5=100.5, ma15=99.8, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is True
    assert reason == "PASSED_FIVE_LINES_LONG"

def test_long_rejected_when_ma5_not_greater_than_ma15():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0}
    prev_bar = {'close': 99.0}
    curr_ind = create_indicators(ma5=99.5, ma15=100.0, kc_mid=100.1, kc_upper=102.1, kc_lower=98.1)
    prev_ind = create_indicators(ma5=99.0, ma15=99.8, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_LONG: MA5 尚未大於 MA15" in reason

def test_long_rejected_when_ma5_flat_or_down():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0}
    prev_bar = {'close': 99.0}
    curr_ind = create_indicators(ma5=101.0, ma15=100.0, kc_mid=100.1, kc_upper=102.1, kc_lower=98.1)
    prev_ind = create_indicators(ma5=101.0, ma15=99.8, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_LONG: MA5 走平或向下" in reason

def test_long_rejected_when_ma15_flat_or_down():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0}
    prev_bar = {'close': 99.0}
    curr_ind = create_indicators(ma5=101.5, ma15=100.0, kc_mid=100.1, kc_upper=102.1, kc_lower=98.1)
    prev_ind = create_indicators(ma5=101.0, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_LONG: MA15 走平或向下" in reason

def test_long_rejected_when_kc_mid_flat_or_down():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0}
    prev_bar = {'close': 99.0}
    curr_ind = create_indicators(ma5=101.5, ma15=100.2, kc_mid=100.0, kc_upper=102.1, kc_lower=98.1)
    prev_ind = create_indicators(ma5=101.0, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_LONG: KC中軌 走平或向下" in reason

def test_long_rejected_when_kc_upper_flat():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0}
    prev_bar = {'close': 99.0}
    # kc_upper flat
    curr_ind = create_indicators(ma5=101.5, ma15=100.2, kc_mid=100.1, kc_upper=102.0, kc_lower=98.1)
    prev_ind = create_indicators(ma5=101.0, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_LONG: KC上軌 走平未打開" in reason

def test_long_rejected_when_kc_lower_flat_or_down():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0}
    prev_bar = {'close': 99.0}
    # kc_lower down
    curr_ind = create_indicators(ma5=101.5, ma15=100.2, kc_mid=100.1, kc_upper=102.1, kc_lower=97.9)
    prev_ind = create_indicators(ma5=101.0, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_LONG: KC下軌 走平或向下" in reason

def test_long_rejected_when_overshoot_too_far():
    gate = FiveLinesConfluenceGate(max_overshoot_atr=2.0)
    # price = 106.0, kc_upper = 102.1, atr = 1.0 -> overshoot = 3.9 > 2.0
    bar = {'close': 106.0}
    prev_bar = {'close': 99.0}
    curr_ind = create_indicators(ma5=101.5, ma15=100.2, kc_mid=100.1, kc_upper=102.1, kc_lower=98.1, atr=1.0)
    prev_ind = create_indicators(ma5=101.0, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0, atr=1.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_LONG: 突破 KC 上軌過遠" in reason

def test_short_five_lines_all_down_passed():
    gate = FiveLinesConfluenceGate(min_slope_pct=0.0002)
    price = 100.0
    bar = {'close': price, 'open': 100.5, 'high': 101.0, 'low': 99.5}
    prev_bar = {'close': 101.0}

    # All slopes < -0.0002 * 100 = -0.02
    curr_ind = create_indicators(ma5=99.0, ma15=100.0, kc_mid=99.9, kc_upper=101.9, kc_lower=97.9)
    prev_ind = create_indicators(ma5=99.5, ma15=100.2, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is True
    assert reason == "PASSED_FIVE_LINES_SHORT"

def test_short_rejected_when_ma5_not_less_than_ma15():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0}
    prev_bar = {'close': 101.0}
    curr_ind = create_indicators(ma5=100.5, ma15=100.0, kc_mid=99.9, kc_upper=101.9, kc_lower=97.9)
    prev_ind = create_indicators(ma5=101.0, ma15=100.2, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_SHORT: MA5 尚未小於 MA15" in reason

def test_short_rejected_when_ma5_flat_or_up():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0}
    prev_bar = {'close': 101.0}
    curr_ind = create_indicators(ma5=99.0, ma15=100.0, kc_mid=99.9, kc_upper=101.9, kc_lower=97.9)
    prev_ind = create_indicators(ma5=99.0, ma15=100.2, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_SHORT: MA5 走平或向上" in reason

def test_short_rejected_when_ma15_flat_or_up():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0}
    prev_bar = {'close': 101.0}
    curr_ind = create_indicators(ma5=99.0, ma15=100.0, kc_mid=99.9, kc_upper=101.9, kc_lower=97.9)
    prev_ind = create_indicators(ma5=99.5, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_SHORT: MA15 走平或向上" in reason

def test_short_rejected_when_kc_mid_flat_or_up():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0}
    prev_bar = {'close': 101.0}
    curr_ind = create_indicators(ma5=99.0, ma15=99.8, kc_mid=100.0, kc_upper=101.9, kc_lower=97.9)
    prev_ind = create_indicators(ma5=99.5, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_SHORT: KC中軌 走平或向上" in reason

def test_short_rejected_when_kc_upper_flat_or_up():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0}
    prev_bar = {'close': 101.0}
    curr_ind = create_indicators(ma5=99.0, ma15=99.8, kc_mid=99.9, kc_upper=102.0, kc_lower=97.9)
    prev_ind = create_indicators(ma5=99.5, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_SHORT: KC上軌 走平或向上" in reason

def test_short_rejected_when_kc_lower_flat():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0}
    prev_bar = {'close': 101.0}
    curr_ind = create_indicators(ma5=99.0, ma15=99.8, kc_mid=99.9, kc_upper=101.9, kc_lower=98.0)
    prev_ind = create_indicators(ma5=99.5, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_SHORT: KC下軌 走平未打開" in reason

def test_short_rejected_when_overshoot_too_far():
    gate = FiveLinesConfluenceGate(max_overshoot_atr=2.0)
    # price = 94.0, kc_lower = 97.9, atr = 1.0 -> overshoot = 3.9 > 2.0
    bar = {'close': 94.0}
    prev_bar = {'close': 101.0}
    curr_ind = create_indicators(ma5=99.0, ma15=99.8, kc_mid=99.9, kc_upper=101.9, kc_lower=97.9, atr=1.0)
    prev_ind = create_indicators(ma5=99.5, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0, atr=1.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_SHORT: 跌破 KC 下軌過遠" in reason

def test_unknown_side():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0}
    prev_bar = {'close': 100.0}
    curr_ind = create_indicators(100, 100, 100, 100, 100)
    prev_ind = create_indicators(100, 100, 100, 100, 100)

    can_open, reason = gate.can_open('UNKNOWN', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert reason == "VETO: UNKNOWN_SIDE"
