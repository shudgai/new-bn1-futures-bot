import math
from types import SimpleNamespace
import pandas as pd
import pytest
from core.engine import TradingEngine
from core.services.entry_contract import evaluate_continuation_entry, evaluate_entry_contract
from core.services.strategies.outer_strategy import live_body_breakout_side
from tools.deployment_preflight import evaluate


def frame(side='LONG'):
    rows = [dict(timestamp=(i+1)*60000, open=101.1, close=101.3,
                 high=101.5, low=100.9, atr=1., ma5=101.1, ma3=100.2,
                 ma15=100., kc_upper=101., kc_middle=100.+i*.01,
                 kc_lower=99., is_closed=True) for i in range(6)]
    rows[-1].update(open=100.8, close=101.5, high=101.5, low=100.6, is_closed=False)
    f=pd.DataFrame(rows)
    if side=='SHORT':
        for key in ('open','close','high','low','ma3','ma5','ma15','kc_upper','kc_lower','kc_middle'):
            f[key]=200.-f[key]
        f[['high','low']]=f[['low','high']].to_numpy()
        f[['kc_upper','kc_lower']]=f[['kc_lower','kc_upper']].to_numpy()
    return f

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_live_breakout_and_shared_contract(side):
    f=frame(side);q=float(f.iloc[-1].close)
    assert live_body_breakout_side(f,q)==side
    d=evaluate_entry_contract(f,q,symbol='CAP/USDT')
    assert d and d['type']=='KC_LIVE_BODY_BREAKOUT_'+side

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['gap','touch','body','atr'])
def test_breakout_fail_closed(side,fault):
    f=frame(side);sign=1 if side=='LONG' else -1
    if fault=='gap':f.loc[5,'open']=100.+sign*1.1
    if fault=='touch':f.loc[5,'close']=100.+sign
    if fault=='body':f.loc[5,'close']=100.+sign*1.2
    if fault=='atr':f.loc[4,'atr']=math.nan
    assert live_body_breakout_side(f,float(f.iloc[-1].close)) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_continuation_quote_and_ma5(side):
    f=frame(side);q=float(f.iloc[-1].close)
    assert evaluate_continuation_entry(f,q,symbol='CAP/USDT')
    assert evaluate_continuation_entry(f,100.,symbol='CAP/USDT') is None
    f.loc[4,'ma5']=q
    assert evaluate_continuation_entry(f,q,symbol='CAP/USDT') is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_same_bar_successful_close_can_reverse(side):
    f=frame(side);a=SimpleNamespace(trades=[dict(symbol='CAP/USDT',action='CLOSE_'+('SHORT' if side=='LONG' else 'LONG'),id=360001)],last_closed_at={})
    d=evaluate_entry_contract(f,float(f.iloc[-1].close),account=a,symbol='CAP/USDT')
    assert d and d['side']==side and d['same_bar_close_id']==360001


def test_half_wallet_and_fee_cap():
    assert TradingEngine._half_wallet_entry_margin(100.,100.,5.)==50.
    assert TradingEngine._half_wallet_entry_margin(100.,20.,5.)<20.
    assert TradingEngine._half_wallet_entry_margin(math.nan,100.,5.)==0.


