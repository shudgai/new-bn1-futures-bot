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
    q=float(f.iloc[-1].open) if fault=='retreat' else float(f.iloc[-1].close)
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
    sign=1 if side=='LONG' else -1
    f.loc[5,'open']=float(f.iloc[-1].close)-sign*.3
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

def test_account_open_lock_serializes_and_blocks_unclosed_position(monkeypatch):
    import asyncio
    from core.paper_account import PaperAccount
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    a=PaperAccount();calls=[]
    async def fill(self,symbol,*args):
        calls.append(symbol)
        await asyncio.sleep(0)
        self.positions[symbol]={'side':'LONG'}
        return True
    monkeypatch.setattr(PaperAccount,'_open_position_locked',fill)
    async def run():
        args=('CAP/USDT','LONG',100.,50.,90.,0.,'TEST')
        results=await asyncio.gather(a.open_position(*args),a.open_position(*args))
        assert sorted(results)==[False,True] and len(calls)==1
        assert not await a.open_position('CAP/USDT','SHORT',100.,50.,110.,0.,'TEST')
        a.positions.clear()
        assert await a.open_position(*args)
    asyncio.run(run())

def channel_turn_frame(side='SHORT'):
    f=frame('LONG')
    f['kc_upper']=102.;f['kc_middle']=100.5;f['kc_lower']=99.;f['ma15']=100.5
    f.loc[4,['open','close','high','low','ma5']]=[101.,101.2,102.2,100.9,101.2]
    f.loc[5,['open','close','high','low']]=[101.2,100.2,101.2,100.2]
    if side=='LONG':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),('ma3','ma3'),('ma5','ma5'),('ma15','ma15'),('kc_upper','kc_lower'),('kc_lower','kc_upper'),('kc_middle','kc_middle')]:f[a]=200.-original[b]
    f.attrs['entry_finality_verified']=True
    return f

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_channel_turn_inside_channel_shared_gate(side):
    f=channel_turn_frame(side);q=float(f.iloc[-1].close)
    assert float(f.iloc[-1].kc_lower)<q<float(f.iloc[-1].kc_upper)
    d=evaluate_entry_contract(f,q,symbol='CAP/USDT')
    assert d and d['type']=='KC_CHANNEL_TURN_'+side

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['small_body','wrong_ma5','no_anchor','recovered_quote'])
def test_channel_turn_rejects_invalid_confirmation(side,fault):
    f=channel_turn_frame(side);sign=1 if side=='LONG' else -1
    if fault=='small_body':f.loc[5,'close']=float(f.iloc[-1].open)+sign*.1
    if fault=='recovered_quote':f.loc[5,'close']=float(f.iloc[-1].open)
    if fault=='wrong_ma5':f.loc[4,'ma5']=float(f.iloc[-1].close)+sign*5
    if fault=='no_anchor':
        f['high']=f[['open','close']].max(axis=1)+.01;f['low']=f[['open','close']].min(axis=1)-.01
    assert evaluate_entry_contract(f,float(f.iloc[-1].close),code='KC_CHANNEL_TURN_'+side,symbol='CAP/USDT') is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('close_succeeds',[True,False])
