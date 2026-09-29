"""Removed PnL veto must not return; entry data and hard-stop ATR remain valid."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2, evaluate_v2_frame
from core.services.entry_firewall import validate_account_entry
from test_v2_execution_boundary import candles, context


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('atr', [.01, .1, .2, .68, 1., 10.])
@pytest.mark.parametrize('scale', [1., 1e-6, 1000.])
def test_valid_entry_independent_of_profit_estimate(side, atr, scale):
    f = candles(side)
    f.loc[f.index[-2], 'atr'] = atr
    columns = ['open','high','low','close','ma3','ma15','atr','kc_upper','kc_lower','kc_middle']
    f[columns] *= scale
    decision = evaluate_v2_frame(f)
    assert decision['type'] == 'SECOND_BAR_OUTSIDE_' + side
    assert decision['entry_atr'] == pytest.approx(atr * scale)


@pytest.mark.parametrize('fault', ['price', 'atr_zero', 'atr_nan', 'atr_inf', 'ma15', 'missing', 'channel'])
def test_invalid_market_data_still_fails_closed(fault):
    f = candles()
    if fault == 'price': f.loc[f.index[-1], 'close'] = -1.
    if fault == 'atr_zero': f.loc[f.index[-2], 'atr'] = 0.
    if fault == 'atr_nan': f.loc[f.index[-2], 'atr'] = float('nan')
    if fault == 'atr_inf': f.loc[f.index[-2], 'atr'] = float('inf')
    if fault == 'ma15': f.loc[f.index[-1], 'ma15'] = float('nan')
    if fault == 'missing': f = f.drop(columns=['kc_upper'])
    if fault == 'channel': f.loc[f.index[-1], 'kc_lower'] = 102.
    assert evaluate_v2_frame(f) is None


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('live', [True, False])
def test_direct_entries_and_shared_boundary_allow_small_valid_atr(side, live):
    f=candles(side,live)
    if not live:
        assert evaluate_v2_frame(f) is None
        return
    assert evaluate_v2_frame(f)
    f['atr']=.01
    strategy=PureTrendStrategyV2()
    for evaluate in (strategy.evaluate_entry, strategy.evaluate_third_bar_open_entry):
        assert evaluate('TEST', *[f.iloc[i].to_dict() for i in [-1,-2,-3]]) is not None
    assert evaluate_v2_frame(f) is not None


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_account_allows_shape_when_old_reward_risk_would_reject(side):
    f=candles(side); ctx=context(f,side)
    f.loc[f.index[-1],'close']=103. if side=='LONG' else 97.
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f),last_closed_at={})
    assert asyncio.run(validate_account_entry(account,'TEST',side,ctx))['type'] == 'SECOND_BAR_OUTSIDE_' + side


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_closed_candidate_rechecks_latest_quote(side):
    f=candles(side,False)
    decision=evaluate_v2_frame(f)
    assert decision is None
    assert evaluate_v2_frame(f,103. if side=='LONG' else 97.) is None


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_live_atr_uses_previous_closed_bar(side):
    f=candles(side)
    f.loc[f.index[-1],'atr']=.00001
    assert evaluate_v2_frame(f)
    f.loc[f.index[-2],'atr']=.01
    f.loc[f.index[-1],'atr']=100.
    assert evaluate_v2_frame(f)['entry_atr'] == .01
