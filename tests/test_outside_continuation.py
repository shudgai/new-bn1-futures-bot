"""A later outside candle can enter near its own open after a miss or exit."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.services.entry_contract import evaluate_entry_contract
from core.services.entry_firewall import validate_account_entry
from test_third_open_chase import near_open
from test_strict_entry_contract import context


def continuation(side='LONG', distance=.05):
    f=near_open(side,.05); sign=1 if side=='LONG' else -1
    f['timestamp']-=60000
    extra=f.iloc[-1].copy()
    f.loc[f.index[-1],'is_closed']=True
    extra['timestamp']+=60000
    extra['open']+=sign*.5
    extra['close']=extra['open']+sign*distance
    extra['high']=max(extra['open'],extra['close'])+.005
    extra['low']=min(extra['open'],extra['close'])-.005
    f.loc[len(f)]=extra
    return f


@pytest.fixture(autouse=True)
def no_network_wait(monkeypatch):
    monkeypatch.setattr('core.services.entry_finality.READ_INTERVAL_SECONDS',0.)


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('distance',[.099,.1,.101])
def test_continuation_chase_boundary_and_recovery(side,distance):
    f=continuation(side,distance);diag={}
    result=evaluate_entry_contract(f,diagnostics=diag)
    assert bool(result)==(distance<=.1)
    if result:
        assert result['chase_open']==float(f.iloc[-1].open)
        assert result['chase_reference_atr']==float(f.iloc[-2].atr)
    else:
        assert diag['reason']=='BLOCKED_CONTINUATION_OPEN_CHASE'
    assert evaluate_entry_contract(continuation(side,.05))


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['inside','closed_return','missing_pair','same_close_bar','long_wick','live_atr'])
def test_continuation_keeps_existing_guards(side,fault):
    f=continuation(side);sign=1 if side=='LONG' else -1
    account=SimpleNamespace(positions={},trades=[])
    assert evaluate_entry_contract(f)
    if fault=='inside': f.loc[f.index[-1],['open','close','high','low']]=[100.,100.,100.1,99.9]
    if fault=='closed_return':
        f.loc[f.index[-2],['open','close','high','low']]=[100.,100.,100.1,99.9]
    if fault=='missing_pair':
        f.loc[f.index[-4],'open']=float(f.iloc[-4].close)-sign*.01
    if fault=='same_close_bar':
        account.trades=[dict(symbol='TEST',action='CLOSE_'+side,id=float(f.iloc[-1].timestamp)+1000)]
    if fault=='long_wick':
        f.loc[f.index[-1],'high']=max(f.iloc[-1].open,f.iloc[-1].close)+.1
    if fault=='live_atr':
        f=continuation(side,.2);f.loc[f.index[-1],'atr']=100.
    assert evaluate_entry_contract(f,account=account,symbol='TEST') is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('symbol',['1000PEPE/USDT','龙虾/USDT'])
@pytest.mark.parametrize('post_exit',[False,True])
def test_later_outside_bar_reaches_real_paper_account(monkeypatch,side,symbol,post_exit):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    f=continuation(side)
    account=PaperAccount();account.balance=100.
    if post_exit:
        stamp=float(f.iloc[-2].timestamp)+1000
        account.trades=[dict(symbol=symbol,action='CLOSE_'+side,id=stamp)]
        account.last_closed_at[symbol]=stamp/1000
    engine=object.__new__(TradingEngine);engine.account=account
    engine.exchange=SimpleNamespace(fetch_time=AsyncMock(return_value=float(f.iloc[-1].timestamp)+10000))
    engine.fetch_klines=AsyncMock(return_value=f)
    engine.tickers={symbol:float(f.iloc[-1].close)}
    engine.strategy=SimpleNamespace(compute_indicators=lambda frame:frame)
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    engine._execution_price_is_safe=AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(engine,symbol,time.time(),None,False,exit_frame=f))
    saved=account.positions[symbol]['entry_snapshot']
    assert saved['chase_open']==float(f.iloc[-1].open)
    assert saved['third_open']!=saved['chase_open']
    assert saved['entry_phase']==('POST_EXIT_CONTINUATION' if post_exit else 'OUTSIDE_CONTINUATION')
    assert engine._entry_gate_diagnostics[(symbol,side,'EXECUTION')][1]=='FILLED'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_account_revalidates_continuation_after_quote_runs_away(side):
    f=continuation(side);ctx=context(f,side)
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=continuation(side,.2)))
    with pytest.raises(ValueError,match='BLOCKED_CONTINUATION_OPEN_CHASE'):
        asyncio.run(validate_account_entry(account,'TEST',side,ctx))