def test_channel_turn_closes_before_reopening(monkeypatch,side,close_succeeds):
    import asyncio
    from unittest.mock import AsyncMock
    import core.services.exits.realtime_profit_exit as realtime
    monkeypatch.setattr(realtime, 'cached_tick_indicators', lambda *args: ({'swing_structure_long': {'intact': False}, 'swing_structure_short': {'intact': False}}, 1.))
    f=channel_turn_frame(side)
    p={'side':'SHORT' if side=='LONG' else 'LONG','entry_mode':'CHANNEL_SWING'}
    a=SimpleNamespace(positions={'CAP/USDT':p})
    events=[]
    async def close(*args,**kwargs):
        events.append('CLOSE')
        if close_succeeds:a.positions.clear()
        return close_succeeds
    async def reopen(*args):
        assert not a.positions;events.append('REOPEN')
    a.close_position=AsyncMock(side_effect=close)
    e=object.__new__(TradingEngine);e.account=a;e.is_running=True
    e._entry_boundary_frame=AsyncMock(return_value=f);e._reevaluate_after_close=AsyncMock(side_effect=reopen)
    assert asyncio.run(e._try_channel_turn_reverse('CAP/USDT',f,float(f.iloc[-1].close)))==close_succeeds
    assert events==(['CLOSE','REOPEN'] if close_succeeds else ['CLOSE'])

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_channel_turn_real_paper_close_then_reverse(monkeypatch,side):
    import core.services.exits.trend_hold_evaluator as trend
    monkeypatch.setattr(trend, 'confirmed_swing_structure', lambda *args: {'intact': False})
    import asyncio,time
    from unittest.mock import AsyncMock
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'log',lambda *a,**k:None)
    now=int(time.time()//60)*60000;clock={'value':(now-120000)/1000}
    monkeypatch.setattr(time,'time',lambda:clock['value'])
    a=PaperAccount();a.balance=150.
    old_side='SHORT' if side=='LONG' else 'LONG'
    assert asyncio.run(a.open_position('CAP/USDT',old_side,100.,50.,0.,0.,'MANUAL',atr=1.,leverage=2,entry_context={'is_manual':True}))
    clock['value']=(now+10000)/1000
    f=channel_turn_frame(side);f['timestamp']+=now-360000;f.attrs['entry_finality_verified']=True
    e=object.__new__(TradingEngine);e.account=a;e.is_running=True
    e.exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=now+10000))
    e.fetch_klines=AsyncMock(return_value=f);e.strategy=SimpleNamespace(compute_indicators=lambda x:x)
    e.tickers={'CAP/USDT':float(f.iloc[-1].close)}
    e.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *a:2)
    e._execution_price_is_safe=AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(e,'CAP/USDT',clock['value'],None,False,exit_frame=f))
    assert a.positions['CAP/USDT']['side']==side
    assert [t['action'] for t in reversed(a.trades)]==['OPEN_'+old_side,'CLOSE_'+old_side,'OPEN_'+side]
    assert a.trades[0]['entry_snapshot']['signal_code']=='KC_CHANNEL_TURN_'+side

@pytest.mark.parametrize('fault',['missing_finality','signal_retreat','stopped'])
def test_turn_reverse_latest_snapshot_rejects_before_close(fault):
    import asyncio
    from unittest.mock import AsyncMock
    f=channel_turn_frame('SHORT');fresh=f.copy()
    if fault=='missing_finality':fresh.attrs.clear()
    if fault=='signal_retreat':fresh.loc[5,'close']=fresh.iloc[-1].open
    a=SimpleNamespace(positions={'CAP/USDT':{'side':'LONG','entry_mode':'CHANNEL_SWING'}},close_position=AsyncMock())
    e=object.__new__(TradingEngine);e.account=a;e.is_running=fault!='stopped'
    e._entry_boundary_frame=AsyncMock(return_value=fresh);e._reevaluate_after_close=AsyncMock()
    assert not asyncio.run(e._try_channel_turn_reverse('CAP/USDT',f,float(f.iloc[-1].close)))
    a.close_position.assert_not_awaited();e._reevaluate_after_close.assert_not_awaited()

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_first_live_breakout_allows_ma5_inside_outer_rail(side):
    f=frame(side);sign=1 if side=='LONG' else -1
    f.loc[5,'kc_upper' if side=='LONG' else 'kc_lower']=100.+sign*1.4
    q=float(f.iloc[-1].close)
    d=evaluate_entry_contract(f,q,symbol='CAP/USDT')
    assert d and d['type']=='KC_LIVE_BODY_BREAKOUT_'+side
    # Without a fresh breakout body, continuation still requires MA5 outside.
    f.loc[5,'open']=q-sign*.3
    assert evaluate_continuation_entry(f,q,symbol='CAP/USDT') is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_opposite_small_candle_then_second_outside_body_can_continue(side):
    f=frame(side);sign=1 if side=='LONG' else -1
    f.loc[4,'open']=float(f.iloc[-2].close)+sign*.1
    f.loc[5,'open']=float(f.iloc[-1].close)-sign*.3
    d=evaluate_entry_contract(f,symbol='CAP/USDT')
    assert d and d['type']=='KC_OUTSIDE_'+side
    assert d['confirmation_bar_id']==float(f.iloc[-1].timestamp)

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['wrong_side','touch','invalid'])
def test_channel_turn_needs_ma15_price_confirmation(side,fault):
    from core.services.entry_contract import evaluate_channel_turn
    f=channel_turn_frame(side);q=float(f.iloc[-1].close);sign=1 if side=='LONG' else -1
    f.loc[5,'ma15']={'wrong_side':q+sign*.1,'touch':q,'invalid':math.nan}[fault]
    assert evaluate_channel_turn(f,q,symbol='CAP/USDT') is None

