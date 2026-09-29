import copy
import pandas as pd
import pytest
from core.services.exit_service import exhaustion_exit_reason
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
from test_peak_trough_cross import frame


def case(side='LONG', kind='pin'):
    f=frame('LONG')
    f.loc[6,['open','close','high','low']]=[101.,102.,102.1,100.9]
    f.loc[7,['open','close','high','low']]=[102.,104.,104.1,101.9]
    f.loc[8,['open','close','high','low']]=[103.,103.5,104.25,102.9]
    f['ma15']=102.;f['ma3']=103.
    if kind=='reversal':f.loc[8,['open','close','high','low']]=[104.,102.8,104.1,102.7]
    if side=='SHORT':
        old=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),('kc_lower','kc_upper')]:f[a]=200-old[b]
    p=dict(side=side,entry_price=100.,entry_atr=2.,open_timestamp=1.,sl=95. if side=='LONG' else 105.)
    return f,p


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('kind,expected',[('pin','PIN'),('reversal','REVERSAL')])
def test_closed_exhaustion_dispatch_and_pending(side,kind,expected):
    f,p=case(side,kind);price=float(f.iloc[-1].close)
    reason=f'EXIT_EXHAUSTION_{expected}_CLOSED'
    assert exhaustion_exit_reason(p,f,price)==reason
    assert DualTrackExitStrategy().evaluate_exit(p,f,current_price=price)==reason
    assert DualTrackExitStrategy().evaluate_exit(copy.deepcopy(p),None,current_price=price)==reason


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['profit','quote_profit','no_new_extreme','wick_short','no_rail','pre_entry'])
def test_pin_requires_profit_location_and_pattern(side,fault):
    f,p=case(side);sign=1 if side=='LONG' else -1;price=float(f.iloc[-1].close)
    if fault=='profit':p['entry_atr']=4.
    elif fault=='quote_profit':price=100+sign*2.39
    elif fault=='no_new_extreme':f.loc[7,'high' if side=='LONG' else 'low']=100+sign*5
    elif fault=='wick_short':f.loc[8,'high' if side=='LONG' else 'low']=100+sign*4.2
    elif fault=='no_rail':f.loc[8,'kc_upper' if side=='LONG' else 'kc_lower']=100+sign*5
    elif fault=='pre_entry':p['open_timestamp']=(float(f.iloc[-1].timestamp)+1)/1000
    assert exhaustion_exit_reason(p,f,price) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_midpoint_touch_not_dark_cloud(side):
    f,p=case(side,'reversal');sign=1 if side=='LONG' else -1
    f.loc[8,'close']=100+sign*3.
    assert exhaustion_exit_reason(p,f,float(f.iloc[-1].close)) is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_live_tail_cannot_trigger_exhaustion(side):
    f,p=case(side);f.loc[8,'is_closed']=False
    assert DualTrackExitStrategy().evaluate_exit(p,f,current_price=float(f.iloc[-1].close)) is None


def test_short_trailing_is_monotone_and_close_only():
    from core.services.exits.dual_track_exit_service import trailing_structure_exit
    f,p=case('SHORT');p['swing_trailing_armed']=True
    assert trailing_structure_exit(p,f) is None
    first=p['swing_trailing_line']
    row=f.iloc[-1].copy();row.timestamp+=60000;row['close']=first+.1
    f=pd.concat([f,pd.DataFrame([row])],ignore_index=True)
    assert trailing_structure_exit(p,f)=='EXIT_TWO_BAR_HIGH_TRAILING_CLOSED'
    assert p['swing_trailing_line']<=first
