"""Third-candle original-open chase limits, independent of elapsed seconds."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from test_strict_entry_contract import candles, context


def near_open(side='LONG', distance=.05):
    f=candles(side); sign=1 if side=='LONG' else -1
    opening=101.75 if side=='LONG' else 98.25
    price=opening+sign*distance
    f.loc[f.index[-1], ['open','close','high','low']]=[
        opening,price,max(opening,price)+.005,min(opening,price)-.005]
    return f


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('distance',[.099,.1,.101,.5])
@pytest.mark.parametrize('scale',[1.,.00001])
def test_third_open_chase_boundary(side,distance,scale):
    f=near_open(side,distance)
    keys=['open','high','low','close','ma3','ma15','atr','kc_upper','kc_middle','kc_lower']
    f[keys]*=scale
    diag={};result=evaluate_entry_contract(f,diagnostics=diag)
    assert bool(result)==(distance<=.1)
    if distance>.1:
        assert diag['reason']=='BLOCKED_THIRD_OPEN_CHASE'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_no_seconds_cutoff_and_live_atr_cannot_loosen_limit(side):
    f=near_open(side,.11)
    f.loc[f.index[-1],'atr']=100.
    f.attrs['entry_finality_server_ms']=float(f.iloc[-1].timestamp)+59000
    assert evaluate_entry_contract(f) is None
    f=near_open(side,.05)
    f.attrs['entry_finality_server_ms']=float(f.iloc[-1].timestamp)+59000
    assert evaluate_entry_contract(f)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_favorable_price_is_not_chasing(side):
    f=near_open(side,-.2)
    assert evaluate_entry_contract(f)


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_requote_outside_limit_then_recovery_has_no_lock(side):
    f=near_open(side,.05);sign=1 if side=='LONG' else -1
    assert evaluate_entry_contract(f)
    assert evaluate_entry_contract(f,float(f.iloc[-1].open)+sign*.11) is None
    assert evaluate_entry_contract(f,float(f.iloc[-1].close))


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('post_exit',[False,True])
def test_continuation_keeps_original_third_open_and_second_atr(side,post_exit):
    f=near_open(side,.05);sign=1 if side=='LONG' else -1
    original_open=float(f.iloc[-1].open)
    f.loc[f.index[-1],'is_closed']=True
    extra=f.iloc[-1].copy();extra['timestamp']+=60000;extra['is_closed']=False
    extra['open']=original_open+sign*.2;extra['close']=original_open+sign*.25
    extra['high']=max(extra['open'],extra['close'])+.005
    extra['low']=min(extra['open'],extra['close'])-.005
    f.loc[len(f)]=extra
    f.loc[f.index[-2],'atr']=100.  # Later ATR cannot increase the original cap.
    account=SimpleNamespace(positions={},trades=[])
    if post_exit:
        account.trades=[dict(symbol='TEST',action='CLOSE_'+side,id=float(f.iloc[-2].timestamp)+1000)]
    diag={}
    assert evaluate_entry_contract(f,account=account,symbol='TEST',diagnostics=diag) is None
    assert diag['reason']=='BLOCKED_THIRD_OPEN_CHASE'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_account_rejects_price_chase_after_initial_signal(side):
    f=near_open(side,.05);ctx=context(f,side)
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=near_open(side,.2)))
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(account,'TEST',side,ctx))


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_exchange_boundary_never_submits_a_chased_quote(monkeypatch,side):
    from core.testnet_account import BinanceTestnetAccount
    monkeypatch.setattr(BinanceTestnetAccount,'_load_state',lambda self:None)
    monkeypatch.setattr(BinanceTestnetAccount,'save_state',lambda self,**kwargs:None)
    raw=AsyncMock()
    account=BinanceTestnetAccount(SimpleNamespace(create_order=raw))
    ctx=context(near_open(side,.05),side)
    account.entry_frame_provider=AsyncMock(return_value=near_open(side,.2))
    with pytest.raises(ValueError,match='BLOCKED_THIRD_OPEN_CHASE'):
        asyncio.run(account._send_order('TEST','market','buy' if side=='LONG' else 'sell',1,
                                       entry_context=ctx))
    raw.assert_not_awaited()


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_final_account_evidence_and_no_opening_seconds_deadline(monkeypatch,side):
    f=near_open(side,.05);ctx=context(f,side)
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f))
    monkeypatch.setattr('core.services.entry_firewall.time.time',
                        lambda:float(f.iloc[-1].timestamp)/1000+59.)
    decision=asyncio.run(validate_account_entry(account,'TEST',side,ctx))
    saved=ctx['entry_snapshot']
    assert saved['third_open']==float(f.iloc[-1].open)
    assert saved['third_bar_id']==float(f.iloc[-1].timestamp)
    assert saved['third_reference_atr']==float(f.iloc[-2].atr)
    assert saved['max_chase_atr']==.1
    assert saved['chase_atr']==pytest.approx(.05)
    assert decision
