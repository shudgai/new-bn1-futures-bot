import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import pandas as pd
from core.services.exits.fading_exit_service import fading_ma3_turn, immediate_ma3_turn, next_breakout_ready, STATE_KEY, EXIT_REASON, IMMEDIATE_EXIT_REASON
from core.services.strategies.outer_strategy import ck_momentum_fading, ck_entry_momentum_ready
from test_channel_significant_ma3 import setup
from test_channel_swing_execution import _execution_engine, SYMBOL
from channel_test_frames import closed_outer_entry_frame

@pytest.fixture
def anyio_backend(): return 'asyncio'

def fading_frame(side, fading=True):
    f,p,s=setup(side)
    prefix = f.iloc[:2].copy()
    prefix["timestamp"] -= 120000
    f = pd.concat([prefix, f], ignore_index=True)
    f["kc_middle"] = 100.
    values=[0.,.3,.5,.6] if fading else [0.,.1,.3,.6]
    f.loc[f.index[-5:-1],'kc_middle']=[100+s*v for v in values]
    middle = f["kc_middle"] if "kc_middle" in f else f["ema_20"]
    f["kc_upper"] = middle * 1.02
    f["kc_lower"] = middle * .98
    if s > 0:
        f.loc[f.index[-2], "kc_upper"] = middle.iloc[-2] * 1.025
        f.loc[f.index[-2], "kc_lower"] = middle.iloc[-2] * .995
    else:
        f.loc[f.index[-2], "kc_upper"] = middle.iloc[-2] * 1.01
        f.loc[f.index[-2], "kc_lower"] = middle.iloc[-2] * .98
    return f,p,s

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('armed',[False,True])
@pytest.mark.parametrize('fading',[False,True])
def test_only_fading_and_post_entry_significant_turn_exits(side,armed,fading):
    f,p,s=fading_frame(side,fading)
    p['channel_profit_protection']={'armed':armed}
    assert not fading_ma3_turn(p,f,100.)
    assert not fading_ma3_turn(p,f,100+s*.3)
    assert not fading_ma3_turn(p,f,100-s*.03)
    assert fading_ma3_turn(p,f,100-s*.3) is fading
    assert p['channel_profit_protection']['armed'] is armed

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_invalid_ck_is_not_fading_and_live_ck_ignored(side):
    f,p,s=fading_frame(side)
    assert ck_momentum_fading(f,side) is True
    f.loc[f.index[-1],'kc_middle']=float('nan')
    assert ck_momentum_fading(f,side) is True
    f.loc[f.index[-2],'kc_middle']=float('nan')
    assert ck_momentum_fading(f,side) is None
    assert not ck_entry_momentum_ready(f,side)
    assert not fading_ma3_turn(p,f,100.)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_retired_immediate_ma3_turn_never_authorizes_close(side):
    sign = 1 if side == 'LONG' else -1
    p = {'side': side, 'entry_price': 100., 'open_timestamp': 1200.}
    rows = pd.DataFrame({
        'timestamp': [1200000, 1260000, 1320000, 1380000, 1440000],
        'close': [100., 101., 102., 103., 104.],
        'open': [100., 100., 101., 102., 103.],
        'atr': [100.] * 5,
    })
    first = 106. if sign == 1 else 98.
    favorable = 108. if sign == 1 else 96.
    adverse = 95. if sign == 1 else 105.
    assert not immediate_ma3_turn(p, rows, first)
    rows.loc[4, 'timestamp'] = 1500000
    assert not immediate_ma3_turn(p, rows, favorable)
    rows.loc[4, 'timestamp'] = 1560000
    rows.loc[2, 'close'] = 104.
    rows.loc[3, 'close'] = 108.
    p['channel_immediate_ma3_turn'] = {'pending': True}
    assert not immediate_ma3_turn(p, rows, adverse)
    assert 'channel_immediate_ma3_turn' not in p

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_old_nonfading_turn_cannot_fire_later(side):
    f,p,s=fading_frame(side,False)
    for price in [100.,100+s*.3,100-s*.3]:
        assert not fading_ma3_turn(p,f,price)
    f,_,_=fading_frame(side)
    assert not fading_ma3_turn(p,f,100-s*.3)
    assert not fading_ma3_turn(p,f,100+s*.6)
    assert fading_ma3_turn(p,f,100-s*.3)
    p=json.loads(json.dumps(p))
    assert fading_ma3_turn(p,None,100.)
    p['open_timestamp']+=1
    assert not fading_ma3_turn(p,f,100.)

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('success',[True,False])
@pytest.mark.parametrize('armed',[False,True])
async def test_live_exit_and_restart_retry_no_reverse(side,success,armed,monkeypatch):
    f,p,s=fading_frame(side)
    if armed: p['entry_price']=100-s*5
    e=_execution_engine(f,side,success);e.is_running=True
    e.account.save_state=lambda:None
    e.account.positions[SYMBOL].update(p)
    e._channel_exit_frames={SYMBOL:f}
    monkeypatch.setattr('core.engine.time.time',lambda:1201.)
    for price in [100.,100+s*.3,100-s*.03]:
        await e._channel_quote_exit(SYMBOL,price,1201000)
        assert not e.account.events,e.account.logs
    await e._channel_quote_exit(SYMBOL,100-s*.3,1201000)
    assert [v[0] for v in e.account.events]==['close'],e.account.logs
    assert e.account.events[0][3]=='Channel Swing '+EXIT_REASON
    if success:
        assert e.account.channel_profit_reentries[SYMBOL]['mode']=='next_breakout'
    else:
        e.account.positions[SYMBOL].pop(STATE_KEY)
        await e._channel_quote_exit(SYMBOL,100.,1201000)
        assert [v[0] for v in e.account.events]==['close','close']
        assert e.account.events[0][3]==e.account.events[1][3]

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('old_side',['LONG','SHORT'])
@pytest.mark.parametrize('case',['ready','old_break','same_bar','missing_fill','wrong_reason','inside','delayed_fill'])
def test_next_cross_requires_matched_fill_and_later_live_candle(side,old_side,case):
    f=closed_outer_entry_frame(side)
    f['timestamp']=[(i+1)*60000 for i in range(len(f))]
    price=float(f.iloc[-1]['close'])
    closed=float(f.iloc[-3]['timestamp'])-59000
    reason='Channel Swing '+EXIT_REASON
    ticket=dict(mode='next_breakout',phase='closed',side=old_side,close_reason=reason,close_requested_at_ms=closed-1)
    trade=dict(symbol=SYMBOL,action='CLOSE_'+old_side,reason=reason,id=closed)
    a=SimpleNamespace(positions={},channel_profit_reentries={SYMBOL:ticket},trades=[trade])
    if case=='old_break':
        sign=1 if side=='LONG' else -1
        f.loc[f.index[-4],'close']=100+sign*5
        f['open']=f['close'];f['high']=f['close']+.1;f['low']=f['close']-.1
    if case=='same_bar': trade['id']=float(f.iloc[-1]['timestamp'])+1
    if case=='missing_fill': a.trades=[]
    if case=='wrong_reason': trade['reason']='manual'
    if case=='inside': price=float(f.iloc[-1]['kc_middle'])
    if case=='delayed_fill': trade['id']=float(f.iloc[-1]['timestamp'])+1
    before=copy.deepcopy(a.__dict__)
    assert next_breakout_ready(a,SYMBOL,f,price) is (case=='ready')
    assert a.__dict__==before


