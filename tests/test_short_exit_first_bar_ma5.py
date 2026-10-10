import pytest
from core.services.exits.realtime_profit_exit import _evaluate_realtime_core_exit_gates

def test_exit_short_on_ma5_invalidation():
    position = {
        'side': 'SHORT',
        'entry_price': 100,
        'qty': 1,
        'margin': 10,
        'open_timestamp': 0.0
    }
    meta = {}
    snapshot = {
        'live_bar_ms': 2000,
        'closed_bar_ms': 1000,
        'live_kc_lower': 90,
        'live_kc_upper': 110,
        'live_kc_middle': 100,
        'atr': 2.0,
        'live_open': 98,
        'live_high': 99,
        'live_low': 97,
        
        # The key for this rule: closed_close > closed_ma5
        'last_close': 97.5,
        'ma5': 97.0
    }
    
    # stamp = 300000 to bypass is_early_hold_period (2 minutes)
    reason, updated = _evaluate_realtime_core_exit_gates(position, meta, 97.5, 300000, snapshot, 0.001, 0.001)
    
    assert reason == 'EXIT_SHORT_ON_MA5_INVALIDATION'

def test_trough_rejection_exit_confirmed():
    position = {
        'side': 'SHORT',
        'entry_price': 100,
        'qty': 1,
        'margin': 10,
        'open_timestamp': 0.0
    }
    meta = {}
    snapshot = {
        'live_bar_ms': 2000,
        'closed_bar_ms': 1000,
        'live_kc_lower': 90,
        'live_kc_upper': 110,
        'live_kc_middle': 100,
        'atr': 4.0, # 2.5 * ATR = 10
        
        # live bar: green and near high
        'live_open': 88.0,
        'live_low': 87.0,
        'live_high': 90.0,
        
        # the key for rule 1 not to trigger:
        'last_close': 88.0,
        'ma5': 89.0, # Not above MA5 yet
        
        'history_5': [
            {
                'o': 92.0,
                'c': 88.0,
                'h': 92.0,
                'l': 85.0 # lower shadow: 88 - 85 = 3. range: 92 - 85 = 7. 3/7 = 0.428 < 0.45? Wait, let's make it 0.5.
            }
        ]
    }
    
    # adjust history to meet 45% lower shadow
    snapshot['history_5'][0]['l'] = 84.0 # range 8. lower shadow 4 (88 - 84). 4/8 = 0.5 >= 0.45
    # quote = 89.5 (close to 90 high)
    # quote > live_open (89.5 > 88.0)
    # candle_range = 3.0 (90.0 - 87.0)
    # quote - live_low = 89.5 - 87.0 = 2.5. 2.5/3 = 0.833 >= 0.70
    # entry (100) - lowest_point (min(87.0, 84.0) = 84) = 16 >= 2.5*4 = 10
    
    reason, updated = _evaluate_realtime_core_exit_gates(position, meta, 89.5, 300000, snapshot, 0.001, 0.001)
    
    assert reason == 'EXIT_SHORT_TROUGH_REVERSAL_CONFIRMED'


def test_hard_stop_loss_trigger():
    position = {
        'side': 'SHORT',
        'entry_price': 100,
        'qty': 1,
        'margin': 10,
        'open_timestamp': 0.0
    }
    meta = {}
    snapshot = {
        'live_bar_ms': 2000,
        'closed_bar_ms': 1000,
        'live_kc_lower': 90,
        'live_kc_upper': 110,
        'live_kc_middle': 100,
        'atr': 2.0,
        'live_open': 103,
        'live_high': 104,
        'live_low': 102.8, # Small lower shadow to bypass TROUGH_REJECTION_EXIT
        'last_close': 102,
        'ma5': 105
    }
    
    # Current quote = 103.1
    # 100 -> 103.1 is 3.1 loss. Margin is 10.
    # 3.1 is > 1.5 * atr (which is 3.0). It should trigger HARD_STOP_LOSS
    reason, updated = _evaluate_realtime_core_exit_gates(position, meta, 103.1, 300000, snapshot, 0.0, 0.0)
    
    assert reason == 'HARD_STOP_LOSS'

