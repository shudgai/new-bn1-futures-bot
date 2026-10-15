"""Closed peak/trough crosses and structural stops through account boundaries."""
import asyncio
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from core.services.strategies.unified_entry_strategy import evaluate_closed_entry
from core.services.entry_firewall import validate_entry_frame, validate_account_entry
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy


def frame(side='LONG'):
    stamp = int(time.time()//60)*60000-60000
    rows = [dict(timestamp=stamp-(8-i)*60000, open=100.,close=100.,high=100.3,low=99.7,
                 ma3=99.9,ma15=100.,atr=1.,kc_upper=101.,kc_middle=100.,kc_lower=99.,is_closed=True)
            for i in range(9)]
    rows[0]['low']=98.
    rows[-1].update(open=99.9,close=100.4,high=100.5,low=99.8,ma3=100.1)
    f=pd.DataFrame(rows)
    if side=='SHORT':
        old=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a]=200-old[b]
    return f


def context(f,side):
    return dict(entry_mode='CHANNEL_SWING',entry_signal_code=f'CLOSED_PEAK_TROUGH_CROSS_{side}',
                channel_confirmation_bar_id=float(f.iloc[-1].timestamp))




@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['no_touch','old_touch','same_side','equality','wrong_body','pin','live','invalid_history','gap'])
def test_invalid_cross_rejected(side,fault):
    f=frame(side);sign=1 if side=='LONG' else -1
    if fault=='no_touch':f.loc[0,'low' if side=='LONG' else 'high']=100-sign*.3
    elif fault=='old_touch':
        extra=f.iloc[:1].copy();extra['timestamp']-=60000
        f.loc[0,'low' if side=='LONG' else 'high']=100-sign*.3
        f=pd.concat([extra,f],ignore_index=True)
    elif fault=='same_side':f.loc[7,'ma3']=100+sign*.1
    elif fault=='equality':f.loc[8,'ma3']=100.
    elif fault=='wrong_body':f.loc[8,'open']=float(f.loc[8,'close'])+sign*.01
    elif fault=='pin':f.loc[8,'high' if side=='LONG' else 'low']=100+sign*3
    elif fault=='live':f.loc[8,'is_closed']=False
    elif fault=='invalid_history':f.loc[0,'low']=float('nan')
    elif fault=='gap':f.loc[0,'timestamp']-=60000
    assert not evaluate_closed_entry(f,side)[0]
    with pytest.raises(ValueError):validate_entry_frame(f,side,f'CLOSED_PEAK_TROUGH_CROSS_{side}')


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['long_wick','wrong_half'])
def test_cross_rejected_by_wick_and_half_checks(side,fault):
    f=frame(side);sign=1 if side=='LONG' else -1
    if fault=='long_wick':
        f.loc[8,'high' if side=='LONG' else 'low']=float(f.loc[8,'close'])+sign*10.0
    elif fault=='wrong_half':
        span = float(f.loc[8,'high']) - float(f.loc[8,'low'])
        if side=='LONG':
            f.loc[8,'close'] = float(f.loc[8,'low']) + span*0.4
            f.loc[8,'open'] = float(f.loc[8,'low']) + span*0.3
        else:
            f.loc[8,'close'] = float(f.loc[8,'low']) + span*0.6
            f.loc[8,'open'] = float(f.loc[8,'low']) + span*0.7
    assert not evaluate_closed_entry(f,side)[0]
    with pytest.raises(ValueError):validate_entry_frame(f,side,f'CLOSED_PEAK_TROUGH_CROSS_{side}')










@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_touch_on_cross_bar_alone_is_insufficient(side):
    f=frame(side)
    f.loc[0,'low' if side=='LONG' else 'high']=99.7 if side=='LONG' else 100.3
    f.loc[8,'low' if side=='LONG' else 'high']=99. if side=='LONG' else 101.
    assert not evaluate_closed_entry(f,side)[0]








