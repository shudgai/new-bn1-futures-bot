import pandas as pd
import numpy as np

# Simulate the 07:54 crash in 1000PEPE
data = {
    'timestamp': [100000, 160000, 220000],
    'open': [100, 95, 90],
    'high': [102, 96, 91],
    'low': [94, 88, 70],
    'close': [95, 90, 75],
    'ma3': [100, 96, 85],
    'ma15': [98, 95, 90],
    'kc_upper': [105, 105, 105],
    'kc_middle': [95, 95, 95],
    'kc_lower': [85, 85, 85],
    'atr': [5, 5, 5],
    'is_closed': [True, True, True]
}

df = pd.DataFrame(data)

# Test unified_entry_strategy.py
from core.services.strategies.unified_entry_strategy import check_streamlined_entry_signal
ok, reason, sig = check_streamlined_entry_signal(df, "SHORT", 75, "NO_POSITION")
print(f"Strategy Result: {ok}, {reason}")

# Test core/engine.py physical gate
from core.engine import BinanceFuturesBot
import os

# Create a mock engine
class MockEngine:
    def __init__(self):
        class MockAccount:
            def log(self, msg, level):
                print(f"LOG [{level}]: {msg}")
        self.account = MockAccount()

    async def _place_structured_entry_locked(self, symbol, signal, live_price, channel_snapshot):
        # Emulate the hard gate logic from engine.py
        from core.services.candle_data import closed_entry_candles
        closed_df = closed_entry_candles(df)
        c1 = closed_df.iloc[-2]
        c2 = closed_df.iloc[-1]
        
        curr_close = float(c2['close'])
        curr_open = float(c2['open'])
        curr_kc_middle = float(c2.get('kc_middle', 0))
        curr_kc_lower = float(c2.get('kc_lower', 0))
        curr_kc_upper = float(c2.get('kc_upper', 0))
        
        c1_ma3 = float(c1.get('ma3', 0))
        c1_ma15 = float(c1.get('ma15', 0))
        curr_ma3 = float(c2.get('ma3', 0))
        curr_ma15 = float(c2.get('ma15', 0))
        
        is_dead_cross = (c1_ma3 >= c1_ma15) and (curr_ma3 < curr_ma15)
        is_golden_cross = (c1_ma3 <= c1_ma15) and (curr_ma3 > curr_ma15)
        
        side = signal['side']
        if side == 'SHORT':
            if curr_close > curr_open:
                return False, "陽線禁止開空"
            if not is_dead_cross and curr_close >= curr_kc_lower:
                return False, "未死叉且未破下軌，禁止開空"
                
        return True, "PASSED_HARD_GATE"

import asyncio
engine = MockEngine()
result = asyncio.run(engine._place_structured_entry_locked("1000PEPE", {"side": "SHORT"}, 75, None))
print(f"Engine Hard Gate Result: {result}")
