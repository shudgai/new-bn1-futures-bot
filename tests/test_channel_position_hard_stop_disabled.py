import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from core.services.exits.hard_stop_service import hard_stop_reason, enforce_hard_stop, HardStopExitStrategy
from core import config


@pytest.mark.parametrize('side,price', [('LONG', 1.), ('SHORT', 1000.)])
@pytest.mark.parametrize('pending', [None, 'MARGIN_LOSS', 'PRICE_LOSS'])
def test_channel_loss_does_not_authorize_close_and_retires_retry(side, price, pending):
    p = dict(side=side, entry_mode='CHANNEL_SWING', entry_price=100., qty=1., margin=10., leverage=10.)
    meta = dict(entry_mode='CHANNEL_SWING')
    if pending:
        p['channel_hard_stop_pending'] = meta['channel_hard_stop_pending'] = pending
    a = SimpleNamespace(positions={'X':p}, position_meta={'X':meta},
                        save_state=Mock(), close_position=AsyncMock())
    assert hard_stop_reason(p, price) is None
    assert HardStopExitStrategy().evaluate_exit(p, None, price) is None
    assert not asyncio.run(enforce_hard_stop(a, 'X', price))
    a.close_position.assert_not_awaited()
    assert 'channel_hard_stop_pending' not in p
    assert 'channel_hard_stop_pending' not in meta
    assert a.save_state.call_count == bool(pending)


def test_metadata_only_mode_also_retires_pending():
    p = dict(channel_hard_stop_pending='MARGIN_LOSS')
    a = SimpleNamespace(positions={'X':p}, position_meta={'X':dict(entry_mode='CHANNEL_SWING')},
                        save_state=Mock(), close_position=AsyncMock())
    assert not asyncio.run(enforce_hard_stop(a, 'X', 1.))
    a.close_position.assert_not_awaited()
    a.save_state.assert_called_once()


def test_other_strategy_loss_policy_is_preserved(monkeypatch):
    monkeypatch.setattr(config, 'MAX_POSITION_MARGIN_LOSS_RATIO', .05)
    p = dict(side='LONG', entry_mode='OTHER', entry_price=100., qty=1., margin=10., leverage=10.)
    assert hard_stop_reason(p, 90.) == 'MARGIN_LOSS'
