"""Doji remains blocked while non-doji long wicks remain admissible."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from test_entry_without_wick_filter import frame_for


def set_body_ratio(f,index,ratio):
    row=f.iloc[index];body=abs(row.close-row.open)
    shadow=(body/ratio-body)/2
    f.loc[f.index[index],'high']=max(row.open,row.close)+shadow
    f.loc[f.index[index],'low']=min(row.open,row.close)-shadow
    return f


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('index',[2,3,4])
@pytest.mark.parametrize('ratio',[.099,.10,.101,.25])
def test_doji_boundary_all_confirmation_and_live_candles(side,index,ratio):
    f=set_body_ratio(frame_for(side),index,ratio)
    result=evaluate_entry_contract(f)
    assert bool(result)==(index != 4 or ratio>=.10)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_fresh_quote_changes_live_candle_into_doji(side):
    f=frame_for(side)
    assert evaluate_entry_contract(f)
    diag={}
    assert evaluate_entry_contract(f,float(f.iloc[-1].open),diagnostics=diag) is None
    assert diag['reason']=='BLOCKED_LIVE_DOJI'
    assert evaluate_entry_contract(f,float(f.iloc[-1].close))


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_account_and_exchange_reject_new_doji_after_signal(monkeypatch,side):
    from core.testnet_account import BinanceTestnetAccount
    f=frame_for(side);d=evaluate_entry_contract(f)
    context=dict(entry_mode='CHANNEL_SWING',entry_signal_code=d['type'],channel_confirmation_bar_id=d['confirmation_bar_id'])
    set_body_ratio(f,4,.09)
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f))
    with pytest.raises(ValueError,match='BLOCKED_LIVE_DOJI'):
        asyncio.run(validate_account_entry(account,'TEST',side,context))
    monkeypatch.setattr(BinanceTestnetAccount,'_load_state',lambda self:None)
    monkeypatch.setattr(BinanceTestnetAccount,'save_state',lambda self,**kwargs:None)
    raw=AsyncMock();account=BinanceTestnetAccount(SimpleNamespace(create_order=raw))
    account.entry_frame_provider=AsyncMock(return_value=f)
    with pytest.raises(ValueError,match='BLOCKED_LIVE_DOJI'):
        asyncio.run(account._send_order('TEST','market','buy' if side=='LONG' else 'sell',1,entry_context=context))
    raw.assert_not_awaited()


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_compatibility_doji_never_bypasses_shared_contract(side):
    from core.services.strategies.strict_state_machine import StrictStateMachineStrategy
    f=frame_for(side);sign=1 if side=='LONG' else -1
    f.loc[f.index[-1],'close']=float(f.iloc[-1].open)+sign*.5
    f.loc[f.index[-1],'ma5']=102. if side=='LONG' else 98.
    f.loc[f.index[-1],'kc_middle']=100.+sign*.1
    set_body_ratio(f,4,.09)
    assert StrictStateMachineStrategy().evaluate_tick('TEST',f,float(f.iloc[-1].close))['action']=='WAIT'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_chase_rejection_keeps_specific_diagnostic(side):
    f=frame_for(side);sign=1 if side=='LONG' else -1
    diag={}
    assert evaluate_entry_contract(f,float(f.iloc[-1].open)+sign*.2,diagnostics=diag) is None
    assert diag['reason']=='BLOCKED_OPEN_CHASE'
