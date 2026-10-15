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






def test_restart_and_out_of_order_trade_history():
    f,a=setup(standard=True)
    a.trades.insert(0,dict(symbol=SYMBOL,action='OPEN_LONG',id=a.trades[0]['id']-1))
    restored=SimpleNamespace(trades=list(reversed(a.trades)))
    assert successful_exit_ticket(a,SYMBOL)==successful_exit_ticket(restored,SYMBOL)
    assert evaluate_v2_frame(f,account=restored,symbol=SYMBOL)['type']=='SECOND_BAR_OUTSIDE_LONG'