def test_preflight_requires_all_evidence_and_authorization():
    e=dict(commit='abc',deploy_authorized=True,**{key:'VERIFIED' for key in ('runtime_source','candidate_integrity','required_files','tests','account_exposure')})
    assert evaluate(e,'abc',True)['DEPLOYMENT_PREFLIGHT_GATE']=='PASS'
    for key in ('runtime_source','candidate_integrity','required_files','tests','account_exposure','deploy_authorized','commit'):
        bad=dict(e);bad.pop(key)
        assert evaluate(bad,'abc',True)['DEPLOYMENT_PREFLIGHT_GATE']=='BLOCK'
    assert evaluate(e,'abc',False)['DEPLOYMENT_PREFLIGHT_GATE']=='BLOCK'

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('peak,limit',[(.5,.60),(1.,.50),(2.,.40),(3.,.35)])
def test_dynamic_atr_pullback(side,peak,limit):
    from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=1.,open_timestamp=60.,entry_atr=1.,margin=100.,entry_mode='CHANNEL_SWING',leverage=1.)
    evaluate_peak_trailing(p,100.+sign*peak,61000,fee=0.,slippage=0.)
    assert evaluate_peak_trailing(p,100.+sign*(peak-limit+.001),62000,fee=0.,slippage=0.) is None
    d=evaluate_peak_trailing(p,100.+sign*(peak-limit-.001),63000,fee=0.,slippage=0.)
    if peak < 1.:
        assert d is None
    else:
        assert d and d['trigger']=='EXIT_PEAK_PULLBACK_PRESSURE'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_initial_stop_is_independent(side):
    from core.services.exits.peak_trailing_exit import evaluate_peak_trailing, HARD_REASON
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=1.,open_timestamp=60.,entry_atr=1.,margin=100.,entry_mode='CHANNEL_SWING',leverage=1.)
    d=evaluate_peak_trailing(p,100.-sign*1.6,61000,fee=0.,slippage=0.)
    assert d and d['type']==HARD_REASON

