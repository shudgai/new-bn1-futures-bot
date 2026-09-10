"""Single adverse abnormal bodies no longer close a holding."""
import pytest
from core.engine import TradingEngine
from test_channel_protected_only_exit import market
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'

@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('pending', [False,True])
async def test_single_abnormal_holds_and_clears_stale_request(side,pending):
    f,_=market(side,'normal');sign=1 if side=='LONG' else -1
    price=100-sign*.6
    e=_execution_engine(f,side,True);e.account.save_state=lambda:None
    p=e.account.positions[SYMBOL];p.update(entry_price=100.,qty=1.,open_timestamp=1.)
    if pending:
        p['channel_exception_exit_pending']='EMERGENCY_EXIT_LIVE_ADVERSE_ABNORMAL'
        e.account.position_meta[SYMBOL]={'channel_exception_exit_pending':p['channel_exception_exit_pending']}
    e.tickers[SYMBOL]=price
    await e._process_single_symbol(SYMBOL,2.,None,False)
    assert not e.account.events,e.account.logs
    assert 'channel_exception_exit_pending' not in p
    assert 'channel_exception_exit_pending' not in e.account.position_meta.get(SYMBOL,{})

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('case',['waterfall','closed_waterfall','double'])
def test_remaining_emergencies_still_trigger(side,case):
    f,price=market(side,case)
    sign = -1 if side == 'LONG' else 1
    if case == 'waterfall':
        price = 100 + sign * 1.5
    elif case == 'closed_waterfall':
        f.loc[f.index[-2], 'close'] = 100 + sign * 1.5
    p=dict(side=side,entry_price=100.,open_timestamp=1.,channel_exception_exit_pending='EMERGENCY_EXIT_LIVE_ADVERSE_ABNORMAL')
    reason=TradingEngine._channel_exception_exit(p,f,price)
    assert reason and reason!='EMERGENCY_EXIT_LIVE_ADVERSE_ABNORMAL'
