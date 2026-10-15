import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from test_second_bar_outside_v2 import second_frame, SYMBOL
from test_red_eye_v2_pressure import engine_fixture
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2, evaluate_v2_frame
from core.services.entry_firewall import validate_account_entry




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