def test_both_accounts_persist_new_state():
    from core.paper_account import ENTRY_CONTEXT_KEYS as paper
    from core.testnet_account import ENTRY_CONTEXT_KEYS as testnet
    assert STATE_KEY in paper and STATE_KEY in testnet

@pytest.mark.anyio
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('phase',['closing','closed'])
async def test_next_break_ticket_only_reopens_after_successful_close(side,phase):
    f=closed_outer_entry_frame(side);price=float(f.iloc[-1]['close'])
    e=_execution_engine(f,side,True);e.account.positions.clear()
    e.account.save_state=lambda:None
    e._place_structured_entry = AsyncMock(return_value=False)
    t=dict(mode='next_breakout',phase=phase,side=side,token='old',requires_pullback=False,exit_bar_id=1.)
    e.account.channel_profit_reentries={SYMBOL:t}
    before=copy.deepcopy(t)
    assert e._profit_reentry_ready(SYMBOL,t,f,price) is (phase == 'closed')
    await e._try_profit_reentry(SYMBOL,f,price,False)
    assert not e.account.events and t==before

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_diagnostic_is_readonly_and_release_returns_to_general_entry(side):
    from core.channel_entry_diagnostics import entry_diagnostics
    f=closed_outer_entry_frame(side);f['timestamp']=[(i+1)*60000 for i in range(len(f))]
    price=float(f.iloc[-1]['close']);now=float(f.iloc[-1]['timestamp'])/1000+1
    e=_execution_engine(f,side,True);e.account.positions.clear();e.is_running=True
    e.account.save_state=lambda:None;e._channel_entry_quote_times={SYMBOL:now}
    closed=float(f.iloc[-3]['timestamp'])-59000
    t=dict(mode='next_breakout',phase='closing',side=side,close_reason='Channel Swing '+EXIT_REASON,close_requested_at_ms=closed-1)
    e.account.channel_profit_reentries={SYMBOL:t}
    e.account.trades=[dict(symbol=SYMBOL,action='CLOSE_'+side,reason=t['close_reason'],id=closed)]
    before=copy.deepcopy(t)
    assert entry_diagnostics(e,SYMBOL,f,price,now)['reason']=='KC_ENTRY_READY'
    assert t==before
    assert e._release_resolved_abnormal_exit(SYMBOL,f,price)
    assert SYMBOL not in e.account.channel_profit_reentries
    assert not e.account.events

