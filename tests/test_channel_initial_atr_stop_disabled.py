"""Channel initial ATR stops are retired; account loss limits stay authoritative."""
import asyncio
import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core import config
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
from core.services.exits.entry_atr_protection import (
    enforce_atr_protection,
    initialize_atr_protection,
)
from core.services.exits.hard_stop_service import enforce_hard_stop
from core.services.exits.peak_trailing_exit import (
    ABNORMAL_REASON,
    HARD_REASON,
    STATE_KEY,
    evaluate_peak_trailing,
    migrate_peak_state,
)


def position(side: str, symbol: str = '龙虾/USDT') -> dict:
    return dict(symbol=symbol, side=side, entry_price=100., qty=1.,
                open_timestamp=60., entry_atr=1., entry_mode='CHANNEL_SWING',
                margin=100., leverage=1.)


@pytest.mark.parametrize('symbol', ['龙虾/USDT', 'CAP/USDT'])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('stamp', [61000, 121000])
def test_crossing_old_atr_stop_does_not_close(symbol, side, stamp):
    p = position(side, symbol)
    sign = 1 if side == 'LONG' else -1
    p.update(sl=100-sign*1.5, atr_sl=100-sign*1.5,
             initial_sl=100-sign*1.5, initial_risk=1.5)

    result = evaluate_peak_trailing(p, 100-sign*1.6, stamp, fee=0., slippage=0.)

    assert result is None
    assert not p[STATE_KEY].get('pending')
    assert all(p[key] == 0 for key in ('sl', 'stop_loss', 'atr_sl', 'initial_sl', 'initial_risk'))
    assert p[STATE_KEY]['atr'] == 1.


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('store', ['position', 'metadata', 'both'])
def test_restart_revokes_only_strategy_atr_pending(side, store):
    p = position(side)
    state = migrate_peak_state(p)
    state.update(peak_price=101. if side == 'LONG' else 99.,
                 pending=HARD_REASON, trigger='INITIAL_ATR', last_ms=61000)
    meta = {'entry_mode': 'CHANNEL_SWING', STATE_KEY: copy.deepcopy(state)}
    if store == 'metadata':
        p.pop(STATE_KEY)
        p['entry_mode'] = ''
    elif store == 'position':
        meta.pop(STATE_KEY)
    p['channel_hard_stop_pending'] = 'MARGIN_LOSS'
    p, meta = json.loads(json.dumps([p, meta]))

    result = migrate_peak_state(p, meta)

    assert result['peak_price'] == (101. if side == 'LONG' else 99.)
    assert result['last_ms'] == 61000
    assert 'pending' not in result
    assert 'trigger' not in result
    assert p['channel_hard_stop_pending'] == 'MARGIN_LOSS'
    if STATE_KEY in meta:
        assert 'pending' not in meta[STATE_KEY]


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_non_channel_initial_stop_is_unchanged(side):
    p = position(side)
    p['entry_mode'] = 'OTHER'
    sign = 1 if side == 'LONG' else -1

    result = evaluate_peak_trailing(p, 100-sign*1.6, 61000, fee=0., slippage=0.)

    assert result['reason'] == HARD_REASON


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_non_channel_initializer_preserves_stop(side):
    p = position(side)
    p['entry_mode'] = 'OTHER'
    initialize_atr_protection(p, 100., side, 1.)
    assert p['sl'] == (98.5 if side == 'LONG' else 101.5)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('initializer', ['account', 'strategy'])
