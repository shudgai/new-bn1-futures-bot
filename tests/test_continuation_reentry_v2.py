import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_v2_execution_boundary import candles
from core.services.strategies.pure_trend_v2 import evaluate_v2_frame, successful_exit_ticket
from core.services.entry_firewall import validate_account_entry

SYMBOL='1000PEPE/USDT'


def setup(side='LONG', bars=2, standard=False):
    f=candles(side)
    if not standard:
        # Previous bar is opposite colored, so the strict three-bar pattern fails.
        f.loc[f.index[-2],'open']=101.9 if side=='LONG' else 98.1
        f.loc[f.index[-2],'high' if side=='LONG' else 'low']=102. if side=='LONG' else 98.
    f.loc[f.index[-1],'ma3']=101.5 if side=='LONG' else 98.5
    stamp=float(f.iloc[-1].timestamp)-bars*60000+17000
    a=SimpleNamespace(trades=[dict(symbol=SYMBOL,action='CLOSE_'+side,id=stamp)],last_closed_at={SYMBOL:stamp/1000})
    a.entry_frame_provider=AsyncMock(return_value=f)
    return f,a


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('bars,allowed',[(0,False),(1,False),(2,True),(8,True)])
@pytest.mark.parametrize('standard',[False,True])
def test_exact_two_bar_cooldown_for_all_entries(side,bars,allowed,standard):
    f,a=setup(side,bars,standard)
    d=evaluate_v2_frame(f,account=a,symbol=SYMBOL)
    allowed = allowed and standard  # Re-entry must also have a directional closed first bar.
    assert bool(d)==allowed
    if allowed:
        assert d['type']=='SECOND_BAR_OUTSIDE_'+side
        ctx=dict(entry_signal_code=d['type'],channel_confirmation_bar_id=d['confirmation_bar_id'])
        assert asyncio.run(validate_account_entry(a,SYMBOL,side,ctx))['type']==d['type']


@pytest.mark.parametrize('fault',['no_fill','failed_close','other_symbol','consumed','inside','ma','retreat'])
def test_continuation_denials(fault):
    f,a=setup()
    if fault=='no_fill':a.trades=[]
    if fault=='failed_close':a.trades=[];a.last_closed_at[SYMBOL]=time.time()-180
    if fault=='other_symbol':a.trades[0]['symbol']='OTHER'
    if fault=='consumed':a.trades.append(dict(symbol=SYMBOL,action='OPEN_LONG',id=a.trades[0]['id']+1))
    if fault=='inside':f.loc[f.index[-1],'close']=101.
    if fault=='ma':f.loc[f.index[-1],'ma3']=99.
    if fault=='retreat':f.loc[f.index[-1],'close']=101.7
    assert evaluate_v2_frame(f,account=a,symbol=SYMBOL) is None


def test_restart_and_out_of_order_trade_history():
    f,a=setup(standard=True)
    a.trades.insert(0,dict(symbol=SYMBOL,action='OPEN_LONG',id=a.trades[0]['id']-1))
    restored=SimpleNamespace(trades=list(reversed(a.trades)))
    assert successful_exit_ticket(a,SYMBOL)==successful_exit_ticket(restored,SYMBOL)
    assert evaluate_v2_frame(f,account=restored,symbol=SYMBOL)['type']=='SECOND_BAR_OUTSIDE_LONG'


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_runner_to_real_paper_account_reentry(monkeypatch,side):
    from core.engine import TradingEngine
    from core.paper_account import PaperAccount
    from core.services.symbol_runner import process_single_symbol_runner
    monkeypatch.setattr(PaperAccount,'load_state',lambda self:None)
    monkeypatch.setattr(PaperAccount,'save_state',lambda self:None)
    f,a=setup(side,standard=True)
    account=PaperAccount();account.balance=100.;account.trades=a.trades;account.last_closed_at=a.last_closed_at
    engine=object.__new__(TradingEngine);engine.account=account
    engine.tickers={SYMBOL:float(f.iloc[-1].close)}
    engine.fetch_klines=AsyncMock(return_value=f)
    engine.strategy=SimpleNamespace(compute_indicators=lambda f:f)
    engine.symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args:2)
    engine._execution_price_is_safe=AsyncMock(return_value=True)
    asyncio.run(process_single_symbol_runner(engine,SYMBOL,time.time(),None,False,exit_frame=f))
    assert account.positions[SYMBOL]['side']==side
    assert max(account.trades,key=lambda t:float(t['id']))['action']=='OPEN_'+side
    assert successful_exit_ticket(account,SYMBOL) is None
