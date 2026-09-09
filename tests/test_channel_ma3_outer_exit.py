import json
import pytest
from core.engine import TradingEngine
from test_channel_swing_execution import _execution_engine, _narrow_channel_frame, SYMBOL

@pytest.fixture
def anyio_backend():
    return 'asyncio'


def fixture(side):
    sign=1 if side=='LONG' else -1
    f=_narrow_channel_frame()
    f['kc_upper'],f['kc_lower']=100.5,99.5
    f.loc[15:18,'close']=[100.+sign*x for x in (0.,2.,4.,3.)]
    f.loc[19,'open']=100.+sign
    f.loc[19,'close']=100.+sign*10. # stale live candle must not drive the exit
    p={'side':side,'entry_price':100.+sign*5.,'qty':1.,'open_timestamp':1.,
       'entry_kc_upper':100.5,'entry_kc_lower':99.5}
    return f,p,100.+sign

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('case',['turn','flat','continuing','armed','inside_entry','missing_rail','invalid','short_history'])
def test_live_ma3_outer_turn(side,case):
    f,p,price=fixture(side);sign=1 if side=='LONG' else -1
    if case=='flat': price=100.+sign*2.
    elif case=='continuing': price=100.+sign*3.
    elif case=='armed': p['channel_profit_protection']={'armed':True}
    elif case=='inside_entry': p['entry_price']=100.
    elif case=='missing_rail': p.pop('entry_kc_upper' if side=='LONG' else 'entry_kc_lower')
    elif case=='invalid': f.loc[18,'close']=float('nan')
    elif case=='short_history': f=f.tail(4)
    assert TradingEngine._channel_outer_ma3_turn_exit(p,f,price) is (case=='turn')

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('success',[True,False])
async def test_exit_before_middle_and_retry_after_ma_recovers(side,success):
    f,p,price=fixture(side)
    e=_execution_engine(f,side,success);e.account.save_state=lambda:None
    e.account.positions[SYMBOL].update(p);e.tickers[SYMBOL]=price
    await e._process_single_symbol(SYMBOL,2.,None,False)
    assert len(e.account.events)==1,e.account.logs
    assert e.account.events[0][2]==price
    assert e.account.events[0][3].endswith(f'KC_{side}_OUTER_MA3_TURN_EXIT')
    assert (SYMBOL in e.account.positions) is (not success)
    if not success:
        e.account.positions[SYMBOL]=json.loads(json.dumps(e.account.positions[SYMBOL]))
        e.tickers[SYMBOL]=p['entry_price']
        await e._process_single_symbol(SYMBOL,3.,None,False)
        assert len(e.account.events)==2,e.account.logs
        assert all(event[0]=='close' for event in e.account.events)
