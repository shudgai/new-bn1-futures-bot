import pandas as pd
from unittest import mock
import sys

import core.services.strategies.unified_entry_strategy as mod

# Patch blockers
mod.long_entry_trend_problem = lambda *args, **kwargs: None
mod.validate_channel_expansion = lambda *args, **kwargs: (True, None)
mod.ma3_entry_problem = lambda *args, **kwargs: None
mod.confirmed = lambda df: df[df.get('is_closed', False) == True]

def make_df(side, c_ms, live_ms, live_open, is_closed=False):
    if side == 'LONG':
        c0 = dict(timestamp=c_ms - 120000, open=98, close=100, high=101, low=97,
                  kc_middle=90, kc_upper=99, kc_lower=81, ma15=90, ma3=95, ma5=95, is_closed=True)
        c1 = dict(timestamp=c_ms - 60000, open=98, close=100, high=101, low=97,
                  kc_middle=90, kc_upper=99, kc_lower=81, ma15=90, ma3=95, ma5=95, is_closed=True)
        c = dict(timestamp=c_ms, open=99, close=101, high=102, low=98,
                 kc_middle=91, kc_upper=100, kc_lower=82, ma15=91, ma3=96, ma5=96, is_closed=True, atr=1)
    else:
        c0 = dict(timestamp=c_ms - 120000, open=100, close=98, high=101, low=97,
                  kc_middle=110, kc_upper=119, kc_lower=101, ma15=110, ma3=105, ma5=105, is_closed=True)
        c1 = dict(timestamp=c_ms - 60000, open=100, close=98, high=101, low=97,
                  kc_middle=110, kc_upper=119, kc_lower=101, ma15=110, ma3=105, ma5=105, is_closed=True)
        c = dict(timestamp=c_ms, open=99, close=97, high=102, low=96,
                 kc_middle=109, kc_upper=118, kc_lower=100, ma15=109, ma3=104, ma5=104, is_closed=True, atr=1)
                 
    live = dict(timestamp=live_ms, open=live_open, close=live_open, high=live_open, low=live_open,
                kc_middle=c['kc_middle'], kc_upper=c['kc_upper'], kc_lower=c['kc_lower'],
                ma15=c['ma15'], ma3=c['ma3'], ma5=c['ma5'], is_closed=is_closed)
                
    df = pd.DataFrame([c0, c1, c, live])
    df.attrs['timeframe_ms'] = 60000
    return df

def test():
    class MockAccount:
        def __init__(self):
            self.trades = []
            
        def has_position(self, symbol, side):
            return any(t.get('action') == f'OPEN_{side}' for t in self.trades)
            
    account = MockAccount()
    symbol = 'TEST/USDT'
    
    def engine_tick(price, side, should_succeed_open=True):
        if account.has_position(symbol, side):
            return "WAIT", "WAIT_EXISTING_POSITION"
            
        df = make_df(side, 1000000.0, 1060000.0, 100.0)
        passed, code, decision = mod.evaluate_closed_entry(df, side, price=price)
        action = decision.get('action')
        
        if action == 'ENTER':
            ps_id = decision.get('pending_signal_id')
            assert ps_id == f"{side}:1000000:1060000", "Must encode exactly side:bar2:bar3"
            
            if any(t.get('pending_signal_id') == ps_id for t in account.trades):
                return "WAIT", "ALREADY_CONSUMED"
                
            if should_succeed_open:
                account.trades.append({'action': f'OPEN_{side}', 'symbol': symbol, 'pending_signal_id': ps_id})
                return "ENTER", "SUCCESS"
            else:
                return "ENTER", "FAILED_OPEN"
                
        return action, decision.get('reason')

    print("Testing B/C/D/I for LONG...")
    
    act, r = engine_tick(100.01, 'LONG', should_succeed_open=False)
    assert act == 'ENTER' and r == 'FAILED_OPEN', "Expected failed open"
    
    act2, r2 = engine_tick(100.02, 'LONG', should_succeed_open=True)
    assert act2 == 'ENTER' and r2 == 'SUCCESS', "Expected retry to succeed"
    assert len(account.trades) == 1
    
    act3, r3 = engine_tick(100.03, 'LONG', should_succeed_open=True)
    assert act3 == 'WAIT' and r3 == 'WAIT_EXISTING_POSITION'
    
    df_restart = make_df('LONG', 1000000.0, 1060000.0, 100.0)
    _, _, dec_restart = mod.evaluate_closed_entry(df_restart, 'LONG', price=100.04)
    ps_id_restart = dec_restart.get('pending_signal_id')
    assert any(t.get('pending_signal_id') == ps_id_restart for t in account.trades), "Restart dup check failed"
    
    act4, r4 = engine_tick(99.99, 'LONG')
    assert act4 == 'WAIT' and r4 == 'WAIT_EXISTING_POSITION'

    print("ALL INTEGRATION ASSERTIONS PASSED.")

test()
