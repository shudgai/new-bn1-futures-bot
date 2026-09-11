"""First live long-body break enters either side before CK or MA3 confirms."""
import pytest
from core.services.strategies.outer_strategy import aligned_entry, live_body_breakout_side
from core.engine import TradingEngine
from core.services.entry_diagnostics_service import entry_diagnostics
from channel_test_frames import closed_outer_entry_frame
from test_channel_swing_execution import _execution_engine, SYMBOL

@pytest.fixture
def anyio_backend(): return 'asyncio'

def breakout_frame(side, scale=1., ck='opposite'):
    f = closed_outer_entry_frame(side)
    sign = 1 if side == 'LONG' else -1
    price = float(f.iloc[-1]['close'])
    f.loc[f.index[-1], 'open'] = price - sign * 1.7
    f['high'] = f[['open','close']].max(axis=1) + .1
    f['low'] = f[['open','close']].min(axis=1) - .1
    f.loc[5, 'high' if side=='LONG' else 'low'] = 110. if side=='LONG' else 90.
    f.loc[f.index[-3:-1], 'kc_middle'] = [100., 100. if ck=='flat' else 100.-sign*.1]
    f['ma3'] = 80. if side=='LONG' else 120.
    f['timestamp'] = [(i+1)*60000. for i in range(len(f))]
    for col in ('open','high','low','close','kc_upper','kc_lower','kc_middle','ema_20','ma3','ma15','atr'):
        f[col] *= scale
    return f, price*scale

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('ck',['flat','opposite'])
@pytest.mark.parametrize('scale',[1.,.00003])
def test_first_body_break_ignores_ck_and_ma3_confirmation(side,ck,scale):
    f, price = breakout_frame(side,scale,ck)
    assert aligned_entry(f,price)==dict(action='ENTER',side=side,reason='KC_LIVE_BODY_BREAKOUT_'+side)
    assert TradingEngine._channel_swing_action(f,price)['side']==side
    f.loc[f.index[-1],'atr']=1e9
    f.loc[f.index[-1],'kc_middle']=float('nan')
    assert aligned_entry(f,price)['side']==side

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('kind',['wick','touch','gap','small','invalid_atr'])
def test_breakout_requires_live_body_not_wick(side,kind):
    f, price=breakout_frame(side,ck='flat');sign=1 if side=='LONG' else -1
    rail=float(f.iloc[-1]['kc_upper' if side=='LONG' else 'kc_lower'])
    if kind=='wick': price=rail-sign*.01
    if kind=='touch': price=rail
    if kind=='gap': f.loc[f.index[-1],'open']=rail+sign*.01
    if kind=='small':
        price=rail+sign*.01
        f.loc[f.index[-1],'open']=rail-sign*.1
    if kind=='invalid_atr': f.loc[f.index[-2],'atr']=float('nan')
    assert live_body_breakout_side(f,price) is None
    assert aligned_entry(f,price)['action']=='WAIT'

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('body,expected',[(.4999,False),(.5,True),(.5001,True)])
def test_closed_atr_body_threshold(side,body,expected):
    f,price=breakout_frame(side,ck='flat');sign=1 if side=='LONG' else -1
    rail=float(f.iloc[-1]['kc_upper' if side=='LONG' else 'kc_lower'])
    f.loc[f.index[-1],'open']=rail-sign*.1
    price=rail-sign*.1+sign*body
    assert (live_body_breakout_side(f,price)==side) is expected

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('route',['fresh','cached','reentry','scan','quote','runner'])
async def test_actual_order_paths_accept_first_break(side,route,monkeypatch):
    f,price=breakout_frame(side,.00003)
    e=_execution_engine(f,side,True);e.account.positions.clear();e.account.save_state=lambda:None
    del e._channel_intrabar_ready
    e._abnormal_market_entry_allowed=lambda *a,**kw:True
    now=float(f.iloc[-1]['timestamp'])/1000+1
    monkeypatch.setattr('core.engine.time.time',lambda:now)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',[SYMBOL])
    monkeypatch.setattr('core.engine.SYMBOL_ROTATION_ENABLED',False)
    e._ck_reverse_new_leg_halted=lambda:False
    e.tickers[SYMBOL]=price;e._observe_channel_entry_quote(SYMBOL,price,now*1000)
    signal=dict(side=side,entry_mode='CHANNEL_SWING',action='ENTER_MARKET',reason='first body',signal_code='KC_LIVE_BODY_BREAKOUT_'+side,live_outer=True)
    if route=='reentry':
        signal['profit_reentry_token']='new'
        e.account.channel_profit_reentries={SYMBOL:dict(side=side,token='new',phase='closed',mode='outer_cycle',requires_pullback=False,exit_bar_id=float(f.iloc[-2]['timestamp']))}
    snapshot=dict(frame=f.copy(),price=price,kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower']))
    if route=='runner':
        from core.services.symbol_runner import process_single_symbol_runner
        await process_single_symbol_runner(e,SYMBOL,now,None,False)
        result=bool(e.account.events)
    elif route=='scan': result=await e._execute_confirmed_channel_break(SYMBOL,f,price,side)
    elif route=='quote': result=await e._try_live_pivot_entry(SYMBOL,f,price)
    else: result=await e._place_structured_entry(SYMBOL,signal,price,channel_snapshot=snapshot if route=='cached' else None)
    assert result,e.account.logs
    assert [v[0] for v in e.account.events]==['open']

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('block',['quote_retracted','room','same_bar','stale','held','terminal','daily'])
async def test_breakout_does_not_bypass_guards(side,block,monkeypatch):
    f,price=breakout_frame(side)
    e=_execution_engine(f,side,True);e.account.positions.clear();e.account.save_state=lambda:None
    del e._channel_intrabar_ready
    e._abnormal_market_entry_allowed=lambda *a,**kw:True
    now=float(f.iloc[-1]['timestamp'])/1000+1
    monkeypatch.setattr('core.engine.time.time',lambda:now)
    monkeypatch.setattr('core.engine.DEFAULT_SYMBOLS',[SYMBOL])
    e.tickers[SYMBOL]=price;e._observe_channel_entry_quote(SYMBOL,price,now*1000)
    snapshot=dict(frame=f.copy(),price=price,kc_upper=float(f.iloc[-1]['kc_upper']),kc_lower=float(f.iloc[-1]['kc_lower']))
    if block=='quote_retracted': e.tickers[SYMBOL]=float(f.iloc[-1]['open'])
    if block=='room': f.loc[5,'high' if side=='LONG' else 'low']=price+(.01 if side=='LONG' else -.01)
    if block=='same_bar': e.account.last_closed_at={SYMBOL:now}
    if block=='stale': e._channel_entry_quote_times[SYMBOL]=now-6
    if block=='held': e.account.positions[SYMBOL]={'side':side}
    if block=='terminal':
        sign=1 if side=='LONG' else -1
        f['volume']=1.;f['vol_ma_20']=1.
        for key in ('kc_upper','kc_lower'):
            last=float(f.iloc[-2][key]);f.loc[f.index[-5:-1],key]=[last-sign*x for x in (.03,.02,.01,0)]
        f.loc[f.index[-5:-1],'ma3']=f.loc[f.index[-5:-1],'ma15']+sign
        assert TradingEngine._channel_terminal_market(f)
    if block=='daily': e._ck_reverse_new_leg_halted=lambda:True
    signal=dict(side=side,entry_mode='CHANNEL_SWING',action='ENTER_MARKET',reason='first body',signal_code='KC_LIVE_BODY_BREAKOUT_'+side,live_outer=True)
    assert not await e._place_structured_entry(SYMBOL,signal,price,channel_snapshot=snapshot)
    assert not e.account.events

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_diagnostics_use_breakout_side_instead_of_old_ck(side):
    f,price=breakout_frame(side)
    e=_execution_engine(f,side,True);e.account.positions.clear();e.is_running=True
    now=float(f.iloc[-1]['timestamp'])/1000+1
    e._channel_entry_quote_times={SYMBOL:now};e._channel_candle_entry_blocked=lambda *a:False
    before=f.copy(deep=True)
    d=entry_diagnostics(e,SYMBOL,f,price,now)
    assert d['reason']=='KC_ENTRY_READY'
    assert d['side']==side and d['outer_signal']=='KC_LIVE_BODY_BREAKOUT_'+side
    assert f.equals(before) and not e.account.events
