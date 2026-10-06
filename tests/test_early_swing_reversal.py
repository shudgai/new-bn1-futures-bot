import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from test_lobster_cap_gates import frame
from core.services.early_swing_reversal import evaluate_early_swing,PHASE,EXIT
from core.services.entry_contract import evaluate_entry_contract
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing


def reversal_frame(side):
    f=frame('LONG')
    candles=[(100,100.2,99.4,99.6),(99.6,99.8,99.1,99.3),(99.7,100,99,99.5),(98.9,99,98,98.5),(98.5,99.2,98.2,99),(99,99.6,98.8,99.5)]
    for i,vals in enumerate(candles):f.loc[i,['open','high','low','close']]=vals
    f['kc_lower']=98.1;f['kc_middle']=99.;f['kc_upper']=100.1;f['atr']=1.
    if side=='SHORT':
        for k in ('open','close','ma3','ma5','ma15','kc_middle'):f[k]=200-f[k]
        for a,b in (('high','low'),('kc_upper','kc_lower')):
            x,y=f[a].copy(),f[b].copy();f[a]=200-y;f[b]=200-x
    return f

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_local_reversal_enters_inside_kc_before_ma_alignment(side):
    f=reversal_frame(side);d=evaluate_entry_contract(f,code=PHASE+'_'+side)
    assert d and d['entry_phase']==PHASE
    assert f.iloc[-1].kc_lower<d['price']<f.iloc[-1].kc_upper

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['unconfirmed','not_broken','too_late','no_run','violated_pivot','opposite','stale'])
def test_false_or_late_reversal_rejected(side,fault):
    f=reversal_frame(side);sign=1 if side=='LONG' else -1;key='low' if sign==1 else 'high'
    if fault=='unconfirmed':f.loc[4,key]=f.loc[3,key]
    if fault=='not_broken':f.loc[5,'close']=float(f.loc[4,'high' if sign==1 else 'low'])
    if fault=='too_late':f.loc[5,'close']+=sign*.6
    if fault=='no_run':f['atr']=3.
    if fault=='violated_pivot':f.loc[5,key]=f.loc[3,key]
    if fault=='opposite':f.loc[5,'open']=f.loc[5,'close']+sign*.1
    if fault=='stale':f.loc[5,'timestamp']+=60000
    assert evaluate_early_swing(f,float(f.iloc[-1].close)) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_exit_reversal_requires_postentry_pivot_and_prior_gain(side):
    opposite='SHORT' if side=='LONG' else 'LONG';f=reversal_frame(opposite)
    r=evaluate_early_swing(f,float(f.iloc[-1].close));sign=1 if side=='LONG' else -1
    price=float(f.iloc[-1].close);entry=price-sign*.2
    p=dict(side=side,entry_price=entry,qty=.1,entry_atr=1.,open_timestamp=60.,entry_mode='CHANNEL_SWING')
    s=dict(quote_ms=361000,closed_bar_ms=300000,live_bar_ms=360000,live_open=price,atr=1.,early_swing_reversal=r)
    assert evaluate_peak_trailing(p,entry+sign*.7,dict(quote_ms=61000,reason='NO_DATA'),fee=0,slippage=0) is None
    d=evaluate_peak_trailing(p,price,s,fee=0,slippage=0)
    assert d and d['reason']==EXIT
    assert evaluate_peak_trailing(p,price,dict(quote_ms=362000,reason='NO_DATA'),fee=0,slippage=0)['reason']==EXIT

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_successful_reversal_close_allows_one_same_bar_entry(side):
    f=reversal_frame(side);closed_side='SHORT' if side=='LONG' else 'LONG'
    trade=dict(symbol='X',id=360500,action='CLOSE_'+closed_side,status='CLOSED',reason='Channel Swing '+EXIT)
    a=SimpleNamespace(positions={},trades=[trade],last_closed_at={'X':360.5})
    d=evaluate_entry_contract(f,code=PHASE+'_'+side,account=a,symbol='X')
    assert d and d['same_bar_close_id']==360500
    a.trades.append(dict(symbol='X',id=360600,action='OPEN_'+side))
    assert evaluate_entry_contract(f,code=PHASE+'_'+side,account=a,symbol='X') is None

