import pytest
from core.channel_outer_entry import aligned_entry, ck_entry_momentum_ready, ma3_outer_continuation_ready
from test_channel_ma3_continuation import continuing
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'

def ready(side):
    f,price=continuing(side);sign=1 if side=='LONG' else -1
    f.loc[f.index[-5:-1],'kc_middle']=[100+sign*x for x in (0,.1,.2,.4)]
    return f,price

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('steps,expected',[( [0,.1,.2,.4],True),([0,.3,.5,.6],False),([0,.3,.4,.5],False),([0,.3,.5,.65],False),([0,.3,.3,.4],True)])
def test_closed_momentum_must_strengthen(side,steps,expected):
    f,price=ready(side);sign=1 if side=='LONG' else -1
    f.loc[f.index[-5:-1],'kc_middle']=[100+sign*x for x in steps]
    assert ck_entry_momentum_ready(f,side) is expected
    f.loc[f.index[-1],'kc_middle']=float('nan')
    assert ck_entry_momentum_ready(f,side) is expected
    assert (aligned_entry(f,price)['action']=='ENTER') is expected

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('gap,expected',[(0,False),(.001,True),(.099,True),(.1,True),(.3,True)])
def test_ma3_gap_closed_atr_boundary(side,gap,expected):
    f,price=ready(side);sign=1 if side=='LONG' else -1
    ma=(float(f.iloc[-3]['close'])+float(f.iloc[-2]['close'])+price)/3
    f.loc[f.index[-1],'kc_upper' if side=='LONG' else 'kc_lower']=ma-sign*gap
    f.loc[f.index[-2],'atr']=1
    f.loc[f.index[-1],'atr']=1000
    assert ma3_outer_continuation_ready(f,price,side) is expected

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('route',['fresh','cached','reentry'])
@pytest.mark.parametrize('blocked',[None,'momentum','gap'])
async def test_all_order_routes_revalidate_recovery(side,route,blocked,monkeypatch):
    f,price=ready(side);sign=1 if side=='LONG' else -1
    snapshot=dict(price=price,frame=f.copy(),kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower']))
    if blocked=='momentum':
        f.loc[f.index[-5:-1],'kc_middle']=[100+sign*x for x in (0,.3,.5,.65)]
    if blocked=='gap':
        ma=(float(f.iloc[-3]['close'])+float(f.iloc[-2]['close'])+price)/3
        f.loc[f.index[-1],'kc_upper' if side=='LONG' else 'kc_lower']=ma-sign*.01
    e=_execution_engine(f,side,True);del e._channel_intrabar_ready
    e.account.positions.clear();e.account.save_state=lambda:None
    e._abnormal_market_entry_allowed=lambda *a,**k:True
    now=float(f.iloc[-1]['timestamp'])/1000+1
    monkeypatch.setattr('core.engine.time.time',lambda:now)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',[SYMBOL])
    signal=dict(side=side,entry_mode='CHANNEL_SWING',action='ENTER_MARKET',reason='recovery')
    if route=='reentry':
        signal['profit_reentry_token']='new'
        e.account.channel_profit_reentries={SYMBOL:dict(side=side,token='new',phase='closed',mode='outer_cycle',requires_pullback=False,exit_bar_id=float(f.iloc[-2]['timestamp']))}
    e.tickers[SYMBOL]=price;e._observe_channel_entry_quote(SYMBOL,price,now*1000)
    result=await e._place_structured_entry(SYMBOL,signal,price,channel_snapshot=snapshot if route=='cached' else None)
    assert bool(result) is (blocked != 'momentum'),e.account.logs
    assert [v[0] for v in e.account.events]==(['open'] if blocked != 'momentum' else [])