@pytest.mark.parametrize('ratio,expected',[(.5,True),(.75,True),(.751,False),(1.,False)])
def test_relative_channel_width_boundary(ratio,expected):
    from core.services.exits.fading_exit_service import ck_channel_narrow
    f,_,_=fading_frame('LONG')
    # Exact integer boundaries, avoiding fixture floating-point cancellation.
    f['kc_middle']=100.;f['kc_upper']=102.;f['kc_lower']=98.
    f.loc[f.index[-2],['kc_upper','kc_lower']]=[100+2*ratio,100-2*ratio]
    assert ck_channel_narrow(f) is expected
    f.loc[f.index[-1],['kc_upper','kc_lower']]=[float('nan'),float('nan')]
    assert ck_channel_narrow(f) is expected

@pytest.mark.parametrize('case',['short','nan','inverted','missing'])
def test_invalid_narrow_data_does_not_authorize_exit(case):
    from core.services.exits.fading_exit_service import ck_channel_narrow
    f,_,_=fading_frame('LONG')
    if case=='short': f=f.iloc[1:]
    if case=='nan': f.loc[f.index[0],'kc_upper']=float('nan')
    if case=='inverted': f.loc[f.index[-2],'kc_lower']=200.
    if case=='missing': f=f.drop(columns='kc_lower')
    assert not ck_channel_narrow(f)

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('armed',[False,True])
def test_wide_channel_blocks_exit_but_still_blocks_new_entry(side,armed):
    f,p,s=fading_frame(side)
    p['channel_profit_protection']={'armed':armed}
    middle=f['kc_middle']
    f['kc_upper']=middle*1.02;f['kc_lower']=middle*.98
    assert not ck_entry_momentum_ready(f,side)
    for price in [100.,100+s*.3,100-s*.3]:
        assert not fading_ma3_turn(p,f,price)
    assert not p[STATE_KEY]['pending']
    f.loc[f.index[-2],'kc_upper']=middle.iloc[-2]*1.01
    f.loc[f.index[-2],'kc_lower']=middle.iloc[-2]*.99
    assert not fading_ma3_turn(p,f,100-s*.3)
    assert not fading_ma3_turn(p,f,100+s*.6)
    assert fading_ma3_turn(p,f,100-s*.3)
    assert fading_ma3_turn(json.loads(json.dumps(p)),None,100.)


@pytest.mark.anyio
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('path', ['quote', 'scan'])
@pytest.mark.parametrize('old_pending', [False, True])
async def test_tick_pullback_while_ma3_still_forward_holds(side, path, old_pending, monkeypatch):
    f, p, sign = fading_frame(side)
    e = _execution_engine(f, side, True)
    e.is_running = True
    e.account.save_state = lambda: None
    e.account.positions[SYMBOL].update(p)
    e._channel_exit_frames = {SYMBOL: f}
    monkeypatch.setattr('core.engine.time.time', lambda: 1201.)
    if old_pending:
        stale = {'identity': [side, p['open_timestamp'], p['entry_price']], 'pending': True}
        e.account.positions[SYMBOL]['channel_immediate_ma3_turn'] = copy.deepcopy(stale)
        e.account.position_meta.setdefault(SYMBOL, {})['channel_immediate_ma3_turn'] = stale
    for price in [100., 100. + sign * .3, 100. + sign * .29, 100. + sign * .4]:
        if path == 'quote':
            await e._channel_quote_exit(SYMBOL, price, 1201000)
        else:
            e.tickers[SYMBOL] = price
            await e._process_single_symbol(SYMBOL, 2., None, False)
        assert not e.account.events, e.account.logs
    assert 'channel_immediate_ma3_turn' not in e.account.positions[SYMBOL]
    assert 'channel_immediate_ma3_turn' not in e.account.position_meta.get(SYMBOL, {})
