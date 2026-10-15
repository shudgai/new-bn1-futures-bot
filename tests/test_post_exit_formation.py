import pytest
import pandas as pd
from core.services.entry_contract import evaluate_entry_contract

class DummyAccount:
    def __init__(self, trades):
        self.trades = trades
        self.positions = {}
        self.last_closed_at = {}

def make_frame(side, K1_ts, K2_ts, K3_ts, edge=0.049):
    sign = 1 if side == 'LONG' else -1
    kc_upper = edge
    kc_middle = edge - 0.001
    kc_lower = edge - 0.002
    
    if side == 'SHORT':
        kc_upper = edge + 0.002
        kc_middle = edge + 0.001
        kc_lower = edge
        
    data = [
        {'timestamp': K1_ts, 'open': edge - sign*0.001, 'high': edge+0.01, 'low': edge-0.01, 'close': edge + sign*0.001, 'atr': 0.01, 'kc_upper': kc_upper, 'kc_middle': kc_middle, 'kc_lower': kc_lower, 'ma5': edge-sign*0.001, 'ma15': edge-sign*0.002},
        {'timestamp': K2_ts, 'open': edge + sign*0.0005, 'high': edge+0.01, 'low': edge-0.01, 'close': edge + sign*0.002, 'atr': 0.01, 'kc_upper': kc_upper, 'kc_middle': kc_middle, 'kc_lower': kc_lower, 'ma5': edge+sign*0.002, 'ma15': edge-sign*0.002},
        {'timestamp': K3_ts, 'open': edge + sign*0.002, 'high': edge+sign*0.004, 'low': edge-sign*0.001, 'close': edge + sign*0.004, 'atr': 0.01, 'kc_upper': kc_upper, 'kc_middle': kc_middle, 'kc_lower': kc_lower, 'ma5': edge+sign*0.003, 'ma15': edge-sign*0.002}
    ]
    if side == 'SHORT':
        # for short, high must be greater than low
        data[2]['high'] = edge + 0.001
        data[2]['low'] = edge - 0.004

    df = pd.DataFrame(data)
    df.attrs['timeframe_ms'] = 60000
    df['is_closed'] = [True, True, False]
    return df



