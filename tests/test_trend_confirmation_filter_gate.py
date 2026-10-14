import pytest
import pandas as pd
from core.gates.trend_confirmation_filter_gate import TrendConfirmationFilterGate
from core.services.gates.entry_gate import TrendConfirmationFilterGate as ExportedGate

def test_gate_export():
    assert TrendConfirmationFilterGate is ExportedGate

def make_df(rows):
    return pd.DataFrame(rows)

def test_long_passed_when_confirmed():
    gate = TrendConfirmationFilterGate()
    # 5 rows. iloc[-4] = idx 1, iloc[-3] = idx 2, iloc[-2] = idx 3, iloc[-1] = idx 4
    # kc_middle: idx 1=100.0, idx 2=100.2 (slope=0.2), idx 3=100.5 (slope=0.3) -> both > 0
    # idx 3 (curr): close=101.0 > kc_middle=100.5, ma5=101.2 > ma15=100.8, low=100.0 >= prev low=99.8
    rows = [
        {'close': 99.0, 'kc_middle': 99.8, 'ma5': 99.0, 'ma15': 99.5, 'high': 100.0, 'low': 98.5},
        {'close': 100.0, 'kc_middle': 100.0, 'ma5': 99.8, 'ma15': 99.9, 'high': 100.5, 'low': 99.5},
        {'close': 100.4, 'kc_middle': 100.2, 'ma5': 100.3, 'ma15': 100.1, 'high': 100.8, 'low': 99.8},
        {'close': 101.0, 'kc_middle': 100.5, 'ma5': 101.2, 'ma15': 100.8, 'high': 101.5, 'low': 100.0},
        {'close': 101.2, 'kc_middle': 100.6, 'ma5': 101.3, 'ma15': 100.9, 'high': 101.6, 'low': 100.5},
    ]
    df = make_df(rows)
    can_open, reason = gate.can_open('LONG', df)
    assert can_open is True
    assert reason == "PASSED_TREND_CONFIRMATION_LONG"

def test_long_rejected_when_single_bar_pullup():
    gate = TrendConfirmationFilterGate()
    # kc_middle: idx 1=100.5, idx 2=100.2 (slope = -0.3 <= 0), idx 3=100.6 (slope = +0.4 > 0)
    # 单根假翘头
    rows = [
        {'close': 100.0, 'kc_middle': 100.8, 'ma5': 100.0, 'ma15': 100.5, 'high': 101.0, 'low': 99.5},
        {'close': 99.5, 'kc_middle': 100.5, 'ma5': 99.5, 'ma15': 100.3, 'high': 100.2, 'low': 99.0},
        {'close': 99.0, 'kc_middle': 100.2, 'ma5': 99.2, 'ma15': 100.1, 'high': 99.8, 'low': 98.8},
        {'close': 101.0, 'kc_middle': 100.6, 'ma5': 100.8, 'ma15': 100.2, 'high': 101.5, 'low': 99.0},
        {'close': 101.2, 'kc_middle': 100.7, 'ma5': 101.0, 'ma15': 100.3, 'high': 101.6, 'low': 100.5},
    ]
    df = make_df(rows)
    can_open, reason = gate.can_open('LONG', df)
    assert can_open is False
    assert "單根假翹頭" in reason

def test_long_rejected_when_ma5_below_ma15():
    gate = TrendConfirmationFilterGate()
    # kc_middle continuous up, but ma5 <= ma15
    rows = [
        {'close': 99.0, 'kc_middle': 99.8, 'ma5': 99.0, 'ma15': 99.5, 'high': 100.0, 'low': 98.5},
        {'close': 100.0, 'kc_middle': 100.0, 'ma5': 99.8, 'ma15': 99.9, 'high': 100.5, 'low': 99.5},
        {'close': 100.4, 'kc_middle': 100.2, 'ma5': 100.1, 'ma15': 100.3, 'high': 100.8, 'low': 99.8},
        {'close': 101.0, 'kc_middle': 100.5, 'ma5': 100.5, 'ma15': 100.8, 'high': 101.5, 'low': 100.0},
        {'close': 101.2, 'kc_middle': 100.6, 'ma5': 100.6, 'ma15': 100.9, 'high': 101.6, 'low': 100.5},
    ]
    df = make_df(rows)
    can_open, reason = gate.can_open('LONG', df)
    assert can_open is False
    assert "MA5 尚未站上 MA15" in reason

