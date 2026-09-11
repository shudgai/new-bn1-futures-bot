import copy
import pytest
from core.services.exits.profit_protection_service import protection
from test_channel_ck_reverse import setup, SYMBOL
from test_channel_protected_only_exit import market
from test_channel_swing_execution import _execution_engine

@pytest.fixture
def anyio_backend(): return 'asyncio'

from core import config


@pytest.fixture(autouse=True)
def _ladder_only(monkeypatch):
    """本檔只驗證階梯鎖利；保底停利另見 tests/test_channel_profit_floor.py。"""
    monkeypatch.setattr(config, "CHANNEL_SWING_PROFIT_FLOOR_NET_USDT", 0.0)


def quote(net, side, fee=.0005, slip=.0001):
    return ((100*(1+fee)+net)/((1-slip)*(1-fee)) if side=='LONG'
            else (100*(1-fee)-net)/((1+slip)*(1+fee)))

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_steps_costs_persistence_and_retry(side):
    p=dict(side=side,entry_price=100.,qty=1.,open_timestamp=1.)
    for net in [1.,2.,3.99]: assert protection(p,quote(net,side),.0005,.0001) is None
    for net,locked in [(4.01,2),(5.99,2),(6.01,4),(10.01,8)]:
        r=protection(p,quote(net,side),.0005,.0001)
        assert r['locked_net']==locked and not r['triggered']
    p=copy.deepcopy(p)
    assert not protection(p,quote(8.01,side),.0005,.0001)['triggered']
    assert protection(p,quote(7.99,side),.0005,.0001)['triggered']
    assert protection(p,quote(11.,side),.0005,.0001)['triggered']

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_old_percentage_stop_removed(side):
    p=dict(side=side,entry_price=100.,qty=1.,open_timestamp=1.)
    p['channel_profit_protection']=dict(identity=[side,1.,100.,1.],armed=True,peak_gross=100.,stop_price=999.,pending=True)
    assert protection(p,quote(2.,side),.0005,.0001) is None
    assert p['channel_profit_protection']['policy']=='fixed_net_steps_v1'
    assert 'stop_price' not in p['channel_profit_protection']

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
async def test_ck_close_waits_for_fresh_quote_and_stale_legacy_ticket_cannot_open(side,monkeypatch):
    f,p,e=setup(side,monkeypatch)
    assert await e._try_ck_reverse(SYMBOL,f,p,False)
    assert [x[0] for x in e.account.events]==['close']
    assert SYMBOL not in e.account.positions
    assert e.account.channel_profit_reentries[SYMBOL]['mode']=='direct_reverse'
    assert e.account.channel_profit_reentries[SYMBOL]['phase']=='closed'
    e.account.channel_profit_reentries[SYMBOL]=dict(mode='ck_reverse',phase='closed',side=side,token='old')
    await e._try_profit_reentry_locked(SYMBOL,f,p,False)
    assert SYMBOL not in e.account.channel_profit_reentries
    assert not e._ck_reverse_order_authorized(SYMBOL,{})

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('kind',['normal','waterfall','double'])
async def test_ma3_removed_emergencies_even_when_armed(side,kind):
    f,price=market(side,kind)
    e=_execution_engine(f,side,True);e.account.save_state=lambda:None
    p=e.account.positions[SYMBOL];p.update(entry_price=100.,qty=1.,open_timestamp=1.)
    p['channel_significant_ma3_turn']={'version':3,'pending':True}
    e.account.position_meta[SYMBOL]={'channel_significant_ma3_turn':{'pending':True}}
    if kind!='normal': protection(p,quote(6.01,side),.0005,.0001)
    e.tickers[SYMBOL]=price
    await e._process_single_symbol(SYMBOL,2.,None,False)
    if kind=='normal':
        assert not e.account.events
        assert 'channel_significant_ma3_turn' not in p
        assert 'channel_significant_ma3_turn' not in e.account.position_meta[SYMBOL]
    else:
        assert e.account.events[0][0]=='close'
        assert 'EMERGENCY_EXIT' in e.account.events[0][3]

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('success',[False,True])
async def test_fixed_step_order_and_failed_close_pending(side,success):
    f,_=market(side,'normal'); price=quote(1.99,side)
    e=_execution_engine(f,side,success);e.account.save_state=lambda:None
    p=e.account.positions[SYMBOL];p.update(entry_price=100.,qty=1.,open_timestamp=1.)
    protection(p,quote(4.01,side),.0005,.0001)
    e.tickers[SYMBOL]=price
    await e._process_single_symbol(SYMBOL,2.,None,False)
    assert [x[0] for x in e.account.events]==['close']
    assert 'PROFIT_PROTECTION' in e.account.events[0][3]
    if not success:
        assert p['channel_profit_protection']['pending']
        e.tickers[SYMBOL]=quote(3.,side)
        await e._process_single_symbol(SYMBOL,3.,None,False)
        assert [x[0] for x in e.account.events]==['close','close']
