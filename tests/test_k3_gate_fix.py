import pytest
import pandas as pd
from core.services.kc_pending_entry import evaluate_kc_pending_entry

def build_test_frame_long():
    data = [
        {"timestamp": 1791030900000.0, "open": 0.04966, "high": 0.0502, "low": 0.04946, "close": 0.0499,
         "ma3": 0.04985333, "ma5": 0.0497, "ma15": 0.04938133, "atr": 0.000503, "kc_upper": 0.049712, "kc_middle": 0.049209, "kc_lower": 0.048706},
        {"timestamp": 1791030960000.0, "open": 0.04991, "high": 0.05003, "low": 0.04975, "close": 0.04998,
         "ma3": 0.04984666, "ma5": 0.0498, "ma15": 0.04936666, "atr": 0.000504, "kc_upper": 0.049786, "kc_middle": 0.049282, "kc_lower": 0.048778},
    ]
    df = pd.DataFrame(data)
    df.attrs['timeframe_ms'] = 60000
    df['is_closed'] = True
    return df

def build_test_frame_short():
    data = [
        {"timestamp": 1791030900000.0, "open": 0.05034, "high": 0.05054, "low": 0.0498, "close": 0.0501,
         "ma3": 0.04914667, "ma5": 0.0495, "ma15": 0.04961867, "atr": 0.000503, "kc_upper": 0.051294, "kc_middle": 0.050791, "kc_lower": 0.050288},
        {"timestamp": 1791030960000.0, "open": 0.05009, "high": 0.05025, "low": 0.04997, "close": 0.05002,
         "ma3": 0.04915334, "ma5": 0.0494, "ma15": 0.04963334, "atr": 0.000504, "kc_upper": 0.051222, "kc_middle": 0.050718, "kc_lower": 0.050214},
    ]
    df = pd.DataFrame(data)
    df.attrs['timeframe_ms'] = 60000
    df['is_closed'] = True
    return df

def build_live_row(open_px, high_px, low_px, close_px, is_long=True):
    return pd.Series(dict(
        timestamp=1791031020000.0,
        open=open_px,
        high=high_px,
        low=low_px,
        close=close_px,
        kc_upper=0.049786 if is_long else 0.051222,
        kc_middle=0.049282 if is_long else 0.050718,
        kc_lower=0.048778 if is_long else 0.050214,
        is_closed=False
    ))

def test_long_neutral_k3_regression():
    df = build_test_frame_long()
    live = build_live_row(0.04997, 0.04997, 0.04997, 0.04997)
    decision = evaluate_kc_pending_entry(df, quote=0.04997, code='KC_3BAR_CONFIRM_LONG', live=live)
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_SAME_COLOR'

def test_long_red_k3():
    df = build_test_frame_long()
    live = build_live_row(0.04997, 0.05000, 0.04990, 0.04996)
    decision = evaluate_kc_pending_entry(df, quote=0.04996, code='KC_3BAR_CONFIRM_LONG', live=live)
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'BLOCKED_THIRD_OPPOSITE_BODY'

def test_long_green_k3_small_ratio():
    df = build_test_frame_long()
    live = build_live_row(0.04997, 0.05020, 0.04980, 0.04998)
    decision = evaluate_kc_pending_entry(df, quote=0.04998, code='KC_3BAR_CONFIRM_LONG', live=live)
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_STRONG_BODY'

def test_long_green_k3_exact_10_percent():
    df = build_test_frame_long()
    live = build_live_row(0.04997, 0.05007, 0.04997, 0.04998)
    decision = evaluate_kc_pending_entry(df, quote=0.04998, code='KC_3BAR_CONFIRM_LONG', live=live)
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_STRONG_BODY'
    
def test_long_green_k3_valid():
    df = build_test_frame_long()
    live = build_live_row(0.04997, 0.05007, 0.04997, 0.05000)
    decision = evaluate_kc_pending_entry(df, quote=0.05000, code='KC_3BAR_CONFIRM_LONG', live=live)
    assert decision['action'] == 'ENTER'

def test_short_green_k3():
    df = build_test_frame_short()
    live = build_live_row(0.05003, 0.05010, 0.05000, 0.05004, is_long=False)
    decision = evaluate_kc_pending_entry(df, quote=0.05004, code='KC_3BAR_CONFIRM_SHORT', live=live)
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'BLOCKED_THIRD_OPPOSITE_BODY'

def test_short_neutral_k3():
    df = build_test_frame_short()
    live = build_live_row(0.05003, 0.05003, 0.05003, 0.05003, is_long=False)
    decision = evaluate_kc_pending_entry(df, quote=0.05003, code='KC_3BAR_CONFIRM_SHORT', live=live)
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_SAME_COLOR'

def test_short_red_k3_small_ratio():
    df = build_test_frame_short()
    live = build_live_row(0.05003, 0.05023, 0.04983, 0.05002, is_long=False)
    decision = evaluate_kc_pending_entry(df, quote=0.05002, code='KC_3BAR_CONFIRM_SHORT', live=live)
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_STRONG_BODY'

def test_short_red_k3_exact_10_percent():
    df = build_test_frame_short()
    live = build_live_row(0.05003, 0.05003, 0.04993, 0.05002, is_long=False)
    decision = evaluate_kc_pending_entry(df, quote=0.05002, code='KC_3BAR_CONFIRM_SHORT', live=live)
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_STRONG_BODY'

def test_short_red_k3_valid():
    df = build_test_frame_short()
    live = build_live_row(0.05003, 0.05003, 0.04993, 0.05000, is_long=False)
    decision = evaluate_kc_pending_entry(df, quote=0.05000, code='KC_3BAR_CONFIRM_SHORT', live=live)
    assert decision['action'] == 'ENTER'
