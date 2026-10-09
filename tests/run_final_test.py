import asyncio
import time
from unittest.mock import Mock, patch

from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit
from core.services.exits.peak_trailing_exit import STATE_KEY

class DummyEngine:
    def __init__(self, account):
        self.is_running = True
        self.account = account
        self._channel_exit_frames = {}

class DummyAccount:
    def __init__(self, position):
        self.positions = {position['symbol']: position}
        self.position_meta = {}
        self.closed_symbol = None
        self.closed_reason = None

    def save_state(self): pass
    def log(self, msg, level): pass
    async def close_position(self, symbol, price, reason, is_manual=False, is_limit=False):
        self.closed_symbol = symbol
        self.closed_reason = reason
        return True

async def main():
    cases = [
        ('TEST_01', 'SHORT', 'HOLD', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', 'CHANNEL_SWING', False),
        ('TEST_02', 'SHORT', 'WARNING', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', 'CHANNEL_SWING', False),
        ('TEST_03', 'SHORT', 'UNKNOWN', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', 'CHANNEL_SWING', False),
        ('TEST_04', 'SHORT', 'RELEASED', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', 'CHANNEL_SWING', True),
        ('TEST_05', 'LONG', 'HOLD', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', 'CHANNEL_SWING', False),
        ('TEST_06', 'LONG', 'WARNING', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', 'CHANNEL_SWING', False),
        ('TEST_07', 'LONG', 'UNKNOWN', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', 'CHANNEL_SWING', False),
        ('TEST_08', 'LONG', 'RELEASED', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', 'CHANNEL_SWING', True),
        ('TEST_09', 'SHORT', 'HOLD', 'EXIT_INITIAL_ATR_HARD_STOP', 'INITIAL_ATR', 'CHANNEL_SWING', True),
        ('TEST_10', 'SHORT', 'HOLD', 'EXIT_ADVERSE_ABNORMAL_BODY', 'WATERFALL_DROP', 'CHANNEL_SWING', True),
        ('TEST_11', 'LONG', 'HOLD', 'EXIT_INITIAL_ATR_HARD_STOP', 'INITIAL_ATR', 'CHANNEL_SWING', True),
        ('TEST_12', 'SHORT', 'HOLD', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', 'SCALPING', True),
    ]
    passed = 0
    for case in cases:
        name, side, t_status, type_val, trigger_val, entry_m, expect_exit = case
        pos = {
            'symbol': 'TEST', 'side': side, 'entry_mode': entry_m,
            'entry_price': 100.0, 'qty': 1.0, 'id': 100,
            STATE_KEY: {'peak_price': 100.0, 'peak_net_pnl': 5.0}
        }
        acc = DummyAccount(pos)
        engine = DummyEngine(acc)
        decision = {'type': type_val, 'trigger': trigger_val}
        
        with patch('core.services.exits.realtime_profit_exit.PureTrendStrategyV2.evaluate_anti_whipsaw_profit_lock', return_value=decision), \
             patch('core.services.exits.realtime_profit_exit.enforce_hard_stop', return_value=False), \
             patch('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', return_value=(t_status, 'TEST_REASON')), \
             patch('core.services.exits.realtime_profit_exit.migrate_peak_state'):
            await enforce_realtime_profit_exit(engine, 'TEST', 90.0 if side == 'SHORT' else 110.0, time.time()*1000)
            did_exit = acc.closed_symbol == 'TEST'
            status = 'PASS' if did_exit == expect_exit else 'FAIL'
            if did_exit == expect_exit:
                passed += 1
            print(f"NAME = {name}\nEXPECTED = {'EXIT' if expect_exit else 'HOLD'}\nACTUAL = {'EXIT' if did_exit else 'HOLD'}\nPASS/FAIL = {status}\n")
    
    print(f"ACTUAL_TARGETED_TEST_COUNT = {len(cases)}")
    print(f"TARGETED_TESTS = {passed} passed / {len(cases)-passed} failed")

asyncio.run(main())
