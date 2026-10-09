import asyncio
import sys
sys.path.append("/home/shudgai999/project/new bn")
from core.engine import engine

async def test_position_safety_gates_preserved():
    signal = dict(side='LONG', signal_code='KC_2BAR_CONFIRM_LONG', entry_mode='CHANNEL_SWING')
    engine.account.positions = {'TEST/USDT': {}}
    result = await engine._place_structured_entry_locked('TEST/USDT', signal, 100.0)
    assert result is False
    print("test_position_safety_gates_preserved passed.")

asyncio.run(test_position_safety_gates_preserved())
