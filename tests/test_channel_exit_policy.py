import pytest
from test_channel_protected_only_exit import market
from test_channel_ma3_outer_exit import fixture
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('kind', ['abnormal','waterfall','ma3'])
@pytest.mark.parametrize('mode', ['unarmed','arming','armed','retraced','pending'])
async def test_policy(side,kind,mode):
    sign=1 if side=='LONG' else -1
    if kind=='ma3':
        f,p,price=fixture(side); f['atr']=100.
    else:
        f,price=market(side,'normal')
        price=100-sign*(.6 if kind=='abnormal' else 1.1)
        p=dict(side=side,entry_price=100.,qty=1.,open_timestamp=1.)
    if mode!='unarmed':
        p['entry_price']=price-sign*5
        if mode!='arming':
            p['channel_profit_protection']=dict(identity=[side,p['open_timestamp'],p['entry_price'],p['qty']],armed=True,peak_gross=10 if mode=='retraced' else 5)
        if mode=='pending':
            p['channel_exception_exit_pending']='EMERGENCY_EXIT_LIVE_ADVERSE_ABNORMAL'
            p['channel_live_ma3_turn_exit_pending']=True
    e=_execution_engine(f,side,True);e.account.save_state=lambda:None
    e.account.positions[SYMBOL].update(p); e.tickers[SYMBOL]=price
    await e._process_single_symbol(SYMBOL,2.,None,False)
    if mode == 'retraced' or (mode == 'unarmed' and kind != 'abnormal'):
        assert len(e.account.events)==1,e.account.logs
        reason=e.account.events[0][3]
        assert ('PROFIT_PROTECTION' in reason) if mode=='retraced' else ('LIVE_MA3_TURN_EXIT' in reason if kind=='ma3' else 'EMERGENCY_EXIT_' in reason)
    else:
        assert not e.account.events,e.account.logs
        assert bool(e.account.positions[SYMBOL]['channel_profit_protection']['armed']) is (mode != 'unarmed')
        assert 'channel_exception_exit_pending' not in e.account.positions[SYMBOL]
        assert 'channel_live_ma3_turn_exit_pending' not in e.account.positions[SYMBOL]
