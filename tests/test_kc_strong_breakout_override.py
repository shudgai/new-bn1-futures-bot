"""
Test KC Strong Breakout Override.
"""
import pandas as pd
from core.services.kc_pending_entry import evaluate_kc_pending_entry, _INVALIDATED_SIGNALS

def build_df(distance, body_sign=1, ma5_slope=1, ma5_ma15_sign=1, waited=1, is_long=True):
    side_sign = 1 if is_long else -1
    atr = 1.0
    kc_upper = 100.0
    kc_lower = 90.0
    kc_edge = kc_upper if is_long else kc_lower
    
    timestamp = 1600000000000
    rows = []
    
    # Bar1: breakout
    b1_open = kc_edge - (side_sign * 0.5)
    b1_close = kc_edge + (side_sign * 0.1)
    b1_ma5 = 95.0
    b1_ma15 = 95.0 - (side_sign * 1.0)
    
    rows.append({'timestamp': timestamp, 'open': b1_open, 'close': b1_close, 'high': max(b1_open, b1_close)+0.1, 'low': min(b1_open, b1_close)-0.1, 'atr': atr, 'kc_upper': kc_upper, 'kc_middle': 95.0, 'kc_lower': kc_lower, 'ma5': b1_ma5, 'ma15': b1_ma15})
    
    # Bar2: confirmation
    timestamp += 60000
    b2_open = b1_close
    b2_close = b2_open + (side_sign * 0.1)
    b2_ma5 = b1_ma5 + (side_sign * 0.1)
    b2_ma15 = b1_ma15
    
    rows.append({'timestamp': timestamp, 'open': b2_open, 'close': b2_close, 'high': max(b2_open, b2_close)+0.1, 'low': min(b2_open, b2_close)-0.1, 'atr': atr, 'kc_upper': kc_upper, 'kc_middle': 95.0, 'kc_lower': kc_lower, 'ma5': b2_ma5, 'ma15': b2_ma15})
    
    # Intervening bars if waited > 1
    prior_ma5 = b2_ma5
    for _ in range(waited - 1):
        timestamp += 60000
        # just inside to wait, body flat
        b_open = kc_edge - (side_sign * 0.1)
        b_close = b_open
        b_ma5 = prior_ma5
        rows.append({'timestamp': timestamp, 'open': b_open, 'close': b_close, 'high': b_open, 'low': b_open, 'atr': atr, 'kc_upper': kc_upper, 'kc_middle': 95.0, 'kc_lower': kc_lower, 'ma5': b_ma5, 'ma15': b2_ma15})
        prior_ma5 = b_ma5

    # Bar3: candidate
    timestamp += 60000
    b3_close = kc_edge + (side_sign * distance)
    b3_open = b3_close - (side_sign * body_sign * 0.1)
    b3_ma5 = prior_ma5 + (side_sign * ma5_slope * 0.1)
    b3_ma15 = b3_ma5 - (side_sign * ma5_ma15_sign * 1.0)
    
    rows.append({'timestamp': timestamp, 'open': b3_open, 'close': b3_close, 'high': max(b3_open, b3_close)+0.1, 'low': min(b3_open, b3_close)-0.1, 'atr': atr, 'kc_upper': kc_upper, 'kc_middle': 95.0, 'kc_lower': kc_lower, 'ma5': b3_ma5, 'ma15': b3_ma15})
    
    return pd.DataFrame(rows)