@pytest.mark.parametrize('case,expected',[('lobster',True),('cap',False)])
def test_reported_turns_ma15_confirmation(case,expected):
    from core.services.entry_contract import evaluate_channel_turn
    closes=([.04523,.04538,.04576,.04556,.04545] if case=='lobster' else [.06952,.06947,.06926,.0697,.06958])
    q=.04514 if case=='lobster' else .06941
    atr=.00036 if case=='lobster' else .000284
    f=channel_turn_frame('SHORT')
    for i,c in enumerate(closes):
        f.loc[i,['open','close','high','low']]=[c-.00001,c,c+.00001,c-.00002]
    f.loc[4,'ma5']=sum(closes)/5.
    f['kc_upper']=max(closes)+.0001;f['kc_lower']=q-.0002;f['kc_middle']=(max(closes)+q)/2.
    f.loc[2 if case=='lobster' else 3,'high']=float(f.iloc[2 if case=='lobster' else 3].kc_upper)
    for i in ([3,4] if case=='lobster' else [4]):f.loc[i,'open']=float(f.iloc[i].close)+.00001
    f.loc[4,'atr']=atr
    f.loc[5,['open','close','high','low']]=[q+.0003,q,q+.0003,q]
    f.loc[5,'ma15']=.04518933333333333 if case=='lobster' else .06922066666666667
    assert bool(evaluate_channel_turn(f,q,symbol='CAP/USDT')) is expected

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('peak,base',[(1.,.5),(2.,.4),(3.,.35)])
def test_strong_trend_pullback_is_one_and_half_times(side,peak,base):
    from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=1.,open_timestamp=60.,entry_atr=1.)
    snap=dict(quote_ms=61000,reason=None,ma5=100.+sign*.3,last_ma5=100.,ma15=100.+sign*.1,last_ma15=100.,kc_middle=100.,last_close=100.+sign*.1)
    assert evaluate_peak_trailing(p,100.+sign*peak,snap,fee=0.,slippage=0.) is None
    snap['quote_ms']=61001
    assert evaluate_peak_trailing(p,100.+sign*(peak-base-.01),snap,fee=0.,slippage=0.) is None
    assert p['strong_trend_pullback'] and p['atr_pullback_limit']==pytest.approx(base*1.5)
    snap['quote_ms']=61002
    d=evaluate_peak_trailing(p,100.+sign*(peak-base*1.5-.01),snap,fee=0.,slippage=0.)
    assert d and d['trigger']=='EXIT_PEAK_PULLBACK_PRESSURE'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_weakening_trend_restores_original_pullback(side):
    from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=1.,open_timestamp=60.,entry_atr=1.)
    strong=dict(quote_ms=61000,reason=None,ma5=100.+sign*.3,last_ma5=100.,ma15=100.+sign*.1,last_ma15=100.,kc_middle=100.,last_close=100.+sign*.1)
    assert evaluate_peak_trailing(p,100.+sign*2,strong,fee=0.,slippage=0.) is None
    weak=dict(strong,quote_ms=61001,ma5=100.)
    d=evaluate_peak_trailing(p,100.+sign*1.59,weak,fee=0.,slippage=0.)
    assert d and not p['strong_trend_pullback'] and p['atr_pullback_limit']==.4


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_strong_direction_survives_price_crossing_ma5(side):
    from core.services.exits.trend_hold_evaluator import strong_direction_held
    sign = 1 if side == 'LONG' else -1
    p = dict(side=side, entry_atr=1.)
    snap = dict(reason=None, ma5=100.+sign*.3, last_ma5=100., ma15=100.+sign*.1,
                last_ma15=100., kc_middle=100., last_close=100.+sign*.2)
    assert strong_direction_held(p, snap, 100.+sign*.2)
    assert not strong_direction_held(p, dict(snap, ma5=100.), 100.+sign*.2)
    assert not strong_direction_held(p, dict(snap, fallback_used=True), 100.+sign*.2)

