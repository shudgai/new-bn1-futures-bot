import copy
import json
import pytest
from core.channel_ma3_turn import significant_ma3_turn
from test_channel_protected_only_exit import market
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'

def setup(side):
    f,_=market(side,'normal');f['open']=f['close']=100.;f['atr']=1.
    p=dict(side=side,entry_price=100.,qty=1.,open_timestamp=1201.,entry_mode='CHANNEL_SWING')
    return f,p,1 if side=='LONG' else -1

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_observed_peak_threshold_and_pending(side):
    f,p,s=setup(side)
    assert not significant_ma3_turn(p,f,100.)
    assert not significant_ma3_turn(p,f,100.+s*.3)
    assert not significant_ma3_turn(p,f,100.+s*.03)
    p=json.loads(json.dumps(p))
    assert not significant_ma3_turn(p,f,100.-s*.03)
    assert significant_ma3_turn(p,f,100.-s*.30)
    assert significant_ma3_turn(p,f,100.+s*.6)

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_no_preentry_turn_or_unobserved_favorable_move(side):
    f,p,s=setup(side)
    assert not significant_ma3_turn(p,f,100.)
    assert not significant_ma3_turn(p,f,100.-s*.9)

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_fixed_atr_and_candle_rollover(side):
    f,p,s=setup(side)
    significant_ma3_turn(p,f,100.)
    significant_ma3_turn(p,f,100.+s*.3)
    f['timestamp']+=60000;f['atr']=10.
    assert not significant_ma3_turn(p,f,100.-s*.03)
    assert significant_ma3_turn(p,f,100.-s*.30)

@pytest.mark.parametrize('case',['armed','new_position','bad_atr'])
def test_state_not_reused(case):
    f,p,s=setup('LONG')
    significant_ma3_turn(p,f,100.);significant_ma3_turn(p,f,100.3)
    if case=='armed':p['channel_profit_protection']={'armed':True}
    elif case=='new_position':p['open_timestamp']+=1
    else:f['atr']=float('nan')
    assert not significant_ma3_turn(p,f,100.)

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('success',[True,False])
async def test_quote_exit_small_turn_holds_large_turn_closes_and_retries(side,success,monkeypatch):
    f,p,s=setup(side);e=_execution_engine(f,side,success);e.is_running=True
    e.account.save_state=lambda:None;e.account.positions[SYMBOL].update(p)
    e._channel_exit_frames={SYMBOL:f}
    monkeypatch.setattr('core.engine.time.time',lambda:1201.)
    for price in [100.,100.+s*.3,100.+s*.03,100.-s*.03,100.-s*.299]:
        await e._channel_quote_exit(SYMBOL,price,1201000)
        assert not e.account.events
    await e._channel_quote_exit(SYMBOL,100.-s*.30,1201000)
    assert len(e.account.events)==1
    assert 'SIGNIFICANT_MA3_TURN_EXIT' in e.account.events[0][3]
    if not success:
        # Reconstructed position must recover the persisted pending state.
        e.account.positions[SYMBOL].pop('channel_significant_ma3_turn')
        await e._channel_quote_exit(SYMBOL,100.+s*.3,1201000)
        assert len(e.account.events)==2

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
async def test_armed_profit_protection_clears_ma3_state(side):
    f,p,s=setup(side);e=_execution_engine(f,side,True)
    e.account.save_state=lambda:None;e.account.positions[SYMBOL].update(p)
    e.account.positions[SYMBOL]['entry_price']=100.-s*5
    state={'pending':True}
    e.account.positions[SYMBOL]['channel_significant_ma3_turn']=copy.deepcopy(state)
    e.account.position_meta[SYMBOL]={'channel_significant_ma3_turn':state}
    e.tickers[SYMBOL]=100.
    await e._process_single_symbol(SYMBOL,1201.,None,False)
    assert not e.account.events
    assert 'channel_significant_ma3_turn' not in e.account.positions[SYMBOL]
    assert 'channel_significant_ma3_turn' not in e.account.position_meta[SYMBOL]

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_tick_retracement_without_line_reversal_holds(side):
    f,p,s=setup(side)
    significant_ma3_turn(p,f,100.)
    significant_ma3_turn(p,f,100.+s*.9)
    assert not significant_ma3_turn(p,f,100.+s*.3)
    assert not significant_ma3_turn(p,f,100.)
    # No rail touch is required once the MA3 line actually reverses.
    f['kc_upper']=110.;f['kc_lower']=90.
    assert not significant_ma3_turn(p,f,100.-s*.03)
    assert significant_ma3_turn(p,f,100.-s*.30)

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_old_pending_cannot_bypass_line_direction(side):
    f,p,s=setup(side)
    p['channel_significant_ma3_turn']={'identity':[side,1201.,100.], 'pending':True}
    assert not significant_ma3_turn(p,f,100.)
    assert not p['channel_significant_ma3_turn']['pending']