def test_new_channel_position_keeps_atr_without_stop(side, initializer):
    p = position(side)

    if initializer == 'account':
        initialize_atr_protection(p, 100., side, 1.)
    else:
        DualTrackExitStrategy().initialize_position(p, 100., 1.)

    assert p['entry_atr'] == 1.
    assert p['sl'] == p['atr_sl'] == p['initial_sl'] == p['initial_risk'] == 0.


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('loss_gate', ['MARGIN_LOSS', 'PRICE_LOSS'])
def test_channel_loss_limits_no_longer_close_or_retry(side, loss_gate, monkeypatch):
    monkeypatch.setattr(config, 'MAX_POSITION_MARGIN_LOSS_RATIO', .01 if loss_gate == 'MARGIN_LOSS' else 0.)
    monkeypatch.setattr(config, 'MAX_ACCEPTABLE_LOSS_PCT', -.02)
    p = position(side)
    sign = 1 if side == 'LONG' else -1
    account = SimpleNamespace(positions={'X': p}, position_meta={},
                              save_state=Mock(), close_position=AsyncMock(return_value=False))
    price = 100-sign*(1.1 if loss_gate == 'MARGIN_LOSS' else 2.1)

    async def run():
        assert not await enforce_hard_stop(account, 'X', price)
        assert not await enforce_hard_stop(account, 'X', 100.)

    asyncio.run(run())

    assert 'channel_hard_stop_pending' not in p
    account.close_position.assert_not_awaited()


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_account_update_ignores_old_atr_stop(side):
    p = position(side)
    p['open_timestamp'] = time.time()-120
    sign = 1 if side == 'LONG' else -1
    account = SimpleNamespace(positions={'X': p}, position_meta={},
                              save_state=Mock(), close_position=AsyncMock())

    result = asyncio.run(enforce_atr_protection(account, 'X', 100-sign*1.6))

    assert not result
    account.close_position.assert_not_awaited()
    assert account.position_meta['X']['sl'] == 0.


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_waterfall_candidate_remains_in_the_exit_evaluator(side):
    p = position(side)
    sign = 1 if side == 'LONG' else -1
    snapshot = dict(quote_ms=61000, live_bar_ms=60000, closed_bar_ms=0,
                    live_open=100., atr=1.)

    result = evaluate_peak_trailing(p, 100-sign*1.6, snapshot, fee=0., slippage=0.)

    assert result['reason'] == ABNORMAL_REASON
    assert result['trigger'] == 'WATERFALL_DROP'
    meta = {STATE_KEY: copy.deepcopy(p[STATE_KEY])}
    p.pop(STATE_KEY)
    state = migrate_peak_state(p, meta)
    assert state['pending'] == ABNORMAL_REASON
    assert state['trigger'] == 'WATERFALL_DROP'


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_account_adapter_closes_for_waterfall_candidate(side, monkeypatch):
    p = position(side)
    p['open_timestamp'] = time.time() - 120
    p[STATE_KEY] = {'pending': ABNORMAL_REASON, 'trigger': 'WATERFALL_DROP'}
    account = SimpleNamespace(positions={'X': p}, position_meta={},
                              save_state=Mock(), close_position=AsyncMock())
    monkeypatch.setattr(
        'core.services.exits.peak_trailing_exit.evaluate_peak_trailing',
        lambda *args, **kwargs: {
            'type': ABNORMAL_REASON, 'trigger': 'WATERFALL_DROP',
        },
    )

    result = asyncio.run(enforce_atr_protection(account, 'X', 101.))

    assert result
    account.close_position.assert_awaited_once()
    assert account.close_position.await_args.args[2] == (
        "Channel Swing " + ABNORMAL_REASON
    )


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_paper_startup_does_not_restore_stop_from_trade(side, tmp_path, monkeypatch):
    import core.paper_account as paper
    monkeypatch.setattr(paper, 'STATE_FILE', str(tmp_path/'paper.json'))
    account = paper.PaperAccount()
    p = position(side, 'X')
    sign = 1 if side == 'LONG' else -1
    p.update(sl=100-sign*1.5, initial_sl=100-sign*1.5, initial_risk=1.5)
    account.positions = {'X': p}
    account.position_meta = {'X': copy.deepcopy(p)}
    account.trades = [dict(symbol='X', action=f'OPEN_{side}', id=60000,
                           initial_sl=p['initial_sl'], sl=p['sl'])]

    asyncio.run(account.initialize())
    reloaded = paper.PaperAccount()

    for source in (reloaded.positions['X'], reloaded.position_meta['X']):
        assert source['sl'] == source['initial_sl'] == source['initial_risk'] == 0.
        assert source['entry_atr'] == 1.


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_testnet_finalize_keeps_zero_local_and_native_stop(side, tmp_path):
    from core.testnet_account import BinanceTestnetAccount
    exchange = Mock()
    exchange.price_to_precision = lambda symbol, price: str(price)
    account = BinanceTestnetAccount(exchange, state_file=str(tmp_path/'testnet.json'))
    account._cancel_all_orders = AsyncMock()
    account._create_protection_order = AsyncMock()
    account.refresh = AsyncMock()
    account.save_state = Mock()
    sign = 1 if side == 'LONG' else -1

    result = asyncio.run(account._finalize_new_position(
        'X', side, 100., 1., 100., 100-sign*1.5, 100+sign*3., 'TEST',
        1., 1, None, 'sell' if side == 'LONG' else 'buy', 'test-order', 100.,
        entry_context={'entry_mode': 'CHANNEL_SWING'}))

    assert result is True
    assert account.position_meta['X']['sl'] == 0.
    assert account.trades[0]['initial_sl'] == 0.
    assert account.position_meta['X']['entry_atr'] == 1.
    account._create_protection_order.assert_not_awaited()