@pytest.mark.parametrize('reason',['EXIT_INITIAL_ATR_HARD_STOP','EXIT_MOVING_PROFIT_STOP'])
def test_other_closes_do_not_grant_same_bar_reversal(reason):
    f=reversal_frame('LONG');a=SimpleNamespace(positions={},trades=[dict(symbol='X',id=360500,action='CLOSE_SHORT',reason=reason)],last_closed_at={})
    assert evaluate_entry_contract(f,code=PHASE+'_LONG',account=a,symbol='X') is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_account_rechecks_reversal_confirmation(side,monkeypatch):
    from core.services.entry_firewall import validate_account_entry
    f=reversal_frame(side);monkeypatch.setattr('time.time',lambda:361.)
    f.attrs.update(entry_finality_verified=True,entry_finality_server_ms=361000.)
    a=SimpleNamespace(positions={},trades=[],last_closed_at={},position_meta={},entry_frame_provider=AsyncMock(return_value=f))
    ctx=dict(entry_signal_code=PHASE+'_'+side,channel_confirmation_bar_id=360000.)
    assert asyncio.run(validate_account_entry(a,'CAP/USDT',side,ctx))
    f.loc[5,'close']=f.loc[5,'open']
    with pytest.raises(ValueError):asyncio.run(validate_account_entry(a,'CAP/USDT',side,ctx))

@pytest.mark.parametrize('fault',['no_gain','preentry_pivot','wrong_side'])
def test_early_exit_does_not_use_unearned_or_preentry_reversal(fault):
    f=reversal_frame('LONG');r=evaluate_early_swing(f,99.3)
    p=dict(side='SHORT',entry_price=99.5,qty=.1,entry_atr=1.,open_timestamp=60.,entry_mode='CHANNEL_SWING')
    s=dict(quote_ms=361000,closed_bar_ms=300000,live_bar_ms=360000,live_open=99.3,atr=1.,early_swing_reversal=r)
    if fault=='preentry_pivot':p['open_timestamp']=250.
    if fault=='wrong_side':r['side']='SHORT'
    if fault!='no_gain':evaluate_peak_trailing(p,98.8,dict(quote_ms=300000,reason='NO_DATA'),fee=0,slippage=0)
    assert evaluate_peak_trailing(p,99.3,s,fee=0,slippage=0) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_realtime_exit_closes_opposite_position(side,monkeypatch):
    from unittest.mock import Mock
    from core.services.exits import realtime_profit_exit as rt
    sign=1 if side=='LONG' else -1;entry=100.;price=entry+sign*.2
    p=dict(side=side,entry_price=entry,qty=.1,entry_atr=1.,open_timestamp=60.,entry_mode='CHANNEL_SWING')
    evaluate_peak_trailing(p,entry+sign*.7,dict(quote_ms=61000,reason='NO_DATA'),fee=0,slippage=0)
    r=dict(side='SHORT' if sign==1 else 'LONG',reversal_pivot_ms=240000.)
    s=dict(quote_ms=361000,closed_bar_ms=300000,live_bar_ms=360000,live_open=price,atr=1.,early_swing_reversal=r)
    a=SimpleNamespace(positions={'X':p},position_meta={},save_state=Mock(),log=Mock(),close_position=AsyncMock(return_value=True))
    e=SimpleNamespace(account=a,is_running=True,_channel_exit_frames={})
    monkeypatch.setattr(rt.time,'time',lambda:361.)
    monkeypatch.setattr(rt,'cached_tick_indicators',lambda *args:(s,1.))
    monkeypatch.setattr(rt,'enforce_hard_stop',AsyncMock(return_value=False))
    assert asyncio.run(rt.enforce_realtime_profit_exit(e,'X',price,361000.))
    assert EXIT in a.close_position.await_args.args[2]
