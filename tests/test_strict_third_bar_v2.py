"""Former third-bar regression cases migrated to the authorized second-bar rule."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_v2_execution_boundary import candles
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2, evaluate_v2_frame
from core.services.entry_firewall import validate_account_entry


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('closed_second', [False,True])
def test_second_bar_enters_only_while_live(side, closed_second):
    f=candles(side).iloc[:-1].copy()
    f.loc[f.index[-2],'close']=101.5 if side=='LONG' else 98.5
    f.loc[f.index[-1],'is_closed']=closed_second
    # Only one completed outside bar is required; no closed-only fallback.
    assert bool(evaluate_v2_frame(f)) is (not closed_second)


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('fraction,allowed', [(0.,False),(.49,False),(.5,False),(.5001,False),(.8,False)])
def test_third_bar_adverse_body_boundary(side, fraction, allowed):
    f=candles(side)
    f.loc[f.index[-2],'close']=102. if side=='LONG' else 98.
    f.loc[f.index[-2],'high' if side=='LONG' else 'low']=102.1 if side=='LONG' else 97.9
    f.loc[f.index[-1],'open']=101.8+fraction*.5 if side=='LONG' else 98.2-fraction*.5
    assert (evaluate_v2_frame(f) is not None) is allowed


@pytest.mark.parametrize('side', ['LONG','SHORT'])
@pytest.mark.parametrize('fault', ['unclosed','gap','duplicate','wrong_color','touch'])
def test_confirmation_requirements(side,fault):
    f=candles(side)
    if fault=='unclosed':f.loc[f.index[-2],'is_closed']=False
    if fault=='gap':f.loc[f.index[-1],'timestamp']+=60000
    if fault=='duplicate':f.loc[f.index[-1],'timestamp']=f.iloc[-2].timestamp
    if fault=='wrong_color':f.loc[f.index[-2],'open']=102. if side=='LONG' else 98.
    if fault=='touch':f.loc[f.index[-1],'close']=f.iloc[-1]['kc_upper' if side=='LONG' else 'kc_lower']
    assert evaluate_v2_frame(f) is None


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_small_first_body_allowed_by_latest_spec(side):
    f=candles(side)
    f.loc[f.index[-3],'open']=101.45 if side=='LONG' else 98.55
    assert evaluate_v2_frame(f)['type']=='SECOND_BAR_OUTSIDE_'+side


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_bad_live_bar_does_not_reuse_previous_closed_signal(side):
    f=candles(side,False)
    assert evaluate_v2_frame(f) is None
    live=f.iloc[-1].copy()
    live['timestamp']+=60000;live['is_closed']=False
    f.loc[f.index[-1], 'open'] = float(f.iloc[-1].close)
    # Previous closed bar is a doji; cannot serve as confirmation #2.
    f.loc[len(f)]=live
    assert evaluate_v2_frame(f) is None


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_account_rechecks_third_bar_retracement(side):
    f=candles(side);d=evaluate_v2_frame(f)
    ctx=dict(entry_signal_code=d['type'],channel_confirmation_bar_id=d['confirmation_bar_id'])
    f.loc[f.index[-1],'open']=102.1 if side=='LONG' else 97.9
    account=SimpleNamespace(entry_frame_provider=AsyncMock(return_value=f),last_closed_at={})
    with pytest.raises(ValueError,match='V2'):
        asyncio.run(validate_account_entry(account,'TEST',side,ctx))
