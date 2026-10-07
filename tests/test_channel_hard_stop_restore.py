import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
import pytest
from core.services.exits.hard_stop_service import hard_stop_reason, enforce_hard_stop


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_fixed_initial_stop_works_before_atr2_arming(side):
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=1.,margin=100.,leverage=1,
           entry_mode='CHANNEL_SWING',initial_sl=100-sign*1.5)
    assert hard_stop_reason(p,100-sign*1.49) is None
    assert hard_stop_reason(p,100-sign*1.5)=='INITIAL_ATR'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('cause', ['MARGIN_LOSS', 'PRICE_LOSS'])
def test_account_limits_remain_independent_of_opposite_entry(side,cause,monkeypatch):
    from core import config
    from core.services.structure_risk_sizing import POLICY
    monkeypatch.setattr(config,'MAX_POSITION_MARGIN_LOSS_RATIO',.1 if cause=='MARGIN_LOSS' else 0.)
    monkeypatch.setattr(config,'MAX_ACCEPTABLE_LOSS_PCT',-.02)
    p=dict(side=side,entry_price=100.,qty=1.,margin=10.,leverage=10.,entry_mode='CHANNEL_SWING')
    price=100-(1 if side=='LONG' else -1)*(1. if cause=='MARGIN_LOSS' else 2.)
    assert hard_stop_reason(p,price)==cause


def test_cap_observed_stop_cross_and_failed_close_retry():
    p=dict(side='SHORT',entry_price=.085971402,qty=3665.65821753321,
           margin=63.02835524283021,leverage=5,entry_mode='CHANNEL_SWING',
           initial_sl=.0866494,structure_risk_policy='structure_fixed_budget_v1',
           structure_risk_budget_usdt=3.1514177621415103)
    a=SimpleNamespace(positions={'CAP/USDT':p},position_meta={},save_state=Mock(),
                      close_position=AsyncMock(return_value=False),channel_profit_reentries={})
    assert not asyncio.run(enforce_hard_stop(a,'CAP/USDT',.08714))
    assert p['channel_hard_stop_pending']=='INITIAL_ATR'
    a.close_position.return_value=True
    assert asyncio.run(enforce_hard_stop(a,'CAP/USDT',.086))
    assert a.close_position.await_count==2
    assert a.close_position.await_args.args[2]=='Channel Swing HARD_STOP INITIAL_ATR'
