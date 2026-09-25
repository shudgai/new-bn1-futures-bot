"""Additional fault and integration assertions for the actual staged core."""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest

from core.services.exits.staged_risk_service import (
    StagedPosition, StagedRiskEngine, install_staged_runtime, staged_entry_permission,
)
from core.services.exits.staged_state_store import FileStagedStore
from core.services.exits.profit_protection_service import protection, ProfitProtectionExitStrategy
from my_package.red_eye_adapter import _InjectedTransport, create_strategy
from red_eye_support import MockExchange, PositionState, RISK_PARAMS


def engine_for(state=None, exchange=None, persist=None):
    state = state or StagedPosition(qty=10)
    exchange = exchange or MockExchange()
    journal = []
    engine = StagedRiskEngine(state, _InjectedTransport(exchange), RISK_PARAMS['B'],
                              persist or (lambda snapshot: journal.append(copy.deepcopy(snapshot))),
                              lambda message: None)
    return engine, exchange, journal


def test_stop_replacement_waits_for_definitive_cancel():
    async def scenario():
        engine, ex, journal = engine_for()
        await engine.update_stop_loss()
        old = engine.position.stop_order_id
        ex.cancel_fault = 'timeout'
        await engine.update_stop_loss(100.5)
        assert engine.position.status == 'RECONCILE'
        assert len([e for e in ex.events if e[0] == 'place']) == 1
        await engine.update_stop_loss(101)
        assert engine.position.stop_price == 100.5
        await engine.reconcile()
        assert engine.position.status == 'RECONCILE'
        ex.cancel_order(old)
        await engine.reconcile()
        assert engine.position.status == 'OPEN'
        await engine.update_stop_loss(100)  # deliberately worse target
        assert engine.position.stop_price == 100.5
        assert engine.transport.requests[-1]['stop_price'] == 100.5
        assert ex.orders[old]['status'] == 'CANCELED'
        assert journal[-1]['status'] == 'OPEN'
    asyncio.run(scenario())


def test_rejected_stop_cannot_be_mistaken_for_active_protection():
    async def scenario():
        engine, ex, _ = engine_for()
        real_place = engine.transport.place_order
        async def reject(request):
            result = await real_place(request)
            ex.orders[request['id']]['status'] = 'REJECTED'
            return {**result, 'status': 'REJECTED'}
        engine.transport.place_order = reject
        await engine.update_stop_loss()
        assert engine.position.status == 'RECONCILE'
        await engine.reconcile()
        assert engine.position.stop_order_id is None
        engine.transport.place_order = real_place
        await engine.update_stop_loss()
        assert len(engine.transport.requests) == 2
        assert engine.position.stop_order_id is not None
    asyncio.run(scenario())


def test_save_failure_prevents_exchange_submission():
    def broken_store(snapshot):
        raise OSError('injected disk full')
    engine, ex, _ = engine_for(persist=broken_store)
    with pytest.raises(OSError, match='disk full'):
        asyncio.run(engine.update_stop_loss())
    assert engine.position.status == 'RECONCILE'
    assert not ex.events


def test_task_cancellation_leaves_durable_unknown_intent():
    async def scenario():
        engine, ex, journal = engine_for()
        entered = asyncio.Event()
        async def disconnected(request):
            entered.set()
            await asyncio.Event().wait()  # 故障注入：送單後斷線，直到任務被取消。
        engine.transport.place_order = disconnected
        task = asyncio.create_task(engine.update_stop_loss())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert journal[-1]['status'] == 'RECONCILE'
        assert journal[-1]['pending_request']['kind'] == 'STOP'
        assert journal[-1]['pending_order_id']
        assert not ex.events
    asyncio.run(scenario())


def test_concurrent_ticks_submit_only_one_partial():
    async def scenario():
        engine, ex, _ = engine_for()
        await asyncio.gather(*(engine.tick(103, 1, 30, 1) for _ in range(20)))
        assert engine.position.stage == 3
        assert sum(e[0] == 'place' and e[2] == 'PARTIAL_TP' for e in ex.events) == 1
    asyncio.run(scenario())


def test_flat_reconciliation_cancels_remaining_known_orders():
    async def scenario():
        engine, ex, _ = engine_for()
        await engine.tick(103, 1, 30, 1)
        tp_id = engine.position.tp_order_id
        ex.place_fault = 'timeout_after_fill'
        await engine.update_stop_loss()
        assert engine.position.status == 'RECONCILE'
        before = sum(e[0] == 'place' for e in ex.events)
        await engine.reconcile()
        assert engine.position.status == 'CLOSED'
        assert ex.orders[tp_id]['status'] == 'CANCELED'
        assert sum(e[0] == 'place' for e in ex.events) == before
    asyncio.run(scenario())


def test_exclusive_store_and_restart_snapshot(tmp_path):
    path = tmp_path/'position.json'
    with FileStagedStore(path) as store:
        state = StagedPosition(qty=10, stage=3, stop_price=102, status='RECONCILE',
                               pending_order_id='durable-client-id')
        store.save(state.snapshot())
        with pytest.raises(BlockingIOError):
            FileStagedStore(path)
        with pytest.raises(ValueError):
            store.save({**state.snapshot(), 'stop_price': float('nan')})
        assert store.load()['stop_price'] == 102
    with FileStagedStore(path) as reopened:
        restored = StagedPosition.restore(reopened.load())
        assert restored.stage == 3 and restored.stop_price == 102
        assert restored.pending_order_id == 'durable-client-id'
        assert restored.position_id == state.position_id


