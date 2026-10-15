"""Final A-E specification: close-only decisions and physical execution gates."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pandas as pd
import pytest
from core.services.strategies.unified_entry_strategy import evaluate_closed_entry, confirmed
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy


def candles(rule='C', side='LONG'):
    rows=[dict(timestamp=(i+1)*60000,open=100.,close=100.1,high=100.3,low=99.8,
               ma3=100.2,ma15=100.,kc_upper=102.,kc_middle=100.,kc_lower=98.,atr=1.,
               volume=100.,is_closed=i<22) for i in range(23)]
    if rule=='A':
        rows[20].update(open=100.5,close=100.2,high=100.6,low=100.1)
        rows[21].update(open=100.,close=101.,high=101.1,low=99.9)
    elif rule=='B':
        rows[21].update(open=100.1,close=101.5,high=101.6,low=100.)
    elif rule=='C':
        rows[20].update(ma3=99.9)
        rows[21].update(open=100.4,close=100.6,high=100.7,low=100.3)
    elif rule=='D':
        rows[19].update(close=102.,high=102.1)
        rows[20].update(open=102.,close=102.2,high=102.3,low=101.9,ma3=102.,ma15=101.)
        rows[21].update(open=102.2,close=102.4,high=102.5,low=102.1,ma3=102.,ma15=101.)
    elif rule=='E':
        for i in range(22):
            close=103.+i*.1
            rows[i].update(open=close-.6,close=close,high=close+.1,low=close-.7)
        rows[21].update(open=105.2,close=106.2,high=106.3,low=105.1)
    f=pd.DataFrame(rows)
    if side=='SHORT':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('ma3','ma3'),('ma15','ma15'),('kc_upper','kc_lower'),('kc_lower','kc_upper'),('kc_middle','kc_middle')]:
            f[a]=200-original[b]
    f.attrs['timeframe_ms']=60000
    return f




@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['unclosed','invalid','gap','color','bad_timeframe'])
def test_physical_failures(side,fault):
    f=candles('A',side)
    if fault=='unclosed': f.loc[21,'is_closed']=False
    if fault=='invalid': f.loc[21,'close']=float('nan')
    if fault=='gap': f.loc[20,'timestamp']-=1
    if fault=='color': f.loc[21,'close']=f.loc[21,'open']+(-.1 if side=='LONG' else .1)
    if fault=='bad_timeframe': f.attrs['timeframe_ms']=300000
    assert not evaluate_closed_entry(f,side)[0]




@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['small','wick','no_new_high','no_close'])
def test_E_never_allows_weak_late_chase(side,fault):
    f=candles('E',side);sign=1 if side=='LONG' else -1
    if fault=='small': f.loc[21,'open']=f.loc[21,'close']-sign*.1
    if fault=='wick': f.loc[21,'high' if side=='LONG' else 'low']=f.loc[21,'close']+sign*2.
    if fault=='no_new_high':
        f.loc[21,'close']-=sign*2.
        f.loc[21,'open']=f.loc[21,'close']-sign*1.
        f.loc[21,'low']=min(f.loc[21,'open'],f.loc[21,'close'])-.1
        f.loc[21,'high']=max(f.loc[21,'open'],f.loc[21,'close'])+.1
    assert not evaluate_closed_entry(f,side,after_close=fault!='no_close')[0]


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_C_requires_cross_instant_and_D_requires_c0_inside(side):
    f=candles('C',side); f.loc[20,'ma3']=f.loc[21,'ma3']
    assert not evaluate_closed_entry(f,side)[0]
    f=candles('D',side);sign=1 if side=='LONG' else -1
    f.loc[19,'close']+=sign*.1
    assert not evaluate_closed_entry(f,side,after_close=True)[0]


def position(side):
    return dict(side=side,entry_price=95. if side=='LONG' else 105.,qty=1.,open_timestamp=1.)








def engine_for(account,frame,quote):
    from core.engine import TradingEngine
    engine=object.__new__(TradingEngine)
    engine.account=account
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *a:1)
    engine._execution_price_is_safe=AsyncMock(return_value=True)
    engine.fetch_klines=AsyncMock(return_value=frame)
    engine.strategy=SimpleNamespace(compute_indicators=lambda f:f)
    engine.tickers={'DOGE/USDT':quote}
    return engine




