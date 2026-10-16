import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
from core.services.exits.hard_stop_service import hard_stop_reason, enforce_hard_stop, HardStopExitStrategy
from core import config


@pytest.mark.parametrize('side,price', [('LONG', 1.), ('SHORT', 1000.)])
@pytest.mark.parametrize('pending', [None, 'MARGIN_LOSS', 'PRICE_LOSS'])
def test_position_hard_stop_is_disabled_for_channel_and_legacy_pending(side, price, pending, monkeypatch):
    monkeypatch.setattr(config, 'MAX_POSITION_MARGIN_LOSS_RATIO', .05)
    monkeypatch.setattr(config, 'MAX_ACCEPTABLE_LOSS_PCT', -.02)
    p = dict(side=side, entry_mode='CHANNEL_SWING', entry_price=100., qty=1., margin=10., leverage=10.)
    meta = dict(entry_mode='CHANNEL_SWING')
    if pending:
        p['channel_hard_stop_pending'] = meta['channel_hard_stop_pending'] = pending
    a = SimpleNamespace(positions={'X':p}, position_meta={'X':meta},
                        save_state=Mock(), close_position=AsyncMock(return_value=True))
    assert hard_stop_reason(p, price) is None
    assert HardStopExitStrategy().evaluate_exit(p, None, price) is None
    assert not asyncio.run(enforce_hard_stop(a, 'X', price))
    a.close_position.assert_not_awaited()
    a.save_state.assert_not_called()


def test_metadata_only_legacy_hard_stop_is_not_retried():
    p = dict(channel_hard_stop_pending='MARGIN_LOSS')
    a = SimpleNamespace(positions={'X':p}, position_meta={'X':dict(entry_mode='CHANNEL_SWING')},
                        save_state=Mock(), close_position=AsyncMock(return_value=True))
    assert not asyncio.run(enforce_hard_stop(a, 'X', 1.))
    a.close_position.assert_not_awaited()


@pytest.mark.parametrize('entry_mode', ['OTHER', 'WILD_SWING'])
def test_per_position_hard_stop_is_disabled_for_other_strategies(monkeypatch, entry_mode):
    monkeypatch.setattr(config, 'MAX_POSITION_MARGIN_LOSS_RATIO', .05)
    p = dict(side='LONG', entry_mode=entry_mode, entry_price=100., qty=1., margin=10., leverage=10.)
    assert hard_stop_reason(p, 90.) is None


@pytest.mark.parametrize('side,price', [('LONG', 1.), ('SHORT', 1000.)])
def test_wild_swing_same_entry_bar_hard_stop_does_not_close(side, price):
    p = dict(
        side=side,
        entry_mode='WILD_SWING',
        entry_price=100.,
        qty=1.,
        margin=10.,
        leverage=10.,
        open_timestamp=1000.,
        entry_atr=1.,
        channel_hard_stop_pending='HARD_STOP_1_2_ATR',
    )
    meta = dict(entry_mode='WILD_SWING', channel_hard_stop_pending='HARD_STOP_1_2_ATR')
    account = SimpleNamespace(
        positions={'LOBSTER/USDT': p},
        position_meta={'LOBSTER/USDT': meta},
        save_state=Mock(),
        close_position=AsyncMock(return_value=True),
    )

    assert hard_stop_reason(p, price) is None
    assert not asyncio.run(enforce_hard_stop(account, 'LOBSTER/USDT', price))
    account.close_position.assert_not_awaited()
    account.save_state.assert_not_called()


@pytest.mark.parametrize('side,price', [('LONG', 1.), ('SHORT', 1000.)])
def test_instant_quote_exit_does_not_execute_position_hard_stop(side, price):
    from core.engine import TradingEngine

    position = dict(
        side=side,
        entry_mode='WILD_SWING',
        entry_price=100.,
        qty=1.,
        margin=10.,
        leverage=10.,
        open_timestamp=1000.,
        entry_atr=1.,
    )
    account = SimpleNamespace(
        positions={'LOBSTER/USDT': position},
        position_meta={'LOBSTER/USDT': {'entry_mode': 'WILD_SWING'}},
        save_state=Mock(),
        close_position=AsyncMock(return_value=True),
    )
    engine = SimpleNamespace(account=account)

    assert not asyncio.run(TradingEngine._instant_quote_exit(
        engine, 'LOBSTER/USDT', price, 1000.,
    ))
    account.close_position.assert_not_awaited()


def test_channel_hard_stop_failure_does_not_create_retry_ticket():
    p = dict(side='LONG', entry_mode='CHANNEL_SWING', entry_price=100.,
             qty=1., margin=10., leverage=10.)
    meta = dict(entry_mode='CHANNEL_SWING')
    a = SimpleNamespace(positions={'X': p}, position_meta={'X': meta},
                        save_state=Mock(), close_position=AsyncMock(return_value=False))
    assert not asyncio.run(enforce_hard_stop(a, 'X', 90.))
    assert 'channel_hard_stop_pending' not in p
    assert 'channel_hard_stop_pending' not in meta
    a.save_state.assert_not_called()
