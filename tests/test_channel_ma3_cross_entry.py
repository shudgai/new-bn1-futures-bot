"""MA3, rather than candle bodies, must newly cross the directional KC rail."""
import copy
from unittest.mock import AsyncMock
import pytest
from core.channel_outer_entry import aligned_entry, ma3_outer_cross_ready, outside_reentry
from core.engine import TradingEngine
from channel_test_frames import closed_outer_entry_frame
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'

def crossing(side):
    f=closed_outer_entry_frame(side)
    f['timestamp']=[(i+1)*60000 for i in range(len(f))]
    # Both closed candles are doji: old two-body confirmation must be irrelevant.
    f['open']=f['close']
    return f,float(f.iloc[-1]['close'])

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('case',['valid','price_only','touch','already_outside','wrong_direction','invalid','stale_ma'])
def test_shared_crossing_rule(side,case):
    f,price=crossing(side);s=1 if side=='LONG' else -1
    rail='kc_upper' if side=='LONG' else 'kc_lower'
    if case=='price_only':
        f.loc[f.index[-3:-1],'close']=100+s
        f['open']=f['close'];f['high']=f['close']+.1;f['low']=f['close']-.1
        price=float(f.iloc[-1][rail])+s*.01
    if case=='touch': price=3*float(f.iloc[-1][rail])-sum(f['close'].iloc[-3:-1])
    if case=='already_outside':
        f.loc[f.index[-4],'close']=float(f.iloc[-2][rail])+s*2
        f['open']=f['close'];f['high']=f['close']+.1;f['low']=f['close']-.1
    if case=='wrong_direction': f.loc[f.index[-2],'kc_middle']=float(f.iloc[-3]['kc_middle'])-s*.1
    if case=='invalid': f.loc[f.index[-2],'close']=float('nan')
    if case=='stale_ma': f['ma3']=1.
    expected=case in ('valid','stale_ma')
    assert (aligned_entry(f,price)['action']=='ENTER') is expected
    assert (outside_reentry(f,price,side)['action']=='ENTER') is expected
    assert (TradingEngine._channel_swing_action(f,price)['action']=='ENTER') is expected

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('route',['fresh','cached','reentry'])
@pytest.mark.parametrize('lost_cross',[False,True])
async def test_real_order_revalidates_latest_ma3_cross_without_two_bodies(side,route,lost_cross,monkeypatch):
    f,price=crossing(side);s=1 if side=='LONG' else -1
    e=_execution_engine(f,side,True);del e._channel_intrabar_ready
    e.account.positions.clear();e.account.save_state=lambda:None
    e._abnormal_market_entry_allowed=lambda *a,**k:True
    now=float(f.iloc[-1]['timestamp'])/1000+1
    monkeypatch.setattr('core.engine.time.time',lambda:now)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',[SYMBOL])
    signal=dict(side=side,entry_mode='CHANNEL_SWING',action='ENTER_MARKET',reason='MA3 cross')
    if route=='reentry':
        signal['profit_reentry_token']='new'
        e.account.channel_profit_reentries={SYMBOL:dict(side=side,token='new',phase='closed',mode='outer_cycle',requires_pullback=False,exit_bar_id=float(f.iloc[-2]['timestamp']))}
    snapshot=dict(price=price,frame=f.copy(),kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower']))
    current=price if not lost_cross else float(f.iloc[-1]['kc_upper' if side=='LONG' else 'kc_lower'])+s*.01
    e.tickers[SYMBOL]=current;e._observe_channel_entry_quote(SYMBOL,current,now*1000)
    placed=await e._place_structured_entry(SYMBOL,signal,price,channel_snapshot=snapshot if route=='cached' else None)
    assert bool(placed) is (not lost_cross),e.account.logs
    assert [v[0] for v in e.account.events]==([] if lost_cross else ['open'])

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_cross_is_immediate_and_does_not_use_live_candle_close(side):
    f,price=crossing(side)
    before=copy.deepcopy(f)
    f.loc[f.index[-1],'close']=f.loc[f.index[-1],'open']
    assert ma3_outer_cross_ready(f,price,side)
    assert f.iloc[-1]['timestamp']==before.iloc[-1]['timestamp']

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
async def test_quote_entry_path_fills_without_closed_bodies(side,monkeypatch):
    f,price=crossing(side)
    e=_execution_engine(f,side,True);del e._channel_intrabar_ready
    e.account.positions.clear();e.account.save_state=lambda:None
    e._abnormal_market_entry_allowed=lambda *a,**k:True
    e._ck_reverse_new_leg_halted=lambda:False
    now=float(f.iloc[-1]['timestamp'])/1000+1
    monkeypatch.setattr('core.engine.time.time',lambda:now)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',[SYMBOL])
    monkeypatch.setattr('core.engine.SYMBOL_ROTATION_ENABLED',False)
    e.tickers[SYMBOL]=price;e._observe_channel_entry_quote(SYMBOL,price,now*1000)
    assert await e._try_live_pivot_entry(SYMBOL,f,price),e.account.logs
    assert [v[0] for v in e.account.events]==['open']