def test_long_rejected_when_close_below_kc_middle():
    gate = TrendConfirmationFilterGate()
    # close <= kc_middle at curr_bar
    rows = [
        {'close': 99.0, 'kc_middle': 99.8, 'ma5': 99.0, 'ma15': 99.5, 'high': 100.0, 'low': 98.5},
        {'close': 100.0, 'kc_middle': 100.0, 'ma5': 99.8, 'ma15': 99.9, 'high': 100.5, 'low': 99.5},
        {'close': 100.4, 'kc_middle': 100.2, 'ma5': 100.3, 'ma15': 100.1, 'high': 100.8, 'low': 99.8},
        {'close': 100.3, 'kc_middle': 100.5, 'ma5': 101.2, 'ma15': 100.8, 'high': 101.5, 'low': 100.0},
        {'close': 101.2, 'kc_middle': 100.6, 'ma5': 101.3, 'ma15': 100.9, 'high': 101.6, 'low': 100.5},
    ]
    df = make_df(rows)
    can_open, reason = gate.can_open('LONG', df)
    assert can_open is False
    assert "弱勢反彈，禁止開多" in reason

def test_long_rejected_when_broken_previous_low():
    gate = TrendConfirmationFilterGate()
    # curr_bar low < prev_bar low
    rows = [
        {'close': 99.0, 'kc_middle': 99.8, 'ma5': 99.0, 'ma15': 99.5, 'high': 100.0, 'low': 98.5},
        {'close': 100.0, 'kc_middle': 100.0, 'ma5': 99.8, 'ma15': 99.9, 'high': 100.5, 'low': 99.5},
        {'close': 100.4, 'kc_middle': 100.2, 'ma5': 100.3, 'ma15': 100.1, 'high': 100.8, 'low': 99.8},
        {'close': 101.0, 'kc_middle': 100.5, 'ma5': 101.2, 'ma15': 100.8, 'high': 101.5, 'low': 99.5},
        {'close': 101.2, 'kc_middle': 100.6, 'ma5': 101.3, 'ma15': 100.9, 'high': 101.6, 'low': 100.5},
    ]
    df = make_df(rows)
    can_open, reason = gate.can_open('LONG', df)
    assert can_open is False
    assert "底部未確立" in reason

def test_short_passed_when_confirmed():
    gate = TrendConfirmationFilterGate()
    # kc_middle: idx 1=100.0, idx 2=99.8 (slope=-0.2), idx 3=99.5 (slope=-0.3) -> both < 0
    # idx 3 (curr): close=99.0 < kc_middle=99.5, ma5=98.8 < ma15=99.2, high=99.8 <= prev high=100.0
    rows = [
        {'close': 101.0, 'kc_middle': 100.2, 'ma5': 101.0, 'ma15': 100.5, 'high': 101.5, 'low': 100.0},
        {'close': 100.0, 'kc_middle': 100.0, 'ma5': 100.2, 'ma15': 100.1, 'high': 100.5, 'low': 99.5},
        {'close': 99.6, 'kc_middle': 99.8, 'ma5': 99.7, 'ma15': 99.9, 'high': 100.0, 'low': 99.2},
        {'close': 99.0, 'kc_middle': 99.5, 'ma5': 98.8, 'ma15': 99.2, 'high': 99.8, 'low': 98.5},
        {'close': 98.8, 'kc_middle': 99.4, 'ma5': 98.7, 'ma15': 99.1, 'high': 99.5, 'low': 98.4},
    ]
    df = make_df(rows)
    can_open, reason = gate.can_open('SHORT', df)
    assert can_open is True
    assert reason == "PASSED_TREND_CONFIRMATION_SHORT"

def test_short_rejected_when_single_bar_dump():
    gate = TrendConfirmationFilterGate()
    # kc_middle: idx 1=99.5, idx 2=99.8 (slope = +0.3 >= 0), idx 3=99.4 (slope = -0.4 < 0)
    # 单根假低头
    rows = [
        {'close': 99.0, 'kc_middle': 99.2, 'ma5': 99.0, 'ma15': 99.5, 'high': 99.5, 'low': 98.5},
        {'close': 99.5, 'kc_middle': 99.5, 'ma5': 99.5, 'ma15': 99.7, 'high': 100.0, 'low': 99.0},
        {'close': 100.0, 'kc_middle': 99.8, 'ma5': 100.1, 'ma15': 99.9, 'high': 100.5, 'low': 99.5},
        {'close': 98.5, 'kc_middle': 99.4, 'ma5': 99.2, 'ma15': 99.8, 'high': 99.5, 'low': 98.0},
        {'close': 98.3, 'kc_middle': 99.3, 'ma5': 99.0, 'ma15': 99.7, 'high': 99.0, 'low': 98.0},
    ]
    df = make_df(rows)
    can_open, reason = gate.can_open('SHORT', df)
    assert can_open is False
    assert "單根假低頭" in reason

