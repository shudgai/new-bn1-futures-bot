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

def test_current_kc3bar_weak_body_signals():
    # first.open <= kc_upper: 9 <= 9.5.
    # first.close > kc_upper: 10.5 > 9.5.
    # second.close > kc_upper: 10.5 > 9.5.
    # distance = (quote - kc_upper) / atr. quote=10.5, kc_upper=9.5, distance = 1.0 < 2.0.
    k1 = create_bar(10000, 9, 20, 8, 10.5, 9.5, 9, ma5=10, ma15=5) # ratio = 1.5 / 12 < 20%
    k2 = create_bar(70000, 10, 20, 8, 10.5, 9.5, 9, ma5=11, ma15=5)
    
    k3 = create_bar(130000, 10, 20, 8, 10.5, 9.5, 9, closed=False)
    
    df = pd.DataFrame([k1, k2])
    live_series = pd.Series(k3)
    
    # distance = 10.5 - 9.5 = 1.0 <= 2.0
    result = evaluate_kc_pending_entry(df, quote=10.5, live=live_series)
    assert result['action'] == 'ENTER'

def test_current_kc3bar_k3_doji_opens():
    k1 = create_bar(10000, 9, 20, 8, 10.5, 9.5, 9, ma5=10, ma15=5)
    k2 = create_bar(70000, 10, 20, 8, 10.5, 9.5, 9, ma5=11, ma15=5)
    
    # quote = 10.1, opening = 10. body = 0.1
    # distance = 10.1 - 9.5 = 0.6 <= 2.0
    k3 = create_bar(130000, 10, 20, 8, 10.1, 9.5, 9, closed=False)
    
    df = pd.DataFrame([k1, k2])
    live_series = pd.Series(k3)
    
    result = evaluate_kc_pending_entry(df, quote=10.1, live=live_series)
    assert result['action'] == 'ENTER'

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
