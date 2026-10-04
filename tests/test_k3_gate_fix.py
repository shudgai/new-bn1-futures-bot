import pytest
import pandas as pd
from core.services.kc_pending_entry import evaluate_kc_pending_entry

def build_test_frame_long(third_bar=None):
    data = [
        {"timestamp": 1791030900000.0, "open": 0.04966, "high": 0.0502, "low": 0.04946, "close": 0.0499,
         "ma3": 0.04985333, "ma5": 0.0497, "ma15": 0.04938133, "atr": 0.000503, "kc_upper": 0.049712, "kc_middle": 0.049209, "kc_lower": 0.048706, "is_closed": True},
        {"timestamp": 1791030960000.0, "open": 0.04991, "high": 0.05003, "low": 0.04975, "close": 0.04998,
         "ma3": 0.04984666, "ma5": 0.0498, "ma15": 0.04936666, "atr": 0.000504, "kc_upper": 0.049786, "kc_middle": 0.049282, "kc_lower": 0.048778, "is_closed": True},
    ]
    if third_bar is not None:
        data.append(third_bar)
    df = pd.DataFrame(data)
    df.attrs['timeframe_ms'] = 60000
    return df

def build_test_frame_short(third_bar=None):
    data = [
        {"timestamp": 1791030900000.0, "open": 0.05034, "high": 0.05054, "low": 0.0498, "close": 0.0501,
         "ma3": 0.04914667, "ma5": 0.0495, "ma15": 0.04961867, "atr": 0.000503, "kc_upper": 0.051294, "kc_middle": 0.050791, "kc_lower": 0.050288, "is_closed": True},
        {"timestamp": 1791030960000.0, "open": 0.05009, "high": 0.05025, "low": 0.04997, "close": 0.05002,
         "ma3": 0.04915334, "ma5": 0.0494, "ma15": 0.04963334, "atr": 0.000504, "kc_upper": 0.051222, "kc_middle": 0.050718, "kc_lower": 0.050214, "is_closed": True},
    ]
    if third_bar is not None:
        data.append(third_bar)
    df = pd.DataFrame(data)
    df.attrs['timeframe_ms'] = 60000
    return df

def build_k3_row(open_px, high_px, low_px, close_px, is_long=True):
    return dict(
        timestamp=1791031020000.0,
        open=open_px,
        high=high_px,
        low=low_px,
        close=close_px,
        ma3=0.04995 if is_long else 0.0492,
        ma5=0.0499 if is_long else 0.0493,
        ma15=0.04935 if is_long else 0.04965,
        atr=0.000504,
        kc_upper=0.049786 if is_long else 0.051222,
        kc_middle=0.049282 if is_long else 0.050718,
        kc_lower=0.048778 if is_long else 0.050214,
        is_closed=True
    )

def test_long_waits_for_third_bar_close():
    df = build_test_frame_long(third_bar=None)
    decision = evaluate_kc_pending_entry(df, quote=0.05010, code='KC_3BAR_CONFIRM_LONG')
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_BAR_CLOSE'

def test_long_neutral_k3_regression():
    k3 = build_k3_row(0.04997, 0.04997, 0.04997, 0.04997)
    df = build_test_frame_long(third_bar=k3)
    decision = evaluate_kc_pending_entry(df, quote=0.04997, code='KC_3BAR_CONFIRM_LONG')
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_SAME_COLOR'

def test_long_red_k3():
    k3 = build_k3_row(0.04997, 0.05000, 0.04990, 0.04996)
    df = build_test_frame_long(third_bar=k3)
    decision = evaluate_kc_pending_entry(df, quote=0.04996, code='KC_3BAR_CONFIRM_LONG')
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'BLOCKED_THIRD_OPPOSITE_BODY'

def test_long_green_k3_small_ratio():
    k3 = build_k3_row(0.04997, 0.05020, 0.04980, 0.04998)
    df = build_test_frame_long(third_bar=k3)
    decision = evaluate_kc_pending_entry(df, quote=0.04998, code='KC_3BAR_CONFIRM_LONG')
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_STRONG_BODY'

def test_long_green_k3_valid():
    k3 = build_k3_row(0.04997, 0.05015, 0.04997, 0.05010)
    df = build_test_frame_long(third_bar=k3)
    decision = evaluate_kc_pending_entry(df, quote=0.05010, code='KC_3BAR_CONFIRM_LONG')
    assert decision['action'] == 'ENTER'
    assert decision['entry_phase'] == 'KC_3BAR_CLOSED_CONFIRM'

def test_long_green_k3_insufficient_body_atr():
    k3 = build_k3_row(0.04997, 0.05007, 0.04997, 0.05000)
    df = build_test_frame_long(third_bar=k3)
    decision = evaluate_kc_pending_entry(df, quote=0.05000, code='KC_3BAR_CONFIRM_LONG')
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_MIN_BODY_ATR'

def test_long_green_k3_excessive_adverse_atr():
    k3 = build_k3_row(0.04997, 0.05015, 0.04970, 0.05010)
    df = build_test_frame_long(third_bar=k3)
    decision = evaluate_kc_pending_entry(df, quote=0.05010, code='KC_3BAR_CONFIRM_LONG')
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'BLOCKED_THIRD_ADVERSE_EXCURSION'

def test_short_waits_for_third_bar_close():
    df = build_test_frame_short(third_bar=None)
    decision = evaluate_kc_pending_entry(df, quote=0.04990, code='KC_3BAR_CONFIRM_SHORT')
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_BAR_CLOSE'

