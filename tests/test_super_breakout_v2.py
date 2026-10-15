import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_v2_execution_boundary import candles
from core.services.strategies.pure_trend_v2 import evaluate_v2_frame
from core.services.entry_firewall import validate_account_entry
from test_second_bar_outside_v2 import second_frame


def super_frame(side):
    f=candles(side).iloc[:-1].copy()
    f.loc[f.index[-2],'close']=101.5 if side=='LONG' else 98.5
    f.loc[f.index[-2],'atr']=.3
    return f


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_second_close_super_and_account_boundary(side):
    f=super_frame(side);d=evaluate_v2_frame(f)
    assert d is None
    a=SimpleNamespace(trades=[],last_closed_at={},entry_frame_provider=AsyncMock(return_value=f))
    ctx=dict(entry_signal_code='SUPER_BREAKOUT_'+side,channel_confirmation_bar_id=float(f.iloc[-1].timestamp))
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(a,'TEST',side,ctx))




@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_body_crosses_both_bands_without_two_atr(side):
    f=super_frame(side)
    f.loc[f.index[-2],'atr']=10.
    f.loc[f.index[-2],'open']=98.5 if side=='LONG' else 101.5
    f.loc[f.index[-2],'low' if side=='LONG' else 'high']=98.4 if side=='LONG' else 101.6
    assert evaluate_v2_frame(f) is None




@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_latest_quote_cannot_replace_second_closed_confirmation(side):
    f = super_frame(side)
    quote = float(f.iloc[-1]['close'])
    f.loc[f.index[-1], 'close'] = 100.
    f.loc[f.index[-1], 'low' if side == 'LONG' else 'high'] = 100.
    assert evaluate_v2_frame(f, price=quote) is None




