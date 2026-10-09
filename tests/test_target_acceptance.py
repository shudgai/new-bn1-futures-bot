import pytest
import pandas as pd
from core.services.kc_pending_entry import evaluate_kc_pending_entry

def create_bar(ts, o, h, l, c, kcu, kcl, ma5=10, ma15=5, atr=1, closed=True):
    return {
        'timestamp': float(ts),
        'open': float(o),
        'high': float(h),
        'low': float(l),
        'close': float(c),
        'kc_upper': float(kcu),
        'kc_lower': float(kcl),
        'ma5': float(ma5),
        'ma15': float(ma15),
        'atr': float(atr),
        'is_closed': closed
    }

def run_legacy_kc(first, second, live, quote):
    df = pd.DataFrame([first, second])
    live_series = pd.Series(live)
    return evaluate_kc_pending_entry(df, quote, live=live_series)

def test_formation_k1_ratio_lt_20():
    k1 = create_bar(10000, 9, 20, 8, 10.5, 9.5, 9, ma5=10, ma15=5) # < 20%
    k2 = create_bar(70000, 10, 20, 8, 10.5, 9.5, 9, ma5=11, ma15=5)
    k3 = create_bar(130000, 10, 20, 8, 10.5, 9.5, 9, closed=False)
    res = run_legacy_kc(k1, k2, k3, 10.5)
    assert res['action'] == 'WAIT', "EXPECTED_PRE_REFACTOR_FAILURE: KC_3BAR missing 20% gate"

def test_formation_k2_ratio_lt_20():
    k1 = create_bar(10000, 9, 20, 8, 10.5, 9.5, 9, ma5=10, ma15=5)
    k2 = create_bar(70000, 10, 20, 8, 10.5, 9.5, 9, ma5=11, ma15=5) # < 20%
    k3 = create_bar(130000, 10, 20, 8, 10.5, 9.5, 9, closed=False)
    res = run_legacy_kc(k1, k2, k3, 10.5)
    assert res['action'] == 'WAIT', "EXPECTED_PRE_REFACTOR_FAILURE: KC_3BAR missing 20% gate"

def test_formation_k1_not_outside():
    k1 = create_bar(10000, 9, 20, 8, 10.5, 14.5, 9, ma5=10, ma15=5) # close < kc_upper
    k2 = create_bar(70000, 10, 20, 8, 10.5, 9.5, 9, ma5=11, ma15=5)
    k3 = create_bar(130000, 10, 20, 8, 10.5, 9.5, 9, closed=False)
    res = run_legacy_kc(k1, k2, k3, 10.5)
    assert res['action'] == 'WAIT'

def test_formation_k2_not_outside():
    k1 = create_bar(10000, 9, 20, 8, 10.5, 9.5, 9, ma5=10, ma15=5)
    k2 = create_bar(70000, 10, 20, 8, 10.5, 14.5, 9, ma5=11, ma15=5) # close < kc_upper
    k3 = create_bar(130000, 10, 20, 8, 10.5, 9.5, 9, closed=False)
    res = run_legacy_kc(k1, k2, k3, 10.5)
    assert res['action'] == 'WAIT'

def test_k3_live_real_same_direction():
    k1 = create_bar(10000, 9, 20, 8, 10.5, 9.5, 9, ma5=10, ma15=5)
    k2 = create_bar(70000, 10, 20, 8, 10.5, 9.5, 9, ma5=11, ma15=5)
    k3 = create_bar(130000, 10, 20, 8, 10.5, 9.5, 9, closed=False)
    res = run_legacy_kc(k1, k2, k3, 10.5)
    assert res['action'] == 'ENTER'

def test_k3_live_same_direction_doji():
    k1 = create_bar(10000, 9, 20, 8, 10.5, 9.5, 9, ma5=10, ma15=5)
    k2 = create_bar(70000, 10, 20, 8, 10.5, 9.5, 9, ma5=11, ma15=5)
    k3 = create_bar(130000, 10, 20, 8, 10.1, 9.5, 9, closed=False) # doji
    res = run_legacy_kc(k1, k2, k3, 10.1)
    assert res['action'] == 'WAIT', "EXPECTED_PRE_REFACTOR_FAILURE: K3 Doji opens"

def test_k3_live_opposite():
    k1 = create_bar(10000, 9, 20, 8, 10.5, 9.5, 9, ma5=10, ma15=5)
    k2 = create_bar(70000, 10, 20, 8, 10.5, 9.5, 9, ma5=11, ma15=5)
    k3 = create_bar(130000, 10, 20, 8, 9.6, 9.5, 9, closed=False)
    res = run_legacy_kc(k1, k2, k3, 9.6)
    assert res['action'] == 'WAIT'

def test_k3_live_opposite_flips_real():
    k1 = create_bar(10000, 9, 20, 8, 10.5, 9.5, 9, ma5=10, ma15=5)
    k2 = create_bar(70000, 10, 20, 8, 10.5, 9.5, 9, ma5=11, ma15=5)
    k3_1 = create_bar(130000, 10, 20, 8, 9.6, 9.5, 9, closed=False)
    res1 = run_legacy_kc(k1, k2, k3_1, 9.6)
    assert res1['action'] == 'WAIT'
    
    k3_2 = create_bar(130000, 10, 20, 8, 10.5, 9.5, 9, closed=False)
    res2 = run_legacy_kc(k1, k2, k3_2, 10.5)
    assert res2['action'] == 'ENTER'

def test_k3_closed_doji_waits_k4():
    k1 = create_bar(10000, 9, 20, 8, 10.5, 9.5, 9, ma5=10, ma15=5)
    k2 = create_bar(70000, 10, 20, 8, 10.5, 9.5, 9, ma5=11, ma15=5)
    k3 = create_bar(130000, 10, 20, 8, 10.1, 9.5, 9, closed=True)
    k4_live = create_bar(190000, 10, 20, 8, 10.5, 9.5, 9, closed=False)
    
    res = run_legacy_kc(k2, k3, k4_live, 10.5)
    assert res['action'] == 'ENTER', "EXPECTED_PRE_REFACTOR_FAILURE: missing K4 state machine."

def test_k4_real_same_direction_ready():
    pytest.fail("EXPECTED_PRE_REFACTOR_FAILURE: missing K4 state machine.")

def test_k5_stale_state_blocked():
    pytest.fail("EXPECTED_PRE_REFACTOR_FAILURE: missing strict signal expiration (legacy relies on window slide).")

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
