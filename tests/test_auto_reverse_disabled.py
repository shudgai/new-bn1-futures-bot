import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.auto_reverse import KEY, matched_ticket, try_auto_reverse
from core.services.direct_reverse import execute, authority
from core.services.entry_firewall import validate_account_entry
from test_two_slot_full_margin import make_engine


@pytest.fixture(autouse=True)
def isolated_shadow_logs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'logs').mkdir()


def account(phase='prepared'):
    return SimpleNamespace(
        position_meta={KEY: {'CAP/USDT': dict(mode='direct_netting_v1', phase=phase, token='old')}},
        positions={'CAP/USDT': dict(side='LONG', entry_mode='CHANNEL_SWING')},
        closing_lock=set(), save_state=Mock(), log=Mock(),
        reverse_position=AsyncMock(), close_position=AsyncMock())


@pytest.mark.parametrize('phase', ['prepared', 'closing', 'closed', 'consumed'])
def test_new_reverse_never_closes_or_submits(phase):
    a = account(phase)
    e = SimpleNamespace(account=a, _entry_boundary_frame=AsyncMock(), is_running=True)
    assert not asyncio.run(try_auto_reverse(e, 'CAP/USDT', None, 100.))
    assert matched_ticket(a, 'CAP/USDT') is None
    a.reverse_position.assert_not_awaited()
    a.close_position.assert_not_awaited()
    e._entry_boundary_frame.assert_not_awaited()


@pytest.mark.parametrize('paper', [True, False])
def test_direct_account_call_cannot_bypass_disabled_authority(paper):
    a = account()
    with pytest.raises(ValueError, match='AUTO_REVERSE_DISABLED'):
        authority(a, 'CAP/USDT', 'SHORT', dict(direct_reverse_token='old'))
    assert not asyncio.run(execute(a, None, 'CAP/USDT', 100., {}, {}, paper=paper))
    a.close_position.assert_not_awaited()


@pytest.mark.parametrize('token', ['auto_reverse_token', 'direct_reverse_token'])
def test_firewall_rejects_old_token_even_for_manual_order(token):
    a = account()
    with pytest.raises(ValueError, match='AUTO_REVERSE_DISABLED'):
        asyncio.run(validate_account_entry(a, 'CAP/USDT', 'SHORT', {token: 'old', 'manual_entry': True}))


@pytest.mark.parametrize('phase', ['submitting', 'unknown'])
def test_existing_unknown_order_is_reconciled_not_erased_or_resubmitted(phase, monkeypatch):
    a = account(phase)
    reconcile = AsyncMock(return_value=False)
    monkeypatch.setattr('core.services.direct_reverse.reconcile', reconcile)
    e = SimpleNamespace(account=a)
    assert asyncio.run(try_auto_reverse(e, 'CAP/USDT', None, 100.))
    reconcile.assert_awaited_once()
    assert a.position_meta[KEY]['CAP/USDT']['phase'] == phase
    a.reverse_position.assert_not_awaited()


@pytest.mark.parametrize('phase', ['submitting', 'unknown', 'partial'])
def test_unresolved_old_fill_still_blocks_normal_and_manual_entry(phase):
    a = account(phase)
    with pytest.raises(ValueError, match='FORBIDDEN_ENTRY'):
        asyncio.run(validate_account_entry(a, 'CAP/USDT', 'SHORT', {'manual_entry': True}))


def test_rejected_reverse_cannot_open_but_fresh_general_gate_can(monkeypatch):
    a = SimpleNamespace(positions={}, pending_limit_orders={}, trades=[], logs=[], log=Mock(),
        position_meta={KEY: {'CAP/USDT': dict(mode='direct_netting_v1', phase='prepared', bar=360000.)}},
        get_wallet_balance=lambda: 200., get_available_balance=lambda: 200.,
        open_position=AsyncMock(return_value=True))
    e, signal = make_engine(a, monkeypatch)
    assert not asyncio.run(e._place_structured_entry_locked('CAP/USDT', dict(signal, auto_reverse_token='old'), 101.5))
    a.open_position.assert_not_awaited()
    assert asyncio.run(e._place_structured_entry_locked('CAP/USDT', signal, 101.5))
    a.open_position.assert_awaited_once()
