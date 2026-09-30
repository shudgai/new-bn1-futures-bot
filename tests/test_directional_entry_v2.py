import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from test_second_bar_outside_v2 import second_frame, SYMBOL
from test_red_eye_v2_pressure import engine_fixture
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2, evaluate_v2_frame
from core.services.entry_firewall import validate_account_entry

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('ohlc,allowed',[
    ((100,101,102,98),True),  # exactly 25%, adverse wick exactly one body
    ((100,100.999,102,98),False),
    ((100,101,102.001,99),False),
    ((100,100,100,100),False),
    ((100,100,101,99),False),
    ((100,99,101,98),False),
    ((100,101,100.5,99),False),
    ((100,101,float('nan'),99),False),
    ((100,101,float('inf'),99),False),
])
def test_directional_body_wick_boundaries(side,ohlc,allowed):
    opened,close,high,low=ohlc
    if side=='SHORT':opened,close,high,low=200-opened,200-close,200-low,200-high
    assert PureTrendStrategyV2().is_valid_directional_entry_bar(
        dict(open=opened,close=close,high=high,low=low),side)==allowed

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('reentry',[False,True])
def test_changed_quote_rejected_at_account_boundary_and_can_recover(side,reentry):
    f=second_frame(side)
    saved=f.copy(deep=True)
    trades=[]
    if reentry:trades=[dict(symbol=SYMBOL,action='CLOSE_'+side,id=float(f.iloc[-1].timestamp)-120000+1000)]
    a=SimpleNamespace(trades=trades,entry_frame_provider=AsyncMock(return_value=f))
    d=evaluate_v2_frame(f,account=a,symbol=SYMBOL)
    ctx=dict(entry_signal_code=d['type'],channel_confirmation_bar_id=d['confirmation_bar_id'])
    quote=float(f.iloc[-1].open)
    assert evaluate_v2_frame(f,quote,account=a,symbol=SYMBOL) is None
    assert f.equals(saved)
    f.loc[f.index[-1],'close']=quote
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(a,SYMBOL,side,ctx))
    f.loc[f.index[-1],'close']=saved.iloc[-1].close
    assert asyncio.run(validate_account_entry(a,SYMBOL,side,ctx))
    assert d['entry_phase']==('CONTINUATION_REENTRY' if reentry else 'INITIAL_BREAKOUT')

@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_third_fourth_outside_cannot_relabel_initial_second(side):
    f=second_frame(side)
    assert evaluate_v2_frame(f)
    f.loc[f.index[-3],'close']=101.5 if side=='LONG' else 98.5
    f.loc[f.index[-3],'high' if side=='LONG' else 'low']=101.6 if side=='LONG' else 98.4
    assert evaluate_v2_frame(f) is None
    f.loc[f.index[-4],'close']=101.5 if side=='LONG' else 98.5
    f.loc[f.index[-4],'high' if side=='LONG' else 'low']=101.6 if side=='LONG' else 98.4
    assert evaluate_v2_frame(f) is None
    # A matched successful close resets the sequence; fresh pair after it is re-entry.
    a=SimpleNamespace(trades=[dict(symbol=SYMBOL,action='CLOSE_'+side,
        id=float(f.iloc[-1].timestamp)-120000+1000)])
    d=evaluate_v2_frame(f,account=a,symbol=SYMBOL)
    assert d['entry_phase']=='CONTINUATION_REENTRY'
    assert d['breakout_bar_id']>d['exit_bar_id']
    a.trades[0]['id']+=60000
    assert evaluate_v2_frame(f,account=a,symbol=SYMBOL) is None

@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('fault',['reverse','doji','wick'])
def test_execution_cannot_bypass_current_bar_guard(monkeypatch,side,fault):
    e,_=engine_fixture(monkeypatch)
    f=second_frame(side)
    if fault=='reverse':f.loc[f.index[-1],'open']=101.9 if side=='LONG' else 98.1
    if fault=='doji':f.loc[f.index[-1],'open']=f.iloc[-1].close
    if fault=='wick':f.loc[f.index[-1],'high' if side=='LONG' else 'low']=103 if side=='LONG' else 97
    e.fetch_klines=AsyncMock(return_value=f)
    e.tickers[SYMBOL]=float(f.iloc[-1].close)
    assert not asyncio.run(e._execute_confirmed_channel_break(SYMBOL,f,float(f.iloc[-1].close),side))
    assert not e.account.trades