@pytest.mark.parametrize('symbol',['CAP/USDT','龙虾/USDT'])
@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('entry',['breakout','continuation'])
@pytest.mark.parametrize('reentry',[False,True])
def test_runner_real_paper_and_duplicate(monkeypatch,symbol,side,entry,reentry):
    import asyncio,time
    from unittest.mock import AsyncMock
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    monkeypatch.setattr('core.services.entry_finality.READ_INTERVAL_SECONDS',0.)
    f=frame(side);now=int(time.time()//60)*60000
    f['timestamp']=[now-(5-i)*60000 for i in range(6)]
    monkeypatch.setattr(time,'time',lambda:now/1000+10.)
    if entry=='continuation':
        f.loc[5,'open']=float(f.iloc[-1].close)-(0.3 if side=='LONG' else -0.3)
    a=PaperAccount();a.balance=100.
    if reentry:
        exit_bar=now-60000
        reason='Channel Swing PROFIT_PROTECTION test-token'
        a.trades.append(dict(symbol=symbol,action='CLOSE_'+side,id=exit_bar+10000,reason=reason))
        a.channel_profit_reentries[symbol]=dict(side=side,old_side=side,phase='closed',mode='outer_cycle',token='test-token',close_reason=reason,exit_bar_id=exit_bar,close_requested_at_ms=exit_bar+1000,requires_pullback=False)
    e=object.__new__(TradingEngine);e.account=a
    e.exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=time.time()*1000))
    e.tickers={symbol:float(f.iloc[-1].close)}
    e.fetch_klines=AsyncMock(return_value=f)
    e.strategy=SimpleNamespace(compute_indicators=lambda x:x)
    e.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    e._execution_price_is_safe=AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(e,symbol,time.time(),None,False,exit_frame=f))
    assert symbol in a.positions
    assert a.positions[symbol]['side']==side
    assert a.positions[symbol]['margin']<=50.
    assert max(a.trades,key=lambda t:t['id'])['entry_snapshot']['signal_code']==('KC_LIVE_BODY_BREAKOUT_' if entry=='breakout' else 'KC_OUTSIDE_')+side
    a.positions.clear()
    asyncio.run(process_single_symbol_runner(e,symbol,time.time(),None,False,exit_frame=f))
    assert len(a.trades)==(2 if reentry else 1)

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('entry',['breakout','continuation'])
@pytest.mark.parametrize('fault',['none','retreat','invalid_atr','missing_finality','changed_signal','flat_ma5'])
def test_account_fresh_revalidation(monkeypatch,side,entry,fault):
    import asyncio,time
    from unittest.mock import AsyncMock
    from core.services.entry_firewall import validate_account_entry
    f=frame(side);now=int(time.time()//60)*60000
    f['timestamp']=[now-(5-i)*60000 for i in range(6)]
    monkeypatch.setattr(time,'time',lambda:now/1000+10.)
    if entry=='continuation':f.loc[5,'open']=float(f.iloc[-1].close)-(0.3 if side=='LONG' else -0.3)
    f.attrs['entry_finality_verified']=True
    d=evaluate_entry_contract(f,float(f.iloc[-1].close),symbol='CAP/USDT')
    assert d
    ctx=dict(entry_signal_code=d['type'],channel_confirmation_bar_id=d['confirmation_bar_id'])
    if fault=='retreat':f.loc[5,'close']=100.
    if fault=='invalid_atr':f.loc[4,'atr']=math.nan
    if fault=='flat_ma5':f.loc[4,'ma5']=(sum(float(v) for v in f.close.iloc[-5:-1])+float(f.iloc[-1].close))/5.
    if fault=='missing_finality':f.attrs.clear()
    if fault=='changed_signal':ctx['channel_confirmation_bar_id']-=60000
    a=SimpleNamespace(positions={},trades=[],last_closed_at={},entry_frame_provider=AsyncMock(return_value=f))
    if fault=='none':assert asyncio.run(validate_account_entry(a,'CAP/USDT',side,ctx))['type']==d['type']
    else:
        with pytest.raises(ValueError):asyncio.run(validate_account_entry(a,'CAP/USDT',side,ctx))

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_profit_reentry_retreat_gate(side):
    e=object.__new__(TradingEngine);e.account=SimpleNamespace(trades=[])
    assert e._profit_reentry_ready('CAP/USDT',dict(side=side),frame(side),100.) is False

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_two_bar_code_and_live_rail_revalidation(monkeypatch,side):
    from core.services.entry_firewall import validate_entry_frame
    f=frame('LONG')
    f.loc[3,['open','close','high','low','ma5','ma15']]=[100.8,101.2,101.3,100.7,100.,99.9]
    f.loc[4,['open','close','high','low','ma5','ma15']]=[101.1,101.5,101.6,101.,100.1,99.9]
    f.loc[5,['open','close','high','low']]=[101.7,101.8,101.9,101.6]
    if side=='SHORT':
        for key in ('open','close','high','low','ma3','ma5','ma15','kc_upper','kc_lower','kc_middle'):f[key]=200.-f[key]
        f[['high','low']]=f[['low','high']].to_numpy()
        f[['kc_upper','kc_lower']]=f[['kc_lower','kc_upper']].to_numpy()
    code='KC_2BAR_CONFIRM_'+side
    assert validate_entry_frame(f,side,code)['type']==code
    f.loc[5,'kc_upper' if side=='LONG' else 'kc_lower']=102. if side=='LONG' else 98.
    assert evaluate_entry_contract(f,float(f.iloc[-1].close),code) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_abnormal_reentry_observes_pullback_before_real_fill(monkeypatch,side):
    import asyncio,time
    from unittest.mock import AsyncMock
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    monkeypatch.setattr('core.services.entry_finality.READ_INTERVAL_SECONDS',0.)
    now=int(time.time()//60)*60000;monkeypatch.setattr(time,'time',lambda:now/1000+10.)
    f=frame(side);f['timestamp']=[now-(5-i)*60000 for i in range(6)]
    symbol='CAP/USDT';a=PaperAccount();a.balance=100.
    ticket=dict(side=side,old_side=side,phase='closed',mode='outer_cycle',token='abnormal',close_reason='WATERFALL_DROP',exit_bar_id=now-60000,close_requested_at_ms=now-59000,requires_pullback=True)
    a.trades=[dict(symbol=symbol,action='CLOSE_'+side,id=now-50000,reason='WATERFALL_DROP')]
    a.channel_profit_reentries[symbol]=ticket
    e=object.__new__(TradingEngine);e.account=a
    e.exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=now+10000))
    e.tickers={symbol:float(f.iloc[-1].close)};e.fetch_klines=AsyncMock(return_value=f)
    e.strategy=SimpleNamespace(compute_indicators=lambda x:x)
    e.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    e._execution_price_is_safe=AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(e,symbol,time.time(),None,False,exit_frame=f))
    assert not a.positions and 'pullback_bar' not in ticket
    q=float(f.iloc[-1].close)
    asyncio.run(process_single_symbol_runner(e,symbol,time.time(),None,False,exit_frame=f,exit_quote=100.))
    assert not a.positions and ticket['pullback_bar']==now
    asyncio.run(process_single_symbol_runner(e,symbol,time.time(),None,False,exit_frame=f,exit_quote=q))
    assert a.positions[symbol]['side']==side
    assert symbol not in a.channel_profit_reentries

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_account_cannot_bypass_unmatched_ticket(monkeypatch,side):
    import asyncio,time
    from unittest.mock import AsyncMock
    from core.services.entry_firewall import validate_account_entry
    f=frame(side);now=int(time.time()//60)*60000
    monkeypatch.setattr(time,'time',lambda:now/1000+10.)
    f['timestamp']=[now-(5-i)*60000 for i in range(6)];f.attrs['entry_finality_verified']=True
    d=evaluate_entry_contract(f,float(f.iloc[-1].close),symbol='CAP/USDT')
    a=SimpleNamespace(positions={},trades=[],last_closed_at={},entry_frame_provider=AsyncMock(return_value=f),channel_profit_reentries={'CAP/USDT':dict(side=side,phase='closed',token='missing-fill',exit_bar_id=now-60000)})
    with pytest.raises(ValueError,match='重開票據'):
        asyncio.run(validate_account_entry(a,'CAP/USDT',side,dict(entry_signal_code=d['type'],channel_confirmation_bar_id=d['confirmation_bar_id'])))

@pytest.mark.parametrize('fault',['nan_rail','inverted_rails','nan_stamp','stale_bar','closed_tail'])
def test_continuation_invalid_live_data(fault):
    f=frame()
    if fault=='nan_rail':f.loc[5,'kc_upper']=math.nan
    if fault=='inverted_rails':f.loc[5,'kc_upper']=98.
    if fault=='nan_stamp':f.loc[5,'timestamp']=math.nan
    if fault=='stale_bar':f.loc[5,'timestamp']+=60000
    if fault=='closed_tail':f.loc[5,'is_closed']=True
    assert evaluate_continuation_entry(f,float(f.iloc[-1].close)) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_compatibility_state_machine_has_no_ma_exit_authority(side):
    from core.services.strategies.strict_state_machine import StrictStateMachineStrategy,PositionState
    strategy=StrictStateMachineStrategy()
    strategy.set_state('CAP/USDT',PositionState.LONG if side=='LONG' else PositionState.SHORT)
    assert strategy.evaluate_tick('CAP/USDT',frame(side),100.)['action']=='WAIT'

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['failed_close','missing_fill','already_reopened','retreat'])
def test_same_bar_reopen_fail_closed(side,fault):
    f=frame(side)
    close=dict(symbol='CAP/USDT',action='CLOSE_'+side,id=360001,status='CLOSED')
    a=SimpleNamespace(trades=[close],last_closed_at={'CAP/USDT':360.001})
    if fault=='failed_close':close['status']='FAILED'
    if fault=='missing_fill':a.trades=[]
    if fault=='already_reopened':a.trades.append(dict(symbol='CAP/USDT',action='OPEN_'+side,id=360002))
    q=100. if fault=='retreat' else float(f.iloc[-1].close)
    assert evaluate_entry_contract(f,q,account=a,symbol='CAP/USDT') is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('old_side',['LONG','SHORT'])
