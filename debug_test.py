import asyncio, time
from core.engine import TradingEngine

async def debug_it():
    import pytest
    pytest.main(['tests/test_realtime_profit_exit.py::test_real_account_reload_and_concurrent_tick_close', '-v', '--tb=short', '-s'])

asyncio.run(debug_it())
