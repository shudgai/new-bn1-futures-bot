import sys, os
sys.path.append(os.getcwd())
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing, position_identity
from tests.test_trend_hold_integration import get_base_snapshot

import logging
logging.basicConfig(level=logging.DEBUG)

def instrumented_evaluate():
    position = {'side': 'LONG', 'entry_price': 100, 'qty': 1, 'state': {'peak_price': 110, 'peak_net_pnl': 10.0, 'identity': ['LONG', 60.0, 100.0, 1.0], 'policy': 'V21_ABNORMAL_FILTER'}, 'initial_sl': 90, 'open_timestamp': 60.0}
    snapshot = get_base_snapshot('RELEASED')
    price = 105
    fee=0.0005
    slippage=0.0001
    from core.services.exits.peak_trailing_exit import migrate_peak_state, estimated_net_pnl
    ident = position_identity(position)
    print("ident", ident)
    state = migrate_peak_state(position)
    print("state after migrate", state)
    
    from core.services.exits.trend_hold_evaluator import evaluate_trend_hold
    trend_status, trend_reason = evaluate_trend_hold(position, snapshot, price)
    print("trend hold:", trend_status, trend_reason)
    
    state['peak_net_pnl'] = max(float(state.get('peak_net_pnl', 10.0)), 10.0)
    print("peak net pnl:", state['peak_net_pnl'])
    net = estimated_net_pnl(100, 105, 1, 1, fee, slippage)
    print("net:", net)
    
    import math
    if state['peak_net_pnl'] >= 4.0:
        locked_net = math.floor((state['peak_net_pnl'] - 4.0) / 2.0) * 2.0 + 2.0
        print("normal locked net:", locked_net)
        if trend_status in ('HOLD', 'WARNING'):
            locked_net = max(2.0, math.floor(locked_net / 2.0))
        print("final locked net:", locked_net)
        if net <= locked_net:
            print("EXIT CONDITION MET!")
        else:
            print("NOT EXITING!")

instrumented_evaluate()
