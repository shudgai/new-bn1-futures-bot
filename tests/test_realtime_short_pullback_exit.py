import pytest
import pandas as pd
from core.services.exits.realtime_profit_exit import _evaluate_realtime_core_exit_gates

def test_realtime_short_pullback_exit():
    # 模擬 04:58 暴跌長紅K
    history_5 = [
        {'timestamp': 1000, 'o': 100, 'h': 101, 'l': 85, 'c': 86, 'kc_middle': 100, 'atr': 5, 'atr': 5},
    ]
    snapshot = {
        'quote_ms': 2000,
        'history_5': history_5,
        'last_close': 86,
        'ma5': 90, 'kc_middle': 100, 'atr': 5,
    }
    
    position = {'side': 'SHORT', 'qty': 1, 'margin': 10, 'leverage': 10}
    
    # 模擬 04:59 當前棒
    stamp = 2000
    price = 88 # 當前價
    
    # 模擬 quote 回踩：live_open = 86, live_low = 84, quote = 88
    # 這裡 quote (88) > live_open (86)，觸發 REALTIME_SHORT_PULLBACK_EXIT
    snapshot['live_open'] = 86
    snapshot['live_low'] = 84
    snapshot['live_high'] = 88
    
    trigger, updated = _evaluate_realtime_core_exit_gates(
        position, {}, price, stamp, snapshot, fee=0.0005, slippage=0.0005
    )
    
    assert trigger == 'REALTIME_SHORT_PULLBACK_EXIT'