def test_same_bar_runner_real_fill_and_close_consumed(monkeypatch,side,old_side):
    import asyncio,time
    from unittest.mock import AsyncMock
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'log',lambda *a,**k:None)
    now=int(time.time()//60)*60000
    monkeypatch.setattr(time,'time',lambda:(now+10000)/1000)
    f=frame(side);f['timestamp']+=now-360000
    f.attrs['entry_finality_verified']=True
    a=PaperAccount();a.balance=100.
    a.trades.append(dict(symbol='CAP/USDT',action='CLOSE_'+old_side,id=now+1000,status='CLOSED',reason='Channel Swing PROFIT_PROTECTION token'))
    a.last_closed_at['CAP/USDT']=(now+1000)/1000
    a.channel_profit_reentries['CAP/USDT']=dict(side=old_side,old_side=old_side,phase='closed',mode='outer_cycle',token='token',close_reason='Channel Swing PROFIT_PROTECTION token',close_requested_at_ms=now,exit_bar_id=now,requires_pullback=False)
    e=object.__new__(TradingEngine);e.account=a
    e.exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=now+10000))
    e.fetch_klines=AsyncMock(return_value=f);e.strategy=SimpleNamespace(compute_indicators=lambda x:x)
    e.tickers={'CAP/USDT':float(f.iloc[-1].close)}
    e.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *a:2)
    e._execution_price_is_safe=AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(e,'CAP/USDT',time.time(),None,False,exit_frame=f))
    assert a.positions['CAP/USDT']['side']==side
    assert 'AFTER_CLOSE' in a.trades[0]['entry_snapshot']['pending_signal_id']
    a.positions.clear()
    asyncio.run(process_single_symbol_runner(e,'CAP/USDT',time.time(),None,False,exit_frame=f))
    assert len(a.trades)==2

