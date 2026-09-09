import pandas as pd
import pytest
from core.trend_pullback import evaluate, confirmed_swings


def entry_frame(side='LONG'):
    f=pd.DataFrame(dict(timestamp=[i*300000 for i in range(30)],open=[100.]*30,
        high=[101.]*30,low=[99.]*30,close=[100.]*30,
        ma15=[99.5+i*.02 for i in range(30)],ema_20=[99.4+i*.02 for i in range(30)],
        atr=[1.]*30,volume=[1000.]*30,kc_upper=[102.]*30,kc_lower=[98.]*30,ma3=[100.]*30))
    f.loc[10,'low']=97.
    f.loc[20,'high']=104.
    f.loc[27,['open','high','low','close']]=[100.4,100.6,99.6,99.9]
    f.loc[28,['open','high','low','close']]=[100.,101.,99.8,100.8]
    f.loc[29,['open','high','low','close']]=[100.8,100.8,100.8,100.8]
    if side=='SHORT':
        hi,lo=f.high.copy(),f.low.copy()
        for col in ['open','close','ma15','ema_20','ma3']: f[col]=200-f[col]
        f['high'],f['low']=200-lo,200-hi
    return f

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_confirmed_pullback_enters_with_absolute_structure_stop(side):
    f=entry_frame(side)
    decision=evaluate(f,float(f.iloc[-1].open))
    assert decision['action']=='ENTER',decision
    assert decision['side']==side
    assert decision['stop_loss']==pytest.approx(99.35 if side=='LONG' else 100.65)
    assert decision['net_rr']>=1.5

@pytest.mark.parametrize('invalid',['wrong_trend','broken_structure','no_confirmation','expensive','wide_stop'])
def test_rejects_invalid_entry(invalid):
    f=entry_frame()
    kwargs={}
    if invalid=='wrong_trend': f['ema_20']=list(reversed(f.ema_20.tolist()))
    elif invalid=='broken_structure': f.loc[27,'low']=96.
    elif invalid=='no_confirmation': f.loc[28,'close']=100.5
    elif invalid=='expensive': kwargs['fee_rate']=.01
    else: f.loc[27,'low']=97.1
    assert evaluate(f,100.8,**kwargs)['action']=='WAIT'

@pytest.mark.parametrize('side,stop,price',[('LONG',99.,98.),('SHORT',101.,102.)])
def test_stop_works_without_candles_and_never_reverses(side,stop,price):
    decision=evaluate(None,price,dict(side=side,sl=stop))
    assert decision==dict(action='EXIT',side=None,reason='PULLBACK_STRUCTURE_STOP',stop_loss=stop)


def test_live_bar_cannot_create_or_cancel_entry():
    f=entry_frame()
    expected=evaluate(f,100.8)
    f.loc[29,['high','low','close','ma15','ema_20']]=[200.,1.,150.,200.,1.]
    assert evaluate(f,100.8)==expected


def test_pivots_need_two_right_closed_candles():
    f=entry_frame().iloc[:11]
    assert not any(kind=='LOW' and level==97. for _,kind,level in confirmed_swings(f))
    f=entry_frame().iloc[:13]
    assert any(kind=='LOW' and level==97. for _,kind,level in confirmed_swings(f))


def test_stop_does_not_loosen_or_use_pre_entry_pivots():
    f=entry_frame()
    decision=evaluate(f,100.8,dict(side='LONG',sl=99.,open_timestamp=8000.))
    assert decision['action']=='HOLD'
    assert decision['stop_loss']==99.
