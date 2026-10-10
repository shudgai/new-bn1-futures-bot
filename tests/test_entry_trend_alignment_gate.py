import pytest
import pandas as pd
from core.services.entry_contract import evaluate_entry_contract

def test_trend_alignment_gate_blocks_short_when_kc_up():
    """
    Simulates the CAP/USDT scenario where KC is UP (or flat), MA15 is flat,
    and a SHORT entry is attempted during consolidation.
    It should be BLOCKED_BY_TREND_MISALIGNMENT_SHORT.
    """
    # Create fake bars
    # ATR = 10, slope_threshold = 0.05
    data = [
        # Bar 1 (prev closed)
        {'timestamp': 1000, 'open': 100, 'high': 110, 'low': 90, 'close': 105,
         'atr': 10, 'kc_lower': 90, 'kc_middle': 100, 'kc_upper': 110,
         'ma5': 102, 'ma15': 100, 'is_closed': True},
        # Bar 2 (last closed) - KC middle increases (kc_slope = 2) -> UP
        {'timestamp': 2000, 'open': 105, 'high': 115, 'low': 95, 'close': 110,
         'atr': 10, 'kc_lower': 92, 'kc_middle': 102, 'kc_upper': 112,
         'ma5': 101, 'ma15': 100, 'is_closed': True}, # ma5 goes down slightly, ma15 flat
        # Bar 3 (live forming)
        {'timestamp': 3000, 'open': 110, 'high': 110, 'low': 90, 'close': 90, 'is_closed': False}
    ]
    frame = pd.DataFrame(data)

    decision = {
        'side': 'SHORT',
        'type': 'TRIGGER_A_KC_BREAKOUT',
    }

    diagnostics = {}
    from unittest.mock import patch
    with patch('core.services.entry_contract.evaluate_realtime_short_rail_breach') as mock_eval:
        mock_eval.return_value = {'action': 'ENTER', 'side': 'SHORT', 'pending_signal_id': 1}
        result = evaluate_entry_contract(frame, price=90, code=decision['type'], account=None, symbol="CAP/USDT", diagnostics=diagnostics)

    
    assert result is None
    assert diagnostics.get('reason') == 'BLOCKED_BY_TREND_MISALIGNMENT_SHORT'

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
    frame = pd.DataFrame(data)

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