@pytest.mark.parametrize('closed',[False,True])
def test_tick_close_reevaluates_only_after_success(monkeypatch,closed):
    import asyncio
    from unittest.mock import AsyncMock
    import core.services.exits.realtime_profit_exit as exits
    e=object.__new__(TradingEngine);e.account=SimpleNamespace(positions={})
    e._reevaluate_after_close=AsyncMock()
    monkeypatch.setattr(exits,'enforce_realtime_profit_exit',AsyncMock(return_value=closed))
    assert asyncio.run(e._instant_quote_exit('CAP/USDT',101.5))==closed
    assert e._reevaluate_after_close.await_count==int(closed)

def test_success_report_with_remaining_position_never_reopens(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    import core.services.exits.realtime_profit_exit as exits
    e=object.__new__(TradingEngine);e.account=SimpleNamespace(positions={'CAP/USDT':{}})
    e._reevaluate_after_close=AsyncMock()
    monkeypatch.setattr(exits,'enforce_realtime_profit_exit',AsyncMock(return_value=True))
    asyncio.run(e._instant_quote_exit('CAP/USDT',101.5))
    e._reevaluate_after_close.assert_not_awaited()

@pytest.mark.parametrize('trend',['HOLD','WARNING','UNKNOWN'])
@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_peak_pullback_cannot_be_vetoed_by_trend(monkeypatch,trend,side):
    import asyncio,time
    from unittest.mock import AsyncMock,Mock
    from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit
    import core.services.exits.realtime_profit_exit as exits
    from core.services.exits.peak_trailing_exit import STATE_KEY
    now=time.time()
    p=dict(side=side,entry_mode='CHANNEL_SWING',entry_price=100.,amount=1.,quantity=1.,open_timestamp=now-120,entry_atr=1.)
    a=SimpleNamespace(positions={'CAP/USDT':p},position_meta={},save_state=Mock(),log=Mock(),close_position=AsyncMock())
    e=object.__new__(TradingEngine);e.account=a;e.is_running=True;e._channel_exit_frames={}
    def decide(self,position,*args):
        position[STATE_KEY].update(peak_price=103.,peak_net_pnl=2.)
        return dict(type='EXIT_REALTIME_PEAK_TRAILING',trigger='EXIT_PEAK_PULLBACK_PRESSURE')
    monkeypatch.setattr(exits.PureTrendStrategyV2,'evaluate_anti_whipsaw_profit_lock',decide)
    monkeypatch.setattr(exits,'enforce_hard_stop',AsyncMock(return_value=False))
    monkeypatch.setattr('core.services.exits.trend_hold_evaluator.evaluate_trend_hold',lambda *a:(trend,'TEST'))
    assert asyncio.run(enforce_realtime_profit_exit(e,'CAP/USDT',102.,now*1000))
    a.close_position.assert_awaited_once()

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('peak',[0.5,0.99,1.0])
def test_atr_protection_arms_at_one_atr(side,peak):
    from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=1.,open_timestamp=60.,entry_atr=1.,entry_mode='CHANNEL_SWING')
    assert evaluate_peak_trailing(p,100.+sign*peak,61000,1.,fee=0.,slippage=0.) is None
    d=evaluate_peak_trailing(p,100.+sign*(peak-.61),61001,1.,fee=0.,slippage=0.)
    if peak<1.:assert d is None
    else:assert d and d['trigger']=='EXIT_PEAK_PULLBACK_PRESSURE'

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['flat','near_flat','opposite','invalid'])
def test_all_breakouts_require_directional_ma5(side,fault):
    f=frame(side);q=float(f.iloc[-1].close)
    live=(sum(float(v) for v in f.close.iloc[-5:-1])+q)/5.
    sign=1 if side=='LONG' else -1
    offsets={'flat':0.,'near_flat':-.005*sign,'opposite':.1*sign,'invalid':math.nan}
    f.loc[4,'ma5']=live+offsets[fault]
    diagnostics={}
    assert live_body_breakout_side(f,q)==side
    assert evaluate_entry_contract(f,q,symbol='CAP/USDT',diagnostics=diagnostics) is None
    assert diagnostics['reason']=='BLOCKED_MA5_FLAT_OPPOSITE_OR_INVALID'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_ma5_direction_boundary(side):
    from core.services.entry_contract import ma5_entry_ready
    f=frame(side);q=float(f.iloc[-1].close);sign=1 if side=='LONG' else -1
    live=(sum(float(v) for v in f.close.iloc[-5:-1])+q)/5.
    f.loc[4,'ma5']=live-sign*.05
    assert ma5_entry_ready(f,q,side)

