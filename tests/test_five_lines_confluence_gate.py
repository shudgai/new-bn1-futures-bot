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
    # price = 100.0, kc_lower = 98.1, kc_upper = 102.1 -> bw = 4.0, kc_pos = 1.9 / 4.0 = 0.475 (in [0.2, 0.8])
    price = 100.0
    bar = {'close': price, 'open': 99.5, 'high': 100.5, 'low': 99.0, 'is_closed': True}
    prev_bar = {'close': 99.0, 'is_closed': True}

    # All slopes > 0.0002 * 100 = 0.02
    curr_ind = create_indicators(ma5=101.0, ma15=100.0, kc_mid=100.1, kc_upper=102.1, kc_lower=98.1)
    prev_ind = create_indicators(ma5=100.5, ma15=99.8, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is True
    assert reason == "PASSED_FIVE_LINES_LONG"

def test_long_kc_bandwidth_expansion_passed_when_lower_pulled_down():
    """多單暴漲時，下軌因 ATR 放大而被往下拉，只要帶寬擴張且中軌與上軌向上，不誤判逆向"""
    gate = FiveLinesConfluenceGate(min_slope_pct=0.0002)
    price = 100.5
    bar = {'close': price, 'open': 99.8, 'high': 101.0, 'low': 99.5, 'is_closed': True}
    prev_bar = {'close': 99.5, 'is_closed': True}

    # curr_bw = 103.5 - 97.5 = 6.0, prev_bw = 102.0 - 98.0 = 4.0 -> expanding!
    # slope_kc_upper = 1.5 > 0, slope_kc_mid = 0.5 > 0, slope_kc_lower = -0.5 < 0 (pulled down by ATR)
    curr_ind = create_indicators(ma5=101.5, ma15=100.2, kc_mid=100.5, kc_upper=103.5, kc_lower=97.5)
    prev_ind = create_indicators(ma5=100.5, ma15=99.8, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    # kc_pos = (100.5 - 97.5) / 6.0 = 0.50 (in [0.2, 0.8])
    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is True
    assert reason == "PASSED_FIVE_LINES_LONG"

def test_long_kc_pos_ceiling_vetoed():
    """多單 KC %B > 0.80 拒絕天花板追多"""
    gate = FiveLinesConfluenceGate(kc_pos_long_max=0.80)
    # price = 101.8, kc_lower = 98.1, kc_upper = 102.1 -> bw = 4.0, kc_pos = 3.7 / 4.0 = 0.925 > 0.80
    price = 101.8
    bar = {'close': price, 'open': 101.0, 'high': 102.0, 'low': 100.8, 'is_closed': True}
    prev_bar = {'close': 100.5, 'is_closed': True}

    curr_ind = create_indicators(ma5=101.5, ma15=100.2, kc_mid=100.1, kc_upper=102.1, kc_lower=98.1)
    prev_ind = create_indicators(ma5=100.5, ma15=99.8, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_LONG: 價格貼近天花板極限" in reason

def test_long_rejected_when_ma5_not_greater_than_ma15():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0, 'is_closed': True}
    prev_bar = {'close': 99.0, 'is_closed': True}
    curr_ind = create_indicators(ma5=99.5, ma15=100.0, kc_mid=100.1, kc_upper=102.1, kc_lower=98.1)
    prev_ind = create_indicators(ma5=99.0, ma15=99.8, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_LONG: MA5 尚未大於 MA15" in reason

def test_long_rejected_when_ma5_flat_or_down():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0, 'is_closed': True}
    prev_bar = {'close': 99.0, 'is_closed': True}
    curr_ind = create_indicators(ma5=101.0, ma15=100.0, kc_mid=100.1, kc_upper=102.1, kc_lower=98.1)
    prev_ind = create_indicators(ma5=101.0, ma15=99.8, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_LONG: MA5 走平或向下" in reason

def test_long_rejected_when_ma15_flat_or_down():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0, 'is_closed': True}
    prev_bar = {'close': 99.0, 'is_closed': True}
    curr_ind = create_indicators(ma5=101.5, ma15=100.0, kc_mid=100.1, kc_upper=102.1, kc_lower=98.1)
    prev_ind = create_indicators(ma5=101.0, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_LONG: MA15 走平或向下" in reason

def test_long_rejected_when_kc_mid_flat_or_down():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0, 'is_closed': True}
    prev_bar = {'close': 99.0, 'is_closed': True}
    curr_ind = create_indicators(ma5=101.5, ma15=100.2, kc_mid=100.0, kc_upper=102.1, kc_lower=98.1)
    prev_ind = create_indicators(ma5=101.0, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_LONG: KC中軌 走平或向下" in reason

def test_long_rejected_when_kc_upper_flat():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0, 'is_closed': True}
    prev_bar = {'close': 99.0, 'is_closed': True}
    curr_ind = create_indicators(ma5=101.5, ma15=100.2, kc_mid=100.1, kc_upper=102.0, kc_lower=98.1)
    prev_ind = create_indicators(ma5=101.0, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_LONG: KC上軌 走平未打開" in reason

def test_short_five_lines_all_down_passed():
    gate = FiveLinesConfluenceGate(min_slope_pct=0.0002)
    # price = 100.0, kc_lower = 97.9, kc_upper = 101.9 -> bw = 4.0, kc_pos = 2.1 / 4.0 = 0.525 (in [0.2, 0.8])
    price = 100.0
    bar = {'close': price, 'open': 100.5, 'high': 101.0, 'low': 99.5, 'is_closed': True}
    prev_bar = {'close': 101.0, 'is_closed': True}

    # All slopes < -0.0002 * 100 = -0.02
    curr_ind = create_indicators(ma5=99.0, ma15=100.0, kc_mid=99.9, kc_upper=101.9, kc_lower=97.9)
    prev_ind = create_indicators(ma5=99.5, ma15=100.2, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is True
    assert reason == "PASSED_FIVE_LINES_SHORT"

def test_short_kc_bandwidth_expansion_passed_when_upper_spread_up():
    """空單暴跌時，上軌因 ATR 放大而向上張開翹起，只要帶寬擴張且中軌與下軌向下，不誤判逆向"""
    gate = FiveLinesConfluenceGate(min_slope_pct=0.0002)
    price = 99.5
    bar = {'close': price, 'open': 100.2, 'high': 100.5, 'low': 99.0, 'is_closed': True}
    prev_bar = {'close': 100.5, 'is_closed': True}

    # curr_bw = 102.5 - 96.5 = 6.0, prev_bw = 102.0 - 98.0 = 4.0 -> expanding!
    # slope_kc_upper = +0.5 > 0 (spread up by ATR), slope_kc_mid = -0.5 < 0, slope_kc_lower = -1.5 < 0
    curr_ind = create_indicators(ma5=98.5, ma15=99.8, kc_mid=99.5, kc_upper=102.5, kc_lower=96.5)
    prev_ind = create_indicators(ma5=99.5, ma15=100.2, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    # kc_pos = (99.5 - 96.5) / 6.0 = 0.50 (in [0.2, 0.8])
    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is True
    assert reason == "PASSED_FIVE_LINES_SHORT"

def test_short_kc_pos_floor_vetoed():
    """空單 KC %B < 0.20 拒絕地板追空"""
    gate = FiveLinesConfluenceGate(kc_pos_short_min=0.20)
    # price = 98.2, kc_lower = 97.9, kc_upper = 101.9 -> bw = 4.0, kc_pos = 0.3 / 4.0 = 0.075 < 0.20
    price = 98.2
    bar = {'close': price, 'open': 99.0, 'high': 99.2, 'low': 98.0, 'is_closed': True}
    prev_bar = {'close': 99.5, 'is_closed': True}

    curr_ind = create_indicators(ma5=98.5, ma15=99.8, kc_mid=99.9, kc_upper=101.9, kc_lower=97.9)
    prev_ind = create_indicators(ma5=99.5, ma15=100.2, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_SHORT: 價格貼近地板極限" in reason

def test_short_rejected_when_ma5_not_less_than_ma15():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0, 'is_closed': True}
    prev_bar = {'close': 101.0, 'is_closed': True}
    curr_ind = create_indicators(ma5=100.5, ma15=100.0, kc_mid=99.9, kc_upper=101.9, kc_lower=97.9)
    prev_ind = create_indicators(ma5=101.0, ma15=100.2, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_SHORT: MA5 尚未小於 MA15" in reason

def test_short_rejected_when_ma5_flat_or_up():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0, 'is_closed': True}
    prev_bar = {'close': 101.0, 'is_closed': True}
    curr_ind = create_indicators(ma5=99.0, ma15=100.0, kc_mid=99.9, kc_upper=101.9, kc_lower=97.9)
    prev_ind = create_indicators(ma5=99.0, ma15=100.2, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_SHORT: MA5 走平或向上" in reason

def test_short_rejected_when_ma15_flat_or_up():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0, 'is_closed': True}
    prev_bar = {'close': 101.0, 'is_closed': True}
    curr_ind = create_indicators(ma5=99.0, ma15=100.0, kc_mid=99.9, kc_upper=101.9, kc_lower=97.9)
    prev_ind = create_indicators(ma5=99.5, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_SHORT: MA15 走平或向上" in reason

def test_short_rejected_when_kc_mid_flat_or_up():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0, 'is_closed': True}
    prev_bar = {'close': 101.0, 'is_closed': True}
    curr_ind = create_indicators(ma5=99.0, ma15=99.8, kc_mid=100.0, kc_upper=101.9, kc_lower=97.9)
    prev_ind = create_indicators(ma5=99.5, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_SHORT: KC中軌 走平或向上" in reason

def test_short_rejected_when_kc_lower_flat_or_up():
    """空單 KC 下軌走平或向上未打開時否決"""
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0, 'is_closed': True}
    prev_bar = {'close': 101.0, 'is_closed': True}
    # kc_lower from 98.0 to 98.0 (flat)
    curr_ind = create_indicators(ma5=99.0, ma15=99.8, kc_mid=99.8, kc_upper=101.8, kc_lower=98.0)
    prev_ind = create_indicators(ma5=99.5, ma15=100.0, kc_mid=100.0, kc_upper=102.0, kc_lower=98.0)

    can_open, reason = gate.can_open('SHORT', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO_SHORT: KC下軌 走平未打開" in reason

def test_unclosed_bar_vetoed():
    """盤中未收盤 K 棒一票否決"""
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0, 'is_closed': False}
    prev_bar = {'close': 99.0, 'is_closed': True}
    curr_ind = create_indicators(100, 100, 100, 100, 100)
    prev_ind = create_indicators(100, 100, 100, 100, 100)

    can_open, reason = gate.can_open('LONG', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert "VETO: 拒絕盤中未收盤 K 棒" in reason

def test_unknown_side():
    gate = FiveLinesConfluenceGate()
    bar = {'close': 100.0, 'is_closed': True}
    prev_bar = {'close': 100.0, 'is_closed': True}
    curr_ind = create_indicators(100, 100, 100, 100, 100)
    prev_ind = create_indicators(100, 100, 100, 100, 100)

    can_open, reason = gate.can_open('UNKNOWN', bar, prev_bar, curr_ind, prev_ind)
    assert can_open is False
    assert reason == "VETO: UNKNOWN_SIDE"
