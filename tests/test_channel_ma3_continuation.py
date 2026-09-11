"""Missed MA3 crossings remain eligible without a new inside-to-outside crossing."""
import pytest
from core.services.strategies.outer_strategy import aligned_entry, outside_reentry, ma3_outer_cross_ready
from test_channel_ma3_cross_entry import crossing
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'

def continuing(side):
    f,price=crossing(side);s=1 if side=='LONG' else -1
    rail='kc_upper' if side=='LONG' else 'kc_lower'
    f.loc[f.index[-4],'close']=float(f.iloc[-2][rail])+s*.1
    f['open']=f['close'];f['high']=f['close']+.1;f['low']=f['close']-.1
    return f,price

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_missed_cross_continues_without_new_cross(side):
    f,price=continuing(side)
    assert not ma3_outer_cross_ready(f,price,side)
    assert aligned_entry(f,price)['side']==side
    assert outside_reentry(f,price,side)['side']==side

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('route',['fresh','cached','reentry'])
async def test_continuation_orders_pass_all_shared_gates(side,route,monkeypatch):
    f,price=continuing(side)
    e=_execution_engine(f,side,True);del e._channel_intrabar_ready
    e.account.positions.clear();e.account.save_state=lambda:None
    e._abnormal_market_entry_allowed=lambda *a,**k:True
    now=float(f.iloc[-1]['timestamp'])/1000+1
    monkeypatch.setattr('core.engine.time.time',lambda:now)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',[SYMBOL])
    signal=dict(side=side,entry_mode='CHANNEL_SWING',action='ENTER_MARKET',reason='MA3 continuation')
    if route=='reentry':
        signal['profit_reentry_token']='new'
        e.account.channel_profit_reentries={SYMBOL:dict(side=side,token='new',phase='closed',mode='outer_cycle',requires_pullback=False,exit_bar_id=float(f.iloc[-2]['timestamp']))}
    e.tickers[SYMBOL]=price;e._observe_channel_entry_quote(SYMBOL,price,now*1000)
    snapshot=dict(price=price,frame=f.copy(),kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower']))
    assert await e._place_structured_entry(SYMBOL,signal,price,channel_snapshot=snapshot if route=='cached' else None),e.account.logs
    assert [v[0] for v in e.account.events]==['open']

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('kind',['profit','abnormal','same_bar'])
def test_normal_close_next_bar_continues_but_abnormal_needs_pullback(side,kind):
    f,price=continuing(side)
    e=_execution_engine(f,side,True);e.account.positions.clear();e.account.save_state=lambda:None
    exited=float(f.iloc[-1 if kind=='same_bar' else -2]['timestamp'])
    ticket=dict(side=side,token='close',phase='closed',mode='outer_cycle',requires_pullback=kind=='abnormal',exit_bar_id=exited)
    assert e._profit_reentry_ready(SYMBOL,ticket,f,price) is (kind=='profit')
    assert 'pullback_bar' not in ticket

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_fading_close_next_bar_accepts_continuation_with_matched_fill(side):
    from core.services.exits.fading_exit_service import next_breakout_ready, EXIT_REASON
    f,price=continuing(side)
    e=_execution_engine(f,side,True);e.account.positions.clear();e.account.save_state=lambda:None
    closed=float(f.iloc[-2]['timestamp'])+1000
    ticket=dict(side=side,phase='closed',mode='next_breakout',close_reason='Channel Swing '+EXIT_REASON,close_requested_at_ms=closed-1)
    e.account.channel_profit_reentries={SYMBOL:ticket}
    e.account.trades=[dict(symbol=SYMBOL,action='CLOSE_'+side,id=closed,reason=ticket['close_reason'])]
    assert not ma3_outer_cross_ready(f,price,side)
    assert next_breakout_ready(e.account,SYMBOL,f,price)
    assert e._release_resolved_abnormal_exit(SYMBOL,f,price)
    assert SYMBOL not in e.account.channel_profit_reentries