def test_short_rejected_when_ma5_above_ma15():
    gate = TrendConfirmationFilterGate()
    rows = [
        {'close': 101.0, 'kc_middle': 100.2, 'ma5': 101.0, 'ma15': 100.5, 'high': 101.5, 'low': 100.0},
        {'close': 100.0, 'kc_middle': 100.0, 'ma5': 100.2, 'ma15': 100.1, 'high': 100.5, 'low': 99.5},
        {'close': 99.6, 'kc_middle': 99.8, 'ma5': 99.9, 'ma15': 99.7, 'high': 100.0, 'low': 99.2},
        {'close': 99.0, 'kc_middle': 99.5, 'ma5': 99.3, 'ma15': 99.2, 'high': 99.8, 'low': 98.5},
        {'close': 98.8, 'kc_middle': 99.4, 'ma5': 99.1, 'ma15': 99.1, 'high': 99.5, 'low': 98.4},
    ]
    df = make_df(rows)
    can_open, reason = gate.can_open('SHORT', df)
    assert can_open is False
    assert "MA5 尚未跌破 MA15" in reason

def test_short_rejected_when_close_above_kc_middle():
    gate = TrendConfirmationFilterGate()
    rows = [
        {'close': 101.0, 'kc_middle': 100.2, 'ma5': 101.0, 'ma15': 100.5, 'high': 101.5, 'low': 100.0},
        {'close': 100.0, 'kc_middle': 100.0, 'ma5': 100.2, 'ma15': 100.1, 'high': 100.5, 'low': 99.5},
        {'close': 99.6, 'kc_middle': 99.8, 'ma5': 99.7, 'ma15': 99.9, 'high': 100.0, 'low': 99.2},
        {'close': 99.6, 'kc_middle': 99.5, 'ma5': 98.8, 'ma15': 99.2, 'high': 99.8, 'low': 98.5},
        {'close': 98.8, 'kc_middle': 99.4, 'ma5': 98.7, 'ma15': 99.1, 'high': 99.5, 'low': 98.4},
    ]
    df = make_df(rows)
    can_open, reason = gate.can_open('SHORT', df)
    assert can_open is False
    assert "強勢回調，禁止開空" in reason

def test_short_rejected_when_broken_previous_high():
    gate = TrendConfirmationFilterGate()
    rows = [
        {'close': 101.0, 'kc_middle': 100.2, 'ma5': 101.0, 'ma15': 100.5, 'high': 101.5, 'low': 100.0},
        {'close': 100.0, 'kc_middle': 100.0, 'ma5': 100.2, 'ma15': 100.1, 'high': 100.5, 'low': 99.5},
        {'close': 99.6, 'kc_middle': 99.8, 'ma5': 99.7, 'ma15': 99.9, 'high': 100.0, 'low': 99.2},
        {'close': 99.0, 'kc_middle': 99.5, 'ma5': 98.8, 'ma15': 99.2, 'high': 100.5, 'low': 98.5},
        {'close': 98.8, 'kc_middle': 99.4, 'ma5': 98.7, 'ma15': 99.1, 'high': 99.5, 'low': 98.4},
    ]
    df = make_df(rows)
    can_open, reason = gate.can_open('SHORT', df)
    assert can_open is False
    assert "頂部未確立" in reason

def test_insufficient_data():
    gate = TrendConfirmationFilterGate()
    df = make_df([{'close': 100}])
    can_open, reason = gate.can_open('LONG', df)
    assert can_open is False
    assert "數據長度不足" in reason

def test_unknown_side():
    gate = TrendConfirmationFilterGate()
    rows = [{'close': 100, 'kc_middle': 100, 'ma5': 100, 'ma15': 100, 'high': 101, 'low': 99}] * 5
    df = make_df(rows)
    can_open, reason = gate.can_open('UNKNOWN', df)
    assert can_open is False
    assert reason == "VETO: UNKNOWN_SIDE"
