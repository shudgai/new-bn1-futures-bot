import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
import pytest
from core.services.exits.hard_stop_service import hard_stop_reason, enforce_hard_stop


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
def test_old_atr_pending_is_persistently_revoked_without_close(side, symbol):
    sign = 1 if side == 'LONG' else -1
    p = dict(side=side, entry_price=100., qty=1., margin=100., leverage=1,
             initial_sl=100-sign*.5, channel_hard_stop_pending='INITIAL_ATR')
    meta = dict(entry_mode='CHANNEL_SWING', channel_hard_stop_pending='INITIAL_ATR',
                peak_trailing_state=dict(pending='EXIT_INITIAL_ATR_HARD_STOP', trigger='INITIAL_ATR'))
    a = SimpleNamespace(positions={symbol:p}, position_meta={symbol:meta},
                        save_state=Mock(), close_position=AsyncMock())
    assert not asyncio.run(enforce_hard_stop(a, symbol, 100-sign*.6))
    assert 'channel_hard_stop_pending' not in p
    assert 'channel_hard_stop_pending' not in meta
    assert 'pending' not in meta['peak_trailing_state']
    a.save_state.assert_called_once()
    a.close_position.assert_not_awaited()


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_initial_atr_stop_is_not_close_authority(side):
    sign=1 if side=='LONG' else -1
    p=dict(side=side,entry_price=100.,qty=1.,margin=100.,leverage=1,
           entry_mode='CHANNEL_SWING',initial_sl=100-sign*1.5)
    assert hard_stop_reason(p,100-sign*1.49) is None
    assert hard_stop_reason(p,100-sign*1.5) is None
    p['channel_hard_stop_pending'] = 'INITIAL_ATR'
    assert hard_stop_reason(p,100-sign*1.6) is None


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
    assert p['channel_hard_stop_pending']=='MARGIN_LOSS'
    a.close_position.return_value=True
    assert asyncio.run(enforce_hard_stop(a,'CAP/USDT',.086))
    assert a.close_position.await_count==2
    assert a.close_position.await_args.args[2]=='Channel Swing HARD_STOP MARGIN_LOSS'
