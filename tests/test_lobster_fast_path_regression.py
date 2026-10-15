import pytest
import pandas as pd
import math
from core.services.entry_contract import evaluate_entry_contract

def make_lobster_frame():
    data = []
    base_ts = 1790965020000 - 9*60000
    for i in range(10):
        data.append({
            'timestamp': base_ts + i*60000,
            'open': 0.04200, 'high': 0.04200, 'low': 0.04150, 'close': 0.04169,
            'kc_upper': 0.04250, 'kc_lower': 0.04115, 'kc_middle': 0.04180,
            'ma3': 0.04180, 'ma5': 0.04180, 'ma15': 0.04200,
            'atr': 0.000387,
            'is_closed': True
        })
    df = pd.DataFrame(data)
    # 02:17 forming bar (K1)
    df.iloc[-1, df.columns.get_loc('is_closed')] = False
    df.iloc[-1, df.columns.get_loc('open')] = 0.04169
    # ensure dataframe has attrs
    df.attrs['timeframe_ms'] = 60000
    return df

def test_lobster_fast_path_respects_opposing_ma15_bias():
    df = make_lobster_frame()
    kc_lower = float(df.iloc[-1]['kc_lower'])
    
    # Still inside, NO ENTRY
    res1 = evaluate_entry_contract(df, kc_lower + 0.00010)
    assert res1 is None
    
    # Below lower, but body < 0.5 ATR
    res2 = evaluate_entry_contract(df, df.iloc[-1]['open'] - 0.000100)
    assert res2 is None
    
    # The raw lower-rail signal exists, but the opposing/extended MA15 trend
    # blocks the order at the shared authorization gate.
    trigger_price = kc_lower - 0.00001
    from core.gates.pipeline import pipeline
    assert pipeline.detect_realtime_breakout(
        df, trigger_price, side='SHORT',
    ) is not None
    res3 = evaluate_entry_contract(df, trigger_price)
    assert res3 is None

