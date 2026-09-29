"""Specified net reward/risk filter, including account boundary revalidation."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2, evaluate_v2_frame
from core.services.entry_firewall import validate_account_entry
from test_v2_execution_boundary import candles, context


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('atr,allowed', [(.1, False), (.2, False), (.68-1e-8, False), (.68, True), (.68+1e-8, True)])
def test_net_reward_and_ratio_boundary(side, atr, allowed):
    bar = dict(kc_upper=101., kc_lower=99., ma15=100.)
    assert PureTrendStrategyV2().verify_profitable_expectation(side,100.,bar,atr) is allowed


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('scale', [1., 1e-6, 1000.])
def test_structural_risk_and_relative_price_scaling(side, scale):
    bar = dict(kc_upper=101.4, kc_lower=98.6, ma15=100.)
    entry = 101.8 if side=='LONG' else 98.2
    bar = {k:v*scale for k,v in bar.items()}
    strategy = PureTrendStrategyV2()
    assert strategy.verify_profitable_expectation(side,entry*scale,bar,scale)
    entry = 103. if side=='LONG' else 97.
    assert not strategy.verify_profitable_expectation(side,entry*scale,bar,scale)


@pytest.mark.parametrize('fault', ['side','price','atr_zero','atr_nan','atr_inf','ma15','missing','channel'])
def test_invalid_input_fails_closed(fault):
    side='LONG'; price=100.; atr=1.
    bar=dict(kc_upper=101.,kc_lower=99.,ma15=100.)
    if fault=='side':side='INVALID'
    if fault=='price':price=-1
    if fault=='atr_zero':atr=0
    if fault=='atr_nan':atr=float('nan')
    if fault=='atr_inf':atr=float('inf')
    if fault=='ma15':bar['ma15']=None
    if fault=='missing':del bar['kc_upper']
    if fault=='channel':bar['kc_lower']=102.
    assert not PureTrendStrategyV2().verify_profitable_expectation(side,price,bar,atr)


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('live', [True, False])
def test_direct_entries_and_shared_boundary_block_costs(side, live):
    f=candles(side,live)
    assert evaluate_v2_frame(f)
    f['atr']=.01
    strategy=PureTrendStrategyV2()
    if live:
        assert strategy.evaluate_third_bar_open_entry('TEST', *[f.iloc[i].to_dict() for i in [-1,-2,-3]]) is None
    else:
        assert strategy.evaluate_entry('TEST', *[f.iloc[i].to_dict() for i in [-1,-2,-3]]) is None
    assert evaluate_v2_frame(f) is None


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_account_recomputes_deteriorated_risk_before_submit(side):
    f=candles(side); ctx=context(f,side)
    f.loc[f.index[-1],'close']=103. if side=='LONG' else 97.
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f),last_closed_at={})
    with pytest.raises(ValueError,match='V2'):
        asyncio.run(validate_account_entry(account,'TEST',side,ctx))


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_closed_candidate_rechecks_latest_quote(side):
    f=candles(side,False)
    decision=evaluate_v2_frame(f)
    assert decision
    assert evaluate_v2_frame(f,103. if side=='LONG' else 97.,decision['type']) is None


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_live_atr_uses_previous_closed_bar(side):
    f=candles(side)
    f.loc[f.index[-1],'atr']=.00001
    assert evaluate_v2_frame(f)
    f.loc[f.index[-2],'atr']=.01
    f.loc[f.index[-1],'atr']=100.
    assert evaluate_v2_frame(f) is None
