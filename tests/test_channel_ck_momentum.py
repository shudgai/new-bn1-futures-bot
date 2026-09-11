from unittest.mock import AsyncMock
import pytest
from core.services.strategies.outer_strategy import ck_entry_momentum_ready, ck_direction, aligned_entry
from channel_test_frames import closed_outer_entry_frame
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'

def frame(side, steps=(.3,.2,.1)):
    f=closed_outer_entry_frame(side);sign=1 if side=='LONG' else -1
    end=float(f.iloc[-2]['kc_middle']); values=[end-sign*sum(steps)]
    for step in steps: values.append(values[-1]+sign*step)
    for k in ['kc_middle','ema_20']:
        if k in f: f.loc[f.index[-5:-1],k]=values
    return f

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('steps,ready',[((.3,.2,.1),False),((.1,.2,.3),True),((.2,.2,.2),False),((.3,.1,.2),True)])
def test_closed_steps_and_recovery(side,steps,ready):
    f=frame(side,steps)
    assert ck_direction(f)==side
    assert ck_entry_momentum_ready(f,side) is ready
    f.loc[f.index[-1],'kc_middle']=float('nan')
    assert ck_entry_momentum_ready(f,side) is ready
    f.loc[f.index[-3],'kc_middle']=float('nan')
    assert not ck_entry_momentum_ready(f,side)

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('cached',[False,True])
@pytest.mark.parametrize('reentry',[False,True])
async def test_fading_blocks_every_order_route(side,cached,reentry,monkeypatch):
    f=frame(side);price=float(f.iloc[-1]['close'])
    assert aligned_entry(f,price)['reason']=='KC_MOMENTUM_FADE_WAIT'
    e=_execution_engine(f,side,True);e.account.positions.clear();e.account.save_state=lambda:None
    e.tickers[SYMBOL]=price;e._abnormal_market_entry_allowed=lambda *a,**k:True
    snapshot=dict(frame=f,price=price,kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower']))
    e._fresh_channel_entry_snapshot=AsyncMock(return_value=snapshot)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',[SYMBOL])
    signal=dict(side=side,score=100,entry_mode='CHANNEL_SWING',action='ENTER_MARKET',reason='fade check')
    if reentry: signal['profit_reentry_token']='old-profit'
    assert not await e._place_structured_entry(SYMBOL,signal,price,channel_snapshot=snapshot if cached else None)
    assert not e.account.events
    assert not getattr(e,'_channel_invalid_entry_candidates',set())

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
async def test_fading_does_not_close_held_position(side):
    f=frame(side);price=float(f.iloc[-1]['close'])
    e=_execution_engine(f,side,True);e.account.save_state=lambda:None
    e.account.positions[SYMBOL].update(entry_price=price,qty=1.,open_timestamp=1.)
    e.tickers[SYMBOL]=price
    await e._process_single_symbol(SYMBOL,2.,None,False)
    assert not e.account.events,e.account.logs

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_pivot_and_shared_recheck_cannot_bypass_fade(side,monkeypatch):
    from unittest.mock import Mock
    from core.engine import TradingEngine
    f=frame(side);price=float(f.iloc[-1]['close'])
    e=_execution_engine(f,side,True);e.account.positions.clear()
    e._channel_live_pivots=Mock();e._channel_live_pivots.observe.return_value=True
    e._channel_entry_quote_times={SYMBOL:1201.}
    monkeypatch.setattr('core.engine.time.time',lambda:1201.)
    assert not TradingEngine._live_pivot_ready(e,SYMBOL,f,price,side)
    assert not TradingEngine._channel_intrabar_ready(e,SYMBOL,f,price,side,live_pivot=True)
