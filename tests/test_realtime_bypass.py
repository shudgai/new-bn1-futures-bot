import pytest
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

@pytest.mark.asyncio
async def test_bypass_cases():
    cases = [
        ('SHORT', 'HOLD', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', False),
        ('SHORT', 'WARNING', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', False),
        ('SHORT', 'UNKNOWN', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', False),
        ('SHORT', 'RELEASED', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', True),
        ('LONG', 'HOLD', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', False),
        ('LONG', 'WARNING', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', False),
        ('LONG', 'UNKNOWN', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', False),
        ('LONG', 'RELEASED', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', True),
        ('SHORT', 'HOLD', 'EXIT_INITIAL_ATR_HARD_STOP', 'INITIAL_ATR', True),
        ('SHORT', 'HOLD', 'EXIT_ADVERSE_ABNORMAL_BODY', 'WATERFALL_DROP', True),
        ('SHORT', 'HOLD', 'EXIT_ADVERSE_ABNORMAL_BODY', 'MA5_TURN_REVERSAL', True),
        ('LONG', 'HOLD', 'EXIT_ADVERSE_ABNORMAL_BODY', 'MA5_TURN_REVERSAL', True),
        ('SHORT', 'HOLD', 'EXIT_REALTIME_PEAK_TRAILING', 'TRAILING_2U_LADDER', True, 'SCALPING'),
    ]
    for case in cases:
        side, t_status, type_val, trigger_val, expect_exit = case[:5]
        entry_m = case[5] if len(case) > 5 else 'CHANNEL_SWING'
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
             patch('core.services.exits.realtime_profit_exit.evaluate_trend_hold', return_value=(t_status, 'TEST_REASON')):
            await enforce_realtime_profit_exit(engine, 'TEST', 90.0 if side == 'SHORT' else 110.0, time.time()*1000)
            did_exit = acc.closed_symbol == 'TEST'
            assert did_exit == expect_exit, f"Failed case: {case}"