def test_install_and_runner_share_durable_runtime(tmp_path):
    from core.services.exits.staged_risk_service import run_staged_position
    import pandas as pd
    async def scenario(store):
        account = SimpleNamespace(positions={'TEST': dict(side='LONG', entry_price=100, qty=10, sl=99)},
                                  position_meta={}, save_state=Mock(), log=Mock())
        runtime = install_staged_runtime(account, 'TEST', _InjectedTransport(MockExchange()),
                                         RISK_PARAMS['B'], store)
        frame = pd.DataFrame([dict(atr=1, timestamp=0), dict(atr=1, timestamp=1)])
        await run_staged_position(account, 'TEST', frame, 103)
        saved = store.load()
        assert saved['stage'] == 3
        assert saved['stop_price'] == 102
        assert saved['stop_order_id'] is not None
        assert account.positions['TEST']['use_staged_risk_engine'] is True
        assert runtime.engine.transport.requests[-1]['kind'] == 'STOP'
    with FileStagedStore(tmp_path/'runtime.json') as store:
        asyncio.run(scenario(store))


def test_legacy_entry_points_are_silent_only_under_new_flag():
    p = dict(side='LONG', entry_price=100, qty=10, margin=1000, use_staged_risk_engine=True)
    assert protection(p, 103, 0.0005, 0.0005) is None
    assert 'channel_profit_protection' not in p
    assert ProfitProtectionExitStrategy().evaluate_exit(p, None, 103) is None
    p['use_staged_risk_engine'] = False
    result = protection(p, 103, 0.0005, 0.0005)
    assert result['peak_net'] > 3


def test_production_adapter_calls_staged_dispatcher_without_legacy(monkeypatch):
    from my_package import red_eye_adapter as adapter
    real_runner = adapter.symbol_runner.process_single_symbol_runner
    runner = AsyncMock(wraps=real_runner)
    monkeypatch.setattr(adapter.symbol_runner, 'process_single_symbol_runner', runner)
    p, ex = PositionState(), MockExchange()
    legacy = Mock(side_effect=AssertionError('legacy must be silent'))
    sut = create_strategy(p, ex, legacy, legacy, RISK_PARAMS['B'])
    sut.tick(103, 1, 30, 1)
    runner.assert_awaited_once()
    legacy.assert_not_called()
    assert sut.raw['use_staged_risk_engine'] is True
    assert sut.state.stage == p.stage == 3
    assert sut.account.saved_states[-1][sut.symbol]['staged_risk_state']['stage'] == 3


@pytest.mark.parametrize('side,price', [('LONG', 103.1), ('SHORT', 96.9)])
def test_deviation_uses_ma7_and_is_symmetric(side, price):
    allowed, reason = staged_entry_permission(price, 100, 1, side)
    assert not allowed and 'Deviation > 2.5 ATR' in reason
    boundary = 102.5 if side == 'LONG' else 97.5
    assert staged_entry_permission(boundary, 100, 1, side)[0]


def test_partial_fill_without_realized_accounting_cannot_fake_drawdown():
    from core.services.exits.staged_risk_service import native_net_pnl
    async def scenario():
        engine, ex, _ = engine_for()
        ex.place_fault = 'unknown_after_fill'
        await engine.tick(103, 1, 30, 1)
        await engine.reconcile()
        assert engine.position.qty == 5
        assert engine.position.is_partial_tp_executed
        with pytest.raises(ValueError, match='REALIZED_PNL_REQUIRED'):
            native_net_pnl(engine.position, 103)
        assert engine.position.peak_net_pnl == 30
    asyncio.run(scenario())


def test_unknown_placement_can_reconcile_to_confirmed_open_stop():
    async def scenario():
        engine, ex, _ = engine_for()
        ex.place_fault = 'unknown_open'
        await engine.update_stop_loss()
        order_id = engine.position.stop_order_id
        assert engine.position.status == 'RECONCILE'
        await engine.reconcile()
        assert engine.position.status == 'OPEN'
        assert engine.position.stop_order_id == order_id
        await engine.update_stop_loss()
        assert len(engine.transport.requests) == 1
    asyncio.run(scenario())


def test_wrong_order_ack_cannot_authorize_more_orders():
    async def scenario():
        engine, ex, _ = engine_for()
        real = engine.transport.place_order
        async def wrong_ack(request):
            response = await real(request)
            return {**response, 'id': 'another-position-order'}
        engine.transport.place_order = wrong_ack
        await engine.update_stop_loss()
        assert engine.position.status == 'RECONCILE'
        await engine.tick(103, 1, 30, 1)
        assert len(engine.transport.requests) == 1
    asyncio.run(scenario())


def test_normal_partial_fill_is_observed_during_account_refresh():
    async def scenario():
        engine, ex, _ = engine_for()
        await engine.tick(103, 1, 30, 1)
        ex.fill(engine.position.tp_order_id)
        assert engine.position.qty == 10  # no execution event has been consumed yet
        await engine.reconcile(refresh_open=True)
        assert engine.position.qty == 5
        assert engine.position.is_partial_tp_executed
        assert engine.position.status == 'OPEN'
        assert engine.position.stage == 3 and engine.position.stop_price == 102
    asyncio.run(scenario())
