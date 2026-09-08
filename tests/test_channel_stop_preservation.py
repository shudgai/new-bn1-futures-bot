import asyncio
import copy
import pytest
from core.engine import TradingEngine
from core.paper_account import PaperAccount
from test_direct_break_execution import setup_engine
from test_channel_swing_execution import SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
async def test_background_pass_preserves_fixed_and_locked_stops(setup_engine, monkeypatch, side):
    e, _ = setup_engine(side, side)
    p = e.account.positions[SYMBOL]
    fixed = 98. if side == 'LONG' else 102.
    p.update(sl=fixed, initial_sl=fixed, initial_risk=2.)
    e.account.position_meta[SYMBOL].update(sl=fixed, initial_sl=fixed, initial_risk=2.)
    e.tickers[SYMBOL] = 100.
    async def one_pass(_):
        raise asyncio.CancelledError
    monkeypatch.setattr(asyncio, 'sleep', one_pass)
    for locked in (False, True):
        if locked:
            assert await e.account.trail_stop_loss(SYMBOL, 99. if side == 'LONG' else 101.)
        before = copy.deepcopy(p)
        meta = copy.deepcopy(e.account.position_meta[SYMBOL])
        e.is_running = True
        with pytest.raises(asyncio.CancelledError):
            await e._fixed_stop_loss_loop()
        for key in ('sl','initial_sl','initial_risk'):
            assert p[key] == before[key]
            assert e.account.position_meta[SYMBOL][key] == meta[key]
        if locked:
            assert not await e.account.trail_stop_loss(SYMBOL, 98.5 if side == 'LONG' else 101.5)

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG','SHORT'])
async def test_restart_repairs_exact_entry_and_unlock_restores_fixed(tmp_path, monkeypatch, side):
    import core.paper_account as pm
    monkeypatch.setattr(pm, 'STATE_FILE', str(tmp_path/'account.json'))
    monkeypatch.setattr(pm, 'DISABLE_STOP_LOSS', False)
    a = PaperAccount()
    a.positions = {SYMBOL: dict(side=side, entry_mode='CHANNEL_SWING', entry_price=100.,
                               open_timestamp=123., sl=0., initial_sl=0., initial_risk=0.)}
    a.position_meta = {SYMBOL: dict(channel_cross_lock=True, channel_pre_lock_sl=0.)}
    fixed = 98. if side == 'LONG' else 102.
    a.trades = [dict(symbol=SYMBOL, action=f'OPEN_{side}', id=124000, initial_sl=80.),
                dict(symbol=SYMBOL, action=f'OPEN_{side}', id=123000, initial_sl=fixed)]
    await a.initialize()
    assert a.positions[SYMBOL]['initial_sl'] == fixed
    assert a.positions[SYMBOL]['sl'] == fixed
    if side == 'LONG':
        assert await a.trail_stop_loss(SYMBOL,99.)
        assert await a.clear_channel_profit_lock(SYMBOL)
    assert a.positions[SYMBOL]['sl'] == fixed
    assert not a.position_meta[SYMBOL].get('channel_cross_lock')
    restored = PaperAccount()
    assert restored.positions[SYMBOL]['sl'] == fixed

@pytest.mark.anyio
async def test_short_cross_inside_channel_never_closes_or_reopens(setup_engine):
    e, f = setup_engine('LONG', 'SHORT')
    f['kc_upper']=110.; f['kc_lower']=90.; f['ma15']=100.
    f.loc[67:69,['open','close']]=100.
    f.loc[67:68,'ma3']=99.9
    f.loc[69,'ma3']=100.1
    p=e.account.positions[SYMBOL]
    p.update(sl=105., initial_sl=105.)
    e.tickers[SYMBOL]=100.
    for i in range(3):
        await e._process_single_symbol(SYMBOL,float(i),None,False)
        await e.account.update_positions({SYMBOL:100.1})
    assert e.account.positions[SYMBOL] is p
    assert p['sl']==105.
    assert not e.account.position_meta[SYMBOL].get('channel_cross_lock')
    assert not e.account.trades

@pytest.mark.anyio
async def test_legacy_short_cross_lock_clears_inside_channel(setup_engine):
    e, f=setup_engine('LONG','SHORT')
    f['kc_upper']=110.; f['kc_lower']=90.; f['ma15']=100.
    f.loc[67:69,['open','close']]=100.
    f.loc[67:69,'ma3']=101.
    p=e.account.positions[SYMBOL]
    p.update(sl=100.01, initial_sl=105., channel_cross_lock=True)
    e.account.position_meta[SYMBOL].update(channel_cross_lock=True,channel_pre_lock_sl=105.)
    e.tickers[SYMBOL]=100.
    await e._process_single_symbol(SYMBOL,1.,None,False)
    assert p['sl']==105.
    assert not e.account.position_meta[SYMBOL].get('channel_cross_lock')
