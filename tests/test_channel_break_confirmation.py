import pytest
from core.engine import TradingEngine
from test_direct_break_execution import setup_engine
from test_channel_swing_execution import SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('held', [False,True])
async def test_weak_confirmation_waits_for_later_strong_body(setup_engine, side, held):
    old=('SHORT' if side=='LONG' else 'LONG') if held else None
    e,f=setup_engine(side,old)
    # Isolate ordinary breakout timing; these bodies are not abnormal.
    f["atr"] = 10.
    confirmed=f.copy()
    # Move breakout to the newest closed row: live outside is not confirmation.
    f.loc[67]=f.loc[66].copy()
    f.loc[68]=confirmed.loc[67].copy()
    _, candidates=await e._process_single_symbol(SYMBOL,1.,None,False)
    assert not candidates and not e.account.trades
    if held: assert e.account.positions[SYMBOL]['side']==old
    else: assert SYMBOL not in e.account.positions
    f.loc[:]=confirmed
    await e._process_single_symbol(SYMBOL,2.,None,False)
    if held:
        assert e.account.positions[SYMBOL]['side']==old
    else:
        assert SYMBOL not in e.account.positions

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('invalid', ['inside_close','inside_live','outside_open','wick_only','old_break','prior_outside'])
async def test_invalid_break_never_reverses_or_retries(setup_engine,side,invalid):
    old='SHORT' if side=='LONG' else 'LONG'
    e,f=setup_engine(side,old)
    if invalid=='inside_close': f.loc[68,'close']=100.
    elif invalid=='inside_live': e.tickers[SYMBOL]=100.
    elif invalid=='outside_open': f.loc[67,'open']=102.5 if side=='LONG' else 97.5
    elif invalid=='wick_only': f.loc[67,'close']=100.
    elif invalid=='prior_outside': f.loc[66,'close']=103. if side=='LONG' else 97.
    elif invalid=='old_break':
        f.loc[65]=f.loc[66].copy()
        f.loc[66]=f.loc[67].copy()
        f.loc[67]=f.loc[68].copy()
    await e._process_single_symbol(SYMBOL,1.,None,False)
    assert e.account.positions[SYMBOL]['side']==old
    assert not e.account.trades
    # A stale pending retry cannot bypass the same validation when flat.
    e.account.positions.clear()
    e._channel_outer_reentry_after_exit[SYMBOL]=side
    await e._process_single_symbol(SYMBOL,2.,None,False)
    assert not e.account.trades
    assert SYMBOL not in e._channel_outer_reentry_after_exit

@pytest.mark.anyio
async def test_confirmation_expiring_during_final_fetch_cancels_order(setup_engine):
    e,f=setup_engine('LONG')
    stale=f.copy()
    fresh=f.copy()
    fresh['timestamp']=list(range(70))
    fresh.loc[68,'timestamp']=999
    async def fetch(*args,**kwargs): return fresh.copy()
    e.fetch_klines=fetch
    assert not await e._execute_confirmed_channel_break(SYMBOL,stale,103.,'LONG')
    assert not e.account.trades
