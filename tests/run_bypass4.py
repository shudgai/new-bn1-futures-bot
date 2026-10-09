import asyncio
from unittest.mock import Mock, patch
from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit
from core.services.exits.peak_trailing_exit import STATE_KEY
import sys

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
    pos = {'symbol': 'TEST', 'side': 'SHORT', 'entry_mode': 'CHANNEL_SWING', 'entry_price': 100.0, 'qty': 1.0, 'id': 100, STATE_KEY: {'peak_price': 100.0, 'peak_net_pnl': 5.0}}
    acc = DummyAccount(pos)
    engine = DummyEngine(acc)
    decision = {'type': 'EXIT_REALTIME_PEAK_TRAILING', 'trigger': 'TRAILING_2U_LADDER'}
    
    with patch('core.services.exits.realtime_profit_exit.PureTrendStrategyV2.evaluate_anti_whipsaw_profit_lock', return_value=decision), \
         patch('core.services.exits.realtime_profit_exit.enforce_hard_stop', return_value=False), \
         patch('core.services.exits.trend_hold_evaluator.evaluate_trend_hold', return_value=('RELEASED', 'TEST_REASON')):
        
        import trace
        tracer = trace.Trace(count=False, trace=True, ignoredirs=[sys.prefix, sys.exec_prefix])
        tracer.run('asyncio.run(enforce_realtime_profit_exit(engine, "TEST", 90.0, 9999999999.0))')

main_coro = main()
asyncio.run(main_coro)
