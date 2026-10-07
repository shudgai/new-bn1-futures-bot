import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from core.services.exits.confirmed_pivot_exit import confirmed_pivot_turn, REASON
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing


def sample(side):
    sign=1 if side=='LONG' else -1
    rows=[dict(timestamp=t,open=o,high=h,low=l,close=c) for t,(o,h,l,c) in zip(
        (120000.,180000.,240000.),[(100.5,101.5,100.4,101.2),(101.2,102.,101.,101.8),(101.7,101.8,101.1,101.4)])]
    if side=='SHORT':
        for row in rows:
            for k in ('open','high','low','close'):row[k]=200.-row[k]
            row['high'],row['low']=row['low'],row['high']
    p=dict(side=side,entry_mode='CHANNEL_SWING',entry_price=100.,qty=1.,margin=100.,
           leverage=1.,entry_atr=1.,initial_sl=100-sign*10.,open_timestamp=60.)
    q=100+sign*1.4
    s=dict(quote_ms=301000.,live_bar_ms=300000.,closed_bar_ms=240000.,
           live_open=q,atr=1.,pivot_exit_history=rows,kc_middle=100.,reason=None)
    return p,s,q,sign


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_pivot_exits_before_middle_without_entry_or_profit_lock(side):
    p,s,q,sign=sample(side)
    assert evaluate_peak_trailing(p,100+sign*2,dict(quote_ms=61000.,reason='NO_DATA'),fee=0,slippage=0) is None
    d=evaluate_peak_trailing(p,q,s,fee=0,slippage=0)
    assert d['reason']==REASON
    assert sign*(q-s['kc_middle'])>0
    assert p['peak_trailing_state']['confirmed_pivot_exit']['confirmation']=='CLOSED'
    assert 'profit_stop_price' not in p['peak_trailing_state']
    assert evaluate_peak_trailing(p,100+sign*2,dict(quote_ms=302000.,reason='NO_DATA'),fee=0,slippage=0)['reason']==REASON


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['no_pivot','tiny_body','doji','no_adverse_close',
    'quote_reclaimed','preentry','stale','gap','missing','invalid','insufficient_run'])
def test_small_noise_and_unconfirmed_or_invalid_pivots_cannot_close(side,fault):
    p,s,q,sign=sample(side);r=s['pivot_exit_history'][-1]
    state={'peak_price':100+sign*2}
    if fault=='no_pivot':s['pivot_exit_history'][1]['high' if sign==1 else 'low']=s['pivot_exit_history'][0]['high' if sign==1 else 'low']
    elif fault=='tiny_body':r['open']=r['close']+sign*.01
    elif fault=='doji':r['high']=104.;r['low']=96.
    elif fault=='no_adverse_close':r['close']=100+sign*1.8;r['open']=100+sign*1.95;r['high']=102.;r['low']=98.
    elif fault=='quote_reclaimed':q+=sign*.01
    elif fault=='preentry':p['open_timestamp']=181.
    elif fault=='stale':s['closed_bar_ms']=180000.
    elif fault=='gap':s['pivot_exit_history'][0]['timestamp']=60000.
    elif fault=='missing':s.pop('pivot_exit_history')
    elif fault=='invalid':p['entry_atr']=float('nan')
    else:state['peak_price']=100+sign*.4
    assert confirmed_pivot_turn(p,state,s,q,sign) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_real_account_closes_without_remote_entry_checks(side,monkeypatch):
    from core.paper_account import PaperAccount
    from core.services.exits import realtime_profit_exit as rt
    p,s,q,sign=sample(side)
    evaluate_peak_trailing(p,100+sign*2,dict(quote_ms=61000.,reason='NO_DATA'),fee=0,slippage=0)
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'log',lambda *args:None)
    a=PaperAccount();a.positions={'CAP/USDT':p};a.trades=[]
    provider=AsyncMock(side_effect=AssertionError('close cannot wait for entry'))
    e=SimpleNamespace(account=a,is_running=True,_channel_exit_frames={},_entry_boundary_frame=provider)
    monkeypatch.setattr(rt.time,'time',lambda:301.)
    monkeypatch.setattr(rt,'cached_tick_indicators',lambda *args:(s,1.))
    assert asyncio.run(rt.enforce_realtime_profit_exit(e,'CAP/USDT',q,301000.))
    assert 'CAP/USDT' not in a.positions
    assert a.trades[0]['reason']=='Channel Swing '+REASON
    assert a.trades[0]['exit_protection_snapshot']['confirmed_pivot_exit']['pivot_ms']==180000.
    provider.assert_not_awaited()


def test_lobster_closed_1401_valley_can_exit_1403_below_middle():
    # Recorded closed bars, evaluated only once the right neighbour has closed.
    rows=[dict(timestamp=t,open=o,high=h,low=l,close=c) for t,(o,h,l,c) in zip(
        (1791352800000.,1791352860000.,1791352920000.),
        [(.0638,.06436,.06365,.06414),(.06415,.06434,.06292,.06421),(.06419,.06514,.06409,.06482)])]
    p=dict(side='SHORT',entry_mode='CHANNEL_SWING',entry_price=.065353464,qty=1.,
           margin=1.,leverage=1.,entry_atr=.000843,initial_sl=.0666643,open_timestamp=1791352416.)
    s=dict(quote_ms=1791352980010.,live_bar_ms=1791352980000.,closed_bar_ms=1791352920000.,
           pivot_exit_history=rows,live_open=.06482,atr=.000843,kc_middle=.0654593144449,reason=None)
    evaluate_peak_trailing(p,.06292,dict(quote_ms=1791352919999.,reason='NO_DATA'))
    assert .06482<s['kc_middle']
    assert evaluate_peak_trailing(p,.06482,s)['reason']==REASON
    # Earlier than confirmation, the same bars never authorize a close.
    assert confirmed_pivot_turn(p,{'peak_price':.06292},{**s,'quote_ms':1791352979999.},.06482,-1) is None
