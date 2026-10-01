"""Wick length cannot veto an otherwise valid current three-bar entry."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry


def frame_for(side='LONG'):
    stamp=int(time.time()//60)*60000
    rows=[]
    for i,(o,c) in enumerate([(100.,100.2),(101.,101.5),(101.5,101.8),(101.8,102.1),(102.1,102.15)]):
        rows.append(dict(timestamp=stamp-(4-i)*60000,open=o,close=c,high=c+.01,low=o-.01,
                         atr=1.,kc_upper=101.4,kc_middle=100.,kc_lower=98.6,
                         ma3=101.,ma5=101.,ma15=100.,is_closed=i<4))
    f=pd.DataFrame(rows)
    if side=='SHORT':
        original=f.copy()
        for a,b in [('open','open'),('close','close'),('high','low'),('low','high'),
                    ('kc_upper','kc_lower'),('kc_lower','kc_upper'),('kc_middle','kc_middle'),
                    ('ma3','ma3'),('ma5','ma5'),('ma15','ma15')]:
            f[a]=200-original[b]
    f.attrs.update(timeframe_ms=60000,entry_finality_verified=True)
    return f


def add_wicks(f,index,shape):
    # Each wick may exceed the body, while total body ratio remains >25%.
    row=f.loc[index];body=abs(row.close-row.open)
    if shape in ('upper','both'): f.loc[index,'high']=max(row.open,row.close)+1.25*body
    if shape in ('lower','both'): f.loc[index,'low']=min(row.open,row.close)-1.25*body
    return f


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('index',[1,2,3,4])
@pytest.mark.parametrize('shape',['upper','lower','both'])
def test_all_signal_and_live_wicks_allowed(side,index,shape):
    f=frame_for(side);before=evaluate_entry_contract(f)
    result=evaluate_entry_contract(add_wicks(f,index,shape))
    assert result is not None
    assert result['side']==side
    assert result['confirmation_bar_id']==before['confirmation_bar_id']


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['zero_body','opposite','no_break','chase','invalid_ohlc','same_close_bar'])
def test_non_wick_guards_preserved(side,fault):
    f=add_wicks(frame_for(side),2,'both');sign=1 if side=='LONG' else -1
    account=SimpleNamespace(positions={},trades=[])
    if fault=='zero_body': f.loc[2,'close']=f.loc[2,'open']
    if fault=='opposite': f.loc[2,'open']=f.loc[2,'close']+sign*.1
    if fault=='no_break': f.loc[1,['open','close','high','low']]=[100.,100.1,100.2,99.9]
    if fault=='chase':
        price=float(f.iloc[-1].open)+sign*.2
        f.loc[4,'close']=price;f.loc[4,'high']=max(f.loc[4,'high'],price);f.loc[4,'low']=min(f.loc[4,'low'],price)
    if fault=='invalid_ohlc': f.loc[2,'high']=f.loc[2,'low']-1.
    if fault=='same_close_bar':
        account.trades=[dict(symbol='TEST',action='CLOSE_'+side,id=float(f.iloc[-1].timestamp)+1)]
    assert evaluate_entry_contract(f,account=account,symbol='TEST') is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_account_and_exchange_revalidation_accept_lengthened_wicks(monkeypatch,side):
    from core.testnet_account import BinanceTestnetAccount
    f=frame_for(side);decision=evaluate_entry_contract(f)
    ctx=dict(entry_mode='CHANNEL_SWING',entry_signal_code=decision['type'],
             channel_confirmation_bar_id=decision['confirmation_bar_id'])
    for index in (1,2,3,4): add_wicks(f,index,'both')
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f))
    assert asyncio.run(validate_account_entry(account,'TEST',side,ctx))['side']==side
    monkeypatch.setattr(BinanceTestnetAccount,'_load_state',lambda self:None)
    monkeypatch.setattr(BinanceTestnetAccount,'save_state',lambda self,**kwargs:None)
    raw=AsyncMock(return_value={'id':'fake'})
    exchange_account=BinanceTestnetAccount(SimpleNamespace(create_order=raw))
    exchange_account.entry_frame_provider=AsyncMock(return_value=f)
    asyncio.run(exchange_account._send_order('TEST','market','buy' if side=='LONG' else 'sell',1,entry_context=ctx))
    raw.assert_awaited_once()


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_scanner_still_reaches_order_boundary_with_long_wicks(side):
    from core.services.symbol_runner import process_single_symbol_runner
    f=frame_for(side)
    for index in (1,2,3,4): add_wicks(f,index,'both')
    engine=SimpleNamespace(account=SimpleNamespace(positions={},log=lambda *args:None),
                           tickers={'TEST':float(f.iloc[-1].close)},
                           _execute_confirmed_channel_break=AsyncMock())
    asyncio.run(process_single_symbol_runner(engine,'TEST',time.time(),None,False,exit_frame=f))
    engine._execute_confirmed_channel_break.assert_awaited_once()


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('body',[.2,.5])
def test_compatibility_entry_ignores_wicks_but_keeps_absolute_body_threshold(side,body):
    from core.services.strategies.strict_state_machine import StrictStateMachineStrategy
    f=frame_for(side).tail(3).copy().reset_index(drop=True)
    sign=1 if side=='LONG' else -1
    price=float(f.iloc[-1].open)+sign*body
    f.loc[2,'close']=price
    f.loc[2,'high']=max(f.loc[2,'open'],price)+1.25*body
    f.loc[2,'low']=min(f.loc[2,'open'],price)-1.25*body
    f.loc[2,'ma5']=102. if side=='LONG' else 98.
    f.loc[2,'kc_middle']=100.+sign*.1
    result=StrictStateMachineStrategy().evaluate_tick('TEST',f,price)
    assert result['action']==('ENTER_'+side if body>=.3 else 'WAIT')
