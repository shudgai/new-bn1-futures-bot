import math
from types import SimpleNamespace
import pandas as pd
import pytest
from core.engine import TradingEngine
from core.services.entry_contract import evaluate_continuation_entry, evaluate_entry_contract
from core.services.strategies.outer_strategy import live_body_breakout_side
from tools.deployment_preflight import evaluate


def frame(side='LONG'):
    rows = [dict(timestamp=(i+1)*60000, open=100., close=100.2,
                 high=100.4, low=99.8, atr=1., ma5=100., ma3=100.2,
                 ma15=100., kc_upper=101., kc_middle=100.+i*.01,
                 kc_lower=99., is_closed=True) for i in range(6)]
    rows[-1].update(open=100.8, close=101.5, high=101.5, is_closed=False)
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
def test_same_bar_close_cannot_reverse(side):
    f=frame(side);a=SimpleNamespace(trades=[dict(symbol='CAP/USDT',action='CLOSE_'+('SHORT' if side=='LONG' else 'LONG'),id=360001)],last_closed_at={})
    assert evaluate_entry_contract(f,float(f.iloc[-1].close),account=a,symbol='CAP/USDT') is None


def test_half_wallet_and_fee_cap():
    assert TradingEngine._half_wallet_entry_margin(100.,100.,5.)==50.
    assert TradingEngine._half_wallet_entry_margin(100.,20.,5.)<20.
    assert TradingEngine._half_wallet_entry_margin(math.nan,100.,5.)==0.


def test_preflight_requires_all_evidence_and_authorization():
    e=dict(commit='abc',deploy_authorized=True,**{key:'VERIFIED' for key in ('runtime_source','candidate_integrity','phase_files','tests','account_exposure')})
    assert evaluate(e,'abc',True)['DEPLOYMENT_PREFLIGHT_GATE']=='PASS'
    for key in ('runtime_source','candidate_integrity','phase_files','tests','account_exposure','deploy_authorized','commit'):
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
    assert d and d['trigger']=='EXIT_PEAK_PULLBACK_PRESSURE'

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_initial_stop_is_independent(side):
    from core.services.exits.peak_trailing_exit import evaluate_peak_trailing, HARD_REASON
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=1.,open_timestamp=60.,entry_atr=1.,margin=100.,entry_mode='CHANNEL_SWING',leverage=1.)
    d=evaluate_peak_trailing(p,100.-sign*1.6,61000,fee=0.,slippage=0.)
    assert d and d['type']==HARD_REASON
