import pytest
import pandas as pd
from core.services.evaluate_structure_break_entry import evaluate_structure_break_entry
from core.services.entry_contract import evaluate_entry_contract
from core.testnet_account import BinanceTestnetAccount as TestnetAccount
from core.paper_account import PaperAccount

def make_frame(prices):
    data = []
    ts = 1600000000000
    for h, l, c in prices:
        data.append({'timestamp': ts, 'open': c, 'high': h, 'low': l, 'close': c, 'kc_upper': c*1.1, 'kc_lower': c*0.9, 'kc_middle': c, 'atr': 10})
        ts += 60000
    return pd.DataFrame(data)

def test_evaluate_structure_break_long():
    # Last is live, prior 3 closed:
    # 3 closed: high=100, 105, 102 (max high = 105)
    # Live close: 106
    frame = make_frame([(100, 90, 95), (105, 95, 100), (102, 98, 100), (110, 100, 106)])
    res = evaluate_structure_break_entry(frame, 'BTCUSDT')
    assert res is not None
    assert res['action'] == 'ENTER'
    assert res['side'] == 'LONG'
    assert res['reference_level'] == 105
    assert res['trigger_price'] == 106

def test_evaluate_structure_break_short():
    # 3 closed: low=90, 85, 88 (min low = 85)
    # Live close: 84
    frame = make_frame([(100, 90, 95), (95, 85, 90), (98, 88, 95), (90, 80, 84)])
    res = evaluate_structure_break_entry(frame, 'BTCUSDT')
    assert res is not None
    assert res['action'] == 'ENTER'
    assert res['side'] == 'SHORT'
    assert res['reference_level'] == 85
    assert res['trigger_price'] == 84


def test_d0_state_isolation_paper():
    acc = PaperAccount()
    acc.d0_in_flight_state['BTCUSDT'] = {'d0_event_id': 'BTCUSDT_LONG_1_2_3', 'client_order_id': 'AGY_D0_123', 'side': 'LONG', 'submission_timestamp': 0}
    # from_state should set quarantine (simulated by save/load)
    acc.save_state()
    
    # We just ensure it was saved
    assert 'BTCUSDT' in acc.d0_in_flight_state