@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_channel_turn_preserves_strong_existing_position(monkeypatch, side):
    import asyncio
    from unittest.mock import AsyncMock
    import core.services.exits.realtime_profit_exit as realtime
    f = channel_turn_frame(side)
    held = 'SHORT' if side == 'LONG' else 'LONG'
    sign = 1 if held == 'LONG' else -1
    price = float(f.iloc[-1].close)
    snapshot = dict(reason=None, ma5=price+sign*.1, last_ma5=price-sign*.1,
                    ma15=price-sign*.2, last_ma15=price-sign*.3,
                    kc_middle=price-sign*.5, last_close=price)
    monkeypatch.setattr(realtime, 'cached_tick_indicators', lambda *args: (snapshot, 1.))
    p = dict(side=held, entry_mode='CHANNEL_SWING', entry_atr=1.)
    a = SimpleNamespace(positions={'CAP/USDT':p}, close_position=AsyncMock())
    e = object.__new__(TradingEngine); e.account=a; e.is_running=True
    e._entry_boundary_frame=AsyncMock(return_value=f)
    e._reevaluate_after_close=AsyncMock()
    assert not asyncio.run(e._try_channel_turn_reverse('CAP/USDT',f,price))
    a.close_position.assert_not_called()


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_confirmed_swing_structure_holds_until_strict_break(side):
    from core.services.exits.trend_hold_evaluator import confirmed_swing_structure
    sign = 1 if side == 'LONG' else -1
    values = [100+sign*x for x in [2, 1, 2, 3]]
    frame = pd.DataFrame({'low': values, 'high': values})
    level = 100+sign
    assert confirmed_swing_structure({'side':side},frame,level)['intact']
    assert not confirmed_swing_structure({'side':side},frame,level-sign*.01)['intact']
    assert confirmed_swing_structure({'side':side},frame.iloc[:2],level) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('structure', [None, {'intact': True}])
def test_channel_turn_cannot_close_intact_or_unknown_structure(monkeypatch, side, structure):
    import asyncio
    from unittest.mock import AsyncMock
    import core.services.exits.realtime_profit_exit as realtime
    f = channel_turn_frame(side)
    held = 'SHORT' if side == 'LONG' else 'LONG'
    monkeypatch.setattr(realtime, 'cached_tick_indicators', lambda *args: ({'swing_structure_'+held.lower():structure},1.))
    a = SimpleNamespace(positions={'CAP/USDT':dict(side=held,entry_mode='CHANNEL_SWING')}, close_position=AsyncMock())
    e = object.__new__(TradingEngine); e.account=a; e.is_running=True
    e._entry_boundary_frame=AsyncMock(return_value=f)
    assert not asyncio.run(e._try_channel_turn_reverse('CAP/USDT', f, float(f.iloc[-1].close)))
    a.close_position.assert_not_called()
