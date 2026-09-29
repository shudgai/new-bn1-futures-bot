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
@pytest.mark.parametrize('fault',['not_large','unclosed','inside','cooldown','cost'])
def test_super_does_not_bypass_required_gates(side,fault):
    f=super_frame(side);a=SimpleNamespace(trades=[])
    if fault=='not_large':f.loc[f.index[-2],'atr']=1.
    if fault=='unclosed':f.loc[f.index[-1],'is_closed']=False
    if fault=='inside':f.loc[f.index[-1],'close']=100.
    if fault=='cooldown':a.trades=[dict(symbol='TEST',action='CLOSE_'+side,id=float(f.iloc[-1].timestamp))]
    if fault=='cost':f.loc[f.index[-1],'atr']=.01
    assert evaluate_v2_frame(f,account=a,symbol='TEST') is None


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_body_crosses_both_bands_without_two_atr(side):
    f=super_frame(side)
    f.loc[f.index[-2],'atr']=10.
    f.loc[f.index[-2],'open']=98.5 if side=='LONG' else 101.5
    f.loc[f.index[-2],'low' if side=='LONG' else 'high']=98.4 if side=='LONG' else 101.6
    assert evaluate_v2_frame(f) is None


def test_pepe_logged_cost_exceeds_target_and_is_exposed():
    f=second_frame('LONG')
    # Scale prices to PEPE magnitude; explicitly set logged ATR.
    for col in ('open','close','high','low','kc_upper','kc_lower','kc_middle','ma3','ma15','atr'):
        f[col]*=.000042
    f.loc[f.index[-2],'atr']=.00000446
    info={}
    assert evaluate_v2_frame(f,diagnostics=info) is None
    assert info['reason']=='BLOCKED_EXPECTED_NET_REWARD'
    assert info['reward']<0 and info['cost']>1.5*info['atr']


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_latest_quote_cannot_replace_second_closed_confirmation(side):
    f = super_frame(side)
    quote = float(f.iloc[-1]['close'])
    f.loc[f.index[-1], 'close'] = 100.
    f.loc[f.index[-1], 'low' if side == 'LONG' else 'high'] = 100.
    assert evaluate_v2_frame(f, price=quote) is None


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_super_returns_revalidated_quote_without_changing_closed_price(side):
    f = second_frame(side)
    closed_price = float(f.iloc[-2]['close'])
    quote = closed_price + (0.01 if side == 'LONG' else -0.01)
    decision = evaluate_v2_frame(f, price=quote)
    assert decision['type'] == 'SECOND_BAR_OUTSIDE_' + side
    assert decision['price'] == quote
    assert decision['close_price'] == closed_price
    assert float(f.iloc[-1]['close']) == closed_price


def test_pnl_rejection_does_not_survive_a_later_successful_check():
    from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2

    strategy = PureTrendStrategyV2()
    row = super_frame('LONG').iloc[-1].to_dict()
    assert not strategy.verify_profitable_expectation('LONG', row['close'], row, .01)
    assert strategy.pnl_rejection
    assert strategy.verify_profitable_expectation('LONG', row['close'], row, 1.)
    assert strategy.pnl_rejection is None