def test_lobster_nearly_flat_short_from_actual_fill_is_blocked():
    from core.services.entry_contract import ma5_entry_ready
    f=frame('SHORT')
    f.loc[0:4,'close']=[.0433,.04373,.04336,.04361,.04355]
    f.loc[4,'ma5']=(.0433+.04373+.04336+.04361+.04355)/5.
    f.loc[4,'atr']=.000599
    assert not ma5_entry_ready(f,.04327,'SHORT')

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['approaching','parallel','wrong_side','invalid'])
def test_ma5_cannot_return_toward_kc(side,fault):
    from core.services.entry_contract import ma5_kc_trend_ready,ma5_entry_ready
    f=frame(side);q=float(f.iloc[-1].close);sign=1 if side=='LONG' else -1
    current=(sum(float(v) for v in f.close.iloc[-5:-1])+q)/5.
    previous=float(f.iloc[-2].ma5);prior_middle=float(f.iloc[-2]['kc_upper' if side=='LONG' else 'kc_lower'])
    old_gap=sign*(previous-prior_middle)
    gaps={'approaching':old_gap-.1,'parallel':old_gap,'wrong_side':-.1,'invalid':math.nan}
    f.loc[5,'kc_upper' if side=='LONG' else 'kc_lower']=current-sign*gaps[fault]
    assert ma5_entry_ready(f,q,side)
    assert ma5_kc_trend_ready(f,q,side) is (fault=='parallel')

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['inside','approaching'])
def test_final_account_blocks_ma5_outer_violation(monkeypatch,side,fault):
    import asyncio,time
    from unittest.mock import AsyncMock
    from core.services.entry_firewall import validate_account_entry
    f=frame(side);now=int(time.time()//60)*60000;f['timestamp']+=now-360000
    monkeypatch.setattr(time,'time',lambda:now/1000+10.)
    f.attrs['entry_finality_verified']=True
    d=evaluate_entry_contract(f,symbol='CAP/USDT');assert d
    sign=1 if side=='LONG' else -1;key='kc_upper' if side=='LONG' else 'kc_lower'
    current=(sum(float(v) for v in f.close.iloc[-5:-1])+float(f.iloc[-1].close))/5.
    if fault=='inside':f.loc[5,key]=current+sign*.05
    else:f.loc[4,key]=float(f.iloc[-2].ma5)-sign*.5
    ctx=dict(entry_signal_code=d['type'],channel_confirmation_bar_id=d['confirmation_bar_id'])
    a=SimpleNamespace(positions={},trades=[],last_closed_at={},entry_frame_provider=AsyncMock(return_value=f))
    with pytest.raises(ValueError):asyncio.run(validate_account_entry(a,'CAP/USDT',side,ctx))
