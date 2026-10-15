import pytest
import pandas as pd
from core.services.entry_contract import evaluate_entry_contract


def with_chop_history(data):
    first = data[0].copy()
    data[-1].setdefault('atr', 10)
    data[-1].setdefault('ma15', first['ma15'])
    data[-1].setdefault('ma5', first['ma5'])
    for key in ('kc_lower', 'kc_middle', 'kc_upper'):
        data[-1].setdefault(key, data[-2][key])
    padding = []
    for offset in range(7, 0, -1):
        row = first.copy()
        row['timestamp'] = first['timestamp'] - offset * 60000
        row['is_closed'] = True
        padding.append(row)
    return pd.DataFrame(padding + data)



def test_trend_alignment_gate_passes_when_aligned():
    """
    Simulates a properly aligned SHORT entry.
    """
    # Create fake bars
    # ATR = 10, slope_threshold = 0.05
    data = [
        # Bar 1 (prev closed)
        {'timestamp': 1000, 'open': 100, 'high': 110, 'low': 90, 'close': 95,
         'atr': 10, 'kc_lower': 90, 'kc_middle': 100, 'kc_upper': 110,
         'ma5': 98, 'ma15': 102, 'is_closed': True},
        # Bar 2 (last closed) - KC middle decreases (kc_slope = -2) -> DOWN
        {'timestamp': 2000, 'open': 95, 'high': 100, 'low': 85, 'close': 88,
         'atr': 10, 'kc_lower': 88, 'kc_middle': 98, 'kc_upper': 108,
         'ma5': 96, 'ma15': 101, 'is_closed': True}, # ma5 goes down, ma15 goes down
        # Bar 3 (live forming)
        {'timestamp': 3000, 'open': 88, 'high': 90, 'low': 80, 'close': 80, 'is_closed': False}
    ]
    data[-1].update(ma5=95, ma15=100, atr=10)
    frame = with_chop_history(data)

    decision = {
        'side': 'SHORT',
        'type': 'TRIGGER_A_KC_BREAKOUT',
    }

    # evaluate_entry_contract requires some other fields maybe, but if it passes the gate,
    # we want to ensure it doesn't fail with BLOCKED_BY_TREND_MISALIGNMENT_SHORT.
    # Note: evaluate_entry_contract might fail for another reason.
    diagnostics = {}
    from unittest.mock import patch
    with patch('core.services.entry_contract.evaluate_realtime_short_rail_breach') as mock_eval:
        mock_eval.return_value = {'action': 'ENTER', 'side': 'SHORT', 'pending_signal_id': 1}
        result = evaluate_entry_contract(frame, price=80, code=decision['type'], account=None, symbol="TEST/USDT", diagnostics=diagnostics)

    
    assert diagnostics.get('reason') != 'BLOCKED_BY_TREND_MISALIGNMENT_SHORT'