def test_short_green_k3():
    k3 = build_k3_row(0.05003, 0.05010, 0.05000, 0.05004, is_long=False)
    df = build_test_frame_short(third_bar=k3)
    decision = evaluate_kc_pending_entry(df, quote=0.05004, code='KC_3BAR_CONFIRM_SHORT')
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'BLOCKED_THIRD_OPPOSITE_BODY'

def test_short_neutral_k3():
    k3 = build_k3_row(0.05003, 0.05003, 0.05003, 0.05003, is_long=False)
    df = build_test_frame_short(third_bar=k3)
    decision = evaluate_kc_pending_entry(df, quote=0.05003, code='KC_3BAR_CONFIRM_SHORT')
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_SAME_COLOR'

def test_short_red_k3_small_ratio():
    k3 = build_k3_row(0.05003, 0.05023, 0.04983, 0.05002, is_long=False)
    df = build_test_frame_short(third_bar=k3)
    decision = evaluate_kc_pending_entry(df, quote=0.05002, code='KC_3BAR_CONFIRM_SHORT')
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_STRONG_BODY'

def test_short_red_k3_valid():
    k3 = build_k3_row(0.05003, 0.05003, 0.04985, 0.04990, is_long=False)
    df = build_test_frame_short(third_bar=k3)
    decision = evaluate_kc_pending_entry(df, quote=0.04990, code='KC_3BAR_CONFIRM_SHORT')
    assert decision['action'] == 'ENTER'
    assert decision['entry_phase'] == 'KC_3BAR_CLOSED_CONFIRM'

def test_short_red_k3_insufficient_body_atr():
    k3 = build_k3_row(0.05003, 0.05003, 0.04993, 0.05000, is_long=False)
    df = build_test_frame_short(third_bar=k3)
    decision = evaluate_kc_pending_entry(df, quote=0.05000, code='KC_3BAR_CONFIRM_SHORT')
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'WAIT_THIRD_MIN_BODY_ATR'

def test_short_red_k3_excessive_adverse_atr():
    k3 = build_k3_row(0.05003, 0.05030, 0.04985, 0.04990, is_long=False)
    df = build_test_frame_short(third_bar=k3)
    decision = evaluate_kc_pending_entry(df, quote=0.04990, code='KC_3BAR_CONFIRM_SHORT')
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'BLOCKED_THIRD_ADVERSE_EXCURSION'

def test_continuation_entry_after_missed_breakout():
    from core.services.entry_contract import evaluate_entry_contract
    # 5 bars in frame: K0 broke out, K1, K2, K3 closed outside, K4 live forming.
    # K1, K2, K3 cannot trigger 3-bar breakout because K1 opened already above kc_upper.
    # Continuation entry triggers KC_OUTSIDE_LONG.
    bars = [
        {"timestamp": 1791030840000.0, "open": 0.0494, "high": 0.0499, "low": 0.0493, "close": 0.0498,
         "ma3": 0.0496, "ma5": 0.0495, "ma15": 0.0492, "atr": 0.0005, "kc_upper": 0.0496, "kc_middle": 0.0491, "kc_lower": 0.0486, "is_closed": True},
        {"timestamp": 1791030900000.0, "open": 0.0498, "high": 0.0502, "low": 0.0497, "close": 0.0500,
         "ma3": 0.0498, "ma5": 0.0496, "ma15": 0.0493, "atr": 0.0005, "kc_upper": 0.04965, "kc_middle": 0.04915, "kc_lower": 0.04865, "is_closed": True},
        {"timestamp": 1791030960000.0, "open": 0.0500, "high": 0.0503, "low": 0.0499, "close": 0.0502,
         "ma3": 0.0500, "ma5": 0.0497, "ma15": 0.0494, "atr": 0.0005, "kc_upper": 0.0497, "kc_middle": 0.0492, "kc_lower": 0.0487, "is_closed": True},
        {"timestamp": 1791031020000.0, "open": 0.0502, "high": 0.0505, "low": 0.0501, "close": 0.0504,
         "ma3": 0.0502, "ma5": 0.0498, "ma15": 0.04945, "atr": 0.0005, "kc_upper": 0.04975, "kc_middle": 0.04925, "kc_lower": 0.04875, "is_closed": True},
        {"timestamp": 1791031080000.0, "open": 0.0504, "high": 0.0507, "low": 0.0504, "close": 0.0506,
         "ma3": 0.0504, "ma5": 0.0499, "ma15": 0.0495, "atr": 0.0005, "kc_upper": 0.0498, "kc_middle": 0.0493, "kc_lower": 0.0488, "is_closed": False},
    ]
    df = pd.DataFrame(bars)
    df.attrs['timeframe_ms'] = 60000
    res = evaluate_entry_contract(df, price=0.0506, symbol='BTC/USDT')
    assert res is not None
    assert res['action'] == 'ENTER'
    assert res['side'] == 'LONG'
    assert res['entry_phase'] == 'KC_CONTINUATION_ENTRY'

def test_flat_ma5_blocked():
    # K3 has identical ma5 to K2 (flat MA5 slope)
    k3 = build_k3_row(0.04997, 0.05015, 0.04997, 0.05010)
    k3['ma5'] = 0.0498 # same as K2 ma5 (0.0498) -> delta = 0
    df = build_test_frame_long(third_bar=k3)
    decision = evaluate_kc_pending_entry(df, quote=0.05010, code='KC_3BAR_CONFIRM_LONG')
    assert decision['action'] == 'WAIT'
    assert decision['reason'] == 'BLOCKED_FLAT_MA5'




