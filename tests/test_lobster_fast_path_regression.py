import pytest
import pandas as pd
import math
from core.services.entry_contract import evaluate_entry_contract

def make_lobster_frame():
    data = []
    base_ts = 1790965020000 - 3*60000
    for i in range(4):
        data.append({
            'timestamp': base_ts + i*60000,
            'open': 0.04200, 'high': 0.04200, 'low': 0.04150, 'close': 0.04169,
            'kc_upper': 0.04250, 'kc_lower': 0.04115, 'kc_middle': 0.04180,
            'ma3': 0.04180, 'ma15': 0.04200, 'atr': 0.000387,
            'is_closed': True
        })
    df = pd.DataFrame(data)
    # 02:17 forming bar (K1)
    df.iloc[-1, df.columns.get_loc('is_closed')] = False
    df.iloc[-1, df.columns.get_loc('open')] = 0.04169
    # ensure dataframe has attrs
    df.attrs['timeframe_ms'] = 60000
    return df

def test_lobster_fast_path_trigger():
    df = make_lobster_frame()
    kc_lower = float(df.iloc[-1]['kc_lower'])
    
    # Still inside, NO ENTRY
    res1 = evaluate_entry_contract(df, kc_lower + 0.00010)
    assert res1 is None
    
    # Below lower, but body < 0.5 ATR
    res2 = evaluate_entry_contract(df, df.iloc[-1]['open'] - 0.000100)
    assert res2 is None
    
    # Trigger exact
    trigger_price = kc_lower - 0.00001
    res3 = evaluate_entry_contract(df, trigger_price)
    assert res3 is not None
    assert res3['action'] == 'ENTER'
    assert res3['side'] == 'SHORT'
    assert res3['type'] == 'KC_LIVE_BODY_BREAKOUT_SHORT'

def test_boundary_conditions():
    df = make_lobster_frame()
    atr = df.iloc[-2]['atr']
    lower = df.iloc[-1]['kc_lower']
    opened = df.iloc[-1]['open']
    
    # SHORT
    price_not_below = lower + 0.00001
    assert evaluate_entry_contract(df, price_not_below) is None
    
    # opened already below lower
    df_already_below = df.copy()
    df_already_below.iloc[-1, df_already_below.columns.get_loc('open')] = lower - 0.00010
    assert evaluate_entry_contract(df_already_below, lower - 0.00050) is None
