import pytest
import pandas as pd
from core.services.entry_contract import evaluate_entry_contract

def test_kc_expansion_blocked_by_flat_ma15():
    data = []
    for i in range(20):
        data.append({
            'timestamp': 1000 + i*1000,
            'kc_middle': 100, 'kc_lower': 95, 'kc_upper': 105, 
            'ma15': 100, 'is_closed': True
        })
    data[-1]['kc_lower'] = 91
    data[-1]['kc_upper'] = 109 
    data[-1]['atr'] = 10
    
    data[-1]['kc_middle'] = 98
    data[-2]['kc_middle'] = 100
    data[-1]['ma5'] = 94
    data[-2]['ma5'] = 100
    data[-1]['ma15'] = 99.88
    data[-2]['ma15'] = 99.94
    data[-3]['ma15'] = 100
    data.append({'timestamp': 22000, 'open': 100, 'high': 110, 'low': 90, 'close': 105, 'is_closed': False})
    
    frame = pd.DataFrame(data)
    decision = {'side': 'SHORT', 'type': 'TRIGGER_A_KC_BREAKOUT'}
    diagnostics = {}
    
    from unittest.mock import patch
    with patch('core.services.entry_contract.evaluate_realtime_short_rail_breach') as mock_eval:
        mock_eval.return_value = {'action': 'ENTER', 'side': 'SHORT', 'pending_signal_id': 1}
        result = evaluate_entry_contract(frame, price=105, code=decision['type'], account=None, symbol="CAP/USDT", diagnostics=diagnostics)
    
    assert result is None
    assert diagnostics.get('reason') == 'BLOCKED_BY_FLAT_MA15_DURING_KC_EXPANSION'

def test_extreme_kc_expansion():
    data = []
    for i in range(20):
        data.append({
            'timestamp': 1000 + i*1000,
            'kc_middle': 100, 'kc_lower': 95, 'kc_upper': 105, 
            'ma15': 100, 'is_closed': True
        })
    data[-1]['kc_lower'] = 85
    data[-1]['kc_upper'] = 115 
    data[-1]['atr'] = 10
    
    data[-1]['kc_middle'] = 98
    data[-2]['kc_middle'] = 100
    data[-1]['ma5'] = 94
    data[-2]['ma5'] = 100
    data[-1]['ma15'] = 98
    data[-2]['ma15'] = 100
    data[-3]['ma15'] = 100
    data.append({'timestamp': 22000, 'open': 100, 'high': 110, 'low': 90, 'close': 105, 'is_closed': False})
    
    frame = pd.DataFrame(data)
    decision = {'side': 'SHORT', 'type': 'TRIGGER_A_KC_BREAKOUT'}
    diagnostics = {}
    
    from unittest.mock import patch
    with patch('core.services.entry_contract.evaluate_realtime_short_rail_breach') as mock_eval:
        mock_eval.return_value = {'action': 'ENTER', 'side': 'SHORT', 'pending_signal_id': 1}
        result = evaluate_entry_contract(frame, price=105, code=decision['type'], account=None, symbol="CAP/USDT", diagnostics=diagnostics)
    
    assert result is None
    assert diagnostics.get('reason') == 'BLOCKED_BY_EXTREME_KC_EXPANSION'

def test_kc_expansion_allowed_initial_steep_ma15():
    data = []
    for i in range(20):
        data.append({
            'timestamp': 1000 + i*1000,
            'kc_middle': 100, 'kc_lower': 95, 'kc_upper': 105, 
            'ma15': 100, 'is_closed': True
        })
    data[-1]['kc_lower'] = 90
    data[-1]['kc_upper'] = 110 
    data[-1]['atr'] = 10
    
    data[-1]['kc_middle'] = 98
    data[-2]['kc_middle'] = 100
    data[-1]['ma5'] = 94
    data[-2]['ma5'] = 100
    data[-1]['ma15'] = 95
    data[-2]['ma15'] = 100
    data[-3]['ma15'] = 100
    
    data.append({'timestamp': 22000, 'open': 100, 'high': 110, 'low': 90, 'close': 105, 'is_closed': False})
    
    frame = pd.DataFrame(data)
    decision = {'side': 'SHORT', 'type': 'TRIGGER_A_KC_BREAKOUT'}
    diagnostics = {}
    
    from unittest.mock import patch
    with patch('core.services.entry_contract.evaluate_realtime_short_rail_breach') as mock_eval:
        mock_eval.return_value = {'action': 'ENTER', 'side': 'SHORT', 'pending_signal_id': 1}
        result = evaluate_entry_contract(frame, price=105, code=decision['type'], account=None, symbol="CAP/USDT", diagnostics=diagnostics)
    
    assert diagnostics.get('reason') not in ['BLOCKED_BY_FLAT_MA15_DURING_KC_EXPANSION', 'BLOCKED_BY_EXTREME_KC_EXPANSION']

